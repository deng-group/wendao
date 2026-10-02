const form = document.querySelector("#ask-form");
const queryInput = document.querySelector("#query");
const messages = document.querySelector("#messages");
const sourcesEl = document.querySelector("#sources");
const statusEl = document.querySelector("#status");
const providerEl = document.querySelector("#provider");
const modelEl = document.querySelector("#model");
const keyEl = document.querySelector("#api-key");
const launcher = document.querySelector("#agent-launcher");
const widget = document.querySelector("#agent-widget");
const closeButton = document.querySelector("#agent-close");

const MEMORY_KEY = "wendao_chat_short_memory";
const MAX_MEMORY_ITEMS = 6;

function readMemory() {
  try {
    return JSON.parse(sessionStorage.getItem(MEMORY_KEY) || "[]");
  } catch {
    return [];
  }
}

function writeMemory(memory) {
  sessionStorage.setItem(MEMORY_KEY, JSON.stringify(memory.slice(-MAX_MEMORY_ITEMS)));
}

function addMessage(role, text, meta = "", kind = "") {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role} ${kind}`.trim();

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.appendChild(bubble);

  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "meta";
    metaEl.textContent = meta;
    wrapper.appendChild(metaEl);
  }

  messages.appendChild(wrapper);
  messages.scrollTop = messages.scrollHeight;
}

function openWidget() {
  widget.hidden = false;
  launcher.setAttribute("aria-expanded", "true");
  queryInput.focus();
}

function closeWidget() {
  widget.hidden = true;
  launcher.setAttribute("aria-expanded", "false");
  launcher.focus();
}

function setSources(sources) {
  sourcesEl.innerHTML = "";
  if (!sources || sources.length === 0) {
    sourcesEl.className = "sources empty";
    sourcesEl.textContent = "No sources";
    return;
  }
  sourcesEl.className = "sources";
  for (const source of sources.slice(0, 5)) {
    const item = document.createElement("div");
    item.className = "source-item";
    item.innerHTML = `
      <div class="source-title"></div>
      <div class="source-path"></div>
      <div class="source-score"></div>
    `;
    item.querySelector(".source-title").textContent = source.title || "Untitled";
    item.querySelector(".source-path").textContent = [source.file_path, source.location].filter(Boolean).join(", ");
    item.querySelector(".source-score").textContent = `score ${Number(source.score || 0).toFixed(3)}`;
    sourcesEl.appendChild(item);
  }
}

function setStatus(result) {
  const values = [
    result.provider || "-",
    result.model || "-",
    result.status || "-",
    result.temporal_context || "-",
  ];
  [...statusEl.querySelectorAll("dd")].forEach((dd, index) => {
    dd.textContent = values[index];
  });
}

async function ask(query) {
  const response = await fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      // Sent only when the student entered their own key; otherwise the course AI answers.
      ...(keyEl.value.trim() ? { ai: { provider: providerEl.value, model: modelEl.value.trim(), api_key: keyEl.value.trim() } } : {}),
      short_memory: readMemory(),
    }),
  });

  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(payload.message || payload.error || "Request failed");
  }
  return payload;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) {
    return;
  }

  queryInput.value = "";
  form.querySelector("button").disabled = true;
  addMessage("user", query);
  addMessage("assistant", "Thinking...", "", "pending");
  const pending = messages.querySelector(".message.pending");

  try {
    const result = await ask(query);
    pending.remove();
    addMessage("assistant", result.answer, `${result.status} | ${result.provider} ${result.model || ""}`.trim());
    setSources(result.sources);
    setStatus(result);

    const memory = readMemory();
    memory.push({ role: "user", content: query });
    memory.push({ role: "assistant", content: result.answer });
    writeMemory(memory);
  } catch (error) {
    pending.remove();
    addMessage("assistant", error.message, "Request error", "error");
  } finally {
    form.querySelector("button").disabled = false;
    queryInput.focus();
  }
});

queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    form.requestSubmit();
  }
});

launcher.addEventListener("click", () => {
  if (widget.hidden) {
    openWidget();
  } else {
    closeWidget();
  }
});

closeButton.addEventListener("click", closeWidget);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !widget.hidden) {
    closeWidget();
  }
});

addMessage(
  "assistant",
  "Ask a question about the course materials. This window keeps short memory only while the tab stays open.",
  "Ready"
);
