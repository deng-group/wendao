"""Build prompt packages for the language model from query pipeline results.

This module does not call a model. It prepares the instructions, evidence, and answer
policy that the answer generator sends to one.
"""

from __future__ import annotations

import re
from pathlib import Path


SYSTEM_INSTRUCTION = """You are the course helper for {course_name}.
Answer students using only the provided course evidence and optional short-memory context.
If evidence is insufficient, ambiguous, outdated, or out of scope, say so clearly.
Do not invent citations, facts, deadlines, course policies, or code behavior.
Do not show raw internal chunk IDs to students.
Start each generated answer with a natural location sentence, for example: "You can find this in [Title] ([file_path])." If multiple sources are genuinely needed, mention at most the top 3 in that first sentence. Do not use report-style headings such as "Relevant Course Source(s)".
Use readable Markdown formatting. Put the location sentence in its own first paragraph. After a blank line, answer procedural or workflow questions as a numbered list. For conceptual questions, use short paragraphs or bullets when that improves readability.
Prefer concise, instructional answers that help students understand the concept.
"""


DIRECT_ANSWER_SYSTEM_INSTRUCTION = """You are the course helper for {course_name}.
Answer students using only the provided course evidence and optional short-memory context.
If evidence is insufficient, ambiguous, outdated, or out of scope, say so clearly.
Do not invent citations, facts, deadlines, course policies, or code behavior.
Do not show raw internal chunk IDs to students.
Answer the question directly. Do not begin with a sentence telling the student where the answer can be found, and do not include course file paths in the answer. The interface presents course sources separately.
Use readable Markdown formatting. For procedural or workflow questions, use a concise numbered list. For conceptual questions, use short paragraphs or bullets when that improves readability.
Prefer concise, instructional answers that help students understand the concept.
"""


# [student] assignment_help = "hints": help with graded work without doing it for the student.
ASSIGNMENT_HINTS = """Graded work: if the question asks you to solve or complete an assignment, quiz, homework, project, or exam question, or looks like one (it asks for the final answer, complete code, or a full derivation for a specific task), do not give the final answer, the complete code, or the full solution. Explain the concepts it needs, give hints and the first step, point to the course pages that help, and suggest how the student can check their own work. Explaining concepts, and examples that are already in the course notes, are fine to answer fully."""

# The teacher's own instructions (AGENTS.md) come before this, so the rules above always win a conflict.
RULES_FIRST = """These rules always come first: if the teacher's instructions conflict with them, follow the rules (answer only from the course evidence, never invent facts or citations, and say clearly when the evidence does not cover the question)."""

MAX_TEACHER_INSTRUCTIONS = 4000  # characters; they are sent with every question


def read_teacher_instructions(path: Path | None) -> str:
    """The teacher's instructions from AGENTS.md (or the file under [model] instructions), without HTML comments
    (the starter file's examples are inside one). Empty when there is no file."""
    if not path or not Path(path).is_file():
        return ""
    text = re.sub(r"<!--.*?-->", "", Path(path).read_text(encoding="utf-8", errors="replace"), flags=re.S)
    return text.strip()[:MAX_TEACHER_INSTRUCTIONS]


STATUS_POLICIES = {
    "answerable": {
        "llm_action": "generate_answer",
        "instruction": "Answer the question using only the provided evidence. Start with a natural location sentence such as: \"You can find this in [Title] ([file_path]).\" If multiple sources are genuinely needed, mention at most the top 3 sources in that first sentence. Put that location sentence in its own first paragraph, followed by a blank line. For procedural or workflow questions, answer with a concise numbered list. Do not use report-style headings. Do not show raw chunk IDs. Cite sources by title and file path only.",
    },
    "needs_time_context": {
        "llm_action": "generate_answer",
        "instruction": "Answer using only the provided evidence and explicitly state the academic year/semester. Start with a natural location sentence such as: \"For [academic year/semester], you can find this in [Title] ([file_path]).\" If multiple sources are genuinely needed, mention at most the top 3 sources in that first sentence. Put that location sentence in its own first paragraph, followed by a blank line. For procedural or workflow questions, answer with a concise numbered list. Do not present offering-specific logistics as timeless. Do not use report-style headings. Do not show raw chunk IDs. Cite sources by title and file path only.",
    },
    "needs_clarification": {
        "llm_action": "ask_clarification",
        "instruction": "Do not answer broadly. Ask the student to narrow the question to a concept, module, task, or comparison.",
    },
    "weak_evidence": {
        "llm_action": "insufficient_evidence",
        "instruction": "Do not answer as if verified. Explain that the course evidence is insufficient and suggest a more specific question.",
    },
    "out_of_scope": {
        "llm_action": "refuse_or_fallback",
        "instruction": "Do not answer from course knowledge. Explain that the question appears outside the course scope. Web fallback may be considered later if enabled.",
    },
}


DIRECT_ANSWER_STATUS_POLICIES = {
    **STATUS_POLICIES,
    "answerable": {
        "llm_action": "generate_answer",
        "instruction": "Answer the question directly using only the provided evidence. Do not open with a source-location sentence, do not mention course file paths, and do not add a sources section because the interface presents sources separately. Use concise, readable Markdown. For procedural or workflow questions, use a numbered list. Do not use report-style headings or show raw chunk IDs.",
    },
    "needs_time_context": {
        "llm_action": "generate_answer",
        "instruction": "Answer the question directly using only the provided evidence and explicitly state the relevant academic year/semester. Do not open with a source-location sentence, do not mention course file paths, and do not add a sources section because the interface presents sources separately. Do not present offering-specific logistics as timeless. Use concise, readable Markdown and do not show raw chunk IDs.",
    },
}


class PromptBuilder:
    """Construct structured prompt packages for future LLM calls."""

    def __init__(
        self,
        max_evidence: int | None = None,
        max_memory_turns: int = 4,
        evidence_score_threshold: float = 0.60,
        presentation_mode: str = "location_lead",
        course_name: str = "this course",
        instructions_path: Path | None = None,
        assignment_help: str = "full",
    ):
        if presentation_mode not in {"location_lead", "direct"}:
            raise ValueError("presentation_mode must be 'location_lead' or 'direct'")
        self.max_evidence = max_evidence
        self.max_memory_turns = max_memory_turns
        self.evidence_score_threshold = evidence_score_threshold
        self.presentation_mode = presentation_mode
        self.course_name = course_name
        self.instructions_path = instructions_path  # read again when the file changes: no restart needed
        self.assignment_help = assignment_help
        self._instructions = (None, "")  # (file stamp, text)

    @classmethod
    def for_workspace(cls, workspace, **options) -> PromptBuilder:
        """The course's prompt builder: its name, the teacher's instructions, and how to help with graded work."""
        return cls(course_name=workspace.display_name, instructions_path=getattr(workspace, "instructions_path", None),
                   assignment_help=getattr(workspace, "assignment_help", "full"), **options)

    def teacher_instructions(self) -> str:
        path = self.instructions_path
        try:
            stat = Path(path).stat() if path else None
        except OSError:
            stat = None
        stamp = (stat.st_mtime_ns, stat.st_size) if stat else None
        if stamp != self._instructions[0]:
            self._instructions = (stamp, read_teacher_instructions(path) if stamp else "")
        return self._instructions[1]

    def system_instruction(self) -> str:
        """Wendao's rules, the graded-work rule, the teacher's own instructions, and then which of them wins."""
        template = DIRECT_ANSWER_SYSTEM_INSTRUCTION if self.presentation_mode == "direct" else SYSTEM_INSTRUCTION
        parts = [template.replace("{course_name}", self.course_name).strip()]
        if self.assignment_help == "hints":
            parts.append(ASSIGNMENT_HINTS)
        teacher = self.teacher_instructions()
        if teacher:
            parts.append("The course teacher's instructions for answers:\n<<<\n" + teacher + "\n>>>")
            parts.append(RULES_FIRST)
        return "\n\n".join(parts)

    def build(
        self,
        pipeline_result: dict,
        short_memory: list[dict] | None = None,
        selected_context: list[str] | None = None,
        selection: str = "",
    ) -> dict:
        status = pipeline_result["status"]
        policies = DIRECT_ANSWER_STATUS_POLICIES if self.presentation_mode == "direct" else STATUS_POLICIES
        policy = policies.get(status, policies["weak_evidence"])
        system_instruction = self.system_instruction()
        memory = self._trim_memory(short_memory or [])
        evidence = self._select_evidence(pipeline_result)

        prompt_sections = [
            f"System instruction:\n{system_instruction.strip()}",
            f"Answerability status: {status}",
            f"Required action: {policy['llm_action']}",
            f"Policy:\n{policy['instruction']}",
        ]

        if pipeline_result.get("temporal_context"):
            prompt_sections.append(f"Temporal context: {pipeline_result['temporal_context']}")

        if memory:
            prompt_sections.append("Short memory from this open session:\n" + self._format_memory(memory))

        context = [str(item).strip()[:160] for item in (selected_context or []) if str(item).strip()]
        if context:
            prompt_sections.append(
                "Student-selected course graph context (use only to focus the question; it is not evidence):\n"
                + "\n".join(f"- {item}" for item in context[:4])
            )

        if selection:
            prompt_sections.append(
                "Text the student highlighted on the course page (the question is about this text; "
                "explain it using the evidence below):\n\"\"\"\n" + selection + "\n\"\"\""
            )

        if policy["llm_action"] == "generate_answer":
            prompt_sections.append("Evidence:\n" + self._format_evidence(evidence))
        else:
            prompt_sections.append("Retrieved evidence summary:\n" + self._format_evidence(evidence))

        prompt_sections.append(f"Student question:\n{pipeline_result['query']}")

        return {
            "query": pipeline_result["query"],
            "status": status,
            "llm_action": policy["llm_action"],
            "answer_policy": {
                "use_only_evidence": policy["llm_action"] == "generate_answer",
                "cite_sources": policy["llm_action"] == "generate_answer" and self.presentation_mode == "location_lead",
                "requires_temporal_context": pipeline_result.get("needs_temporal_context", False),
                "temporal_context": pipeline_result.get("temporal_context"),
                "evidence_score_threshold": self.evidence_score_threshold,
                "short_memory_scope": "current_window_only",
                "memory_persistence": "cleared_when_window_or_session_closes",
            },
            "short_memory": memory,
            "selected_context": context[:4],
            "selection": selection,
            "evidence": evidence,
            "messages": [
                {"role": "system", "content": system_instruction.strip()},
                {"role": "user", "content": "\n\n".join(prompt_sections[1:])},
            ],
            "final_prompt": "\n\n---\n\n".join(prompt_sections),
        }

    def _select_evidence(self, pipeline_result: dict) -> list[dict]:
        selected = []
        evidence = [
            item
            for item in pipeline_result.get("evidence", [])
            if item["score"] >= self.evidence_score_threshold or item.get("temporal_context")
        ]
        if not evidence and pipeline_result.get("evidence"):
            evidence = [pipeline_result["evidence"][0]]
        if self.max_evidence is not None:
            evidence = evidence[: self.max_evidence]

        for item in evidence:
            selected.append(
                {
                    "chunk_id": item["chunk_id"],
                    "file_path": item["file_path"],
                    "location": item.get("location", ""),
                    "title": item["title"],
                    "score": item["score"],
                    "time_sensitive": item["time_sensitive"],
                    "temporal_context": item["temporal_context"],
                    "content": item["content"],
                }
            )
        return selected

    def _trim_memory(self, memory: list[dict]) -> list[dict]:
        allowed = []
        for item in memory[-self.max_memory_turns :]:
            role = item.get("role")
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                allowed.append({"role": role, "content": content[:1200]})
        return allowed

    @staticmethod
    def _format_memory(memory: list[dict]) -> str:
        return "\n".join(f"- {item['role']}: {item['content']}" for item in memory)

    @staticmethod
    def _format_evidence(evidence: list[dict]) -> str:
        if not evidence:
            return "- No evidence selected."
        lines = []
        for idx, item in enumerate(evidence, start=1):
            temporal = ""
            if item.get("temporal_context"):
                temporal = f" | temporal_context={item['temporal_context'].get('year')}"
            where = f" ({item['location']})" if item.get("location") else ""
            lines.append(
                f"[Source {idx}] title={item['title']} | file={item['file_path']}{where} | "
                f"score={item['score']:.3f}{temporal}\n{item['content']}"
            )
        return "\n\n".join(lines)
