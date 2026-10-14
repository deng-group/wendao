# Developer notes

How Wendao works inside, and how to work on it. For using Wendao, see the [README](README.md) and the
[teacher guide](docs/GUIDE.md). Plans and ideas are tracked in [GitHub issues](https://github.com/deng-group/wendao/issues).

## Setup

```bash
git clone https://github.com/deng-group/wendao.git
cd wendao
uv sync --all-extras                          # .venv with the locked versions, `wendao` included
uv run python -m unittest discover -s tests   # no network: providers use a local fake server
cd examples/mle4217_5219
uv run wendao doctor --no-model               # the example's 21 test questions must all pass
```

Dependencies are in `pyproject.toml` and `uv.lock`; commit both together. `pip install wendao` has everything for
building and serving a course; extras: `gpu` (PyTorch search) and `deploy` (gunicorn). Keep the note readers' imports
lazy, so serving starts fast. CI checks that the built wheel installs and runs on Linux, Windows, and macOS.

| Folder | What's in it |
| --- | --- |
| `src/wendao/` | The tool: command line (`cli.py`), workspaces, extraction, knowledge graph, test questions |
| `src/wendao/rag/` | Search, answer checks, prompts, and model providers |
| `src/wendao/web/` | The knowledge graph website, the chat widget and its API, sign-in, feedback |
| `examples/mle4217_5219/` | The example workspace, ready built |
| `tests/` | Unit tests |
| `deploy/` | Server setup files |
| `docs/` | Teacher guide, figures, demo videos, and older design notes (`docs/history/`) |

## Phase Workflow and Tests

![Phase workflow and tests](docs/assets/phase_workflow_tests.png)

The current system is organized as a staged RAG pipeline. Phase A extracts course
content from the sibling book repo into chunk records and metadata. Phase B runs
hybrid retrieval over those chunks, then applies an answerability gate before any
LLM call. Phase C packages selected evidence, short memory, and answer policy
into a prompt and sends it to the configured model provider. Phase D is the
Jupyter Book widget, which only calls the backend API. Phase E is deployment and
pilot testing.

Retrieval currently combines two signals:

- BM25 lexical search over normalized chunk text, weighted at `0.45`.
- Sentence-transformers embedding retrieval, weighted at `0.55`.

The retriever normalizes case and separators, expands a small alias table such as
`convexhull -> convex hull`, boosts exact phrase/title/path matches, downweights
figure-only chunks unless the query asks for figures, and applies a `+0.25`
boost to `syllabus.md` / `calendar.md` for logistics queries.

The answerability gate has five statuses:

| Status | Meaning |
| --- | --- |
| `answerable` | Retrieved course evidence is strong enough for an answer. |
| `needs_time_context` | The question is logistics/time-sensitive and must include the academic year/semester. |
| `needs_clarification` | The query is too broad and should be narrowed. |
| `weak_evidence` | Retrieved evidence is too weak to support a reliable answer. |
| `out_of_scope` | The question appears outside the course knowledge base. |

Current gate thresholds:

| Parameter | Value | Use |
| --- | --- | --- |
| `strong_score` | `0.72` | Baseline strong-evidence reference. |
| `weak_score` | `0.42` | Below this, return `weak_evidence`. |
| `min_embedding_score` | `0.18` | If BM25 is nearly zero and embedding is below this, return `weak_evidence`. |
| multi-source ratio | `0.72` | Top-5 results within 72% of the top score are considered close evidence. |
| prompt evidence threshold | `0.60` | Chunks at or above this score are sent as full-content LLM evidence. |
| off-topic meaning floor | `0.20` | With the search model: the best meaning score below this, or below `0.30` while fewer than half the question's words appear in the notes, is `out_of_scope` (after the broad-question check; not for highlighted text or questions without English words). |

Short memory is intentionally small and session-scoped. The browser widget stores
recent turns in `sessionStorage`, so memory is cleared when the tab/window is
closed. The backend prompt builder keeps the most recent 4 memory items and
truncates each item to 1200 characters. For referential follow-ups containing
terms such as "this", "that", "it", or "they", retrieval also prepends up to the
last 4 memory items, truncated to 400 characters each, to make the search query
less ambiguous.

The LLM provider is chosen under `[model]` in the workspace's `wendao.toml`, with
the API key in the workspace's `.env` (ignored by git). Real environment
variables override both. Three providers are
supported: `anthropic` (Anthropic Messages API: `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`), `openai` (any OpenAI-compatible Chat
Completions API: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`), and
`gemini` (`GEMINI_API_KEY`, `GEMINI_MODEL`). All three stream answers. Set
`LLM_PROVIDER` to choose one explicitly; otherwise the first configured provider
in that order is used, falling back to `dry_run`. `LLM_TEMPERATURE` sets the
answer temperature for every provider (default `0.2`); `none` omits the field for
models that reject it. The current local setup uses an
Anthropic-compatible relay.

## Repository Relationship

![Repository relationship](docs/assets/repo_relationship.png)

The book repo and backend repo are intentionally separate. The book repo owns the
course content, generated site, and small student-facing widget. This repo owns
Phase A extraction outputs, retrieval, answerability, prompts, model providers,
evaluations, and the backend `/api/answer` route.

The two repos communicate through a narrow API contract:

```text
book widget -> POST /api/answer/stream -> backend RAG pipeline
```

The widget consumes newline-delimited `start`, `delta`, and `done` events. The
buffered `/api/answer` route remains available for scripts and evaluations.

The book repo should not store API keys, retrieval code, prompts, provider logic,
or chunk data. This repo should not edit course source content unless explicitly
requested. When the book content changes, rerun extraction and evaluation from
the example workspace (`cd examples/mle4217_5219 && wendao build && wendao doctor --no-model`),
which reads the sibling path `../MLE4217_5219_book`.

Syllabus/calendar answers are time-aware by design. Logistics answers must state
the applicable academic year and semester; current extracted logistics content is
marked as **AY2025/2026 Semester 2**.

## Testing the Course-Website Widget Locally

Build the book, add the widget to the built pages, then start the widget API and
serve the built site together:

```bash
cd ../MLE4217_5219_book && make web               # builds the site (and adds the widget)
cd ../wendao/examples/mle4217_5219
wendao widget install ../../../MLE4217_5219_book/_build/html   # if the build didn't add it
wendao serve --widget --site ../../../MLE4217_5219_book/_build/html
```

The widget source is `src/wendao/web/static/site_widget/` (`wendao-widget.js`, `.css`);
`wendao widget install` copies it into `_build/html/_wendao/`. The Graph tab shows the
graph website (`static/explorer/`, the same page as `wendao serve`) in a frame: the widget
API serves it at `/graph/` with `/api/graph`, and `?embed` hides its chat panel and logo.
The widget tells it the current page (`/api/page`, then a `focus` message) and the theme;
Explain and Ask on a node come back as messages and are answered in the Chat tab. The
"On this page" chips use `/api/neighborhood` (`web/graph_api.py`).

When a student highlights text on a page, the widget sends it as `selection` (up to 1500
characters) with the question. The pipeline searches with it ahead of the question, so
"explain this" finds the right passage. The gate does not treat short questions like
"summarize this" as too broad when there is a selection. The prompt quotes the text as
what the question is about; it is not used as evidence.

The highlight bubble's **Search** button opens the text (first 300 characters) in a search
engine in a new tab. The engine is `[widget] web_search` in `wendao.toml` (default `google`;
built-in names in `WEB_SEARCH_ENGINES` in `workspace.py`, or an address with `{q}`) and reaches
the widget through `/api/health` as `web_search`. Nothing is sent to the Wendao server.

Under each answer the widget shows follow-ups (Simpler / Example / Test me: the chat shows a short
label, the AI gets a fuller instruction; after "Test me" the student's next message is sent as
"My answer to that question: ...") and 👍/👎. Ratings go to `POST /api/feedback` and are stored by
`web/feedback.py` in `usage.db` (table `feedback`), with the sender as a daily keyed hash (to cap
spam at 100 a day per sender), never the IP or email. `wendao feedback` lists them. Formulas are
set aside before the Markdown renderer and drawn by KaTeX 0.16.22 from jsDelivr, pinned with
subresource integrity; without it they stay as text.

The last command checks the model, starts the API at `http://127.0.0.1:5055`, serves the
site at `http://127.0.0.1:8000`, and opens it. Use the `?` button at the
bottom-right. Stop both with `Ctrl+C`.
