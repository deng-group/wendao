"""Extract course notes into search chunks.

Reads Markdown/MyST pages, Jupyter notebooks, PDF, PowerPoint, Word, LaTeX, HTML, and plain
text (see `readers.py` for the non-Markdown formats).

Each file is split into chunks of roughly 100-400 tokens, keeping paragraphs together.
Every chunk records its file, chapter (top-level folder), section, title, and headings.
Files listed as time-sensitive (by default `syllabus.md` and `calendar.md`) are tagged
with the course term so answers about deadlines and schedules state which term they mean.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from wendao.readers import READERS

SUPPORTED_SUFFIXES = {".md", ".ipynb", *READERS}


class ContentExtractor:
    """Extract content from MyST markdown and Jupyter notebook files"""

    def __init__(
        self,
        repo_path: str | Path,
        time_sensitive_files: List[str] | None = None,
        term: str = "",
        suffixes: set[str] | None = None,
    ):
        self.repo_path = Path(repo_path)
        # File types to read (from `[source] file_types`); every path in, including the table of contents, honours it.
        self.suffixes = suffixes or SUPPORTED_SUFFIXES
        self.time_sensitive_files = set(time_sensitive_files if time_sensitive_files is not None else ["syllabus.md", "calendar.md"])
        self.chunks = []
        self.warnings: List[str] = []
        self.course_temporal_context = self.load_course_temporal_context(term)

    def load_course_temporal_context(self, term: str = "") -> Dict[str, Any]:
        """Use the configured term, or read it from a `- Year: ...` line in syllabus.md."""
        syllabus_path = self.repo_path / "syllabus.md"
        context = {
            "source": "wendao.toml" if term else "syllabus.md",
            "year": None,
            "academic_year": None,
            "semester": None,
            "requires_time_disambiguation": True,
        }

        year = term.strip()
        if not year and syllabus_path.exists():
            content = syllabus_path.read_text(encoding="utf-8")
            match = re.search(r"^- Year:\s*(.+)$", content, re.MULTILINE)
            if match:
                year = match.group(1).strip()
        if not year:
            return context

        context["year"] = year

        ay_match = re.search(r"(AY\d{4}/\d{4})", year)
        semester_match = re.search(r"(Semester\s+\d+)", year, re.IGNORECASE)
        if ay_match:
            context["academic_year"] = ay_match.group(1)
        if semester_match:
            context["semester"] = semester_match.group(1)

        return context

    def extract_headings(self, content: str) -> List[Dict[str, Any]]:
        """Extract headings hierarchy from markdown content"""
        headings = []
        for match in re.finditer(r'^(#{1,6})\s+(.+)$', content, re.MULTILINE):
            level = len(match.group(1))
            title = match.group(2).strip()
            headings.append({
                'level': level,
                'title': title,
                'position': match.start()
            })
        return headings

    def make_chunk(self, content: str, token_estimate: float, metadata: Dict[str, Any], chunk_id: int) -> Dict[str, Any]:
        """Create one chunk and inject temporal context where needed."""
        chunk_content = content.strip()
        if metadata.get("temporal_context"):
            note = metadata.get("temporal_context_note", "")
            if note and not chunk_content.startswith(note):
                chunk_content = f"{note}\n\n{chunk_content}"

        return {
            'chunk_id': f"{metadata['file_path']}_{chunk_id}",
            'content': chunk_content,
            'token_estimate': int(token_estimate),
            **metadata
        }

    def split_into_chunks(self, content: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Split content into chunks of 100-300 tokens (approximately 200-600 words)
        while preserving paragraph boundaries
        """
        chunks = []

        # Split by headings first to preserve context
        sections = re.split(r'\n(?=#{1,6}\s+)', content)

        current_chunk = ""
        current_tokens = 0
        chunk_id = 0

        # Approximate tokens: 1 token ≈ 0.75 words
        TARGET_TOKENS = 200
        MAX_TOKENS = 400
        MIN_TOKENS = 100

        for section in sections:
            if not section.strip():
                continue

            # Split section into paragraphs
            paragraphs = section.split('\n\n')

            for para in paragraphs:
                if not para.strip():
                    continue

                # Estimate tokens (rough approximation)
                para_tokens = len(para.split()) * 1.33

                # If paragraph is too large, split it
                if para_tokens > MAX_TOKENS:
                    sentences = re.split(r'(?<=[.!?])\s+', para)
                    for sent in sentences:
                        sent_tokens = len(sent.split()) * 1.33
                        if current_tokens + sent_tokens > MAX_TOKENS and current_tokens > MIN_TOKENS:
                            # Save current chunk
                            chunk_data = self.make_chunk(current_chunk, current_tokens, metadata, chunk_id)
                            chunks.append(chunk_data)
                            chunk_id += 1
                            current_chunk = sent
                            current_tokens = sent_tokens
                        else:
                            current_chunk += " " + sent
                            current_tokens += sent_tokens
                else:
                    # Add paragraph to current chunk
                    if current_tokens + para_tokens > MAX_TOKENS and current_tokens > MIN_TOKENS:
                        # Save current chunk
                        chunk_data = self.make_chunk(current_chunk, current_tokens, metadata, chunk_id)
                        chunks.append(chunk_data)
                        chunk_id += 1
                        current_chunk = para
                        current_tokens = para_tokens
                    else:
                        if current_chunk:
                            current_chunk += "\n\n" + para
                        else:
                            current_chunk = para
                        current_tokens += para_tokens

        # Save remaining content
        if current_chunk.strip():
            chunk_data = self.make_chunk(current_chunk, current_tokens, metadata, chunk_id)
            chunks.append(chunk_data)

        return chunks

    def temporal_metadata_for_file(self, relative_path: str) -> Dict[str, Any]:
        """Return additional time-sensitive metadata for offering-specific files."""
        if relative_path not in self.time_sensitive_files:
            return {}

        year = self.course_temporal_context.get("year") or "unknown academic year/semester"
        kind = Path(relative_path).stem.replace("_", " ")
        note = (
            f"Temporal context: This {kind} information applies to {year}. "
            "Answers about course schedule, assessment, grading, deadlines, or logistics must state this time context."
        )

        return {
            "temporal_context": self.course_temporal_context,
            "temporal_context_note": note,
            "time_sensitive": True,
        }

    def extract_markdown(self, file_path: Path, module: str, section: str) -> List[Dict[str, Any]]:
        """Extract content from markdown file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Extract headings
        headings = self.extract_headings(content)

        # Extract metadata
        metadata = {
            'file_path': file_path.relative_to(self.repo_path).as_posix(),
            'file_type': 'markdown',
            'module': module,
            'section': section,
            'headings': headings,
            'title': headings[0]['title'] if headings else file_path.stem,
        }
        metadata.update(self.temporal_metadata_for_file(metadata['file_path']))

        # Split into chunks
        chunks = self.split_into_chunks(content, metadata)
        return chunks

    def extract_notebook(self, file_path: Path, module: str, section: str) -> List[Dict[str, Any]]:
        """Extract content from Jupyter notebook"""
        import nbformat

        with open(file_path, 'r', encoding='utf-8') as f:
            nb = nbformat.read(f, as_version=4)

        content_parts = []
        code_cells = []

        for cell in nb.cells:
            if cell.cell_type == 'markdown':
                content_parts.append(cell.source)
            elif cell.cell_type == 'code':
                if cell.source.strip():
                    code_cells.append(cell.source)
                    # Also include code descriptions
                    content_parts.append(f"```python\n{cell.source}\n```")

        full_content = "\n\n".join(content_parts)

        # Extract headings
        headings = self.extract_headings(full_content)

        metadata = {
            'file_path': file_path.relative_to(self.repo_path).as_posix(),
            'file_type': 'notebook',
            'module': module,
            'section': section,
            'headings': headings,
            'title': headings[0]['title'] if headings else file_path.stem,
        }
        metadata.update(self.temporal_metadata_for_file(metadata['file_path']))

        chunks = self.split_into_chunks(full_content, metadata)
        return chunks

    def extract_document(self, file_path: Path, module: str, section: str) -> List[Dict[str, Any]]:
        """Extract a PDF, PowerPoint, Word, LaTeX, HTML, or text file via `readers.py`."""
        document = READERS[file_path.suffix.lower()](file_path)
        relative = file_path.relative_to(self.repo_path).as_posix()
        if not document.parts:
            hint = " It may be a scanned PDF, which needs OCR first." if document.file_type == "pdf" else ""
            self.warnings.append(f"No text found in {relative}.{hint}")
            return []
        metadata = {
            'file_path': relative,
            'file_type': document.file_type,
            'module': module,
            'section': section,
            'title': document.title,
        }
        metadata.update(self.temporal_metadata_for_file(relative))
        chunks = []
        for part in document.parts:
            part_metadata = {**metadata, 'headings': self.extract_headings(part.text)}
            if part.location:
                part_metadata['location'] = part.location
            chunks.extend(self.split_into_chunks(part.text, part_metadata))
        # Number chunks across the whole file so IDs stay unique when a file has many parts.
        for index, chunk in enumerate(chunks):
            chunk['chunk_id'] = f"{relative}_{index}"
        return chunks

    def extract_file(self, file_path: Path) -> None:
        """Extract one supported file and add its chunks. Problems become warnings, not crashes."""
        suffix = file_path.suffix.lower()
        if suffix not in self.suffixes:
            return
        parts = file_path.relative_to(self.repo_path).parts
        module = parts[0] if len(parts) > 1 else "root"
        section = parts[1] if len(parts) > 2 else file_path.stem
        try:
            if suffix == '.md':
                chunks = self.extract_markdown(file_path, module, section)
            elif suffix == '.ipynb':
                chunks = self.extract_notebook(file_path, module, section)
            else:
                chunks = self.extract_document(file_path, module, section)
        except Exception as exc:  # noqa: BLE001 - one broken file should not stop the build
            self.warnings.append(f"Could not read {file_path.relative_to(self.repo_path)}: {exc}")
            return
        self.chunks.extend(chunks)

    def process_all(self, toc_structure: Dict[str, Any]) -> None:
        """Process all files listed in a myst.yml table of contents."""
        for item in toc_structure:
            if 'file' in item:
                file_path = self.repo_path / item['file']
                if not file_path.exists():
                    self.warnings.append(f"{item['file']} is in the table of contents but does not exist.")
                else:
                    self.extract_file(file_path)
            if 'children' in item:
                self.process_all(item['children'])

    def process_file(self, relative_path: str) -> None:
        """Process one file by its path relative to the notes folder."""
        file_path = self.repo_path / relative_path
        if not file_path.exists():
            self.warnings.append(f"{relative_path} does not exist.")
            return
        self.extract_file(file_path)

    def process_files(self, relative_paths: List[str]) -> None:
        """Process a list of relative file paths."""
        for relative_path in relative_paths:
            self.process_file(relative_path)

    def save_chunks(self, output_path: str | Path) -> None:
        """Save chunks to a JSONL file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            for chunk in self.chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + '\n')


def load_toc_structure(repo_path: str | Path) -> Dict[str, Any]:
    """Load the table of contents from myst.yml."""
    import yaml

    myst_config = Path(repo_path) / 'myst.yml'
    with open(myst_config, 'r') as f:
        config = yaml.safe_load(f)

    return config['project']['toc']


def discover_content_files(
    repo_path: str | Path,
    exclude: List[str],
    ignore_dirs: List[str],
    suffixes: set[str] | None = None,
) -> List[str]:
    """Find every supported file, skipping ignored folders and excluded files."""
    suffixes = suffixes or SUPPORTED_SUFFIXES
    repo = Path(repo_path)
    ignored_parts = set(ignore_dirs)
    excluded = {Path(item).as_posix() for item in exclude}
    files = []

    for path in repo.rglob('*'):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        relative = path.relative_to(repo)
        if any(part in ignored_parts or part.endswith('.egg-info') for part in relative.parts[:-1]):
            continue
        relative_str = relative.as_posix()  # forward slashes on every OS, so paths match across machines
        if relative_str in excluded:
            continue
        files.append(relative_str)

    return sorted(files)


def fingerprints_path(workspace) -> Path:
    return workspace.build_dir / "notes.json"


def extract_settings(workspace) -> str:
    """Besides the files themselves, what decides the chunks: the [source] settings and the Wendao version (a new
    version may read notes differently)."""
    from wendao import __version__

    settings = {
        "wendao": __version__, "source": str(workspace.source), "use_toc": workspace.use_toc,
        "exclude": sorted(workspace.exclude), "ignore_dirs": sorted(workspace.ignore_dirs),
        "types": sorted(workspace.file_suffixes or []), "time_sensitive": sorted(workspace.time_sensitive_files),
        "term": workspace.term,
    }
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def read_record(workspace) -> tuple[str | None, dict[str, str]] | None:
    """The last build's (settings, file fingerprints), or None. Older records hold only the fingerprints."""
    path = fingerprints_path(workspace)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if isinstance(record.get("files"), dict):
        return record.get("settings"), record["files"]
    return None, record


def fingerprints(source: Path, files: list[str]) -> dict[str, str]:
    """A short content hash of each note file, so Wendao can tell when the notes changed."""
    result = {}
    for name in files:
        path = source / name
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return result


def changed_notes(workspace) -> list[str] | None:
    """Note files added, changed, or removed since the last build; None if the build kept no record."""
    record = read_record(workspace)
    if record is None:
        return None
    source = workspace.require_source()
    before = record[1]
    files = set(before)
    if not workspace.use_toc:
        files |= set(discover_content_files(source, workspace.exclude, workspace.ignore_dirs, workspace.file_suffixes))
    now = fingerprints(source, sorted(files))
    return sorted(name for name in files if before.get(name) != now.get(name))


def notes_up_to_date(workspace) -> bool:
    """The chunks were made from exactly these notes, with these settings and this Wendao version."""
    record = read_record(workspace)
    return (workspace.chunks_path.exists() and record is not None and record[0] == extract_settings(workspace)
            and changed_notes(workspace) == [])


def extract(workspace) -> dict:
    """Extract the workspace's course notes into build/chunks.jsonl. Returns a short summary."""
    source = workspace.require_source()
    extractor = ContentExtractor(source, workspace.time_sensitive_files, workspace.term, workspace.file_suffixes)
    if workspace.use_toc:
        extractor.process_all(load_toc_structure(source))
        file_count = len({chunk["file_path"] for chunk in extractor.chunks})
    else:
        files = discover_content_files(source, workspace.exclude, workspace.ignore_dirs, workspace.file_suffixes)
        extractor.process_files(files)
        file_count = len(files)
    extractor.save_chunks(workspace.chunks_path)
    used = files if not workspace.use_toc else sorted({chunk["file_path"] for chunk in extractor.chunks} | {"myst.yml"})
    record = {"settings": extract_settings(workspace), "files": fingerprints(source, used)}
    fingerprints_path(workspace).write_text(json.dumps(record, indent=0), encoding="utf-8")
    return {
        "files": file_count,
        "chunks": len(extractor.chunks),
        "term": extractor.course_temporal_context.get("year"),
        "types": sorted({chunk["file_type"] for chunk in extractor.chunks}),
        "warnings": extractor.warnings,
    }
