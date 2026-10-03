"""Answer generation: search the course, check the evidence, and ask the model.

`AnswerGenerator` connects the query pipeline, the prompt builder, and a language model
provider. With the default dry-run provider it works without API keys or network access.
"""

from __future__ import annotations

import re
from typing import Iterator

from wendao.rag.pipeline import QueryPipeline
from wendao.rag.prompts import PromptBuilder
from wendao.rag.providers import DryRunProvider, LLMProvider


class AnswerGenerator:
    """Answer one student question from the course materials."""

    def __init__(
        self,
        pipeline: QueryPipeline,
        prompt_builder: PromptBuilder | None = None,
        provider: LLMProvider | None = None,
        course_name: str = "this course",
    ):
        self.pipeline = pipeline
        self.prompt_builder = prompt_builder or PromptBuilder(course_name=course_name)
        self.provider = provider or DryRunProvider()
        self.course_name = course_name

    def _prepare(
        self,
        query: str,
        short_memory: list[dict] | None = None,
        selected_context: list[str] | None = None,
        selection: str = "",
    ) -> tuple[dict, dict]:
        memory = short_memory or []
        context = selected_context or []
        pipeline_kwargs = {"short_memory": memory}
        if context:
            pipeline_kwargs["context_terms"] = context
        if selection:
            pipeline_kwargs["selection"] = selection
        pipeline_result = self.pipeline.ask(query, **pipeline_kwargs)
        prompt_package = self.prompt_builder.build(
            pipeline_result,
            short_memory=memory,
            selected_context=context,
            selection=selection,
        )
        return pipeline_result, prompt_package

    def answer(
        self,
        query: str,
        short_memory: list[dict] | None = None,
        selected_context: list[str] | None = None,
        selection: str = "",
    ) -> dict:
        pipeline_result, prompt_package = self._prepare(query, short_memory, selected_context, selection)
        if prompt_package["llm_action"] == "generate_answer":
            provider_result = self.provider.generate(prompt_package)
            sources = self._student_sources(prompt_package["evidence"])
        else:
            provider_result = {
                "provider": "course_policy",
                "model": None,
                "answer": self._policy_answer(prompt_package["llm_action"]),
                "citations": [],
                "raw_response": None,
            }
            sources = []
        answer = self._hide_internal_chunk_ids(provider_result["answer"], prompt_package["evidence"])

        return {
            "query": query,
            "status": prompt_package["status"],
            "llm_action": prompt_package["llm_action"],
            "next_action": pipeline_result["next_action"],
            "answer": answer,
            "citations": provider_result["citations"],
            "sources": sources,
            "provider": provider_result["provider"],
            "model": provider_result["model"],
            "confidence": pipeline_result["confidence"],
            "temporal_context": pipeline_result["temporal_context"],
            "short_memory": prompt_package["short_memory"],
            "evidence": prompt_package["evidence"],
            "prompt_package": prompt_package,
            "raw_response": provider_result["raw_response"],
        }

    def stream_answer(
        self,
        query: str,
        short_memory: list[dict] | None = None,
        selected_context: list[str] | None = None,
        selection: str = "",
    ) -> Iterator[dict]:
        pipeline_result, prompt_package = self._prepare(query, short_memory, selected_context, selection)
        should_generate = prompt_package["llm_action"] == "generate_answer"
        sources = self._student_sources(prompt_package["evidence"]) if should_generate else []
        resolve_model = getattr(self.provider, "resolved_model", None)
        model = (resolve_model() if callable(resolve_model) else getattr(self.provider, "model", None)) if should_generate else None
        provider_name = self.provider.name if should_generate else "course_policy"

        yield {
            "type": "start",
            "ok": True,
            "query": query,
            "status": prompt_package["status"],
            "llm_action": prompt_package["llm_action"],
            "provider": provider_name,
            "model": model,
            "confidence": pipeline_result["confidence"],
            "temporal_context": pipeline_result["temporal_context"],
            "sources": self._public_sources(sources),
        }

        if not should_generate:
            answer = self._policy_answer(prompt_package["llm_action"])
            yield {"type": "delta", "text": answer}
            yield {
                "type": "done",
                "ok": True,
                "answer": answer,
                "status": prompt_package["status"],
                "llm_action": prompt_package["llm_action"],
                "provider": provider_name,
                "model": None,
                "confidence": pipeline_result["confidence"],
                "temporal_context": pipeline_result["temporal_context"],
                "sources": [],
            }
            return

        stream = getattr(self.provider, "stream", None)
        if callable(stream):
            deltas = stream(prompt_package)
        else:
            # Providers without streaming still work; the answer arrives as one delta.
            deltas = iter([self.provider.generate(prompt_package)["answer"]])

        chunks = []
        for delta in deltas:
            if not delta:
                continue
            chunks.append(delta)
            yield {"type": "delta", "text": self._replace_chunk_ids(delta, prompt_package["evidence"])}

        if not chunks:
            raise RuntimeError("The model stream completed without returning answer text.")
        answer = self._hide_internal_chunk_ids("".join(chunks), prompt_package["evidence"])
        yield {
            "type": "done",
            "ok": True,
            "answer": answer,
            "status": prompt_package["status"],
            "llm_action": prompt_package["llm_action"],
            "provider": self.provider.name,
            "model": model,
            "confidence": pipeline_result["confidence"],
            "temporal_context": pipeline_result["temporal_context"],
            "sources": self._public_sources(sources),
        }

    def _policy_answer(self, action: str) -> str:
        if action == "ask_clarification":
            return "Please narrow the question to a specific course concept, chapter, task, or comparison."
        if action == "refuse_or_fallback":
            return f"I don’t know based on the course materials. This question appears to be outside the scope of {self.course_name}."
        return "I don’t know based on the available course materials. Try asking about a more specific course concept or chapter."

    @staticmethod
    def _student_sources(evidence: list[dict]) -> list[dict]:
        sources = []
        seen = set()
        for item in evidence:
            key = (item["title"], item["file_path"], item.get("location", ""))
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "title": item["title"],
                    "file_path": item["file_path"],
                    "location": item.get("location", ""),
                    "chunk_id": item["chunk_id"],
                    "score": item["score"],
                }
            )
        return sources

    @staticmethod
    def _public_sources(sources: list[dict]) -> list[dict]:
        return [
            {
                "title": source["title"],
                "file_path": source["file_path"],
                "location": source.get("location", ""),
                "score": source["score"],
            }
            for source in sources
        ]

    @staticmethod
    def _hide_internal_chunk_ids(answer: str, evidence: list[dict]) -> str:
        cleaned = AnswerGenerator._replace_chunk_ids(answer, evidence)
        cleaned = re.sub(r"\(\s*chunk_id\s*=\s*[^)]+\)", "", cleaned)
        cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
        return cleaned.strip()

    @staticmethod
    def _replace_chunk_ids(answer: str, evidence: list[dict]) -> str:
        cleaned = answer
        for item in evidence:
            replacement = f"{item['title']} ({item['file_path']})"
            cleaned = cleaned.replace(item["chunk_id"], replacement)
        return cleaned
