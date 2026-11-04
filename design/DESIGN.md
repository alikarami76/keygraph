# keygraph design language

This folder holds keygraph's visual identity and the design system the HTML explorer is built from. It is the reference the generator is rebuilt to match. All artwork is original and all fonts are OFL, so everything here can ship in the MIT repository.

| What | Where |
|---|---|
| Design tokens (source of truth) | `tokens.json` |
| CSS: variables plus component classes, for the generator to inline | `keygraph.css`, and `keygraph.inline.css` with the fonts embedded as base64 |
| Theme helpers (schema colours, confidence bands) plus a cytoscape stylesheet for any view that still uses cytoscape | `kg-theme.js` |
| Diagram renderer (HTML boxes, SVG edges, dagre layout, pan and zoom) | `src/kg-diagram.js` |
| Component CSS source and theme JS source | `src/components.css`, `src/kg-theme.src.js` |
| Build: tokens to CSS and JS, plus a WCAG contrast check | `python3 design/build.py [--inline] [--check]` |
| Fonts and their licences | `fonts/` (Inter, JetBrains Mono, Latin subsets, OFL 1.1) |
| Layout library | `vendor/dagre-1.1.4.min.js` (@dagrejs/dagre 1.1.4, MIT, `DAGRE_LICENSE`) |
| Logo, mark, favicon | `logo/` |
| README hero, section banners, social preview, diagrams | `visuals/` (regenerate with `python3 design/visuals/gen.py`; snippets in `visuals/README-snippet.md`) |
| Reference prototype (five screens plus states, both themes) | `prototype/explorer.html`, built by `prototype/build_prototype.py` from `prototype/src/` (its own fake 63-table shop in `data.js`, not the shipped explorer, which is `src/keygraph/static/explorer.js`); screenshots made by `prototype/shoot.py` into `prototype/shots/` |

## Principles

1. **One job per view.** Joining, understanding a table, mapping an area and finding where data lives are different questions, so each gets its own view. No view tries to be all four.
2. **Columns, not dots.** Relations attach to the column that joins. A table is drawn as a box that lists its key columns.
3. **Never more than about 40 boxes.** Anything bigger is grouped, and the grouping says what it hides and offers to expand it. Nothing is dropped silently.
4. **Uncertainty is visible.** Every inferred relation shows its confidence and its evidence, and every risky join step (hub, fan-out, log table) carries a labelled warning.
5. **Quiet chrome, loud data.** The interface is graphite and white, with one brass accent for focus and the primary action. Colour in the diagrams carries meaning (area), and line style carries relation kind.

## Decisions and why

**Direction: graphite plus brass.** Brass is the colour of keys, it is distinctive among developer tools (most use blue or violet), and it reads well on both a white and a near-black canvas. It is used sparingly: the PK tag, the focus ring, the selected box, the active path, the primary button and the logo's second node. Everything else is neutral, so the area palette has room to carry meaning. I considered an indigo direction (safer, more generic) and rejected it because it would collide with the blue in the area palette and with the blue links of a typical README.

**Type: Inter for UI and prose, JetBrains Mono for every identifier, type, number and SQL.** A tool whose content is names like `order_item_id` must make similar names easy to tell apart. Both fonts are OFL. They are subset to Latin (Inter variable 58 KB, JetBrains Mono variable 19 KB) and embedded, so the explorer never fetches anything. If a build must be smaller, drop the `@font-face` block: the stacks fall back to `ui-monospace` and `system-ui`.

**Renderer: HTML boxes with SVG edges, laid out by dagre, instead of cytoscape.** The new views draw at most about 40 boxes, and each box is a table listing its key columns, with relations attached to exact rows. HTML is the right medium for that: the text is real (crisp, selectable, searchable with Ctrl+F, readable by screen readers), it is styled by the same CSS tokens as the rest of the app, and it supports focus and keyboard navigation. Canvas rendering, which is cytoscape's strength, matters only for thousands of nodes, and the 40-box rule removes that case. dagre (`@dagrejs/dagre@1.1.4`, MIT, 94 KB minified, no dependencies, one file) does the layered ER layout. ELK lays out ports better, but it is about 1.5 MB and EPL-2.0 licensed, which is too heavy and adds licence obligations. Net effect on the file: cytoscape (365 KB) is replaced by dagre plus `kg-diagram.js` (about 110 KB). `kg-theme.js` keeps only the two helpers the explorer uses, `schemaColor` and `band`; the cytoscape stylesheet it used to export is gone.

**The Area map uses a radial-force layout, not dagre.** Relations between areas are undirected and dense, and a layered layout produced long, looping edges in testing. The map places the busiest area in the middle, places its neighbours on a ring ordered so that linked areas sit side by side, and then runs a short deterministic force pass with rectangle collision. The ellipse is stretched to the viewport's aspect ratio. The same input always gives the same picture.

**ER cards list key columns only and hide types.** At a readable zoom, four layers of cards must fit a 1440px laptop with the panel open. Types and every other column are one click away in the Table view, and the card shows "+N columns" so nothing looks complete when it is not. Each row has a tooltip with its name and type.

**Hubs are labels.** A table counts as a hub when most of the database points to it, such as `users` through `created_by_id`. It is drawn as a pill on each box ("→ users"), never as lines, unless "Show hubs as edges" is on. When the card header runs out of room, the table name wins and the pills are clipped.

**Under 1366px the rail collapses to icons** (56px), and the panel narrows to 340px. That gives the canvas about 880px at 1280.

**Minimum zoom of 0.8 on ER diagrams.** Below that, 11.5px mono labels drop under about 9px and stop being readable. A drawing that does not fit is shown at 0.8, centred on the focus table, and the reader pans. The Area map allows 0.7 because its text is larger.

## Tokens

All values live in `tokens.json`. `build.py` turns them into CSS custom properties: `--kg-*` on `:root`, the dark values under `@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme="light"])`, and again under `:root[data-theme="dark"]`, so the explorer follows the system theme and a toggle can override it. Run `python3 design/build.py --check` after any colour change. Every text colour must pass 4.5:1 on both surfaces, and every area colour 3:1 on the canvas. All pairs pass today.

**Colour.** Neutrals: `canvas` (the diagram background, with a faint 20px dot grid), `surface`, `surface-2` and `surface-3` (panels, wells, tags), `border` and `border-strong`, and three text levels (`text`, `text-2`, `text-3`). The accent is `accent` for fills, `accent-text` for text on light backgrounds, `accent-soft` for selection fills and `accent-line` for selected borders. Semantic colours are `success`, `warning`, `danger` and `info`, each with a `-soft` background. Graph colours sit under `graph-*`. In light mode the canvas is `#F5F5F3` and the surface is white. In dark mode they are `#0D0E10` and `#141518`, with text at `#EDEDEC`.

**Type scale** (size/line, px): caption 11/16, overline 11/16 uppercase with 0.06em tracking, small 12/18, body 13/20 (the UI default), large 14/22, title 16/24, heading 20/28, display 28/34. Mono: mono-small 11.5/16, mono 12.5/18, mono-title 15/22. Weights are 400, 500, 550 and 600. Numbers always use tabular figures.

**Spacing** uses a 4px base: 2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 40, 48, 64. Panels pad 20px horizontally, and rows are 24 (card), 30 (nav) or 34px (table grid).

**Radii:** xs 3 (tags), sm 5 (small buttons), md 7 (buttons, inputs, code), lg 10 (cards, panels' inner boxes), xl 14 (area boxes), full (chips, pills).

**Elevation:** shadow-1 for cards on the canvas, shadow-2 for popovers and floating strips, shadow-3 for toasts and dialogs. Dark mode uses deeper shadows and relies more on borders.

**Motion:** fast 120ms for hover and colour, base 180ms for popovers and sections, slow 280ms, layout 360ms for any animated re-layout. The easing is `cubic-bezier(0.2, 0, 0, 1)`. Under `prefers-reduced-motion` every duration is 0 and layouts do not animate.

## Graph encodings

| Thing | Encoding |
|---|---|
| Table | A card with a 10px rounded square glyph in its area colour. In lists, the same glyph next to the mono name, with the schema in `text-3`. |
| Column | An 8px circle glyph in `text-2`, and a row in a card. |
| Concept | A hollow diamond in teal (`graph-concept`). |
| Metric | A hexagon in violet (`graph-metric`). |
| Area | A large rounded box with its name, a table count, a dot bar (one 6px square per table, up to 24) and its top three tables. |
| Declared relation | A solid 1.5px line in `graph-edge-declared`. |
| Inferred relation | A dashed line (5 4) in `graph-edge-inferred`, with opacity stepping with confidence: 1.0 at 0.95 and above, 0.8 at 0.85, 0.6 at 0.7, 0.4 below. A mono confidence pill sits at the midpoint. |
| Polymorphic relation | A dash-dot line in magenta (`graph-edge-poly`), and a dashed magenta border on the table. |
| Concept link | A dotted teal line, with no arrowhead. |
| Cardinality | A crow's foot at the FK (many) end and a bar at the PK (one) end, both in the line's style. |
| Hub relation | A pill on the box ("→ users"), not a line. |
| Neighbour from another area | A dashed card on `surface-2`, with its area named in the header. |
| Hover | The edge turns brass and thicker, both column rows get the `accent-soft` fill, and other edges fade to 25%. |
| Selected / focus | A brass border plus a 3px `accent-soft` ring. |
| Active path | Brass lines and brass borders on the two end cards. |
| Confidence chip | A mono value plus a 4-bar meter. The bands are high (0.9 and above, 4 bars), medium (0.7 and above, 3 bars) and low (below 0.7, 2 bars in warning colour). The default floor is 0.6; "Show weak relations" lowers it to 0.4. |
| Key tags | PK on brass fill; UK on brass-soft; FK on info-soft; IX in grey with the column's position in the index ("IX 2"). |

**Area and schema colour.** Areas are ordered by table count, and the first eight get the curated palette: blue, green, orange, violet, rose, olive, brown and slate. Both themes are tuned to at least 3:1 on the canvas. Area nine and above get generated colours in OKLCH, stepping by the golden angle at fixed lightness and chroma, and skipping the hue ranges reserved for brass (70–100°), teal (170–200°) and magenta (330–355°) (`KG.schemaColor(i, theme)`). Past about 16 areas, adjacent hues get close: rely on the area name and position, and keep the colour as a secondary cue. Hop order is never encoded by hue.

## Views and layout

The shell is a left rail (search box, Areas, Path, Recent, the two relation toggles, theme), a view (52px header plus canvas or document) and a right context panel (380px, or 340px below 1366). The panel opens for a selection and holds context, never the primary action.

**Area map.** A radial-force layout of area boxes (see Decisions). Lines are straight and clipped at box edges. Their width is `1 + 5 * sqrt(count / max)` px, and their count is printed in a pill. Above 40 areas, the smallest merge into a dashed "Other areas" box that expands into a list. The panel shows database totals, relation counts by kind, the largest areas and the hubs. Clicking a box opens the Area.

**Area.** An ER diagram, dagre left to right, with referenced (parent) tables placed before referencing (child) tables: `ranksep` 60, `nodesep` 22, cards 196px wide. Relations attach to column rows. A legend sits bottom-left, the "Neighbouring tables" strip runs along the bottom (ordered by relation count; clicking one adds it as a dashed card), and the zoom controls sit bottom-right. Areas over 25 tables show the 25 most connected, with a "Show all" callout. Selecting two cards enables "Path between selected".

**Path.** From and To fields, then a max-joins segmented control (3, 4, 5). The best route is a wrapping chain of compact table cards (168px), joined by connectors that show `alias.col = alias.col`, the confidence, the cardinality and any warning. The SQL block follows, with "Copy SQL" as the one primary button, and then up to three other routes as one-line rows with their warnings. The panel holds the evidence for each step and the ranking rule.

**Table** (and Column). A document page, not a canvas. The header shows the area, the verified badge, `schema.table` at display size, what one row is, and fact chips (rows, columns, relations, freshness, soft-delete column). Below come the column grid (column, type with a `null` chip only when nullable, keys, flags, values or profile, empty rate), then keys and indexes in column order, then "Points to" and "Pointed to by" grouped by column and area. Groups over 8 collapse. Hubs appear as a line of pills. The panel holds the selected column: meaning, codes with frequency bars (undocumented codes in warning colour, with a callout), use (filter, index, metrics) and references.

**Search.** A large field, type filters with counts, and keyboard hints. Results are grouped by type (concept, metric, table, column, value). Each result shows its title in mono (concepts and metrics in sans), the matched text highlighted in `accent-soft`, a reason chip (name, synonym, context, enum label, profiled value), the reason text and the area. Enter opens the top result in the view that fits it. The panel previews the highlighted result.

**States** (prototype `#states`): first open, loading a large graph (spinner plus skeleton rows with a concrete message), a damaged data file, no path found, no search results (with a "did you mean"), and an area too large to draw whole. Each state names what happened and offers the next action.

## Components

The component classes are in `keygraph.css`, all prefixed `kg-`: shell (`kg-app`, `kg-rail`, `kg-nav-item`, `kg-viewhead`, `kg-panel`, `kg-section`), controls (`kg-btn` and its `--primary`, `--ghost`, `--sm` and `--icon` variants, `kg-seg`, `kg-input`, `kg-select`, `kg-switch`, `kg-toggle`, `kg-kbd`, `kg-search`, `kg-popover`, `kg-menu-item`), data (`kg-entity`, `kg-glyph--*`, `kg-chip[data-kind]`, `kg-conf`, `kg-key--*`, `kg-badge--*`, `kg-hub`, `kg-kv`, `kg-callout--pitfall`, `kg-table`, `kg-rel`, `kg-relgroup`, `kg-profile-row`, `kg-stats`, `kg-code`), diagram (`kg-viewport`, `kg-tcard`, `kg-area`, `kg-edge--*`, `kg-link`, `kg-strip`, `kg-nchip`, `kg-legend`, `kg-zoom`), path (`kg-chain`, `kg-step`, `kg-warn`, `kg-alt`, `kg-rank`, `kg-sqlblock`), document (`kg-doc`, `kg-fact`, `kg-cols-grid`, `kg-enum`), search (`kg-result`, `kg-result-group`), and states (`kg-empty`, `kg-skeleton`, `kg-spinner`, `kg-error`, `kg-toast`). Icons are 16px, 1.5px stroke, round caps, drawn for keygraph (see the `I` map in `prototype/src/app.js`).

## For the generator

1. Run `python3 design/build.py --inline`.
2. Inline into `keygraph.html`, in this order: `keygraph.inline.css` in a `<style>`, then `vendor/dagre-1.1.4.min.js`, `kg-theme.js` and `src/kg-diagram.js` in `<script>` tags, escaping `</script`.
3. Keep the dagre and font licence texts in an HTML comment, as is done for cytoscape today.
4. Call `KGDiagram.mount(host).er({tables, edges})` for the Area view and `.map({areas, links})` for the Area map. Then call `.fit({minK: 0.8, focus})`. The data shapes are documented at the top of `kg-diagram.js`, and `prototype/src/data.js` is a full example.
5. Path, Table and Search are plain HTML from the component classes; `prototype/src/app.js` shows the markup for each one.
6. Use the favicon as a data URI from `logo/favicon.svg`.

## Logo

The mark is a key whose shaft forks into two graph nodes: the bow is the key, the fork is the relation, and the brass node is the one keygraph inferred. It is drawn on a 32 grid with 3px strokes, so it holds at 16px. The wordmark is Inter SemiBold, outlined to paths, with −2.5% tracking, so the logo needs no font. Use `keygraph-logo-light.svg` on light backgrounds, `-dark.svg` on dark ones and `-auto.svg` where the SVG can follow the colour scheme. The favicon is the mark on a graphite tile (SVG, ICO, and PNG at 16, 32, 180 and 512). Keep clear space of half the mark's height on every side. Do not recolour the brass node, add effects, or set the wordmark in another font.

## Open items

- Real-data check: the prototype uses a 63-table fake shop. The Area and Area map layouts must be checked on the private 2,900-table build once areas are computed (largest area size and area count).
- Keyboard model: arrow keys between cards along relations, and Enter to open. The boxes are focusable, but the key handling is not specified yet.
- The ER card hides hub pills that do not fit. If that hides too much in practice, add a "+N hubs" pill.
- The `kg-theme.js` cytoscape stylesheet is kept only for migration. Delete it once no view uses cytoscape.
