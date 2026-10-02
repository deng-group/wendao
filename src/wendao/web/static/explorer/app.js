const SVG_NS = "http://www.w3.org/2000/svg";
const MEMORY_KEY = "wendao_course_memory_v1";
const MAX_MEMORY_ITEMS = 6;
const AI_SETTINGS_KEY = "wendao_ai_settings_v1";
const SIGN_IN_KEY = "wendao_sign_in_v1";

const elements = {
  shell: document.querySelector(".app-shell"),
  svg: document.querySelector("#graph-svg"),
  viewport: document.querySelector("#graph-viewport"),
  edgeLayer: document.querySelector("#edge-layer"),
  nodeLayer: document.querySelector("#node-layer"),
  stage: document.querySelector("#graph-stage"),
  search: document.querySelector("#graph-search"),
  searchResults: document.querySelector("#search-results"),
  nodeCard: document.querySelector("#node-card"),
  nodeKind: document.querySelector("#node-kind"),
  nodeTitle: document.querySelector("#node-title"),
  chatPanel: document.querySelector("#chat-panel"),
  openChat: document.querySelector("#open-chat"),
  clearChat: document.querySelector("#clear-chat"),
  closeChat: document.querySelector("#close-chat"),
  contextBar: document.querySelector("#context-bar"),
  contextChip: document.querySelector("#context-chip"),
  explainNode: document.querySelector("#explain-node"),
  messages: document.querySelector("#messages"),
  suggestions: document.querySelector("#suggestions"),
  form: document.querySelector("#chat-form"),
  question: document.querySelector("#question"),
  send: document.querySelector("#send-question"),
  sourceDrawer: document.querySelector("#source-drawer"),
  sourceToggle: document.querySelector("#source-toggle"),
  sourceList: document.querySelector("#source-list"),
  sourceCount: document.querySelector("#source-count"),
  graphHint: document.querySelector("#graph-hint"),
  legendChapter: document.querySelector("#legend-chapter"),
  legendConcept: document.querySelector("#legend-concept"),
  aiBadge: document.querySelector("#ai-badge"),
  aiButton: document.querySelector("#open-ai-settings"),
  aiDialog: document.querySelector("#ai-settings"),
  aiNote: document.querySelector("#ai-settings-note"),
  aiProvider: document.querySelector("#ai-provider"),
  aiModel: document.querySelector("#ai-model"),
  aiBaseUrl: document.querySelector("#ai-base-url"),
  aiKey: document.querySelector("#ai-key"),
  signInButton: document.querySelector("#open-sign-in"),
  signInDialog: document.querySelector("#sign-in"),
  signInNote: document.querySelector("#sign-in-note"),
  signInEmail: document.querySelector("#sign-in-email"),
  signInError: document.querySelector("#sign-in-error"),
};

function signedIn() {
  try { return JSON.parse(localStorage.getItem(SIGN_IN_KEY) || "null"); } catch { return null; }
}

function updateSignInButton() {
  const account = signedIn();
  elements.signInButton.hidden = !courseAi.login_required;
  elements.signInButton.textContent = account ? (account.name || account.email).split(" ")[0] : "Sign in";
  elements.signInButton.title = account ? `Signed in as ${account.email}` : "Sign in";
}

function openSignIn(note) {
  const account = signedIn();
  elements.signInEmail.value = account ? account.email : "";
  elements.signInNote.textContent = note || (account
    ? `Signed in as ${account.email}.`
    : "Use the email your course knows you by. It is used to count your daily questions.");
  elements.signInError.hidden = true;
  document.querySelector("#sign-out").hidden = !account;
  elements.signInDialog.showModal();
}

async function signIn() {
  elements.signInError.hidden = true;
  try {
    const response = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: elements.signInEmail.value.trim() }),
    });
    const reply = await response.json();
    if (!reply.ok) throw new Error(reply.message || "Sign-in failed.");
    localStorage.setItem(SIGN_IN_KEY, JSON.stringify({ token: reply.token, email: reply.email, name: reply.name || "" }));
    elements.signInDialog.close("signed-in"); updateSignInButton();
  } catch (error) {
    elements.signInError.textContent = error.message; elements.signInError.hidden = false;
  }
}

// What the server says about AI for this course: { mode, teacher_ai, student_keys, needs_student_key }.
let courseAi = { mode: "teacher", teacher_ai: true, student_keys: false, needs_student_key: false };

function readAiSettings() {
  try { return JSON.parse(localStorage.getItem(AI_SETTINGS_KEY) || "null"); } catch { return null; }
}

function studentAi() {
  const saved = readAiSettings();
  return courseAi.student_keys && saved && (saved.api_key || saved.base_url) ? saved : null;
}

function updateAiBadge() {
  elements.aiButton.hidden = !courseAi.student_keys;
  elements.aiBadge.textContent = studentAi() ? "Your AI key" : "Course-grounded";
}

function openAiSettings(note) {
  const saved = readAiSettings() || {};
  elements.aiProvider.value = saved.provider || "openai";
  elements.aiModel.value = saved.model || "";
  elements.aiBaseUrl.value = saved.base_url || "";
  elements.aiKey.value = saved.api_key || "";
  elements.aiNote.textContent = note || "Use your own AI account to ask questions. Your key is saved only in this browser.";
  elements.aiDialog.showModal();
}

async function loadCourseAi() {
  try {
    const response = await fetch("/api/health");
    if (response.ok) courseAi = { ...courseAi, ...((await response.json()).ai || {}) };
  } catch { /* keep the defaults; questions will report any problem */ }
  updateAiBadge(); updateSignInButton();
}

const state = {
  graph: null,
  nodesById: new Map(),
  edgesByNode: new Map(),
  visibleIds: new Set(),
  visibleEdges: [],
  positions: new Map(),
  nodeElements: new Map(),
  edgeElements: [],
  selectedId: null,
  transform: { x: 0, y: 0, k: 1 },
  simulationFrame: null,
  simulationEnergy: 0,
  pointer: null,
  mode: "overview",
  activeExplanationController: null,
  activeQuestionController: null,
  chatAutoFollow: true,
  chatOpen: false,
};

function setChatOpen(open, options = {}) {
  state.chatOpen = open;
  elements.shell.classList.toggle("chat-is-open", open);
  elements.chatPanel.hidden = !open;
  elements.chatPanel.setAttribute("aria-hidden", String(!open));
  elements.openChat.hidden = open;
  elements.openChat.setAttribute("aria-expanded", String(open));
  requestAnimationFrame(() => {
    if (state.graph) fitGraph();
    if (open && options.focusQuestion !== false) elements.question.focus();
    if (!open && options.restoreFocus !== false) elements.openChat.focus();
  });
}

function readMemory() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(MEMORY_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.slice(-MAX_MEMORY_ITEMS) : [];
  } catch {
    return [];
  }
}

function writeMemory(items) {
  sessionStorage.setItem(MEMORY_KEY, JSON.stringify(items.slice(-MAX_MEMORY_ITEMS)));
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function radiusFor(node) {
  if (node.type === "chapter") return 18;
  return 11;
}

function visualType(node) {
  return node.type === "chapter" ? "chapter" : "concept";
}

function primaryChapters() {
  return state.graph.nodes
    .filter((node) => node.type === "chapter" && node.visibility === "primary")
    .sort((a, b) => a.order - b.order);
}

function studentConcepts() {
  const primaryChapterIds = new Set(primaryChapters().map((chapter) => chapter.id));
  return state.graph.nodes.filter((node) => {
    if (node.visibility === "hidden" || node.type === "chapter") return false;
    if (node.type === "topic") return primaryChapterIds.has(node.chapter_id);
    return (node.chapter_ids || []).some((chapterId) => primaryChapterIds.has(chapterId));
  });
}

const FEATURED_CONCEPT_IDS = [
  "keyword:crystal-structure",
  "keyword:materials-project",
  "keyword:dft",
  "keyword:molecular-dynamics",
  "keyword:optimization",
  "keyword:convex-hull",
  "keyword:machine-learning",
  "keyword:gnn",
  "keyword:machine-learning-potential",
  "keyword:mace",
  "keyword:training-data",
  "keyword:phase-diagram",
];

function representativeTopics(chapterId, limit = 2) {
  const genericSections = new Set(["index", "introduction", "intro", "overview", "summary"]);
  return state.graph.nodes
    .filter((node) => node.type === "topic" && node.chapter_id === chapterId && node.visibility !== "hidden")
    .sort((a, b) => {
      const genericDifference = Number(genericSections.has(a.section)) - Number(genericSections.has(b.section));
      return genericDifference || (b.keyword_ids?.length || 0) - (a.keyword_ids?.length || 0) || a.label.localeCompare(b.label);
    })
    .slice(0, limit);
}

function initialVisibleIds() {
  const ids = new Set();
  for (const chapter of primaryChapters()) {
    ids.add(chapter.id);
    representativeTopics(chapter.id).forEach((topic) => ids.add(topic.id));
  }
  FEATURED_CONCEPT_IDS.filter((id) => state.nodesById.has(id)).forEach((id) => ids.add(id));
  return ids;
}

function modeVisibleIds(mode) {
  if (mode === "chapter") return new Set(primaryChapters().map((chapter) => chapter.id));
  if (mode === "concept") return new Set(studentConcepts().map((concept) => concept.id));
  return initialVisibleIds();
}

function indexGraph(graph) {
  state.graph = graph;
  state.nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  state.edgesByNode = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) {
    state.edgesByNode.get(edge.source)?.push(edge);
    state.edgesByNode.get(edge.target)?.push(edge);
  }
}

function edgeOther(edge, nodeId) {
  return edge.source === nodeId ? edge.target : edge.source;
}

function abortAutomaticExplanation() {
  if (state.activeExplanationController) {
    state.activeExplanationController.abort();
    state.activeExplanationController = null;
  }
  setExplainButtonBusy(false);
}

function setExplainButtonBusy(busy) {
  elements.explainNode.disabled = busy;
  elements.explainNode.textContent = busy ? "Explaining…" : "Explain";
}

function clearNodeExplanation() {
  abortAutomaticExplanation();
  elements.messages.querySelectorAll(".auto-explanation").forEach((message) => message.remove());
}

function updateModeControls() {
  elements.legendChapter.setAttribute("aria-pressed", String(state.mode === "chapter"));
  elements.legendConcept.setAttribute("aria-pressed", String(state.mode === "concept"));
  const hint = state.mode === "chapter"
    ? "Chapter view — choose one to reveal its concepts"
    : state.mode === "concept"
      ? "Concept view — choose one to reveal its chapters"
      : "Overview — use the legend to filter by level";
  elements.graphHint.lastChild.textContent = ` ${hint}`;
}

function setViewMode(mode, options = {}) {
  const nextMode = options.toggle && state.mode === mode ? "overview" : mode;
  clearNodeExplanation();
  state.mode = nextMode;
  state.selectedId = null;
  state.visibleIds = modeVisibleIds(nextMode);
  elements.nodeCard.hidden = true;
  updateContext();
  updateModeControls();
  seedPositions(true);
  rebuildGraph();
  runSimulation(nextMode === "concept" ? 170 : 120, true);
}

function setInitialView() {
  setViewMode("overview");
}

function chaptersForConcept(node) {
  if (node.type === "topic") return node.chapter_id ? [node.chapter_id] : [];
  return node.chapter_ids || [];
}

function visibleSetFor(node) {
  if (node.type === "chapter") {
    const ids = new Set(primaryChapters().map((chapter) => chapter.id));
    state.graph.nodes
      .filter((candidate) => candidate.type === "topic" && candidate.chapter_id === node.id)
      .forEach((candidate) => ids.add(candidate.id));
    (node.keyword_ids || []).forEach((id) => ids.add(id));
    return ids;
  }

  const ids = new Set(studentConcepts().map((concept) => concept.id));
  chaptersForConcept(node)
      .map((id) => state.nodesById.get(id))
      .filter((chapter) => chapter?.visibility === "primary")
      .forEach((chapter) => ids.add(chapter.id));
  return ids;
}

function selectNode(nodeId, options = {}) {
  const node = state.nodesById.get(nodeId);
  if (!node) return;
  clearNodeExplanation();
  state.mode = visualType(node);
  state.selectedId = nodeId;
  state.visibleIds = visibleSetFor(node);
  showNodeCard(node);
  updateContext();
  updateSuggestions(node);
  updateModeControls();
  seedPositions(false, node);
  rebuildGraph();
  runSimulation(150, Boolean(options.fit));
  if (options.focusQuestion) elements.question.focus();
}

function seedPositions(reset = false, focusNode = null) {
  const width = Math.max(elements.stage.clientWidth, 520);
  const height = Math.max(elements.stage.clientHeight, 420);
  if (reset) state.positions.clear();
  const chapters = primaryChapters();
  chapters.forEach((chapter, index) => {
    if (state.positions.has(chapter.id) && !reset) return;
    const angle = -Math.PI / 2 + (index / chapters.length) * Math.PI * 2;
    state.positions.set(chapter.id, {
      x: width / 2 + Math.cos(angle) * width * 0.31,
      y: height / 2 + Math.sin(angle) * height * 0.31,
      vx: 0,
      vy: 0,
    });
  });

  if (state.mode === "concept") {
    const concepts = [...state.visibleIds]
      .map((id) => state.nodesById.get(id))
      .filter((node) => node && visualType(node) === "concept");
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    concepts.forEach((concept, index) => {
      if (state.positions.has(concept.id) && !reset) return;
      const radius = 24 + Math.sqrt(index) * 34;
      const angle = index * goldenAngle;
      state.positions.set(concept.id, {
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
      });
    });
    return;
  }

  const visibleTopics = [...state.visibleIds]
    .map((id) => state.nodesById.get(id))
    .filter((node) => node?.type === "topic" && !state.positions.has(node.id));
  const topicsByChapter = new Map();
  for (const topic of visibleTopics) {
    if (!topicsByChapter.has(topic.chapter_id)) topicsByChapter.set(topic.chapter_id, []);
    topicsByChapter.get(topic.chapter_id).push(topic);
  }
  for (const [chapterId, topics] of topicsByChapter) {
    const parent = state.positions.get(chapterId) || { x: width / 2, y: height / 2 };
    const chapter = state.nodesById.get(chapterId);
    const chapterIndex = Math.max(0, chapters.findIndex((candidate) => candidate.id === chapter?.id));
    const outwardAngle = -Math.PI / 2 + (chapterIndex / Math.max(chapters.length, 1)) * Math.PI * 2;
    topics.forEach((topic, index) => {
      const spread = (index - (topics.length - 1) / 2) * 0.72;
      const angle = outwardAngle + spread;
      const ring = 72 + (index % 2) * 12;
      state.positions.set(topic.id, {
        x: parent.x + Math.cos(angle) * ring,
        y: parent.y + Math.sin(angle) * ring,
        vx: 0,
        vy: 0,
      });
    });
  }

  const visibleKeywords = [...state.visibleIds]
    .map((id) => state.nodesById.get(id))
    .filter((node) => node?.type === "keyword" && !state.positions.has(node.id));
  visibleKeywords.forEach((keyword, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(visibleKeywords.length, 1)) * Math.PI * 2;
    const ringX = width * (0.13 + (index % 2) * 0.035);
    const ringY = height * (0.14 + (index % 2) * 0.04);
    state.positions.set(keyword.id, {
      x: width / 2 + Math.cos(angle) * ringX,
      y: height / 2 + Math.sin(angle) * ringY,
      vx: 0,
      vy: 0,
    });
  });

  const center = focusNode && state.positions.get(focusNode.id)
    ? state.positions.get(focusNode.id)
    : { x: width / 2, y: height / 2 };
  const unseeded = [...state.visibleIds].filter((id) => !state.positions.has(id));
  unseeded.forEach((id, index) => {
    const angle = (index / Math.max(unseeded.length, 1)) * Math.PI * 2;
    const ring = 65 + (index % 4) * 15;
    state.positions.set(id, {
      x: center.x + Math.cos(angle) * ring,
      y: center.y + Math.sin(angle) * ring,
      vx: 0,
      vy: 0,
    });
  });
}

function isChapterConceptEdge(edge) {
  if (!["contains", "covers", "mentions"].includes(edge.type)) return false;
  const source = state.nodesById.get(edge.source);
  const target = state.nodesById.get(edge.target);
  return source && target && visualType(source) !== visualType(target);
}

function getVisibleEdges() {
  const visible = state.visibleIds;
  return state.graph.edges.filter((edge) => {
    if (!visible.has(edge.source) || !visible.has(edge.target)) return false;
    const source = state.nodesById.get(edge.source);
    const target = state.nodesById.get(edge.target);
    const sourceType = source && visualType(source);
    const targetType = target && visualType(target);

    if (state.selectedId) {
      const selected = state.nodesById.get(state.selectedId);
      if (selected?.type === "chapter") return isChapterConceptEdge(edge);
      return isChapterConceptEdge(edge) && (edge.source === state.selectedId || edge.target === state.selectedId);
    }

    if (state.mode === "concept") return false;
    if (state.mode === "chapter") {
      return sourceType === "chapter" && targetType === "chapter"
        && (edge.type === "sequence" || (edge.type === "related" && edge.weight >= 0.16));
    }
    if (isChapterConceptEdge(edge)) return true;
    return sourceType === "chapter" && targetType === "chapter"
      && (edge.type === "sequence" || (edge.type === "related" && edge.weight >= 0.16));
  });
}

function rebuildGraph() {
  state.visibleEdges = getVisibleEdges();
  elements.stage.classList.toggle("dense-concepts", state.mode === "concept" && state.visibleIds.size > 80);
  state.nodeElements.clear();
  state.edgeElements = [];
  elements.edgeLayer.replaceChildren();
  elements.nodeLayer.replaceChildren();

  for (const edge of state.visibleEdges) {
    const line = svgElement("line", {
      class: `edge${edge.source === state.selectedId || edge.target === state.selectedId ? " active" : ""}`,
      "data-source": edge.source,
      "data-target": edge.target,
    });
    const title = svgElement("title");
    title.textContent = "Related through course material";
    line.appendChild(title);
    elements.edgeLayer.appendChild(line);
    state.edgeElements.push({ edge, element: line });
  }

  for (const id of state.visibleIds) {
    const node = state.nodesById.get(id);
    if (!node) continue;
    const kind = visualType(node);
    const radius = radiusFor(node);
    const group = svgElement("g", {
      class: `node ${kind}${id === state.selectedId ? " selected" : ""}`,
      tabindex: "0",
      role: "button",
      "aria-label": `${kind}: ${node.label}`,
      "data-id": id,
    });
    group.appendChild(svgElement("circle", { class: "hit-area", r: radius + 10 }));
    group.appendChild(svgElement("circle", { class: "halo", r: radius + 14, fill: kind === "chapter" ? "#2563eb" : "#d97706" }));
    group.appendChild(svgElement("circle", { class: "core", r: radius }));
    const label = svgElement("text", { y: radius + 19 });
    label.textContent = node.label.length > 29 ? `${node.label.slice(0, 27)}…` : node.label;
    const title = svgElement("title"); title.textContent = node.label;
    group.append(label, title);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault(); selectNode(id);
      }
    });
    group.addEventListener("pointerdown", startNodeDrag);
    elements.nodeLayer.appendChild(group);
    state.nodeElements.set(id, group);
  }
  updateGraphPositions();
}

function updateGraphPositions() {
  for (const [id, element] of state.nodeElements) {
    const position = state.positions.get(id);
    if (position) element.setAttribute("transform", `translate(${position.x.toFixed(2)} ${position.y.toFixed(2)})`);
  }
  for (const { edge, element } of state.edgeElements) {
    const source = state.positions.get(edge.source);
    const target = state.positions.get(edge.target);
    if (!source || !target) continue;
    element.setAttribute("x1", source.x.toFixed(2)); element.setAttribute("y1", source.y.toFixed(2));
    element.setAttribute("x2", target.x.toFixed(2)); element.setAttribute("y2", target.y.toFixed(2));
  }
}

function simulationStep() {
  const nodes = [...state.visibleIds].map((id) => ({ id, node: state.nodesById.get(id), position: state.positions.get(id) })).filter((item) => item.position);
  const width = Math.max(elements.stage.clientWidth, 520);
  const height = Math.max(elements.stage.clientHeight, 420);
  for (let i = 0; i < nodes.length; i += 1) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = nodes[j];
      let dx = b.position.x - a.position.x;
      let dy = b.position.y - a.position.y;
      const distance2 = Math.max(dx * dx + dy * dy, 90);
      const distance = Math.sqrt(distance2);
      const desired = radiusFor(a.node) + radiusFor(b.node) + 68;
      const force = Math.min(2.4, (desired * desired) / distance2 * 0.7);
      dx /= distance; dy /= distance;
      a.position.vx -= dx * force; a.position.vy -= dy * force;
      b.position.vx += dx * force; b.position.vy += dy * force;
    }
  }
  for (const edge of state.visibleEdges) {
    const source = state.positions.get(edge.source); const target = state.positions.get(edge.target);
    if (!source || !target) continue;
    const dx = target.x - source.x; const dy = target.y - source.y;
    const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
    const sourceNode = state.nodesById.get(edge.source); const targetNode = state.nodesById.get(edge.target);
    const desired = sourceNode.type === "chapter" && targetNode.type === "chapter" ? 205 : 112;
    const force = (distance - desired) * 0.008 * Math.max(edge.weight || 0.4, 0.35);
    source.vx += (dx / distance) * force; source.vy += (dy / distance) * force;
    target.vx -= (dx / distance) * force; target.vy -= (dy / distance) * force;
  }
  for (const item of nodes) {
    const position = item.position;
    position.vx += (width / 2 - position.x) * 0.0007;
    position.vy += (height / 2 - position.y) * 0.0007;
    position.vx *= 0.86; position.vy *= 0.86;
    position.x += position.vx; position.y += position.vy;
  }
  updateGraphPositions();
}

function runSimulation(frames = 120, fitWhenDone = false) {
  if (state.simulationFrame) cancelAnimationFrame(state.simulationFrame);
  state.simulationEnergy = frames;
  const animate = () => {
    simulationStep(); state.simulationEnergy -= 1;
    if (state.simulationEnergy > 0) state.simulationFrame = requestAnimationFrame(animate);
    else if (fitWhenDone) fitGraph();
  };
  state.simulationFrame = requestAnimationFrame(animate);
}

function applyTransform() {
  const { x, y, k } = state.transform;
  elements.viewport.setAttribute("transform", `translate(${x} ${y}) scale(${k})`);
}

function fitGraph() {
  const positions = [...state.visibleIds].map((id) => state.positions.get(id)).filter(Boolean);
  if (!positions.length) return;
  const width = elements.stage.clientWidth; const height = elements.stage.clientHeight;
  const xs = positions.map((position) => position.x); const ys = positions.map((position) => position.y);
  const minX = Math.min(...xs) - 55; const maxX = Math.max(...xs) + 55;
  const minY = Math.min(...ys) - 55; const maxY = Math.max(...ys) + 55;
  const scale = Math.min(1.35, Math.max(0.25, Math.min(width / Math.max(maxX - minX, 1), height / Math.max(maxY - minY, 1)) * 0.9));
  state.transform = { k: scale, x: width / 2 - ((minX + maxX) / 2) * scale, y: height / 2 - ((minY + maxY) / 2) * scale };
  applyTransform();
}

function graphCoordinates(event) {
  const rect = elements.svg.getBoundingClientRect();
  return { x: (event.clientX - rect.left - state.transform.x) / state.transform.k, y: (event.clientY - rect.top - state.transform.y) / state.transform.k };
}

function startNodeDrag(event) {
  event.stopPropagation();
  if (state.simulationFrame) {
    cancelAnimationFrame(state.simulationFrame);
    state.simulationFrame = null;
  }
  state.pointer = { kind: "node", id: event.currentTarget.dataset.id, startX: event.clientX, startY: event.clientY, moved: false };
  elements.svg.setPointerCapture(event.pointerId);
}

elements.svg.addEventListener("pointerdown", (event) => {
  if (event.target.closest?.(".node")) return;
  state.pointer = { kind: "pan", startX: event.clientX, startY: event.clientY, originX: state.transform.x, originY: state.transform.y, moved: false };
  elements.svg.setPointerCapture(event.pointerId);
});

elements.svg.addEventListener("pointermove", (event) => {
  if (!state.pointer) return;
  const dx = event.clientX - state.pointer.startX; const dy = event.clientY - state.pointer.startY;
  if (Math.abs(dx) + Math.abs(dy) > 3) state.pointer.moved = true;
  if (state.pointer.kind === "pan") {
    state.transform.x = state.pointer.originX + dx; state.transform.y = state.pointer.originY + dy; applyTransform();
  } else {
    const position = state.positions.get(state.pointer.id); const point = graphCoordinates(event);
    if (position) { position.x = point.x; position.y = point.y; position.vx = 0; position.vy = 0; updateGraphPositions(); }
  }
});

elements.svg.addEventListener("pointerup", (event) => {
  const pointer = state.pointer;
  if (elements.svg.hasPointerCapture(event.pointerId)) elements.svg.releasePointerCapture(event.pointerId);
  state.pointer = null;
  if (pointer?.kind === "node" && !pointer.moved) selectNode(pointer.id);
});

elements.svg.addEventListener("pointercancel", (event) => {
  if (elements.svg.hasPointerCapture(event.pointerId)) elements.svg.releasePointerCapture(event.pointerId);
  state.pointer = null;
});

elements.svg.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = elements.svg.getBoundingClientRect(); const oldScale = state.transform.k;
  const nextScale = Math.min(2.4, Math.max(0.35, oldScale * Math.exp(-event.deltaY * 0.0012)));
  const px = event.clientX - rect.left; const py = event.clientY - rect.top;
  const graphX = (px - state.transform.x) / oldScale; const graphY = (py - state.transform.y) / oldScale;
  state.transform.k = nextScale; state.transform.x = px - graphX * nextScale; state.transform.y = py - graphY * nextScale; applyTransform();
}, { passive: false });

function showNodeCard(node) {
  const kind = visualType(node);
  elements.nodeKind.textContent = kind;
  elements.nodeKind.style.color = kind === "chapter" ? "var(--chapter)" : "var(--concept)";
  elements.nodeTitle.textContent = node.label;
  elements.nodeCard.hidden = false;
}

function updateContext() {
  const node = state.nodesById.get(state.selectedId);
  elements.contextBar.hidden = !node;
  elements.question.placeholder = node ? `Ask about ${node.label}…` : "Ask about the course…";
  if (node) {
    elements.contextChip.querySelector("b").textContent = node.label;
    setExplainButtonBusy(false);
  }
}

function updateSuggestions(node) {
  const suggestions = [`Explain ${node.label} using the course materials.`, `What course concepts are most closely connected to ${node.label}?`];
  elements.suggestions.replaceChildren();
  suggestions.forEach((text) => {
    const button = document.createElement("button"); button.type = "button"; button.textContent = text;
    button.addEventListener("click", () => { elements.question.value = text; resizeQuestion(); elements.question.focus(); });
    elements.suggestions.appendChild(button);
  });
}

function showSearchResults(query) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) { elements.searchResults.hidden = true; elements.searchResults.replaceChildren(); return; }
  const matches = state.graph.nodes
    .map((node) => {
      const label = node.label.toLowerCase();
      const aliasMatch = (node.aliases || []).some((alias) => alias.toLowerCase().includes(normalized));
      const score = label === normalized ? 0 : label.startsWith(normalized) ? 1 : label.includes(normalized) ? 2 : aliasMatch ? 3 : 9;
      return { node, score };
    })
    .filter((item) => item.score < 9 && item.node.visibility !== "hidden")
    .sort((a, b) => a.score - b.score || a.node.label.localeCompare(b.node.label))
    .slice(0, 9);
  elements.searchResults.replaceChildren();
  matches.forEach(({ node }) => {
    const kindName = visualType(node);
    const button = document.createElement("button"); button.className = `search-result ${kindName}`; button.type = "button";
    const dot = document.createElement("i"); const label = document.createElement("span"); const kind = document.createElement("small");
    label.textContent = node.label; kind.textContent = kindName; button.append(dot, label, kind);
    button.addEventListener("click", () => { elements.search.value = ""; elements.searchResults.hidden = true; selectNode(node.id, { fit: true }); });
    elements.searchResults.appendChild(button);
  });
  if (!matches.length) { const empty = document.createElement("div"); empty.className = "search-result"; empty.textContent = "No matching course concept"; elements.searchResults.appendChild(empty); }
  elements.searchResults.hidden = false;
}

function appendInlineMarkdown(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|\*[^*\n]+\*|_[^_\n]+_)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("`")) {
      const code = document.createElement("code"); code.textContent = token.slice(1, -1); parent.appendChild(code);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      const strong = document.createElement("strong"); appendInlineMarkdown(strong, token.slice(2, -2)); parent.appendChild(strong);
    } else if (token.startsWith("[")) {
      const parts = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
      if (parts) {
        const link = document.createElement("a"); link.href = parts[2]; link.target = "_blank"; link.rel = "noopener noreferrer";
        appendInlineMarkdown(link, parts[1]); parent.appendChild(link);
      } else {
        parent.appendChild(document.createTextNode(token));
      }
    } else {
      const emphasis = document.createElement("em"); appendInlineMarkdown(emphasis, token.slice(1, -1)); parent.appendChild(emphasis);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
}

function isMarkdownBlockStart(line) {
  return /^\s*(?:```|#{1,4}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/.test(line);
}

function renderMarkdown(container, markdown) {
  const lines = String(markdown || "").replaceAll("\r\n", "\n").split("\n");
  const fragment = document.createDocumentFragment();
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }

    const fence = line.match(/^\s*```([^\s`]*)\s*$/);
    if (fence) {
      index += 1; const codeLines = [];
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) { codeLines.push(lines[index]); index += 1; }
      if (index < lines.length) index += 1;
      const pre = document.createElement("pre"); const code = document.createElement("code");
      if (fence[1]) code.className = `language-${fence[1].replace(/[^a-z0-9_-]/gi, "")}`;
      code.textContent = codeLines.join("\n"); pre.appendChild(code); fragment.appendChild(pre); continue;
    }

    const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(`h${Math.min(heading[1].length + 1, 5)}`);
      appendInlineMarkdown(element, heading[2].trim()); fragment.appendChild(element); index += 1; continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    const ordered = line.match(/^\s*(\d+)[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const list = document.createElement(unordered ? "ul" : "ol");
      if (ordered) list.start = Number(ordered[1]);
      const itemPattern = unordered ? /^\s*[-*+]\s+(.+)$/ : /^\s*(\d+)[.)]\s+(.+)$/;
      while (index < lines.length) {
        const itemMatch = lines[index].match(itemPattern); if (!itemMatch) break;
        const itemText = unordered ? itemMatch[1] : itemMatch[2];
        const item = document.createElement("li"); appendInlineMarkdown(item, itemText.trim()); list.appendChild(item); index += 1;
      }
      fragment.appendChild(list); continue;
    }

    if (/^\s*>/.test(line)) {
      const quote = document.createElement("blockquote"); const quoteLines = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) { quoteLines.push(lines[index].replace(/^\s*>\s?/, "")); index += 1; }
      appendInlineMarkdown(quote, quoteLines.join(" ")); fragment.appendChild(quote); continue;
    }

    const paragraphLines = [line.trim()]; index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines[index])) {
      paragraphLines.push(lines[index].trim()); index += 1;
    }
    const paragraph = document.createElement("p"); appendInlineMarkdown(paragraph, paragraphLines.join(" ")); fragment.appendChild(paragraph);
  }
  container.replaceChildren(fragment);
}

function addUserMessage(text) {
  const article = document.createElement("article"); article.className = "message user";
  const bubble = document.createElement("div"); const paragraph = document.createElement("p");
  paragraph.textContent = text; bubble.appendChild(paragraph); article.appendChild(bubble); elements.messages.appendChild(article);
  state.chatAutoFollow = true;
  scrollMessagesToBottom(true);
}

function addAssistantMessage(options = {}) {
  const article = document.createElement("article"); article.className = `message assistant${options.className ? ` ${options.className}` : ""}`;
  const avatar = document.createElement("div"); avatar.className = "assistant-avatar"; avatar.textContent = "W";
  const content = document.createElement("div"); const body = document.createElement("div"); body.className = "message-body";
  if (options.heading) {
    const heading = document.createElement("strong"); heading.className = "message-heading"; heading.textContent = options.heading;
    content.appendChild(heading);
  }
  const thinking = document.createElement("span"); thinking.className = "thinking"; thinking.innerHTML = "<i></i><i></i><i></i>";
  body.appendChild(thinking); content.appendChild(body); article.append(avatar, content); elements.messages.appendChild(article);
  state.chatAutoFollow = true;
  scrollMessagesToBottom(true);
  return { article, body, content };
}

function showWelcomeMessage() {
  const article = document.createElement("article"); article.className = "message assistant";
  const avatar = document.createElement("div"); avatar.className = "assistant-avatar"; avatar.setAttribute("aria-hidden", "true"); avatar.textContent = "W";
  const content = document.createElement("div"); const message = document.createElement("p"); const note = document.createElement("small");
  message.textContent = "Choose a node, then select Explain—or ask a question about the course.";
  note.textContent = "Answers use only the provided course materials.";
  content.append(message, note); article.append(avatar, content); elements.messages.replaceChildren(article);
}

function clearConversation() {
  abortAutomaticExplanation();
  if (state.activeQuestionController) {
    state.activeQuestionController.abort();
    state.activeQuestionController = null;
  }
  sessionStorage.removeItem(MEMORY_KEY);
  elements.question.value = "";
  elements.send.disabled = false;
  elements.suggestions.hidden = false;
  elements.sourceToggle.setAttribute("aria-expanded", "false");
  elements.sourceList.hidden = true;
  setSources([]);
  updateContext();
  resizeQuestion();
  state.chatAutoFollow = true;
  showWelcomeMessage();
  elements.question.focus();
}

function scrollMessagesToBottom(force = false) {
  if (!force && !state.chatAutoFollow) return;
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function setSources(sources = []) {
  elements.sourceList.replaceChildren(); elements.sourceCount.textContent = String(sources.length); elements.sourceDrawer.hidden = !sources.length;
  for (const source of sources) {
    // Link to the course website when one is set in wendao.toml; otherwise show the source as plain text.
    const item = document.createElement(source.url ? "a" : "div"); item.className = "source-item";
    if (source.url) { item.href = source.url; item.target = "_blank"; item.rel = "noopener noreferrer"; }
    const title = document.createElement("strong"); title.textContent = source.title || "Course material";
    const path = document.createElement("small"); path.textContent = [source.file_path, source.location].filter(Boolean).join(", ");
    item.append(title, path); elements.sourceList.appendChild(item);
  }
}

async function streamQuestion(query, assistant, options = {}) {
  elements.suggestions.hidden = true;
  const response = await fetch("/api/answer/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(signedIn() ? { Authorization: `Bearer ${signedIn().token}` } : {}) },
    body: JSON.stringify({
      query, context_node_ids: state.selectedId ? [state.selectedId] : [], short_memory: readMemory(),
      ...(studentAi() ? { ai: studentAi() } : {}),
    }),
    signal: options.signal,
  });
  if (!response.ok || !response.body) throw new Error("The course assistant is not available right now.");
  const reader = response.body.getReader(); const decoder = new TextDecoder();
  let buffer = ""; let answer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n"); buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "error") { const failure = new Error(event.message || "The course assistant could not answer."); failure.code = event.error; throw failure; }
      if (event.type === "start") setSources(event.sources || []);
      if (event.type === "delta") { answer += event.text || ""; renderMarkdown(assistant.body, answer); scrollMessagesToBottom(); }
      if (event.type === "done") { answer = event.answer || answer; renderMarkdown(assistant.body, answer); setSources(event.sources || []); }
    }
    if (done) break;
  }
  if (!answer) throw new Error("The course assistant returned an empty response.");
  return answer;
}

async function explainSelectedNode(node) {
  clearNodeExplanation();
  const controller = new AbortController();
  state.activeExplanationController = controller;
  setExplainButtonBusy(true);
  const assistant = addAssistantMessage({ className: "auto-explanation", heading: `About ${node.label}` });
  const query = `Explain ${node.label} using only the course materials. Focus on what it means in this course and mention important connections to other chapters or concepts only when the retrieved evidence supports them.`;
  try {
    const answer = await streamQuestion(query, assistant, { signal: controller.signal });
    if (state.activeExplanationController !== controller) return;
    const memory = readMemory();
    memory.push({ role: "user", content: query }, { role: "assistant", content: answer });
    writeMemory(memory);
  } catch (error) {
    if (error.name === "AbortError") {
      assistant.article.remove();
      return;
    }
    assistant.article.classList.add("error");
    assistant.body.textContent = error.message;
  } finally {
    if (state.activeExplanationController === controller) {
      state.activeExplanationController = null;
      setExplainButtonBusy(false);
    }
    scrollMessagesToBottom();
  }
}

async function submitQuestion(query) {
  const text = query.trim(); if (!text || elements.send.disabled) return;
  if (courseAi.needs_student_key && !studentAi()) {
    openAiSettings("This course asks you to use your own AI key. Add it to start asking questions.");
    return;
  }
  if (courseAi.login_required && !studentAi() && !signedIn()) {
    openSignIn("Sign in with your email to ask the course AI.");
    return;
  }
  abortAutomaticExplanation();
  const controller = new AbortController();
  state.activeQuestionController = controller;
  elements.question.value = ""; resizeQuestion(); elements.send.disabled = true; elements.suggestions.hidden = true;
  addUserMessage(text); const assistant = addAssistantMessage();
  try {
    const answer = await streamQuestion(text, assistant, { signal: controller.signal });
    if (state.activeQuestionController !== controller) return;
    const memory = readMemory(); memory.push({ role: "user", content: text }, { role: "assistant", content: answer }); writeMemory(memory);
  } catch (error) {
    if (error.name === "AbortError") {
      assistant.article.remove();
      return;
    }
    assistant.article.classList.add("error"); assistant.body.textContent = error.message;
    if (error.code === "LoginRequired") { localStorage.removeItem(SIGN_IN_KEY); updateSignInButton(); openSignIn(error.message); }
  } finally {
    if (state.activeQuestionController === controller) {
      state.activeQuestionController = null;
      elements.send.disabled = false;
      elements.question.focus();
    }
    scrollMessagesToBottom();
  }
}

function resizeQuestion() {
  elements.question.style.height = "auto"; elements.question.style.height = `${Math.min(elements.question.scrollHeight, 140)}px`;
}

async function initialize() {
  try {
    const response = await fetch("/api/graph");
    if (!response.ok) throw new Error("Graph data could not be loaded.");
    indexGraph(await response.json()); setInitialView();
  } catch (error) {
    elements.nodeLayer.replaceChildren(); elements.edgeLayer.replaceChildren();
    const message = document.createElement("div"); message.className = "graph-load-error"; message.textContent = error.message; elements.stage.appendChild(message);
  }
}

elements.search.addEventListener("input", () => state.graph && showSearchResults(elements.search.value));
elements.search.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { elements.search.value = ""; showSearchResults(""); elements.search.blur(); }
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); elements.search.focus(); }
  if (event.key === "Escape" && state.chatOpen) setChatOpen(false);
  else if (event.key === "Escape" && !elements.nodeCard.hidden) elements.nodeCard.hidden = true;
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".search-box") && !event.target.closest(".search-results")) elements.searchResults.hidden = true;
});
document.querySelector("#reset-view").addEventListener("click", setInitialView);
document.querySelector("#fit-view").addEventListener("click", fitGraph);
elements.legendChapter.addEventListener("click", () => setViewMode("chapter", { toggle: true }));
elements.legendConcept.addEventListener("click", () => setViewMode("concept", { toggle: true }));
elements.explainNode.addEventListener("click", () => {
  const node = state.nodesById.get(state.selectedId);
  if (node) explainSelectedNode(node);
});
document.querySelector("#close-node-card").addEventListener("click", () => { elements.nodeCard.hidden = true; });
document.querySelector("#ask-node").addEventListener("click", () => {
  const node = state.nodesById.get(state.selectedId); if (!node) return;
  elements.question.value = "";
  elements.question.placeholder = `Ask about ${node.label}…`;
  resizeQuestion();
  setChatOpen(true);
});
elements.openChat.addEventListener("click", () => setChatOpen(true));
elements.clearChat.addEventListener("click", clearConversation);
elements.closeChat.addEventListener("click", () => setChatOpen(false));
elements.contextChip.addEventListener("click", () => setViewMode(state.mode));
elements.sourceToggle.addEventListener("click", () => {
  const open = elements.sourceToggle.getAttribute("aria-expanded") === "true";
  elements.sourceToggle.setAttribute("aria-expanded", String(!open)); elements.sourceList.hidden = open;
});
elements.form.addEventListener("submit", (event) => { event.preventDefault(); submitQuestion(elements.question.value); });
elements.question.addEventListener("input", resizeQuestion);
elements.messages.addEventListener("scroll", () => {
  const distanceFromBottom = elements.messages.scrollHeight - elements.messages.scrollTop - elements.messages.clientHeight;
  state.chatAutoFollow = distanceFromBottom < 72;
});
elements.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); elements.form.requestSubmit(); }
});
window.addEventListener("resize", () => { if (state.graph) fitGraph(); });
elements.aiButton.addEventListener("click", () => openAiSettings());
elements.aiDialog.addEventListener("close", () => {
  if (elements.aiDialog.returnValue === "save") {
    localStorage.setItem(AI_SETTINGS_KEY, JSON.stringify({
      provider: elements.aiProvider.value, model: elements.aiModel.value.trim(),
      base_url: elements.aiBaseUrl.value.trim(), api_key: elements.aiKey.value.trim(),
    }));
  }
  updateAiBadge();
});
elements.signInButton.addEventListener("click", () => openSignIn());
document.querySelector("#sign-in-submit").addEventListener("click", signIn);
elements.signInEmail.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); signIn(); } });
document.querySelector("#sign-out").addEventListener("click", () => {
  localStorage.removeItem(SIGN_IN_KEY); updateSignInButton(); elements.signInDialog.close("cancel");
});
document.querySelector("#ai-forget").addEventListener("click", () => {
  localStorage.removeItem(AI_SETTINGS_KEY); elements.aiDialog.close("cancel");
});

initialize();
loadCourseAi();
