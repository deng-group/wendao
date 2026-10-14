<p align="center">
  <a href="https://github.com/deng-group/wendao">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/logo/wendao_logo_dark.svg">
      <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/logo/wendao_logo.svg" width="460" alt="Wendao: AI learning companion">
    </picture>
  </a>
</p>

<p align="center">
  <a href="https://deng-group.github.io/wendao/"><img src="https://img.shields.io/badge/try%20it-live%20demo-7c3aed" alt="Try the live demo"></a>
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

**[Try the live demo](https://deng-group.github.io/wendao/)**: the knowledge graph of the example course, in your
browser (no install; the AI chat needs a Wendao server, so it is off in the demo).

Guides: [teacher guide](https://github.com/deng-group/wendao/blob/main/docs/GUIDE.md) · [server setup](https://github.com/deng-group/wendao/blob/main/deploy/DEPLOYMENT.md) · [developer notes](https://github.com/deng-group/wendao/blob/main/README_DEVELOPERS.md) · [plans and ideas](https://github.com/deng-group/wendao/issues)

## Demo

### Knowledge graph

Chapters, pages, and concepts, linked where the course connects them. Drag, zoom, and search; click a concept to read
a short introduction from the notes and jump to the course page.

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/knowledge_graph_demo.webp" width="100%" alt="Screen recording: hovering and dragging nodes in the Wendao knowledge graph, searching for a concept and reading its introduction, filtering with the legend, and switching to dark mode and The Economist style">
</p>

<p align="center"><sub>Full video: <a href="https://github.com/deng-group/wendao/blob/main/docs/assets/demo/knowledge_graph_demo.mp4">knowledge_graph_demo.mp4</a></sub></p>

### Guided path

New to the course? The guided path walks through it chapter by chapter: each chapter's overview and learning
objectives from the course pages, then the key concepts it introduces, with where to read about them. Students can stop
and continue later. [Try it](https://deng-group.github.io/wendao/?path).

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/guided_path_demo.webp" width="100%" alt="Screen recording: the guided path through the example course, showing chapter overviews and learning objectives, key concepts with the pages to read, and Back, Next and Next chapter">
</p>

<p align="center"><sub>Full video: <a href="https://github.com/deng-group/wendao/blob/main/docs/assets/demo/guided_path_demo.mp4">guided_path_demo.mp4</a></sub></p>

### AI learning agent

Select a node and press **Explain**, or ask your own question. Answers come only from the course and link to the pages
they used. If the course doesn't cover a question, the agent says so.

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/ai_learning_agent_demo.webp" width="100%" alt="Screen recording: selecting Convex Hull, asking the Wendao AI learning agent to explain it, asking a follow-up, and opening the course sources">
</p>

<p align="center"><sub>Full video: <a href="https://github.com/deng-group/wendao/blob/main/docs/assets/demo/ai_learning_agent_demo.mp4">ai_learning_agent_demo.mp4</a></sub></p>

### Chat widget on your course website

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/widget_chat.png" width="52%" alt="The Wendao widget on a course page: an answer about convex hulls with links to the course pages it used">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/demo/widget_graph_dark.png" width="46%" alt="The widget's Graph tab in dark mode: the course knowledge graph, chapters and the concepts that connect them">
</p>

## Install

You need Python 3.11 or newer.

```bash
pip install wendao
```

Students don't install anything: they use your website, the chat widget on your course pages, or one HTML file.

## Quick start

```bash
wendao init my-course        # a new workspace with a notes/ folder
# copy your notes (Markdown, notebooks, PDF, slides, Word, LaTeX) into my-course/notes/
cd my-course
# choose a model under [model] in wendao.toml, and put its API key in .env
wendao build                 # graph, concepts, and search index
wendao doctor                # checks everything and says how to fix problems
wendao serve                 # opens the course website at http://127.0.0.1:5057/
```

No model yet? Everything except the AI answers still works, and `wendao ask --search-only "..."` shows what search
finds. To try the example course: `git clone https://github.com/deng-group/wendao.git`, then
`cd wendao/examples/mle4217_5219` and run `wendao serve --no-check` (it is already built; for AI answers, set your own
model under `[model]` in its `wendao.toml`).

The [teacher guide](https://github.com/deng-group/wendao/blob/main/docs/GUIDE.md) explains each step and setting.

## Share it with students

| Way | How | AI answers |
| --- | --- | --- |
| One HTML file | `wendao export`, then share `build/<course>.html`; it opens by double-click | needs a server |
| Website | put Wendao on a server ([server setup](https://github.com/deng-group/wendao/blob/main/deploy/DEPLOYMENT.md)) and share the link | yes |
| Chat widget | `wendao widget install _build/html --api https://...` on your built course website | yes |

You decide how the AI answers: write your own instructions in `AGENTS.md`, and with `assignment_help = "hints"` it
helps with graded work without solving it. You decide who pays for AI answers: your key (with a daily limit per student and optional sign-in), each student's
own key, or either. See [How students use AI](https://github.com/deng-group/wendao/blob/main/docs/GUIDE.md#7-how-students-use-ai).

## Commands

| Command | What it does |
| --- | --- |
| `wendao init my-course` | Create a new workspace with starter files |
| `wendao build` | Read your notes; build the knowledge graph, concepts, and search index (only what changed) |
| `wendao doctor` | Check settings, notes, build, test questions, and model, and say how to fix problems |
| `wendao ask "question"` | Ask a question (`--search-only`: see what search finds, without a model) |
| `wendao serve` | Run the course website (`--widget`: the API for the chat widget) |
| `wendao export` | Put the knowledge graph in one HTML file |
| `wendao widget install _build/html` | Add the chat widget to every page of a built course website |
| `wendao feedback` | See which answers students marked as not helpful, and why |
| `wendao students` | See your class list and how many questions each student asked |

Run any command with `--help` to see its options.

## How it works

<p align="center">
  <img src="https://raw.githubusercontent.com/deng-group/wendao/main/docs/assets/wendao_workflow.png" width="100%" alt="Wendao workflow: course materials are extracted and chunked, feed a retrieval-augmented generation pipeline and an LLM, and surface as an interactive knowledge graph and an AI learning agent">
</p>

Wendao splits your notes into small pieces, links chapters and concepts that appear together, and, for each question,
finds the most relevant pieces with keyword and meaning-based search. If they don't really answer the question, it says
so instead of guessing. Otherwise a language model (Claude, GPT, Gemini, DeepSeek, or a local model) writes the answer
from them. No training or GPU is needed.

## Citation

Wendao is developed by the [Deng group](https://github.com/deng-group) in the Department of Materials Science and Engineering at the
National University of Singapore, for MLE4217/5219 Materials Informatics. If you use Wendao or adapt it for your own course, please
cite this repository:

```bibtex
@software{wendao2026,
  author = {Deng, Yanhao and Deng, Zeyu},
  title  = {Wendao: an AI learning companion with an interactive course knowledge graph},
  year   = {2026},
  url    = {https://github.com/deng-group/wendao}
}
```

## License

Wendao is free software under the [GNU General Public License v3.0 or later](https://github.com/deng-group/wendao/blob/main/LICENSE). You can use, study, change, and share it.
If you share a changed version, you must share its source code under the same license.
