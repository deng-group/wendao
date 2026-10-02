"""Hybrid search over course chunks: BM25 keyword search plus embedding search.

This module never calls a language model. It answers one question: which course
chunks should the model read for this student question?
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from wendao.rag.embeddings import make_encoder


TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)?")

STOP_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "between",
    "do",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "or",
    "the",
    "to",
    "what",
    "when",
    "why",
}


def normalize_text(text: str, aliases: dict[str, str] | None = None) -> str:
    """Normalize query/document text for lexical search.

    `aliases` rewrites course-specific spellings, e.g. {"convexhull": "convex hull"}.
    """
    text = text.lower()
    text = text.replace("-", " ").replace("_", " ").replace("/", " ")
    for source, target in (aliases or {}).items():
        text = text.replace(source.lower(), target.lower())
    return text


def tokenize(text: str, aliases: dict[str, str] | None = None) -> list[str]:
    return TOKEN_RE.findall(normalize_text(text, aliases))


def query_phrases(query: str, aliases: dict[str, str] | None = None) -> set[str]:
    """Extract useful 2-3 token phrases for exact phrase boosting."""
    terms = tokenize(query, aliases)
    phrases = set()
    for size in (2, 3):
        for idx in range(len(terms) - size + 1):
            window = terms[idx : idx + size]
            if all(term in STOP_TERMS for term in window):
                continue
            if window[0] in STOP_TERMS and window[-1] in STOP_TERMS:
                continue
            content_terms = [term for term in window if term not in STOP_TERMS]
            if len(content_terms) < 2:
                continue
            phrases.add(" ".join(window))
            phrases.add(" ".join(content_terms))
    return phrases


def load_chunks(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@dataclass
class SearchResult:
    chunk_id: str
    file_path: str
    title: str
    module: str
    score: float
    bm25_score: float
    embedding_score: float
    time_sensitive: bool
    temporal_context: dict | None
    content: str
    content_preview: str
    location: str = ""


class BM25Index:
    """Small, dependency-free BM25 index."""

    def __init__(
        self,
        tokenized_docs: list[list[str]],
        aliases: dict[str, str] | None = None,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        self.tokenized_docs = tokenized_docs
        self.aliases = aliases or {}
        self.k1 = k1
        self.b = b
        self.doc_lengths = np.array([len(doc) for doc in tokenized_docs], dtype=np.float32)
        self.avg_doc_length = float(np.mean(self.doc_lengths)) if len(self.doc_lengths) else 0.0
        self.term_freqs = [Counter(doc) for doc in tokenized_docs]
        self.doc_freqs = Counter()
        for doc in tokenized_docs:
            self.doc_freqs.update(set(doc))
        self.num_docs = len(tokenized_docs)
        self.idf = {
            term: math.log(1 + (self.num_docs - df + 0.5) / (df + 0.5))
            for term, df in self.doc_freqs.items()
        }

    def search(self, query: str) -> np.ndarray:
        query_terms = [term for term in tokenize(query, self.aliases) if term not in STOP_TERMS]
        scores = np.zeros(self.num_docs, dtype=np.float32)
        if not query_terms or self.num_docs == 0:
            return scores

        for term in query_terms:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for idx, freqs in enumerate(self.term_freqs):
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                denominator = tf + self.k1 * (
                    1 - self.b + self.b * self.doc_lengths[idx] / max(self.avg_doc_length, 1)
                )
                scores[idx] += idf * (tf * (self.k1 + 1)) / denominator

        return scores


class EmbeddingIndex:
    """Meaning-based search over chunk embeddings, with a TF-IDF fallback.

    Embeddings come from `make_encoder` (ONNX on the CPU by default, or PyTorch with the
    `gpu` extra). Both engines give the same embeddings, so one cached index works with
    either. The cache in `cache_dir` is rebuilt automatically when the chunk contents or
    the model change. Use model_name="tfidf" to skip the neural model entirely.
    """

    NEURAL = {"embedding", "sentence-transformers"}  # the second is the name used by older caches

    def __init__(
        self,
        chunks: list[dict],
        cache_dir: Path,
        model_name: str,
        aliases: dict[str, str] | None = None,
        rebuild: bool = False,
        engine: str = "auto",
        device: str = "auto",
        model_path: str | None = None,
    ):
        self.chunks = chunks
        self.model_path = model_path
        self.cache_dir = cache_dir
        self.model_name = model_name
        self.aliases = aliases or {}
        self.rebuild = rebuild
        self.engine = engine
        self.device = device
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.encoder = None
        self.tfidf = None
        self.chunk_embeddings = None
        self.backend = "embedding"
        self._load_or_build()

    @property
    def cache_path(self) -> Path:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", self.model_name)
        return self.cache_dir / f"embeddings_{safe_name}.npz"

    @property
    def description(self) -> str:
        if self.backend == "tfidf":
            return "TF-IDF (no neural model)"
        device = getattr(self.encoder, "device", "cpu")
        return f"{self.model_name} via {self.encoder.name} on {device}"

    def content_hash(self) -> str:
        digest = hashlib.sha256(self.model_name.encode("utf-8"))
        for chunk in self.chunks:
            digest.update(chunk["chunk_id"].encode("utf-8"))
            digest.update(b"\0")
            digest.update(chunk["content"].encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _load_or_build(self) -> None:
        if self.model_name == "tfidf":
            self._build_tfidf()
            self.backend = "tfidf"
            return

        chunk_ids = np.array([chunk["chunk_id"] for chunk in self.chunks])
        content_hash = self.content_hash()

        if self.cache_path.exists() and not self.rebuild:
            cached = np.load(self.cache_path, allow_pickle=False)
            if (
                "content_hash" in cached.files
                and str(cached["content_hash"]) == content_hash
                and str(cached["backend"]) in self.NEURAL
            ):
                self.chunk_embeddings = cached["embeddings"].astype(np.float32)
                self.encoder = make_encoder(self.model_name, self.engine, self.device, self.model_path)
                return

        try:
            self.encoder = make_encoder(self.model_name, self.engine, self.device, self.model_path)
            self.chunk_embeddings = self.encoder.encode([chunk["content"] for chunk in self.chunks])
        except (ImportError, RuntimeError, OSError) as exc:
            if self.engine != "auto":
                raise RuntimeError(f"The `{self.engine}` search engine could not be loaded: {exc}") from exc
            print(f"Warning: the search model could not be loaded ({exc}). Falling back to TF-IDF.")
            self._build_tfidf()
            self.backend = "tfidf"
            return

        np.savez_compressed(
            self.cache_path,
            chunk_ids=chunk_ids,
            embeddings=self.chunk_embeddings,
            backend=np.array("embedding"),
            content_hash=np.array(content_hash),
        )

    def _build_tfidf(self) -> None:
        self.tfidf = TfidfIndex([chunk["content"] for chunk in self.chunks], self.aliases)

    def search(self, query: str) -> np.ndarray:
        if self.backend == "tfidf":
            return self.tfidf.search(query)
        query_embedding = self.encoder.encode([query])[0]
        return np.asarray(self.chunk_embeddings @ query_embedding, dtype=np.float32)


class TfidfIndex:
    """Small TF-IDF index (words and word pairs, cosine similarity), used when no search model is available.

    Same weighting as scikit-learn's TfidfVectorizer defaults (smooth idf, L2 norm), without the dependency.
    """

    WORD_RE = re.compile(r"(?u)\b\w+\b")

    def __init__(self, texts: list[str], aliases: dict[str, str] | None = None):
        self.aliases = aliases or {}
        counts = [Counter(self._terms(text)) for text in texts]
        doc_freq = Counter(term for terms in counts for term in terms)
        self.size = len(texts)
        self.idf = {term: math.log((1 + self.size) / (1 + freq)) + 1 for term, freq in doc_freq.items()}
        self.postings: dict[str, list[tuple[int, float]]] = {}
        for index, terms in enumerate(counts):
            weights = {term: count * self.idf[term] for term, count in terms.items()}
            norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
            for term, weight in weights.items():
                self.postings.setdefault(term, []).append((index, weight / norm))

    def _terms(self, text: str) -> list[str]:
        words = self.WORD_RE.findall(normalize_text(text, self.aliases))
        return words + [f"{left} {right}" for left, right in zip(words, words[1:])]

    def search(self, query: str) -> np.ndarray:
        scores = np.zeros(self.size, dtype=np.float32)
        weights = {term: count * self.idf[term] for term, count in Counter(self._terms(query)).items() if term in self.idf}
        norm = math.sqrt(sum(weight * weight for weight in weights.values()))
        if not norm:
            return scores
        for term, weight in weights.items():
            for index, doc_weight in self.postings[term]:
                scores[index] += (weight / norm) * doc_weight
        return scores


class HybridRetriever:
    """BM25 + embedding retrieval with simple score fusion and metadata boosts."""

    def __init__(
        self,
        chunks_path: Path,
        cache_dir: Path,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        aliases: dict[str, str] | None = None,
        logistics_terms: Iterable[str] = (),
        rebuild_index: bool = False,
        engine: str = "auto",
        device: str = "auto",
        model_path: str | None = None,
    ):
        self.aliases = aliases or {}
        self.logistics_terms = set(logistics_terms)
        self.chunks = load_chunks(chunks_path)
        self.bm25 = BM25Index([tokenize(chunk["content"], self.aliases) for chunk in self.chunks], self.aliases)
        self.embedding = EmbeddingIndex(
            self.chunks, cache_dir, model_name, self.aliases, rebuild=rebuild_index, engine=engine, device=device,
            model_path=model_path,
        )

    def normalize(self, text: str) -> str:
        return normalize_text(text, self.aliases)

    def tokenize(self, text: str) -> list[str]:
        return tokenize(text, self.aliases)

    def search(self, query: str, top_k: int = 5, candidate_k: int = 30) -> list[SearchResult]:
        bm25_scores = self.bm25.search(query)
        embedding_scores = self.embedding.search(query)

        bm25_norm = self._minmax(bm25_scores)
        embedding_norm = self._minmax(embedding_scores)
        fused = 0.45 * bm25_norm + 0.55 * embedding_norm

        query_tokens = set(self.tokenize(query))
        phrases = query_phrases(query, self.aliases)
        is_logistics = bool(query_tokens & self.logistics_terms)

        if is_logistics:
            for idx, chunk in enumerate(self.chunks):
                if chunk.get("time_sensitive"):
                    fused[idx] += 0.25

        wants_figures = bool(query_tokens & {"figure", "figures", "plot", "plots", "image", "images"})
        wants_comparison = bool(query_tokens & {"difference", "differences", "compare", "comparison", "versus", "vs"})
        for idx, chunk in enumerate(self.chunks):
            searchable = self.normalize(
                " ".join(
                    [
                        chunk.get("title", ""),
                        chunk.get("file_path", ""),
                        chunk.get("content", ""),
                    ]
                )
            )
            title_and_path = self.normalize(f"{chunk.get('title', '')} {chunk.get('file_path', '')}")
            for phrase in phrases:
                if phrase in searchable:
                    fused[idx] += 0.10
                if phrase in title_and_path:
                    fused[idx] += 0.18
            if wants_comparison and "comparison" in searchable:
                fused[idx] += 0.14
            if chunk.get("module") == "figures" and not wants_figures:
                fused[idx] *= 0.82

        sorted_indices = list(np.argsort(fused)[::-1][:candidate_k])
        selected_indices = sorted_indices[:top_k]

        if is_logistics:
            has_temporal_result = any(
                self.chunks[int(idx)].get("time_sensitive") for idx in selected_indices
            )
            if not has_temporal_result:
                temporal_indices = [
                    idx for idx in sorted_indices
                    if self.chunks[int(idx)].get("time_sensitive")
                ]
                if temporal_indices:
                    selected_indices = selected_indices[: max(top_k - 1, 0)] + [temporal_indices[0]]

        results = []
        for idx in selected_indices:
            chunk = self.chunks[int(idx)]
            results.append(
                SearchResult(
                    chunk_id=chunk["chunk_id"],
                    file_path=chunk["file_path"],
                    title=chunk.get("title", ""),
                    module=chunk.get("module", ""),
                    score=float(fused[idx]),
                    bm25_score=float(bm25_scores[idx]),
                    embedding_score=float(embedding_scores[idx]),
                    time_sensitive=bool(chunk.get("time_sensitive", False)),
                    temporal_context=chunk.get("temporal_context"),
                    content=chunk["content"],
                    content_preview=self._preview(chunk["content"]),
                    location=chunk.get("location", ""),
                )
            )
        return results

    @staticmethod
    def _minmax(scores: np.ndarray) -> np.ndarray:
        if len(scores) == 0:
            return scores
        low = float(np.min(scores))
        high = float(np.max(scores))
        if math.isclose(high, low):
            return np.zeros_like(scores, dtype=np.float32)
        return (scores - low) / (high - low)

    @staticmethod
    def _preview(text: str, max_chars: int = 360) -> str:
        preview = re.sub(r"\s+", " ", text).strip()
        if len(preview) <= max_chars:
            return preview
        return preview[: max_chars - 1].rstrip() + "..."
