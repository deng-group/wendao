"""Course workspaces: a folder with `wendao.toml`, your course settings, and generated files.

A workspace looks like this::

    my-course/
      wendao.toml       course name, where the notes are, chapters, search terms, model
      concepts.json    concepts shown in the knowledge graph
      questions.json   test questions for `wendao eval`
      .env             API keys (never committed)
      build/           generated chunks, graph, search index, and reports

Every path in `wendao.toml` is relative to the workspace folder.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


CONFIG_NAME = "wendao.toml"

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

DEFAULT_IGNORE_DIRS = [".git", "_build", ".ipynb_checkpoints", "__pycache__", "node_modules", ".venv"]

DEFAULT_TIME_SENSITIVE_FILES = ["syllabus.md", "calendar.md"]

DEFAULT_LOGISTICS_TERMS = [
    "assignment", "assignments", "before", "covered", "quiz", "quizzes", "exam", "final",
    "grading", "grade", "deadline", "deadlines", "schedule", "calendar", "syllabus",
    "lecture", "lectures", "midterm", "review", "topic", "topics", "week", "weeks", "when",
]

DEFAULT_OUT_OF_SCOPE_TERMS = [
    "cafeteria", "canteen", "menu", "weather", "stock", "price", "president", "restaurant", "flight", "hotel",
]

CHAPTER_VISIBILITY = {"primary", "secondary", "hidden"}

# Maps [model] settings in wendao.toml to the environment variables the providers read.
MODEL_ENV = {
    "anthropic": {"model": "ANTHROPIC_MODEL", "base_url": "ANTHROPIC_BASE_URL"},
    "openai": {"model": "OPENAI_MODEL", "base_url": "OPENAI_BASE_URL"},
    "gemini": {"model": "GEMINI_MODEL"},
}


class WorkspaceError(Exception):
    """A workspace is missing or its settings are invalid."""


@dataclass
class Chapter:
    folder: str
    name: str
    description: str = ""
    show: str = "primary"


@dataclass
class Workspace:
    root: Path
    course_name: str = "Course"
    course_code: str = ""
    website: str = ""
    term: str = ""
    source: Path | None = None
    use_toc: bool = False
    file_types: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    ignore_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_DIRS))
    time_sensitive_files: list[str] = field(default_factory=lambda: list(DEFAULT_TIME_SENSITIVE_FILES))
    chapters: list[Chapter] = field(default_factory=list)
    bridge_stop_concepts: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    logistics_terms: list[str] = field(default_factory=lambda: list(DEFAULT_LOGISTICS_TERMS))
    out_of_scope_terms: list[str] = field(default_factory=lambda: list(DEFAULT_OUT_OF_SCOPE_TERMS))
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    search_engine: str = "auto"
    search_device: str = "auto"
    model: dict = field(default_factory=dict)
    student_ai: str = "teacher"
    student_server: str = ""
    questions_per_day: int = 0
    allowed_origins: list[str] = field(default_factory=list)
    roster: str = ""

    # Workspace files
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def concepts_path(self) -> Path:
        return self.root / "concepts.json"

    @property
    def questions_path(self) -> Path:
        return self.root / "questions.json"

    @property
    def env_path(self) -> Path:
        return self.root / ".env"

    # Generated files
    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def chunks_path(self) -> Path:
        return self.build_dir / "chunks.jsonl"

    @property
    def graph_path(self) -> Path:
        return self.build_dir / "graph.json"

    @property
    def index_dir(self) -> Path:
        return self.build_dir / "index"

    @property
    def reports_dir(self) -> Path:
        return self.build_dir / "reports"

    @property
    def file_suffixes(self) -> set[str] | None:
        """File types to read from the notes folder, e.g. {".md", ".pdf"}; None means all supported types."""
        if not self.file_types:
            return None
        return {"." + kind.lower().lstrip(".") for kind in self.file_types}

    @property
    def roster_path(self) -> Path | None:
        return self.root / self.roster if self.roster else None

    def daily_limit(self):
        """The per-address daily limit used when there is no roster, shared by all server workers."""
        if self.roster or self.questions_per_day <= 0:
            return None
        from wendao.web.accounts import UsageStore
        from wendao.web.ai import SharedDailyLimit

        return SharedDailyLimit(UsageStore(self.root / "usage.db"), self.questions_per_day)

    def accounts(self):
        """Student sign-in for this course server, or None when there is no roster (see web/accounts.py)."""
        if not self.roster_path:
            return None
        from wendao.web.accounts import Accounts

        self.require(self.roster_path, "Add your class list there, or remove `roster` under [student].")
        return Accounts(self.roster_path, self.root, self.questions_per_day)

    @property
    def display_name(self) -> str:
        return f"{self.course_code} {self.course_name}".strip() if self.course_code else self.course_name

    def require_source(self) -> Path:
        if self.source is None:
            raise WorkspaceError(f"Set `path` under [source] in {self.config_path} to the folder with your course notes.")
        if not self.source.is_dir():
            raise WorkspaceError(f"Course notes folder not found: {self.source} (set in [source] in {CONFIG_NAME}).")
        return self.source

    def require(self, path: Path, hint: str) -> Path:
        if not path.exists():
            raise WorkspaceError(f"{path.relative_to(self.root)} not found. {hint}")
        return path

    def apply_model_settings(self) -> None:
        """Load `.env`, then fill in model settings from wendao.toml without overriding the environment."""
        from wendao.rag.providers import load_env_file

        load_env_file(self.env_path)
        provider = self.model.get("provider")
        if provider:
            os.environ.setdefault("LLM_PROVIDER", str(provider))
        if "temperature" in self.model:
            os.environ.setdefault("LLM_TEMPERATURE", str(self.model["temperature"]))
        for key, env_name in MODEL_ENV.get(str(provider or ""), {}).items():
            if self.model.get(key):
                os.environ.setdefault(env_name, str(self.model[key]))


def find_root(start: Path | None = None) -> Path:
    """Find the workspace containing `start`, `$WENDAO_WORKSPACE`, or the current folder."""
    if start is None and os.environ.get("WENDAO_WORKSPACE"):
        start = Path(os.environ["WENDAO_WORKSPACE"])
    current = (start or Path.cwd()).expanduser().resolve()
    for folder in [current, *current.parents]:
        if (folder / CONFIG_NAME).is_file():
            return folder
    raise WorkspaceError(
        f"No {CONFIG_NAME} found in {current} or its parent folders.\n"
        "Run `wendao init` to create a workspace, or run this command inside one (or pass --workspace)."
    )


def load(start: Path | None = None) -> Workspace:
    root = find_root(start)
    config_path = root / CONFIG_NAME
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"{config_path} is not valid TOML: {exc}") from None

    course = config.get("course", {})
    source = config.get("source", {})
    search = config.get("search", {})
    graph = config.get("graph", {})
    student = config.get("student", {})
    if student.get("ai", "teacher") not in {"teacher", "student", "either"}:
        raise WorkspaceError(f"[student] ai in {CONFIG_NAME} must be \"teacher\", \"student\", or \"either\".")

    chapters = []
    for index, item in enumerate(config.get("chapters", [])):
        if "folder" not in item:
            raise WorkspaceError(f"Chapter {index + 1} in {CONFIG_NAME} needs a `folder`.")
        show = item.get("show", "primary")
        if show not in CHAPTER_VISIBILITY:
            raise WorkspaceError(f"Chapter `{item['folder']}`: `show` must be one of {', '.join(sorted(CHAPTER_VISIBILITY))}.")
        chapters.append(
            Chapter(
                folder=item["folder"],
                name=item.get("name", item["folder"].replace("_", " ").title()),
                description=item.get("description", ""),
                show=show,
            )
        )

    from wendao.ingest import SUPPORTED_SUFFIXES

    unknown = sorted({"." + str(k).lower().lstrip(".") for k in source.get("file_types", [])} - SUPPORTED_SUFFIXES)
    if unknown:
        supported = ", ".join(sorted(suffix.lstrip(".") for suffix in SUPPORTED_SUFFIXES))
        raise WorkspaceError(f"Unsupported file_types in {CONFIG_NAME}: {', '.join(unknown)}. Supported: {supported}.")

    workspace = Workspace(
        root=root,
        course_name=course.get("name", root.name),
        course_code=course.get("code", ""),
        website=course.get("website", ""),
        term=course.get("term", ""),
        use_toc=bool(source.get("use_toc", False)),
        file_types=[str(kind) for kind in source.get("file_types", [])],
        source=(root / source["path"]).resolve() if source.get("path") else None,
        exclude=list(source.get("exclude", [])),
        ignore_dirs=list(DEFAULT_IGNORE_DIRS) + [d for d in source.get("ignore_dirs", []) if d not in DEFAULT_IGNORE_DIRS],
        time_sensitive_files=list(source.get("time_sensitive_files", DEFAULT_TIME_SENSITIVE_FILES)),
        chapters=chapters,
        bridge_stop_concepts=list(graph.get("bridge_stop_concepts", [])),
        aliases=dict(search.get("aliases", {})),
        logistics_terms=list(search.get("logistics_terms", DEFAULT_LOGISTICS_TERMS)),
        out_of_scope_terms=list(search.get("out_of_scope_terms", DEFAULT_OUT_OF_SCOPE_TERMS)),
        embedding_model=search.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
        search_engine=str(search.get("engine", "auto")),
        search_device=str(search.get("device", "auto")),
        model=dict(config.get("model", {})),
        student_ai=str(student.get("ai", "teacher")),
        student_server=str(student.get("server", "")),
        questions_per_day=int(student.get("questions_per_day", 0) or 0),
        allowed_origins=[str(item) for item in student.get("allowed_origins", [])],
        roster=str(student.get("roster", "")),
    )
    return workspace
