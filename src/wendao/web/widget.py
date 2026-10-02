"""Widget backend: the API the course-website chat widget calls, plus a small test chat page.

Allows cross-origin requests, so a course website on another address can call it.
"""

from __future__ import annotations

import json
import os

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from wendao import workspace as workspace_module
from wendao.rag.answer import AnswerGenerator
from wendao.rag.pipeline import QueryPipeline
from wendao.rag.prompts import PromptBuilder
from wendao.rag.providers import default_model_name, default_provider_name, provider_from_name
from wendao.web import STATIC_DIR, TEMPLATES_DIR


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


def create_app(workspace=None) -> Flask:
    """Create the app for a workspace (default: `$WENDAO_WORKSPACE` or the current folder)."""
    workspace = workspace or workspace_module.load()
    workspace.apply_model_settings()
    app = Flask(__name__, static_folder=str(STATIC_DIR / "widget"), static_url_path="/static", template_folder=str(TEMPLATES_DIR))
    pipeline = QueryPipeline.for_workspace(workspace, top_k=5)
    prompt_builder = PromptBuilder(evidence_score_threshold=0.60, course_name=workspace.display_name)

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    @app.route("/api/answer", methods=["OPTIONS"])
    @app.route("/api/answer/stream", methods=["OPTIONS"])
    def answer_options():
        return ("", 204)

    @app.get("/")
    def index():
        provider = default_provider_name()
        return render_template(
            "widget.html",
            course_name=workspace.display_name,
            default_provider=provider,
            default_model=default_model_name(provider),
        )

    @app.get("/api/health")
    def health():
        return jsonify(
            {
                "ok": True,
                "default_provider": default_provider_name(),
                "anthropic_configured": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")),
                "openai_configured": bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL")),
                "gemini_configured": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            }
        )

    @app.post("/api/answer")
    def answer():
        payload = request.get_json(force=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"ok": False, "error": "Query is required."}), 400

        provider = payload.get("provider") or default_provider_name()
        model = payload.get("model") or default_model_name(provider) or None
        memory = payload.get("short_memory") or []

        try:
            generator = AnswerGenerator(
                pipeline=pipeline,
                prompt_builder=prompt_builder,
                provider=provider_from_name(provider, model=model),
                course_name=workspace.display_name,
            )
            result = generator.answer(query, short_memory=memory)
        except Exception as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": type(exc).__name__,
                        "message": str(exc),
                        "provider": provider,
                        "model": model,
                    }
                ),
                502,
            )

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
                "sources": public_sources(result["sources"]),
            }
        )

    @app.post("/api/answer/stream")
    def answer_stream():
        payload = request.get_json(force=True) or {}
        query = str(payload.get("query", "")).strip()
        if not query:
            return jsonify({"ok": False, "error": "Query is required."}), 400

        provider = payload.get("provider") or default_provider_name()
        model = payload.get("model") or default_model_name(provider) or None
        memory = payload.get("short_memory") or []

        def events():
            try:
                generator = AnswerGenerator(
                    pipeline=pipeline,
                    prompt_builder=prompt_builder,
                    provider=provider_from_name(provider, model=model),
                    course_name=workspace.display_name,
                )
                for event in generator.stream_answer(query, short_memory=memory):
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except Exception as exc:
                yield json.dumps(
                    {
                        "type": "error",
                        "ok": False,
                        "error": type(exc).__name__,
                        "message": str(exc),
                        "provider": provider,
                        "model": model,
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
