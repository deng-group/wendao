"""Add the Wendao chat widget to a built course website (MyST, Jupyter Book, Sphinx, or any static HTML).

`install(site)` copies the widget files into `<site>/_wendao/` and adds a stylesheet link and a script
tag to every HTML page, between marker comments so it can be updated or removed cleanly. It also
removes the older MLE4217 widget, if present, so a page never shows two.

Run it after each build of the website (for example as the last step of `make web`).
"""

from __future__ import annotations

import shutil
from html import escape
from pathlib import Path

from wendao import __version__
from wendao.web import STATIC_DIR

ASSET_DIR = "_wendao"
FILES = ("wendao-widget.js", "wendao-widget.css")
MARKERS = [
    ("<!-- Wendao widget: start -->", "<!-- Wendao widget: end -->"),
    ("<!-- MLE AI Agent Widget: start -->", "<!-- MLE AI Agent Widget: end -->"),  # the widget before Wendao
]


def strip_blocks(html: str) -> tuple[str, int, int]:
    """Remove widget blocks. Returns (html, blocks removed, of which from the older MLE widget)."""
    removed = legacy = 0
    for index, (start_marker, end_marker) in enumerate(MARKERS):
        while True:
            start = html.find(start_marker)
            end = html.find(end_marker, start + len(start_marker)) if start != -1 else -1
            if start == -1 or end == -1:
                break
            html = html[:start] + html[end + len(end_marker):]
            removed += 1
            legacy += index > 0
    return html, removed, legacy


def asset_prefix(page: Path, site: Path) -> str:
    depth = len(page.parent.relative_to(site).parts)
    return "/".join([".."] * depth + [ASSET_DIR]) if depth else ASSET_DIR


def pages(site: Path) -> list[Path]:
    return sorted(path for path in site.rglob("*.html") if ASSET_DIR not in path.relative_to(site).parts)


def install(site: Path, api: str = "") -> dict:
    """Add the widget to every page of the built site. `api` is the widget API address (default: the site itself)."""
    site = Path(site).expanduser().resolve()
    if not site.is_dir():
        raise FileNotFoundError(f"Website folder not found: {site}. Build the site first (for example `make web`).")
    html_pages = pages(site)
    if not html_pages:
        raise FileNotFoundError(f"No HTML pages in {site}. Point to the built site, for example _build/html.")
    target = site / ASSET_DIR
    target.mkdir(exist_ok=True)
    for name in FILES:
        shutil.copyfile(STATIC_DIR / "site_widget" / name, target / name)

    version = __version__
    api_attr = f' data-api="{escape(api.rstrip("/"), quote=True)}"' if api else ""
    legacy_removed = 0
    for page in html_pages:
        html, _, legacy = strip_blocks(page.read_text(encoding="utf-8", errors="replace"))
        legacy_removed += legacy
        prefix = asset_prefix(page, site)
        start, end = MARKERS[0]
        head = f'{start}\n<link rel="stylesheet" href="{prefix}/wendao-widget.css?v={version}" data-wendao-widget="style">\n{end}'
        body = f'{start}\n<script src="{prefix}/wendao-widget.js?v={version}" data-version="{version}"{api_attr} defer></script>\n{end}'
        html = html.replace("</head>", f"{head}\n</head>", 1) if "</head>" in html else f"{head}\n{html}"
        html = html.replace("</body>", f"{body}\n</body>", 1) if "</body>" in html else f"{html}\n{body}"
        page.write_text(html, encoding="utf-8")
    return {"pages": len(html_pages), "folder": target, "replaced_old_widget": legacy_removed > 0}


def remove(site: Path) -> int:
    """Take the widget out of every page and delete its files. Returns the number of pages changed."""
    site = Path(site).expanduser().resolve()
    changed = 0
    for page in pages(site):
        html, removed, _ = strip_blocks(page.read_text(encoding="utf-8", errors="replace"))
        if removed:
            page.write_text(html, encoding="utf-8")
            changed += 1
    shutil.rmtree(site / ASSET_DIR, ignore_errors=True)
    return changed
