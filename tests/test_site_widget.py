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

    def test_home_page_and_escaped_addresses_under_a_sub_folder(self):
        view = GraphView({"nodes": [
            {"id": "topic:home", "type": "topic", "label": "Home", "source_files": ["index.md"]},
            {"id": "topic:unit", "type": "topic", "label": "Unit", "source_files": ["unit 1/topic.md"]},
        ], "edges": []})
        for address in ["/book/", "/book/index.html"]:
            with self.subTest(address=address):
                self.assertEqual(view.page(address, base="/book/")["id"], "topic:home")
        self.assertEqual(view.page("/", base="")["id"], "topic:home")
        self.assertEqual(view.page("/book/unit%201/topic/", base="/book/")["id"], "topic:unit")

    def test_neighbourhood_is_small_and_connected(self):
        view = self.view.neighborhood("keyword:convex-hull")
        self.assertEqual(view["center"]["label"], "Convex Hull")
        self.assertLessEqual(len(view["nodes"]), sum(GraphView.LIMITS.values()))
        self.assertTrue(all(node["type"] in {"keyword", "topic", "chapter"} for node in view["nodes"]))
        included = {view["center"]["id"], *(node["id"] for node in view["nodes"])}
        self.assertTrue(all(link["source"] in included and link["target"] in included for link in view["links"]))
        self.assertTrue(any(view["center"]["id"] in (link["source"], link["target"]) for link in view["links"]))
        self.assertIsNone(self.view.neighborhood("keyword:does-not-exist"))

    def test_a_concept_shows_every_chapter_it_appears_in(self):
        chapters = {edge["target"] if edge["source"] == "keyword:python" else edge["source"]
                    for edge in self.view.edges if "keyword:python" in (edge["source"], edge["target"])
                    and self.view.nodes[edge["target"] if edge["source"] == "keyword:python" else edge["source"]]["type"] == "chapter"
                    and self.view.nodes[edge["target"] if edge["source"] == "keyword:python" else edge["source"]].get("visibility") != "hidden"}
        view = self.view.neighborhood("keyword:python")
        shown = {node["id"] for node in view["nodes"] if node["type"] == "chapter"}
        self.assertEqual(shown, chapters)
        self.assertGreater(len(shown), 4)


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
        self.assertEqual(health["course"]["style"], "modern")
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

    def test_both_answer_apis_link_sources_to_the_course_site(self):
        fake = {"query": "q", "answer": "a", "status": "answerable", "llm_action": "generate_answer", "provider": "p",
                "model": "m", "confidence": 1.0, "temporal_context": None,
                "sources": [{"title": "Thermodynamics", "file_path": "high_throughput/thermodynamics.md", "score": 1.0}]}
        with mock.patch("wendao.rag.answer.AnswerGenerator.answer", return_value=fake), \
                mock.patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test"}):
            reply = self.client.post("/api/answer", json={"query": "What is a convex hull?"}).get_json()
        self.assertEqual(reply["sources"][0]["url"], "https://mle4217-5219.matsci.dev/high-throughput/thermodynamics/")

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

    def test_serves_the_whole_course_graph_for_the_graph_tab(self):
        page = self.client.get("/graph/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"MLE4217/5219", page.data)
        self.assertEqual(self.client.get("/graph/app.js").status_code, 200)
        self.assertEqual(self.client.get("/graph/style.css").status_code, 200)
        self.assertEqual(self.client.get("/graph/secret.txt").status_code, 404)
        graph = self.client.get("/api/graph").get_json()
        self.assertGreater(len(graph["nodes"]), 100)
        from wendao import __version__

        self.assertIn(f"app.js?v={__version__}".encode(), page.data)  # a new release: browsers fetch the new code
        self.assertNotIn(b'id="explain-in-widget" class="ask-node" type="button" hidden', page.data)

    def test_graph_is_sent_small_and_only_when_changed(self):
        import gzip

        plain = self.client.get("/api/graph")
        packed = self.client.get("/api/graph", headers={"Accept-Encoding": "gzip, br"})
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertLess(len(packed.data), len(plain.data) / 3)
        self.assertEqual(json.loads(gzip.decompress(packed.data)), plain.get_json())
        again = self.client.get("/api/graph", headers={"If-None-Match": plain.headers["ETag"]})
        self.assertEqual((again.status_code, again.data), (304, b""))

    def test_home_page_shows_the_new_widget(self):
        page = self.client.get("/").data.decode()
        self.assertIn("/static/site_widget/wendao-widget.js", page)
        self.assertIn('data-api="http://localhost"', page)
        self.assertEqual(self.client.get("/static/site_widget/wendao-widget.js").status_code, 200)

    def test_health_names_the_search_engine(self):
        self.assertEqual(self.client.get("/api/health").get_json()["web_search"]["name"], "Google")



class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wendao.toml").write_text('[course]\nname = "Demo"\n[search]\nembedding_model = "tfidf"\n', encoding="utf-8")
        (self.root / "build").mkdir()
        (self.root / "build" / "chunks.jsonl").write_text(json.dumps({
            "chunk_id": "a.md_0", "file_path": "a.md", "module": "root", "title": "A", "content": "Convex hull and stable phases."}) + "\n",
            encoding="utf-8")
        self.client = create_app(workspace_module.load(self.root)).test_client()
        self.down = {"rating": "down", "question": "What is a convex hull?", "answer": "It is a kind of fruit.",
                     "note": "Wrong!", "sources": ["a.md"], "page": "/thermo/"}

    def tearDown(self):
        self.tmp.cleanup()

    def rate(self, payload, address="10.0.0.7"):
        return self.client.post("/api/feedback", json=payload, environ_base={"REMOTE_ADDR": address})

    def test_ratings_are_stored_without_who_sent_them(self):
        self.assertTrue(self.client.get("/api/health").get_json()["feedback"])
        self.assertFalse((self.root / "usage.db").exists())  # nothing is created until someone rates an answer
        self.assertEqual(self.rate(self.down).status_code, 200)
        self.assertEqual(self.rate({**self.down, "rating": "up", "note": ""}).status_code, 200)
        self.assertEqual(self.rate({**self.down, "rating": "meh"}).status_code, 400)
        self.assertEqual(self.rate({**self.down, "question": ""}).status_code, 400)
        stored = (self.root / "usage.db").read_bytes()
        self.assertNotIn(b"10.0.0.7", stored)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["feedback", "-w", str(self.root)])
        text = output.getvalue()
        self.assertIn("1 helpful, 1 not helpful", text)
        self.assertIn("Q: What is a convex hull?", text)
        self.assertIn("Student's note: Wrong!", text)
        self.assertIn("on /thermo/", text)

    def test_one_sender_cannot_flood_the_database(self):
        with mock.patch("wendao.web.feedback.MAX_PER_SENDER_PER_DAY", 2):
            codes = [self.rate(self.down).status_code for _ in range(3)]
            self.assertEqual(codes, [200, 200, 400])
            self.assertEqual(self.rate(self.down, address="10.0.0.8").status_code, 200)  # someone else still can

    def test_a_rebuilt_graph_is_served_without_a_restart(self):
        graph_path = self.root / "build" / "graph.json"
        node = {"id": "chapter:a", "type": "chapter", "label": "A"}
        graph_path.write_text(json.dumps({"nodes": [node], "edges": []}), encoding="utf-8")
        self.assertEqual(len(self.client.get("/api/graph").get_json()["nodes"]), 1)
        graph_path.write_text(json.dumps({"nodes": [node, {**node, "id": "chapter:b", "label": "B"}], "edges": []}), encoding="utf-8")
        later = graph_path.stat().st_mtime + 5
        os.utime(graph_path, (later, later))  # as `wendao build` would, a moment later
        self.assertEqual(len(self.client.get("/api/graph").get_json()["nodes"]), 2)

    def test_command_without_feedback(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["feedback", "-w", str(self.root)])
        self.assertIn("No feedback yet", output.getvalue())


class StudentAddressTest(unittest.TestCase):
    def test_proxy_headers_are_trusted_only_from_this_machine(self):
        from flask import Flask, request

        from wendao.web.ai import student_id

        app = Flask(__name__)
        tunnel = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "198.51.100.1"}
        with app.test_request_context(headers=tunnel, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertEqual(student_id(request), "203.0.113.9")   # Cloudflare Tunnel
        with app.test_request_context(headers={"X-Real-IP": "198.51.100.2"}, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertEqual(student_id(request), "198.51.100.2")  # Nginx
        with app.test_request_context(headers=tunnel, environ_base={"REMOTE_ADDR": "192.0.2.50"}):
            self.assertEqual(student_id(request), "192.0.2.50")    # anyone else can't pretend


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




class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "wendao.toml").write_text('[course]\nname = "Demo"\nwebsite = "https://demo.edu/"\n', encoding="utf-8")
        (self.root / "build").mkdir()
        graph = {"nodes": [
            {"id": "chapter:a", "type": "chapter", "label": "A</script><script>alert(1)</script>", "visibility": "primary",
             "chunk_ids": ["x"] * 50, "description": "long"},
            {"id": "topic:a-intro", "type": "topic", "label": "Intro", "chapter_id": "chapter:a", "source_files": ["a/intro.md"]},
        ], "edges": [{"id": "e1", "source": "chapter:a", "target": "topic:a-intro", "type": "contains", "weight": 1,
                      "evidence": ["a long passage"] * 20}]}
        (self.root / "build" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_file_with_everything_inside(self):
        from wendao.web.explorer import export_page

        page = export_page(workspace_module.load(self.root))
        self.assertNotIn("./app.js", page)
        self.assertNotIn("./style.css", page)
        self.assertNotIn("a long passage", page)  # only what the page needs
        self.assertIn("https://demo.edu/a/intro/", page)  # page nodes link to the course website
        self.assertEqual(page.count("</script>"), 2)  # a label can't close the script early
        self.assertIn('"online":""', page)
        self.assertIn('"path":', page)  # the guided path works in the file too
        # Without a server there is no AI: the buttons that ask it are hidden.
        self.assertIn(".exported-offline #ask-node, .exported-offline #explain-in-widget", page)

    def test_command(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(["export", "-w", str(self.root), "--online", "https://ai.demo.edu/graph/"])
        self.assertTrue((self.root / "build" / "demo.html").is_file())
        self.assertIn("double-clicking", output.getvalue())
        self.assertIn('"online":"https://ai.demo.edu/graph/"', (self.root / "build" / "demo.html").read_text(encoding="utf-8"))

class StyleSettingTest(unittest.TestCase):
    def workspace(self, course: str):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "wendao.toml").write_text(f'[course]\nname = "Test"\n{course}', encoding="utf-8")
        return workspace_module.load(root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_modern_by_default_economist_on_request(self):
        self.assertEqual(self.workspace("").style, "modern")
        self.assertEqual(self.workspace('style = "Economist"\n').style, "economist")
        with self.assertRaisesRegex(workspace_module.WorkspaceError, "must be \"modern\" or \"economist\""):
            self.workspace('style = "neon"\n')

    def test_the_graph_page_and_widget_get_the_style(self):
        from wendao.web.explorer import render_index

        workspace = self.workspace('style = "economist"\n')
        self.assertIn('data-style="economist"', render_index(workspace))

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

        again = install(self.site, api="https://api.example.edu/")  # running again replaces, never duplicates
        self.assertFalse(again["replaced_old_widget"])  # it only replaced its own earlier copy
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
