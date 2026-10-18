/*! Wendao graph embed: an interactive knowledge graph for any web page, with no server.
 *  https://github.com/deng-group/wendao (GPL-3.0-or-later)
 *
 *  <div data-wendao-graph data-src="graph.json"></div>
 *  <script src="wendao-graph.js" defer></script>
 *
 *  or from a script:  WendaoGraph.mount(element, { src: "graph.json" })   // or { graph: {...} }
 *
 *  The data is the "wendao-graph/1" format (docs/EMBED.md): nodes with id, type, label and any fields; edges
 *  with source and target; and "types", which say how each node type is named, coloured, labelled and shown
 *  in its card. Colours follow the page: its text colour and background, and dark mode when the page switches.
 */
(function () {
  "use strict";
  if (window.WendaoGraph) return;

  var REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  // The default colours are matsci.dev's: blue hubs, grey and amber nodes, faint links.
  var PALETTE = {
    light: ["#2f5bff", "#9a9a9a", "#d97706", "#a855f7", "#0d9488", "#e11d48"],
    dark: ["#5b7cff", "#8e8e93", "#f59e0b", "#c084fc", "#2dd4bf", "#fb7185"],
  };
  var CREDIT = "https://github.com/deng-group/wendao";
  var ALLOWED_TAGS = /^(sub|sup|i|em|b|strong)$/i;

  function esc(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // Titles may keep <sub>/<sup> (chemical formulae) and simple emphasis; everything else is escaped.
  function safeHtml(text) {
    return esc(text).replace(/&lt;(\/?)([a-z]+)&gt;/gi, function (all, slash, tag) {
      return ALLOWED_TAGS.test(tag) ? "<" + slash + tag.toLowerCase() + ">" : all;
    });
  }
  function plain(text) {
    return String(text == null ? "" : text).replace(/<[^>]*>/g, "");
  }
  function safeUrl(url) {
    return /^(https?:|mailto:|\/|\.\/|\.\.\/|#)/i.test(String(url || "")) ? String(url) : "";
  }
  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }
  // A small seeded random generator: the same graph gets the same layout on every visit.
  function seededRandom(text) {
    var h = 2166136261;
    for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    var state = h >>> 0 || 1;
    return function () {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }

  function mount(root, options) {
    options = options || {};
    if (root.__wendaoGraph) root.__wendaoGraph.destroy();
    var controller = { destroy: function () {}, focus: function () {}, fit: function () {} };
    root.__wendaoGraph = controller;
    root.classList.add("wg");
    if (options.graph) start(options.graph);
    else if (options.src) {
      fetch(options.src).then(function (r) {
        if (!r.ok) throw new Error(r.status + " " + r.statusText);
        return r.json();
      }).then(start).catch(function (error) {
        root.innerHTML = '<p class="wg-error">The graph could not be loaded (' + esc(error.message) + ").</p>";
      });
    }
    return controller;

    function start(data) {
      if (!data || !Array.isArray(data.nodes) || !data.nodes.length) {
        root.innerHTML = '<p class="wg-error">This graph has no nodes.</p>';
        return;
      }
      build(root, data, options, controller);
    }
  }

  function build(root, data, options, controller) {
    // ---------- types ----------
    var types = {};
    var given = Object.assign({}, data.types || {}, options.types || {});
    var order = Object.keys(given);
    data.nodes.forEach(function (n) { if (order.indexOf(n.type) < 0) order.push(n.type); });
    order.forEach(function (name, index) {
      var t = Object.assign({}, given[name] || {});
      t.name = name;
      t.label = t.label || name.charAt(0).toUpperCase() + name.slice(1);
      t.kind = t.kind || t.label;
      t.slot = index % 6;
      t.labels = t.labels || (t.hub ? "always" : "zoom");
      t.card = t.card || {};
      types[name] = t;
    });

    // ---------- page structure ----------
    root.innerHTML = "";
    var canvas = el("canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", options.label || data.label || "Knowledge graph. Select a node for details.");
    var legend = el("div", "wg-legend");
    legend.setAttribute("role", "group");
    legend.setAttribute("aria-label", "Show or hide kinds of nodes");
    var controls = el("div", "wg-controls",
      '<button type="button" data-do="in" aria-label="Zoom in" title="Zoom in">+</button>' +
      '<button type="button" data-do="out" aria-label="Zoom out" title="Zoom out">&minus;</button>' +
      '<button type="button" data-do="fit" aria-label="Fit to view" title="Fit to view">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3.5"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg></button>' +
      '<button type="button" data-do="full" aria-label="Full screen" title="Full screen">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></svg></button>');
    var hint = el("p", "wg-hint",
      '<span class="wg-hint-mouse">Drag to move · click, then scroll to zoom · hover or click a node</span>' +
      '<span class="wg-hint-touch">Pinch to zoom · drag sideways to move · tap a node</span>');
    var wheelHint = el("div", "wg-wheel-hint", "Click the graph to zoom with scroll");
    wheelHint.setAttribute("aria-hidden", "true");
    var card = el("div", "wg-card");
    card.hidden = true;
    card.setAttribute("aria-live", "polite");
    var cardBody = el("div", "wg-card-body");
    var cardClose = el("button", "wg-card-close", "&times;");
    cardClose.type = "button";
    cardClose.setAttribute("aria-label", "Close");
    card.append(cardClose, cardBody);
    root.append(canvas, legend, controls, hint, wheelHint, card);
    if (options.credit !== false) {
      root.append(el("p", "wg-credit", 'Powered by <a href="' + CREDIT + '" target="_blank" rel="noopener">Wendao</a>'));
    }
    if (options.legend === false) legend.hidden = true;
    var ctx = canvas.getContext("2d");

    // ---------- graph ----------
    var nodes = [], byId = new Map(), links = [], neighbours = new Map();
    data.nodes.forEach(function (n) {
      if (!n || n.id == null || byId.has(String(n.id))) return;
      var item = { id: String(n.id), node: n, type: types[n.type] || types[order[0]], x: 0, y: 0, vx: 0, vy: 0, fx: null, fy: null, r: 4, degree: 0 };
      nodes.push(item);
      byId.set(item.id, item);
      neighbours.set(item.id, new Set());
    });
    (data.edges || []).forEach(function (e) {
      var s = byId.get(String(e.source)), t = byId.get(String(e.target));
      if (!s || !t || s === t || neighbours.get(s.id).has(t.id)) return;
      neighbours.get(s.id).add(t.id);
      neighbours.get(t.id).add(s.id);
      links.push({ source: s, target: t });
    });
    nodes.forEach(function (item) {
      item.degree = neighbours.get(item.id).size;
      var hub = item.type.hub;
      item.r = Math.min((hub ? 7 : 3) + Math.sqrt(item.degree) * (hub ? 1.6 : 1.0), 22);
    });
    links.forEach(function (l) {
      var a = l.source.degree, b = l.target.degree;
      l.bias = a / (a + b || 1);
      l.distance = l.source.type.hub || l.target.type.hub ? 70 : 55;
    });

    var hidden = new Set();
    function shown(item) { return !hidden.has(item.type.name); }

    // ---------- seeded layout ----------
    var random = seededRandom(String(options.seed || "") + nodes.map(function (i) { return i.id; }).join("|"));
    (function seed() {
      var hubs = nodes.filter(function (i) { return i.type.hub; }).sort(function (a, b) { return b.degree - a.degree || (a.id < b.id ? -1 : 1); });
      var ring = 90 + hubs.length * 14;
      hubs.forEach(function (item, i) {
        var a = -Math.PI / 2 + (i / hubs.length) * Math.PI * 2;
        item.x = Math.cos(a) * ring; item.y = Math.sin(a) * ring;
      });
      var jitter = function () { return (random() - 0.5) * 40; };
      nodes.forEach(function (item) {
        if (item.type.hub) return;
        var anchors = Array.from(neighbours.get(item.id)).map(function (id) { return byId.get(id); }).filter(function (o) { return o.type.hub; });
        var mx = 0, my = 0;
        anchors.forEach(function (o) { mx += o.x; my += o.y; });
        var k = anchors.length ? 1.3 / anchors.length : 0;
        item.x = mx * k + jitter() + (anchors.length ? 0 : (random() - 0.5) * ring);
        item.y = my * k + jitter() + (anchors.length ? 0 : (random() - 0.5) * ring);
      });
    })();

    // ---------- physics (small graphs: exact repulsion) ----------
    var alpha = 1, frame = null;
    function tick() {
      var list = nodes.filter(shown);
      for (var i = 0; i < list.length; i++) {
        var a = list[i];
        for (var j = i + 1; j < list.length; j++) {
          var b = list[j], dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy || 0.01;
          if (d2 > 160000) continue;
          var f = (-110 * alpha) / d2;
          a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
          var min = a.r + b.r + 4;
          if (d2 < min * min) {
            var d = Math.sqrt(d2), push = ((min - d) / d) * 0.5;
            a.vx -= dx * push; a.vy -= dy * push; b.vx += dx * push; b.vy += dy * push;
          }
        }
      }
      links.forEach(function (l) {
        if (!shown(l.source) || !shown(l.target)) return;
        var dx = l.target.x + l.target.vx - l.source.x - l.source.vx, dy = l.target.y + l.target.vy - l.source.y - l.source.vy;
        var d = Math.sqrt(dx * dx + dy * dy) || 1e-6, s = ((d - l.distance) / d) * alpha * 0.35;
        dx *= s; dy *= s;
        l.target.vx -= dx * l.bias; l.target.vy -= dy * l.bias;
        l.source.vx += dx * (1 - l.bias); l.source.vy += dy * (1 - l.bias);
      });
      list.forEach(function (item) {
        item.vx -= item.x * 0.012 * alpha; item.vy -= item.y * 0.012 * alpha;
        if (item.fx !== null) { item.x = item.fx; item.y = item.fy; item.vx = item.vy = 0; return; }
        item.vx *= 0.6; item.vy *= 0.6;
        item.x += item.vx; item.y += item.vy;
      });
      alpha += (0 - alpha) * 0.03;
    }
    function settle(steps) { for (var i = 0; i < steps; i++) tick(); }
    function animate(heat) {
      alpha = Math.max(alpha, heat);
      if (REDUCED) { settle(120); draw(); return; }
      if (frame || !visible) return;
      var loop = function () {
        tick(); render();
        frame = alpha > 0.004 && visible ? requestAnimationFrame(loop) : null;
      };
      frame = requestAnimationFrame(loop);
    }

    // ---------- colours from the page ----------
    var colours = {};
    function pageIsDark() {
      var html = document.documentElement, body = document.body;
      var theme = (html.getAttribute("data-theme") || body.getAttribute("data-theme") || html.getAttribute("data-bs-theme") || "").toLowerCase();
      if (theme === "dark") return true;
      if (theme === "light") return false;
      if (html.classList.contains("dark") || body.classList.contains("dark")) return true;
      if (html.classList.contains("light") || body.classList.contains("light")) return false;
      return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
    }
    function background(node) {
      for (; node && node.nodeType === 1; node = node.parentElement) {
        var bg = getComputedStyle(node).backgroundColor;
        if (bg && bg !== "transparent" && !/rgba\(.*,\s*0\)$/.test(bg)) return bg;
      }
      return pageIsDark() ? "#111111" : "#ffffff";
    }
    function readColours() {
      var cs = getComputedStyle(root), dark = pageIsDark();
      var v = function (name, fallback) { return cs.getPropertyValue(name).trim() || fallback; };
      // The page's own text colour (from around the graph: the graph's own colour is what we set last time).
      var around = root.parentElement ? getComputedStyle(root.parentElement).color : "";
      var text = v("--wendao-graph-text", around || (dark ? "#e6e6e6" : "#1f2328"));
      var bg = v("--wendao-graph-bg", background(root.parentElement));
      root.classList.toggle("wg-dark", dark);
      root.style.setProperty("--wg-bg", bg);
      root.style.setProperty("--wg-text", text);
      colours = {
        text: text, halo: bg,
        link: v("--wendao-graph-link", dark ? "rgba(255,255,255,0.14)" : "rgba(0,0,0,0.12)"),
        linkActive: v("--wendao-graph-link-active", dark ? "rgba(91,124,255,0.8)" : "rgba(47,91,255,0.7)"),
        type: {},
      };
      order.forEach(function (name) {
        var t = types[name];
        var given = t.color && typeof t.color === "object" ? t.color[dark ? "dark" : "light"] : t.color; // {light, dark}
        var fixed = typeof given === "string" && given ? given : null;
        if (fixed && /^var\(/.test(fixed)) fixed = cs.getPropertyValue(fixed.slice(4, -1).split(",")[0].trim()).trim() || null;
        var slot = typeof given === "number" ? (given - 1) % 6 : t.slot;
        colours.type[name] = v("--wendao-graph-" + name, fixed || PALETTE[dark ? "dark" : "light"][slot]);
      });
      legend.querySelectorAll("i").forEach(function (dot) { dot.style.background = colours.type[dot.dataset.type]; });
    }

    // ---------- legend ----------
    var counts = {};
    nodes.forEach(function (i) { counts[i.type.name] = (counts[i.type.name] || 0) + 1; });
    order.forEach(function (name) {
      if (!counts[name]) return;
      var button = el("button", "", '<i data-type="' + esc(name) + '"></i>' + esc(types[name].label));
      button.type = "button";
      button.dataset.type = name;
      button.setAttribute("aria-pressed", "true");
      button.addEventListener("click", function () {
        if (hidden.has(name)) hidden.delete(name);
        else if (hidden.size < Object.keys(counts).length - 1) hidden.add(name); // never hide everything
        legend.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(!hidden.has(b.dataset.type))); });
        if (selectedId && !shown(byId.get(selectedId))) select(null);
        animate(0.3); draw();
      });
      legend.append(button);
    });

    // ---------- view ----------
    var W = 0, H = 0, ratio = 1, view = { x: 0, y: 0, k: 1 }, viewFrame = null;
    var hoverId = null, selectedId = null, labelled = new Set(), pulse = null, visible = true;
    function resize() {
      var box = canvas.getBoundingClientRect();
      ratio = Math.min(window.devicePixelRatio || 1, 2);
      var w = box.width, h = box.height;
      if (W && H) { view.x += (w - W) / 2; view.y += (h - H) / 2; }
      W = w; H = h;
      canvas.width = Math.round(W * ratio); canvas.height = Math.round(H * ratio);
      draw();
    }
    function viewFor(list, maxScale) {
      if (!list.length || !W) return { x: W / 2, y: H / 2, k: 1 };
      var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      list.forEach(function (i) { x0 = Math.min(x0, i.x - i.r); y0 = Math.min(y0, i.y - i.r); x1 = Math.max(x1, i.x + i.r); y1 = Math.max(y1, i.y + i.r); });
      var pad = 36, k = Math.min(maxScale || 2, Math.max(0.15, Math.min((W - pad * 2) / (x1 - x0 || 1), (H - pad * 2) / (y1 - y0 || 1))));
      return { k: k, x: W / 2 - ((x0 + x1) / 2) * k, y: H / 2 - ((y0 + y1) / 2) * k };
    }
    function glide(target) {
      cancelAnimationFrame(viewFrame);
      if (REDUCED) { view = target; draw(); return; }
      var from = Object.assign({}, view), began = performance.now();
      var step = function (now) {
        var t = Math.min(1, (now - began) / 320), e = 1 - Math.pow(1 - t, 3);
        view = { x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e, k: from.k + (target.k - from.k) * e };
        render();
        if (t < 1) viewFrame = requestAnimationFrame(step);
      };
      viewFrame = requestAnimationFrame(step);
    }
    function fit(instant) {
      var target = viewFor(nodes.filter(shown), 1.6);
      if (instant) { view = target; draw(); } else glide(target);
    }
    function focusOn(item) {
      var near = [item].concat(Array.from(neighbours.get(item.id)).map(function (id) { return byId.get(id); }).filter(shown));
      var target = viewFor(near, 2.2);
      target.k = Math.max(target.k, Math.min(view.k, 2.2), 0.9);
      // On a narrow screen the card covers the bottom: put the node in the middle of the part above it.
      var covered = !card.hidden && W < 560 ? H - card.offsetTop + 8 : 0;
      target.x = W / 2 - item.x * target.k; target.y = Math.max(60, (H - covered) / 2) - item.y * target.k;
      glide(target);
    }
    function zoomAt(factor, px, py) {
      var k = Math.min(5, Math.max(0.15, view.k * factor));
      view.x = px - ((px - view.x) / view.k) * k; view.y = py - ((py - view.y) / view.k) * k; view.k = k;
      draw();
    }

    // ---------- drawing ----------
    var queued = false, frames = 0;
    function draw() {
      if (queued || !visible) return;
      queued = true;
      requestAnimationFrame(function () { queued = false; render(); });
    }
    function focusId() { return hoverId || selectedId; }
    function render() {
      if (!W) return;
      frames += 1;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.setTransform(ratio * view.k, 0, 0, ratio * view.k, ratio * view.x, ratio * view.y);
      var k = view.k, focus = focusId();
      var near = focus ? new Set([focus].concat(Array.from(neighbours.get(focus)))) : null;
      ctx.lineWidth = 1 / k; ctx.strokeStyle = colours.link; ctx.globalAlpha = near ? 0.35 : 0.9;
      ctx.beginPath();
      links.forEach(function (l) {
        if (!shown(l.source) || !shown(l.target)) return;
        ctx.moveTo(l.source.x, l.source.y); ctx.lineTo(l.target.x, l.target.y);
      });
      ctx.stroke();
      if (near) {
        var centre = byId.get(focus);
        ctx.globalAlpha = 1; ctx.strokeStyle = colours.linkActive; ctx.lineWidth = 1.4 / k;
        ctx.beginPath();
        neighbours.get(focus).forEach(function (id) {
          var o = byId.get(id);
          if (shown(o) && shown(centre)) { ctx.moveTo(centre.x, centre.y); ctx.lineTo(o.x, o.y); }
        });
        ctx.stroke();
      }
      order.forEach(function (name) {
        [true, false].forEach(function (faded) {
          ctx.globalAlpha = faded ? 0.2 : 1;
          ctx.fillStyle = colours.type[name];
          ctx.beginPath();
          nodes.forEach(function (i) {
            if (i.type.name !== name || !shown(i) || (near ? !near.has(i.id) : false) !== faded) return;
            ctx.moveTo(i.x + i.r, i.y); ctx.arc(i.x, i.y, i.r, 0, Math.PI * 2);
          });
          ctx.fill();
        });
      });
      var f = focus && byId.get(focus);
      if (f && shown(f)) { // a soft halo: two light rings
        ctx.fillStyle = colours.type[f.type.name];
        [[9, 0.16], [4.5, 0.3]].forEach(function (ring) {
          ctx.globalAlpha = ring[1];
          ctx.beginPath(); ctx.arc(f.x, f.y, f.r + ring[0] / k, 0, Math.PI * 2); ctx.fill();
        });
      }
      var s = selectedId && byId.get(selectedId);
      if (s && shown(s)) {
        ctx.globalAlpha = 1; ctx.lineWidth = 2.2 / k; ctx.strokeStyle = colours.text;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r + 3 / k, 0, Math.PI * 2); ctx.stroke();
      }
      if (pulse) {
        var p = byId.get(pulse.id), t = (performance.now() - pulse.start) / 650;
        if (t >= 1 || !p) pulse = null;
        else {
          ctx.globalAlpha = 0.5 * (1 - t); ctx.lineWidth = 2 / k; ctx.strokeStyle = colours.type[p.type.name];
          ctx.beginPath(); ctx.arc(p.x, p.y, p.r + (4 + 26 * (1 - Math.pow(1 - t, 3))) / k, 0, Math.PI * 2); ctx.stroke();
          draw();
        }
      }
      drawLabels(near, focus);
      ctx.globalAlpha = 1;
    }
    // Labels never overlap; the ones already showing keep their place, so they don't flicker.
    var widths = new Map();
    function drawLabels(near, focus) {
      var k = view.k, zoomed = Math.max(0, Math.min(1, (k - 0.8) / 0.6)), scale = 1 / Math.max(k, 0.7);
      var cand = [];
      nodes.forEach(function (item) {
        if (!shown(item)) return;
        var rule = item.type.labels, isNear = near && near.has(item.id), a;
        if (rule === "always") a = 1;
        else if (rule === "zoom") a = zoomed;
        else a = 0;
        if (near) a = isNear ? (rule === "focus" && item.id !== focus ? 0.85 : 1) : Math.min(a, 0.15);
        var sx = item.x * k + view.x, sy = item.y * k + view.y;
        if (a >= 0.05 && sx > -80 && sx < W + 80 && sy > -40 && sy < H + 40) cand.push({ item: item, a: a, near: isNear });
      });
      var rank = function (c) { return (c.item.id === focus ? 4 : c.near ? 3 : c.item.type.hub ? 2 : 1) * 1000 + (labelled.has(c.item.id) ? 500 : 0) + Math.min(c.item.degree, 499); };
      cand.sort(function (a, b) { return rank(b) - rank(a); });
      var boxes = [], now = new Set();
      ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.lineJoin = "round";
      cand.forEach(function (c) {
        var item = c.item, hub = item.type.hub, base = hub ? 12.5 : 10.5;
        var text = plain(item.node.label || item.id);
        if (text.length > (c.near || hub ? 48 : 30)) text = text.slice(0, c.near || hub ? 46 : 28) + "…";
        var font = (hub ? "650 " : "500 ") + base + "px system-ui, -apple-system, 'Segoe UI', sans-serif";
        var key = font + text;
        if (!widths.has(key)) { ctx.font = font; widths.set(key, ctx.measureText(text).width); }
        var w = widths.get(key) * scale, h = base * scale * 1.2, gap = 3 / k;
        var box = { l: item.x - w / 2 - 2 / k, r: item.x + w / 2 + 2 / k, t: item.y + item.r + gap, b: item.y + item.r + gap + h };
        var clash = boxes.some(function (o) { return box.l < o.r && box.r > o.l && box.t < o.b && box.b > o.t; });
        if (clash && !(c.near && item.id === focus)) return;
        boxes.push(box); now.add(item.id);
        ctx.font = font.replace(base + "px", base * scale + "px");
        ctx.globalAlpha = c.a;
        ctx.lineWidth = 3 / k; ctx.strokeStyle = colours.halo; ctx.strokeText(text, item.x, box.t);
        ctx.fillStyle = colours.text; ctx.fillText(text, item.x, box.t);
      });
      labelled = now;
    }

    // ---------- cards ----------
    function neighboursOf(item, typeName) {
      return Array.from(neighbours.get(item.id)).map(function (id) { return byId.get(id); })
        .filter(function (o) { return !typeName || o.type.name === typeName; });
    }
    function sortBy(list, key) {
      if (!key) return list;
      var desc = key.charAt(0) === "-", field = desc ? key.slice(1) : key;
      return list.slice().sort(function (a, b) {
        var x = a.node[field], y = b.node[field];
        if (x === y) return 0;
        return (x > y ? 1 : -1) * (desc ? -1 : 1);
      });
    }
    function titleHtml(n) {
      var title = n.title ? safeHtml(n.title) : esc(n.label || n.id), url = safeUrl(n.url);
      return url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + title + "</a>" : title;
    }
    function defaultCard(item) {
      // The colour can come from the data: escaped, so it can't break out of the attribute.
      var n = item.node, t = item.type, spec = t.card, html = '<p class="wg-kind"><i style="background:' + esc(colours.type[t.name]) + '"></i>' + esc(t.kind) + "</p>";
      html += "<h3>" + titleHtml(n) + "</h3>";
      (spec.fields || []).forEach(function (field) {
        var parts = String(field).split(",").map(function (f) { return f.trim(); });
        var values = parts.map(function (f) { return n[f]; }).filter(function (v) { return v != null && v !== ""; });
        if (values.length) html += '<p class="wg-field wg-field-' + esc(parts[0]) + '">' + values.map(esc).join(", ") + "</p>";
      });
      if (n.summary) html += '<p class="wg-summary">' + esc(n.summary) + "</p>";
      if (spec.list) {
        var list = sortBy(neighboursOf(item, spec.list), spec.sort), what = types[spec.list] ? types[spec.list].label.toLowerCase() : "";
        html += '<p class="wg-count">' + list.length + " " + esc(list.length === 1 && what.slice(-1) === "s" ? what.slice(0, -1) : what) + "</p>";
        html += "<ul>" + list.slice(0, spec.limit || 6).map(function (o) {
          var extra = spec.detail && o.node[spec.detail] != null ? " <span>(" + esc(o.node[spec.detail]) + ")</span>" : "";
          var url = safeUrl(o.node.url);
          var name = o.node.title ? safeHtml(o.node.title) : esc(o.node.label || o.id);
          return "<li>" + (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + name + "</a>" : '<button type="button" data-focus="' + esc(o.id) + '">' + name + "</button>") + extra + "</li>";
        }).join("") + "</ul>";
        if (list.length > (spec.limit || 6)) html += '<p class="wg-more">and ' + (list.length - (spec.limit || 6)) + " more</p>";
      }
      if (spec.tags) {
        var tags = neighboursOf(item, spec.tags);
        if (tags.length) html += '<p class="wg-tags">' + tags.map(function (o) { return '<button type="button" data-focus="' + esc(o.id) + '">#' + esc(plain(o.node.label)) + "</button>"; }).join(" ") + "</p>";
      }
      return html;
    }
    function showCard(item) {
      if (!item || options.cards === false) { card.hidden = true; return; }
      var custom = typeof options.card === "function" ? options.card(item.node, { types: types, neighbours: function (type) { return neighboursOf(item, type).map(function (o) { return o.node; }); }, defaultHtml: function () { return defaultCard(item); } }) : null;
      cardBody.innerHTML = "";
      if (custom && custom.nodeType) cardBody.append(custom);
      else cardBody.innerHTML = custom != null ? String(custom) : defaultCard(item);
      card.hidden = false;
    }
    cardBody.addEventListener("click", function (e) {
      var target = e.target.closest("[data-focus]");
      if (target) select(byId.get(target.getAttribute("data-focus")), true);
    });
    cardClose.addEventListener("click", function () { select(null); });
    function select(item, move) {
      if (item && !shown(item)) { hidden.delete(item.type.name); legend.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(!hidden.has(b.dataset.type))); }); }
      selectedId = item ? item.id : null;
      if (item && !REDUCED) pulse = { id: item.id, start: performance.now() };
      showCard(item);
      if (item && move) focusOn(item);
      draw();
    }

    // ---------- input ----------
    var pointers = new Map(), drag = null, pinch = null, moved = false, startPoint = null, active = false, hintTimer = null;
    function point(e) {
      var box = canvas.getBoundingClientRect(), sx = e.clientX - box.left, sy = e.clientY - box.top;
      return { sx: sx, sy: sy, x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
    }
    function nodeAt(p) {
      var best = null, bestD = Infinity, slack = 6 / view.k;
      nodes.forEach(function (i) {
        if (!shown(i)) return;
        var d = Math.hypot(i.x - p.x, i.y - p.y);
        if (d <= i.r + slack && d < bestD) { best = i; bestD = d; }
      });
      return best;
    }
    function setActive(on) { active = on; root.classList.toggle("wg-active", on); }
    canvas.addEventListener("pointerdown", function (e) {
      setActive(true);
      canvas.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, point(e));
      moved = false; startPoint = pointers.get(e.pointerId);
      cancelAnimationFrame(viewFrame);
      if (pointers.size === 2) {
        var ps = Array.from(pointers.values());
        pinch = { d: Math.hypot(ps[0].sx - ps[1].sx, ps[0].sy - ps[1].sy) || 1, k: view.k };
        if (drag && drag.node) drag.node.fx = drag.node.fy = null;
        drag = null; return;
      }
      var p = pointers.get(e.pointerId), hit = nodeAt(p);
      drag = hit ? { node: hit } : { pan: true, x: p.sx - view.x, y: p.sy - view.y };
    });
    canvas.addEventListener("pointermove", function (e) {
      var p = point(e);
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
      if (pinch && pointers.size === 2) {
        var ps = Array.from(pointers.values()), d = Math.hypot(ps[0].sx - ps[1].sx, ps[0].sy - ps[1].sy);
        zoomAt((pinch.k * d) / pinch.d / view.k, (ps[0].sx + ps[1].sx) / 2, (ps[0].sy + ps[1].sy) / 2);
        moved = true; return;
      }
      if (drag) {
        if (!moved && startPoint && Math.hypot(p.sx - startPoint.sx, p.sy - startPoint.sy) < (e.pointerType === "mouse" ? 3 : 8)) return;
        if (!moved && drag.node) { drag.node.fx = drag.node.x; drag.node.fy = drag.node.y; }
        moved = true;
        if (drag.pan) {
          view.x = p.sx - drag.x;
          // A finger only pans sideways (outside full screen): up and down scroll the page.
          if (e.pointerType !== "touch" || isFull()) view.y = p.sy - drag.y;
          draw();
        }
        else { drag.node.fx = p.x; drag.node.fy = p.y; animate(0.25); }
        return;
      }
      if (e.pointerType === "mouse") {
        var hit = nodeAt(p), id = hit ? hit.id : null;
        canvas.style.cursor = hit ? "pointer" : "";
        if (id !== hoverId) { hoverId = id; draw(); }
      }
    });
    function endPointer(e) {
      var p = pointers.get(e.pointerId);
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (drag && drag.node && moved) { drag.node.fx = drag.node.fy = null; animate(0.3); }
      if (!moved && p && e.type === "pointerup") {
        var hit = nodeAt(p);
        if (e.pointerType !== "mouse") hoverId = null;
        select(hit, true);
      }
      drag = null;
    }
    canvas.addEventListener("pointerup", endPointer);
    canvas.addEventListener("pointercancel", endPointer);
    canvas.addEventListener("pointerleave", function (e) {
      if (e.pointerType !== "mouse") return;
      if (!pointers.size) setActive(false); // the mouse left: scrolling belongs to the page again
      if (hoverId) { hoverId = null; draw(); }
    });
    // A plain wheel keeps scrolling the page; it zooms once the graph is clicked, in full screen, or with Ctrl/⌘.
    canvas.addEventListener("wheel", function (e) {
      if (!active && !isFull() && !e.ctrlKey && !e.metaKey) {
        wheelHint.classList.add("wg-show");
        clearTimeout(hintTimer);
        hintTimer = setTimeout(function () { wheelHint.classList.remove("wg-show"); }, 1400);
        return;
      }
      e.preventDefault();
      var p = point(e), dy = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
      zoomAt(Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.002)), p.sx, p.sy);
    }, { passive: false });
    function outside(e) { if (!root.contains(e.target)) setActive(false); }
    document.addEventListener("pointerdown", outside);

    controls.addEventListener("click", function (e) {
      var button = e.target.closest("button");
      if (!button) return;
      var what = button.dataset.do;
      if (what === "in") zoomAt(1.3, W / 2, H / 2);
      else if (what === "out") zoomAt(1 / 1.3, W / 2, H / 2);
      else if (what === "fit") fit();
      else if (what === "full") toggleFull();
    });

    // ---------- full screen (with a fallback for iPhone Safari: fill the window) ----------
    var fullButton = controls.querySelector('[data-do="full"]');
    function isFull() {
      return document.fullscreenElement === root || document.webkitFullscreenElement === root || root.classList.contains("wg-window");
    }
    function syncFull() {
      var on = isFull();
      fullButton.setAttribute("aria-label", on ? "Exit full screen" : "Full screen");
      fullButton.title = fullButton.getAttribute("aria-label");
      root.classList.toggle("wg-full", on);
      document.documentElement.classList.toggle("wg-locked", root.classList.contains("wg-window"));
      requestAnimationFrame(function () { resize(); fit(true); });
    }
    function toggleFull() {
      if (isFull()) {
        if (root.classList.contains("wg-window")) root.classList.remove("wg-window");
        else if (document.exitFullscreen) document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
        syncFull(); return;
      }
      var request = root.requestFullscreen || root.webkitRequestFullscreen;
      var fallback = function () { root.classList.add("wg-window"); syncFull(); };
      if (!request) { fallback(); return; }
      try {
        var result = request.call(root);
        if (result && result.catch) result.catch(fallback);
      } catch (error) { fallback(); }
    }
    function onKey(e) {
      if (e.key === "Escape" && root.classList.contains("wg-window")) { root.classList.remove("wg-window"); syncFull(); }
    }
    document.addEventListener("fullscreenchange", syncFull);
    document.addEventListener("webkitfullscreenchange", syncFull);
    document.addEventListener("keydown", onKey);

    // ---------- follow the page: theme, size, and whether the graph is on screen ----------
    var themeWatch = new MutationObserver(function () { requestAnimationFrame(function () { readColours(); draw(); }); });
    themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "data-bs-theme", "style"] });
    if (document.body) themeWatch.observe(document.body, { attributes: true, attributeFilter: ["class", "data-theme"] });
    var scheme = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
    var onScheme = function () { readColours(); draw(); };
    if (scheme && scheme.addEventListener) scheme.addEventListener("change", onScheme);
    var sizeWatch = new ResizeObserver(function () { resize(); fit(true); });
    sizeWatch.observe(canvas);
    var seenWatch = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
      visible = entries[entries.length - 1].isIntersecting;
      if (visible) draw(); // nothing is drawn or animated while the graph is off screen
    }) : null;
    if (seenWatch) seenWatch.observe(root);

    controller.fit = function () { fit(); };
    // Where a node is drawn, in pixels from the graph's top-left corner (for automated tests); null if unknown.
    controller.screenPoint = function (id) {
      var item = byId.get(String(id));
      return item ? { x: item.x * view.k + view.x, y: item.y * view.k + view.y, r: item.r * view.k } : null;
    };
    controller.frames = function () { return frames; };
    controller.focus = function (id) { var item = byId.get(String(id)); if (item) select(item, true); };
    controller.destroy = function () {
      themeWatch.disconnect(); sizeWatch.disconnect(); if (seenWatch) seenWatch.disconnect();
      if (scheme && scheme.removeEventListener) scheme.removeEventListener("change", onScheme);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("fullscreenchange", syncFull);
      document.removeEventListener("webkitfullscreenchange", syncFull);
      document.removeEventListener("keydown", onKey);
      cancelAnimationFrame(frame); cancelAnimationFrame(viewFrame);
      root.innerHTML = ""; root.classList.remove("wg", "wg-active", "wg-full", "wg-window", "wg-dark");
      delete root.__wendaoGraph;
    };

    // Lay the graph out before the first paint, so it appears settled rather than flying into place.
    readColours();
    settle(260);
    alpha = 0;
    resize();
    fit(true);
  }

  function mountAll(scope) {
    (scope || document).querySelectorAll("[data-wendao-graph]").forEach(function (node) {
      if (node.__wendaoGraph) return;
      var inline = node.querySelector('script[type="application/json"]');
      var options = { src: node.getAttribute("data-src") || undefined, credit: node.getAttribute("data-credit") !== "false" };
      if (inline) { try { options.graph = JSON.parse(inline.textContent); } catch (error) { /* shown as an error below */ } }
      if (node.getAttribute("data-height")) node.style.height = node.getAttribute("data-height");
      mount(node, options);
    });
  }

  window.WendaoGraph = { mount: mount, mountAll: mountAll, version: "1" };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { mountAll(); });
  else mountAll();
})();
