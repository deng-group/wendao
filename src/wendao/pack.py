"""Course apps: one `.wendao` file that students can open without building anything.

`wendao pack` (teacher) puts a built course into a single file: the knowledge graph, the
chunks, the search index, the search model, and the course's AI settings. `wendao open`
(student) unpacks it into a cache folder and starts the app. Students need only the small
base install (`pip install wendao`); no notes, no build tools, and no download of the model.

A `.wendao` file is a zip archive:

    course.json            name, website, search and AI settings, format version
    chunks.jsonl           course text, split into chunks
    graph.json             knowledge graph
    index/embeddings.npz   search index (optional)
    model/...              ONNX search model files (optional)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from wendao import __version__

PACK_SUFFIX = ".wendao"
PACK_FORMAT = 1
MODEL_FILES = ("onnx/model.onnx", "tokenizer.json", "sentence_bert_config.json")


class PackError(Exception):
    """A course file is missing, damaged, or from a newer version of Wendao."""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "course"


def default_pack_path(workspace) -> Path:
    return workspace.build_dir / f"{slugify(workspace.course_name)}{PACK_SUFFIX}"


def model_files(model_name: str) -> dict[str, Path]:
    """Find the ONNX model files for `model_name` in the Hugging Face cache, downloading them if needed."""
    from huggingface_hub import hf_hub_download

    found = {}
    for name in MODEL_FILES:
        try:
            found[name] = Path(hf_hub_download(model_name, name, local_files_only=True))
        except Exception:  # noqa: BLE001 - not cached yet
            found[name] = Path(hf_hub_download(model_name, name))
    return found


def pack(workspace, output: Path | None = None, include_model: bool = True) -> tuple[Path, list[str]]:
    """Write the course app. Returns (path, notes for the teacher)."""
    from wendao.rag.pipeline import QueryPipeline

    graph = workspace.require(workspace.graph_path, "Run `wendao build` first.")
    chunks = workspace.require(workspace.chunks_path, "Run `wendao build` first.")
    # Loading the pipeline also brings the search index up to date with the chunks.
    pipeline = QueryPipeline.for_workspace(workspace)
    embedding = pipeline.retriever.embedding
    neural = embedding.backend != "tfidf"

    notes = []
    if workspace.student_ai == "teacher" and not workspace.student_server:
        notes.append(
            "Students who open this file can't ask the AI yet: set [student] server to your Wendao website, "
            'or let students use their own key with [student] ai = "either" or "student".'
        )
    elif workspace.student_ai == "either" and not workspace.student_server:
        notes.append("No [student] server is set, so students who open this file will need their own AI key.")
    if neural and not include_model:
        notes.append("The search model is not included, so students need internet the first time they open the app.")

    manifest = {
        "format": PACK_FORMAT,
        "wendao_version": __version__,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "course": {
            "name": workspace.course_name,
            "code": workspace.course_code,
            "website": workspace.website,
            "term": workspace.term,
        },
        "search": {
            "embedding_model": workspace.embedding_model if neural else "tfidf",
            "model_included": bool(neural and include_model),
            "aliases": workspace.aliases,
            "logistics_terms": workspace.logistics_terms,
            "out_of_scope_terms": workspace.out_of_scope_terms,
        },
        "ai": {
            "mode": workspace.student_ai,
            "server": workspace.student_server,
            "login": bool(workspace.roster),
        },
    }

    output = Path(output) if output else default_pack_path(workspace)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("course.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.write(chunks, "chunks.jsonl")
        archive.write(graph, "graph.json")
        if neural:
            archive.write(embedding.cache_path, "index/embeddings.npz")
            if include_model:
                for name, path in model_files(workspace.embedding_model).items():
                    archive.write(path, f"model/{name}")
    temporary.replace(output)
    return output, notes


def cache_root() -> Path:
    if os.environ.get("WENDAO_CACHE"):
        return Path(os.environ["WENDAO_CACHE"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "wendao"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wendao"


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def unpack(path: Path) -> Path:
    """Unpack a course file into the cache (once per file version). Returns the folder."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise PackError(f"Course file not found: {path}")
    if not zipfile.is_zipfile(path):
        raise PackError(f"{path.name} is not a Wendao course file.")
    target = cache_root() / "apps" / file_digest(path)
    if (target / "course.json").is_file():
        return target
    staging = target.with_name(target.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            # Refuse paths that would land outside the folder.
            if member.startswith(("/", "\\")) or ".." in Path(member).parts:
                raise PackError(f"{path.name} contains an unsafe file name: {member}")
        if "course.json" not in archive.namelist():
            raise PackError(f"{path.name} is not a Wendao course file (course.json is missing).")
        archive.extractall(staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return target


@dataclass
class CourseApp:
    """A course opened from a `.wendao` file. Offers what the web apps need from a workspace."""

    root: Path
    course_name: str = "Course"
    course_code: str = ""
    website: str = ""
    term: str = ""
    embedding_model: str = "tfidf"
    model_path: str | None = None
    aliases: dict[str, str] = field(default_factory=dict)
    logistics_terms: list[str] = field(default_factory=list)
    out_of_scope_terms: list[str] = field(default_factory=list)
    student_ai: str = "teacher"
    student_server: str = ""
    questions_per_day: int = 0
    allowed_origins: list[str] = field(default_factory=list)
    search_engine: str = "onnx"
    search_device: str = "cpu"
    # On a student's laptop the "course AI" is the teacher's website, never keys found on this computer.
    uses_local_key: bool = False
    server_needs_login: bool = False
    roster: str = ""

    def accounts(self):
        """Sign-in happens on the teacher's website, not in the course app."""
        return None

    @property
    def chunks_path(self) -> Path:
        return self.root / "chunks.jsonl"

    @property
    def graph_path(self) -> Path:
        return self.root / "graph.json"

    @property
    def index_dir(self) -> Path:
        return self.root / "index"

    @property
    def display_name(self) -> str:
        return f"{self.course_code} {self.course_name}".strip() if self.course_code else self.course_name

    def require(self, path: Path, hint: str = "") -> Path:
        if not path.exists():
            raise PackError(f"This course file is incomplete ({path.name} is missing). Ask your teacher for a new copy.")
        return path

    def apply_model_settings(self) -> None:
        """Course apps don't read API keys from the computer; see `uses_local_key`."""


def open_pack(path: Path) -> CourseApp:
    root = unpack(path)
    manifest = json.loads((root / "course.json").read_text(encoding="utf-8"))
    if int(manifest.get("format", 0)) > PACK_FORMAT:
        raise PackError("This course file needs a newer Wendao. Update with: pip install -U wendao")
    course, search, ai = manifest.get("course", {}), manifest.get("search", {}), manifest.get("ai", {})
    embeddings = root / "index" / "embeddings.npz"
    if embeddings.exists():
        # The index file name follows the model name, as in a workspace's build/index folder.
        model = search.get("embedding_model", "tfidf")
        named = root / "index" / f"embeddings_{re.sub(r'[^a-zA-Z0-9_.-]+', '_', model)}.npz"
        if not named.exists():
            embeddings.rename(named)
    has_model = (root / "model" / "onnx" / "model.onnx").exists()
    return CourseApp(
        root=root,
        course_name=course.get("name", "Course"),
        course_code=course.get("code", ""),
        website=course.get("website", ""),
        term=course.get("term", ""),
        embedding_model=search.get("embedding_model", "tfidf"),
        model_path=str(root / "model") if has_model else None,
        aliases=search.get("aliases", {}),
        logistics_terms=search.get("logistics_terms", []),
        out_of_scope_terms=search.get("out_of_scope_terms", []),
        student_ai=ai.get("mode", "teacher"),
        student_server=ai.get("server", ""),
        server_needs_login=bool(ai.get("login", False)),
    )
