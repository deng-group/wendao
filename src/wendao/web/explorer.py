"""Wendao explorer: the knowledge graph website with the AI learning agent built in."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from wendao import __version__
from wendao import workspace as workspace_module
from wendao.rag.answer import AnswerGenerator
from wendao.rag.pipeline import QueryPipeline
from wendao.rag.prompts import PromptBuilder
from wendao.web import STATIC_DIR
from wendao.web.accounts import SignInError
from wendao.web.ai import AiPolicy, AiUnavailable, bearer_token, student_id
from wendao.web.graph_api import GraphFile, browser_graph, graph_response
from wendao.web.insights import recorder, store_opener

EXPLORER_DIR = STATIC_DIR / "explorer"


def course_source_url(file_path: str, site_base: str, location: str = "") -> str:
    """Map a retrieved course file to its page on the course website (empty if no website is set).

    Markdown and notebooks map to their published MyST page. Other files (PDF, slides, ...)
    link to the file itself, and PDFs open at the right page.
    """
    if not site_base:
        return ""
    site_base = site_base.rstrip("/") + "/"
    normalized = str(file_path or "").strip().replace("\\", "/")
    normalized = re.sub(r"^(?:\./)+", "", normalized).lstrip("/")
    if not re.search(r"\.(?:md|ipynb|myst|rst)$", normalized, flags=re.IGNORECASE):
        url = site_base + "/".join(quote(part) for part in normalized.split("/") if part)
        page = re.fullmatch(r"page (\d+)", location or "")
        return f"{url}#page={page.group(1)}" if page and normalized.lower().endswith(".pdf") else url
    normalized = re.sub(r"\.(?:md|ipynb|myst|rst)$", "", normalized, flags=re.IGNORECASE)
    if normalized.lower() in {"index", "readme"}:
        normalized = ""
    elif normalized.lower().endswith(("/index", "/readme")):
        normalized = normalized.rsplit("/", 1)[0]
    # MyST publishes file and directory names as lowercase kebab-case slugs.
    # For example, structures/crystal_structure.ipynb becomes
    # /structures/crystal-structure/, rather than /structures/crystal_structure/.
    slug_parts = [part.replace("_", "-").lower() for part in normalized.split("/") if part]
    encoded_path = "/".join(quote(part) for part in slug_parts)
    return site_base if not encoded_path else f"{site_base}{encoded_path}/"


def render_index(workspace) -> str:
    page = (EXPLORER_DIR / "index.html").read_text(encoding="utf-8")
    replacements = {
        "{{COURSE_NAME}}": workspace.course_name,
        "{{COURSE_LABEL}}": workspace.course_code or workspace.course_name,
        "{{COURSE_DISPLAY_NAME}}": workspace.display_name,
        "{{STYLE}}": workspace.style,
        # A new release gets new file addresses, so browsers don't keep using the old app.js and style.css.
        "{{VERSION}}": __version__,
    }
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, html.escape(value))
    return page


def export_page(workspace, online: str = "") -> str:
    """The graph website as one HTML file: page, style, code and graph inside. It opens by double-click, with no
    server (`wendao export`). The AI needs a server: with `online` (that server's graph page), "Ask AI" opens it.
    """
    graph = browser_graph(json.loads(Path(workspace.graph_path).read_text(encoding="utf-8")),
                          lambda path: course_source_url(path, workspace.website))
    data = {"graph": graph, "online": online}
    # "</" can't appear inside <script>: write it as "<\/" (the same in JSON).
    script = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = render_index(workspace)
    css = (EXPLORER_DIR / "style.css").read_text(encoding="utf-8")
    code = (EXPLORER_DIR / "app.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    page = re.sub(r'<link rel="stylesheet" href="\./style\.css[^"]*" />', lambda _: f"<style>\n{css}\n</style>", page, count=1)
    page = re.sub(r'<script src="\./app\.js[^"]*"></script>',
                  lambda _: f"<script>window.WENDAO_EXPORT = {script};</script>\n<script>\n{code}\n</script>", page, count=1)
    return page


def create_app(workspace=None) -> Flask:
    workspace = workspace or workspace_module.load()
    workspace.apply_model_settings()
    graph_file = GraphFile(workspace.require(workspace.graph_path, "Run `wendao build` first."))
    record_question = recorder(store_opener(workspace), "website", lambda: graph_file.graph)  # teacher insights
    pipeline = QueryPipeline.for_workspace(workspace, top_k=5)
    prompt_builder = PromptBuilder.for_workspace(workspace, evidence_score_threshold=0.60, presentation_mode="direct")
    index_page = render_index(workspace)
    policy = AiPolicy.from_settings(
        workspace.student_ai, workspace.questions_per_day, accounts=workspace.accounts(), limit=workspace.daily_limit()
    )
    app = Flask(__name__, static_folder=None)

    def selected_context(payload: dict) -> list[str]:
        context = []
        for node_id in payload.get("context_node_ids", [])[:4]:
            node = graph_file.view.nodes.get(str(node_id)) if graph_file.view else None
            if node:
                context.append(f"{node['type']}: {node['label']}")
        return context

    @app.get("/")
    def index():
        return Response(index_page, content_type="text/html; charset=utf-8")

    @app.get("/assets/<path:filename>")
    def assets(filename: str):
        return send_from_directory(EXPLORER_DIR, filename)

    @app.get("/<path:filename>")
    def static_files(filename: str):
        if filename in {"app.js", "style.css"}:
            return send_from_directory(EXPLORER_DIR, filename)
        return Response(index_page, content_type="text/html; charset=utf-8")

    @app.get("/api/health")
    def health():
        ai = policy.describe()
        return jsonify(
            {
                "ok": True,
                "ai": ai,
                "provider": ai["provider"],
                "model": ai["model"],
                "graph_nodes": len((graph_file.graph or {}).get("nodes", [])),
                "graph_edges": len((graph_file.graph or {}).get("edges", [])),
            }
        )

    @app.get("/api/graph")
    def course_graph():
        link_for = lambda path: course_source_url(path, workspace.website)  # noqa: E731
        return graph_response(graph_file, link_for) or jsonify(browser_graph({}, link_for))

    @app.post("/api/login")
    def login():
        email = str((request.get_json(force=True) or {}).get("email", ""))
        if policy.accounts is not None:
            try:
                token, student = policy.accounts.sign_in(email)
            except SignInError as exc:
                return jsonify({"ok": False, "message": str(exc)}), 403
            return jsonify({"ok": True, "token": token, "email": student.email, "name": student.name})
        return jsonify({"ok": False, "message": "This course doesn't use sign-in."}), 400

    @app.post("/api/answer/stream")
    def answer_stream():
        payload = request.get_json(force=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"ok": False, "error": "Query is required."}), 400

        memory = payload.get("short_memory") or []
        context = selected_context(payload)
        student = student_id(request)
        token = bearer_token(request)

        def events():
            try:
                chosen = policy.choose(payload, student, token)
                generator = AnswerGenerator(
                    pipeline=pipeline,
                    prompt_builder=prompt_builder,
                    provider=chosen,
                    course_name=workspace.display_name,
                    on_decision=record_question,
                )
                for event in generator.stream_answer(
                    query,
                    short_memory=memory,
                    selected_context=context,
                ):
                    if event.get("sources"):
                        event = {
                            **event,
                            "sources": [
                                {**source, "url": course_source_url(source.get("file_path", ""), workspace.website, source.get("location", ""))}
                                for source in event["sources"]
                            ],
                        }
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except AiUnavailable as exc:
                yield json.dumps({"type": "error", "ok": False, "message": str(exc), "error": exc.code}) + "\n"
            except Exception as exc:
                yield json.dumps(
                    {
                        "type": "error",
                        "ok": False,
                        "message": str(exc),
                        "error": type(exc).__name__,
                    },
                    ensure_ascii=False,
                ) + "\n"

        response = Response(
            stream_with_context(events()),
            content_type="application/x-ndjson; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return app
