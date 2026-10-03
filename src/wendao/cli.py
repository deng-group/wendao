"""The `wendao` command. Run `wendao --help` to see everything it can do."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wendao import __version__
from wendao import workspace as workspace_module
from wendao.pack import PackError
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
    """Building a course needs the teacher extra; say so plainly if it is missing."""
    import importlib.util

    missing = [package for module, package in TEACHER_MODULES.items() if importlib.util.find_spec(module) is None]
    if missing:
        raise RuntimeError(
            "Building a course needs the teacher tools, which are not installed "
            f"(missing: {', '.join(missing)}).\nInstall them with:  pip install \"wendao[teacher]\""
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
    step_graph(workspace)
    step_index(workspace, rebuild=False)
    say(f"Done in {time.monotonic() - started:.0f}s. Try: wendao ask \"<a question about your course>\"")


def cmd_extract(args: argparse.Namespace) -> None:
    require_teacher_tools()
    step_extract(load_workspace(args))


def cmd_graph(args: argparse.Namespace) -> None:
    step_graph(load_workspace(args))


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
            "Add an API key to .env (see `wendao check`), or use --search-only to see what search finds."
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


def cmd_check(args: argparse.Namespace) -> None:
    from wendao.rag.providers import check_connection, default_provider_name

    workspace = load_workspace(args)
    say(f"Workspace: {workspace.root}")
    say(f"Course:    {workspace.display_name}")
    ai = {"teacher": "your course AI", "student": "their own AI key", "either": "your course AI or their own key"}
    server = f" (apps use {workspace.student_server})" if workspace.student_server else ""
    say(f"Students:  ask with {ai[workspace.student_ai]}{server}")
    source = workspace.source
    say(f"Notes:     {source if source else '(not set)'}{'' if source is None or source.is_dir() else '  ← folder not found'}")
    for label, path in [("Chunks", workspace.chunks_path), ("Graph", workspace.graph_path)]:
        say(f"{label + ':':<10} {'ok' if path.exists() else 'missing, run `wendao build`'}")
    try:
        provider, model = check_connection()
    except RuntimeError as exc:
        say("Model:     not working")
        if default_provider_name() != "dry_run":
            raise
        raise RuntimeError(
            f"{exc}\n\nChoose a model under [model] in wendao.toml and put its API key in .env, for example:\n"
            '  wendao.toml:  [model]\n               provider = "openai"\n               model = "gpt-4.1-mini"\n'
            "  .env:        OPENAI_API_KEY=sk-..."
        ) from None
    say(f"Model:     ok ({provider} / {model})")


# serve ----------------------------------------------------------------------------------------


def serve_static_site(folder: Path, port: int) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def cmd_serve(args: argparse.Namespace) -> None:
    workspace = load_workspace(args)
    if not args.no_check and workspace.student_ai != "student":
        from wendao.rag.providers import check_connection

        try:
            provider, model = check_connection()
            say(f"Model: {provider} / {model}")
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}\nRun `wendao check` for help, or start anyway with --no-check.") from None

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


# site -----------------------------------------------------------------------------------------


def cmd_site(args: argparse.Namespace) -> None:
    from wendao.course_site import build, new, point_source_at

    workspace = load_workspace(args)
    if args.action == "new":
        require_teacher_tools()
        out = args.out or workspace.root / "site"
        result = new(workspace, out)
        folder = relative(workspace, result["folder"])
        say(f"Made the course website in {folder}:")
        say(f"  {result['chapters']} chapters, {result['copied']} pages copied, {result['converted']} made from slides, "
            f"Word, PDF or LaTeX files, {result['files']} other files (images, data, downloads).")
        if result["used_own"]:
            say(f"  Used your own {', '.join(result['used_own'])}.")
        for warning in result["warnings"]:
            say(f"  Warning: {warning}")
        old = point_source_at(workspace, result["folder"])
        say(f"  {CONFIG_NAME}: Wendao now reads the website's pages ([source] path = \"{folder}\""
            + (f", was \"{old}\")." if old else ")."))
        say()
        say("Next:")
        say(f"  1. Fill in {folder}/syllabus.md and {folder}/calendar.md, and edit any page you like.")
        say("  2. wendao site build    builds the website and adds the chat widget")
        say("  3. wendao build         rebuilds the knowledge graph and search from the website's pages")
        say(f"  4. wendao serve --widget --site {folder}/_build/html    to try it")
        return
    site = Path(args.site) if args.site else website_folder(workspace)
    say(f"Building the website in {relative(workspace, site)} with MyST ...")
    result = build(site, api=args.api or "")
    html = relative(workspace, result["html"])
    say(f"Built {html} and added the chat widget to {result['pages']} pages.")
    say("Publish that folder on any web server. Try it first with:")
    say(f"  wendao serve --widget --site {html}")


def website_folder(workspace) -> Path:
    """The site to build: the notes folder if it is a MyST site, else site/ in the workspace."""
    if workspace.source and (Path(workspace.source) / "myst.yml").exists():
        return Path(workspace.source)
    return workspace.root / "site"


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


# pack and open --------------------------------------------------------------------------------


def cmd_pack(args: argparse.Namespace) -> None:
    require_teacher_tools()
    from wendao.pack import pack

    workspace = load_workspace(args)
    say("Packing the course app ...")
    path, notes = pack(workspace, Path(args.output).expanduser() if args.output else None, include_model=not args.no_model)
    size = path.stat().st_size / 1_000_000
    say(f"  {relative(workspace, path)} ({size:.0f} MB)")
    ai = {"teacher": "your course AI", "student": "their own AI key", "either": "your course AI or their own key"}
    say(f"  Students ask questions with {ai[workspace.student_ai]}.")
    for note in notes:
        say(f"  Note: {note}")
    say()
    say("Share this file with your students. They open it with:")
    say("  pip install wendao")
    say(f"  wendao open {path.name}")


def free_port(preferred: int) -> int:
    import socket

    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found near {preferred}; pass one with --port.")


def cmd_open(args: argparse.Namespace) -> None:
    from wendao.pack import open_pack
    from wendao.web.explorer import create_app

    say(f"Opening {Path(args.file).name} ...")
    course = open_pack(Path(args.file))
    app = create_app(course)
    port = args.port or free_port(5057)
    url = f"http://127.0.0.1:{port}/"
    say(f"{course.display_name}: {url}   Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


# eval -----------------------------------------------------------------------------------------


def cmd_eval(args: argparse.Namespace) -> None:
    from wendao import evaluate
    from wendao.rag.pipeline import QueryPipeline

    workspace = load_workspace(args)
    questions = evaluate.load_questions(workspace)
    if args.only:
        questions = [case for case in questions if case["id"] in set(args.only)]
        if not questions:
            raise ValueError(f"No questions with id: {', '.join(args.only)}")
    pipeline = QueryPipeline.for_workspace(workspace)

    if args.real:
        say(f"Asking the model {len(questions)} questions ...")
        items, report = evaluate.run_real(workspace, pipeline, questions)
        ok = sum(item["ok"] for item in items)
        say(f"{ok}/{len(items)} answered. Read them in {relative(workspace, report)}")
        for item in items:
            if not item["ok"]:
                say(f"  FAILED {item['case']['id']}: {item['error']}")
        if ok < len(items):
            raise SystemExit(1)
        return

    runner = evaluate.run_answers if args.answers else evaluate.run_search
    evaluations, report = runner(workspace, pipeline, questions)
    passed = sum(item["passed"] for item in evaluations)
    say(f"{passed}/{len(evaluations)} passed. Report: {relative(workspace, report)}")
    for item in evaluations:
        if not item["passed"]:
            failed = [name for name, ok in item["checks"].items() if not ok]
            say(f"  FAILED {item['id']}: {', '.join(failed)}")
    if passed < len(evaluations):
        raise SystemExit(1)


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

    commands.add_parser("check", parents=[common], help="check the workspace and the model connection").set_defaults(func=cmd_check)

    widget = commands.add_parser("widget", help="add the chat widget to a built course website (MyST, Jupyter Book, ...)")
    widget.add_argument("action", choices=["install", "remove"], help="install: add it to every page; remove: take it out")
    widget.add_argument("site", help="the built website folder, for example _build/html")
    widget.add_argument("--api", help="address of your Wendao widget API (default: the same website, under /api)")
    widget.set_defaults(func=cmd_widget)

    site = commands.add_parser("site", parents=[common], help="make a course website (MyST) from your files, and build it")
    site.add_argument("action", choices=["new", "build"],
                      help="new: make the website from your notes folder; build: build it and add the chat widget")
    site.add_argument("--out", type=Path, help="new: where to put the website (default: site/ in the workspace)")
    site.add_argument("--site", help="build: the website folder (default: your notes folder if it is a MyST site, else site/)")
    site.add_argument("--api", help="build: address of your Wendao widget API (default: the same website, under /api)")
    site.set_defaults(func=cmd_site)

    students = commands.add_parser("students", parents=[common], help="see the class list and questions asked per student")
    students.add_argument("--all", action="store_true", help="total questions over all days instead of today")
    students.set_defaults(func=cmd_students)

    pack = commands.add_parser("pack", parents=[common], help="package the built course into one file for students")
    pack.add_argument("-o", "--output", help="where to write it (default: build/<course-name>.wendao)")
    pack.add_argument("--no-model", action="store_true", help="leave out the search model (smaller file; students download it once)")
    pack.set_defaults(func=cmd_pack)

    opener = commands.add_parser("open", help="open a course app (.wendao file) from your teacher")
    opener.add_argument("file", help="the .wendao file")
    opener.add_argument("--port", type=int, help="port (default: the first free port from 5057)")
    opener.add_argument("--no-browser", action="store_true", help="don't open a browser")
    opener.set_defaults(func=cmd_open)

    serve = commands.add_parser("serve", parents=[common], help="start the knowledge graph website")
    serve.add_argument("--widget", action="store_true", help="start the course-website widget API instead")
    serve.add_argument("--port", type=int, help="port (default: 5057, or 5055 with --widget)")
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    serve.add_argument("--site", help="also serve a built course website from this folder, to test the widget")
    serve.add_argument("--site-port", type=int, default=8000, help="port for --site (default: 8000)")
    serve.add_argument("--no-browser", action="store_true", help="don't open a browser")
    serve.add_argument("--no-check", action="store_true", help="start without checking the model connection")
    serve.set_defaults(func=cmd_serve)

    evaluation = commands.add_parser("eval", parents=[common], help="test Wendao with the questions in questions.json")
    level = evaluation.add_mutually_exclusive_group()
    level.add_argument("--answers", action="store_true", help="check prompts and citations (dry run, no model)")
    level.add_argument("--real", action="store_true", help="ask the real model every question (uses API credits)")
    evaluation.add_argument("--only", nargs="+", metavar="ID", help="only run these question ids")
    evaluation.set_defaults(func=cmd_eval)
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
    except (WorkspaceError, PackError, RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
