from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from wendao.cli import main
from wendao.web.insights import InsightStore, concepts_in, recorder, report

GRAPH = {"nodes": [
    {"id": "keyword:convex-hull", "type": "keyword", "label": "Convex Hull", "aliases": ["convex hull", "convex hulls"]},
    {"id": "keyword:dft", "type": "keyword", "label": "Density Functional Theory", "aliases": ["dft"]},
    {"id": "chapter:thermo", "type": "chapter", "label": "Thermodynamics"},
    {"id": "topic:thermo-hull", "type": "topic", "label": "Hulls", "chapter_id": "chapter:thermo", "source_files": ["thermo/hull.md"]},
]}


class InsightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "usage.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_question_text_is_kept_only_when_the_teacher_asks_for_it(self):
        InsightStore(self.db).add("widget", "weak_evidence", [], ["thermo/hull.md"], "my private question")
        InsightStore(self.db, keep_questions=True).add("widget", "weak_evidence", [], [], "What is the Hubbard U?")
        rows = sqlite3.connect(self.db).execute("SELECT question FROM asks ORDER BY id").fetchall()
        self.assertEqual(rows, [(None,), ("What is the Hubbard U?",)])
        self.assertNotIn("sender", [column[1] for column in sqlite3.connect(self.db).execute("PRAGMA table_info(asks)")])

    def test_concepts_are_found_by_name_and_alias(self):
        self.assertEqual(concepts_in("How are convex hulls built from DFT energies?", GRAPH), ["keyword:convex-hull", "keyword:dft"])
        self.assertEqual(concepts_in("What is the weather?", GRAPH), [])

    def test_recorder_notes_the_outcome_concepts_and_pages(self):
        store = InsightStore(self.db)
        record = recorder(lambda: store, "website", lambda: GRAPH)
        record("Explain this", {"evidence": [{"file_path": "thermo/hull.md"}, {"file_path": "thermo/hull.md"}]},
               {"status": "answerable"}, ["Convex Hull"])
        [item] = store.recent(1)
        self.assertEqual((item["app"], item["status"], item["concepts"], item["files"]),
                         ("website", "answerable", ["keyword:convex-hull"], ["thermo/hull.md"]))
        recorder(lambda: None, "website", lambda: GRAPH)("ignored", {}, {"status": "answerable"}, [])  # insights off

    def test_report(self):
        store = InsightStore(self.db)
        store.add("website", "answerable", ["keyword:convex-hull"], ["thermo/hull.md"])
        store.add("widget", "weak_evidence", [], ["thermo/hull.md"], "not kept")
        store.add("widget", "out_of_scope", [], [])
        text = "\n".join(report(store.recent(7), GRAPH, feedback_down=1))
        self.assertIn("Questions: 3 (2 on the chat widget, 1 on the graph website)", text)
        self.assertIn("not in the notes", text)
        self.assertIn("1  Convex Hull", text)
        self.assertIn("Hulls (thermo/hull.md)", text)
        self.assertIn("1  Thermodynamics", text)
        self.assertIn("closest page: Hulls", text)
        self.assertIn('[student] insights = "questions"', text)  # how to see the questions themselves
        self.assertIn("Answers rated not helpful: 1", text)
        self.assertNotIn("not kept", text)

    def test_command(self):
        root = Path(self.tmp.name)
        (root / "wendao.toml").write_text('[course]\nname = "Demo"\n', encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["insights", "-w", str(root)])
        self.assertIn("No questions yet", output.getvalue())
        InsightStore(root / "usage.db").add("widget", "answerable", [], [])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["insights", "-w", str(root), "--days", "1"])
        self.assertIn("What students asked in today", output.getvalue())
        self.assertIn("Questions: 1", output.getvalue())

        (root / "wendao.toml").write_text('[course]\nname = "Demo"\n[student]\ninsights = "off"\n', encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["insights", "-w", str(root)])
        self.assertIn("Insights are off", output.getvalue())


if __name__ == "__main__":
    unittest.main()
