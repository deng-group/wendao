"""Draft concepts and test questions with the course's language model, for the teacher to check.

`wendao suggest` reads the extracted notes (build/chunks.jsonl), asks the model chapter by chapter,
and writes drafts to suggestions/concepts.json and suggestions/questions.json. It never changes
concepts.json or questions.json: the teacher checks the drafts, deletes what they don't want, and
runs `wendao suggest --add` to copy the rest in.

Everything the model proposes is checked against the notes: a concept is kept only if one of its
names appears in them, and a question only if its key terms appear on the page it is about.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from wendao.graph import alias_pattern, normalized_text

CONCEPTS_PER_CHAPTER = 12
PAGES_PER_CHAPTER = 3
QUESTIONS_PER_PAGE = 2
CHAPTER_BUDGET = 20000  # characters of notes shown to the model per chapter
PAGE_BUDGET = 5000
REVIEW_FIELDS = {"mentions", "chapter", "search_finds_it"}  # help the teacher review; not copied by --add

FILLER = {"the", "and", "for", "with", "this", "that", "data", "example", "examples", "introduction", "method", "methods"}

SYSTEM = (
    "You help a teacher prepare an AI tutor for their course. Use only the course notes you are given. "
    "Answer with JSON only, no other text."
)


class SuggestError(RuntimeError):
    """A problem the teacher can fix; the message says how."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


def suggestions_dir(workspace) -> Path:
    return workspace.root / "suggestions"


def course_model():
    """The teacher's language model, as set up in .env / wendao.toml. Returns (provider, "name (model)")."""
    from wendao.rag.providers import default_model_name, default_provider_name, provider_from_name

    name = default_provider_name()
    if name == "dry_run":
        raise SuggestError(
            "`wendao suggest` uses your language model, and none is set up. Add an API key to .env "
            "or set [model] in wendao.toml, then check it with `wendao check`."
        )
    model = default_model_name(name)
    return provider_from_name(name, model=model), f"{name} ({model})" if model else name


# ---------- reading the notes ----------

def load_chapters(workspace) -> list[tuple[str, str, list[dict]]]:
    """(folder, name, chunks) for each chapter students see, in teaching order."""
    path = workspace.chunks_path
    if not path.exists():
        raise SuggestError("No extracted notes yet. Run `wendao build` (or `wendao extract`) first.")
    by_module: dict[str, list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunk = json.loads(line)
            by_module[chunk.get("module", "root")].append(chunk)
    settings = {chapter.folder: chapter for chapter in workspace.chapters}
    order = [chapter.folder for chapter in workspace.chapters if chapter.folder in by_module]
    order += sorted(module for module in by_module if module not in order)
    chapters = []
    for module in order:
        chapter = settings.get(module)
        if module == "root" or (chapter and chapter.show == "hidden"):
            continue  # course information (syllabus, calendar) or chapters the teacher hid
        chapters.append((module, chapter.name if chapter else module.replace("_", " ").title(), by_module[module]))
    return chapters


def excerpt(chunks: list[dict], budget: int) -> str:
    """The start of every chunk, so the model sees the whole chapter within the budget."""
    share = max(300, budget // max(len(chunks), 1))
    parts = []
    for chunk in chunks:
        text = chunk.get("content", "").strip()
        parts.append(text[:share])
    return "\n\n".join(parts)[:budget]


def ask_json(provider, prompt: str) -> list:
    """Ask the model and read a JSON list from its reply (it may wrap it in ```json fences)."""
    reply = provider.generate({"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], "evidence": []})
    text = reply.get("answer", "") or ""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)]


# ---------- concepts ----------

def concept_prompt(course: str, chapter: str, notes: str) -> str:
    return f"""Course: {course}
Chapter: {chapter}

Notes from this chapter:
<<<
{notes}
>>>

List the {CONCEPTS_PER_CHAPTER} most important concepts a student should learn in this chapter: specific terms
such as methods, laws, quantities, models, or tools. Leave out general words like "introduction", "example",
or "data". For each concept give:
- "label": the name as the notes write it
- "aliases": 1 to 4 lowercase ways the notes write it, including abbreviations

Return a JSON array, e.g. [{{"label": "Convex Hull", "aliases": ["convex hull"]}}]"""


def suggest_concepts(workspace, provider, chapters, existing: list[dict], say=print) -> tuple[list[dict], int]:
    """Concepts for each chapter, kept only if the notes use them. Returns (new concepts, how many you already had)."""
    all_text = normalized_text("\n".join(chunk.get("content", "") for _, _, chunks in chapters for chunk in chunks))
    known_ids = {item["id"] for item in existing}
    known_aliases = {normalized_text(alias) for item in existing for alias in item.get("aliases", [])}
    found: dict[str, dict] = {}
    already = set()
    for number, (folder, name, chunks) in enumerate(chapters, 1):
        say(f"  Concepts {number}/{len(chapters)}: {name} ...")
        chapter_text = normalized_text("\n".join(chunk.get("content", "") for chunk in chunks))
        for item in ask_json(provider, concept_prompt(workspace.course_name, name, excerpt(chunks, CHAPTER_BUDGET))):
            label = str(item.get("label", "")).strip()
            if not label or len(label) > 60:
                continue
            aliases = []
            for alias in [label, *(item.get("aliases") or [])]:
                alias = normalized_text(str(alias))
                if alias and alias not in aliases and len(alias) <= 60 and alias_pattern(alias).search(chapter_text):
                    aliases.append(alias)
            if not aliases:
                continue  # the notes never use it: probably made up
            concept_id = slug(label)
            if concept_id in known_ids or set(aliases) & known_aliases:
                already.add(concept_id)
                continue
            if concept_id in found:
                found[concept_id]["aliases"] = sorted(set(found[concept_id]["aliases"]) | set(aliases))
                continue
            found[concept_id] = {"id": concept_id, "label": label, "category": slug(folder), "aliases": aliases, "chapter": name}
    for concept in found.values():
        concept["mentions"] = sum(len(alias_pattern(alias).findall(all_text)) for alias in concept["aliases"])
    concepts = sorted(found.values(), key=lambda item: -item["mentions"])
    return concepts, len(already)


# ---------- questions ----------

def question_prompt(course: str, chapter: str, pages: list[tuple[str, str]]) -> str:
    shown = "\n\n".join(f"Page {path}:\n<<<\n{text}\n>>>" for path, text in pages)
    return f"""Course: {course}
Chapter: {chapter}

{shown}

Write {QUESTIONS_PER_PAGE} questions per page that a student of this course might ask, and that the page answers.
Mix definitions ("What is ...?"), explanations ("Why ...?", "How does ...?"), and comparisons. For each give:
- "question": the question, as a student would type it
- "page": the page it is about, exactly as written after "Page"
- "key_terms": 2 or 3 short terms from that page that a good answer must use

Return a JSON array, e.g. [{{"question": "What is a convex hull?", "page": "thermo.md", "key_terms": ["convex hull", "stable"]}}]"""


def suggest_questions(workspace, provider, chapters, existing: list[dict], say=print) -> list[dict]:
    known_ids = {item.get("id") for item in existing}
    known_queries = {normalized_text(item.get("query", "")) for item in existing}
    questions = []
    for number, (folder, name, chunks) in enumerate(chapters, 1):
        by_page: dict[str, list[dict]] = defaultdict(list)
        for chunk in chunks:
            by_page[chunk["file_path"]].append(chunk)
        # The pages with the most to say make the best test questions.
        pages = sorted(by_page, key=lambda page: -sum(len(chunk.get("content", "")) for chunk in by_page[page]))[:PAGES_PER_CHAPTER]
        if not pages:
            continue
        say(f"  Questions {number}/{len(chapters)}: {name} ...")
        texts = {page: excerpt(by_page[page], PAGE_BUDGET) for page in pages}
        full = {page: normalized_text("\n".join(chunk.get("content", "") for chunk in by_page[page])) for page in pages}
        for item in ask_json(provider, question_prompt(workspace.course_name, name, list(texts.items()))):
            query = " ".join(str(item.get("question", "")).split())
            page = str(item.get("page", "")).strip()
            if page not in full or not 10 <= len(query) <= 200 or normalized_text(query) in known_queries:
                continue
            terms = [str(term).strip() for term in item.get("key_terms") or [] if str(term).strip()]
            # Keep terms that are on the page and say something (not "the" or "data").
            terms = [term for term in terms if len(term) >= 3 and normalized_text(term) not in FILLER
                     and normalized_text(term) in full[page]][:3]
            if not terms:
                continue  # its key terms are not on the page: not a fair test
            question_id = slug(query)[:50]
            while question_id in known_ids:
                question_id += "-2"
            known_ids.add(question_id)
            known_queries.add(normalized_text(query))
            questions.append({
                "id": question_id,
                "query": query,
                "expected_status": "answerable",
                "expected_files": [page],
                "expected_terms": terms,
                "category": "concept",
                "notes": f"Drafted by wendao suggest from {name}.",
                "chapter": name,
            })
    return questions


def check_with_search(workspace, questions: list[dict]) -> int | None:
    """Mark which drafted questions Wendao's search already gets right. Returns how many, or None without an index."""
    try:
        from wendao.evaluate import evaluate_search_case
        from wendao.rag.pipeline import QueryPipeline

        pipeline = QueryPipeline.for_workspace(workspace)
    except Exception:  # noqa: BLE001 - no index yet: the drafts are still useful
        return None
    passed = 0
    for case in questions:
        results = pipeline.retriever.search(case["query"], top_k=pipeline.top_k)
        decision = pipeline.gate.decide(case["query"], results)
        case["search_finds_it"] = evaluate_search_case(case, results, decision, pipeline.retriever.normalize)["passed"]
        passed += case["search_finds_it"]
    return passed


# ---------- files ----------

def read_concepts(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("concepts", []) if path.exists() else []


def read_questions(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def add_to_workspace(workspace) -> dict:
    """Copy the (checked) drafts into concepts.json and questions.json, skipping what is already there."""
    folder = suggestions_dir(workspace)
    drafts_concepts = read_concepts(folder / "concepts.json")
    drafts_questions = read_questions(folder / "questions.json")
    if not drafts_concepts and not drafts_questions:
        raise SuggestError(f"No drafts in {folder}. Run `wendao suggest` first.")

    added = {"concepts": 0, "questions": 0}
    if drafts_concepts:
        concepts = [item for item in read_concepts(workspace.concepts_path) if item.get("id") != "example-concept"]
        ids = {item["id"] for item in concepts}
        for item in drafts_concepts:
            if item.get("id") and item["id"] not in ids and item.get("aliases"):
                concepts.append({key: value for key, value in item.items() if key not in REVIEW_FIELDS})
                ids.add(item["id"])
                added["concepts"] += 1
        write_json(workspace.concepts_path, {"version": 1, "concepts": concepts})
    if drafts_questions:
        questions = read_questions(workspace.questions_path)
        ids = {item.get("id") for item in questions}
        for item in drafts_questions:
            if item.get("id") and item["id"] not in ids and item.get("query"):
                questions.append({key: value for key, value in item.items() if key not in REVIEW_FIELDS})
                ids.add(item["id"])
                added["questions"] += 1
        write_json(workspace.questions_path, questions)
    return added
