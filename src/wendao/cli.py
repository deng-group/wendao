"""The `wendao` command. Run `wendao --help` to see everything it can do."""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wendao import __version__
from wendao import workspace as workspace_module
from wendao.workspace import CONFIG_NAME, Workspace, WorkspaceError

STARTER_DIR = Path(__file__).resolve().parent / "starter"


def say(message: str = "") -> None:
    print(message, flush=True)


def load_workspace(args: argparse.Namespace) -> Workspace:
    workspace = workspace_module.load(args.workspace)
    workspace.apply_model_settings()
    return workspace


def relative(workspace: Workspace, path: Path) -> str:
    try:
        return str(path.relative_to(workspace.root))
    except ValueError:
        return str(path)


# init -----------------------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.source:
        source = Path(args.source).expanduser().resolve()
        try:
            source_setting = source.relative_to(root).as_posix()
        except ValueError:
            # Forward slashes work in wendao.toml on every OS (backslashes would be escapes in TOML).
            source_setting = Path(_relpath(source, root)).as_posix()
    else:
        source_setting = "notes"
        (root / "notes").mkdir(exist_ok=True)

    files = {
        CONFIG_NAME: (STARTER_DIR / "wendao.toml").read_text(encoding="utf-8")
        .replace("{name}", root.name.replace("_", " ").replace("-", " ").title())
        .replace("{source}", source_setting),
        "concepts.json": (STARTER_DIR / "concepts.json").read_text(encoding="utf-8"),
        "questions.json": (STARTER_DIR / "questions.json").read_text(encoding="utf-8"),
        ".env": (STARTER_DIR / "env.example").read_text(encoding="utf-8"),
        ".gitignore": (STARTER_DIR / "gitignore").read_text(encoding="utf-8"),
    }
    for name, content in files.items():
        path = root / name
        if path.exists():
            say(f"  kept     {name} (already exists)")
            continue
        path.write_text(content, encoding="utf-8")
        say(f"  created  {name}")

    say()
    say(f"Workspace ready: {root}")
    say("Next steps:")
    step = 1
    if root != Path.cwd():
        say(f"  {step}. cd {_relpath(root, Path.cwd())}")
        step += 1
    if not args.source:
        say(f"  {step}. Put your lecture notes (Markdown pages, Jupyter notebooks) in notes/")
        step += 1
    say(f"  {step}. Edit wendao.toml (course name, website) and concepts.json (concepts for the graph)")
    say(f"  {step + 1}. Add your API key to .env, then run: wendao build")
    say(f"  {step + 2}. No concept list yet? wendao build --concepts lets your AI model find them in your notes")


def _relpath(path: Path, start: Path) -> str:
    """Relative path when possible; the full path when there is none (on Windows, across drives like C: and D:)."""
    import os

    try:
        return os.path.relpath(path, start)
    except ValueError:
        return str(path)


# build steps ----------------------------------------------------------------------------------

TEACHER_MODULES = {"nbformat": "nbformat", "pypdf": "pypdf", "docx": "python-docx", "pptx": "python-pptx", "yaml": "pyyaml"}


def require_teacher_tools() -> None:
    """Building a course needs the note readers; say so plainly if this Python lacks them (a broken install)."""
    import importlib.util

    missing = [package for module, package in TEACHER_MODULES.items() if importlib.util.find_spec(module) is None]
    if missing:
        # Name this Python: a common mix-up is installing with one `pip` and running another `wendao`.
        raise RuntimeError(
            "Building a course needs packages that are missing for the Python that runs Wendao "
            f"({sys.executable}; missing: {', '.join(missing)}).\n"
            f"Install them there with:  \"{sys.executable}\" -m pip install --upgrade wendao"
        )



def step_extract(workspace: Workspace) -> None:
    from wendao.ingest import extract

    say(f"Extracting notes from {relative(workspace, workspace.require_source())} ...")
    summary = extract(workspace)
    term = f" Term: {summary['term']}." if summary["term"] else ""
    types = f" ({', '.join(summary['types'])})" if summary["types"] else ""
    say(f"  {summary['files']} files{types} → {summary['chunks']} chunks in {relative(workspace, workspace.chunks_path)}.{term}")
    for warning in summary["warnings"]:
        say(f"  Warning: {warning}")
    if not summary["chunks"]:
        raise RuntimeError(
            "No text was found in your notes. Supported files: Markdown, Jupyter notebooks, PDF, PowerPoint (.pptx), "
            "Word (.docx), LaTeX (.tex), HTML, and plain text."
        )


def step_graph(workspace: Workspace) -> None:
    from wendao.graph import build

    say("Building the knowledge graph ...")
    graph = build(workspace)
    types = graph["stats"]["node_types"]
    say(
        f"  {types.get('chapter', 0)} chapters, {types.get('topic', 0)} topics, {types.get('keyword', 0)} concepts, "
        f"{graph['stats']['edges']} links → {relative(workspace, workspace.graph_path)}"
    )
    if not types.get("keyword"):
        say("  No concepts yet, so the graph shows only chapters and pages. Set up an AI model (see `wendao doctor`)")
        say("  and run `wendao build` again to find them, or list them yourself in concepts.json.")


def needs_concepts(workspace: Workspace) -> bool:
    """A first build with a model set up but no concepts yet: find them without being asked."""
    from wendao import suggest
    from wendao.rag.providers import default_provider_name

    have = [item for item in suggest.read_concepts(workspace.concepts_path) if item.get("id") != "example-concept"]
    return not have and default_provider_name() != "dry_run"


def step_concepts(workspace: Workspace) -> None:
    """Let the course's model find concepts in each chapter and add them to concepts.json."""
    from wendao import suggest

    provider, label = suggest.course_model()
    chapters = suggest.load_chapters(workspace)
    if not chapters:
        say("No chapters to read, so no concepts to find.")
        return
    say(f"Finding concepts in {len(chapters)} chapters with {label} (one request per chapter) ...")
    existing = suggest.read_concepts(workspace.concepts_path)
    concepts, already = suggest.suggest_concepts(workspace, provider, chapters, existing, say=lambda *_: None)
    backup = suggest.add_concepts(workspace, concepts)
    say(f"  Added {len(concepts)} concepts to concepts.json" + (f"; {already} more were already there." if already else "."))
    if concepts:
        names = ", ".join(item["label"] for item in concepts[:10])
        say(f"  {names}{', ...' if len(concepts) > 10 else ''}")
        say("  Each is used in your notes. Delete any you don't want from concepts.json, then run `wendao graph`.")
    if backup:
        say(f"  The list from before is in {relative(workspace, backup)}.")


def step_index(workspace: Workspace, rebuild: bool = True) -> None:
    from wendao.rag.pipeline import QueryPipeline

    say("Building the search index (the first run downloads the search model) ...")
    pipeline = QueryPipeline.for_workspace(workspace, rebuild_index=rebuild)
    say(f"  {len(pipeline.retriever.chunks)} chunks indexed with {pipeline.retriever.embedding.description} → {relative(workspace, workspace.index_dir)}")


def cmd_build(args: argparse.Namespace) -> None:
    require_teacher_tools()
    workspace = load_workspace(args)
    started = time.monotonic()
    step_extract(workspace)
    if args.concepts or (not args.no_concepts and needs_concepts(workspace)):
        step_concepts(workspace)
    step_graph(workspace)
    step_index(workspace, rebuild=False)
    say(f"Done in {time.monotonic() - started:.0f}s. Try: wendao ask \"<a question about your course>\"")
    say(RESTART_NOTE)


# A running server shows a new graph by itself, but answers use the notes it loaded when it started.
RESTART_NOTE = "If `wendao serve` is running, restart it (Ctrl+C, then start it again) so answers use the new notes."


def cmd_extract(args: argparse.Namespace) -> None:
    require_teacher_tools()
    step_extract(load_workspace(args))


def cmd_graph(args: argparse.Namespace) -> None:
    step_graph(load_workspace(args))  # a running `wendao serve` picks up the new graph by itself


def cmd_index(args: argparse.Namespace) -> None:
    require_teacher_tools()
    step_index(load_workspace(args), rebuild=True)


# ask ------------------------------------------------------------------------------------------


def cmd_ask(args: argparse.Namespace) -> None:
    from wendao.rag.answer import AnswerGenerator
    from wendao.rag.pipeline import QueryPipeline
    from wendao.rag.prompts import PromptBuilder
    from wendao.rag.providers import default_model_name, default_provider_name, provider_from_name

    workspace = load_workspace(args)
    question = " ".join(args.question)
    pipeline = QueryPipeline.for_workspace(workspace, top_k=args.top_k)

    if args.search_only:
        result = pipeline.ask(question)
        if args.json:
            say(json.dumps(result, indent=2, ensure_ascii=False))
            return
        say(f"Decision: {result['status']} ({result['reason']})")
        if result["needs_temporal_context"] and result["temporal_context"]:
            say(f"Term: {result['temporal_context']}")
        say()
        for rank, item in enumerate(result["evidence"], start=1):
            where = f", {item['location']}" if item.get("location") else ""
            say(f"{rank}. {item['file_path']}{where}  score {item['score']:.2f}  (keyword {item['bm25_score']:.2f}, meaning {item['embedding_score']:.2f})")
            say(f"   {item['title']}")
        return

    provider_name = args.provider or default_provider_name()
    if provider_name == "dry_run" and not args.provider:
        raise RuntimeError(
            "No language model is configured, so there is nobody to write the answer.\n"
            "Add an API key to .env (see `wendao doctor`), or use --search-only to see what search finds."
        )
    model = args.model or default_model_name(provider_name)
    generator = AnswerGenerator(
        pipeline=pipeline,
        prompt_builder=PromptBuilder(course_name=workspace.display_name),
        provider=provider_from_name(provider_name, model=model),
        course_name=workspace.display_name,
    )
    result = generator.answer(question)
    if args.json:
        result.pop("raw_response", None)
        if not args.show_prompt:
            result.pop("prompt_package", None)
        say(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if args.show_prompt:
        say(result["prompt_package"]["final_prompt"])
        say("\n" + "=" * 72 + "\n")
    say(result["answer"])
    if result["sources"]:
        say("\nSources:")
        for source in result["sources"]:
            where = f", {source['location']}" if source.get("location") else ""
            say(f"  - {source['title']} ({source['file_path']}{where})")
    model_note = f" / {result['model']}" if result.get("model") else ""
    say(f"\n[{result['status']} · {result['provider']}{model_note}]")


# check ----------------------------------------------------------------------------------------


def graph_schema(workspace: Workspace) -> str:
    """The format version written in build/graph.json ("" if unreadable)."""
    try:
        return str(json.loads(workspace.graph_path.read_text(encoding="utf-8")).get("schema_version", ""))
    except (OSError, ValueError):
        return ""


KEY_NAMES = {"anthropic": "ANTHROPIC_AUTH_TOKEN", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}


def model_fix(provider: str, error: str) -> str:
    """Plain advice for a model that doesn't answer, guessed from the error text."""
    text = error.lower()
    key = KEY_NAMES.get(provider, "the API key")
    if provider == "dry_run":
        return (
            "Choose a model under [model] in wendao.toml and put its API key in .env, for example:\n"
            '  wendao.toml:  [model]\n               provider = "openai"\n               model = "gpt-4.1-mini"\n'
            "  .env:        OPENAI_API_KEY=sk-..."
        )
    if any(word in text for word in ("401", "403", "unauthor", "authentication", "invalid api key", "invalid x-api-key", "permission")):
        return (f"The key was refused. Check {key} in .env. A key set in your shell (for example in .bashrc) "
                "wins over .env, so check there too.")
    if any(word in text for word in ("no available accounts", "429", "rate limit", "quota", "overloaded", "503", "529")):
        return "The provider is busy or out of quota. Try again in a few minutes, or choose another model under [model]."
    if any(word in text for word in ("404", "not found", "does not exist", "unknown model", "model_not_found")):
        return "Check the model name under [model] model in wendao.toml (it must match the provider's name exactly)."
    if any(word in text for word in ("resolve", "connect", "timed out", "timeout", "unreachable", "ssl", "certificate")):
        return "Can't reach the provider. Check [model] base_url in wendao.toml and your internet connection."
    if "max_tokens" in text or "no text" in text or "no answer" in text:
        return "The model used up its tokens before answering. Raise [model] max_tokens in wendao.toml (e.g. 16000)."
    return f"Check [model] in wendao.toml and {key} in .env."


def cmd_doctor(args: argparse.Namespace) -> None:
    from wendao import evaluate, suggest
    from wendao.graph import GRAPH_SCHEMA
    from wendao.ingest import SUPPORTED_SUFFIXES, changed_notes, discover_content_files
    from wendao.rag.providers import check_connection, default_model_name, default_provider_name

    workspace = load_workspace(args)
    problems: list[tuple[str, str]] = []  # (what is wrong, how to fix it)

    def line(label: str, text: str) -> None:
        say(f"{label + ':':<11}{text}")

    line("Workspace", str(workspace.root))
    line("Course", workspace.display_name)

    source = workspace.source
    notes: list[str] = []
    if source is None:
        line("Notes", "not set")
        problems.append(("Notes: no folder set.", "Set [source] path in wendao.toml to your notes folder."))
    elif not source.is_dir():
        line("Notes", f"{source}  (folder not found)")
        problems.append((f"Notes: {source} doesn't exist.",
                         "Fix [source] path in wendao.toml. It is relative to the folder that holds wendao.toml."))
    else:
        notes = discover_content_files(source, workspace.exclude, workspace.ignore_dirs, workspace.file_suffixes)
        line("Notes", f"{len(notes)} files in {source}")
        if not notes:
            kinds = ", ".join(sorted(workspace.file_suffixes or SUPPORTED_SUFFIXES))
            problems.append(("Notes: no files Wendao can read.",
                             f"Put your notes ({kinds}) in {source}, or check [source] file_types and exclude."))

    built = workspace.chunks_path.exists() and workspace.graph_path.exists()
    if not built:
        line("Build", "not built yet")
        problems.append(("Build: no graph or search index yet.", "Run `wendao build`."))
    elif graph_schema(workspace) != GRAPH_SCHEMA:
        line("Build", "made by an older Wendao")
        problems.append(("Build: made by an older version of Wendao, so concept introductions and course links are missing.",
                         "Run `wendao build`, then restart `wendao serve` if it is running."))
    elif notes and (changed := changed_notes(workspace)):
        line("Build", f"older than your notes ({len(changed)} file{'s' if len(changed) > 1 else ''} changed)")
        shown = ", ".join(changed[:3]) + (", ..." if len(changed) > 3 else "")
        problems.append((f"Build: your notes changed after the last build ({shown}).",
                         "Run `wendao build`, then restart `wendao serve` if it is running."))
    else:
        line("Build", "ok")

    concepts = [item for item in suggest.read_concepts(workspace.concepts_path) if item.get("id") != "example-concept"]
    line("Concepts", f"{len(concepts)}")
    if not concepts and built:  # before the first build, building comes first
        problems.append(("Concepts: none yet, so the graph only shows chapters and pages.",
                         "Run `wendao build --concepts` (your AI model finds them in your notes)."))

    if workspace.roster_path and not workspace.roster_path.exists():
        problems.append((f"Class list: {relative(workspace, workspace.roster_path)} not found.",
                         "Add the file (one column, \"email\"), or remove `roster` under [student] in wendao.toml."))
    if not workspace.website:
        say(f"{'Website:':<11}not set (answers show sources without links; set [course] website to add them)")

    if args.add_questions:
        step_questions(workspace)
    if built and workspace.questions_path.exists():
        check_questions(workspace, args, problems)
    elif built:
        line("Questions", "none")
        problems.append(("Questions: no test questions, so nothing checks that search finds the right pages.",
                         "Run `wendao doctor --add-questions` (your AI model writes some from your notes)."))

    name = default_provider_name()
    if args.no_model:
        line("Model", "not checked")
    else:
        try:
            provider, model = check_connection()
            line("Model", f"ok ({provider} / {model})")
        except RuntimeError as exc:
            label = f"{name} / {default_model_name(name)}" if name != "dry_run" else "none set up"
            line("Model", f"not working ({label})")
            problems.append((f"Model: {exc}", model_fix(name, str(exc))))
        else:
            if args.real and built:
                ask_real(workspace, args, problems)
    if workspace.student_ai == "teacher" and name == "dry_run":
        problems.append(("Students: [student] ai = \"teacher\" but no model is set up, so students can't ask questions.",
                         "Set up a model (see above), or set [student] ai = \"student\" so students use their own key."))

    say()
    if not problems:
        say("All good.")
        return
    say(f"{len(problems)} thing{'s' if len(problems) > 1 else ''} to fix:")
    for number, (problem, fix) in enumerate(problems, 1):
        say(f"\n{number}. {problem}")
        say("   Fix: " + fix.replace("\n", "\n   "))
    raise SystemExit(1)


def selected_questions(workspace: Workspace, args: argparse.Namespace) -> list[dict]:
    from wendao import evaluate

    questions = evaluate.load_questions(workspace)
    if args.only:
        questions = [case for case in questions if case["id"] in set(args.only)]
        if not questions:
            raise ValueError(f"No questions with id: {', '.join(args.only)}")
    return questions


def check_questions(workspace: Workspace, args: argparse.Namespace, problems: list) -> None:
    """Search and check the prompt for every test question; no model, no cost."""
    from wendao import evaluate
    from wendao.rag.pipeline import QueryPipeline

    questions = selected_questions(workspace, args)
    pipeline = QueryPipeline.for_workspace(workspace)
    searched, search_report = evaluate.run_search(workspace, pipeline, questions)
    answered, answers_report = evaluate.run_answers(workspace, pipeline, questions)
    failed: dict[str, list[str]] = {}
    for item in searched + answered:
        for check, ok in item["checks"].items():
            if not ok and check not in failed.setdefault(item["id"], []):
                failed[item["id"]].append(check)
    failed = {qid: checks for qid, checks in failed.items() if checks}
    say(f"{'Questions:':<11}{len(questions) - len(failed)}/{len(questions)} as expected")
    for qid, checks in failed.items():
        problems.append((
            f"Question {qid}: {', '.join(checks)} not as expected.",
            f"See {relative(workspace, search_report)} and {relative(workspace, answers_report)}. If search misses the "
            "right page, add the word to [search] aliases in wendao.toml; if your notes changed, update the question "
            "in questions.json.",
        ))


def ask_real(workspace: Workspace, args: argparse.Namespace, problems: list) -> None:
    """Ask the real model every test question (uses API credits)."""
    from wendao import evaluate
    from wendao.rag.pipeline import QueryPipeline
    from wendao.rag.providers import default_provider_name

    questions = selected_questions(workspace, args)
    say(f"Asking the model {len(questions)} questions ...")
    items, report = evaluate.run_real(workspace, pipeline=QueryPipeline.for_workspace(workspace), questions=questions)
    ok = sum(item["ok"] for item in items)
    say(f"{'Answers:':<11}{ok}/{len(items)} answered. Read them in {relative(workspace, report)}")
    for item in items:
        if not item["ok"]:
            problems.append((f"Question {item['case']['id']}: {item['error']}",
                             model_fix(default_provider_name(), item["error"])))


# serve ----------------------------------------------------------------------------------------


def serve_static_site(folder: Path, port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def cmd_serve(args: argparse.Namespace) -> None:
    workspace = load_workspace(args)
    if not args.no_check and workspace.student_ai != "student":
        from wendao.rag.providers import check_connection, default_provider_name

        try:
            if default_provider_name() == "dry_run":
                say("No AI model set up: the graph and search work, AI answers are off. See `wendao doctor`.")
            else:
                provider, model = check_connection()
                say(f"Model: {provider} / {model}")
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}\nRun `wendao doctor` for help, or start anyway with --no-check.") from None

    if args.widget:
        from wendao.web.widget import create_app

        port = args.port or 5055
    else:
        from wendao.web.explorer import create_app

        port = args.port or 5057
    app = create_app(workspace)

    url = f"http://127.0.0.1:{port}/"
    if args.site:
        site = Path(args.site).expanduser().resolve()
        if not site.is_dir():
            raise WorkspaceError(f"Site folder not found: {site}")
        serve_static_site(site, args.site_port)
        url = f"http://127.0.0.1:{args.site_port}/"
        say(f"Course site: {url} (serving {site})")
    say(f"{'Widget API' if args.widget else 'Wendao'}: http://127.0.0.1:{port}/   Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host=args.host, port=port, debug=False, threaded=True)


# course-website widget --------------------------------------------------------------------------


def cmd_widget(args: argparse.Namespace) -> None:
    from wendao.web.site_widget import install, remove

    site = Path(args.site)
    if args.action == "remove":
        changed = remove(site)
        say(f"Removed the Wendao widget from {changed} pages in {site}.")
        return
    result = install(site, api=args.api or "")
    say(f"Added the Wendao widget to {result['pages']} pages in {site}.")
    if result["replaced_old_widget"]:
        say("  Replaced the previous course widget on those pages.")
    say(f"  Widget files: {result['folder']}")
    if args.api:
        say(f"  Questions go to {args.api}")
    else:
        say("  Questions go to the same website address under /api (on your computer: http://127.0.0.1:5055).")
    say()
    say("Try it locally:  wendao serve --widget --site " + str(site))


# feedback -------------------------------------------------------------------------------------


def cmd_feedback(args: argparse.Namespace) -> None:
    from wendao.web.accounts import TokenSigner
    from wendao.web.feedback import FeedbackStore

    workspace = load_workspace(args)
    if not (workspace.root / "usage.db").exists():
        say("No feedback yet. Students rate answers with the thumbs buttons in the chat widget.")
        return
    items = FeedbackStore(workspace.root / "usage.db", TokenSigner.for_folder(workspace.root).secret).recent(args.days)
    span = "today" if args.days == 1 else f"the last {args.days} days"
    up = sum(item["rating"] == "up" for item in items)
    say(f"Feedback in {span}: {up} helpful, {len(items) - up} not helpful.")
    shown = items if args.all else [item for item in items if item["rating"] == "down"]
    for item in shown[: args.limit]:
        say()
        mark = "Helpful" if item["rating"] == "up" else "Not helpful"
        say(f"{mark}  {item['time'].replace('T', ' ')}" + (f"  on {item['page']}" if item["page"] else ""))
        say(f"  Q: {item['question']}")
        answer = " ".join(item["answer"].split())
        say(f"  A: {answer[:300]}{'...' if len(answer) > 300 else ''}")
        if item["note"]:
            say(f"  Student's note: {item['note']}")
    if len(shown) > args.limit:
        say(f"\n... and {len(shown) - args.limit} more. Use --limit to see more.")
    if not args.all and up:
        say("\n(Only answers marked not helpful are listed. Add --all to see every rating.)")


# students -------------------------------------------------------------------------------------


def cmd_students(args: argparse.Namespace) -> None:
    from datetime import date

    from wendao.web.accounts import UsageStore, load_roster

    workspace = load_workspace(args)
    if not workspace.roster_path:
        raise RuntimeError(
            "No class list is set. Add one to use sign-in and per-student limits:\n"
            '  1. Save your class list as students.csv with an "email" column (optional: "name", "limit").\n'
            '  2. Under [student] in wendao.toml, add: roster = "students.csv"'
        )
    roster = load_roster(workspace.require(workspace.roster_path, "Add your class list there."))
    default = workspace.questions_per_day
    say(f"Class list: {relative(workspace, workspace.roster_path)} ({len(roster)} students)")
    say(f"Daily limit: {default if default > 0 else 'none'} questions per student on the course AI")
    usage_db = workspace.root / "usage.db"
    if not usage_db.exists():
        say("No questions yet. Usage is recorded on the computer that runs `wendao serve`.")
        return
    day = None if args.all else date.today().isoformat()
    usage = dict(UsageStore(usage_db).report(day))
    say()
    say(f"{'All days' if args.all else 'Today'}:")
    width = max([len(email) for email in roster] + [10])
    for email, student in sorted(roster.items(), key=lambda item: -usage.get(item[0], 0)):
        limit = student.limit if student.limit is not None else default
        used = usage.get(email, 0)
        shown = f"{used}/{limit}" if limit > 0 and not args.all else str(used)
        say(f"  {email:<{width}}  {shown:>7}  {student.name}")
    unknown = sorted(set(usage) - set(roster))
    if unknown:
        say(f"  (+ {len(unknown)} no longer on the class list: {', '.join(unknown)})")


# export -----------------------------------------------------------------------------------------


def cmd_export(args: argparse.Namespace) -> None:
    from wendao.web.explorer import export_page

    workspace = load_workspace(args)
    workspace.require(workspace.graph_path, "Run `wendao build` first.")
    online = (args.online or "").strip()
    if online and not online.startswith(("https://", "http://")):
        raise WorkspaceError("--online must be a web address, e.g. https://wendao.example.edu/graph/")
    slug = re.sub(r"[^a-z0-9]+", "-", workspace.course_name.lower()).strip("-") or "course"
    path = Path(args.output).expanduser() if args.output else workspace.build_dir / f"{slug}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(export_page(workspace, online), encoding="utf-8")
    say(f"Made {relative(workspace, path)} ({path.stat().st_size / 1_000_000:.1f} MB): the knowledge graph in one file.")
    say("  Students open it by double-clicking; it needs no server and no install. You can also put it on any website.")
    if online:
        say(f"  \"Ask AI\" opens the online version at {online}.")
    else:
        say("  The AI chat needs a server. Add --online https://<your Wendao server>/graph/ to link to it.")


def step_questions(workspace: Workspace) -> None:
    """Let the course's model write test questions from the notes and add them to questions.json."""
    from wendao import suggest

    provider, label = suggest.course_model()
    chapters = suggest.load_chapters(workspace)
    say(f"Writing test questions for {len(chapters)} chapters with {label} (one request per chapter) ...")
    existing = suggest.read_questions(workspace.questions_path)
    questions, had = suggest.suggest_questions(workspace, provider, chapters, existing, say=lambda *_: None)
    suggest.add_questions(workspace, questions)
    say(f"  Added {len(questions)} questions to questions.json" + (f"; {had} were already there." if had else "."))
    say("  Each asks about a real page. Read them and delete or fix any that are off.")
    say()


# parser ---------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wendao",
        description="Turn course notes into a knowledge graph and an AI learning agent.",
        epilog="Run a command with --help for its options, e.g. `wendao ask --help`.",
    )
    parser.add_argument("--version", action="version", version=f"wendao {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-w", "--workspace", type=Path, default=None,
        help=f"course workspace folder (default: the folder with {CONFIG_NAME} that contains the current folder)",
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    init = commands.add_parser("init", help="create a new course workspace")
    init.add_argument("folder", nargs="?", default=".", help="where to create it (default: current folder)")
    init.add_argument("--source", help="folder with your existing notes (default: a new notes/ folder)")
    init.set_defaults(func=cmd_init)

    build = commands.add_parser("build", parents=[common], help="extract notes, build the graph and the search index")
    found = build.add_mutually_exclusive_group()
    found.add_argument("--concepts", action="store_true",
                       help="let your AI model look for new concepts in your notes and add them to concepts.json "
                            "(done on its own the first time, when there are none yet)")
    found.add_argument("--no-concepts", action="store_true", help="don't look for concepts, even if there are none yet")
    build.set_defaults(func=cmd_build)
    commands.add_parser("extract", parents=[common], help="only extract notes into chunks").set_defaults(func=cmd_extract)
    commands.add_parser("graph", parents=[common], help="only rebuild the knowledge graph").set_defaults(func=cmd_graph)
    commands.add_parser("index", parents=[common], help="only rebuild the search index").set_defaults(func=cmd_index)

    ask = commands.add_parser("ask", parents=[common], help="ask a question about the course")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--search-only", action="store_true", help="show what search finds, without calling a model")
    ask.add_argument("--provider", choices=["anthropic", "openai", "gemini", "dry_run"], help="override the configured provider")
    ask.add_argument("--model", help="override the configured model")
    ask.add_argument("--show-prompt", action="store_true", help="also print the prompt sent to the model")
    ask.add_argument("--json", action="store_true", help="print the full result as JSON")
    ask.add_argument("--top-k", type=int, default=5, help="how many chunks to retrieve (default: 5)")
    ask.set_defaults(func=cmd_ask)

    doctor = commands.add_parser("doctor", parents=[common],
                                 help="check your settings, notes, build, test questions, and model, and say how to fix problems")
    doctor.add_argument("--no-model", action="store_true", help="don't contact the model (no API cost)")
    doctor.add_argument("--real", action="store_true", help="also ask the model every test question (uses API credits)")
    doctor.add_argument("--only", nargs="+", metavar="ID", help="only use these test questions")
    doctor.add_argument("--add-questions", action="store_true",
                        help="first let your AI model write test questions from your notes and add them to questions.json")
    doctor.set_defaults(func=cmd_doctor)

    widget = commands.add_parser("widget", help="add the chat widget to a built course website (MyST, Jupyter Book, ...)")
    widget.add_argument("action", choices=["install", "remove"], help="install: add it to every page; remove: take it out")
    widget.add_argument("site", help="the built website folder, for example _build/html")
    widget.add_argument("--api", help="address of your Wendao widget API (default: the same website, under /api)")
    widget.set_defaults(func=cmd_widget)

    feedback = commands.add_parser("feedback", parents=[common], help="see which answers students marked helpful or not")
    feedback.add_argument("--days", type=int, default=7, help="how many days back (default: 7)")
    feedback.add_argument("--all", action="store_true", help="list helpful answers too, not only the unhelpful ones")
    feedback.add_argument("--limit", type=int, default=20, help="how many to list (default: 20)")
    feedback.set_defaults(func=cmd_feedback)

    students = commands.add_parser("students", parents=[common], help="see the class list and questions asked per student")
    students.add_argument("--all", action="store_true", help="total questions over all days instead of today")
    students.set_defaults(func=cmd_students)

    export = commands.add_parser("export", parents=[common], help="put the knowledge graph in one HTML file that opens by double-click")
    export.add_argument("-o", "--output", help="where to write it (default: build/<course-name>.html)")
    export.add_argument("--online", help="address of your online graph page, for the \"Ask AI\" button")
    export.set_defaults(func=cmd_export)


    serve = commands.add_parser("serve", parents=[common], help="start the knowledge graph website")
    serve.add_argument("--widget", action="store_true", help="start the course-website widget API instead")
    serve.add_argument("--port", type=int, help="port (default: 5057, or 5055 with --widget)")
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    serve.add_argument("--site", help="also serve a built course website from this folder, to test the widget")
    serve.add_argument("--site-port", type=int, default=8000, help="port for --site (default: 8000)")
    serve.add_argument("--no-browser", action="store_true", help="don't open a browser")
    serve.add_argument("--no-check", action="store_true", help="start without checking the model connection")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    # Windows consoles and pipes may not use UTF-8; never crash on characters like "→".
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower().replace("-", "") != "utf8":
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return
    try:
        args.func(args)
    except (WorkspaceError, RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
