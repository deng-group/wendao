"""Widget backend: the API the course-website chat widget calls, plus a small test chat page.

Who answers is decided by the course's AI settings (see `wendao.web.ai`); the browser cannot
pick the teacher's provider or model. Cross-origin requests are accepted only from the course
website (`[course] website`), extra origins in `[student] allowed_origins`, and localhost.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from wendao import __version__
from wendao import workspace as workspace_module
from wendao.rag.answer import AnswerGenerator
from wendao.rag.pipeline import QueryPipeline
from wendao.rag.prompts import PromptBuilder
from wendao.web import STATIC_DIR
from wendao.web.accounts import SignInError, TokenSigner
from wendao.web.ai import AiPolicy, AiUnavailable, bearer_token, student_id
from wendao.web.explorer import EXPLORER_DIR, course_source_url, render_index
from wendao.web.feedback import FeedbackError, FeedbackStore
from wendao.web.graph_api import GraphFile, graph_response

MAX_SELECTION = 1500  # characters of highlighted page text sent with a question


def public_sources(sources: list[dict]) -> list[dict]:
    return [
        {
            "title": source["title"],
            "file_path": source["file_path"],
            "location": source.get("location", ""),
            "score": source["score"],
        }
        for source in sources
    ]


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def allowed_origin(origin: str, allowed: set[str]) -> bool:
    if not origin:
        return False
    if origin in allowed or "*" in allowed:
        return True
    host = urlsplit(origin).hostname or ""
    return host in {"localhost", "127.0.0.1", "::1"}


def create_app(workspace=None) -> Flask:
    """Create the app for a workspace (default: `$WENDAO_WORKSPACE` or the current folder)."""
    workspace = workspace or workspace_module.load()
    workspace.apply_model_settings()
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    pipeline = QueryPipeline.for_workspace(workspace, top_k=5)
    prompt_builder = PromptBuilder.for_workspace(workspace, evidence_score_threshold=0.60)
    policy = AiPolicy.from_settings(
        workspace.student_ai, workspace.questions_per_day, accounts=workspace.accounts(), limit=workspace.daily_limit()
    )
    origins = {origin_of(workspace.website)} | {origin_of(item) or item for item in workspace.allowed_origins}
    graph_file = GraphFile(Path(workspace.graph_path))

    def selected_context(payload: dict) -> list[str]:
        """Graph nodes the student picked (e.g. a concept in the Graph tab), as labels to focus the search."""
        graph_view = graph_file.view
        if graph_view is None:
            return []
        context = []
        for node_id in payload.get("context_node_ids", [])[:4]:
            node = graph_view.nodes.get(str(node_id))
            if node:
                context.append(f"{node['type']}: {node.get('label', node_id)}")
        return context

    feedback_store: list[FeedbackStore] = []  # made on first use, so a server nobody rates creates no files

    def feedback() -> FeedbackStore:
        if not feedback_store:
            root = Path(workspace.root)
            feedback_store.append(FeedbackStore(root / "usage.db", TokenSigner.for_folder(root).secret))
        return feedback_store[0]

    def selection(payload: dict) -> str:
        """Text the student highlighted on the course page, tidied and cut to a reasonable length."""
        return " ".join(str(payload.get("selection") or "").split())[:MAX_SELECTION]

    def with_links(item: dict) -> dict:
        if item.get("file_path"):
            item = {**item, "url": course_source_url(item["file_path"], workspace.website)}
        return item

    def generator_for(payload: dict) -> AnswerGenerator:
        provider = policy.choose(payload, student_id(request), bearer_token(request))
        return AnswerGenerator(
            pipeline=pipeline,
            prompt_builder=prompt_builder,
            provider=provider,
            course_name=workspace.display_name,
        )

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin", "")
        if allowed_origin(origin, origins):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.route("/api/answer", methods=["OPTIONS"])
    @app.route("/api/answer/stream", methods=["OPTIONS"])
    @app.route("/api/login", methods=["OPTIONS"])
    @app.route("/api/feedback", methods=["OPTIONS"])
    def answer_options():
        return ("", 204)

    @app.get("/")
    def index():
        """A plain page with the widget on it, to try the widget without a course website."""
        name = html.escape(workspace.display_name)
        api = html.escape(request.host_url.rstrip("/"), quote=True)
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} · Wendao</title>
<link rel="stylesheet" href="/static/site_widget/wendao-widget.css?v={__version__}" data-wendao-widget="style">
<style>body{{margin:0;font-family:system-ui,sans-serif;color:#0b1220;background:#f5f6fb}}
main{{max-width:640px;margin:12vh auto;padding:0 24px;line-height:1.6}}h1{{margin:0 0 8px}}p{{color:#5b6577}}</style>
</head><body><main><h1>{name}</h1>
<p>This is the Wendao chat widget, the same one <code>wendao widget install</code> adds to your course website.
Open it with the button at the bottom-right.</p></main>
<script src="/static/site_widget/wendao-widget.js?v={__version__}" data-version="{__version__}" data-api="{api}" defer></script>
</body></html>"""
        return Response(page, content_type="text/html; charset=utf-8")

    # The whole course graph (the same page as `wendao serve`), shown inside the widget's Graph tab.
    graph_page = render_index(workspace)

    @app.get("/graph/")
    def graph_index():
        if graph_file.graph is None:
            return Response("This course has no knowledge graph yet. Run `wendao build`.", status=404)
        return Response(graph_page, content_type="text/html; charset=utf-8")

    @app.get("/graph/<any('app.js', 'style.css'):filename>")
    def graph_files(filename: str):
        return send_from_directory(EXPLORER_DIR, filename)

    @app.get("/api/graph")
    def course_graph():
        response = graph_response(graph_file, lambda path: course_source_url(path, workspace.website))
        if response is None:
            return jsonify({"ok": False, "message": "No knowledge graph yet."}), 404
        return response

    @app.get("/api/health")
    def health():
        ai = policy.describe()
        return jsonify({
            "ok": True,
            "ai": ai,
            "default_provider": ai["provider"],
            "course": {"name": workspace.course_name, "code": workspace.course_code, "display": workspace.display_name,
                       "style": workspace.style},
            "graph": graph_file.view is not None,
            "web_search": workspace.web_search,
            "feedback": True,  # this server takes thumbs up / down on answers  # where the Search button looks up highlighted text
        })

    @app.post("/api/feedback")
    def give_feedback():
        """A student's thumbs up / down on an answer, stored without who sent it (see web/feedback.py)."""
        try:
            feedback().add(student_id(request), request.get_json(force=True) or {})
        except FeedbackError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        return jsonify({"ok": True})

    @app.get("/api/page")
    def page():
        """The graph node for the page the student is reading (`?path=` is the browser address)."""
        graph_view = graph_file.view
        found = graph_view.page(request.args.get("path", ""), base=urlsplit(workspace.website or "").path) if graph_view else None
        return jsonify({"ok": True, "node": with_links(found) if found else None})

    @app.get("/api/neighborhood")
    def neighborhood():
        """A node and its closest neighbours, for the widget's Graph tab."""
        graph_view = graph_file.view
        view = graph_view.neighborhood(request.args.get("node", "")) if graph_view else None
        if view is None:
            return jsonify({"ok": False, "message": "This part of the graph was not found."}), 404
        view["center"] = with_links(view["center"])
        view["nodes"] = [with_links(node) for node in view["nodes"]]
        return jsonify({"ok": True, **view})

    @app.post("/api/login")
    def login():
        if policy.accounts is None:
            return jsonify({"ok": False, "message": "This course doesn't use sign-in."}), 400
        try:
            token, student = policy.accounts.sign_in(str((request.get_json(force=True) or {}).get("email", "")))
        except SignInError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 403
        return jsonify({"ok": True, "token": token, "email": student.email, "name": student.name})

    @app.post("/api/answer")
    def answer():
        payload = request.get_json(force=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"ok": False, "error": "Query is required."}), 400
        try:
            result = generator_for(payload).answer(
                query,
                short_memory=payload.get("short_memory") or [],
                selected_context=selected_context(payload),
                selection=selection(payload),
            )
        except AiUnavailable as exc:
            return jsonify({"ok": False, "error": exc.code, "message": str(exc)}), 401 if exc.code == "LoginRequired" else 429
        except Exception as exc:  # noqa: BLE001 - report provider failures to the widget
            return jsonify({"ok": False, "error": type(exc).__name__, "message": str(exc)}), 502

        return jsonify(
            {
                "ok": True,
                "query": result["query"],
                "answer": result["answer"],
                "status": result["status"],
                "llm_action": result["llm_action"],
                "provider": result["provider"],
                "model": result["model"],
                "confidence": result["confidence"],
                "temporal_context": result["temporal_context"],
                "sources": [with_links(source) for source in public_sources(result["sources"])],
            }
        )

    @app.post("/api/answer/stream")
    def answer_stream():
        payload = request.get_json(force=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"ok": False, "error": "Query is required."}), 400

        def events():
            try:
                generator = generator_for(payload)
                for event in generator.stream_answer(
                    query,
                    short_memory=payload.get("short_memory") or [],
                    selected_context=selected_context(payload),
                    selection=selection(payload),
                ):
                    if event.get("sources"):
                        event = {**event, "sources": [with_links(source) for source in event["sources"]]}
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except Exception as exc:  # noqa: BLE001 - report every failure as a stream event
                error = exc.code if isinstance(exc, AiUnavailable) else type(exc).__name__
                yield json.dumps({"type": "error", "ok": False, "error": error, "message": str(exc)}, ensure_ascii=False) + "\n"

        response = Response(
            stream_with_context(events()),
            content_type="application/x-ndjson; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return app
