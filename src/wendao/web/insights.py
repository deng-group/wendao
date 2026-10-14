"""Teacher insights: what students ask about, and where the notes fall short (`wendao insights`).

After the answer check, each question is noted in usage.db: the day, where it was asked (the graph website or the
widget), the outcome, the concepts it mentions, and the pages search found for it. No names, emails, or addresses are
kept, and the question's own words only when the teacher chooses `[student] insights = "questions"`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from wendao.graph import alias_pattern, normalized_text

INSIGHT_MODES = ("counts", "questions", "off")
# Answer-check outcomes, in the words the report uses.
OUTCOMES = {
    "answerable": "answered",
    "needs_time_context": "answered, with the course term",
    "needs_clarification": "too broad (asked to narrow it)",
    "weak_evidence": "not in the notes",
    "out_of_scope": "outside the course",
}


class InsightStore:
    def __init__(self, path: Path, keep_questions: bool = False):
        self.path = path
        self.keep_questions = keep_questions
        self._lock = threading.Lock()
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS asks (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, day TEXT, app TEXT, "
                "status TEXT, concepts TEXT, files TEXT, question TEXT)"
            )

    def add(self, app: str, status: str, concepts: list[str], files: list[str], question: str = "") -> None:
        now = datetime.now()
        row = (now.isoformat(timespec="seconds"), now.date().isoformat(), app, status, json.dumps(concepts[:8]),
               json.dumps(files[:3]), " ".join(question.split())[:500] if self.keep_questions else None)
        with self._lock, closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("INSERT INTO asks (time, day, app, status, concepts, files, question) VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    def recent(self, days: int = 7, today: date | None = None) -> list[dict]:
        since = ((today or date.today()) - timedelta(days=days - 1)).isoformat()
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            rows = db.execute(
                "SELECT day, app, status, concepts, files, question FROM asks WHERE day >= ? ORDER BY id", (since,)
            ).fetchall()
        return [{"day": day, "app": app, "status": status, "concepts": json.loads(concepts or "[]"),
                 "files": json.loads(files or "[]"), "question": question} for day, app, status, concepts, files, question in rows]


def concepts_in(text: str, graph: dict | None) -> list[str]:
    """The graph's concepts that a question (or the node it was asked about) mentions, by their names and aliases."""
    if not graph or not text.strip():
        return []
    haystack = normalized_text(text)
    found = []
    for node in graph.get("nodes", []):
        if node.get("type") != "keyword":
            continue
        names = {normalized_text(name) for name in [node.get("label", ""), *node.get("aliases", [])] if name}
        if any(name and alias_pattern(name).search(haystack) for name in names):
            found.append(node["id"])
    return found


def store_opener(workspace):
    """A function giving the course's InsightStore, made on the first question (or None when insights are off)."""
    made: list[InsightStore] = []

    def open_store() -> InsightStore | None:
        mode = getattr(workspace, "insights", "counts")
        if mode == "off":
            return None
        if not made:
            made.append(InsightStore(Path(workspace.root) / "usage.db", keep_questions=mode == "questions"))
        return made[0]
    return open_store


def recorder(store_for, app: str, graph_for):
    """An `on_decision` callback for AnswerGenerator that notes each question in the insight store.

    `store_for()` gives the InsightStore (or None when insights are off), `graph_for()` the current graph.
    """
    def record(query: str, pipeline_result: dict, prompt_package: dict, context: list[str]) -> None:
        store = store_for()
        if store is None:
            return
        files = []
        for item in pipeline_result.get("evidence", []):
            path = item.get("file_path")
            if path and path not in files:
                files.append(path)
        concepts = concepts_in(" ".join([query, *context]), graph_for())
        store.add(app, str(prompt_package.get("status", "")), concepts, files, query)
    return record


def report(items: list[dict], graph: dict | None, feedback_down: int = 0, limit: int = 8) -> list[str]:
    """The `wendao insights` text: outcomes, what students ask about most, and the gaps in the notes."""
    if not items:
        return ["No questions yet. They are counted when students ask on the graph website or in the chat widget."]
    nodes = {node["id"]: node for node in (graph or {}).get("nodes", [])}
    page_of = {}
    for node in nodes.values():
        if node.get("type") == "topic":
            for path in node.get("source_files", []):
                page_of.setdefault(path, node)
    lines = []
    total = len(items)
    apps = Counter(item["app"] for item in items)
    where = ", ".join(f"{count} on the {'chat widget' if app == 'widget' else 'graph website'}" for app, count in apps.most_common())
    lines.append(f"Questions: {total} ({where})")
    outcomes = Counter(item["status"] for item in items)
    for status, words in OUTCOMES.items():
        if outcomes[status]:
            lines.append(f"  {words:<34}{outcomes[status]:>5}  {100 * outcomes[status] / total:4.0f}%")
    days = Counter(item["day"] for item in items)
    lines.append("By day: " + ", ".join(f"{day[5:]} {count}" for day, count in sorted(days.items())))

    def top(title: str, counter: Counter, name) -> None:
        if not counter:
            return
        lines.append("")
        lines.append(title)
        for key, count in counter.most_common(limit):
            lines.append(f"  {count:>4}  {name(key)}")

    top("Concepts asked about most:", Counter(c for item in items for c in item["concepts"]),
        lambda key: nodes.get(key, {}).get("label", key))
    answered = [item for item in items if item["status"] in {"answerable", "needs_time_context"}]
    top("Pages most used in answers:", Counter(path for item in answered for path in item["files"][:1]),
        lambda path: f"{page_of[path]['label']} ({path})" if path in page_of else path)
    chapters = Counter(nodes.get(page_of[path].get("chapter_id"), {}).get("label") for item in answered
                       for path in item["files"][:1] if path in page_of)
    chapters.pop(None, None)
    top("Chapters asked about most:", chapters, lambda label: label)

    gaps = [item for item in items if item["status"] == "weak_evidence"]
    lines.append("")
    if not gaps:
        lines.append("Not in the notes: none. Every in-course question found its answer in the notes.")
    else:
        lines.append(f"Not in the notes: {len(gaps)} question{'s' if len(gaps) > 1 else ''} (your notes may need more on these)")
        texts = [item["question"] for item in gaps if item["question"]]
        for text in texts[-limit:]:
            lines.append(f"  - {text}")
        if not texts:
            near = Counter(path for item in gaps for path in item["files"][:1])
            for path, count in near.most_common(limit):
                lines.append(f"  {count:>4}  closest page: {page_of[path]['label'] if path in page_of else path}")
            lines.append('  To see the questions themselves, set [student] insights = "questions" in wendao.toml.')
    if feedback_down:
        lines.append("")
        lines.append(f"Answers rated not helpful: {feedback_down}. See them with `wendao feedback`.")
    return lines
