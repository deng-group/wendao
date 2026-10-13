from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wendao import workspace as workspace_module
from wendao.cli import main
from wendao.graph import build_graph, load_jsonl, validate
from wendao.workspace import WorkspaceError

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "mle4217_5219"


def run_cli(*argv: str) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        main(list(argv))
    return output.getvalue()


def make_course(root: Path) -> None:
    """A tiny course: two chapters, a syllabus, three concepts, two test questions."""
    (root / "notes" / "week01").mkdir(parents=True)
    (root / "notes" / "week02").mkdir(parents=True)
    (root / "notes" / "week01" / "intro.md").write_text(
        "# Newton's Laws\n\nNewton's second law relates force, mass, and acceleration: F = ma.\n"
        "A net force changes motion.\n",
        encoding="utf-8",
    )
    (root / "notes" / "week02" / "energy.md").write_text(
        "# Energy\n\nKinetic energy is the energy of motion. Work done by a force changes kinetic energy.\n",
        encoding="utf-8",
    )
    (root / "notes" / "syllabus.md").write_text(
        "# Syllabus\n- Year: AY2026/2027 Semester 1\n\nThe final exam is in week 13.\n", encoding="utf-8"
    )
    (root / "concepts.json").write_text(
        json.dumps(
            {
                "version": 1,
                "concepts": [
                    {"id": "force", "label": "Force", "category": "mechanics", "aliases": ["force"]},
                    {"id": "kinetic-energy", "label": "Kinetic Energy", "category": "energy", "aliases": ["kinetic energy"]},
                    {"id": "motion", "label": "Motion", "category": "mechanics", "aliases": ["motion"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "questions.json").write_text(
        json.dumps(
            [
                {"id": "energy", "query": "What is kinetic energy?", "expected_status": "answerable", "expected_files": ["week02/energy.md"]},
                {"id": "exam", "query": "When is the final exam?", "expected_status": "needs_time_context",
                 "expected_temporal_context": "AY2026/2027 Semester 1"},
            ]
        ),
        encoding="utf-8",
    )


class WorkspaceCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "physics-101"
        # Keep the host's API keys and model settings out of these tests.
        self.env = mock.patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def init_course(self) -> None:
        run_cli("init", str(self.root))
        make_course(self.root)
        config = self.root / "wendao.toml"
        # TF-IDF search needs no model download, which keeps the test fast and offline.
        config.write_text(
            config.read_text(encoding="utf-8").replace("[search]", '[search]\nembedding_model = "tfidf"'),
            encoding="utf-8",
        )

    def test_init_creates_starter_files_and_keeps_existing_ones(self):
        output = run_cli("init", str(self.root))
        for name in ["wendao.toml", "concepts.json", "questions.json", ".env", ".gitignore"]:
            self.assertTrue((self.root / name).is_file(), name)
        self.assertTrue((self.root / "notes").is_dir())
        self.assertIn("Workspace ready", output)

        (self.root / "concepts.json").write_text("{}", encoding="utf-8")
        output = run_cli("init", str(self.root))
        self.assertIn("kept     concepts.json", output)
        self.assertEqual((self.root / "concepts.json").read_text(encoding="utf-8"), "{}")

        workspace = workspace_module.load(self.root)
        self.assertEqual(workspace.course_name, "Physics 101")
        self.assertEqual(workspace.source, self.root.resolve() / "notes")

    def test_build_ask_and_eval(self):
        self.init_course()
        output = run_cli("build", "-w", str(self.root))
        self.assertIn("3 files (markdown) → 3 chunks", output)
        self.assertIn("Term: AY2026/2027 Semester 1", output)
        self.assertNotIn("No concepts yet", output)
        for path in ["build/chunks.jsonl", "build/graph.json"]:
            self.assertTrue((self.root / path).is_file(), path)

        output = run_cli("ask", "-w", str(self.root), "--search-only", "what is kinetic energy")
        self.assertIn("Decision: answerable", output)
        self.assertIn("1. week02/energy.md", output)

        output = run_cli("ask", "-w", str(self.root), "--provider", "dry_run", "When is the final exam?")
        self.assertIn("needs_time_context", output)
        self.assertIn("syllabus.md", output)

        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.suppress(SystemExit):  # no model set up here
            main(["doctor", "-w", str(self.root), "--no-model"])
        self.assertIn("Questions: 2/2 as expected", output.getvalue())
        self.assertNotIn("Question q", output.getvalue())
        self.assertTrue((self.root / "build/reports/search.md").is_file())
        self.assertTrue((self.root / "build/reports/answers.md").is_file())

    def test_doctor_says_how_to_fix_problems(self):
        self.init_course()
        (self.root / "questions.json").unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(["doctor", "-w", str(self.root), "--no-model"])
        output = output.getvalue()
        self.assertIn("Build:     not built yet", output)
        self.assertIn("Fix: Run `wendao build`.", output)

        run_cli("build", "-w", str(self.root))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(["doctor", "-w", str(self.root), "--no-model"])
        self.assertIn("wendao doctor --add-questions", output.getvalue())
        self.assertIn("Build:     ok", output.getvalue())

        (self.root / "notes" / "week02" / "energy.md").write_text("# Energy\n\nChanged.\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(["doctor", "-w", str(self.root), "--no-model"])
        self.assertIn("your notes changed after the last build (week02/energy.md)", output.getvalue())

    def test_model_fix_reads_the_error(self):
        from wendao.cli import model_fix

        self.assertIn("ANTHROPIC_AUTH_TOKEN", model_fix("anthropic", "API error 401: invalid x-api-key"))
        self.assertIn("busy", model_fix("anthropic", '{"error":{"message":"No available accounts"}}'))
        self.assertIn("model name", model_fix("openai", "HTTP 404: model not found"))
        self.assertIn("base_url", model_fix("openai", "Connection refused"))
        self.assertIn("[model]", model_fix("dry_run", "No language model is configured."))

    def use_starter_concepts(self):
        starter = Path(__file__).resolve().parents[1] / "src" / "wendao" / "starter" / "concepts.json"
        (self.root / "concepts.json").write_text(starter.read_text(encoding="utf-8"), encoding="utf-8")

    def test_build_says_what_to_do_without_concepts(self):
        self.init_course()
        self.use_starter_concepts()
        keep = {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_API", "LLM_"))}
        with mock.patch.dict(os.environ, keep, clear=True):  # no model: nothing is sent anywhere
            output = run_cli("build", "-w", str(self.root))
        self.assertIn("0 concepts", output)
        self.assertIn("No concepts yet", output)

    def test_first_build_finds_concepts_when_a_model_is_set_up(self):
        from wendao import suggest

        class FakeModel:
            def generate(self, package):
                return {"answer": json.dumps([{"label": "Kinetic Energy", "aliases": ["kinetic energy"]}])}

        self.init_course()
        self.use_starter_concepts()
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "openai"}), \
                mock.patch.object(suggest, "course_model", return_value=(FakeModel(), "fake model")):
            output = run_cli("build", "-w", str(self.root))
            self.assertIn("Finding concepts", output)
            self.assertIn("Kinetic Energy", output)
            self.assertNotIn("No concepts yet", output)
            again = run_cli("build", "-w", str(self.root))  # it has concepts now: not asked again
        self.assertNotIn("Finding concepts", again)

    def test_chapter_names_from_folders(self):
        from wendao.workspace import folder_title

        self.assertEqual(folder_title("models_and_theories_II"), "Models and Theories II")
        self.assertEqual(folder_title("intro-to-DFT"), "Intro to DFT")
        self.assertEqual(folder_title("week02"), "Week02")

    def test_commands_find_the_workspace_from_a_subfolder(self):
        self.init_course()
        subfolder = self.root / "notes" / "week01"
        self.assertEqual(workspace_module.find_root(subfolder), self.root.resolve())

    def test_ask_without_a_model_explains_what_to_do(self):
        self.init_course()
        run_cli("build", "-w", str(self.root))
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            run_cli("ask", "-w", str(self.root), "What is kinetic energy?")
        self.assertIn("--search-only", errors.getvalue())

    def test_model_settings_fill_in_environment_without_overriding_it(self):
        self.init_course()
        config = self.root / "wendao.toml"
        config.write_text(
            config.read_text(encoding="utf-8")
            .replace('# provider = "openai"', 'provider = "openai"')
            .replace('# model = "gpt-4.1-mini"', 'model = "from-config"'),
            encoding="utf-8",
        )
        (self.root / ".env").write_text("export OPENAI_API_KEY=sk-from-env-file\n", encoding="utf-8")
        os.environ["OPENAI_MODEL"] = "from-shell"

        workspace_module.load(self.root).apply_model_settings()

        self.assertEqual(os.environ["LLM_PROVIDER"], "openai")
        self.assertEqual(os.environ["OPENAI_MODEL"], "from-shell")
        self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-from-env-file")

    def test_missing_workspace_and_bad_settings_are_reported(self):
        with self.assertRaisesRegex(WorkspaceError, "wendao init"):
            workspace_module.load(Path(self.tmp.name))
        self.root.mkdir()
        (self.root / "wendao.toml").write_text('[[chapters]]\nfolder = "a"\nshow = "sometimes"\n', encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "show"):
            workspace_module.load(self.root)


class ExampleWorkspaceTest(unittest.TestCase):
    """The bundled MLE4217/5219 example must stay loadable and its graph reproducible."""

    def test_example_graph_is_reproducible(self):
        workspace = workspace_module.load(EXAMPLE)
        taxonomy = json.loads(workspace.concepts_path.read_text(encoding="utf-8"))
        graph = build_graph(
            load_jsonl(workspace.chunks_path),
            taxonomy,
            chapters=workspace.chapters,
            bridge_stop_concepts=set(workspace.bridge_stop_concepts),
            course={"code": workspace.course_code, "title": workspace.course_name, "website": workspace.website},
        )
        committed = json.loads(workspace.graph_path.read_text(encoding="utf-8"))
        self.assertEqual(validate(graph), [])
        self.assertEqual(graph["nodes"], committed["nodes"])
        self.assertEqual(graph["edges"], committed["edges"])

        # Links the course relies on in teaching; these should survive graph changes.
        related = {frozenset((e["source"], e["target"])) for e in graph["edges"] if e["type"] == "related"}
        for pair in [
            {"keyword:materials-project", "keyword:mace"},
            {"keyword:molecular-dynamics", "keyword:mace"},
            {"keyword:dft", "keyword:training-data"},
        ]:
            self.assertIn(frozenset(pair), related)
        primary = [n for n in graph["nodes"] if n["type"] == "chapter" and n["visibility"] == "primary"]
        self.assertGreaterEqual(len(primary), 10)


if __name__ == "__main__":
    unittest.main()
