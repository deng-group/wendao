"""Check Wendao against your test questions (questions.json in the workspace).

Three levels:

- search:  does search find the right pages, and does the gate make the right call? (no model)
- answers: are prompts, citations, and follow-up memory put together correctly? (dry run, no model)
- real:    ask the configured model every question, for you to read the answers (uses API credits)

Each run writes build/reports/<level>.md and a timestamped copy in build/reports/history/.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from wendao.rag.answer import AnswerGenerator
from wendao.rag.answerability import AnswerabilityGate
from wendao.rag.pipeline import QueryPipeline
from wendao.rag.prompts import PromptBuilder
from wendao.rag.providers import default_model_name, default_provider_name, provider_from_name
from wendao.rag.retriever import SearchResult


def load_questions(workspace) -> list[dict]:
    path = workspace.require(workspace.questions_path, "Add test questions there; see `wendao init` for the format.")
    questions = json.loads(path.read_text(encoding="utf-8"))
    for index, case in enumerate(questions):
        if "query" not in case:
            raise ValueError(f"Question {index + 1} in {path.name} needs a `query`.")
        case.setdefault("id", f"q{index + 1}")
    return questions


def save_report(workspace, name: str, text: str) -> Path:
    """Write the latest report and a timestamped copy. Returns the latest report's path."""
    latest = workspace.reports_dir / f"{name}.md"
    history = workspace.reports_dir / "history" / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    for path in (latest, history):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return latest


# Search and gate ---------------------------------------------------------------------------


def has_expected_file(results: list[SearchResult], expected_files: list[str]) -> bool:
    if not expected_files:
        return True
    result_files = {result.file_path for result in results}
    return any(file_path in result_files for file_path in expected_files)


def has_expected_terms(results: list[SearchResult], expected_terms: list[str], normalize) -> bool:
    if not expected_terms:
        return True
    combined = "\n".join(normalize(" ".join([r.file_path, r.title, r.content_preview])) for r in results)
    return any(normalize(term) in combined for term in expected_terms)


def evaluate_search_case(case: dict, results: list[SearchResult], decision, normalize) -> dict:
    checks = {
        "status": decision.status == case["expected_status"],
        "expected_file": has_expected_file(results, case.get("expected_files", [])),
        "expected_terms": has_expected_terms(results, case.get("expected_terms", []), normalize),
    }
    if "expected_temporal_context" in case:
        checks["temporal_context"] = decision.temporal_context == case["expected_temporal_context"]
    if "expected_multi_source" in case:
        checks["multi_source"] = decision.multi_source == case["expected_multi_source"]

    return {
        "id": case["id"],
        "query": case["query"],
        "expected_status": case["expected_status"],
        "expected_files": case.get("expected_files", []),
        "passed": all(checks.values()),
        "checks": checks,
        "notes": case.get("notes", ""),
        "decision": asdict(decision),
    }


def top_evidence(decision: dict, expected_files: list[str], limit: int = 3) -> str:
    selected = select_evidence_for_display(decision["top_results"], expected_files, limit)
    parts = []
    for result in selected:
        temporal = ""
        if result.get("temporal_context"):
            temporal = f" ({result['temporal_context'].get('year')})"
        parts.append(f"`{result['file_path']}` `{result['score']:.3f}`{temporal}")
    return "<br>".join(parts)


def select_evidence_for_display(results: list[dict], expected_files: list[str], limit: int) -> list[dict]:
    selected = []
    seen_chunks = set()

    def add(result: dict) -> None:
        if len(selected) >= limit:
            return
        if result["chunk_id"] in seen_chunks:
            return
        selected.append(result)
        seen_chunks.add(result["chunk_id"])

    if results:
        add(results[0])

    for expected_file in expected_files:
        for result in results:
            if result["file_path"] == expected_file:
                add(result)
                break

    if not any(result.get("temporal_context") for result in selected):
        for result in results:
            if result.get("temporal_context"):
                add(result)
                break

    for result in results:
        add(result)
        if len(selected) >= limit:
            break

    return selected


def search_report(evaluations: list[dict], gate: AnswerabilityGate, display_top_k: int = 3) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    passed = sum(1 for item in evaluations if item["passed"])
    statuses = ["answerable", "needs_time_context", "needs_clarification", "weak_evidence", "out_of_scope"]

    lines = [
        "# Search Evaluation Report",
        "",
        f"- Generated: **{generated_at}**",
        f"- Cases: **{len(evaluations)}**",
        f"- Passed: **{passed} / {len(evaluations)}**",
        f"- Results shown per case: **{display_top_k}**",
        "",
        "## Thresholds And Rules",
        "",
        "| Parameter | Value | Meaning |",
        "| --- | ---: | --- |",
        f"| `strong_score` | `{gate.strong_score:.2f}` | Reference threshold for strong retrieved evidence. |",
        f"| `weak_score` | `{gate.weak_score:.2f}` | Top score below this becomes `weak_evidence`. |",
        f"| `min_embedding_score` | `{gate.min_embedding_score:.2f}` | If BM25 is near zero and embedding is below this, evidence is weak. |",
        "| Multi-source closeness ratio | `0.72` | Results within 72% of the top score are considered close enough for multi-source detection. |",
        "| Logistics temporal boost | `+0.25` | Applied to time-sensitive files (e.g. `syllabus.md`) for logistics questions. |",
        "",
    ]

    for status in statuses:
        group = [item for item in evaluations if item["decision"]["status"] == status]
        if not group:
            continue
        lines.extend(
            [
                f"## {status}",
                "",
                "| Result | ID | Question | Score | Multi-source | Top evidence |",
                "| --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for item in group:
            mark = "PASS" if item["passed"] else "FAIL"
            decision = item["decision"]
            question = item["query"].replace("|", "\\|")
            lines.append(
                f"| {mark} | `{item['id']}` | {question} | `{decision['confidence']:.3f}` | "
                f"`{decision['multi_source']}` | {top_evidence(decision, item.get('expected_files', []), display_top_k)} |"
            )
        lines.append("")

    failed = [item for item in evaluations if not item["passed"]]
    if failed:
        lines.extend(["## Failed Checks", ""])
        for item in failed:
            lines.append(f"- `{item['id']}`: `{item['checks']}`")
        lines.append("")

    return "\n".join(lines)


def run_search(workspace, pipeline: QueryPipeline, questions: list[dict]) -> tuple[list[dict], Path]:
    evaluations = []
    for case in questions:
        retrieval_query = pipeline._contextual_query(case["query"], case.get("short_memory", []))
        results = pipeline.retriever.search(retrieval_query, top_k=pipeline.top_k)
        decision = pipeline.gate.decide(case["query"], results)
        if "expected_status" not in case:
            case = {**case, "expected_status": decision.status}
        evaluations.append(evaluate_search_case(case, results, decision, pipeline.retriever.normalize))
    return evaluations, save_report(workspace, "search", search_report(evaluations, pipeline.gate))


# Answers, dry run -----------------------------------------------------------------------------


def evidence_text(result: dict) -> str:
    parts = []
    for item in result.get("evidence", []):
        parts.extend([item.get("chunk_id", ""), item.get("file_path", ""), item.get("content", "")])
    return "\n".join(parts).lower()


def evaluate_answer_case(case: dict, result: dict) -> dict:
    checks = {}
    if "expected_status" in case:
        checks["status"] = result["status"] == case["expected_status"]
    if "expected_action" in case:
        checks["llm_action"] = result["llm_action"] == case["expected_action"]
    if "expected_temporal_context" in case:
        checks["temporal_context"] = result["temporal_context"] == case["expected_temporal_context"]
    if case.get("short_memory"):
        policy = result["prompt_package"]["answer_policy"]
        checks["memory_is_session_only"] = policy["memory_persistence"] == "cleared_when_window_or_session_closes"
        checks["short_memory_included"] = bool(result["short_memory"])
    if "expected_citation_terms" in case:
        haystack = evidence_text(result)
        checks["citation_terms"] = any(term.lower() in haystack for term in case["expected_citation_terms"])
    return {"id": case["id"], "query": case["query"], "passed": all(checks.values()), "checks": checks, "result": result}


def answers_report(evaluations: list[dict]) -> str:
    passed = sum(item["passed"] for item in evaluations)
    lines = [
        "# Answer Evaluation Report (dry run)",
        "",
        f"- Generated: **{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**",
        f"- Questions: **{len(evaluations)}**",
        f"- Passed: **{passed} / {len(evaluations)}**",
        "",
        "| Result | ID | Status | Action | Temporal context | Evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in evaluations:
        result = item["result"]
        evidence = "<br>".join(f"`{entry['chunk_id']}` `{entry['score']:.3f}`" for entry in result.get("evidence", [])[:3])
        lines.append(
            f"| {'PASS' if item['passed'] else 'FAIL'} | `{item['id']}` | `{result['status']}` | `{result['llm_action']}` | "
            f"`{result['temporal_context']}` | {evidence} |"
        )
    failed = [item for item in evaluations if not item["passed"]]
    if failed:
        lines.extend(["", "## Failed Checks", ""])
        lines.extend(f"- `{item['id']}`: `{item['checks']}`" for item in failed)
    return "\n".join(lines)


def run_answers(workspace, pipeline: QueryPipeline, questions: list[dict]) -> tuple[list[dict], Path]:
    generator = AnswerGenerator(pipeline=pipeline, provider=provider_from_name("dry_run"), course_name=workspace.display_name)
    evaluations = [
        evaluate_answer_case(case, generator.answer(case["query"], short_memory=case.get("short_memory", [])))
        for case in questions
    ]
    return evaluations, save_report(workspace, "answers", answers_report(evaluations))


# Real model answers ---------------------------------------------------------------------------


def answer_with_retry(generator: AnswerGenerator, case: dict, retries: int, retry_delay: float) -> dict:
    last_error = None
    for attempt in range(retries + 1):
        try:
            result = generator.answer(case["query"], short_memory=case.get("short_memory", []))
            result.pop("raw_response", None)
            result.pop("prompt_package", None)
            return {"case": case, "ok": True, "attempts": attempt + 1, "result": result, "error": None}
        except Exception as exc:  # noqa: BLE001 - report every provider failure
            last_error = exc
            if attempt < retries:
                time.sleep(retry_delay)
    return {
        "case": case,
        "ok": False,
        "attempts": retries + 1,
        "result": None,
        "error": f"{type(last_error).__name__}: {last_error}",
    }


def real_checks(case: dict, result: dict | None) -> dict:
    if not result:
        return {"llm_call": False}
    checks = {"llm_call": True}
    if "expected_status" in case:
        checks["status"] = result["status"] == case["expected_status"]
    if "expected_temporal_context" in case:
        checks["temporal_context"] = result["temporal_context"] == case["expected_temporal_context"]
    checks["has_answer"] = bool(result.get("answer", "").strip())
    return checks


def real_report(items: list[dict], provider: str, model: str | None) -> str:
    ok_count = sum(item["ok"] for item in items)
    lines = [
        "# Real Model Answers",
        "",
        f"- Generated: **{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**",
        f"- Provider: **{provider}**",
        f"- Model: **{model}**",
        f"- Model calls succeeded: **{ok_count} / {len(items)}**",
        "",
    ]
    for item in items:
        case, result = item["case"], item["result"]
        lines.extend([f"## {case['id']}", "", f"- Question: {case['query']}", f"- Checks: `{real_checks(case, result)}`"])
        if result:
            sources = "<br>".join(f"`{s['title']}` `{s['file_path']}`" for s in result.get("sources", [])[:3])
            lines.extend([f"- Status: `{result['status']}`", f"- Sources: {sources or 'none'}", "", result["answer"], ""])
        else:
            lines.extend(["", f"**Error:** `{item['error']}`", ""])
    return "\n".join(lines)


def run_real(workspace, pipeline: QueryPipeline, questions: list[dict], retries: int = 1, retry_delay: float = 5.0):
    provider_name = default_provider_name()
    if provider_name == "dry_run":
        raise RuntimeError("No language model is configured. Run `wendao doctor` for help.")
    model = default_model_name(provider_name)
    generator = AnswerGenerator(
        pipeline=pipeline,
        prompt_builder=PromptBuilder(course_name=workspace.display_name),
        provider=provider_from_name(provider_name, model=model),
        course_name=workspace.display_name,
    )
    items = [answer_with_retry(generator, case, retries, retry_delay) for case in questions]
    return items, save_report(workspace, "real", real_report(items, provider_name, model))
