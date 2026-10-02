"""Open a course file the way a student does and check it really works.

Run with the base install only (no teacher tools), and offline, so the search model must
come from the course file:

    HF_HUB_OFFLINE=1 python tests/smoke_student_app.py course.wendao "a question"
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from wendao.pack import open_pack
from wendao.rag.pipeline import QueryPipeline
from wendao.web.explorer import create_app


def main() -> None:
    course_file, question = Path(sys.argv[1]), sys.argv[2]
    leaked = [name for name in ("pypdf", "docx", "pptx", "nbformat") if importlib.util.find_spec(name)]
    assert not leaked, f"teacher tools are installed in the student environment: {leaked}"

    course = open_pack(course_file)
    client = create_app(course).test_client()
    health = client.get("/api/health").get_json()
    assert health["ok"] and health["graph_nodes"] > 0, health
    assert client.get("/api/graph").status_code == 200
    assert course.course_name in client.get("/").get_data(as_text=True)

    pipeline = QueryPipeline.for_workspace(course)
    engine = pipeline.retriever.embedding.description
    assert "onnx" in engine, f"expected the ONNX search model from the course file, got: {engine}"
    result = pipeline.ask(question)
    assert result["evidence"], "search returned nothing"
    print(f"OK: {course.display_name} | {health['graph_nodes']} nodes | {engine}")
    print(f"    '{question}' → {result['status']}, top source {result['evidence'][0]['file_path']}")


if __name__ == "__main__":
    main()
