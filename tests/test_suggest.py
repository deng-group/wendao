from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wendao import suggest
from wendao import workspace as workspace_module
from wendao.cli import main

CONFIG = """[course]
name = "Demo Course"

[source]
path = "notes"

[search]
embedding_model = "tfidf"

[[chapters]]
folder = "thermo"
name = "Thermodynamics"

[[chapters]]
folder = "figures"
show = "hidden"
"""

CHUNKS = [
    ("syllabus.md", "root", "Syllabus. Grading: final exam 40 percent."),
    ("thermo/hull.md", "thermo", "# Convex hull\nThe convex hull connects the stable phases. Phases above the hull "
                                 "decompose. The formation energy decides stability."),
    ("thermo/entropy.md", "thermo", "# Entropy\nConfigurational entropy grows with disorder."),
    ("figures/plots.md", "figures", "Matplotlib figure code."),
]


class FakeModel:
    """Answers like a language model would, including the usual mistakes."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, package):
        self.prompts.append(package["messages"][1]["content"])
        return {"answer": self.replies.pop(0) if self.replies else "[]"}


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "notes").mkdir()
        (self.root / "wendao.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / "concepts.json").write_text(json.dumps({"version": 1, "concepts": [
            {"id": "example-concept", "label": "Example Concept", "category": "general", "aliases": ["example concept"]},
            {"id": "entropy", "label": "Entropy", "category": "thermo", "aliases": ["entropy"]}]}), encoding="utf-8")
        (self.root / "questions.json").write_text(json.dumps([
            {"id": "hull", "query": "What is a convex hull?", "expected_status": "answerable"}]), encoding="utf-8")
        self.workspace = workspace_module.load(self.root)
        self.workspace.build_dir.mkdir()
        with self.workspace.chunks_path.open("w", encoding="utf-8") as out:
            for number, (path, module, content) in enumerate(CHUNKS):
                out.write(json.dumps({"chunk_id": f"{path}_{number}", "file_path": path, "module": module,
                                      "title": path, "content": content}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_only_chapters_students_see(self):
        chapters = suggest.load_chapters(self.workspace)
        self.assertEqual([(folder, name) for folder, name, _ in chapters], [("thermo", "Thermodynamics")])

    def test_keeps_only_concepts_the_notes_use(self):
        model = FakeModel(['Sure!\n```json\n' + json.dumps([
            {"label": "Convex Hull", "aliases": ["convex hull", "hull"]},
            {"label": "Formation Energy", "aliases": ["formation energy", "e_f"]},
            {"label": "Quantum Flux", "aliases": ["flux capacitor"]},   # not in the notes: made up
            {"label": "Entropy", "aliases": ["entropy"]},               # already in concepts.json
        ]) + "\n```"])
        existing = suggest.read_concepts(self.workspace.concepts_path)
        concepts, already = suggest.suggest_concepts(self.workspace, model, suggest.load_chapters(self.workspace), existing, say=lambda *_: None)
        self.assertEqual([item["id"] for item in concepts], ["convex-hull", "formation-energy"])
        self.assertEqual(concepts[0]["aliases"], ["convex hull", "hull"])  # "e_f" was dropped: the notes don't use it
        self.assertEqual(concepts[1]["aliases"], ["formation energy"])
        self.assertEqual(concepts[0]["category"], "thermo")
        self.assertEqual(already, 1)
        self.assertIn("Chapter: Thermodynamics", model.prompts[0])
        self.assertIn("connects the stable phases", model.prompts[0])

    def test_keeps_only_fair_questions(self):
        model = FakeModel([json.dumps([
            {"question": "Why do phases above the hull decompose?", "page": "thermo/hull.md", "key_terms": ["stable phases", "the", "free energy"]},
            {"question": "What is a convex hull?", "page": "thermo/hull.md", "key_terms": ["convex hull"]},     # already a test question
            {"question": "What is entropy?", "page": "thermo/missing.md", "key_terms": ["entropy"]},           # no such page
            {"question": "How does disorder change entropy?", "page": "thermo/entropy.md", "key_terms": ["kelvin"]},  # term not on the page
        ])])
        existing = suggest.read_questions(self.workspace.questions_path)
        questions = suggest.suggest_questions(self.workspace, model, suggest.load_chapters(self.workspace), existing, say=lambda *_: None)
        self.assertEqual(len(questions), 1)
        question = questions[0]
        self.assertEqual(question["expected_files"], ["thermo/hull.md"])
        self.assertEqual(question["expected_terms"], ["stable phases"])
        self.assertEqual(question["expected_status"], "answerable")

    def test_a_reply_that_is_not_json_is_skipped(self):
        model = FakeModel(["Sorry, I can't help with that."])
        concepts, _ = suggest.suggest_concepts(self.workspace, model, suggest.load_chapters(self.workspace), [], say=lambda *_: None)
        self.assertEqual(concepts, [])

    def test_add_copies_the_checked_drafts_once(self):
        folder = suggest.suggestions_dir(self.workspace)
        suggest.write_json(folder / "concepts.json", {"version": 1, "concepts": [
            {"id": "convex-hull", "label": "Convex Hull", "category": "thermo", "aliases": ["convex hull"], "mentions": 3, "chapter": "Thermodynamics"}]})
        suggest.write_json(folder / "questions.json", [
            {"id": "why-decompose", "query": "Why do phases above the hull decompose?", "expected_status": "answerable",
             "expected_files": ["thermo/hull.md"], "expected_terms": ["stable phases"], "chapter": "Thermodynamics", "search_finds_it": False}])
        self.assertEqual(suggest.add_to_workspace(self.workspace), {"concepts": 1, "questions": 1})
        self.assertEqual(suggest.add_to_workspace(self.workspace), {"concepts": 0, "questions": 0})
        concepts = suggest.read_concepts(self.workspace.concepts_path)
        self.assertEqual([item["id"] for item in concepts], ["entropy", "convex-hull"])  # the starter example is gone
        self.assertNotIn("mentions", concepts[1])
        questions = suggest.read_questions(self.workspace.questions_path)
        self.assertEqual(len(questions), 2)
        self.assertNotIn("search_finds_it", questions[1])

    def test_command_explains_what_is_missing(self):
        keep = {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_API", "LLM_"))}
        errors = io.StringIO()
        with mock.patch.dict(os.environ, keep, clear=True), contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            main(["suggest", "-w", str(self.root)])
        self.assertIn("none is set up", errors.getvalue())
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            main(["suggest", "--add", "-w", str(self.root)])
        self.assertIn("Run `wendao suggest` first", errors.getvalue())

    def test_command_writes_drafts_and_leaves_your_files_alone(self):
        before = (self.root / "concepts.json").read_text(encoding="utf-8")
        model = FakeModel([json.dumps([{"label": "Convex Hull", "aliases": ["convex hull"]}]),
                           json.dumps([{"question": "Why do phases above the hull decompose?", "page": "thermo/hull.md",
                                        "key_terms": ["stable phases"]}])])
        output = io.StringIO()
        with mock.patch.object(suggest, "course_model", return_value=(model, "fake model")), \
                contextlib.redirect_stdout(output):
            main(["suggest", "-w", str(self.root)])
        text = output.getvalue()
        self.assertIn("1 new concepts", text)
        self.assertIn("1 test questions", text)
        self.assertIn("wendao suggest --add", text)
        self.assertEqual((self.root / "concepts.json").read_text(encoding="utf-8"), before)
        drafts = suggest.read_questions(self.root / "suggestions" / "questions.json")
        self.assertIn("search_finds_it", drafts[0])


if __name__ == "__main__":
    unittest.main()
