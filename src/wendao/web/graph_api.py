"""Small views of the knowledge graph for the course-website widget.

The widget shows the part of the graph around the page a student is reading. Two lookups:

- `page(path)`: which topic node a course-website address (like `/structures/crystal-structure/`)
  belongs to, using the same address rules as source links.
- `neighborhood(node_id)`: a node and its closest neighbours, small enough to draw in the chat window.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote


def page_slug(file_path: str) -> str | None:
    """The website address (without the site's base) of a Markdown/notebook page, or None for other files.

    MyST publishes `structures/crystal_structure.ipynb` as `structures/crystal-structure`, and an
    `index.md` as its folder. Matches `course_source_url` in explorer.py.
    """
    path = re.sub(r"^(?:\./)+", "", str(file_path or "").strip().replace("\\", "/")).lstrip("/")
    if not re.search(r"\.(?:md|ipynb|myst|rst)$", path, flags=re.IGNORECASE):
        return None
    path = re.sub(r"\.(?:md|ipynb|myst|rst)$", "", path, flags=re.IGNORECASE)
    if path.lower() in {"index", "readme"}:
        return ""
    if path.lower().endswith(("/index", "/readme")):
        path = path.rsplit("/", 1)[0]
    return "/".join(part.replace("_", "-").lower() for part in path.split("/") if part)


def normalize_address(address: str) -> str:
    """Turn a browser path like `/book/structures/crystal-structure/index.html` into `book/structures/crystal-structure`."""
    path = unquote(re.sub(r"[?#].*$", "", str(address or ""))).strip("/")  # "/unit%201/" → "unit 1"
    path = re.sub(r"(?:^|/)index\.html?$", "", path)
    path = re.sub(r"\.html?$", "", path)
    return path.lower()


class GraphView:
    # Every chapter a concept appears in is shown (courses have few chapters); concepts and pages are capped.
    LIMITS = {"keyword": 8, "topic": 6, "chapter": 16}
    MAX_OTHER_LINKS = 30

    def __init__(self, graph: dict):
        self.nodes = {node["id"]: node for node in graph.get("nodes", [])}
        self.edges = graph.get("edges", [])
        self.adjacent: dict[str, list[tuple[str, dict]]] = {}
        for edge in self.edges:
            self.adjacent.setdefault(edge["source"], []).append((edge["target"], edge))
            self.adjacent.setdefault(edge["target"], []).append((edge["source"], edge))
        # website address of each page → the topic node for it
        self.pages: dict[str, str] = {}
        for node in self.nodes.values():
            if node.get("type") != "topic":
                continue
            for file_path in node.get("source_files", []):
                slug = page_slug(file_path)
                if slug is not None and slug not in self.pages:
                    self.pages[slug] = node["id"]

    def public(self, node: dict, center: bool = False) -> dict:
        item = {"id": node["id"], "type": node["type"], "label": node.get("label", node["id"])}
        if node.get("type") == "topic" and node.get("source_files"):
            item["file_path"] = node["source_files"][0]
        if center and node.get("description"):
            item["description"] = node["description"]
        return item

    def page(self, address: str, base: str = "") -> dict | None:
        """The topic node for a browser address. Works when the site is hosted under a sub-folder.

        `base` is the site's own folder (from `[course] website`, e.g. "book"), so "/book/" is the home page.
        """
        path = normalize_address(address)
        base = normalize_address(base)
        if base and (path == base or path.startswith(base + "/")):
            path = path[len(base):].strip("/")
        best = None
        for slug, node_id in self.pages.items():
            if slug == path or (slug and path.endswith("/" + slug)) or (not slug and not path):
                if best is None or len(slug) > len(best[0]):
                    best = (slug, node_id)
        return self.public(self.nodes[best[1]], center=True) if best else None

    def neighborhood(self, node_id: str) -> dict | None:
        """The node, its strongest neighbours by type, and the links among them."""
        center = self.nodes.get(node_id)
        if center is None:
            return None
        ranked: dict[str, list[tuple[float, str]]] = {"keyword": [], "topic": [], "chapter": []}
        for other_id, edge in self.adjacent.get(node_id, []):
            other = self.nodes.get(other_id)
            if not other or other.get("visibility") == "hidden" or other["type"] not in ranked:
                continue
            weight = float(edge.get("weight", 0.5)) + 0.01 * float(other.get("mention_count", 0)) ** 0.5
            ranked[other["type"]].append((weight, other_id))
        chosen = []
        for kind, items in ranked.items():
            seen = set()
            for _, other_id in sorted(items, reverse=True):
                if other_id not in seen:
                    seen.add(other_id)
                    chosen.append(other_id)
                if len(seen) >= self.LIMITS[kind]:
                    break
        included = {node_id, *chosen}
        center_links, other_links = [], []
        seen_pairs = set()
        for source in included:
            for target, edge in self.adjacent.get(source, []):
                pair = tuple(sorted((source, target)))
                if target in included and pair not in seen_pairs and edge.get("type") != "sequence":
                    seen_pairs.add(pair)
                    link = {"source": pair[0], "target": pair[1], "type": edge.get("type", "related"),
                            "weight": round(float(edge.get("weight", 0.5)), 3)}
                    (center_links if node_id in pair else other_links).append(link)
        # Every link to the centre, then links to chapters (where each concept belongs), then the strongest of the rest:
        # a small window can't show them all.
        types = {item: self.nodes[item]["type"] for item in included}
        other_links.sort(key=lambda link: ("chapter" not in (types[link["source"]], types[link["target"]]), -link["weight"]))
        links = center_links + other_links[: self.MAX_OTHER_LINKS]
        counts = Counter(self.nodes[other]["type"] for other in chosen)
        return {
            "center": self.public(center, center=True),
            "nodes": [self.public(self.nodes[other]) for other in chosen],
            "links": links,
            "counts": dict(counts),
        }


class GraphFile:
    """build/graph.json for a running server, read again when `wendao build` rewrites it (no restart needed)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._stamp = None
        self._graph: dict | None = None
        self._view: GraphView | None = None

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            self._stamp, self._graph, self._view = None, None, None
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._stamp:
            graph = json.loads(self.path.read_text(encoding="utf-8"))
            self._graph, self._view, self._stamp = graph, GraphView(graph), stamp

    @property
    def graph(self) -> dict | None:
        self._refresh()
        return self._graph

    @property
    def view(self) -> GraphView | None:
        self._refresh()
        return self._view


NODE_FIELDS = ("id", "type", "label", "visibility", "order", "chapter_id", "chapter_ids", "aliases", "mention_count")
EDGE_FIELDS = ("source", "target", "type", "weight")


def browser_graph(graph: dict, link_for=None) -> dict:
    """What the graph page needs, and nothing else (no evidence text or chunk ids): about a fifth of graph.json.

    `link_for(file_path)` gives a page node its address on the course website, for an "Open page" link.
    """
    nodes = []
    for node in graph.get("nodes", []):
        item = {key: node[key] for key in NODE_FIELDS if key in node}
        if link_for and node.get("type") == "topic" and node.get("source_files"):
            url = link_for(node["source_files"][0])
            if url:
                item["url"] = url
        nodes.append(item)
    edges = [{key: edge[key] for key in EDGE_FIELDS if key in edge} for edge in graph.get("edges", [])]
    return {"nodes": nodes, "edges": edges}
