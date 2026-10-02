<p align="center">
  <a href="https://github.com/deng-group/wendao"><img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/logo/wendao_logo.svg" width="520" alt="Wendao: ask the way. An AI learning companion."></a>
</p>

<p align="center">
  <a href="https://mle4217-5219.matsci.dev/"><img src="https://img.shields.io/badge/example%20course-MLE4217%2F5219-2563eb" alt="Example course: MLE4217/5219"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/backend-Flask-000000?logo=flask&logoColor=white" alt="Flask backend">
  <img src="https://img.shields.io/badge/LLM-Anthropic%20%7C%20OpenAI%20%7C%20Gemini-d97706" alt="LLM: Anthropic, OpenAI, or Gemini">
</p>

**Wendao** (问道, *wèn dào*, "asking the way") is an AI learning companion. It turns your course materials into two things
for students:

- an **interactive knowledge graph** of the course's chapters and concepts, and
- an **AI learning agent** that answers questions using only the course materials, with a link to the page each answer came from.

It was built for the NUS course [MLE4217/5219 Materials Informatics](https://mle4217-5219.matsci.dev/), and this repository uses
that course as its example. Nothing in the code is tied to that course, so you can run it on any course written as Markdown pages
and Jupyter notebooks.

More docs: [developer notes](https://github.com/deng-group/wendao/blob/main/README_DEVELOPERS.md) · [student guide](https://github.com/deng-group/wendao/blob/main/README_STUDENTS.md) · [product plan](https://github.com/deng-group/wendao/blob/main/docs/PROJECT_PLAN.md)

## How it works

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/wendao_workflow.png" width="100%" alt="Wendao workflow: course materials are extracted and chunked, feed a retrieval-augmented generation pipeline and an LLM, and surface as an interactive knowledge graph and an AI learning agent">
</p>

1. **Extract.** Wendao reads your course pages and notebooks and splits them into small pieces called chunks.
2. **Knowledge graph.** Chapters and concepts become nodes. Two nodes are linked only when they appear together in the course.
3. **Search.** When a student asks a question, Wendao finds the most relevant chunks. It combines keyword search with meaning-based search.
   If the chunks don't really answer the question, Wendao says so instead of guessing.
4. **Answer.** The chunks it found are sent to a language model (Claude, GPT, Gemini, or a local model), which writes the answer.
   No model training or GPU is needed.

## Demo

### Knowledge graph

Blue nodes are chapters and orange nodes are concepts. Click a chapter to see its concepts, or click a concept to see every chapter
that uses it. You can also filter with the legend or search.

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/knowledge_graph_demo.gif" width="100%" alt="Screen recording: exploring the Wendao knowledge graph by clicking nodes, using the legend, and searching">
</p>

<p align="center"><sub>Full-resolution video: <a href="https://github.com/deng-group/wendao/blob/main/docs/assets/demo/knowledge_graph_demo.mp4">knowledge_graph_demo.mp4</a></sub></p>

### AI learning agent

Select a node and press **Explain**, or pick one of the suggested questions. You can ask follow-up questions and open the cited
course page from the answer. If the course doesn't cover a question, the agent tells you.

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/ai_learning_agent_demo.gif" width="100%" alt="Screen recording: asking the Wendao AI learning agent to explain a selected node, following up, and opening the cited course page">
</p>

<p align="center"><sub>Full-resolution video: <a href="https://github.com/deng-group/wendao/blob/main/docs/assets/demo/ai_learning_agent_demo.mp4">ai_learning_agent_demo.mp4</a></sub></p>

## Why use it

**Students** can explore the course in any order, see how topics connect, and check their understanding. The agent only answers
from the course, and it says clearly when it can't.

**Instructors** can reuse Wendao for their own course. You only need your course content and a list of concepts. Only the chunks
relevant to each question are sent to the model, and any major model provider works. Test questions let you check what the agent
will and won't answer before students use it.

## Install

You need Python 3.11 or newer and `curl`. Install the `wendao` command with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install wendao
```

Or with pip:

```bash
pip install wendao
```

Check it works with `wendao --help`.

## How you work with Wendao

Wendao is the tool. Your course lives in its own folder, called a **workspace**, next to your lecture notes:

```text
my-course/
  notes/            your lecture notes: Markdown pages and Jupyter notebooks (or point to a folder elsewhere)
  wendao.toml        course name, website, chapters, and which model to use
  concepts.json     the concepts to show in the knowledge graph
  questions.json    test questions
  .env              your API key (never committed)
  build/            files Wendao creates: chunks, graph, search index, reports
```

Run `wendao` commands anywhere inside the workspace. Wendao finds `wendao.toml` by itself.

| Command | What it does |
| --- | --- |
| `wendao init my-course` | Create a new workspace with starter files |
| `wendao build` | Read your notes, build the knowledge graph, and build the search index |
| `wendao ask "question"` | Ask a question. Add `--search-only` to see what search finds, without a model |
| `wendao serve` | Open the knowledge graph website with the AI agent |
| `wendao serve --widget` | Start the API for the chat widget on your course website |
| `wendao check` | Check your settings and the connection to the model |
| `wendao eval` | Test Wendao with the questions in `questions.json` |

Run any command with `--help` to see its options.

## Quick start: try the example course

The repository includes a ready-built workspace for MLE4217/5219:

```bash
git clone https://github.com/deng-group/wendao.git
cd wendao/examples/mle4217_5219
wendao ask --search-only "What is a convex hull?"    # works without a model
```

To get real answers, set `[model]` in `wendao.toml` to your own provider and put its key in `.env`
(see [Connect a model](#4-connect-a-model)). Then run `wendao serve`. It checks the model connection, starts Wendao at
http://127.0.0.1:5057/, and opens it in your browser. Press `Ctrl+C` to stop.

## Use Wendao for your own course

### 1. Create a workspace

```bash
wendao init my-course                              # creates my-course/ with a notes/ folder
wendao init my-course --source ~/teaching/notes    # or use notes you already have
cd my-course
```

Your notes should be Markdown (or MyST) pages and Jupyter notebooks, such as a Jupyter Book. Each top-level folder becomes a
chapter.

### 2. Describe your course

Open `wendao.toml` and fill in the course name and website. The website lets answers link to the right page. The file explains
each setting. The most useful ones are:

- `[[chapters]]`: names, order, and visibility for your chapter folders. Folders you don't list still appear, named after the folder.
- `[search] aliases`: spellings students might type, such as `{ "convexhull" = "convex hull" }`.
- `[graph] bridge_stop_concepts`: very common concepts, such as `python`, that shouldn't link chapters on their own.

Then list the concepts for the knowledge graph in `concepts.json`. For each concept, give the words to look for in your notes:

```json
{"id": "convex-hull", "label": "Convex Hull", "category": "thermodynamics", "aliases": ["convex hull", "convex hulls"]}
```

See [`examples/mle4217_5219/`](https://github.com/deng-group/wendao/tree/main/examples/mle4217_5219/) for a complete example.

### 3. Build

```bash
wendao build
```

This reads your notes, builds the knowledge graph, and builds the search index. The first run downloads the search model.
Run it again whenever you change your notes or settings.

Check the search results before you connect a model:

```bash
wendao ask --search-only "What is a convex hull?"
```

This shows the pages search found and whether Wendao thinks it can answer.

### 4. Connect a model

Wendao works with three kinds of API. Choose one in `wendao.toml` and put its key in `.env`:

| `provider` | Works with | Key in `.env` |
| --- | --- | --- |
| `anthropic` | Anthropic (Claude), or any server that uses the same API | `ANTHROPIC_AUTH_TOKEN` |
| `openai` | OpenAI, or any OpenAI-compatible server: DeepSeek, OpenRouter, vLLM, Ollama, LM Studio | `OPENAI_API_KEY` |
| `gemini` | Google Gemini | `GEMINI_API_KEY` |

For example, for a local model with [Ollama](https://ollama.com/), which needs no key:

```toml
[model]
provider = "openai"
model = "llama3.1"
base_url = "http://localhost:11434/v1"
```

Then test it:

```bash
wendao check
wendao ask "How is Materials Project data used to train MACE potentials?"
```

A small, cheap model is usually enough, because Wendao gives it the relevant course text. Answers use a temperature of 0.2. Some
models, such as OpenAI's reasoning models, don't accept one; set `temperature = "none"` under `[model]` for those.

### 5. Run it

```bash
wendao serve
```

To put Wendao on a server, see [`deploy/DEPLOYMENT.md`](https://github.com/deng-group/wendao/blob/main/deploy/DEPLOYMENT.md). To add the agent to your course website as a chat
widget, run `wendao serve --widget` and see [`README_DEVELOPERS.md`](https://github.com/deng-group/wendao/blob/main/README_DEVELOPERS.md).

## Testing your agent

Add real questions from your course to `questions.json`. For each one you can say what Wendao should do:

```json
{
  "id": "convex_hull",
  "query": "What is a convex hull?",
  "expected_status": "answerable",
  "expected_files": ["high_throughput/thermodynamics.md"]
}
```

`expected_status` is one of `answerable`, `needs_time_context` (schedule questions), `needs_clarification` (too broad),
`weak_evidence` (not enough in the notes), or `out_of_scope`. Then run:

```bash
wendao eval             # search and answer decisions, no model needed
wendao eval --answers   # prompts and citations, no model needed
wendao eval --real      # ask the real model every question, so you can read the answers (uses API credits)
```

Reports are saved in `build/reports/`.

## Developing Wendao

```bash
git clone https://github.com/deng-group/wendao.git
cd wendao
uv sync --all-extras                       # creates .venv with the locked versions, `wendao` included
uv run python -m unittest discover -s tests
```

| Folder | What's in it |
| --- | --- |
| `src/wendao/` | The tool: command line (`cli.py`), workspaces, extraction, knowledge graph, evaluation |
| `src/wendao/rag/` | Search, answer checks, prompts, and model providers |
| `src/wendao/web/` | The knowledge graph website and the chat widget API |
| `examples/mle4217_5219/` | The example workspace, ready built |
| `tests/` | Unit tests |
| `deploy/` | Server setup files |
| `docs/` | Figures, demo videos, project plan, and older design notes |

## Citation

Wendao is developed by the [Deng group](https://github.com/deng-group) in the Department of Materials Science and Engineering at the
National University of Singapore, for MLE4217/5219 Materials Informatics. If you use Wendao or adapt it for your own course, please
cite this repository:

```bibtex
@software{wendao2026,
  author = {Deng, Zeyu and contributors},
  title  = {Wendao: an AI learning companion with an interactive course knowledge graph},
  year   = {2026},
  url    = {https://github.com/deng-group/wendao}
}
```

## License

Wendao is free software under the [GNU General Public License v3.0 or later](https://github.com/deng-group/wendao/blob/main/LICENSE). You can use, study, change, and share it.
If you share a changed version, you must share its source code under the same license.
