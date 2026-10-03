/* Wendao course-website widget: chat with the course AI, and explore the knowledge graph around this page.
 *
 * Added to every page of a built course website by `wendao widget install`. Settings come from the
 * <script> tag: data-api (address of the Wendao widget API; default: this site, or 127.0.0.1:5055 when
 * testing locally) and data-version.
 */
(function () {
  "use strict";

  const SCRIPT = document.currentScript;
  const VERSION = (SCRIPT && SCRIPT.dataset.version) || "dev";
  if (window.WENDAO_WIDGET_VERSION === VERSION) return;
  window.WENDAO_WIDGET_VERSION = VERSION;

  const LOCAL = ["127.0.0.1", "localhost"].includes(window.location.hostname);
  const API = ((SCRIPT && SCRIPT.dataset.api) || (LOCAL ? "http://127.0.0.1:5055" : window.location.origin)).replace(/\/$/, "");
  const ASSETS = SCRIPT && SCRIPT.src ? new URL(".", SCRIPT.src).href : "_wendao/";
  const KEY = (name) => `wendao-widget:${API}:${name}`;
  const MEMORY_KEY = KEY("memory");
  const OPEN_KEY = KEY("open");
  const MAX_MEMORY = 6;
  const MAX_SELECTION = 1500; // characters of highlighted page text sent with a question

  const MARK = `<svg viewBox="0 0 128 128" aria-hidden="true"><defs><linearGradient id="wdw-tile" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1e1b4b"/><stop offset="1" stop-color="#1d4ed8"/></linearGradient></defs>
    <rect x="0" y="0" width="128" height="128" rx="30" fill="url(#wdw-tile)"/>
    <path d="M44 50 C44 35 53 27 64 27 C77 27 86 36 86 47 C86 58 78 63 71 67 C66 70 64 73 64 81" fill="none"
      stroke="#fff" stroke-width="10" stroke-linecap="butt" stroke-linejoin="round"/><circle cx="64" cy="98" r="7" fill="#38bdf8"/></svg>`;
  const ICONS = {
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    expand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    shrink: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></svg>',
    up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>',
    key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 8.2-8.2M17 6l2 2M15 8l2 2"/></svg>',
  };

  // ---------- small helpers ----------
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
  const escapeRegExp = (text) => String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const readJson = (storage, key, fallback) => { try { return JSON.parse(storage.getItem(key) || "null") ?? fallback; } catch { return fallback; } };
  const seconds = (ms) => (ms < 10000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 1000)} s`);

  // ---------- theme: follow the site (MyST, Jupyter Book, Sphinx) or the system ----------
  function siteIsDark() {
    const html = document.documentElement;
    const body = document.body;
    if (html.classList.contains("dark") || (body && body.classList.contains("dark"))) return true;
    const theme = html.dataset.theme || html.getAttribute("data-mode") || (body && body.dataset.theme);
    if (theme === "dark") return true;
    if (theme === "light" || html.classList.contains("light")) return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  // ---------- answer formatting (Markdown subset with course-page links) ----------
  const COURSE_PATH = "([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+\\.(?:md|ipynb))";
  const WRAPPED_COURSE_PATH = `\`?\\s*\\[?\\s*${COURSE_PATH}\\s*\\]?\\s*\`?`;
  let sourceUrls = {};

  function coursePathToHref(path) {
    if (sourceUrls[path]) return sourceUrls[path];
    const parts = path.replace(/^\/+/, "").replace(/\.(md|ipynb)$/i, "").split("/").filter(Boolean)
      .map((part) => part.replace(/_/g, "-").toLowerCase());
    if (parts[parts.length - 1] === "index") parts.pop();
    return `/${parts.join("/")}`;
  }

  function normalizeCourseCitations(text) {
    const bold = new RegExp(`\\*\\*([^*\\n]+)\\*\\*\\s*\\(\\s*${WRAPPED_COURSE_PATH}\\s*\\)`, "g");
    const plain = new RegExp(`(\\s(?:and|or|in|from|see|source|sources)\\s+)([^()[\\]\\n]{2,100}?)\\s*\\(\\s*${WRAPPED_COURSE_PATH}\\s*\\)`, "g");
    return String(text)
      .replace(bold, (_m, title, path) => `[${title.trim()}](${path})`)
      .replace(plain, (_m, prefix, title, path) => `${prefix}[${title.trim()}](${path})`);
  }

  function normalizeSourceCitations(text, sources) {
    let out = String(text);
    for (const source of [...(sources || [])].sort((a, b) => String(b.title || "").length - String(a.title || "").length)) {
      const title = String(source.title || "").trim();
      const path = String(source.file_path || "").trim();
      if (!title || !path) continue;
      const pathPattern = `\`?\\s*\\[?\\s*${escapeRegExp(path)}\\s*\\]?\\s*\`?`;
      const replacement = `[${title}](${path})`;
      out = out.replace(new RegExp(`\\*\\*${escapeRegExp(title)}\\*\\*\\s*\\(\\s*${pathPattern}\\s*\\)`, "g"), replacement);
      out = out.replace(new RegExp(`${escapeRegExp(title)}\\s*\\(\\s*${pathPattern}\\s*\\)`, "g"), replacement);
    }
    return normalizeCourseCitations(out);
  }

  function renderInline(text) {
    let html = escapeHtml(normalizeCourseCitations(text));
    html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, href) => {
      if (/^https?:/i.test(href)) return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
      if (/\.(md|ipynb)$/i.test(href)) return `<a href="${escapeHtml(coursePathToHref(href))}">${label}</a>`;
      return label;
    });
    html = html.split(/(<[^>]+>)/g).map((part) => (part.startsWith("<") ? part
      : part.replace(/\b[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)+\.(?:md|ipynb)\b/g, (path) => `<a href="${escapeHtml(coursePathToHref(path))}">${path}</a>`))).join("");
    return html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
  }

  function renderBlocks(text) {
    const blocks = [];
    let paragraph = [];
    let list = null;
    const flushParagraph = () => { if (paragraph.length) { blocks.push(`<p>${renderInline(paragraph.join("\n").trim())}</p>`); paragraph = []; } };
    const flushList = () => {
      if (!list) return;
      const items = list.items.map((item) => `<li>${renderInline(item.text)}${item.children.length ? `<ul>${item.children.map((c) => `<li>${renderInline(c)}</li>`).join("")}</ul>` : ""}</li>`).join("");
      blocks.push(list.type === "ol" ? `<ol start="${list.start}">${items}</ol>` : `<ul>${items}</ul>`);
      list = null;
    };
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line) { flushParagraph(); flushList(); continue; }
      const heading = line.match(/^(#{2,4})\s+(.+)$/);
      if (heading) { flushParagraph(); flushList(); blocks.push(`<h${heading[1].length}>${renderInline(heading[2])}</h${heading[1].length}>`); continue; }
      const bullet = raw.match(/^(\s*)[-*]\s+(.+)$/);
      if (bullet) {
        if (list && list.type === "ol" && bullet[1].length) { list.items[list.items.length - 1].children.push(bullet[2]); continue; }
        flushParagraph(); if (list && list.type !== "ul") flushList();
        list = list || { type: "ul", items: [] }; list.items.push({ text: bullet[2], children: [] }); continue;
      }
      const ordered = raw.match(/^\s*(\d+)[.)]\s+(.+)$/);
      if (ordered) {
        flushParagraph(); if (list && list.type !== "ol") flushList();
        list = list || { type: "ol", start: Number(ordered[1]), items: [] }; list.items.push({ text: ordered[2], children: [] }); continue;
      }
      flushList(); paragraph.push(line);
    }
    flushParagraph(); flushList();
    return blocks.join("");
  }

  // Formulas ($...$, $$...$$, \(...\), \[...\]) are set aside before the Markdown is rendered, then drawn by KaTeX.
  // "$10k" or "$5 and $10" stay text: an inline formula can't start or end with a space or be followed by a digit.
  const MATH = /(```[\s\S]*?```|`[^`\n]*`)|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)/g;

  function protectMath(text, formulas) {
    return text.replace(MATH, (match, code, display, display2, inline, inline2) => {
      if (code) return match;
      const tex = display || display2 || inline || inline2;
      formulas.push({ tex, display: Boolean(display || display2), source: match });
      return `\uE000${formulas.length - 1}\uE001`;
    });
  }

  function restoreMath(html, formulas) {
    return html.replace(/\uE000(\d+)\uE001/g, (_m, index) => {
      const formula = formulas[Number(index)];
      return `<span class="wdw-math${formula.display ? " display" : ""}" data-tex="${escapeHtml(formula.tex)}">${escapeHtml(formula.source)}</span>`;
    });
  }

  let katexLoading = null;
  function loadKatex() {
    if (window.katex) return Promise.resolve(window.katex);
    if (katexLoading) return katexLoading;
    const KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/";
    katexLoading = new Promise((resolve, reject) => {
      const style = el("link");
      Object.assign(style, { rel: "stylesheet", href: `${KATEX}katex.min.css`, crossOrigin: "anonymous",
        integrity: "sha384-5TcZemv2l/9On385z///+d7MSYlvIEw9FuZTIdZ14vJLqWphw7e7ZPuOiCHJcFCP" });
      document.head.appendChild(style);
      const script = el("script");
      Object.assign(script, { src: `${KATEX}katex.min.js`, crossOrigin: "anonymous", async: true,
        integrity: "sha384-cMkvdD8LoxVzGF/RPUKAcvmm49FQ0oxwDF3BGKtDXcEc+T1b2N+teh/OJfpU0jr6" });
      script.onload = () => (window.katex ? resolve(window.katex) : reject(new Error("KaTeX did not load")));
      script.onerror = () => reject(new Error("KaTeX could not be loaded"));
      document.head.appendChild(script);
    });
    return katexLoading;
  }

  function typeset(container) {
    const pending = container.querySelectorAll(".wdw-math:not([data-done])");
    if (!pending.length) return;
    loadKatex().then((katex) => {
      for (const node of container.querySelectorAll(".wdw-math:not([data-done])")) {
        try {
          katex.render(node.dataset.tex, node, { displayMode: node.classList.contains("display"), throwOnError: false });
        } catch { /* keep the formula as text */ }
        node.dataset.done = "1";
      }
    }).catch(() => { /* no KaTeX (offline or blocked): formulas stay readable as text */ });
  }

  function renderMarkdown(source) {
    const formulas = [];
    const text = protectMath(String(source), formulas);
    return restoreMath(renderMarkdownText(text), formulas);
  }

  function renderMarkdownText(text) {
    const pieces = [];
    const pattern = /```([a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) pieces.push(renderBlocks(text.slice(cursor, match.index).trim()));
      pieces.push(`<pre><code>${escapeHtml(match[2].trim())}</code></pre>`);
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) pieces.push(renderBlocks(text.slice(cursor).trim()));
    return pieces.join("");
  }

  // ---------- API ----------
  const state = {
    ai: { mode: "teacher", login_required: false, student_keys: false, needs_student_key: false },
    course: null, graph: false, pageNode: null, history: [], center: null, selected: null, busy: false,
    quote: "", // text the student highlighted on the page, sent with the next question
    quizzing: false, // the last answer asked the student a question: their next message is their answer
    feedback: false, // the server takes thumbs up / down
    // where the Search button looks up highlighted text; the teacher sets it in wendao.toml ([widget] web_search)
    webSearch: { name: "Google", url: "https://www.google.com/search?q={q}" },
  };
  const account = () => readJson(localStorage, KEY("account"), null);
  const ownAi = () => {
    const saved = readJson(localStorage, KEY("ai"), null);
    return state.ai.student_keys && saved && (saved.api_key || saved.base_url) ? saved : null;
  };

  async function getJson(path) {
    const response = await fetch(`${API}${path}`);
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.json();
  }

  async function streamAnswer(query, contextIds, selection, onEvent) {
    const headers = { "Content-Type": "application/json" };
    if (account()) headers.Authorization = `Bearer ${account().token}`;
    const body = { query, short_memory: readJson(sessionStorage, MEMORY_KEY, []), context_node_ids: contextIds || [] };
    if (selection) body.selection = selection;
    if (ownAi()) body.ai = ownAi();
    const response = await fetch(`${API}/api/answer/stream`, { method: "POST", headers, body: JSON.stringify(body) });
    if (!response.ok || !response.body) {
      const payload = await response.json().catch(() => ({}));
      throw Object.assign(new Error(payload.message || `Request failed (${response.status})`), { code: payload.error });
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done = null;
    const consume = (line) => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === "error" || event.ok === false) throw Object.assign(new Error(event.message || "The course AI could not answer."), { code: event.error });
      onEvent(event);
      if (event.type === "done") done = event;
    };
    for (;;) {
      const { value, done: finished } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !finished });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.forEach(consume);
      if (finished) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!done) throw new Error("The answer ended before it was complete.");
    return done;
  }

  // ---------- build the widget ----------
  let root;
  let ui = {};

  function build() {
    root = el("div", "wdw-root");
    root.dataset.theme = siteIsDark() ? "dark" : "light";
    root.innerHTML = `
      <button class="wdw-launcher" type="button" aria-expanded="false" aria-label="Open Wendao, the course AI">${MARK}</button>
      <section class="wdw-panel" role="dialog" aria-label="Wendao course AI" hidden>
        <button class="wdw-resize" data-ref="resize" type="button" aria-label="Drag to resize" title="Drag to resize"></button>
        <header class="wdw-header">
          <div class="wdw-brand">${MARK}<div><strong>Wendao</strong><small data-ref="course">Course AI</small></div></div>
          <div class="wdw-actions">
            <button class="wdw-icon-button wdw-account" data-ref="accountButton" type="button" hidden>Sign in</button>
            <button class="wdw-icon-button" data-ref="keyButton" type="button" title="Use my own AI key" aria-label="Use my own AI key" hidden>${ICONS.key}</button>
            <button class="wdw-icon-button" data-ref="clear" type="button" title="New conversation">Clear</button>
            <button class="wdw-icon-button wdw-maximize" data-ref="maximize" type="button" title="Larger window" aria-label="Larger window">${ICONS.expand}</button>
            <button class="wdw-icon-button" data-ref="close" type="button" title="Close" aria-label="Close">${ICONS.close}</button>
          </div>
        </header>
        <nav class="wdw-tabs" role="tablist">
          <button class="wdw-tab" role="tab" data-tab="chat" aria-selected="true" type="button">Chat</button>
          <button class="wdw-tab" role="tab" data-tab="graph" aria-selected="false" type="button" data-ref="graphTab">Graph</button>
        </nav>

        <div class="wdw-view" data-view="chat">
          <div class="wdw-messages" data-ref="messages" aria-live="polite"></div>
          <div class="wdw-chips" data-ref="chips" hidden></div>
          <form class="wdw-card" data-ref="signIn" hidden>
            <h3>Sign in</h3>
            <p>Use the email your course knows you by. It counts your daily questions.</p>
            <label>Email <input type="email" data-ref="email" autocomplete="email" placeholder="you@university.edu"></label>
            <p class="wdw-form-error" data-ref="signInError" hidden></p>
            <div class="wdw-row"><button class="wdw-button primary" type="submit">Sign in</button></div>
          </form>
          <form class="wdw-card" data-ref="keyForm" hidden>
            <h3>Your own AI key</h3>
            <p data-ref="keyNote">Saved only in this browser.</p>
            <label>Provider <select data-ref="provider"><option value="openai">OpenAI-compatible</option><option value="anthropic">Anthropic</option><option value="gemini">Google Gemini</option></select></label>
            <label>Model <input data-ref="model" placeholder="e.g. gpt-4.1-mini" autocomplete="off"></label>
            <label>Server address (optional) <input data-ref="baseUrl" placeholder="https://..." autocomplete="off"></label>
            <label>API key <input type="password" data-ref="apiKey" autocomplete="off"></label>
            <div class="wdw-row"><button class="wdw-button quiet" type="button" data-ref="forgetKey">Forget</button><button class="wdw-button" type="button" data-ref="cancelKey">Cancel</button><button class="wdw-button primary" type="submit">Save</button></div>
          </form>
          <div class="wdw-quote" data-ref="quote" hidden>
            <span data-ref="quoteText"></span>
            <button class="wdw-icon-button" data-ref="quoteClear" type="button" title="Don't ask about this text" aria-label="Don't ask about this text">${ICONS.close}</button>
          </div>
          <form class="wdw-composer" data-ref="composer">
            <textarea data-ref="question" rows="1" placeholder="Ask about this course…" aria-label="Ask a question"></textarea>
            <button class="wdw-send" type="submit" aria-label="Send">${ICONS.send}</button>
          </form>
        </div>

        <div class="wdw-view" data-view="graph" hidden>
          <div class="wdw-graph-bar">
            <button class="wdw-icon-button" data-ref="back" type="button" title="Back" aria-label="Back" disabled>←</button>
            <strong data-ref="graphTitle">Knowledge graph</strong>
            <button class="wdw-icon-button" data-ref="here" type="button" title="Back to this page">This page</button>
          </div>
          <div class="wdw-legend"><span class="chapter"><i></i>Chapter</span><span class="topic"><i></i>Page</span><span class="keyword"><i></i>Concept</span></div>
          <div class="wdw-graph" data-ref="graph"><div class="wdw-graph-empty" data-ref="graphEmpty">Loading the knowledge graph…</div></div>
          <div class="wdw-node-card" data-ref="nodeCard" hidden>
            <small data-ref="nodeKind"></small><strong data-ref="nodeLabel"></strong>
            <div class="wdw-row">
              <button class="wdw-button primary" type="button" data-ref="explain">Explain</button>
              <button class="wdw-button" type="button" data-ref="recenter">Explore around it</button>
              <a class="wdw-button" data-ref="openPage" hidden>Open page</a>
            </div>
          </div>
        </div>
      </section>
      <div class="wdw-pick" data-ref="pick" role="toolbar" aria-label="Ask Wendao about the highlighted text" hidden>
        <span class="wdw-pick-mark">${MARK}</span>
        <button type="button" data-ref="pickExplain">Explain</button>
        <button type="button" data-ref="pickAsk">Ask about it</button>
        <button type="button" class="wdw-pick-search" data-ref="pickSearch">${ICONS.search}<span>Search</span></button>
      </div>`;
    document.body.appendChild(root);
    ui = { launcher: root.querySelector(".wdw-launcher"), panel: root.querySelector(".wdw-panel") };
    root.querySelectorAll("[data-ref]").forEach((node) => { ui[node.dataset.ref] = node; });
    wire();
  }

  // ---------- chat ----------
  function addMessage(role, text, meta, kind, quote) {
    const wrapper = el("article", `wdw-message ${role} ${kind || ""}`.trim());
    if (quote) wrapper.appendChild(el("blockquote", "wdw-said", shorten(quote, 220)));
    const bubble = el("div", "wdw-bubble");
    if (role === "assistant") bubble.innerHTML = renderMarkdown(text); else bubble.textContent = text;
    wrapper.appendChild(bubble);
    if (meta) wrapper.appendChild(el("div", "wdw-meta", meta));
    ui.messages.appendChild(wrapper);
    typeset(wrapper);
    ui.messages.scrollTop = ui.messages.scrollHeight;
    return wrapper;
  }

  function updateMessage(wrapper, text, meta, kind, sources) {
    wrapper.className = `wdw-message assistant ${kind || ""}`.trim();
    wrapper.querySelector(".wdw-bubble").innerHTML = renderMarkdown(text);
    wrapper.querySelectorAll(".wdw-meta, .wdw-sources").forEach((node) => node.remove());
    if (sources && sources.length) {
      const list = el("div", "wdw-sources");
      for (const source of sources.slice(0, 4)) {
        const label = [source.title || source.file_path, source.location].filter(Boolean).join(" · ");
        const item = source.url ? Object.assign(el("a", "", label), { href: source.url }) : el("span", "", label);
        item.title = source.file_path || label;
        list.appendChild(item);
      }
      wrapper.appendChild(list);
    }
    if (meta) wrapper.appendChild(el("div", "wdw-meta", meta));
    typeset(wrapper);
    ui.messages.scrollTop = ui.messages.scrollHeight;
  }

  function greet() {
    ui.messages.innerHTML = "";
    const name = state.course ? state.course.name : "this course";
    addMessage("assistant", `Ask me anything about ${name}. I answer from the course materials and link to the pages I used.`);
    showChips();
  }

  function restore() {
    const memory = readJson(sessionStorage, MEMORY_KEY, []);
    if (!memory.length) { greet(); return; }
    ui.messages.innerHTML = "";
    for (const item of memory) addMessage(item.role, item.shown || item.content, item.meta || "", "", item.quote);
  }

  function needsSetup() {
    if (state.ai.needs_student_key && !ownAi()) { openKeyForm("This course asks you to use your own AI key."); return true; }
    if (state.ai.login_required && !account() && !ownAi()) { ui.signIn.hidden = false; ui.email.focus(); return true; }
    return false;
  }

  async function ask(query, contextIds, sendAs) {
    query = query.trim();
    if (!query || state.busy || needsSetup()) return;
    const quote = state.quote;
    setQuote("");
    const sent = sendAs || query;
    state.quizzing = false;
    ui.question.placeholder = "Ask about this course…";
    ui.messages.querySelectorAll(".wdw-followups").forEach((node) => node.remove()); // only the latest answer has them
    // With highlighted text, the page itself is useful context too.
    if (quote && state.pageNode) contextIds = [...(contextIds || []), state.pageNode.id];
    state.busy = true;
    ui.composer.querySelector("button").disabled = true;
    ui.chips.hidden = true;
    addMessage("user", query, "", "", quote);
    const pending = addMessage("assistant", "Thinking…", "Generating… 0.0 s", "pending");
    const started = performance.now();
    const timer = setInterval(() => updateMessage(pending, streamed || "Thinking…", `Generating… ${seconds(performance.now() - started)}`, "pending", sources), 250);
    let streamed = "";
    let sources = [];
    try {
      const result = await streamAnswer(sent, contextIds, quote, (event) => {
        if (event.sources) { sources = event.sources; sources.forEach((s) => { if (s.url) sourceUrls[s.file_path] = s.url; }); }
        if (event.type === "delta") streamed += event.text || "";
      });
      const answer = normalizeSourceCitations(result.answer, result.sources || sources);
      const meta = `Answered in ${seconds(performance.now() - started)}`;
      clearInterval(timer);
      updateMessage(pending, answer, meta, "", result.sources || sources);
      const askedText = quote ? `About "${shorten(quote, 300)}": ${sent}` : sent;
      addAnswerTools(pending, { question: askedText, answer, sources: result.sources || sources, answered: result.llm_action === "generate_answer" });
      if (sent === FOLLOW_UPS.quiz.ask && result.llm_action === "generate_answer") {
        state.quizzing = true;
        ui.question.placeholder = "Type your answer…";
      }
      const memory = readJson(sessionStorage, MEMORY_KEY, []);
      // `content` is what the AI sees in later turns; `shown` and `quote` redraw the chat after a reload.
      const asked = { role: "user", content: askedText, ...(askedText !== query ? { shown: query } : {}), ...(quote ? { quote } : {}) };
      memory.push(asked, { role: "assistant", content: answer, meta });
      sessionStorage.setItem(MEMORY_KEY, JSON.stringify(memory.slice(-MAX_MEMORY)));
    } catch (error) {
      clearInterval(timer);
      updateMessage(pending, streamed ? `${streamed}\n\n${error.message}` : error.message, `Stopped after ${seconds(performance.now() - started)}`, "error");
      if (error.code === "LoginRequired") { localStorage.removeItem(KEY("account")); updateAccount(); ui.signIn.hidden = false; }
    } finally {
      state.busy = false;
      ui.composer.querySelector("button").disabled = false;
      ui.question.focus();
    }
  }

  // ---------- under each answer: one-tap follow-ups, and thumbs up / down for the teacher ----------
  const FOLLOW_UPS = {
    // label: the button; shown: what appears in the chat; ask: what the AI gets
    simpler: { label: "Simpler", shown: "Explain it more simply", ask: "Explain that again more simply, in a few short sentences." },
    example: { label: "Example", shown: "Give me an example", ask: "Give me a concrete example of that from the course." },
    quiz: { label: "Test me", shown: "Test me on this", ask: "Ask me one short question to check that I understood this. Wait for my answer before explaining." },
  };

  function addAnswerTools(wrapper, item) {
    const bar = el("div", "wdw-answer-tools");
    if (item.answered) {
      const follow = el("div", "wdw-followups");
      for (const choice of Object.values(FOLLOW_UPS)) {
        const button = el("button", "wdw-chip", choice.label);
        button.type = "button";
        button.addEventListener("click", () => ask(choice.shown, [], choice.ask));
        follow.appendChild(button);
      }
      bar.appendChild(follow);
    }
    if (state.feedback) bar.appendChild(ratingButtons(item));
    if (bar.childElementCount) wrapper.appendChild(bar);
    ui.messages.scrollTop = ui.messages.scrollHeight;
  }

  function ratingButtons(item) {
    const rate = el("div", "wdw-rate");
    const send = async (rating, note) => {
      rate.innerHTML = "";
      rate.appendChild(el("span", "wdw-rate-thanks", "Sending…"));
      const body = { rating, note: note || "", question: item.question, answer: item.answer,
        sources: (item.sources || []).map((source) => source.file_path).filter(Boolean), page: window.location.pathname };
      try {
        const response = await fetch(`${API}/api/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const reply = await response.json().catch(() => ({}));
        rate.firstChild.textContent = reply.ok ? "Thanks! Your teacher sees this, without your name." : (reply.message || "Couldn't send that.");
      } catch { rate.firstChild.textContent = "Couldn't send that."; }
    };
    const button = (rating, label) => {
      const node = el("button", "wdw-icon-button");
      Object.assign(node, { type: "button", title: label, innerHTML: ICONS[rating] });
      node.setAttribute("aria-label", label);
      return node;
    };
    const up = button("up", "Helpful");
    const down = button("down", "Not helpful");
    up.addEventListener("click", () => send("up"));
    down.addEventListener("click", () => {
      // Ask what was wrong; the note is optional.
      rate.innerHTML = "";
      const form = el("form", "wdw-rate-form");
      form.innerHTML = '<input maxlength="1000" placeholder="What was wrong? (optional)" aria-label="What was wrong? (optional)"><button class="wdw-button primary" type="submit">Send</button>';
      form.addEventListener("submit", (event) => { event.preventDefault(); send("down", form.querySelector("input").value.trim()); });
      rate.appendChild(form);
      form.querySelector("input").focus();
    });
    rate.append(up, down);
    return rate;
  }

  async function showChips() {
    ui.chips.hidden = true;
    ui.chips.innerHTML = "";
    if (!state.pageNode) return;
    try {
      const view = await getJson(`/api/neighborhood?node=${encodeURIComponent(state.pageNode.id)}`);
      const concepts = view.nodes.filter((node) => node.type === "keyword").slice(0, 5);
      if (!concepts.length || ui.messages.querySelectorAll(".wdw-message.user").length) return;
      ui.chips.appendChild(el("span", "", "On this page:"));
      for (const concept of concepts) {
        const chip = el("button", "wdw-chip", concept.label);
        chip.type = "button";
        chip.addEventListener("click", () => ask(`Explain ${concept.label}.`, [concept.id]));
        ui.chips.appendChild(chip);
      }
      ui.chips.hidden = false;
    } catch { /* the chat still works without the graph */ }
  }

  // ---------- highlighted text on the page ----------
  function shorten(text, length) {
    return text.length > length ? `${text.slice(0, length - 1).trimEnd()}…` : text;
  }

  function setQuote(text) {
    state.quote = text ? text.slice(0, MAX_SELECTION) : "";
    ui.quote.hidden = !state.quote;
    ui.quoteText.textContent = state.quote ? `“${shorten(state.quote, 160)}”` : "";
    ui.question.placeholder = state.quote ? "Ask about the highlighted text…" : "Ask about this course…";
  }

  function pageSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const text = selection.toString().replace(/\s+/g, " ").trim();
    if (text.length < 2) return null;
    const range = selection.getRangeAt(0);
    const start = range.commonAncestorContainer;
    const element = start.nodeType === 1 ? start : start.parentElement;
    if (!element || root.contains(element) || element.closest("input, textarea, [contenteditable]")) return null;
    const lines = range.getClientRects();
    const rect = lines.length ? lines[0] : range.getBoundingClientRect();
    return rect.width || rect.height ? { text, rect, last: lines.length ? lines[lines.length - 1] : rect } : null;
  }

  function placePick() {
    const picked = pageSelection();
    if (!picked) { hidePick(); return; }
    state.picked = picked.text;
    ui.pick.hidden = false;
    const box = ui.pick.getBoundingClientRect();
    // Above the first line; below the last line if there is no room (or on touch screens, where the phone's own menu sits above).
    const touch = window.matchMedia("(pointer: coarse)").matches;
    let top = picked.rect.top - box.height - 8;
    let left = picked.rect.left;
    if (touch || top < 8) { top = picked.last.bottom + 8; left = picked.last.left; }
    ui.pick.style.top = `${Math.min(Math.max(8, top), window.innerHeight - box.height - 8)}px`;
    ui.pick.style.left = `${Math.min(Math.max(8, left), window.innerWidth - box.width - 8)}px`;
  }

  function hidePick() {
    ui.pick.hidden = true;
  }

  // ---------- look the highlighted text up with a search engine ----------
  function updateSearch() {
    const engine = state.webSearch;
    ui.pickSearch.hidden = !engine;
    if (engine) ui.pickSearch.title = `Search ${engine.name || "the web"} for the highlighted text`;
  }

  function searchUrl(engine, text) {
    // Search engines work best with short queries: keep the first 300 characters, ending on a whole word.
    const query = text.length > 300 ? text.slice(0, 300).replace(/\s+\S*$/, "") : text;
    const url = String(engine.url || "").split("{q}").join(encodeURIComponent(query));
    return /^https?:\/\//i.test(url) ? url : null;
  }

  function searchSelection() {
    const url = state.webSearch && state.picked && searchUrl(state.webSearch, state.picked);
    hidePick();
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  }

  function useSelection(explain) {
    const text = state.picked;
    hidePick();
    if (!text) return;
    setQuote(text);
    setOpen(true);
    setTab("chat");
    if (explain) ask("Explain this in simple terms.");
    else ui.question.focus();
  }

  function watchSelection() {
    let dragging = false;
    let timer = null;
    const later = (delay) => { clearTimeout(timer); timer = setTimeout(placePick, delay); };
    document.addEventListener("mousedown", (event) => { if (!ui.pick.contains(event.target)) { dragging = true; hidePick(); } }, true);
    document.addEventListener("mouseup", (event) => { dragging = false; if (!ui.pick.contains(event.target)) later(10); }, true);
    // Keyboard (shift + arrows) and touch selections arrive as selection changes.
    document.addEventListener("selectionchange", () => { if (!dragging) later(window.getSelection().isCollapsed ? 0 : 350); });
    window.addEventListener("scroll", () => { if (!ui.pick.hidden) placePick(); }, { passive: true, capture: true });
    window.addEventListener("resize", () => { if (!ui.pick.hidden) placePick(); });
    ui.pick.addEventListener("mousedown", (event) => event.preventDefault()); // keep the highlight while clicking
    ui.pickExplain.addEventListener("click", () => useSelection(true));
    ui.pickAsk.addEventListener("click", () => useSelection(false));
    ui.pickSearch.addEventListener("click", searchSelection);
    ui.quoteClear.addEventListener("click", () => { setQuote(""); ui.question.focus(); });
  }

  // ---------- sign-in and own key ----------
  function updateAccount() {
    const current = account();
    ui.accountButton.hidden = !state.ai.login_required;
    ui.accountButton.textContent = current ? (current.name || current.email).split(" ")[0] : "Sign in";
    ui.accountButton.title = current ? `Signed in as ${current.email}. Click to sign out.` : "Sign in";
    ui.keyButton.hidden = !state.ai.student_keys;
  }

  function openKeyForm(note) {
    const saved = readJson(localStorage, KEY("ai"), {}) || {};
    ui.provider.value = saved.provider || "openai";
    ui.model.value = saved.model || "";
    ui.baseUrl.value = saved.base_url || "";
    ui.apiKey.value = saved.api_key || "";
    ui.keyNote.textContent = note || "Use your own AI account. The key is saved only in this browser.";
    ui.keyForm.hidden = false;
    ui.signIn.hidden = true;
  }

  // ---------- graph ----------
  const SVG = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => { const node = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v); return node; };
  const KIND = { chapter: "Chapter", topic: "Page", keyword: "Concept" };

  async function showGraph(nodeId, remember) {
    if (!nodeId) {
      ui.graphEmpty.textContent = state.graph ? "This page isn't in the course knowledge graph." : "This course has no knowledge graph yet.";
      ui.graphEmpty.hidden = false;
      return;
    }
    try {
      const view = await getJson(`/api/neighborhood?node=${encodeURIComponent(nodeId)}`);
      if (remember && state.center && state.center.id !== nodeId) state.history.push(state.center.id);
      state.center = view.center;
      state.selected = null;
      ui.nodeCard.hidden = true;
      ui.back.disabled = !state.history.length;
      ui.graphTitle.textContent = view.center.label;
      state.view = view;
      drawGraph(view);
    } catch (error) {
      ui.graphEmpty.textContent = `Couldn't load the graph: ${error.message}`;
      ui.graphEmpty.hidden = false;
    }
  }

  function drawGraph(view) {
    ui.graph.querySelectorAll("svg").forEach((node) => node.remove());
    ui.graphEmpty.hidden = true;
    const box = ui.graph.getBoundingClientRect();
    const width = Math.max(box.width, 280);
    const height = Math.max(box.height, 220);
    const cx = width / 2;
    const cy = height / 2;
    // Neighbours on an oval (wider than tall), grouped by kind. Labels point outward, left or right,
    // and are spaced per side so they never overlap.
    const rx = Math.max(Math.min(width / 2 - 125, 260), 50);
    const ry = Math.max(height / 2 - 26, 60);
    const order = { chapter: 0, topic: 1, keyword: 2 };
    const ring = [...view.nodes].sort((a, b) => order[a.type] - order[b.type]);
    const positions = { [view.center.id]: { x: cx, y: cy } };
    ring.forEach((node, index) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * (index + 0.5)) / Math.max(ring.length, 1);
      const x = cx + rx * Math.cos(angle);
      positions[node.id] = { x, y: cy + ry * Math.sin(angle), right: x >= cx, labelY: 0 };
    });
    for (const right of [true, false]) {
      const side = ring.map((node) => positions[node.id]).filter((pos) => pos.right === right).sort((a, b) => a.y - b.y);
      let previous = -Infinity;
      for (const pos of side) { pos.labelY = Math.max(pos.y + 4, previous + 15); previous = pos.labelY; }
      let limit = height - 6;
      for (const pos of [...side].reverse()) { pos.labelY = Math.min(pos.labelY, limit); limit = pos.labelY - 15; }
    }
    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": `Knowledge graph around ${view.center.label}` });
    const links = svgEl("g");
    for (const link of view.links) {
      const a = positions[link.source]; const b = positions[link.target];
      if (!a || !b) continue;
      const center = link.source === view.center.id || link.target === view.center.id;
      const line = svgEl("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `wdw-link ${center ? "center" : "dim"}` });
      line.dataset.ends = `${link.source} ${link.target}`;
      links.appendChild(line);
    }
    svg.appendChild(links);
    const nodes = svgEl("g");
    for (const node of [view.center, ...ring]) {
      const position = positions[node.id];
      const isCenter = node.id === view.center.id;
      const group = svgEl("g", { class: `wdw-node ${node.type}${isCenter ? " center" : ""}`, tabindex: "0", role: "button", "aria-label": `${KIND[node.type] || ""}: ${node.label}` });
      const size = isCenter ? 11 : node.type === "chapter" ? 8 : node.type === "topic" ? 6.5 : 5.5;
      group.appendChild(svgEl("circle", { cx: position.x, cy: position.y, r: size }));
      const label = svgEl("text", {});
      const short = node.label.length > 22 ? `${node.label.slice(0, 20)}…` : node.label;
      label.textContent = short;
      if (isCenter) { label.setAttribute("x", position.x); label.setAttribute("y", position.y + 26); label.setAttribute("text-anchor", "middle"); }
      else {
        label.setAttribute("x", position.x + (position.right ? 10 : -10));
        label.setAttribute("y", position.labelY);
        label.setAttribute("text-anchor", position.right ? "start" : "end");
      }
      group.appendChild(label);
      const title = svgEl("title"); title.textContent = node.label; group.appendChild(title);
      const select = () => selectNode(node, isCenter);
      group.addEventListener("click", select);
      group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); } });
      group.addEventListener("mouseenter", () => links.querySelectorAll("line").forEach((line) => line.classList.toggle("dim", !line.dataset.ends.includes(node.id))));
      group.addEventListener("mouseleave", () => links.querySelectorAll("line").forEach((line) => line.classList.toggle("dim", !line.classList.contains("center"))));
      nodes.appendChild(group);
    }
    svg.appendChild(nodes);
    ui.graph.appendChild(svg);
  }

  function selectNode(node, isCenter) {
    state.selected = node;
    ui.graph.querySelectorAll(".wdw-node").forEach((group) => group.classList.toggle("selected", group.getAttribute("aria-label") === `${KIND[node.type] || ""}: ${node.label}`));
    ui.nodeKind.textContent = KIND[node.type] || "Node";
    ui.nodeLabel.textContent = node.label;
    ui.recenter.hidden = isCenter;
    ui.openPage.hidden = !node.url;
    if (node.url) ui.openPage.href = node.url;
    ui.nodeCard.hidden = false;
  }

  // ---------- window: open, tabs, resize, maximize ----------
  function setOpen(open) {
    ui.panel.hidden = !open;
    ui.launcher.setAttribute("aria-expanded", String(open));
    sessionStorage.setItem(OPEN_KEY, open ? "1" : "0");
    if (open) { ui.question.focus(); applySize(); }
  }

  function setTab(name) {
    root.querySelectorAll(".wdw-tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === name)));
    root.querySelectorAll(".wdw-view").forEach((view) => { view.hidden = view.dataset.view !== name; });
    if (name === "graph") showGraph(state.center ? state.center.id : state.pageNode && state.pageNode.id, false);
  }

  function applySize() {
    const size = readJson(localStorage, KEY("size"), null);
    const maximized = localStorage.getItem(KEY("max")) === "1";
    if (maximized) {
      ui.panel.style.width = `${Math.min(980, window.innerWidth - 44)}px`;
      ui.panel.style.height = `${window.innerHeight - 112}px`;
    } else if (size) {
      ui.panel.style.width = `${size.w}px`;
      ui.panel.style.height = `${size.h}px`;
    }
    ui.maximize.innerHTML = maximized ? ICONS.shrink : ICONS.expand;
    ui.maximize.title = maximized ? "Smaller window" : "Larger window";
  }

  function startResize(event) {
    event.preventDefault();
    const startX = event.clientX; const startY = event.clientY;
    const rect = ui.panel.getBoundingClientRect();
    const move = (e) => {
      const w = Math.min(Math.max(rect.width + (startX - e.clientX), 320), window.innerWidth - 32);
      const h = Math.min(Math.max(rect.height + (startY - e.clientY), 380), window.innerHeight - 110);
      ui.panel.style.width = `${w}px`; ui.panel.style.height = `${h}px`;
    };
    const stop = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop);
      const r = ui.panel.getBoundingClientRect();
      localStorage.setItem(KEY("size"), JSON.stringify({ w: Math.round(r.width), h: Math.round(r.height) }));
      localStorage.removeItem(KEY("max"));
      applySize();
    };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop);
  }

  function wire() {
    ui.launcher.addEventListener("click", () => setOpen(ui.panel.hidden));
    ui.close.addEventListener("click", () => { setOpen(false); ui.launcher.focus(); });
    ui.clear.addEventListener("click", () => { sessionStorage.removeItem(MEMORY_KEY); greet(); });
    ui.maximize.addEventListener("click", () => {
      localStorage.setItem(KEY("max"), localStorage.getItem(KEY("max")) === "1" ? "0" : "1");
      if (localStorage.getItem(KEY("max")) === "0") { ui.panel.style.width = ""; ui.panel.style.height = ""; }
      applySize();
    });
    ui.resize.addEventListener("pointerdown", startResize);
    root.querySelectorAll(".wdw-tab").forEach((tab) => tab.addEventListener("click", () => setTab(tab.dataset.tab)));
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!ui.pick.hidden) hidePick();
      else if (!ui.panel.hidden) setOpen(false);
    });
    watchSelection();

    ui.composer.addEventListener("submit", (event) => {
      event.preventDefault();
      const q = ui.question.value.trim();
      ui.question.value = "";
      if (q) ask(q, [], state.quizzing ? `My answer to that question: ${q}` : undefined);
    });
    ui.question.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ui.composer.requestSubmit(); } });
    ui.question.addEventListener("input", () => { ui.question.style.height = "auto"; ui.question.style.height = `${Math.min(ui.question.scrollHeight, 140)}px`; });

    ui.signIn.addEventListener("submit", async (event) => {
      event.preventDefault();
      ui.signInError.hidden = true;
      try {
        const response = await fetch(`${API}/api/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: ui.email.value.trim() }) });
        const reply = await response.json();
        if (!reply.ok) throw new Error(reply.message || "Sign-in failed.");
        localStorage.setItem(KEY("account"), JSON.stringify({ token: reply.token, email: reply.email, name: reply.name || "" }));
        ui.signIn.hidden = true; updateAccount(); ui.question.focus();
      } catch (error) { ui.signInError.textContent = error.message; ui.signInError.hidden = false; }
    });
    ui.accountButton.addEventListener("click", () => {
      if (account() && confirm(`Sign out ${account().email}?`)) { localStorage.removeItem(KEY("account")); updateAccount(); }
      else if (!account()) { ui.signIn.hidden = !ui.signIn.hidden; setTab("chat"); }
    });
    ui.keyButton.addEventListener("click", () => { setTab("chat"); if (ui.keyForm.hidden) openKeyForm(); else ui.keyForm.hidden = true; });
    ui.keyForm.addEventListener("submit", (event) => {
      event.preventDefault();
      localStorage.setItem(KEY("ai"), JSON.stringify({ provider: ui.provider.value, model: ui.model.value.trim(), base_url: ui.baseUrl.value.trim(), api_key: ui.apiKey.value.trim() }));
      ui.keyForm.hidden = true;
    });
    ui.cancelKey.addEventListener("click", () => { ui.keyForm.hidden = true; });
    ui.forgetKey.addEventListener("click", () => { localStorage.removeItem(KEY("ai")); ui.keyForm.hidden = true; });

    ui.back.addEventListener("click", () => { const previous = state.history.pop(); if (previous) showGraph(previous, false); });
    ui.here.addEventListener("click", () => { state.history = []; showGraph(state.pageNode && state.pageNode.id, false); });
    ui.recenter.addEventListener("click", () => { if (state.selected) showGraph(state.selected.id, true); });
    ui.explain.addEventListener("click", () => {
      if (!state.selected) return;
      setTab("chat");
      const kind = state.selected.type === "keyword" ? "the concept" : state.selected.type === "chapter" ? "the chapter" : "the page";
      ask(`Explain ${kind} "${state.selected.label}".`, [state.selected.id]);
    });

    // Follow the site's light/dark switch live.
    const syncTheme = () => { root.dataset.theme = siteIsDark() ? "dark" : "light"; };
    new MutationObserver(syncTheme).observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "data-mode"] });
    if (document.body) new MutationObserver(syncTheme).observe(document.body, { attributes: true, attributeFilter: ["class", "data-theme"] });
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", syncTheme);
    window.addEventListener("resize", () => { if (!ui.panel.hidden) applySize(); });
    // Redraw the graph whenever its area changes size (window resized, node card shown, maximize).
    let redraw = null;
    new ResizeObserver(() => {
      clearTimeout(redraw);
      redraw = setTimeout(() => {
        if (state.view && !ui.graph.closest(".wdw-view").hidden) {
          const selected = state.selected;
          drawGraph(state.view);
          if (selected) selectNode(selected, selected.id === state.view.center.id);
        }
      }, 60);
    }).observe(ui.graph);
  }

  // ---------- page context: the site may change pages without reloading (MyST) ----------
  let lastPath = null;
  async function syncPage() {
    if (window.location.pathname === lastPath) return;
    lastPath = window.location.pathname;
    try {
      const reply = await getJson(`/api/page?path=${encodeURIComponent(lastPath)}`);
      state.pageNode = reply.node;
    } catch { state.pageNode = null; }
    state.history = [];
    state.center = null;
    if (!root.querySelector('[data-view="graph"]').hidden) showGraph(state.pageNode && state.pageNode.id, false);
    if (!ui.messages.querySelector(".wdw-message.user")) showChips();
  }

  async function start() {
    if (document.querySelector(".wdw-root")) return;
    if (!document.querySelector('link[data-wendao-widget="style"]')) {
      const link = el("link"); link.rel = "stylesheet"; link.href = `${ASSETS}wendao-widget.css?v=${VERSION}`; link.dataset.wendaoWidget = "style";
      document.head.appendChild(link);
    }
    build();
    try {
      const health = await getJson("/api/health");
      state.ai = { ...state.ai, ...(health.ai || {}) };
      state.course = health.course || null;
      state.graph = Boolean(health.graph);
      state.feedback = Boolean(health.feedback);
      if ("web_search" in health) state.webSearch = health.web_search && health.web_search.url ? health.web_search : null;
      if (state.course) ui.course.textContent = state.course.display || state.course.name;
    } catch {
      ui.course.textContent = "Course AI (offline)";
    }
    ui.graphTab.hidden = !state.graph;
    updateSearch();
    updateAccount();
    restore();
    await syncPage();
    if (sessionStorage.getItem(OPEN_KEY) === "1") setOpen(true);
    // MyST re-renders pages in place: re-attach if removed, and notice page changes.
    setInterval(() => {
      if (!document.body.contains(root)) document.body.appendChild(root);
      syncPage();
    }, 1200);
  }

  // Wait until the site (e.g. MyST) has finished setting up the page.
  if (document.readyState === "complete") setTimeout(start, 600);
  else window.addEventListener("load", () => setTimeout(start, 600), { once: true });
})();
