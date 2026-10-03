"""Make a MyST course website from a folder of course material, and build it.

`new(workspace, out)` turns the workspace's notes folder into a MyST project:

- `myst.yml` with a table of contents: home page, syllabus, calendar, then one chapter per top-level folder
  (in the order of [[chapters]] in wendao.toml, then by name);
- a home page, and `syllabus.md` / `calendar.md` to fill in (yours are used if the notes have them);
- Markdown pages and notebooks copied as they are; slides, Word, PDF and LaTeX files turned into pages, with
  the original file as a download; images and data files copied, so links in the pages keep working.

Hidden files (like `.env`), the class list, and the workspace's own files are never copied: the site is
meant to be published. `build(site)` runs MyST (`myst`, Jupyter Book 2, or `npx mystmd`) and adds the widget.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from wendao.workspace import CONFIG_NAME

PAGE_SUFFIXES = {".md", ".ipynb"}
CONVERTED = {  # file type → (how the download link reads, kind of page)
    ".pptx": "Download the slides (PowerPoint)",
    ".docx": "Download the document (Word)",
    ".pdf": "Open the PDF",
    ".tex": "Download the LaTeX source",
}
JUNK_TITLES = re.compile(r"^(powerpoint presentation|presentation|untitled.*|slide \d+|microsoft (word|powerpoint).*)$|\.(docx?|pptx?)$", re.I)


class SiteError(RuntimeError):
    """A problem the teacher can fix; the message says how."""


@dataclass
class Page:
    source: Path   # the file in the notes folder
    target: str    # path of the page in the site, e.g. "week1/lecture1.md"
    title: str = ""


@dataclass
class Plan:
    root_pages: list[Page] = field(default_factory=list)
    chapters: dict[str, list[Page]] = field(default_factory=dict)
    special: dict[str, Path] = field(default_factory=dict)  # index.md, syllabus.md, calendar.md, <chapter>/index.md
    assets: list[tuple[Path, str]] = field(default_factory=list)


# ---------- small helpers ----------

def natural(text: str) -> list:
    """Sort key that puts "lecture2" before "lecture10"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def humanize(name: str) -> str:
    words = re.sub(r"[_\-]+", " ", name).strip()
    words = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", words)  # "week3" → "week 3"
    return words[:1].upper() + words[1:] if words.islower() else words


def yaml_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid YAML string


# Bullet marks in PDF text: real bullets, and the control or private-use characters many slide PDFs use for them.
PDF_BULLET = re.compile(r"^[•●▪◦‣∙\x00-\x08\x0b-\x1f\x7f\ue000-\uf8ff]\s*")
SPECIAL = re.compile(r"([\\`*_{}\[\]<>$|~])")


def plain(text: str) -> str:
    """Text from a slide or document, made safe to put in a MyST page (no accidental markup)."""
    text = SPECIAL.sub(r"\\\1", text)
    return re.sub(r"^(\s*)([#+=%]|:::|-{3,})", r"\1\\\2", text)


def first_heading(path: Path) -> str:
    """The title of a Markdown page or notebook: front-matter title or first `# ` heading."""
    try:
        if path.suffix == ".ipynb":
            cells = json.loads(path.read_text(encoding="utf-8")).get("cells", [])
            text = "\n".join("".join(cell.get("source", "")) for cell in cells if cell.get("cell_type") == "markdown")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    match = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    if match:
        title = re.search(r"^title:\s*(.+)$", match.group(1), re.M)
        if title:
            return title.group(1).strip().strip("'\"")
    heading = re.search(r"^#\s+(.+)$", text, re.M)
    return heading.group(1).strip() if heading else ""


# ---------- deciding what goes where ----------

def private_files(workspace) -> set[Path]:
    """Workspace files that must never end up on a public website."""
    names = [workspace.config_path, workspace.concepts_path, workspace.root / "questions.json", workspace.root / "usage.db",
             workspace.root / ".env", workspace.root / ".wendao-secret"]
    if getattr(workspace, "roster", ""):
        names.append(workspace.root / workspace.roster)
    return {path.resolve() for path in names}


def plan_site(workspace, source: Path, out: Path) -> Plan:
    source, out = source.resolve(), out.resolve()
    private = private_files(workspace)
    skip_dirs = set(workspace.ignore_dirs)
    excluded = {Path(item).as_posix() for item in workspace.exclude}
    build_dir = workspace.build_dir.resolve()

    files = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        resolved = path.resolve()
        if (any(part.startswith(".") for part in relative.parts)
                or any(part in skip_dirs or part.endswith(".egg-info") for part in relative.parts[:-1])
                or relative.as_posix() in excluded or resolved in private
                or resolved.is_relative_to(out) or resolved.is_relative_to(build_dir)):
            continue
        files.append(relative)

    # A PDF or slide file that a page links to (a figure, a handout) stays a file, not a page of its own.
    mentioned = " ".join(
        (source / item).read_text(encoding="utf-8", errors="replace") for item in files if item.suffix.lower() in PAGE_SUFFIXES
    )

    plan = Plan()
    taken = {item.with_suffix("").as_posix() + item.suffix.lower() for item in files if item.suffix.lower() in PAGE_SUFFIXES}
    for relative in sorted(files, key=lambda item: natural(item.as_posix())):
        suffix = relative.suffix.lower()
        is_page = suffix in PAGE_SUFFIXES or (suffix in CONVERTED and relative.name not in mentioned)
        if not is_page:
            plan.assets.append((source / relative, relative.as_posix()))
            continue
        top = relative.parts[0] if len(relative.parts) > 1 else ""
        if relative.name.lower() == "index.md" and len(relative.parts) <= 2:
            plan.special[f"{top}/index.md" if top else "index.md"] = source / relative
            continue
        if not top and relative.name.lower() in {"syllabus.md", "calendar.md"}:
            plan.special[relative.name.lower()] = source / relative
            continue
        if suffix in PAGE_SUFFIXES:
            target = relative.as_posix()
        else:
            # lecture1.pptx → lecture1.md (or lecture1-pptx.md if there is already a lecture1.md)
            stem = relative.with_suffix("").as_posix()
            target = f"{stem}.md" if f"{stem}.md" not in taken else f"{stem}-{suffix[1:]}.md"
            taken.add(target)
            plan.assets.append((source / relative, relative.as_posix()))  # the original, for the download link
        page = Page(source / relative, target)
        if top:
            plan.chapters.setdefault(top, []).append(page)
        else:
            plan.root_pages.append(page)
    return plan


def chapter_order(workspace, folders) -> list[str]:
    configured = [chapter.folder for chapter in workspace.chapters if chapter.folder in folders]
    return configured + sorted((folder for folder in folders if folder not in configured), key=natural)


# ---------- writing pages ----------

def converted_page(path: Path, relative_link: str) -> tuple[str, str]:
    """A page for a slide deck, Word file, PDF, or LaTeX file. Returns (title, MyST text)."""
    from wendao.readers import READERS

    document = READERS[path.suffix.lower()](path)
    title = document.title.strip()
    if not title or JUNK_TITLES.search(title):
        title = humanize(path.stem)
    lines = [f"---\ntitle: {yaml_text(title)}\n---", "", f"{{download}}`{CONVERTED[path.suffix.lower()]} <{relative_link}>`", ""]
    kind = path.suffix.lower()

    if kind == ".pdf":
        pages = [part for part in document.parts if part.text.strip()]
        if not pages:
            lines.append("This PDF has no text that can be shown here (it may be scanned images). Open it with the link above.")
        else:
            lines += [f":::{{dropdown}} Text of the PDF ({len(pages)} {'page' if len(pages) == 1 else 'pages'})", ""]
            for part in pages:
                lines += [f"**{part.location.capitalize()}**", ""]
                page_lines = [line.strip() for line in part.text.splitlines() if line.strip()]
                # Slides have short lines that should stay apart; notes and papers are reflowed into paragraphs.
                slide_like = sum(map(len, page_lines)) / max(len(page_lines), 1) < 60
                for paragraph in re.split(r"\n\s*\n", part.text):
                    rows = [plain(PDF_BULLET.sub("• ", line.strip())) for line in paragraph.splitlines() if line.strip()]
                    lines += [("\\\n" if slide_like else "\n").join(rows), ""]
            lines.append(":::")
        return title, "\n".join(lines).rstrip() + "\n"

    title_heading_seen = False
    for part in document.parts:
        notes = []
        in_notes = False
        raw_lines = part.text.splitlines()
        index = 0
        while index < len(raw_lines):
            line = raw_lines[index].rstrip()
            index += 1
            if in_notes:
                notes.append(plain(line))
                continue
            if line.startswith("Speaker notes:"):
                in_notes = True
                notes.append(plain(line[len("Speaker notes:"):].strip()))
                continue
            if " | " in line and kind != ".tex":
                # Table rows from Word and PowerPoint ("a | b | c"): rows with the same number of cells form a table.
                width = line.count(" | ") + 1
                rows = [line]
                while index < len(raw_lines) and raw_lines[index].count(" | ") + 1 == width:
                    rows.append(raw_lines[index].rstrip())
                    index += 1
                if len(rows) > 1:
                    cells = [[plain(cell.strip()) for cell in row.split(" | ")] for row in rows]
                    lines += ["", "| " + " | ".join(cells[0]) + " |", "|" + " --- |" * width]
                    lines += ["| " + " | ".join(row) + " |" for row in cells[1:]]
                    lines.append("")
                    continue
            heading = re.match(r"^(#{1,6})\s+(.*)$", line)
            bullet = re.match(r"^(\s*)- (.*)$", line)
            if heading:
                if not title_heading_seen and heading.group(2).strip() == title:
                    title_heading_seen = True
                    continue  # the page title is already at the top
                level = max(2, len(heading.group(1)))
                text = heading.group(2) if kind == ".tex" else plain(heading.group(2))
                lines += ["", f"{'#' * level} {text}", ""]
            elif bullet:
                text = bullet.group(2) if kind == ".tex" else plain(bullet.group(2))
                lines.append(f"{bullet.group(1)}- {text}")
            elif line.strip():
                # LaTeX keeps its $math$, which MyST shows; other text is escaped. Each line is its own paragraph.
                text = re.sub(r"^(\s*)([#%<]|:::)", r"\1\\\2", line) if kind == ".tex" else plain(line)
                if lines and re.match(r"^\s*- ", lines[-1]):
                    lines.append("")  # end the list first
                lines += [text, ""]
            else:
                lines.append("")
        if notes and any(note.strip() for note in notes):
            lines += ["", ":::{note} Speaker notes", *notes, ":::", ""]
    text = "\n".join(lines)
    text = re.sub(r"(\n\s*- [^\n]*)\n\n+(?=\s*- )", r"\1\n", text)  # keep lists tight
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    return title, text


def home_page(workspace, chapters: list[tuple[str, str, str]]) -> str:
    """The landing page: course title and term (MyST's title block), a start button, and a card per chapter."""
    subtitle = " · ".join(item for item in [workspace.course_code, workspace.term] if item)
    lines = ["---", f"title: {yaml_text(workspace.course_name)}"]
    if subtitle:
        lines.append(f"subtitle: {yaml_text(subtitle)}")
    lines += ["site:", "  hide_outline: true", "  hide_toc: true", "---", "",
              "Welcome! Start with the syllabus, or pick a chapter below.", "",
              "{button}`Get started <syllabus.md>`", "", "## Chapters", "", "::::{grid} 1 2 2 3", ""]
    for folder, title, description in chapters:
        lines += [f":::{{card}} {title}", f":link: {folder}/index.md", "", description, ":::", ""]
    lines += ["::::", ""]
    return "\n".join(lines)


def syllabus_page(workspace) -> str:
    year = f"- Year: {workspace.term}" if workspace.term else "% Fill in the term, e.g.:  - Year: AY2026/2027 Semester 1"
    instructor = getattr(workspace, "instructor", "") or "(fill in)"
    return f"""# Syllabus

{year}
- Instructor: {instructor}
- Email: (fill in)
- Lectures: (fill in day, time and room)

## Goals

- (fill in what students will be able to do after the course)

## Organization

- (fill in: lectures, tutorials, assignments, quizzes, exams)

## Grading

| Part | Weight |
| --- | --- |
| (fill in) | |

## Textbooks

This website is the main text for the course. (Add any other books here.)
"""


def calendar_page(chapters: list[tuple[str, str, str]]) -> str:
    rows = "\n".join(f"| {number} | {title} | {plain(description)} |" for number, (_, title, description) in enumerate(chapters, 1))
    return f"""# Calendar

:::{{warning}} Planned schedule
This is the planned schedule. It may change as the course goes on.
:::

| Week | Topic | Details |
| --- | --- | --- |
{rows}
"""


def chapter_page(title: str, description: str, pages: list[Page]) -> str:
    links = "\n".join(f"- [{page.title}]({Path(page.target).relative_to(Path(page.target).parts[0]).as_posix()})" for page in pages)
    return f"---\ntitle: {yaml_text(title)}\n---\n\n{description}\n\n## In this chapter\n\n{links}\n".replace("\n\n\n", "\n\n")


def myst_config(workspace, toc: list[dict]) -> str:
    author = getattr(workspace, "instructor", "") or (f"{workspace.course_code} Teaching Team" if workspace.course_code else "Teaching Team")

    def entries(items, indent):
        out = []
        for item in items:
            out.append(f"{indent}- file: {yaml_text(item['file'])}")
            if item.get("children"):
                out.append(f"{indent}  children:")
                out.extend(entries(item["children"], indent + "    "))
        return out

    lines = [
        "# MyST website for the course. Made by `wendao site new`; edit freely.",
        "version: 1",
        "project:",
        f"  title: {yaml_text(workspace.course_name)}",
        "  authors:",
        f"    - name: {yaml_text(author)}",
        "  exclude:",
        "    - _build",
        "    - README.md",
        "    - '**.ipynb_checkpoints'",
        "  toc:",
        *entries(toc, "    "),
        "site:",
        f"  title: {yaml_text(workspace.course_name)}",
        "  options:",
        "    folders: true",
        f"    logo_text: {yaml_text(workspace.course_name)}",
        "    # logo: logo.svg",
        "    # favicon: favicon.ico",
    ]
    if workspace.website:
        lines += ["  # actions:", "  #   - title: Course website", f"  #     url: {workspace.website}"]
    return "\n".join(lines) + "\n"


README = """# {name}: course website

Made by Wendao (`wendao site new`) from your course files. Edit the pages here like any MyST site:
`myst.yml` has the title and the table of contents; fill in `syllabus.md` and `calendar.md`.

Build the website and add the Wendao chat widget:

    wendao site build

The finished site is in `_build/html`: publish that folder on any web server.
After changing pages, run `wendao build` in the workspace so the AI and the knowledge graph see the changes.
"""


# ---------- the commands ----------

def new(workspace, out: Path | None = None) -> dict:
    """Create the MyST site. Returns a summary for the command line."""
    source = workspace.require_source().resolve()
    out = Path(out or workspace.root / "site").expanduser().resolve()
    if (source / "myst.yml").exists() or (source / "_toc.yml").exists():
        raise SiteError(
            f"Your notes in {source} are already a MyST / Jupyter Book site. Build it as usual, then add the "
            "chat widget with:  wendao widget install _build/html"
        )
    if out == source or source.is_relative_to(out):
        raise SiteError(f"Choose a different folder for the website than your notes ({source}), e.g. --out site.")
    if out.exists() and any(out.iterdir()):
        raise SiteError(f"{out} already has files in it. Choose another folder with --out, or delete it first.")

    plan = plan_site(workspace, source, out)
    if not plan.chapters and not plan.root_pages:
        raise SiteError(f"No pages found in {source}. Wendao uses Markdown, notebooks, PowerPoint, Word, PDF and LaTeX files.")

    out.mkdir(parents=True, exist_ok=True)
    for path, target in plan.assets:
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out / target)

    counts = {"copied": 0, "converted": 0}
    warnings = []

    def write_page(page: Page) -> None:
        destination = out / page.target
        destination.parent.mkdir(parents=True, exist_ok=True)
        if page.source.suffix.lower() in PAGE_SUFFIXES:
            shutil.copy2(page.source, destination)
            page.title = first_heading(page.source) or humanize(page.source.stem)
            counts["copied"] += 1
            return
        try:
            page.title, text = converted_page(page.source, page.source.name)
        except Exception as exc:  # noqa: BLE001 - one broken file should not stop the site
            warnings.append(f"Could not read {page.source.relative_to(source)} ({exc}); it is linked as a download instead.")
            page.title = humanize(page.source.stem)
            text = (f"---\ntitle: {yaml_text(page.title)}\n---\n\n"
                    f"{{download}}`{CONVERTED[page.source.suffix.lower()]} <{page.source.name}>`\n")
        destination.write_text(text, encoding="utf-8")
        counts["converted"] += 1

    for page in plan.root_pages:
        write_page(page)
    chapters = []  # (folder, title, description)
    toc = [{"file": "index.md"}, {"file": "syllabus.md"}, {"file": "calendar.md"}]
    toc += [{"file": page.target} for page in plan.root_pages]
    names = {chapter.folder: chapter for chapter in workspace.chapters}
    for folder in chapter_order(workspace, plan.chapters):
        pages = plan.chapters[folder]
        for page in pages:
            write_page(page)
        configured = names.get(folder)
        title = configured.name if configured else humanize(folder)
        description = configured.description if configured else ""
        index = f"{folder}/index.md"
        if index in plan.special:
            shutil.copy2(plan.special[index], out / index)
        else:
            (out / index).write_text(chapter_page(title, description, pages), encoding="utf-8")
        summary = description or ", ".join(page.title for page in pages[:3]) + (", ..." if len(pages) > 3 else "")
        chapters.append((folder, title, summary))
        toc.append({"file": index, "children": [{"file": page.target} for page in pages]})

    used_own = []
    for name, make in [("index.md", lambda: home_page(workspace, chapters)), ("syllabus.md", lambda: syllabus_page(workspace)),
                       ("calendar.md", lambda: calendar_page(chapters))]:
        if name in plan.special:
            shutil.copy2(plan.special[name], out / name)
            used_own.append(name)
        else:
            (out / name).write_text(make(), encoding="utf-8")

    (out / "myst.yml").write_text(myst_config(workspace, toc), encoding="utf-8")
    (out / ".gitignore").write_text("_build/\n", encoding="utf-8")
    (out / "README.md").write_text(README.format(name=workspace.course_name), encoding="utf-8")
    return {
        "folder": out,
        "chapters": len(chapters),
        "copied": counts["copied"],
        "converted": counts["converted"],
        "files": len(plan.assets),
        "used_own": used_own,
        "warnings": warnings,
    }


def point_source_at(workspace, site: Path) -> str:
    """Make the website the notes Wendao reads: [source] path = the site, use_toc = true. Returns the old path."""
    config = workspace.config_path
    try:
        relative = Path(os.path.relpath(Path(site).resolve(), workspace.root.resolve())).as_posix()
    except ValueError:  # another drive on Windows: use the full path
        relative = Path(site).resolve().as_posix()
    lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[source\]\s*(#.*)?$", line)), None)
    if start is None:
        lines += ["\n", "[source]\n"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    old = ""
    path_line = f'path = "{relative}"   # the course website, made by `wendao site new`\n'
    toc_line = "use_toc = true      # read the pages listed in myst.yml\n"
    found_path = found_toc = False
    for i in range(start + 1, end):
        if re.match(r"^\s*path\s*=", lines[i]):
            match = re.match(r'^\s*path\s*=\s*"([^"]*)"', lines[i])
            old = match.group(1) if match else ""
            lines[i] = path_line + (f'# path = "{old}"   # your original files\n' if old else "")
            found_path = True
        elif re.match(r"^\s*#?\s*use_toc\s*=", lines[i]):
            lines[i] = toc_line
            found_toc = True
        elif re.match(r"^\s*file_types\s*=", lines[i]):
            lines[i] = "# " + lines[i].lstrip()  # the website's pages are .md and .ipynb, whatever the originals were
    if not found_toc:
        after = next((i + 1 for i in range(start + 1, end) if lines[i].startswith(path_line)), start + 1)
        lines.insert(after if found_path else start + 1, toc_line)
    if not found_path:
        lines.insert(start + 1, path_line)
    config.write_text("".join(lines), encoding="utf-8")
    return old


def myst_command() -> list[str] | None:
    """How to run MyST here: the `myst` command, Jupyter Book 2, or `npx mystmd` (needs Node.js)."""
    if shutil.which("myst"):
        return [shutil.which("myst"), "build", "--html"]
    jupyter_book = shutil.which("jupyter-book")
    if jupyter_book:
        result = subprocess.run([jupyter_book, "--version"], capture_output=True, text=True, check=False)
        if re.search(r"(^|\D)2\.\d", result.stdout + result.stderr):
            return [jupyter_book, "build", "--html"]
    if shutil.which("npx"):
        return [shutil.which("npx"), "-y", "mystmd", "build", "--html"]
    return None


def build(site: Path, api: str = "") -> dict:
    """Build the website with MyST and add the chat widget. Returns {pages, html}."""
    from wendao.web.site_widget import install

    site = Path(site).expanduser().resolve()
    if not (site / "myst.yml").exists():
        raise SiteError(f"No myst.yml in {site}. Make the website first with:  wendao site new")
    command = myst_command()
    if command is None:
        raise SiteError(
            "Building the website needs MyST, which runs on Node.js. Install Node.js from https://nodejs.org "
            "(or `pip install jupyter-book`), then run `wendao site build` again."
        )
    result = subprocess.run(command, cwd=site, check=False)
    html = site / "_build" / "html"
    if result.returncode != 0 or not html.is_dir():
        raise SiteError("MyST could not build the website; see its messages above.")
    return {"pages": install(html, api=api)["pages"], "html": html}
