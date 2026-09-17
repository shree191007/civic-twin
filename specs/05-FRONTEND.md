# 05 — Frontend

**Builds:** `web/`
**Gate:** `npm run build` succeeds; all five views render against a live API;
the demo script in spec 08 runs end to end.

---

## 1. Stack (do not substitute)

| Concern | Choice |
|---|---|
| Framework | React 18 + TypeScript, Vite |
| Map | deck.gl 9 + MapLibre GL (basemap: CARTO dark-matter, no API key) |
| Charts | `@observablehq/plot` |
| Dependency graph | `cytoscape` + `cytoscape-dagre` |
| State | `zustand` (one store, see §3) |
| Data fetching | `@tanstack/react-query` |
| Styling | CSS modules + CSS custom properties. **No Tailwind, no UI kit.** |
| Types | Generated from the OpenAPI schema via `openapi-typescript` into `src/api/types.ts` |

## 2. Visual design system

The aesthetic is an **operations console**: dark, dense, monospaced numerics,
restrained colour used only to carry meaning.

```css
:root {
  --bg:            #0a0e14;
  --bg-panel:      #111823;
  --bg-elevated:   #18212e;
  --border:        #1f2a38;
  --text:          #d8e0ea;
  --text-dim:      #7d8b9e;
  --mono: "JetBrains Mono", "SF Mono", ui-monospace, monospace;
  --sans: "Inter", system-ui, sans-serif;

  /* Portfolio identity — used for layer tint and legend only */
  --energy:    #f5a623;
  --water:     #2ec8f0;
  --comms:     #b06cf5;
  --transport: #8b98a8;
  --services:  #ff5f6d;

  /* Functionality scale — the ONLY colours used for status */
  --f-full:    #2fd98a;
  --f-degraded:#f5c542;
  --f-critical:#ff8a3d;
  --f-down:    #ff3b4e;

  /* Provenance */
  --prov-observed:  #d8e0ea;
  --prov-inferred:  #7d8b9e;
  --prov-synthetic: #4a5768;
}
```

**Colour rules (enforced in review):**
- Status is always the functionality scale. Portfolio colour never encodes status.
- Never use red/green alone to distinguish two categories (colour-blind safety);
  pair with shape or label.
- All numbers render in `--mono` with tabular figures.

## 3. State store — `src/store.ts`

```ts
interface AppState {
  // scenario
  scenarioId: string;               // "storm_50y" | "adhoc:<hash>"
  plan: "baseline" | "asset_by_asset" | "optimised";
  t: number;                        // current hour, 0..72
  playing: boolean;
  speed: 0.25 | 0.5 | 1 | 2 | 4;

  // view
  mode: AnalysisMode;               // see §5
  activeLayers: Set<Portfolio>;
  selectedAsset: string | null;
  hoveredAsset: string | null;
  compareWith: string | null;       // "storm_50y:optimised" for split view
  cameraTarget: {lon: number; lat: number; zoom: number; pitch: number} | null;

  // plan builder
  budget: number;

  // actions
  setT(t: number): void;
  selectAsset(id: string | null): void;
  flyTo(assetId: string): void;
  runAdhoc(req: ScenarioRequest): Promise<void>;
}
```

Timeline playback uses `requestAnimationFrame`, advancing `t` by
`speed * dt_h` per 250 ms of wall clock. Frames are interpolated for
positions but **never for status values** — status steps at frame boundaries.

## 4. Views

Five routes, switched by a left icon rail. Keyboard shortcuts in brackets.

| Route | Name | Shortcut |
|---|---|---|
| `/explore` | System explorer | `1` |
| `/scenario` | Scenario player | `2` |
| `/risk` | Risk & criticality | `3` |
| `/plan` | Plan builder | `4` |
| `/log` | Decisions & brief | `5` |

### 4.1 `/explore` — System explorer (the hero view)

**Layout:** full-bleed map; right panel 380 px (asset inspector); top-left HUD.

**The layer cake.** Each portfolio renders at its `layer_altitude_m` as a
separate deck.gl layer group, with a faint `PolygonLayer` "plate" showing the
township boundary at that altitude (fill opacity 0.04, stroke in the portfolio
colour at 0.25). Camera pitch defaults to 50°.

Layers per portfolio:
- `ScatterplotLayer` for assets, radius by `served_population^0.4`, fill by
  functionality colour, stroke by provenance colour, width 1.5 px.
- `TextLayer` for asset ids, visible above zoom 13.
- Transport additionally has a `PathLayer` for roads at altitude 0.

**Cross-layer dependency arcs.** An `ArcLayer` drawn only for links where
source and target are in different portfolios. `getSourcePosition` includes the
source's altitude, `getTargetPosition` the target's. Width 1.5, opacity 0.35.
On asset selection, non-chain arcs drop to opacity 0.05.

**Dependency tracer (required feature).** Clicking an asset calls
`/assets/{id}/trace` and renders the returned subgraph as highlighted arcs with
an animated dash offset travelling **from the failing asset toward its
dependents**, so the direction of causation is visible. Affected zones pulse.

**Asset inspector panel** shows: name, kind, provenance badge, served
population, functionality, criticality rank and systemic ratio, upstream and
downstream lists (clickable), any SPOFs naming it, and the explanation chains.
Below that, a Cytoscape graph of the trace (dagre layout, left-to-right).

**HUD (top-left, `--mono`, 11 px):** township name, data version, active
scenario, hour, and four live counters (people without power / water / comms /
hospital access). Plus a one-line scenario readout generated from the frame:
`H+14 · east ward isolated · 2 pumps down · 31,000 without hospital access`.
This string is composed client-side from the frame's `totals` and `reason`
fields — **not** from an LLM.

### 4.2 `/scenario` — Scenario player

**Layout:** map (60%) left; right column with the portfolio timeline (top) and
drawdown charts (bottom). Bottom bar: transport controls.

**Controls bar:**
- Rainfall slider, snapping to the four precomputed storms, with the return
  period shown. Dragging beyond them triggers an ad-hoc `POST /scenarios`
  (debounced 400 ms, with a subtle "computing" state).
- Timeline scrubber 0–72 h with tick marks at 6 h, playhead, and speed buttons
  (0.25× 0.5× 1× 2× 4×).
- Plan selector: baseline / asset-by-asset / optimised.
- "Fail an asset" toggle: when active, clicking a map asset adds it to
  `forced_failures` and re-runs.

**Flood rendering.** A `HeatmapLayer` or `GridCellLayer` at altitude 0 fed by a
depth grid returned in the frame. If the API does not return a grid, render
per-road depth on the `PathLayer` colour ramp instead (acceptable fallback).

**Portfolio timeline (the WNTR-style state plot).** A canvas grid: one row per
asset (grouped by portfolio, sorted by criticality), one column per hour,
cell colour = functionality. The playhead is a vertical line. Hovering a cell
shows `asset · hour · functionality · reason`. Clicking selects the asset.
This is the single most information-dense view; get it right.

**Drawdown charts.** One small multiple per portfolio: service level (0–1)
against hours, baseline in `--text-dim`, current plan in the portfolio colour,
with the recovery-90% point marked. Rendered with Observable Plot.

### 4.3 `/risk` — Risk and criticality

Four panels:
1. **Loss distribution.** Histogram of weighted loss across scenario years,
   with VaR95 and CVaR95 marked. Tail region shaded.
2. **Portfolio contributions.** Horizontal stacked bar of CVaR contributions,
   with the sum labelled to show it equals total CVaR.
3. **Criticality table.** Sortable: asset, portfolio, served population,
   failure probability, tail criticality, systemic ratio. Systemic assets get a
   badge. Clicking a row flies the map to it and opens the inspector.
4. **Hidden single points of failure.** Cards, one per SPOF, each stating the
   redundant group, the shared cause, the affected population, and a
   "Show me" button that flies to the assets and highlights the shared chain.

SPOF card copy format:
```
FAKE REDUNDANCY · 31,400 people
Towers T5 and T6 both serve zones Z9–Z12.
Both are powered by S2, which floods in a 1-in-25-year storm.
[ Show me ]
```

### 4.4 `/plan` — Plan builder

- **Efficient frontier** chart: cumulative cost (x) vs CVaR (y), stepped line,
  current budget marked by a draggable handle. Dragging updates
  `GET /plans?budget=`.
- **Selected interventions list:** label, cost, CVaR reduction, and a confidence
  badge showing `selection_frequency` as a filled ring plus the percentage.
- **Map** highlights targeted assets with a ring in the portfolio colour.
- **Split-screen comparison:** a toggle splits the map into two synced deck.gl
  views (shared view state), left = `asset_by_asset`, right = `optimised`,
  same storm, both playing on the same clock, with people-affected counters
  under each. This is the demo's proof moment.
- **Ghost overlay** (alternative to split): render the optimised outcome as
  faint outlines beneath the baseline in a single view. Implement both; the
  demo uses split.

### 4.5 `/log` — Decisions and brief

- Table of recorded decisions from `GET /decisions`.
- "Record this plan" form → `POST /decisions`.
- "Generate brief" button → renders the one-page brief (spec 08 §5) as a
  print-styled HTML page (`@media print` rules; A4; no interactive chrome).
  Use the browser's print-to-PDF; do not add a PDF library.

## 5. Analysis modes (the "sensor reskin" idea, made substantive)

A mode switcher (keys `Q W E R T`) changes what asset colour encodes.
Every mode maps to a real metric. **No purely decorative filters.**

| Mode | Key | Colour encodes | Legend |
|---|---|---|---|
| `functionality` | Q | Current functionality 0–1 | f-scale |
| `flood` | W | Current flood depth at the asset | blue ramp, 0–3 m |
| `criticality` | E | `tail_criticality_ph` | sequential amber ramp |
| `provenance` | R | observed / inferred / synthetic | provenance colours |
| `risk` | T | Zone CVaR (zones only; assets dim) | sequential red ramp |

Each mode swap animates colour over 200 ms. The legend is always visible and
states the units.

## 6. Camera behaviour

- `flyTo(assetId)` uses deck.gl `FlyToInterpolator`, 1200 ms, easing
  `d3.easeCubicInOut`, final pitch 50°, zoom sized so the asset's affected zones
  fit the viewport.
- "Frame the cascade": when a scenario is loaded, an optional button pulls the
  camera back and pitches to show all four layers at once.
- Store and restore the previous view when toggling global context (`G`).

## 7. Copilot panel

A collapsible right-edge drawer (key `C`), available in every view.

- Text input; on submit, POST `/copilot` with the current `scene` from the store.
- Render the answer, then a collapsed **tool trace** listing each tool call and
  its one-line summary. The trace is expanded by default during the demo.
- If `available: false`, show a single dim line: "Copilot not configured."
- Any asset id mentioned in the answer is rendered as a clickable chip that
  selects the asset.
- **Never** render a number from the answer in a highlighted stat block; the
  answer is prose. Stat blocks come only from API data.

## 8. Performance requirements

| Metric | Target |
|---|---|
| First contentful paint (prod build, local API) | < 1.5 s |
| Timeline scrub at 4× | ≥ 30 fps on integrated graphics |
| Mode switch | < 200 ms |
| Portfolio timeline render (75 assets × 72 h) | < 100 ms |

Techniques: precompute every frame's colour array once per scenario load;
memoise deck.gl layers on `[scenarioId, plan, t, mode]`; use `updateTriggers`
rather than recreating layers; render the portfolio timeline to a single canvas,
not DOM nodes.

## 9. Degradation and fallbacks

- If WebGL2 is unavailable or fps drops below 15 for 3 s, switch to a 2D
  fallback: pitch 0, no arcs, no plates. Show a dim banner stating this.
- If the API is unreachable, load from `web/public/fixtures/` (a copy of
  `results/`) so the demo still runs. Show a "fixture data" badge.
- Every chart has an empty state; never render a broken axis.

## 10. Accessibility

- All interactive elements reachable by keyboard; visible focus rings.
- The map has a text-alternative panel listing the current failures and counters
  (also useful for screenshots).
- Minimum contrast 4.5:1 for text on `--bg-panel`.

## 11. File layout

```
web/src/
├── main.tsx
├── App.tsx
├── store.ts
├── api/{client.ts,types.ts,queries.ts}
├── views/{Explore,Scenario,Risk,Plan,Log}/index.tsx
├── map/
│   ├── DeckMap.tsx            # shared map shell, view state, basemap
│   ├── layers/{assets.ts,roads.ts,arcs.ts,zones.ts,flood.ts,plates.ts}
│   ├── modes.ts               # colour accessors per analysis mode
│   └── camera.ts
├── components/
│   ├── Hud.tsx
│   ├── TimelineScrubber.tsx
│   ├── PortfolioTimeline.tsx  # the canvas state plot
│   ├── DrawdownCharts.tsx
│   ├── AssetInspector.tsx
│   ├── DependencyGraph.tsx    # cytoscape
│   ├── SpofCard.tsx
│   ├── FrontierChart.tsx
│   ├── ConfidenceBadge.tsx
│   ├── ProvenanceBadge.tsx
│   ├── SplitCompare.tsx
│   └── CopilotDrawer.tsx
├── styles/{tokens.css,base.css,print.css}
└── lib/{format.ts,colors.ts,frames.ts}
```

`lib/format.ts` must include: `people(n)` → `31,400`; `hours(h)` → `14 h`;
`inr(n)` → `₹45 lakh` / `₹1.2 crore`; `personHours(ph)` → `1.28 M person-hours`.

## 12. Acceptance checklist

- [ ] All five views render with no console errors against a live API.
- [ ] Clicking `S2` in `/explore` highlights the chain to `H2` and shows the
      affected population, demonstrating trap #3 visually.
- [ ] Scrubbing `/scenario` to H+14 on `storm_50y` shows tower batteries
      exhausted and east zones dark, matching the API timeline exactly.
- [ ] A SPOF card's "Show me" flies to `B1` and highlights both fibres.
- [ ] Dragging the budget handle updates the selected interventions and map
      rings within 300 ms.
- [ ] Split-screen comparison plays both sides on one clock.
- [ ] Every mode's legend states its units.
- [ ] Print view produces a clean single-page A4 brief.
- [ ] Fixture fallback works with the API stopped.
- [ ] `npm run build` emits no TypeScript errors.
