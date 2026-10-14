const SVG_NS = "http://www.w3.org/2000/svg";
const MEMORY_KEY = "wendao_course_memory_v1";
const MAX_MEMORY_ITEMS = 6;
const AI_SETTINGS_KEY = "wendao_ai_settings_v1";
const SIGN_IN_KEY = "wendao_sign_in_v1";

// Shown inside the course-website widget's Graph tab (?embed): no chat panel here. Explain and Ask go to the
// widget's chat, the widget says which page the student is on (focus) and whether the site is dark.
const PARAMS = new URLSearchParams(window.location.search);
const EMBEDDED = PARAMS.has("embed") && window.parent !== window;
// A file made by `wendao export`: the graph is inside, there is no server. The AI lives online, if the teacher said where.
const EXPORTED = window.WENDAO_EXPORT || null;
if (EXPORTED) document.documentElement.classList.add(EXPORTED.online ? "exported" : "exported-offline");
function openOnline(node) {
  window.open(node ? `${EXPORTED.online}?focus=${encodeURIComponent(node.id)}` : EXPORTED.online, "_blank", "noopener");
}
// Appearance: the style the teacher chose ([course] style in wendao.toml), unless this reader picked another one
// (saved in the browser); `?style=` previews one. Light or dark follows the computer unless picked too.
const LOOK_KEY = "wendao_look_v1";
function savedLook() {
  try { return JSON.parse(localStorage.getItem(LOOK_KEY) || "{}") || {}; } catch { return {}; }
}
function currentStyle() {
  return PARAMS.get("style") || (!EMBEDDED && savedLook().style) || document.documentElement.dataset.style || "modern";
}
document.documentElement.classList.toggle("style-modern", currentStyle() === "modern");
if (EMBEDDED) {
  document.documentElement.classList.add("embedded");
  document.documentElement.classList.toggle("dark", PARAMS.get("theme") === "dark");
}
function tellWidget(type, node) {
  window.parent.postMessage({ source: "wendao-graph", type, id: node.id, label: node.label, kind: node.type }, "*");
}

const elements = {
  shell: document.querySelector(".app-shell"),
  canvas: document.querySelector("#graph-canvas"),
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
  legendPage: document.querySelector("#legend-page"),
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
  if (EXPORTED) return; // no server to ask
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
  sim: null,
  alpha: 1,
  alphaTarget: 0,
  frame: null,
  viewFrame: null,
  fitPending: true,
  hiddenKinds: new Set(), // node kinds switched off in the legend
  hoverId: null,
  selectedId: null,
  transform: { x: 0, y: 0, k: 1 },
  ratio: 1,
  size: null,
  viewTouched: false, // the student panned or zoomed: don't move the view for them
  colours: null,
  pointer: null,
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
    if (state.graph) { resizeCanvas(); fitGraph(); }
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

// ---------- the knowledge graph: every chapter, page and concept, drawn on a canvas with live physics ----------
// Like Obsidian's graph view: nodes push each other apart and links pull like springs, so the layout settles by
// itself. Dragging a node pulls its neighbours along and it springs back when let go. Hover a node to see what it
// connects to; zoom in for labels.
const KINDS = { chapter: "Chapter", topic: "Page", keyword: "Concept" };
// The layout uses a tidy skeleton: a chapter holds its pages, and each concept hangs on the one or two chapters
// that use it most (concepts shared by chapters sit between them). Every chapter becomes a cluster, like the
// "dandelions" in Obsidian. All other links (which pages mention a concept, ...) light up on hover and selection.
const LINK_DISTANCE = { contains: 40, covers: 60, sequence: 240 };
const LINK_STRENGTH = { contains: 0.8, covers: 0.5, sequence: 0.04 };
const CHAPTERS_PER_CONCEPT = 2;
const SHOWN_LINK_TYPES = new Set(["contains", "covers", "mentions", "sequence"]); // what hover shows
const CHARGE = { chapter: -1400, topic: -90, keyword: -110 };
const VELOCITY_DECAY = 0.4; // calm: nodes glide to a stop
// Once the graph has settled, each node remembers its spot and is pulled back to it like a rubber band.
const HOME_PULL = 0.05; // gentle: it drifts back rather than snapping
const ALPHA_MIN = 0.001;
const ALPHA_DECAY = 1 - Math.pow(ALPHA_MIN, 1 / 300);

function visualType(node) {
  return node.type === "chapter" ? "chapter" : "concept";
}
function primaryChapters() {
  return state.graph.nodes
    .filter((node) => node.type === "chapter" && node.visibility === "primary")
    .sort((a, b) => a.order - b.order);
}
function indexGraph(graph) {
  state.graph = graph;
  state.nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  state.edgesByNode = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) {
    state.edgesByNode.get(edge.source)?.push(edge);
    state.edgesByNode.get(edge.target)?.push(edge);
  }
  buildSimulation();
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

// Which nodes students see: no hidden chapters, and nothing that belongs only to hidden chapters.
function isShown(node) {
  if (!node || node.visibility === "hidden") return false;
  if (node.type === "topic") return state.nodesById.get(node.chapter_id)?.visibility !== "hidden";
  return true;
}

function buildSimulation() {
  const sim = { nodes: [], byId: new Map(), links: [], neighbours: new Map() };
  for (const node of state.graph.nodes) {
    if (!isShown(node)) continue;
    const item = { id: node.id, node, x: 0, y: 0, vx: 0, vy: 0, fx: null, fy: null, degree: 0, r: 4 };
    sim.nodes.push(item); sim.byId.set(node.id, item); sim.neighbours.set(node.id, new Set());
  }
  for (const edge of state.graph.edges) {
    if (!SHOWN_LINK_TYPES.has(edge.type) || !sim.byId.has(edge.source) || !sim.byId.has(edge.target) || edge.source === edge.target) continue;
    sim.neighbours.get(edge.source).add(edge.target); sim.neighbours.get(edge.target).add(edge.source);
  }
  for (const edge of skeletonEdges(sim)) {
    const source = sim.byId.get(edge.source); const target = sim.byId.get(edge.target);
    sim.links.push({ source, target, distance: LINK_DISTANCE[edge.type], strength: LINK_STRENGTH[edge.type] * (edge.secondary ? 0.4 : 1) });
  }
  const layoutDegree = new Map();
  for (const { source, target } of sim.links) {
    layoutDegree.set(source.id, (layoutDegree.get(source.id) || 0) + 1); layoutDegree.set(target.id, (layoutDegree.get(target.id) || 0) + 1);
  }
  for (const link of sim.links) {
    const a = layoutDegree.get(link.source.id); const b = layoutDegree.get(link.target.id);
    link.bias = a / (a + b);
  }
  for (const item of sim.nodes) item.degree = sim.neighbours.get(item.id).size; // how connected it really is
  for (const item of sim.nodes) {
    // Size grows with the number of links, like Obsidian: hubs stand out.
    const base = item.node.type === "chapter" ? 7 : item.node.type === "keyword" ? 3.2 : 2.8;
    item.r = Math.min(base + Math.sqrt(item.degree) * (item.node.type === "chapter" ? 1.4 : 1.05), 22);
  }
  state.sim = sim;
  state.shown = null;
  seedPositions();
  state.alpha = 1;
  state.homesSet = false;
}

function skeletonEdges(sim) {
  const usable = (type) => state.graph.edges.filter((edge) => edge.type === type && edge.source !== edge.target
    && sim.byId.has(edge.source) && sim.byId.has(edge.target));
  const edges = [...usable("contains"), ...usable("sequence")];
  const byConcept = new Map();
  for (const edge of usable("covers")) {
    const concept = sim.byId.get(edge.source).node.type === "keyword" ? edge.source : edge.target;
    if (!byConcept.has(concept)) byConcept.set(concept, []);
    byConcept.get(concept).push(edge);
  }
  for (const list of byConcept.values()) {
    list.sort((a, b) => (b.weight || 0) - (a.weight || 0))
      .slice(0, CHAPTERS_PER_CONCEPT)
      .forEach((edge, index) => edges.push(index ? { ...edge, secondary: true } : edge));
  }
  return edges;
}

// A sensible start so the physics settles quickly: chapters on a ring, pages next to their chapter,
// concepts between the chapters whose pages mention them.
function seedPositions() {
  const sim = state.sim;
  const chapters = sim.nodes.filter((item) => item.node.type === "chapter").sort((a, b) => (a.node.order ?? 0) - (b.node.order ?? 0));
  const ring = 120 + chapters.length * 22;
  chapters.forEach((item, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(chapters.length, 1)) * Math.PI * 2;
    item.x = Math.cos(angle) * ring; item.y = Math.sin(angle) * ring;
  });
  const jitter = () => (Math.random() - 0.5) * 40;
  for (const item of sim.nodes) {
    if (item.node.type === "topic") {
      const chapter = sim.byId.get(item.node.chapter_id);
      item.x = (chapter ? chapter.x * 1.25 : 0) + jitter(); item.y = (chapter ? chapter.y * 1.25 : 0) + jitter();
    }
  }
  for (const item of sim.nodes) {
    if (item.node.type !== "keyword") continue;
    const chapters = [...sim.neighbours.get(item.id)].map((id) => sim.byId.get(id)).filter((other) => other?.node.type === "chapter");
    const mean = (key) => (chapters.length ? chapters.reduce((sum, chapter) => sum + chapter[key], 0) / chapters.length : 0);
    item.x = mean("x") * 1.2 + jitter(); item.y = mean("y") * 1.2 + jitter();
  }
}

// What the legend lets through, kept until the legend changes (not rebuilt every frame).
function shownNodes() {
  if (!state.shown) refreshShown();
  return state.shown.nodes;
}
function shownLinks() {
  if (!state.shown) refreshShown();
  return state.shown.links;
}
function refreshShown() {
  const hidden = state.hiddenKinds;
  state.shown = {
    nodes: state.sim.nodes.filter((item) => !hidden.has(item.node.type)),
    links: state.sim.links.filter((link) => !hidden.has(link.source.node.type) && !hidden.has(link.target.node.type)),
  };
}
function isShownNode(item) {
  return item && !state.hiddenKinds.has(item.node.type);
}

// One step of the physics (the same forces as d3-force: charge, links, a gentle pull to the middle).
// Push between nodes. Small graphs: every pair (exact, under 1 ms for a few hundred nodes). Large graphs: a
// Barnes-Hut quadtree, where a far-away group of nodes pushes like one, so the cost grows like n log n.
const BARNES_HUT_FROM = 300;
// How far a group must be to push like one node (squared): 0.9 as in d3, coarser for very large graphs.
const theta2 = (n) => (n > 2000 ? 1.44 : 0.81);

function chargeExact(nodes, alpha) {
  for (let i = 0; i < nodes.length; i += 1) {
    const a = nodes[i]; const chargeA = CHARGE[a.node.type];
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = nodes[j];
      let dx = b.x - a.x; let dy = b.y - a.y;
      let l = dx * dx + dy * dy;
      if (l > 640000) continue; // far apart: no push
      if (l < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; l = 1; }
      const pushA = (CHARGE[b.node.type] * alpha) / l; const pushB = (chargeA * alpha) / l;
      a.vx += dx * pushA; a.vy += dy * pushA;
      b.vx -= dx * pushB; b.vy -= dy * pushB;
    }
  }
}

// The quadtree lives in flat typed arrays (no objects per cell), and is walked with a reused stack.
const tree = { size: 0, x0: null, y0: null, side: null, charge: null, cx: null, cy: null, kid: null, item: null, stack: new Int32Array(4096) };
function treeCell(x0, y0, side) {
  const cell = tree.size++;
  tree.x0[cell] = x0; tree.y0[cell] = y0; tree.side[cell] = side;
  tree.charge[cell] = 0; tree.cx[cell] = 0; tree.cy[cell] = 0; tree.kid[cell] = -1; tree.item[cell] = -1;
  return cell;
}
function chargeBarnesHut(nodes, alpha) {
  const n = nodes.length; const capacity = n * 8 + 16;
  if (!tree.x0 || tree.x0.length < capacity) {
    for (const key of ["x0", "y0", "side", "charge", "cx", "cy"]) tree[key] = new Float64Array(capacity);
    tree.kid = new Int32Array(capacity); tree.item = new Int32Array(capacity);
  }
  tree.size = 0;
  let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
  const q = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const item = nodes[i]; q[i] = CHARGE[item.node.type];
    if (item.x < minX) minX = item.x; if (item.y < minY) minY = item.y; if (item.x > maxX) maxX = item.x; if (item.y > maxY) maxY = item.y;
  }
  const root = treeCell(minX, minY, Math.max(maxX - minX, maxY - minY, 1));
  const quadrant = (cell, i) => (nodes[i].x >= tree.x0[cell] + tree.side[cell] / 2 ? 1 : 0) + (nodes[i].y >= tree.y0[cell] + tree.side[cell] / 2 ? 2 : 0);
  for (let i = 0; i < n; i += 1) {
    let cell = root; let depth = 0;
    for (;;) {
      const total = tree.charge[cell] + q[i];
      tree.cx[cell] = (tree.cx[cell] * tree.charge[cell] + nodes[i].x * q[i]) / total;
      tree.cy[cell] = (tree.cy[cell] * tree.charge[cell] + nodes[i].y * q[i]) / total;
      tree.charge[cell] = total;
      if (tree.kid[cell] < 0 && tree.item[cell] < 0 && total === q[i]) { tree.item[cell] = i; break; }
      if (depth > 24 || tree.size + 4 > capacity) break; // nodes on top of each other
      if (tree.kid[cell] < 0) {
        const half = tree.side[cell] / 2; const first = tree.size;
        for (let k = 0; k < 4; k += 1) treeCell(tree.x0[cell] + (k & 1) * half, tree.y0[cell] + (k >> 1) * half, half);
        tree.kid[cell] = first;
        const old = tree.item[cell]; tree.item[cell] = -1;
        if (old >= 0) { // move the node that was here one level down
          const down = first + quadrant(cell, old);
          tree.charge[down] = q[old]; tree.cx[down] = nodes[old].x; tree.cy[down] = nodes[old].y; tree.item[down] = old;
        }
      }
      cell = tree.kid[cell] + quadrant(cell, i); depth += 1;
    }
  }
  if (tree.stack.length < tree.size) tree.stack = new Int32Array(tree.size * 2);
  const stack = tree.stack; const openFrom = theta2(n);
  for (let i = 0; i < n; i += 1) {
    const item = nodes[i]; let top = 0; stack[top++] = root;
    while (top) {
      const cell = stack[--top];
      if (!tree.charge[cell] || tree.item[cell] === i) continue;
      let dx = tree.cx[cell] - item.x; let dy = tree.cy[cell] - item.y; let l = dx * dx + dy * dy;
      const side = tree.side[cell];
      if (tree.kid[cell] >= 0 && side * side > openFrom * Math.max(l, 1)) {
        const first = tree.kid[cell]; stack[top++] = first; stack[top++] = first + 1; stack[top++] = first + 2; stack[top++] = first + 3;
        continue;
      }
      if (l > 640000) continue;
      if (l < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; l = 1; }
      const push = (tree.charge[cell] * alpha) / l;
      item.vx += dx * push; item.vy += dy * push;
    }
  }
}

// Nodes never overlap; only nodes in the same or neighbouring grid squares are compared.
const SEPARATE_CELL = 60;
function separate(nodes) {
  const n = nodes.length; const grid = new Map(); const cellOf = new Int32Array(n * 2);
  for (let i = 0; i < n; i += 1) {
    const gx = Math.floor(nodes[i].x / SEPARATE_CELL); const gy = Math.floor(nodes[i].y / SEPARATE_CELL);
    cellOf[i * 2] = gx; cellOf[i * 2 + 1] = gy;
    const key = gx * 1000003 + gy; const bucket = grid.get(key);
    if (bucket) bucket.push(i); else grid.set(key, [i]);
  }
  for (let i = 0; i < n; i += 1) {
    const a = nodes[i]; const gx = cellOf[i * 2]; const gy = cellOf[i * 2 + 1];
    for (let ox = -1; ox <= 1; ox += 1) for (let oy = -1; oy <= 1; oy += 1) {
      const bucket = grid.get((gx + ox) * 1000003 + gy + oy);
      if (!bucket) continue;
      for (const j of bucket) {
        if (j <= i) continue;
        const b = nodes[j]; const dx = b.x - a.x; const dy = b.y - a.y; const l = dx * dx + dy * dy; const room = a.r + b.r + 8;
        if (l >= room * room || l === 0) continue;
        const d = Math.sqrt(l); const shift = ((room - d) / d) * 0.25;
        a.vx -= dx * shift; a.vy -= dy * shift; b.vx += dx * shift; b.vy += dy * shift;
      }
    }
  }
}

function tick() {
  const alpha = state.alpha;
  const nodes = shownNodes();
  if (nodes.length >= BARNES_HUT_FROM) chargeBarnesHut(nodes, alpha); else chargeExact(nodes, alpha);
  separate(nodes);
  for (const link of shownLinks()) {
    const { source, target } = link;
    let dx = target.x + target.vx - source.x - source.vx; let dy = target.y + target.vy - source.y - source.vy;
    let l = Math.sqrt(dx * dx + dy * dy) || 1e-6;
    l = ((l - link.distance) / l) * alpha * link.strength;
    dx *= l; dy *= l;
    target.vx -= dx * link.bias; target.vy -= dy * link.bias;
    source.vx += dx * (1 - link.bias); source.vy += dy * (1 - link.bias);
  }
  for (const item of nodes) {
    item.vx -= item.x * 0.01 * alpha; item.vy -= item.y * 0.01 * alpha; // gentle pull to the middle
    if (item.hx !== undefined) { item.vx += (item.hx - item.x) * HOME_PULL * alpha; item.vy += (item.hy - item.y) * HOME_PULL * alpha; }
    if (item.fx !== null) { item.x = item.fx; item.y = item.fy; item.vx = 0; item.vy = 0; continue; }
    item.vx *= 1 - VELOCITY_DECAY; item.vy *= 1 - VELOCITY_DECAY;
    item.x += item.vx; item.y += item.vy;
  }
  state.alpha += (state.alphaTarget - state.alpha) * ALPHA_DECAY;
}

function startPhysics(alpha) {
  if (alpha !== undefined) state.alpha = Math.max(state.alpha, alpha);
  if (state.frame) return;
  const loop = () => {
    tick();
    if (state.fitPending && state.alpha < 0.08) { state.fitPending = false; fitGraph(); }
    if (!state.homesSet && state.alpha < 0.02) rememberHomes();
    render();
    if (state.alpha > ALPHA_MIN || state.alphaTarget > 0) state.frame = requestAnimationFrame(loop);
    else state.frame = null;
  };
  state.frame = requestAnimationFrame(loop);
}

function rememberHomes() {
  for (const item of state.sim.nodes) { item.hx = item.x; item.hy = item.y; }
  state.homesSet = true;
}

// ---------- drawing ----------
function readColours() {
  updateTabIcon();
  const style = getComputedStyle(document.documentElement);
  const value = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  state.colours = {
    chapter: value("--chapter", "#2563eb"), topic: value("--page", "#64748b"), keyword: value("--concept", "#d97706"),
    text: value("--text", "#17233a"), link: value("--link", "rgba(70, 94, 128, 0.22)"),
    linkActive: value("--link-active", "rgba(37, 99, 235, 0.65)"), halo: value("--bg", "#f7f9fc"), glow: value("--glow", ""),
  };
}

// The browser-tab icon in the same colours as the logo on the page.
function updateTabIcon() {
  const style = getComputedStyle(document.documentElement);
  const colour = (name) => style.getPropertyValue(name).trim();
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'><defs><linearGradient id='t' x1='0' y1='0' x2='1' y2='1'>`
    + `<stop offset='0' stop-color='${colour("--logo-from")}'/><stop offset='1' stop-color='${colour("--logo-to")}'/></linearGradient></defs>`
    + `<rect x='4' y='4' width='120' height='120' rx='28' fill='url(#t)'/><path d='M44 50 C44 35 53 27 64 27 C77 27 86 36 86 47 C86 58 78 63 71 67 C66 70 64 73 64 81' `
    + `fill='none' stroke='${colour("--logo-mark")}' stroke-width='10' stroke-linejoin='round'/><circle cx='64' cy='98' r='7' fill='${colour("--logo-dot")}'/></svg>`;
  const icon = document.querySelector('link[rel="icon"]');
  if (icon) icon.href = `data:image/svg+xml,${svg.replace(/#/g, "%23")}`;
}

// Pixels per CSS pixel. Sharp (up to 2) when the graph is still; while it moves on a device that can't keep up
// (most often a phone, with 4-9 times the pixels of its CSS size), fewer pixels, then sharp again when it stops.
const sharpRatio = () => Math.min(window.devicePixelRatio || 1, 2);
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const motion = { last: 0, slow: 0, fast: 0, ratio: 0, restore: null };

function setCanvasRatio(ratio) {
  const canvas = elements.canvas; const { width, height } = state.size;
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  state.ratio = ratio;
}

// Called for every drawn frame: count slow frames while moving, and go sharp again once things are still.
function watchFrameTime() {
  const now = performance.now(); const gap = now - motion.last; motion.last = now;
  const sharp = sharpRatio();
  if (!motion.ratio) motion.ratio = sharp;
  if (gap < 100) { // moving: frames follow each other
    motion.slow = gap > 24 ? motion.slow + 1 : Math.max(0, motion.slow - 1);
    motion.fast = gap < 20 ? motion.fast + 1 : 0;
    if (motion.slow >= 6 && motion.ratio > 1) { motion.ratio = Math.max(1, motion.ratio - 0.5); motion.slow = 0; }
    if (motion.fast >= 120 && motion.ratio < sharp) { motion.ratio = Math.min(sharp, motion.ratio + 0.5); motion.fast = 0; } // keeping up easily: try sharper
    if (state.ratio !== motion.ratio) setCanvasRatio(motion.ratio);
  }
  clearTimeout(motion.restore);
  if (state.ratio !== sharp) motion.restore = setTimeout(() => { setCanvasRatio(sharp); render(); }, 180);
}

function resizeCanvas() {
  const canvas = elements.canvas; const ratio = sharpRatio();
  const width = elements.stage.clientWidth; const height = elements.stage.clientHeight;
  // Keep the same part of the graph in the middle when the area grows or shrinks (e.g. the widget is enlarged).
  if (state.size) { state.transform.x += (width - state.size.width) / 2; state.transform.y += (height - state.size.height) / 2; }
  state.size = { width, height };
  canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
  setCanvasRatio(ratio);
  draw();
  // Bigger or smaller area (e.g. the widget was enlarged): frame the same thing again, unless the view was moved by hand.
  if (state.sim && !state.viewTouched && !state.fitPending) {
    clearTimeout(state.refitTimer);
    state.refitTimer = setTimeout(() => (state.selectedId ? focusOn(state.selectedId) : fitGraph()), 120);
  }
}

// The node the graph is about right now: the one under the pointer, else the selected one.
function focusId() {
  return state.hoverId || state.selectedId;
}

// Several things may ask for a redraw in one frame (hover, zoom, physics): draw once per frame.
function draw() {
  if (state.drawQueued) return;
  state.drawQueued = true;
  requestAnimationFrame(() => { state.drawQueued = false; render(); });
}

function render() {
  if (!state.sim || !state.colours) return;
  if (state.size) watchFrameTime();
  const canvas = elements.canvas; const context = canvas.getContext("2d");
  const { x, y, k } = state.transform; const ratio = state.ratio || 1;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.setTransform(ratio * k, 0, 0, ratio * k, ratio * x, ratio * y);
  const colours = state.colours;
  const focus = focusId();
  const near = focus ? new Set([focus, ...state.sim.neighbours.get(focus) || []]) : null;
  // Highlights fade in quickly instead of switching on at once.
  const now = performance.now();
  if (focus !== state.lastFocus) { state.lastFocus = focus; state.focusSince = now; }
  const fade = !near || REDUCED_MOTION ? 1 : Math.min(1, (now - state.focusSince) / 180);
  if (fade < 1) draw();
  // The part of the graph on screen (with a margin): anything outside is not drawn.
  const view = { left: -x / k - 60, top: -y / k - 60, right: (canvas.width / ratio - x) / k + 60, bottom: (canvas.height / ratio - y) / k + 60 };
  const onScreen = (item) => item.x > view.left && item.x < view.right && item.y > view.top && item.y < view.bottom;

  context.lineWidth = 1 / k;
  context.strokeStyle = colours.link;
  context.globalAlpha = near ? 0.7 - 0.45 * fade : 0.7;
  context.beginPath();
  for (const { source, target } of shownLinks()) {
    if ((source.x < view.left && target.x < view.left) || (source.x > view.right && target.x > view.right)
      || (source.y < view.top && target.y < view.top) || (source.y > view.bottom && target.y > view.bottom)) continue;
    context.moveTo(source.x, source.y); context.lineTo(target.x, target.y);
  }
  context.stroke();
  if (near) {
    const centre = state.sim.byId.get(focus);
    context.globalAlpha = fade; context.strokeStyle = colours.linkActive; context.lineWidth = 1.4 / k;
    context.beginPath();
    for (const id of state.sim.neighbours.get(focus) || []) {
      const other = state.sim.byId.get(id);
      if (!isShownNode(other) || !isShownNode(centre)) continue;
      context.moveTo(centre.x, centre.y); context.lineTo(other.x, other.y);
    }
    context.stroke();
  }

  // Nodes: one fill per colour (and per faded / not faded), not one per node.
  const nodes = shownNodes();
  const batches = new Map();
  for (const item of nodes) {
    if (!onScreen(item)) continue;
    const key = `${item.node.type}|${near && !near.has(item.id) ? 1 : 0}`;
    if (!batches.has(key)) batches.set(key, []);
    batches.get(key).push(item);
  }
  for (const [key, items] of batches) {
    const [type, faded] = key.split("|");
    context.globalAlpha = faded === "1" ? 1 - 0.82 * fade : 1;
    context.fillStyle = colours[type] || colours.keyword;
    context.beginPath();
    for (const item of items) { context.moveTo(item.x + item.r, item.y); context.arc(item.x, item.y, item.r, 0, Math.PI * 2); }
    context.fill();
  }
  const glowing = colours.glow && focus && state.sim.byId.get(focus);
  if (glowing && isShownNode(glowing)) { // a soft glow around the node in focus (modern style)
    context.save(); context.globalAlpha = 1; context.shadowColor = colours.glow; context.shadowBlur = 18 * (state.ratio || 1);
    context.fillStyle = colours[glowing.node.type] || colours.keyword;
    context.beginPath(); context.arc(glowing.x, glowing.y, glowing.r, 0, Math.PI * 2); context.fill(); context.restore();
  }
  const selected = state.selectedId && state.sim.byId.get(state.selectedId);
  if (selected && isShownNode(selected)) {
    context.globalAlpha = 1; context.lineWidth = 2.5 / k; context.strokeStyle = colours.text;
    context.beginPath(); context.arc(selected.x, selected.y, selected.r + 3 / k, 0, Math.PI * 2); context.stroke();
  }

  // A ripple from a node that was just chosen.
  const pulse = state.pulse && state.sim.byId.get(state.pulse.id);
  if (pulse) {
    const t = (now - state.pulse.start) / 650;
    if (t >= 1 || !isShownNode(pulse)) state.pulse = null;
    else {
      const grow = 1 - Math.pow(1 - t, 3);
      context.globalAlpha = 0.55 * (1 - t); context.lineWidth = 2 / k; context.strokeStyle = colours[pulse.node.type] || colours.keyword;
      context.beginPath(); context.arc(pulse.x, pulse.y, pulse.r + (4 + 28 * grow) / k, 0, Math.PI * 2); context.stroke();
      draw();
    }
  }

  drawLabels(context, nodes, near, focus, onScreen);
  context.globalAlpha = 1;
}

// Labels: the focus and its neighbours, chapters, then the best-connected nodes, as long as they don't overlap
// (checked on a coarse grid). Zooming in leaves room for more. Label widths are measured once.
const LABEL_FONT = { chapter: [700, 13], other: [500, 11] };
function drawLabels(context, nodes, near, focus, onScreen) {
  const { k } = state.transform;
  const zoomed = Math.max(0, Math.min(1, (k - 0.6) / 0.6));
  const scale = 1 / Math.max(k, 0.6);
  const candidates = [];
  for (const item of nodes) {
    const isNear = near?.has(item.id);
    let alpha = item.node.type === "chapter" ? 1 : zoomed;
    if (near) alpha = isNear ? 1 : Math.min(alpha, 0.12);
    if (alpha >= 0.05 && onScreen(item)) candidates.push({ item, alpha, isNear });
  }
  if (!candidates.length) return;
  const rank = ({ item, isNear }) => (item.id === focus ? 4 : isNear ? 3 : item.node.type === "chapter" ? 2 : 1) * 1000 + item.degree;
  candidates.sort((a, b) => rank(b) - rank(a));
  context.textAlign = "center"; context.textBaseline = "top"; context.lineJoin = "round";
  const cell = 80 / k; const taken = new Map();
  const cellsOf = (box) => {
    const keys = [];
    for (let gx = Math.floor(box.left / cell); gx <= Math.floor(box.right / cell); gx += 1)
      for (let gy = Math.floor(box.top / cell); gy <= Math.floor(box.bottom / cell); gy += 1) keys.push(`${gx},${gy}`);
    return keys;
  };
  const overlaps = (box, keys) => keys.some((key) => (taken.get(key) || []).some((other) =>
    box.left < other.right && box.right > other.left && box.top < other.bottom && box.bottom > other.top));
  let font = "";
  for (const { item, alpha, isNear } of candidates) {
    const [weight, base] = item.node.type === "chapter" ? LABEL_FONT.chapter : LABEL_FONT.other;
    if (item.labelWidth === undefined) { // measured once at the base size; scaled after
      item.labelText = item.node.label.length > 30 ? `${item.node.label.slice(0, 28)}…` : item.node.label;
      context.font = `${weight} ${base}px Inter, ui-sans-serif, system-ui, sans-serif`; font = "";
      item.labelWidth = context.measureText(item.labelText).width;
    }
    const size = base * scale; const width = item.labelWidth * scale;
    const height = size * 1.2; const gap = 3 / k;
    // Where a label can go: under the node first; a chapter (always named) may also go above, right or left.
    const spots = [[item.x - width / 2, item.y + item.r + gap]];
    if (item.node.type === "chapter") {
      spots.push([item.x - width / 2, item.y - item.r - gap - height], [item.x + item.r + gap * 2, item.y - height / 2],
        [item.x - item.r - gap * 2 - width, item.y - height / 2]);
    }
    let box = null; let keys = null;
    for (const [left, top] of spots) {
      const candidate = { left: left - 2 / k, right: left + width + 2 / k, top, bottom: top + height };
      const candidateKeys = cellsOf(candidate);
      if (!overlaps(candidate, candidateKeys)) { box = candidate; keys = candidateKeys; break; }
    }
    if (!box) {
      if (!isNear && item.node.type !== "chapter") continue; // no room: leave it out (zoom in to see it)
      box = { left: spots[0][0] - 2 / k, right: spots[0][0] + width + 2 / k, top: spots[0][1], bottom: spots[0][1] + height };
      keys = cellsOf(box);
    }
    const top = box.top; const centre = (box.left + box.right) / 2;
    for (const key of keys) { if (!taken.has(key)) taken.set(key, []); taken.get(key).push(box); }
    const wanted = `${weight} ${size}px Inter, ui-sans-serif, system-ui, sans-serif`;
    if (wanted !== font) { context.font = wanted; font = wanted; }
    context.globalAlpha = alpha;
    context.lineWidth = 3 / k; context.strokeStyle = state.colours.halo; context.strokeText(item.labelText, centre, top);
    context.fillStyle = state.colours.text; context.fillText(item.labelText, centre, top);
  }
}

// ---------- views ----------
function updateModeControls() {
  elements.legendChapter.setAttribute("aria-pressed", String(!state.hiddenKinds.has("chapter")));
  elements.legendPage.setAttribute("aria-pressed", String(!state.hiddenKinds.has("topic")));
  elements.legendConcept.setAttribute("aria-pressed", String(!state.hiddenKinds.has("keyword")));
  elements.graphHint.lastChild.textContent = " Drag to move · scroll to zoom · hover to see connections";
}

function toggleKind(kind) {
  if (state.hiddenKinds.has(kind)) state.hiddenKinds.delete(kind); else state.hiddenKinds.add(kind);
  if (state.hiddenKinds.size === 3) state.hiddenKinds.delete(kind); // never hide everything
  state.shown = null;
  if (state.selectedId && state.hiddenKinds.has(state.sim.byId.get(state.selectedId)?.node.type)) clearSelection();
  updateModeControls();
  startPhysics(0.5);
}

function clearSelection() {
  clearNodeExplanation();
  state.selectedId = null;
  elements.nodeCard.hidden = true;
  updateContext();
  draw();
}

function setInitialView() {
  state.hiddenKinds.clear();
  state.shown = null;
  updateModeControls();
  clearSelection();
  state.fitPending = true;
  startPhysics(0.6);
}

function selectNode(nodeId, options = {}) {
  const node = state.nodesById.get(nodeId);
  if (!node) return;
  clearNodeExplanation();
  if (state.hiddenKinds.delete(node.type)) { state.shown = null; updateModeControls(); }
  state.selectedId = nodeId;
  if (!REDUCED_MOTION) state.pulse = { id: nodeId, start: performance.now() };
  showNodeCard(node);
  updateContext();
  updateSuggestions(node);
  if (options.fit) focusOn(nodeId);
  draw();
  if (options.focusQuestion) elements.question.focus();
}

// Move the view smoothly to a node and its neighbours.
function focusOn(nodeId) {
  const item = state.sim.byId.get(nodeId);
  if (!item) return;
  const near = [item, ...[...state.sim.neighbours.get(nodeId)].map((id) => state.sim.byId.get(id))].filter(Boolean);
  animateTo(viewFor(near, 2.2));
}

function viewFor(items, maxScale = 1.6) {
  const width = elements.stage.clientWidth;
  // On a narrow screen the node card covers the bottom of the graph: frame the part above it.
  const card = elements.nodeCard; const covered = !card.hidden && width < 600 ? card.offsetHeight + 20 : 0;
  const height = Math.max(160, elements.stage.clientHeight - covered);
  if (!items.length) return { x: width / 2, y: height / 2, k: 1 };
  const xs = items.map((item) => item.x); const ys = items.map((item) => item.y);
  const minX = Math.min(...xs) - 40; const maxX = Math.max(...xs) + 40;
  const minY = Math.min(...ys) - 40; const maxY = Math.max(...ys) + 50;
  const k = Math.min(maxScale, Math.max(0.12, Math.min(width / (maxX - minX), height / (maxY - minY)) * 0.92));
  return { k, x: width / 2 - ((minX + maxX) / 2) * k, y: height / 2 - ((minY + maxY) / 2) * k };
}

function fitGraph() {
  animateTo(viewFor(shownNodes()));
}

function animateTo(target) {
  state.viewTouched = false;
  const start = { ...state.transform }; const began = performance.now();
  cancelAnimationFrame(state.viewFrame);
  const step = (now) => {
    const t = Math.min(1, (now - began) / 380); const ease = 1 - Math.pow(1 - t, 3);
    for (const key of ["x", "y", "k"]) state.transform[key] = start[key] + (target[key] - start[key]) * ease;
    draw();
    if (t < 1) state.viewFrame = requestAnimationFrame(step);
  };
  state.viewFrame = requestAnimationFrame(step);
}

// ---------- pointer: hover, drag nodes (elastic), pan, zoom ----------
function graphPoint(event) {
  const rect = elements.canvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left - state.transform.x) / state.transform.k, y: (event.clientY - rect.top - state.transform.y) / state.transform.k };
}

function nodeAt(point) {
  let best = null; let bestDistance = Infinity;
  const slack = 5 / state.transform.k;
  for (const item of shownNodes()) {
    const distance = Math.hypot(item.x - point.x, item.y - point.y);
    if (distance <= item.r + slack && distance < bestDistance) { best = item; bestDistance = distance; }
  }
  return best;
}

// Fingers on the graph, for pinch-to-zoom with two.
const touches = new Map();
function pinchState() {
  const [a, b] = [...touches.values()];
  const rect = elements.canvas.getBoundingClientRect();
  return { distance: Math.hypot(a.x - b.x, a.y - b.y) || 1, x: (a.x + b.x) / 2 - rect.left, y: (a.y + b.y) / 2 - rect.top };
}

elements.canvas.addEventListener("pointerdown", (event) => {
  touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
  elements.canvas.setPointerCapture(event.pointerId);
  if (touches.size === 2) { // a second finger: stop dragging, start pinching
    const dragged = state.pointer?.kind === "node" && state.pointer.item;
    if (dragged) { dragged.fx = null; dragged.fy = null; state.alphaTarget = 0; }
    cancelAnimationFrame(state.viewFrame);
    state.pointer = { kind: "pinch", start: pinchState(), transform: { ...state.transform }, moved: true };
    return;
  }
  if (touches.size > 2) return;
  const point = graphPoint(event); const item = nodeAt(point);
  if (item) {
    item.fx = item.x; item.fy = item.y;
    state.alphaTarget = 0.2; startPhysics(0.2); // dragging stirs the graph a little, not a lot
    state.pointer = { kind: "node", item, startX: event.clientX, startY: event.clientY, moved: false };
  } else {
    state.pointer = { kind: "pan", startX: event.clientX, startY: event.clientY, originX: state.transform.x, originY: state.transform.y, moved: false };
  }
});

elements.canvas.addEventListener("pointermove", (event) => {
  const pointer = state.pointer;
  if (!pointer) {
    const item = nodeAt(graphPoint(event));
    const id = item ? item.id : null;
    if (id !== state.hoverId) { state.hoverId = id; elements.canvas.style.cursor = id ? "pointer" : ""; draw(); }
    return;
  }
  if (touches.has(event.pointerId)) touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointer.kind === "pinch") {
    if (touches.size < 2) return;
    const now = pinchState(); const { start, transform } = pointer;
    const k = Math.min(4, Math.max(0.1, transform.k * (now.distance / start.distance)));
    // Keep the point between the fingers under the fingers.
    const gx = (start.x - transform.x) / transform.k; const gy = (start.y - transform.y) / transform.k;
    state.transform = { k, x: now.x - gx * k, y: now.y - gy * k };
    state.viewTouched = true; draw();
    return;
  }
  const dx = event.clientX - pointer.startX; const dy = event.clientY - pointer.startY;
  if (Math.abs(dx) + Math.abs(dy) > 3) pointer.moved = true;
  if (pointer.kind === "pan") {
    state.viewTouched = true;
    state.transform.x = pointer.originX + dx; state.transform.y = pointer.originY + dy; draw();
  } else {
    const point = graphPoint(event); pointer.item.fx = point.x; pointer.item.fy = point.y;
  }
});

function endPointer(event) {
  const pointer = state.pointer;
  touches.delete(event.pointerId);
  if (elements.canvas.hasPointerCapture(event.pointerId)) elements.canvas.releasePointerCapture(event.pointerId);
  if (pointer?.kind === "pinch" && touches.size) return; // one finger still down: the pinch ends with the last one
  state.pointer = null;
  if (!pointer || pointer.kind === "pinch") return;
  if (pointer.kind === "node") {
    pointer.item.fx = null; pointer.item.fy = null; // let go: it springs back into place
    state.alphaTarget = 0;
    startPhysics(0.3); // enough to drift back into place
    if (!pointer.moved && event.type === "pointerup") selectNode(pointer.item.id);
  } else if (!pointer.moved && event.type === "pointerup") {
    clearSelection();
  }
}
elements.canvas.addEventListener("pointerup", endPointer);
elements.canvas.addEventListener("pointercancel", endPointer);
elements.canvas.addEventListener("pointerleave", () => { if (!state.pointer && state.hoverId) { state.hoverId = null; draw(); } });

elements.canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  cancelAnimationFrame(state.viewFrame);
  state.viewTouched = true;
  const rect = elements.canvas.getBoundingClientRect(); const old = state.transform.k;
  const next = Math.min(4, Math.max(0.1, old * Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0015))));
  const px = event.clientX - rect.left; const py = event.clientY - rect.top;
  state.transform.x = px - ((px - state.transform.x) / old) * next;
  state.transform.y = py - ((py - state.transform.y) / old) * next;
  state.transform.k = next;
  draw();
}, { passive: false });

function showNodeCard(node) {
  const kind = visualType(node);
  elements.nodeKind.textContent = node.type === "topic" ? "page" : kind;
  elements.nodeKind.style.color = node.type === "chapter" ? "var(--chapter)" : node.type === "topic" ? "var(--page)" : "var(--concept)";
  elements.nodeTitle.textContent = node.label;
  // A short introduction, from the course text itself, and where to read more.
  const summary = document.querySelector("#node-summary");
  summary.textContent = node.summary || "";
  summary.hidden = !node.summary;
  const openPage = document.querySelector("#open-page");
  openPage.hidden = !node.url;
  if (node.url) {
    openPage.href = node.url;
    openPage.firstChild.textContent = node.type === "topic" ? "Open page " : "Read more in the course ";
  }
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
    let graph = EXPORTED && EXPORTED.graph;
    if (!graph) {
      const response = await fetch("/api/graph");
      if (!response.ok) throw new Error("Graph data could not be loaded.");
      graph = await response.json();
    }
    readColours(); resizeCanvas();
    indexGraph(graph);
    state.transform = { x: elements.stage.clientWidth / 2, y: elements.stage.clientHeight / 2, k: 0.5 };
    setInitialView();
    const focus = PARAMS.get("focus");
    if (focus && state.nodesById.has(focus)) selectNode(focus, { fit: true });
  } catch (error) {
    const message = document.createElement("div"); message.className = "graph-load-error"; message.textContent = error.message; elements.stage.appendChild(message);
  }
}

// A short hint on narrow screens, where the long one is cut off.
const narrow = window.matchMedia("(max-width: 590px)");
const searchHint = () => { elements.search.placeholder = narrow.matches ? "Search" : "Find a chapter or concept"; };
searchHint(); narrow.addEventListener?.("change", searchHint);

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
// Full screen: the whole graph page fills the screen (Escape or the button again leaves).
const fullScreenButton = document.querySelector("#fit-view");
if (!document.fullscreenEnabled) fullScreenButton.hidden = true; // e.g. a frame that doesn't allow it
fullScreenButton.addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch(() => { fullScreenButton.hidden = true; });
});
document.addEventListener("fullscreenchange", () => {
  const on = Boolean(document.fullscreenElement);
  fullScreenButton.title = on ? "Leave full screen" : "Full screen";
  fullScreenButton.setAttribute("aria-label", fullScreenButton.title);
  fullScreenButton.classList.toggle("active", on);
  requestAnimationFrame(() => { resizeCanvas(); });
});
elements.legendChapter.addEventListener("click", () => toggleKind("chapter"));
elements.legendPage.addEventListener("click", () => toggleKind("topic"));
elements.legendConcept.addEventListener("click", () => toggleKind("keyword"));
elements.explainNode.addEventListener("click", () => {
  const node = state.nodesById.get(state.selectedId);
  if (node && EMBEDDED) tellWidget("explain", node);
  else if (node) explainSelectedNode(node);
});
// Explain on the node card: in the widget's chat, the online version (exported file), or this page's chat panel.
document.querySelector("#explain-in-widget").addEventListener("click", () => {
  const node = state.nodesById.get(state.selectedId); if (!node) return;
  if (EMBEDDED) tellWidget("explain", node);
  else if (EXPORTED) openOnline(node);
  else { setChatOpen(true); explainSelectedNode(node); }
});
document.querySelector("#close-node-card").addEventListener("click", () => { elements.nodeCard.hidden = true; });
document.querySelector("#ask-node").addEventListener("click", () => {
  const node = state.nodesById.get(state.selectedId); if (!node) return;
  if (EMBEDDED) { tellWidget("ask", node); return; }
  if (EXPORTED) { openOnline(node); return; }
  elements.question.value = "";
  elements.question.placeholder = `Ask about ${node.label}…`;
  resizeQuestion();
  setChatOpen(true);
});
elements.openChat.addEventListener("click", () => (EXPORTED ? openOnline(null) : setChatOpen(true)));
elements.clearChat.addEventListener("click", clearConversation);
elements.closeChat.addEventListener("click", () => setChatOpen(false));
elements.contextChip.addEventListener("click", clearSelection);
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
// The graph area can change size without the window (the chat panel opens, the widget is resized): follow it,
// once per frame.
let resizeQueued = false;
new ResizeObserver(() => {
  if (resizeQueued || !state.graph) return;
  resizeQueued = true;
  requestAnimationFrame(() => { resizeQueued = false; resizeCanvas(); });
}).observe(elements.stage);
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

if (EMBEDDED) {
  window.addEventListener("message", (event) => {
    const message = event.data || {};
    if (event.source !== window.parent || message.source !== "wendao-widget") return;
    if (message.type === "focus" && state.nodesById.has(message.id)) selectNode(message.id, { fit: true });
    if (message.type === "theme") { document.documentElement.classList.toggle("dark", Boolean(message.dark)); readColours(); draw(); }
  });
}

// Outside the widget: the Appearance menu (style, and light / dark / auto). Auto follows the computer, live.
// Inside the widget the course website decides, so the menu is hidden.
if (!EMBEDDED) {
  const darkSetting = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : { matches: false, addEventListener() {} };
  const button = document.querySelector("#appearance");
  const menu = document.querySelector("#appearance-menu");
  const applyLook = () => {
    const look = savedLook(); const mode = look.mode || "auto"; const style = currentStyle();
    document.documentElement.classList.toggle("style-modern", style === "modern");
    document.documentElement.classList.toggle("dark", mode === "dark" || (mode === "auto" && darkSetting.matches));
    menu.querySelectorAll('[data-choice="style"] button').forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.value === style)));
    menu.querySelectorAll('[data-choice="mode"] button').forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.value === mode)));
    if (state.graph) { readColours(); draw(); }
  };
  menu.querySelectorAll(".segmented button").forEach((item) => item.addEventListener("click", () => {
    const look = savedLook();
    look[item.parentElement.dataset.choice] = item.dataset.value;
    try { localStorage.setItem(LOOK_KEY, JSON.stringify(look)); } catch { /* private mode: still applies until reload */ }
    if (item.parentElement.dataset.choice === "style" && PARAMS.has("style")) PARAMS.delete("style");
    applyLook();
  }));
  const setMenu = (open) => { menu.hidden = !open; button.setAttribute("aria-expanded", String(open)); };
  button.addEventListener("click", (event) => { event.stopPropagation(); setMenu(menu.hidden); });
  document.addEventListener("click", (event) => { if (!menu.hidden && !event.target.closest(".appearance")) setMenu(false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !menu.hidden) { setMenu(false); button.focus(); } });
  applyLook();
  darkSetting.addEventListener("change", () => { if ((savedLook().mode || "auto") === "auto") applyLook(); });
}
