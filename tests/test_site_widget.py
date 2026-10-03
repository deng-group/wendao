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
from wendao.web.graph_api import GraphView, normalize_address, page_slug
from wendao.web.site_widget import install, remove
from wendao.web.widget import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_GRAPH = REPO_ROOT / "examples" / "mle4217_5219" / "build" / "graph.json"


class GraphViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = GraphView(json.loads(EXAMPLE_GRAPH.read_text(encoding="utf-8")))

    def test_page_addresses_follow_myst_rules(self):
        self.assertEqual(page_slug("structures/crystal_structure.ipynb"), "structures/crystal-structure")
        self.assertEqual(page_slug("high_throughput/index.md"), "high-throughput")
        self.assertEqual(page_slug("index.md"), "")
        self.assertIsNone(page_slug("slides/week1.pdf"))
        self.assertEqual(normalize_address("/book/High-Throughput/thermodynamics/index.html?x=1#top"), "book/high-throughput/thermodynamics")

    def test_finds_the_page_also_under_a_sub_folder(self):
        for address in ["/high-throughput/thermodynamics/", "/mle-book/high-throughput/thermodynamics/index.html"]:
            with self.subTest(address=address):
                self.assertEqual(self.view.page(address)["id"], "topic:high-throughput-thermodynamics")
        self.assertIsNone(self.view.page("/not/a/page/"))

    def test_neighbourhood_is_small_and_connected(self):
        view = self.view.neighborhood("keyword:convex-hull")
        self.assertEqual(view["center"]["label"], "Convex Hull")
        self.assertLessEqual(len(view["nodes"]), 18)
        self.assertTrue(all(node["type"] in {"keyword", "topic", "chapter"} for node in view["nodes"]))
        included = {view["center"]["id"], *(node["id"] for node in view["nodes"])}
        self.assertTrue(all(link["source"] in included and link["target"] in included for link in view["links"]))
        self.assertTrue(any(view["center"]["id"] in (link["source"], link["target"]) for link in view["links"]))
        self.assertIsNone(self.view.neighborhood("keyword:does-not-exist"))


class WidgetApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Remove only AI settings: Windows needs the rest of the environment (e.g. to find the model cache).
        keep = {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_API", "LLM_"))}
        with mock.patch.dict(os.environ, keep, clear=True):
            cls.client = create_app(workspace_module.load(REPO_ROOT / "examples" / "mle4217_5219")).test_client()

    def test_health_names_the_course_and_graph(self):
        health = self.client.get("/api/health").get_json()
        self.assertEqual(health["course"]["code"], "MLE4217/5219")
        self.assertTrue(health["graph"])

    def test_page_and_neighbourhood_include_links_to_the_course_site(self):
        node = self.client.get("/api/page?path=/high-throughput/thermodynamics/").get_json()["node"]
        self.assertEqual(node["label"], "Thermodynamics")
        self.assertEqual(node["url"], "https://mle4217-5219.matsci.dev/high-throughput/thermodynamics/")
        view = self.client.get(f"/api/neighborhood?node={node['id']}").get_json()
        pages = [item for item in view["nodes"] if item["type"] == "topic"]
        self.assertTrue(pages and all(item["url"].startswith("https://mle4217-5219.matsci.dev/") for item in pages))
        self.assertEqual(self.client.get("/api/neighborhood?node=nope").status_code, 404)
        self.assertIsNone(self.client.get("/api/page?path=/nope/").get_json()["node"])

    def test_highlighted_text_focuses_the_search(self):
        from wendao.rag.pipeline import QueryPipeline

        pipeline = QueryPipeline.for_workspace(workspace_module.load(REPO_ROOT / "examples" / "mle4217_5219"))
        passage = ("For systems with more than two components, the concept of the convex hull is used to determine "
                   "phase stability. The convex hull is a geometric construct that represents the lowest-energy phases.")
        alone = pipeline.ask("Summarize this.")
        self.assertEqual(alone["status"], "needs_clarification")  # "this" means nothing without the highlight
        focused = pipeline.ask("Summarize this.", selection=passage)
        self.assertEqual(focused["status"], "answerable")
        self.assertEqual(focused["evidence"][0]["file_path"], "high_throughput/thermodynamics.md")

    def test_health_names_the_search_engine(self):
        self.assertEqual(self.client.get("/api/health").get_json()["web_search"]["name"], "Google")


class WebSearchSettingTest(unittest.TestCase):
    def load(self, widget: str):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "wendao.toml").write_text(f'[course]\nname = "Test"\n{widget}', encoding="utf-8")
            return workspace_module.load(Path(folder)).web_search

    def test_google_by_default_or_the_one_chosen(self):
        self.assertEqual(self.load("")["url"], "https://www.google.com/search?q={q}")
        self.assertEqual(self.load('[widget]\nweb_search = "Scholar"\n')["name"], "Google Scholar")
        own = "https://next-gen.materialsproject.org/materials?formula={q}"
        self.assertEqual(self.load(f'[widget]\nweb_search = "{own}"\n')["url"], own)
        self.assertIsNone(self.load('[widget]\nweb_search = ""\n'))

    def test_mistakes_are_explained(self):
        for bad in ["altavista", "javascript:alert({q})", "https://example.org/search"]:
            with self.subTest(bad=bad), self.assertRaisesRegex(workspace_module.WorkspaceError, "must be one of google"):
                self.load(f'[widget]\nweb_search = "{bad}"\n')


class SelectionPromptTest(unittest.TestCase):
    def test_highlighted_text_is_quoted_in_the_prompt_and_capped(self):
        keep = {key: value for key, value in os.environ.items()
                if not key.startswith(("ANTHROPIC_", "OPENAI_", "GEMINI_", "GOOGLE_API", "LLM_"))}
        with mock.patch.dict(os.environ, keep, clear=True), \
                mock.patch("wendao.rag.answer.AnswerGenerator.answer", autospec=True) as answer:
            answer.return_value = {key: None for key in ("query", "answer", "status", "llm_action", "provider",
                                                         "model", "confidence", "temporal_context")} | {"sources": []}
            client = create_app(workspace_module.load(REPO_ROOT / "examples" / "mle4217_5219")).test_client()
            client.post("/api/answer", json={"query": "What does this mean?", "selection": "  convex\n hull " + "x" * 5000})
        selection = answer.call_args.kwargs["selection"]
        self.assertTrue(selection.startswith("convex hull x"))
        self.assertEqual(len(selection), 1500)

        from wendao.rag.prompts import PromptBuilder
        pipeline_result = {"query": "What does this mean?", "status": "answerable", "evidence": []}
        prompt = PromptBuilder(course_name="Test course").build(pipeline_result, selection="the lowest-energy phases")
        self.assertIn('highlighted on the course page', prompt["final_prompt"])
        self.assertIn("the lowest-energy phases", prompt["final_prompt"])
        self.assertNotIn("highlighted", PromptBuilder(course_name="Test course").build(pipeline_result)["final_prompt"])


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.site = Path(self.tmp.name) / "html"
        (self.site / "structures" / "crystal-structure").mkdir(parents=True)
        (self.site / "index.html").write_text("<html><head><title>Home</title></head><body><p>Home</p></body></html>", encoding="utf-8")
        old_widget = ('<!-- MLE AI Agent Widget: start -->\n<script src="../../ai_agent_widget/ai_agent_widget.js"></script>\n'
                      "<!-- MLE AI Agent Widget: end -->")
        (self.site / "structures" / "crystal-structure" / "index.html").write_text(
            f"<html><head></head><body><p>Page</p>{old_widget}</body></html>", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_adds_the_widget_once_with_relative_paths(self):
        result = install(self.site)
        self.assertEqual(result["pages"], 2)
        self.assertTrue(result["replaced_old_widget"])
        self.assertTrue((self.site / "_wendao" / "wendao-widget.js").is_file())
        home = (self.site / "index.html").read_text(encoding="utf-8")
        deep = (self.site / "structures" / "crystal-structure" / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="_wendao/wendao-widget.js', home)
        self.assertIn('src="../../_wendao/wendao-widget.js', deep)
        self.assertNotIn("ai_agent_widget", deep)

        install(self.site, api="https://api.example.edu/")  # running again replaces, never duplicates
        home = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertEqual(home.count("wendao-widget.js"), 1)
        self.assertEqual(home.count("wendao-widget.css"), 1)
        self.assertIn('data-api="https://api.example.edu"', home)

    def test_remove_restores_the_pages(self):
        original = (self.site / "index.html").read_text(encoding="utf-8")
        install(self.site)
        self.assertEqual(remove(self.site), 2)
        self.assertEqual((self.site / "index.html").read_text(encoding="utf-8").replace("\n", ""), original)
        self.assertFalse((self.site / "_wendao").exists())

    def test_command_explains_a_missing_site(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
            main(["widget", "install", str(Path(self.tmp.name) / "missing")])
        self.assertIn("Build the site first", errors.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["widget", "install", str(self.site)])
        self.assertIn("Added the Wendao widget to 2 pages", output.getvalue())
        self.assertIn("Replaced the previous course widget", output.getvalue())


if __name__ == "__main__":
    unittest.main()
