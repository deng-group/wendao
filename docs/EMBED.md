# Put a Wendao graph on any website

The Wendao graph embed shows an interactive knowledge graph inside an ordinary web page: a publication list, a
research group site, a project page. It needs **no server**: two files and a JSON file, so it works on static sites
such as Jekyll on GitHub Pages.

## 1. Add the files

Copy the embed into your site:

```bash
wendao embed install assets/wendao
```

or load it from a Wendao release on jsDelivr (replace the version):

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/deng-group/wendao@v0.4.1/src/wendao/web/static/embed/wendao-graph.css">
<script src="https://cdn.jsdelivr.net/gh/deng-group/wendao@v0.4.1/src/wendao/web/static/embed/wendao-graph.js" defer></script>
```

## 2. Place the graph

```html
<div data-wendao-graph data-src="/assets/papers-graph.json" style="height: 560px"></div>
```

The data can also sit inside the element, which suits a site generator that writes the JSON into the page:

```html
<div data-wendao-graph>
  <script type="application/json">{"nodes": [...], "edges": [...]}</script>
</div>
```

Or from your own script: `WendaoGraph.mount(element, { src: "graph.json" })` (or `{ graph: data }`). It returns a
controller with `focus(id)`, `fit()`, and `destroy()`.

## 3. Make the data

**From BibTeX** (themes from `keywords`, papers, and frequent co-authors):

```bash
wendao bibtex papers.bib -o assets/papers-graph.json --me "Your Name" \
  --theme "mof=MOFs" --theme "perovskites=Halide perovskites" --skip thesis
```

- `--me`: your name as it appears in the authors; you are left out of the collaborators.
- `--theme "keyword=Name"`: the name a keyword shows as. Other keywords show as themselves, capitalised.
- `--skip keyword`: a keyword that is not a theme.
- `--min-shared 3`: how many joint papers a co-author needs to appear.

**From anything else**, write the format below from your own build step.

## The `wendao-graph/1` format

```json
{
  "format": "wendao-graph/1",
  "types": {
    "theme":  { "label": "Themes", "kind": "Research theme", "hub": true, "labels": "always", "color": 1,
                "card": { "list": "paper", "sort": "-year", "detail": "year" } },
    "paper":  { "label": "Papers", "kind": "Paper", "labels": "focus", "color": 2,
                "card": { "fields": ["authors", "venue, year"], "tags": "theme" } },
    "person": { "label": "Collaborators", "kind": "Collaborator", "labels": "zoom", "color": 3,
                "card": { "list": "paper", "sort": "-year", "detail": "year" } }
  },
  "nodes": [
    { "id": "paper:smith2024", "type": "paper", "label": "Hydrophobic composites for CO2 capture",
      "title": "Hydrophobic composites for CO<sub>2</sub> capture", "authors": "A. Smith, B. Lee",
      "venue": "J. Am. Chem. Soc.", "year": 2024, "url": "https://doi.org/10.1021/..." },
    { "id": "theme:carbon-capture", "type": "theme", "label": "Carbon capture" }
  ],
  "edges": [ { "source": "paper:smith2024", "target": "theme:carbon-capture" } ]
}
```

**Nodes**: `id` (unique), `type`, and `label` (plain text, shown on the graph). Optional: `title` (the card's
heading; may keep `<sub>`, `<sup>`, `<i>`, `<em>`, `<b>`, `<strong>`), `url` (the heading links there), `summary` (a
paragraph in the card), and any other fields for the card.

**Edges**: `source` and `target` node ids. Links have no direction.

**Types** (all optional; unknown types still work):

| Setting | Meaning |
| --- | --- |
| `label` | Name in the legend, for example "Papers" |
| `kind` | Name at the top of a card, for example "Paper" (default: the label) |
| `hub` | `true`: bigger, sized by its links, laid out on a ring, labelled by default |
| `labels` | When names show: `"always"`, `"zoom"` (when zoomed in or near the node in focus), or `"focus"` (only for the node in focus and its neighbours) |
| `color` | `1`–`6` (the palette below), a CSS colour, `"var(--my-colour)"`, or `{"light": "...", "dark": "..."}` |
| `card.fields` | Fields shown as lines; `"venue, year"` puts two on one line |
| `card.list` | A node type to list in the card, linked to this node (for example a theme's papers) |
| `card.sort` | Field to sort that list by; a leading `-` means highest first (`"-year"`: newest first) |
| `card.detail` | A field shown after each listed item, for example `"year"` |
| `card.limit` | How many to list (default 6) |
| `card.tags` | A node type shown as clickable tags (for example a paper's themes) |

The default palette (matsci.dev's colours): 1 blue (`#2f5bff`, dark `#5b7cff`), 2 grey, 3 amber, 4 violet, 5 teal,
6 rose.

**Custom cards**: `WendaoGraph.mount(el, { src, card(node, ctx) { ... } })`. Return an HTML string or an element,
or `null` for the default card. `ctx.neighbours("paper")` gives the linked nodes of a type, `ctx.defaultHtml()` the
default card.

## Look and behaviour

- **Colours follow your page**: its text colour and background, and dark mode when the page switches (it watches
  `class`, `data-theme`, and `data-bs-theme` on `<html>` and `<body>`, and the system setting). Override with CSS
  variables on the element: `--wendao-graph-text`, `--wendao-graph-bg`, `--wendao-graph-link`,
  `--wendao-graph-link-active`, `--wendao-graph-glow`, and `--wendao-graph-<type>` (for example
  `--wendao-graph-paper`).
- **Height**: set it on the element (`style="height: 560px"`, or `data-height="560px"`); the default is 520 px.
- **Scrolling**: a plain scroll keeps scrolling the page. The wheel zooms after the graph is clicked, in full screen,
  or with Ctrl/⌘ + scroll.
- **Touch**: pinch to zoom, tap a node for its card, drag sideways to move; swiping up or down scrolls the page.
- **Full screen** button, with a fallback that fills the window where the browser has no full-screen mode (iPhone).
- **The same layout on every visit** (it is seeded from the data), laid out before it first appears.
- **No work while idle or off screen**; `prefers-reduced-motion` turns off the motion.
- **"Powered by Wendao"** at the bottom; keep it, or hide it with `data-credit="false"`.
