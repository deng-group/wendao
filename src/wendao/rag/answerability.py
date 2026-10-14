"""Rule-based answerability gate for retrieved course evidence.

The gate decides whether the retrieved chunks are strong enough to pass to the
language model. It never generates answers itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from wendao.rag.retriever import SearchResult, tokenize


BROAD_TERMS = {
    "everything",
    "anything",
    "overview",
    "summarize",
    "summary",
    "tell",
}


@dataclass
class AnswerabilityDecision:
    query: str
    status: str
    reason: str
    needs_temporal_context: bool
    temporal_context: str | None
    multi_source: bool
    confidence: float
    top_results: list[dict]


# Everyday words that say nothing about whether a question is about the course.
COMMON_WORDS = {
    "the", "and", "for", "are", "how", "what", "when", "why", "who", "which", "where", "does", "can", "could", "would",
    "should", "you", "your", "this", "that", "these", "those", "with", "from", "about", "into", "there", "their", "have",
    "has", "was", "were", "will", "tell", "explain", "give", "show", "please", "some", "any", "use", "used", "get",
}


class AnswerabilityGate:
    """Conservative rule-based gate over retrieval results."""

    def __init__(
        self,
        strong_score: float = 0.72,
        weak_score: float = 0.42,
        min_embedding_score: float = 0.18,
        logistics_terms: Iterable[str] = (),
        out_of_scope_terms: Iterable[str] = (),
        aliases: dict[str, str] | None = None,
        vocabulary: set[str] | None = None,
        meaning_floor: float | None = None,
    ):
        # Off-topic check (only with the neural search model, whose meaning scores are comparable): the words in the
        # notes, and the meaning score below which a question is not about the course. See `_off_topic`.
        self.vocabulary = vocabulary or set()
        self.meaning_floor = meaning_floor
        self.logistics_terms = set(logistics_terms)
        self.out_of_scope_terms = set(out_of_scope_terms)
        self.aliases = aliases or {}
        self.strong_score = strong_score
        self.weak_score = weak_score
        self.min_embedding_score = min_embedding_score

    def _off_topic(self, tokens: set[str], results: list[SearchResult]) -> bool:
        """A question unlike anything in the notes. Keyword search always finds *something* (common words like "best"
        or "fix"), and scores are scaled so the top one looks strong, so this looks at the meaning score itself:
        clearly low, or a little low while most of the question's words never appear in the notes ("bake sourdough
        bread with a lattice crust"). Skipped without English words (the search model is English-only)."""
        if self.meaning_floor is None or not self.vocabulary:
            return False
        words = [word for word in tokens if len(word) > 2 and not word.isdigit() and word not in COMMON_WORDS]
        if not words:
            return False
        meaning = max(result.embedding_score for result in results[:5])
        known = sum(word in self.vocabulary for word in words) / len(words)
        return meaning < self.meaning_floor or (meaning < self.meaning_floor + 0.10 and known < 0.5)

    def decide(self, query: str, results: list[SearchResult], focused: bool = False) -> AnswerabilityDecision:
        """`focused`: the student highlighted text on the page, so a short question like "summarize this" is not broad."""
        tokens = set(tokenize(query, self.aliases))
        top = results[0] if results else None
        temporal_context = self._temporal_context(results)
        needs_temporal_context = bool(tokens & self.logistics_terms)
        multi_source = self._is_multi_source(results)

        if not top:
            return self._decision(query, "weak_evidence", "No chunks were retrieved.", False, None, False, 0.0, results)

        if tokens & self.out_of_scope_terms:
            return self._decision(
                query,
                "out_of_scope",
                "The query appears outside the course knowledge base.",
                needs_temporal_context,
                temporal_context,
                multi_source,
                top.score,
                results,
            )

        if top.score < self.weak_score or (top.bm25_score <= 0.01 and top.embedding_score < self.min_embedding_score):
            return self._decision(
                query,
                "weak_evidence",
                "The retrieved chunks are too weak to support a reliable answer.",
                needs_temporal_context,
                temporal_context,
                multi_source,
                top.score,
                results,
            )

        if needs_temporal_context and not temporal_context:
            return self._decision(
                query,
                "needs_time_context",
                "The query is course-offering specific, but no syllabus/calendar time context was retrieved.",
                True,
                None,
                multi_source,
                top.score,
                results,
            )

        if not focused and tokens & BROAD_TERMS and len(tokens) <= 4:
            return self._decision(
                query,
                "needs_clarification",
                "The query is broad and should be narrowed to a concept, module, or task.",
                needs_temporal_context,
                temporal_context,
                multi_source,
                top.score,
                results,
            )

        # After the vague-question check: "summarize this" should be asked to narrow down, not turned away.
        if not focused and self._off_topic(tokens, results):
            return self._decision(
                query,
                "out_of_scope",
                "The question doesn't seem to be about this course: it means little like the notes and uses few of their words.",
                needs_temporal_context,
                temporal_context,
                multi_source,
                top.score,
                results,
            )

        status = "needs_time_context" if needs_temporal_context else "answerable"
        reason = "Course-offering answer should state the retrieved academic year/semester." if needs_temporal_context else "Retrieved course evidence is strong enough."
        return self._decision(
            query,
            status,
            reason,
            needs_temporal_context,
            temporal_context,
            multi_source,
            top.score,
            results,
        )

    def _decision(
        self,
        query: str,
        status: str,
        reason: str,
        needs_temporal_context: bool,
        temporal_context: str | None,
        multi_source: bool,
        confidence: float,
        results: list[SearchResult],
    ) -> AnswerabilityDecision:
        return AnswerabilityDecision(
            query=query,
            status=status,
            reason=reason,
            needs_temporal_context=needs_temporal_context,
            temporal_context=temporal_context,
            multi_source=multi_source,
            confidence=float(confidence),
            top_results=[asdict(result) for result in results],
        )

    @staticmethod
    def _temporal_context(results: list[SearchResult]) -> str | None:
        for result in results:
            if result.temporal_context and result.temporal_context.get("year"):
                return result.temporal_context["year"]
        return None

    @staticmethod
    def _is_multi_source(results: list[SearchResult]) -> bool:
        if len(results) < 2:
            return False
        top_score = results[0].score
        close_results = [result for result in results[:5] if result.score >= top_score * 0.72]
        modules = {result.module for result in close_results}
        files = {result.file_path for result in close_results}
        return len(modules) >= 2 or len(files) >= 3
