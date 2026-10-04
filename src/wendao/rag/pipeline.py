"""Query pipeline: search the course, then decide whether the evidence can answer the question."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wendao.rag.answerability import AnswerabilityGate
from wendao.rag.retriever import HybridRetriever, SearchResult

if TYPE_CHECKING:
    from wendao.workspace import Workspace


SELECTION_SEARCH_CHARS = 600  # how much highlighted text goes into the search query

NEXT_ACTIONS = {
    "answerable": "answer_from_course_evidence",
    "needs_time_context": "answer_with_time_context",
    "needs_clarification": "ask_clarifying_question",
    "weak_evidence": "return_insufficient_course_evidence",
    "out_of_scope": "refuse_or_consider_web_fallback",
}


class QueryPipeline:
    """Run retrieval and answerability in one stable interface."""

    def __init__(self, retriever: HybridRetriever, gate: AnswerabilityGate, top_k: int = 5):
        self.retriever = retriever
        self.gate = gate
        self.top_k = top_k

    @classmethod
    def for_workspace(cls, workspace: Workspace, top_k: int = 5, rebuild_index: bool = False) -> QueryPipeline:
        """Build the pipeline from a workspace's chunks, index, and search settings."""
        chunks_path = workspace.require(workspace.chunks_path, "Run `wendao build` first.")
        retriever = HybridRetriever(
            chunks_path=chunks_path,
            cache_dir=workspace.index_dir,
            model_name=workspace.embedding_model,
            aliases=workspace.aliases,
            logistics_terms=workspace.logistics_terms,
            rebuild_index=rebuild_index,
            engine=workspace.search_engine,
            device=workspace.search_device,
            model_path=getattr(workspace, "model_path", None),
        )
        gate = AnswerabilityGate(
            logistics_terms=workspace.logistics_terms,
            out_of_scope_terms=workspace.out_of_scope_terms,
            aliases=workspace.aliases,
        )
        return cls(retriever, gate, top_k=top_k)

    def ask(
        self,
        query: str,
        top_k: int | None = None,
        short_memory: list[dict] | None = None,
        context_terms: list[str] | None = None,
        selection: str = "",
    ) -> dict:
        retrieval_query = self._contextual_query(query, short_memory or [], context_terms or [])
        if selection:
            # The highlighted text says what "this" is; search with it, ahead of the question.
            retrieval_query = f"{selection[:SELECTION_SEARCH_CHARS]} {retrieval_query}"
        results = self.retriever.search(retrieval_query, top_k=top_k or self.top_k)
        decision = self.gate.decide(query, results, focused=bool(selection))
        return {
            "query": query,
            "retrieval_query": retrieval_query,
            "status": decision.status,
            "next_action": NEXT_ACTIONS.get(decision.status, "inspect_manually"),
            "reason": decision.reason,
            "confidence": decision.confidence,
            "needs_temporal_context": decision.needs_temporal_context,
            "temporal_context": decision.temporal_context,
            "multi_source": decision.multi_source,
            "evidence": [self._format_evidence(result) for result in results],
        }

    @staticmethod
    def _contextual_query(query: str, short_memory: list[dict], context_terms: list[str] | None = None) -> str:
        """Use selected graph nodes and short memory to disambiguate retrieval."""
        selected_context = [str(term).strip()[:160] for term in (context_terms or []) if str(term).strip()]
        lowered = query.lower()
        referential = any(token in lowered.split() for token in {"it", "this", "that", "they", "them"})
        if not referential or not short_memory:
            return " ".join(selected_context + [query]) if selected_context else query

        recent = []
        for item in short_memory[-4:]:
            role = item.get("role")
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                recent.append(content[:400])

        if not recent:
            return " ".join(selected_context + [query]) if selected_context else query
        return " ".join(selected_context + recent + [query])

    @staticmethod
    def _format_evidence(result: SearchResult) -> dict:
        return {
            "chunk_id": result.chunk_id,
            "file_path": result.file_path,
            "location": result.location,
            "title": result.title,
            "module": result.module,
            "score": result.score,
            "bm25_score": result.bm25_score,
            "embedding_score": result.embedding_score,
            "time_sensitive": result.time_sensitive,
            "temporal_context": result.temporal_context,
            "content": result.content,
        }
