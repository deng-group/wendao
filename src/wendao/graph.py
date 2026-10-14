"""Build and check the student-facing knowledge graph from course chunks.

The graph is built by rules, not by a language model, so the same chunks and concepts
always give the same graph. Chapters (top-level folders), topics (pages), and concepts
(from concepts.json) become nodes. Two nodes are linked only when the course materials
show them together.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from wendao.workspace import folder_title

# The graph.json format. 1.1 (Wendao 0.3.1): introductions for concepts and chapters, and course links.
# 1.2: a guided learning path ("path").
GRAPH_SCHEMA = "1.2"
# Key concepts the learning path introduces in each chapter.
PATH_CONCEPTS_PER_CHAPTER = 3


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def topic_id(module: str, section: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", f"{module}-{section}".lower()).strip("-")
    return f"topic:{cleaned}"


def chapter_id(module: str) -> str:
    return f"chapter:{module.lower()}"


def keyword_id(concept: str) -> str:
    return f"keyword:{concept}"


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("_", " ")).strip()


def alias_pattern(alias: str) -> re.Pattern[str]:
    normalized = normalized_text(alias)
    escaped = re.escape(normalized).replace(r"\ ", r"[\s\-/]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def detect_concepts(text: str, taxonomy: list[dict]) -> dict[str, int]:
    searchable = normalized_text(text)
    found = {}
    for concept in taxonomy:
        count = 0
        for pattern in concept["patterns"]:
            count += len(pattern.findall(searchable))
        if count:
            found[concept["id"]] = count
    return found


# ---------- short introductions, taken from the course text itself ----------

DEFINES = r"(?:is|are|was|refers to|describes|means|denotes|represents|is defined as|is called|stands for)"


def plain_sentences(text: str) -> list[str]:
    """Readable sentences from a Markdown page: no code, directives, math, tables, headings, or link syntax."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\$\$.*?\$\$", " ", text, flags=re.S)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", ":", "<", "!", "%", "+++", "---", "Temporal context:")):
            lines.append("")  # paragraph break
            continue
        lines.append(re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", stripped))
    text = "\n".join(lines)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links and images -> their text
    text = re.sub(r"\{[a-z-]+\}`([^`]*)`", r"\1", text)    # MyST roles
    text = re.sub(r"[*_`]{1,3}", "", text)
    sentences = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        sentences.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", paragraph) if part.strip())
    return sentences


def concept_summary(aliases: list[str], label: str, chunks: list[dict]) -> tuple[str, str]:
    """The sentence in the notes that best introduces a concept, and the page it is on ("" if none).

    A definition ("X is ...", "X refers to ...") counts most, then a sentence that starts with the concept, then a
    readable length. Sentences with leftover math or markup are passed over.
    """
    names = sorted({label.lower(), *(alias.lower() for alias in aliases)}, key=len, reverse=True)
    name_pattern = "|".join(re.escape(name).replace(r"\ ", r"[\s\-/]+") for name in names)
    mentions = re.compile(rf"(?<![a-z0-9])(?:{name_pattern})(?![a-z0-9])", re.IGNORECASE)
    defines = re.compile(rf"(?<![a-z0-9])(?:{name_pattern})(?![a-z0-9])[^.]{{0,40}}?\b{DEFINES}\b", re.IGNORECASE)
    starts = re.compile(rf"^(?:(?:a|an|the)\s+)?(?:{name_pattern})(?![a-z0-9])", re.IGNORECASE)
    best, best_score, best_file = "", 0.0, ""
    for order, chunk in enumerate(chunks):
        about_it = bool(mentions.search(str(chunk.get("title", ""))))
        for sentence in plain_sentences(str(chunk.get("content", ""))):
            if not mentions.search(sentence) or not 40 <= len(sentence) <= 320:
                continue
            score = 1.0
            definition = defines.search(sentence)
            # A definition counts most when the concept is the subject, near the start of the sentence.
            score += (4 if definition.start() < 45 else 1) if definition else 0
            score += 2 if starts.search(sentence) else 0
            score -= 2 if re.match(r"(?:it|this|these|they|that|such|here|in this)\b", sentence, re.IGNORECASE) else 0
            score += 1 if about_it else 0
            score += 0.5 if 60 <= len(sentence) <= 240 else 0
            score -= 3 if re.search(r"[$\\{}<>|=]", sentence) else 0
            score -= order * 1e-4  # earlier in the course wins a tie
            if score > best_score:
                best, best_score, best_file = sentence, score, chunk.get("file_path", "")
    return best, best_file


def chapter_intro(module_chunks: list[dict]) -> tuple[str, str]:
    """A chapter's name and opening paragraph, from its index page (README) if it has one."""
    index = [chunk for chunk in module_chunks if Path(chunk.get("file_path", "")).stem.lower() in {"index", "readme"}]
    if not index:
        return "", ""
    title = str(index[0].get("title", "")).strip()
    for sentence_list in (plain_sentences(str(chunk.get("content", ""))) for chunk in index):
        intro = " ".join(sentence_list[:2]).strip()
        if len(intro) >= 30 and not re.search(r"[$\\{}<>|]", intro):
            return title, intro[:320]
    return title, ""


def edge_key(source: str, target: str, edge_type: str) -> tuple[str, str, str]:
    if edge_type == "related" and source > target:
        source, target = target, source
    return source, target, edge_type


def top_symmetric_pairs(
    candidates: list[tuple[float, str, str, dict]],
    max_per_node: int,
) -> list[tuple[float, str, str, dict]]:
    chosen = []
    degree = Counter()
    for score, source, target, metadata in sorted(candidates, reverse=True):
        if degree[source] >= max_per_node or degree[target] >= max_per_node:
            continue
        chosen.append((score, source, target, metadata))
        degree[source] += 1
        degree[target] += 1
    return chosen


def learning_path(
    chapters: list[str], chapter_counts: dict[str, Counter], stop_concepts: set[str], per_chapter: int = PATH_CONCEPTS_PER_CHAPTER
) -> list[dict]:
    """A guided path through the course: each chapter in teaching order, then the key concepts it introduces.

    A concept is introduced in its main chapter, the one that mentions it most (the earlier one on a tie), not where it
    first comes up in passing (an overview chapter mentions almost everything). Each chapter shows its few most-used
    concepts. Very common concepts (`bridge_stop_concepts`) are left out. Steps name the other chapters that use a
    concept, so students see where it comes back.
    """
    main_chapter: dict[str, str] = {}
    for concept in sorted({concept for counts in chapter_counts.values() for concept in counts}):
        best = max(chapters, key=lambda module: (chapter_counts[module][concept], -chapters.index(module)))
        if chapter_counts[best][concept]:
            main_chapter[concept] = best
    steps = []
    for module in chapters:
        steps.append({"node": chapter_id(module), "chapter": chapter_id(module)})
        introduced = sorted(
            (concept for concept, home in main_chapter.items() if home == module and concept not in stop_concepts),
            key=lambda concept: (-chapter_counts[module][concept], concept),
        )[:per_chapter]
        for concept in introduced:
            also = [chapter_id(other) for other in chapters if other != module and chapter_counts[other][concept]]
            steps.append({"node": keyword_id(concept), "chapter": chapter_id(module), "also_in": also[:4]})
    return steps


def build_graph(
    chunks: list[dict],
    raw_taxonomy: dict,
    chapters: list | None = None,
    bridge_stop_concepts: set[str] | None = None,
    course: dict | None = None,
) -> dict:
    """Build the graph. `chapters` holds optional workspace Chapter settings (order, names, visibility)."""
    chapter_settings = {chapter.folder: chapter for chapter in (chapters or [])}
    chapter_order = [chapter.folder for chapter in (chapters or [])]
    stop_concepts = set(bridge_stop_concepts or ())

    def visibility_for(module: str) -> str:
        if module in chapter_settings:
            return chapter_settings[module].show
        # Loose files at the top of the course folder are course information, not a chapter.
        return "hidden" if module == "root" else "primary"

    taxonomy = []
    for item in raw_taxonomy["concepts"]:
        entry = dict(item)
        entry["patterns"] = [alias_pattern(alias) for alias in item["aliases"]]
        taxonomy.append(entry)
    taxonomy_by_id = {item["id"]: item for item in taxonomy}

    chunks_by_module: dict[str, list[dict]] = defaultdict(list)
    chunks_by_topic: dict[tuple[str, str], list[dict]] = defaultdict(list)
    chunk_concepts: dict[str, dict[str, int]] = {}
    concept_chunks: dict[str, list[str]] = defaultdict(list)
    concept_mentions = Counter()

    for chunk in chunks:
        module = chunk.get("module", "root")
        section = chunk.get("section", "unknown")
        chunks_by_module[module].append(chunk)
        chunks_by_topic[(module, section)].append(chunk)
        search_text = "\n".join(
            [str(chunk.get("title", "")), str(chunk.get("section", "")), str(chunk.get("content", ""))]
        )
        matches = detect_concepts(search_text, taxonomy)
        chunk_concepts[chunk["chunk_id"]] = matches
        for concept, count in matches.items():
            concept_chunks[concept].append(chunk["chunk_id"])
            concept_mentions[concept] += count

    active_concepts = {concept for concept, ids in concept_chunks.items() if ids}
    nodes = []
    edges = []
    edge_keys = set()

    ordered_modules = [module for module in chapter_order if module in chunks_by_module]
    ordered_modules.extend(module for module in chunks_by_module if module not in ordered_modules)  # notes order

    topic_concepts: dict[str, set[str]] = {}
    topic_evidence: dict[str, dict[str, list[str]]] = {}
    module_topics: dict[str, list[str]] = defaultdict(list)
    module_concepts: dict[str, set[str]] = defaultdict(set)
    module_concept_counts: dict[str, Counter] = {}

    for module_index, module in enumerate(ordered_modules):
        module_chunks = chunks_by_module[module]
        index_title, index_intro = chapter_intro(module_chunks)
        default_label = "Course Information" if module == "root" else (index_title or folder_title(module))
        setting = chapter_settings.get(module)
        label = setting.name if setting else default_label
        description = (setting.description if setting else "") or index_intro or f"Course material for {module.replace('_', ' ')}."
        section_keys = sorted(
            {(chunk.get("section", "unknown"), chunk.get("title") or chunk.get("section", "unknown")) for chunk in module_chunks},
            key=lambda item: (item[0] == "index", item[0]),
        )
        chapter_keyword_counts = Counter()
        for chunk in module_chunks:
            chapter_keyword_counts.update(chunk_concepts[chunk["chunk_id"]])
        chapter_keywords = sorted(chapter_keyword_counts)
        module_concepts[module].update(chapter_keywords)
        module_concept_counts[module] = chapter_keyword_counts

        nodes.append(
            {
                "id": chapter_id(module),
                "type": "chapter",
                "label": label,
                "description": description,
                "module": module,
                "order": module_index,
                "visibility": visibility_for(module),
                "chunk_ids": [chunk["chunk_id"] for chunk in module_chunks],
                "source_files": sorted({chunk["file_path"] for chunk in module_chunks}),
                "keyword_ids": [keyword_id(value) for value in chapter_keywords],
                "summary": (setting.description if setting else "") or index_intro,
                "summary_file": next((path for path in sorted({chunk["file_path"] for chunk in module_chunks})
                                      if Path(path).stem.lower() in {"index", "readme"}), ""),
            }
        )

        for section, title in section_keys:
            section_chunks = chunks_by_topic[(module, section)]
            node_id = topic_id(module, section)
            module_topics[module].append(node_id)
            evidence: dict[str, list[str]] = defaultdict(list)
            keyword_counts = Counter()
            for chunk in section_chunks:
                keyword_counts.update(chunk_concepts[chunk["chunk_id"]])
                for concept in chunk_concepts[chunk["chunk_id"]]:
                    evidence[concept].append(chunk["chunk_id"])
            concepts = set(keyword_counts)
            topic_concepts[node_id] = concepts
            topic_evidence[node_id] = evidence
            topic_visibility = visibility_for(module)
            if section == "index" and topic_visibility == "primary":
                topic_visibility = "secondary"
            nodes.append(
                {
                    "id": node_id,
                    "type": "topic",
                    "label": str(title).strip(),
                    "description": f"{str(title).strip()} in {label}.",
                    "module": module,
                    "section": section,
                    "chapter_id": chapter_id(module),
                    "visibility": topic_visibility,
                    "chunk_ids": [chunk["chunk_id"] for chunk in section_chunks],
                    "source_files": sorted({chunk["file_path"] for chunk in section_chunks}),
                    "keyword_ids": [keyword_id(value) for value in sorted(concepts)],
                }
            )
            edges.append(
                {
                    "id": f"contains:{chapter_id(module)}:{node_id}",
                    "source": chapter_id(module),
                    "target": node_id,
                    "type": "contains",
                    "label": "contains topic",
                    "weight": 1.0,
                    "evidence": {"chunk_ids": [chunk["chunk_id"] for chunk in section_chunks]},
                }
            )

            for concept, count in sorted(keyword_counts.items()):
                target = keyword_id(concept)
                edges.append(
                    {
                        "id": f"mentions:{node_id}:{target}",
                        "source": node_id,
                        "target": target,
                        "type": "mentions",
                        "label": "covers concept",
                        "weight": round(min(1.0, 0.45 + math.log1p(count) / 4), 4),
                        "evidence": {"chunk_ids": sorted(set(evidence[concept]))},
                        "mention_count": count,
                    }
                )

        for concept, count in sorted(chapter_keyword_counts.items()):
            target = keyword_id(concept)
            supporting_topics = [
                node_id for node_id in module_topics[module] if concept in topic_concepts[node_id]
            ]
            edges.append(
                {
                    "id": f"covers:{chapter_id(module)}:{target}",
                    "source": chapter_id(module),
                    "target": target,
                    "type": "covers",
                    "label": "covers concept",
                    "weight": round(min(1.0, 0.4 + 0.12 * len(supporting_topics) + math.log1p(count) / 10), 4),
                    "evidence": {
                        "chunk_ids": sorted(
                            chunk["chunk_id"]
                            for chunk in module_chunks
                            if concept in chunk_concepts[chunk["chunk_id"]]
                        ),
                        "topic_ids": supporting_topics,
                    },
                    "mention_count": count,
                }
            )

    for concept in sorted(active_concepts):
        item = taxonomy_by_id[concept]
        linked_topics = sorted(node_id for node_id, concepts in topic_concepts.items() if concept in concepts)
        linked_chapters = sorted(
            chapter_id(module) for module, concepts in module_concepts.items() if concept in concepts
        )
        concept_chunk_ids = set(concept_chunks[concept])
        summary, summary_file = concept_summary(
            item["aliases"], item["label"], [chunk for chunk in chunks if chunk["chunk_id"] in concept_chunk_ids]
        )
        nodes.append(
            {
                "id": keyword_id(concept),
                "type": "keyword",
                "label": item["label"],
                "description": f"A course concept in the {item['category'].replace('-', ' ')} group.",
                "summary": summary,
                "summary_file": summary_file,
                "category": item["category"],
                "aliases": item["aliases"],
                "visibility": "primary",
                "chunk_ids": sorted(set(concept_chunks[concept])),
                "topic_ids": linked_topics,
                "chapter_ids": linked_chapters,
                "mention_count": concept_mentions[concept],
            }
        )

    # Course sequence is descriptive, not a claim that every chapter is a prerequisite.
    primary_modules = [module for module in ordered_modules if visibility_for(module) == "primary"]
    for left, right in zip(primary_modules, primary_modules[1:]):
        edges.append(
            {
                "id": f"sequence:{chapter_id(left)}:{chapter_id(right)}",
                "source": chapter_id(left),
                "target": chapter_id(right),
                "type": "sequence",
                "label": "followed by",
                "weight": 0.5,
                "evidence": {"source": "course chapter order"},
            }
        )

    # Keyword relationships are intentionally undirected and semantically
    # neutral. The graph records only that two concepts share course evidence;
    # it does not guess the nature or direction of the relationship.
    keyword_candidates = []
    active_list = sorted(active_concepts)
    concept_topic_sets = {
        concept: {node_id for node_id, concepts in topic_concepts.items() if concept in concepts}
        for concept in active_list
    }
    concept_chunk_sets = {concept: set(concept_chunks[concept]) for concept in active_list}
    for index, left in enumerate(active_list):
        for right in active_list[index + 1 :]:
            shared_topics = concept_topic_sets[left] & concept_topic_sets[right]
            shared_chunks = concept_chunk_sets[left] & concept_chunk_sets[right]
            if not shared_topics or not shared_chunks:
                continue
            denominator = math.sqrt(len(concept_topic_sets[left]) * len(concept_topic_sets[right]))
            score = len(shared_topics) / denominator if denominator else 0.0
            if score < 0.10:
                continue
            keyword_candidates.append(
                (
                    score,
                    keyword_id(left),
                    keyword_id(right),
                    {
                        "chunk_ids": sorted(shared_chunks)[:8],
                        "topic_ids": sorted(shared_topics),
                    },
                )
            )
    for score, source, target, evidence in sorted(keyword_candidates, reverse=True):
        edges.append(
            {
                "id": f"related:{source}:{target}",
                "source": source,
                "target": target,
                "type": "related",
                "label": "related",
                "basis": "shared_course_evidence",
                "weight": round(score, 4),
                "evidence": evidence,
            }
        )

    # Cross-chapter topic bridges are based on at least two shared curated concepts.
    topic_candidates = []
    topic_ids = sorted(topic_concepts)
    topic_modules = {node_id: node_id.split(":", 1)[1].split("-", 1)[0] for node_id in topic_ids}
    # Use the actual module mapping because module IDs can contain underscores.
    for module, ids in module_topics.items():
        for node_id in ids:
            topic_modules[node_id] = module
    for index, left in enumerate(topic_ids):
        if visibility_for(topic_modules[left]) != "primary":
            continue
        for right in topic_ids[index + 1 :]:
            if topic_modules[left] == topic_modules[right] or visibility_for(topic_modules[right]) != "primary":
                continue
            shared = (topic_concepts[left] & topic_concepts[right]) - stop_concepts
            if len(shared) < 2:
                continue
            union = topic_concepts[left] | topic_concepts[right]
            score = len(shared) / max(len(union), 1)
            if score < 0.12:
                continue
            topic_candidates.append(
                (
                    score,
                    left,
                    right,
                    {"shared_keyword_ids": [keyword_id(value) for value in sorted(shared)]},
                )
            )
    for score, source, target, evidence in top_symmetric_pairs(topic_candidates, max_per_node=3):
        edges.append(
            {
                "id": f"related:{source}:{target}",
                "source": source,
                "target": target,
                "type": "related",
                "label": "related",
                "basis": "shared_keywords",
                "weight": round(score, 4),
                "evidence": evidence,
            }
        )

    # Chapter bridges summarize the concepts that span two course chapters.
    chapter_candidates = []
    primary_chapters = [module for module in ordered_modules if visibility_for(module) == "primary"]
    for index, left in enumerate(primary_chapters):
        for right in primary_chapters[index + 1 :]:
            shared = (module_concepts[left] & module_concepts[right]) - stop_concepts
            if len(shared) < 3:
                continue
            union = module_concepts[left] | module_concepts[right]
            score = len(shared) / max(len(union), 1)
            chapter_candidates.append(
                (
                    score,
                    chapter_id(left),
                    chapter_id(right),
                    {"shared_keyword_ids": [keyword_id(value) for value in sorted(shared)]},
                )
            )
    for score, source, target, evidence in top_symmetric_pairs(chapter_candidates, max_per_node=3):
        edges.append(
            {
                "id": f"related:{source}:{target}",
                "source": source,
                "target": target,
                "type": "related",
                "label": "related",
                "basis": "shared_keywords",
                "weight": round(score, 4),
                "evidence": evidence,
            }
        )

    # Remove accidental duplicate edges while keeping deterministic ordering.
    unique_edges = []
    for edge in edges:
        key = edge_key(edge["source"], edge["target"], edge["type"])
        if key in edge_keys:
            continue
        edge_keys.add(key)
        unique_edges.append(edge)

    nodes.sort(key=lambda node: (node["type"], node["id"]))
    unique_edges.sort(key=lambda edge: (edge["type"], edge["source"], edge["target"]))
    node_counts = Counter(node["type"] for node in nodes)
    edge_counts = Counter(edge["type"] for edge in unique_edges)
    cross_chapter_topics = sum(
        1
        for edge in unique_edges
        if edge["type"] == "related" and edge["source"].startswith("topic:")
    )

    return {
        "schema_version": GRAPH_SCHEMA,
        "course": course or {},
        "source": {
            "chunks": "build/chunks.jsonl",
            "taxonomy": "concepts.json",
            "taxonomy_version": raw_taxonomy.get("version", 1),
        },
        "display_defaults": {
            "initial_node_types": ["chapter"],
            "initial_visibility": ["primary"],
            "expand_on_select": ["topic", "keyword"],
            "hidden_edge_types": ["covers", "mentions", "related"],
        },
        "stats": {
            "nodes": len(nodes),
            "edges": len(unique_edges),
            "node_types": dict(sorted(node_counts.items())),
            "edge_types": dict(sorted(edge_counts.items())),
            "cross_chapter_topic_bridges": cross_chapter_topics,
        },
        "nodes": nodes,
        "edges": unique_edges,
        "path": learning_path(primary_modules, module_concept_counts, stop_concepts),
    }


def validate(graph: dict) -> list[str]:
    errors = []
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_ids = [node.get("id") for node in nodes]
    edge_ids = [edge.get("id") for edge in edges]
    known_nodes = set(node_ids)

    duplicate_nodes = [value for value, count in Counter(node_ids).items() if count > 1]
    duplicate_edges = [value for value, count in Counter(edge_ids).items() if count > 1]
    if duplicate_nodes:
        errors.append(f"Duplicate node IDs: {duplicate_nodes[:10]}")
    if duplicate_edges:
        errors.append(f"Duplicate edge IDs: {duplicate_edges[:10]}")

    for edge in edges:
        if edge.get("source") not in known_nodes:
            errors.append(f"Unknown edge source: {edge.get('id')} -> {edge.get('source')}")
        if edge.get("target") not in known_nodes:
            errors.append(f"Unknown edge target: {edge.get('id')} -> {edge.get('target')}")
        if edge.get("source") == edge.get("target"):
            errors.append(f"Self-edge: {edge.get('id')}")
        if not edge.get("evidence"):
            errors.append(f"Ungrounded edge: {edge.get('id')}")

    incident = Counter()
    for edge in edges:
        incident[edge.get("source")] += 1
        incident[edge.get("target")] += 1
    for node in nodes:
        if node.get("type") == "keyword" and not incident[node["id"]]:
            errors.append(f"Orphan keyword: {node['id']}")
        if node.get("type") in {"chapter", "topic", "keyword"} and not node.get("chunk_ids"):
            errors.append(f"Node has no course chunks: {node['id']}")

    forbidden_semantic_fields = {"predicate", "review_status"}
    for edge in edges:
        if forbidden_semantic_fields & edge.keys():
            errors.append(f"Relationship assigns semantic meaning: {edge.get('id')}")

    return errors


def graph_inputs(workspace) -> str:
    """What the graph is made from: the chunks, concepts.json, the chapter and graph settings, the course details, and
    the Wendao version. The same inputs give the same graph, so `wendao build` can keep it."""
    from wendao import __version__

    digest = hashlib.sha256()
    for path in (workspace.chunks_path, workspace.concepts_path):
        digest.update(path.read_bytes() if path.exists() else b"")
        digest.update(b"\0")
    settings = {
        "wendao": __version__, "schema": GRAPH_SCHEMA,
        "chapters": [[chapter.folder, chapter.name, chapter.description, chapter.show] for chapter in workspace.chapters],
        "stop": sorted(workspace.bridge_stop_concepts),
        "course": [workspace.course_code, workspace.course_name, workspace.website],
    }
    digest.update(json.dumps(settings, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:20]


def graph_up_to_date(workspace) -> bool:
    try:
        graph = json.loads(workspace.graph_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return graph.get("built_from") == graph_inputs(workspace)


def build(workspace) -> dict:
    """Build build/graph.json for a workspace and check it. Returns the graph."""
    chunks_path = workspace.require(workspace.chunks_path, "Run `wendao extract` (or `wendao build`) first.")
    concepts_path = workspace.require(workspace.concepts_path, "List your course concepts there; see `wendao init`.")
    taxonomy = json.loads(concepts_path.read_text(encoding="utf-8"))
    course = {"code": workspace.course_code, "title": workspace.course_name, "website": workspace.website}
    graph = build_graph(
        load_jsonl(chunks_path),
        taxonomy,
        chapters=workspace.chapters,
        bridge_stop_concepts=set(workspace.bridge_stop_concepts),
        course=course,
    )
    graph["built_from"] = graph_inputs(workspace)
    errors = validate(graph)
    if errors:
        preview = "\n".join(f"  - {error}" for error in errors[:20])
        raise ValueError(f"The knowledge graph has {len(errors)} problem(s):\n{preview}")
    workspace.graph_path.parent.mkdir(parents=True, exist_ok=True)
    workspace.graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return graph
