from __future__ import annotations

import contextlib
import io
import json
import os
import sys
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
        questions, had = suggest.suggest_questions(self.workspace, model, suggest.load_chapters(self.workspace), existing, say=lambda *_: None)
        self.assertEqual(had, 1)
        self.assertEqual(len(questions), 1)
        question = questions[0]
        self.assertEqual(question["expected_files"], ["thermo/hull.md"])
        self.assertEqual(question["expected_terms"], ["stable phases"])
        self.assertEqual(question["expected_status"], "answerable")

    def test_odd_model_replies_do_not_crash_or_merge_concepts(self):
        model = FakeModel([json.dumps([
            {"label": "Convex Hull", "aliases": "convex hull"},          # one string, not a list: not split into letters
            {"label": "Hull", "aliases": ["hull"]},
            {"label": "hull", "aliases": ["the hull"]},                  # same concept again: merged
            {"label": "Stable phases", "aliases": 5},                    # nonsense aliases: the label still counts
        ])])
        concepts, _ = suggest.suggest_concepts(self.workspace, model, suggest.load_chapters(self.workspace), [], say=lambda *_: None)
        by_id = {item["id"]: item for item in concepts}
        self.assertEqual(by_id["convex-hull"]["aliases"], ["convex hull"])
        self.assertEqual(sorted(by_id["hull"]["aliases"]), ["hull", "the hull"])
        self.assertEqual(by_id["stable-phases"]["aliases"], ["stable phases"])
        self.assertEqual(suggest.slug("C++"), "c")
        self.assertEqual(suggest.slug("凸包"), "凸包")  # not "item" for every non-English label

        model = FakeModel([json.dumps([
            {"question": "Why do phases above the hull decompose?", "page": "thermo/hull.md", "key_terms": "stable phases"},
            {"question": "What does the hull connect in a diagram?", "page": "thermo/hull.md", "key_terms": 42},
            {"question": "What happens to phases at the edge?", "page": "thermo/hull.md", "key_terms": ["phase"]},  # only inside "phases"
        ])])
        questions, _ = suggest.suggest_questions(self.workspace, model, suggest.load_chapters(self.workspace), [], say=lambda *_: None)
        self.assertEqual([item["expected_terms"] for item in questions], [["stable phases"]])

    def test_different_concepts_with_the_same_id_stay_apart(self):
        chapters = [("code", "Code", [{"file_path": "code/c.md", "content": "C and C++ are languages."}])]
        model = FakeModel([json.dumps([{"label": "C", "aliases": ["c"]}, {"label": "C++", "aliases": ["c++"]}])])
        concepts, _ = suggest.suggest_concepts(self.workspace, model, chapters, [], say=lambda *_: None)
        self.assertEqual(sorted((item["id"], item["label"]) for item in concepts), [("c", "C"), ("c-2", "C++")])

    def test_excerpt_covers_the_whole_chapter_within_the_budget(self):
        chunks = [{"content": f"Part {number} " + "x" * 500} for number in range(200)]
        text = suggest.excerpt(chunks, 4000)
        self.assertLessEqual(len(text), 4000)
        self.assertIn("Part 0 ", text)
        self.assertIn("Part 199 ", text)  # later parts are not dropped

    def test_a_typo_in_concepts_json_is_explained(self):
        (self.root / "concepts.json").write_text('{"version": 1, "concepts": [\n  {"label": "Hull",}\n]}', encoding="utf-8")
        with self.assertRaisesRegex(suggest.SuggestError, "isn't valid JSON \\(line 2"):
            suggest.read_concepts(self.workspace.concepts_path)

    def test_found_items_are_added_once_and_old_concepts_kept(self):
        found = [{"id": "convex-hull", "label": "Convex Hull", "category": "thermo", "aliases": ["convex hull"],
                  "mentions": 3, "chapter": "Thermodynamics"}]
        backup = suggest.add_concepts(self.workspace, found)
        self.assertIn("example-concept", backup.read_text(encoding="utf-8"))  # the list from before
        suggest.add_concepts(self.workspace, found)
        concepts = suggest.read_concepts(self.workspace.concepts_path)
        self.assertEqual([item["id"] for item in concepts], ["entropy", "convex-hull"])  # the starter example is gone
        self.assertNotIn("mentions", concepts[1])
        question = {"id": "why-decompose", "query": "Why do phases above the hull decompose?", "expected_status": "answerable",
                    "expected_files": ["thermo/hull.md"], "expected_terms": ["stable phases"], "chapter": "Thermodynamics"}
        suggest.add_questions(self.workspace, [question])
        suggest.add_questions(self.workspace, [question])
        questions = suggest.read_questions(self.workspace.questions_path)
        self.assertEqual([item["id"] for item in questions], ["hull", "why-decompose"])
        self.assertNotIn("chapter", questions[1])

    def test_build_without_a_model_explains_what_is_missing(self):
        keep = {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_API", "LLM_"))}
        errors = io.StringIO()
        with mock.patch.dict(os.environ, keep, clear=True), contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            from wendao.cli import step_concepts
            try:
                step_concepts(self.workspace)
            except suggest.SuggestError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                raise SystemExit(1) from None
        self.assertIn("none is set up", errors.getvalue())

    def test_build_concepts_and_eval_add_questions(self):
        model = FakeModel([json.dumps([{"label": "Convex Hull", "aliases": ["convex hull"]}]),
                           json.dumps([{"question": "Why do phases above the hull decompose?", "page": "thermo/hull.md",
                                        "key_terms": ["stable phases"]}])])
        from wendao.cli import step_concepts, step_questions

        output = io.StringIO()
        with mock.patch.object(suggest, "course_model", return_value=(model, "fake model")), contextlib.redirect_stdout(output):
            step_concepts(self.workspace)
            step_questions(self.workspace)
        text = output.getvalue()
        self.assertIn("Added 1 concepts to concepts.json", text)
        self.assertIn("Convex Hull", text)
        self.assertIn("concepts.json.bak", text)
        self.assertIn("Added 1 questions to questions.json", text)
        self.assertIn("convex-hull", (self.root / "concepts.json").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
