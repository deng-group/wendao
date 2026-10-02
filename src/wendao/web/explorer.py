"""Wendao explorer: the knowledge graph website with the AI learning agent built in."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from wendao import workspace as workspace_module
from wendao.rag.answer import AnswerGenerator
from wendao.rag.pipeline import QueryPipeline
from wendao.rag.prompts import PromptBuilder
from wendao.web import STATIC_DIR
from wendao.web.ai import AiPolicy, AiUnavailable, forward_stream, student_id

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
    }
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, html.escape(value))
    return page


def create_app(workspace=None) -> Flask:
    workspace = workspace or workspace_module.load()
    workspace.apply_model_settings()
    graph_path = workspace.require(workspace.graph_path, "Run `wendao build` first.")
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    pipeline = QueryPipeline.for_workspace(workspace, top_k=5)
    prompt_builder = PromptBuilder(
        evidence_score_threshold=0.60,
        presentation_mode="direct",
        course_name=workspace.display_name,
    )
    index_page = render_index(workspace)
    # `[student] server` is where course apps on students' laptops send questions. The teacher's own
    # server (a workspace) answers with its own key, so it must never forward, or it would call itself.
    in_course_app = not getattr(workspace, "uses_local_key", True)
    policy = AiPolicy.from_settings(
        workspace.student_ai,
        workspace.student_server if in_course_app else "",
        workspace.questions_per_day,
        use_local_key=not in_course_app,
    )
    app = Flask(__name__, static_folder=None)

    def selected_context(payload: dict) -> list[str]:
        context = []
        for node_id in payload.get("context_node_ids", [])[:4]:
            node = nodes_by_id.get(str(node_id))
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
                "graph_nodes": len(graph["nodes"]),
                "graph_edges": len(graph["edges"]),
            }
        )

    @app.get("/api/graph")
    def course_graph():
        return jsonify(graph)

    @app.post("/api/answer/stream")
    def answer_stream():
        payload = request.get_json(force=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"ok": False, "error": "Query is required."}), 400

        memory = payload.get("short_memory") or []
        context = selected_context(payload)
        student = student_id(request)

        def events():
            try:
                how, chosen = policy.choose(payload, student)
                if how == "forward":
                    yield from forward_stream(chosen, payload)
                    return
                generator = AnswerGenerator(
                    pipeline=pipeline,
                    prompt_builder=prompt_builder,
                    provider=chosen,
                    course_name=workspace.display_name,
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
                yield json.dumps({"type": "error", "ok": False, "message": str(exc), "error": "AiUnavailable"}) + "\n"
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
