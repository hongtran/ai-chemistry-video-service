# Tech Template — Software & AI Education Videos

A data-driven HyperFrames template for technical explainer videos (software,
programming, AI: RAG, agents, career topics…). Same mechanism as
`templates/chemistry/` — a JSON spec is populated into a renderable project —
plus **dual aspect-ratio support** and a **dark developer aesthetic**
(near-black `#0D1117` canvas, neon accents, Space Grotesk / JetBrains Mono /
Inter, glowing window chrome).

## Usage

```bash
# Generate a video project from a spec
node templates/tech/populate.js templates/tech/data.json     # → videos/<slug>/

# Validate + render
cd videos/<slug> && npm run check && npm run render
```

Test fixtures: `test-<type>.json` renders one frame type in isolation;
`test-showcase-v.json` / `test-showcase-h.json` run all 15 types in sequence
in each orientation. `data.json` is a full worked example ("RAG for AI
Agents", ~60s).

## Orientation

`config.orientation` selects the canvas:

- `"vertical"` (default) — 9:16, 1080×1920 (Shorts/Reels/TikTok)
- `"horizontal"` — 16:9, 1920×1080 (YouTube)

Every frame ships **two hand-tuned layouts**, switched by an `{{orientation}}`
class on the clip root: vertical stacks, horizontal goes side-by-side. Frames
that compute coordinates in JS (roadmap, vector-space, neural-net) derive them
from `{{width}}`/`{{height}}` constants — nothing is pixel-baked to one canvas.

## Frame types (21)

| Type | Use for |
|---|---|
| `cover` | opening hook: mono eyebrow badge, slam-in headline, blinking cursor |
| `stats` | one striking number — animated count-up with glow |
| `quote` | pull quote styled as a code comment (`//`), word-by-word reveal |
| `bullet-list` | 3–5 point listicle, staggered rows (2-col grid in 16:9) |
| `cta` | outro: recap + follow pill (eyebrow = handle) |
| `concept-card` | define one term: glyph + term + tagline card, definition types on |
| `code-snippet` | editor window, syntax-highlighted line-by-line reveal, accent bars on key lines (font auto-fits longest line) |
| `terminal` | commands typed char-by-char, output cascades, `✓`-prefixed lines style as success |
| `chat` | user/assistant bubbles with typing-dots indicator |
| `pipeline` | boxes-and-arrows flow with marching-dash connectors (V: top→down, H: left→right) |
| `comparison` | two panels + VS badge + verdict (V: stacked, H: side-by-side) |
| `roadmap` | milestone line with traveling glow runner lighting up steps |
| `stack-layers` | architecture slabs drop in; one highlights with "you work HERE" callout |
| `vector-space` | embedding scatter: clusters pop, query drops, top-k edges draw |
| `neural-net` | layered network with repeating signal waves |
| `task-breakdown` | agent decomposes a goal: goal card fans into a self-ticking checklist |
| `thought-chain` | chain-of-thought: question pill → typed thinking cards → accent answer card |
| `tool-use` | agent hub with tool spokes; request/response pulses travel out and back |
| `memory` | SHORT-TERM vs LONG-TERM stores fill up, then items are recalled to the agent |
| `reflection-loop` | self-check cycle ring: lap 1 fails ✗ at the Check node, lap 2 passes ✓ |
| `mcp-hub` | agent → MCP socket → app chips snap in: one standard plug, many apps |

Shared by every frame: `eyebrow`, `headline`/`title`, `bg`/`fg`/`accent`
overrides, `captions[]` (synced to `captionTiming` when provided, evenly
spaced otherwise), and `**word**` → accent-highlight markup in any text field.

Field reference: `schema.json` (descriptions carry `(type)` tags; the `type`
enum carries a `typeUsage` guidance map). Machine-readable contract:
`frame-defaults.mjs` (`FRAME_DEFAULTS`, `TYPE_CONTENT_FIELDS`,
`REQUIRED_CONTENT_FIELDS`, `ORIENTATIONS`, `validateData`).

## Frame authoring contract (for new types)

- Sub-composition file: content wrapped in `<template>`; root
  `<div id="root" data-composition-id="{{id}}">`; timed section
  `<section id="main-clip" class="clip main-clip {{orientation}}">`.
- **CSS scoping**: style the root as `#root { … }` and everything else with
  plain descendant selectors; orientation variables live on
  `#main-clip.vertical` / `#main-clip.horizontal`. Never key a rule off the
  root's own class — the runtime scopes sub-composition CSS to
  `[data-composition-id] <selector>` and such rules silently stop matching at
  render (passes lint/preview, renders unstyled).
- One paused GSAP timeline registered as `window.__timelines['{{id}}']`.
- Deterministic only: no `Math.random`/`Date.now`, finite `repeat` counts,
  no DOM-measurement-driven layout (compute coordinates from
  `{{width}}`/`{{height}}` constants).
- Rotated connector lines: set rotation via `gsap.set(el, {rotation, …})`,
  not an inline `transform` (a later GSAP transform tween replaces it).
- Arrays/objects are injected into `<script>` as JSON literals
  (`var NODES = {{nodes}};`); escape all string content at DOM-build time.
- Keep something visible from ~0.25s — the inspector samples early and flags
  scenes that sit fully empty over the background.
- Inspector layering rules: prefer geometric fixes over suppression markers.
  Multi-word `**highlights**` next to inline decorations (the cover caret)
  must be split into per-word chips — a chip wrapping across lines has a
  union bounding rect that swallows neighbors and reads as `text_occluded`.
  Traveling pulses/runners paint *beneath* the cards they pass (DOM order).
  When suppression is genuinely right: `data-layout-allow-occlusion` only
  works on the OCCLUDED text element or one of its ancestors (`closest()`),
  never on the covering element; `data-layout-allow-overflow` goes on the
  deliberately-oversized child itself (the pipeline marching dash uses it).

## LLM pipeline

The tech template has its own end-to-end script pipeline (same mechanism as
chemistry's, plus orientation support):

```bash
# query → narration → TTS → Whisper timestamps → typed scene split → aligned data.json
OPENAI_API_KEY=sk-... node scripts/generate-script-tech.mjs "RAG for AI agents" \
  --orientation vertical      # or: horizontal (16:9); default vertical (9:16)

# then populate + check + render in one step (stages the narration audio)
scripts/build-video-tech.sh templates/tech/data-rag-for-ai-agents.json \
  --audio_file templates/tech/generated/rag-for-ai-agents/narration.mp3
```

The LLM only supplies content, never timing: real timing comes from the TTS
recording's Whisper word timestamps via the shared `scripts/align-captions.mjs`.
The scene-split prompt is built from `schema.json` (field descriptions +
`typeUsage` guidance), so schema edits reach the LLM with no code change.
Artifacts land in `templates/tech/generated/<slug>/`. Without the pipeline,
the template still works manually via `populate.js` — missing audio referenced
by `config.bgm`/`config.audio`/`scene.sfx` is stubbed with silence so
`npm run check` passes.
