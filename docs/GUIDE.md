# Teacher guide

Everything you need to run Wendao for your own course. For a short overview, see the [README](../README.md).

- [1. Create a workspace](#1-create-a-workspace)
- [2. Describe your course](#2-describe-your-course)
- [3. Build](#3-build)
- [4. Connect a model](#4-connect-a-model)
- [5. Check everything with `wendao doctor`](#5-check-everything-with-wendao-doctor)
- [6. Share it with your students](#6-share-it-with-your-students)
- [7. How students use AI](#7-how-students-use-ai)
- [8. Chat widget on your course website](#8-chat-widget-on-your-course-website)

## 1. Create a workspace

Your course lives in its own folder, called a **workspace**:

```text
my-course/
  notes/            your lecture notes (or point to a folder elsewhere)
  wendao.toml       course name, website, chapters, which model to use, and how students use AI
  concepts.json     the concepts to show in the knowledge graph
  questions.json    test questions
  .env              your API key (never committed)
  build/            files Wendao creates: chunks, graph, search index, reports
```

```bash
wendao init my-course                              # creates my-course/ with a notes/ folder
wendao init my-course --source ~/teaching/notes    # or use notes you already have
cd my-course
```

Run `wendao` commands anywhere inside the workspace. Wendao finds `wendao.toml` by itself.

Wendao reads these kinds of files:

| Type | Files | Answers point to |
| --- | --- | --- |
| Markdown / MyST pages | `.md` | the page |
| Jupyter notebooks | `.ipynb` | the notebook |
| PDF (lecture notes, papers) | `.pdf` | the page number |
| PowerPoint slides | `.pptx` (including speaker notes and tables) | the slide number |
| Word documents | `.docx` | the document |
| LaTeX | `.tex` | the document |
| Web pages and plain text | `.html`, `.txt` | the file |

Each top-level folder becomes a chapter. Scanned PDFs (pictures of pages) have no text to read, so run them through OCR
first. To read only some types, set `file_types` under `[source]`, for example `file_types = ["pdf", "pptx"]`.

## 2. Describe your course

Open `wendao.toml` and fill in the course name and website. The website lets answers and the graph link to the right
page. The file explains each setting. The most useful ones are:

- `[course] style`: the look of the graph website and the chat widget. `"modern"` (the default: indigo and violet) or
  `"economist"` (red and navy, like The Economist). Students can still switch light and dark mode.
- `[[chapters]]`: names, order, and visibility for your chapter folders. You don't have to list them: Wendao names each
  chapter after its index page (or its folder) and keeps the order of your notes.
- `[search] aliases`: spellings students might type, such as `{ "convexhull" = "convex hull" }`.
- `[graph] bridge_stop_concepts`: very common concepts, such as `python`, that shouldn't link chapters on their own.

**Concepts.** The knowledge graph shows the concepts listed in `concepts.json`. You don't have to write them by hand:
the first `wendao build` with a model set up (step 4) asks the model to find the concepts in each chapter (one
request per chapter) and adds them. It keeps only concepts your notes really use, and saves the list from before as
`concepts.json.bak`. Delete any you don't want and run `wendao graph`. After adding notes, run
`wendao build --concepts` to look for new ones (it only adds), or `--no-concepts` to skip this.

To write a concept by hand, give the words to look for in your notes:

```json
{"id": "convex-hull", "label": "Convex Hull", "category": "thermodynamics", "aliases": ["convex hull", "convex hulls"]}
```

When a student clicks a concept, the graph shows a short introduction: the sentence in your notes that best defines it,
with a link to that page.

**Guided path.** `wendao build` also makes a path through the course: each chapter in your teaching order, followed by
up to three key concepts it introduces. A concept is introduced in the chapter that uses it most, not where it is first
mentioned in passing, and concepts under `[graph] bridge_stop_concepts` are left out. Students start it with the
guided path button (the route icon at the top) and go step by step (Back / Next, or the arrow keys); each step shows the introduction, where
the concept comes up again, and links to explain it with AI or read the course page. The browser remembers where they
stopped. To send students straight into it, link to the graph website with `?path` at the end.

A chapter step shows what the chapter's index page (`index.md` or `README.md`) says: its overview, from a section such
as *Content*, *Overview* or *Introduction*, and what students will learn, from a list under *Learning Objectives*,
*Outcomes* or *Goals*, plus links to the chapter's pages. Slides, lecture recordings and their login details are
never shown. A concept step links to the pages of its chapter that use it most. Chapters with
`show = "secondary"` or `"hidden"` are not part of the path.

See [`examples/mle4217_5219/`](../examples/mle4217_5219/) for a complete example.

## 3. Build

```bash
wendao build
```

This reads your notes, builds the knowledge graph, and builds the search index. The first run downloads the search
model. Run it again whenever you change your notes or settings, or after upgrading Wendao: it only redoes what
changed. Unchanged notes aren't read again, the graph is kept when its inputs are the same, and the search index only
processes new or edited text. `wendao build --force` redoes everything.

Check the search results before you connect a model:

```bash
wendao ask --search-only "What is a convex hull?"
```

This shows the pages search found and whether Wendao thinks it can answer.

Wendao runs its search model on the CPU, so no graphics card is needed. For a very large course, you can build on a
GPU with `pip install "wendao[gpu]"`. Both give the same results.

## 4. Connect a model

Wendao works with three kinds of API. Choose one under `[model]` in `wendao.toml` and put its key in `.env`:

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

Settings in your shell (for example `export ANTHROPIC_MODEL=...` in `.bashrc`) win over `.env`, and `.env` wins over
`wendao.toml`.

A small, cheap model is usually enough, because Wendao gives it the relevant course text. Answers use a temperature of
0.2. Some models, such as OpenAI's reasoning models, don't accept one; set `temperature = "none"` under `[model]`.
Replies can be up to 8192 tokens long. Models that think before answering (such as DeepSeek) use part of that for
thinking; if answers stop with "its thinking used up the token limit", raise `max_tokens` under `[model]`.

## 5. Check everything with `wendao doctor`

```bash
wendao doctor
```

This checks your settings, your notes, the build, your test questions, and the model, and tells you how to fix
anything that's wrong. It sends the model one tiny request; add `--no-model` to skip that.

**Test questions.** Add real questions from your course to `questions.json`, and say what Wendao should do:

```json
{
  "id": "convex_hull",
  "query": "What is a convex hull?",
  "expected_status": "answerable",
  "expected_files": ["high_throughput/thermodynamics.md"]
}
```

`expected_status` is one of `answerable`, `needs_time_context` (schedule questions), `needs_clarification` (too broad),
`weak_evidence` (not enough in the notes), or `out_of_scope`. `wendao doctor` checks that search finds the right pages
and makes the right decision for each one (no model needed). Reports are saved in `build/reports/`.

- `wendao doctor --add-questions` first lets your model write test questions from your notes (one request per chapter).
  Read them, and fix or delete any that are off.
- `wendao doctor --real` also asks the model every test question, so you can read the answers (uses API credits).

## 6. Share it with your students

Try it yourself first: `wendao serve` opens the course website on your computer at http://127.0.0.1:5057/.

Then share it in one or more ways:

- **One HTML file (nothing to install).** Run `wendao export`. It writes `build/<course-name>.html` (about 0.5 MB) with
  the whole knowledge graph inside: search, connections, concept introductions, and links to your course pages.
  Students open it by double-clicking, or you put it on any website (for example next to your course on Cloudflare
  Pages or GitHub Pages). The AI needs a server; with `wendao export --online https://<your Wendao server>/graph/`,
  its **Ask AI** button opens the online version.
- **Website, with the AI.** Put Wendao on a server with [`deploy/DEPLOYMENT.md`](../deploy/DEPLOYMENT.md) and send your
  students the link.
- **Chat widget on your course website.** See [section 8](#8-chat-widget-on-your-course-website).

## 7. How students use AI

Decide under `[student]` in `wendao.toml`:

```toml
[student]
ai = "teacher"            # "teacher", "student", or "either"
questions_per_day = 50    # per student, on your key (0 = no limit)
```

| `ai` | Students ask with | Who pays |
| --- | --- | --- |
| `"teacher"` | your model and key. The key stays on your server; students never see it. | you, with a daily limit per student |
| `"student"` | their own API key, entered in the app's **AI** settings | each student |
| `"either"` | your model by default; students may add their own key instead | you, unless a student adds a key |

A student's own key is saved only in their browser and sent with each question. It's never stored on any server.
Students can also point to their own model server (for example DeepSeek, OpenRouter, or a university server). This
must be a public `https://` address, so nobody can use your server to reach private machines.

**Sign-in and daily limits per student.** To limit questions per student (not per network), give Wendao your class
list. Save it as `students.csv` with an `email` column (export it from your learning platform; optional columns: `name`,
and `limit` for a personal daily limit), then add it under `[student]`:

```toml
roster = "students.csv"
questions_per_day = 30
```

Students then sign in with their email before using your course AI; emails not on the list can still browse the graph,
but can't use your AI. See who asked how much with `wendao students` (today) or `wendao students --all`. Wendao keeps
only emails and daily question counts (in `usage.db`, next to `wendao.toml`), not the questions. The class list and
`usage.db` are personal data, so keep them out of git (new workspaces already ignore them).

Signing in uses the email alone, so someone who knows a classmate's email could use that classmate's questions for the
day, but never more than that.

## 8. Chat widget on your course website

If your course already has a website built with MyST, Jupyter Book, or Sphinx, add Wendao to every page after each
build:

```bash
make web                                                        # or however you build your site
wendao widget install _build/html --api https://<your Wendao server>
```

Students get a Wendao button at the bottom-right of each page. It opens a chat window that they can resize or enlarge,
and that follows the site's light or dark theme. A **Graph** tab shows the course knowledge graph, opened on the page
they are reading. Students can also highlight text on a page and click **Explain** or **Ask about it**, or **Search**
to look it up on the web. To use another search engine:

```toml
[widget]
web_search = "scholar"   # or bing, duckduckgo, baidu, wikipedia, semantic_scholar, arxiv, youtube,
                         # or your own address with {q} for the text; "" hides the Search button
```

Under each answer, students can tap **Simpler**, **Example**, or **Test me** (the AI asks them a short question and
then checks their answer), and mark the answer 👍 or 👎 with an optional note. `wendao feedback` shows you the answers
marked not helpful. Feedback is stored on your server in `usage.db`, without names or addresses. Formulas are drawn
with [KaTeX](https://katex.org), loaded from the jsDelivr CDN; if it can't load, formulas show as plain text.

The widget needs a Wendao server behind it (`wendao serve --widget`, or the server in
[`deploy/DEPLOYMENT.md`](../deploy/DEPLOYMENT.md)). Without `--api`, it uses the same website address under `/api`.
Try it on your computer with `wendao serve --widget --site _build/html`. `wendao widget remove _build/html` takes it
out again.
