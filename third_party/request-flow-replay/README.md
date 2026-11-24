# Request Flow Replay

An animated sankey diagram that replays one day of production traffic through a platform. Motion carries what a static sankey can't: latency, provenance after merges, queues, and retries.

## Intent

- **Form:** an explanatory visualization you can scrub, played like an incident replay. It is not a dashboard.
- **Person and context:** an SRE or platform engineer at a desk the morning after an incident. They want to know what saturated, whose traffic caused it, and how it cascaded. The pen should work for a curious CodePen visitor too.
- **Core experience:** press play and watch the 14:20 incident spread from a cache flush to a saturated database, then to a retry storm, and back out.
- **Must keep achieving:** each animated channel encodes one real quantity, and widths use one fixed scale for the whole day. The incident has to come out of the model's mechanics, never from scripted numbers.

## Design decisions

- **Encodings.** Each one adds information to the static sankey:
  - Width is req/s on a single scale for the whole day, so 03:00 and 20:00 compare honestly.
  - Dot travel time on a hop is that hop's latency, on a log scale because values span 0.8 ms to 3 s. Each dot samples a lognormal between p50 and p99, so tail latency shows as stragglers.
  - Dot color is the client that sent the request. Within a ribbon, colors run in lanes sized to each client's share, so provenance survives merges at the gateway and services.
  - A pile left of a store is its queue. The pile's depth is the wait relative to the 2.5 s pool timeout, or for Kafka, consumer lag relative to one hour. Queued dots wait there.
  - A loop is retried failures. Bands drop from beneath the 5xx/timeout nodes, run along the bottom, and climb back into each client from the left. A forward-only sankey can't draw a cycle, so the loop makes one. The loops drop through the empty space under the outcome column rather than through a lane on the right, which saves the figure that width.
- **The incident is simulated, not drawn** (`model.ts`). Stores have capacity and a pool queue, and saturated pools thrash, which lowers effective capacity. Clients retry by policy, and a retry repeats the call that failed, so retried traffic returns along that client's primary-bound paths and skips the edge cache. That routing is what makes retries matter. With retries spread like fresh traffic, thrashing alone carried the outage, which contradicted the story. The result: 24 minutes above SLO (14:23 to 14:46). Without the shed, retries hold it there until 15:11. With a retry budget and no shed, it clears by 14:47 on its own. The recovery copy is computed from these runs (`DayFacts`), and other event times are measured, so re-measure after retuning.
- **The evening peak is the counterpoint.** It is the highest load of the day and fully healthy. That is the takeaway: load alone was never the problem.
- **Information architecture: three zones, each with one job.**
  - **Status (top)** shows where you are and how healthy things are: the clock and environment, throughput, errors, burn rate against a 99.9% SLO, and the slowest hop. It is laid out as one two-row grid, so every label sits on the top line and the clock and all values share one baseline. On the right is one toolbar (Walkthrough, Follow a request, Hourly ledger), drawn as a single bordered group with hairline dividers. A tool fills in while it is active.
  - **Control rail (right on desktop, a bottom sheet behind "Controls" below 900 px).** On desktop it holds one panel at a time and never scrolls: What if normally, or the waterfall while you follow a request. Closing the waterfall brings What if back. Its sections are:
    - What if: the levers, plus the replay compared with the actual day.
    - Explore (narrow screens only, where the toolbar doesn't fit): follow a request, the hourly ledger, and the walkthrough.
    - While you follow a request, its waterfall replaces What if in the rail. On narrow screens it docks above the figure instead.
  - **Key (under the figure, always visible)** shows width, dot pace, pile, loop and the four client colors. The colors are toggles: select one to highlight that client's path. This interactive-legend pattern puts the color key and the filter in one place. A client's retry policy is in the swatch's tooltip and accessible name, and in the inspector for its node.
  - **Transport (bottom)** is time only: play and a full-width scrubber drawn as the day's sparkline.
    - The axis reads in HH:MM. The playhead carries its time, hovering shows the time under the pointer, and axis labels hide while a readout covers them.
    - On wide timelines, every event is annotated: a tick at its exact time and a short label in the row above. Labels that would collide merge into a group centered on its events, so the incident's seven labels fan out around it, joined to their ticks by curved leaders.
    - If even that can't fit, or on narrow screens, the timeline shows one diamond per stretch of the day (the events flagged `marker`), so labels never overlap.
  - **The walkthrough is a story control, not a playback control.** It sits in the header toolbar on desktop. On narrow screens it sits in the idle incident ticker and in the Controls sheet.
  - **What-if is a mode, shown without moving anything.** The actual day needs no label. While a lever differs, a mode chip ("WHAT-IF · Retry budget", or "n changes") appears in the header's existing free space next to the toolbar. Selecting it opens the What if section, its × returns to the actual day, and its tooltip and accessible name compare the replay with the actual day. On narrow screens the Controls button carries the mode instead: it fills in and reads "What-if", at the same size. Earlier versions used an "Actual day" badge, which didn't explain itself, and then a banner, which pushed the layout down. Both were rejected, so don't bring either back.
  - These replaced an earlier layout where controls were spread across the header and footer wherever there was room.
- **Callouts.**
  - On desktop, a callout sits beside its node, at whichever of four sides or a 9×7 grid position overlaps nodes and retry lanes least. A leader line ties it to the node.
  - On phones and tablets, the callout docks as a fixed-height incident ticker above the figure, and its node gets a ring instead. The idle ticker names the next annotated moment.
- **The flow runs along the figure's longer side.** When the figure is more than 1.15× taller than wide, it rotates to run top to bottom, using the same layout code (flow coordinates u/v mapped to x/y). Column gaps are weighted, and the last gap is widest because it holds both store and outcome labels. When that gap is under 260 px, or in portrait, labels use short names and values drop their units. In portrait, value lines appear only for nodes with a queue or failure. Tapping a node gives the rest.
- **Playback slows from 12 to 1.6 replay minutes per second** between 14:12 and 15:35, so the incident gets time while the rest of the day stays brief. Seeks pre-run the dot engine for 9 s so a jump lands on a full pipeline.
- **Content uses SRE vocabulary:** req/s, p50 · p99, pool wait, consumer lag, burn ×, 429, 5xx, client timeout.

## Design system

The system is drawn from an incident-review console: a graphite ground, ink in three strengths, and color reserved for meaning. There are four client hues (an Okabe-Ito-derived set chosen for color-vision deficiency) and one failure red, which appears only where requests fail or the SLO burns. Labels use IBM Plex Sans Condensed, and figures use IBM Plex Mono with tabular numerals.

- `tokens.css` holds every value, grouped by color, type, space and shape, marks, and motion. Tokens are named by role, for example `--ribbon-alpha-focus`, `--marker-drop` and `--client-mobile`.
- Mark values (dot radius and alphas) and GSAP motion (durations, eases) are also CSS tokens. `main.ts` reads them with `getComputedStyle`, so canvas, SVG and CSS can't drift apart.
- To add a token, define it in `tokens.css` under its group and reference it with `var()`. In TypeScript, read it with `token()` or `tokenNumber()`.

## Data visualization

- **Audience:** engineers who know request paths, queues and retries, studying a replay rather than glancing at it.
- **Data:** a per-minute flow network (22 nodes, 37 links) with per-client shares, plus queues, waits and retries.
- **Insight:** retries, not load, turned a cache flush into a sustained outage, and the cheapest fix was in the clients and the gateway, not in the database.
- **Goal:** diagnose. That means seeing the cause chain, tracing whose traffic hurt, and comparing healthy load to failing load.
- **Form:** a sankey, because the data is flow between stages. Animation extends it rather than replacing it.
- **Accessible equivalent:** the hourly ledger table, a computed text summary in the figure's caption, live announcements of events, and focusable nodes whose labels carry their readings.

## Usage

- **Play/pause:** the button, or Space.
- **Scrub:** drag the timeline, or focus it and use the arrow keys (one minute per step).
- **Step through time:** ← and → move 5 minutes, and Shift moves an hour. `[` and `]` jump to the previous or next event.
- **Event labels:** select any to jump just before it. The current event's label is highlighted. On narrow screens, the three diamonds jump to the night's consumer lag, the incident and the evening peak.
- **Highlight a client:** select its color in the key to highlight its lane through every ribbon and dim the others. Select it again to clear.
- **Inspect:** hover, tap or Tab to a node or ribbon to see throughput, capacity, utilization, queue, p50 · p99 and client mix. Escape dismisses it.
- **Hourly ledger:** the same day as a table. It follows the replayed day, so it changes with the what-if levers.
- **Walk through the incident:** pauses at each moment of the story; Continue plays to the next. It opens by itself on a first visit (remembered in localStorage) and ends if you scrub or jump.
- **What if:** four levers re-simulate the whole day in about 150 ms. They are pre-warming the cache, a retry budget, adaptive concurrency at the gateway, and on-call's manual shed. The panel compares the replay with the actual day, and the scrubber draws the actual day's errors as a dashed line.
- **Follow a request:** click a dot, or use the crosshair button, which picks a request just past the gateway. A waterfall lists every hop, queue wait and retry in real milliseconds, and the followed dot is ringed.

## Trial features

Six features were added together for review; pulses, normal-day outlines and store sparklines were removed after it. The three that remain are each self-contained and can be removed without touching the others:

| Feature | Markup and styles | Code |
| :--- | :--- | :--- |
| What-if levers | `#rail-whatif`, `.mode-chip`, `.transport__controls[data-mode]`, `#tpl-lever`, `.timeline__actual` | `levers`, `scenario.prewarmHitFloor`, `retryBudgetShare` and `adaptive` in `data.ts`; `DayOptions`, `dayOutcome` in `model.ts`; the What if section in `main.ts` |
| Follow a request | `.trace`, `.rail__trace`, `.trace-dock`, `#tpl-span`, `follow-start` and `follow-start-rail` | `Span`, `follow`, `dotNear`, `freshRequest` in `particles.ts`; the Following section in `main.ts` |
| Walkthrough | `tour-start` in `.toolbar`, `.callout__tour`, `tour-start-rail`, `.callout__actions` | `tourIntro`, `tourStops` in `data.ts`; the Walkthrough section and `config.tour` |

## Architecture

- `index.html`: structure (status, figure, key, rail, transport) and every template. That includes the SVG templates (nodes, ribbons, retries), client rows, levers, markers, inspector rows, waterfall rows and ledger rows.
- `tokens.css`: the design system.
- `styles.css`: layout and component styles. It is mobile-first, with desktop at 900 px and up and phone overrides at the end.
- `data.ts`: the platform. It holds nodes, clients (hourly traffic, entry, retry policy), routing shares, capacities, latencies, batch jobs, incident causes and annotated events.
- `config.ts`: behavior. It holds playback pacing, requests per dot, the latency-to-seconds mapping, layout proportions and callout placement.
- `model.ts`: the pure simulation. `simulateDay()` steps 1,441 minutes, and `frameAt()` interpolates between them.
- `layout.ts`: geometry for one frame in flow coordinates. It covers nodes, ribbons, piles, capacity brackets and retry loops, with landscape/portrait mapping.
- `particles.ts`: the dot engine. It handles spawning, routing by current per-client shares, queues, retries, per-request traces, hit-testing, and canvas drawing.
- `main.ts`: DOM wiring, render loop, readouts, callout, inspector, transport, keyboard, reduced motion and ledger.

The pen uses plain TypeScript with no framework. It has a single view, and its state is a minute, a play flag, a traced client and a focus. The figure has three layers: an SVG for ribbons, trace lanes and loops; a canvas for up to 2,600 dots; and an SVG overlay for nodes and labels, so labels stay legible above the dots. There's no D3. `d3-sankey` refits its scale on every layout, which would break the fixed all-day scale, and a six-column layout is small enough to write directly.

## Customizing

- **Add a client:** add it to `clientIds`, `nodes` (column 0), `clients` and `serviceMix` in `data.ts`, and add `--client-<id>` to `tokens.css`.
- **Add a service or store:** add it to `nodes`, `storeMix` and, for stores, `storeErrors`. Links derive from these tables.
- **Change the incident:** use `scenario` (deploy time, hit-rate floor, warm-up, shed window, pool timeout, thrashing) and `batchJobs`. Re-check `events` copy and times against the model afterwards.
- **Pacing:** `config.playback` sets base speed, slow windows and the reduced-motion step.
- **Dot density and travel:** `config.particles.requestsPerDot`, `travelBaseSeconds` and `travelSecondsPerDecade`.
- **When capacity brackets appear:** `config.layout.capacityShownAbove`, a utilization share.

## Dependencies

- `gsap` 3.15.0: callout transitions, scrub tweens, the ticker that drives the render loop, and `gsap.matchMedia()` for reduced motion.
- `typescript` 5.9.3 (dev): matches CodePen's TypeScript block.

## Accessibility and devices

- Every node is focusable and named with its current reading. The timeline is a native range with a spoken value of time, req/s and errors. Event changes are announced politely, and the ledger dialog holds the full day as a table.
- With reduced motion, the dots are removed. Playback steps 15 minutes every 1.2 s, node labels add each hop's p50 latency, and the piles, loops, widths and callouts remain, so no information is lost.
- Desktop is primary, with the rail as a column. Below 900 px the rail becomes a bottom sheet and the callout docks as a ticker. Tall figures (phones in portrait) get the rotated flow. The layout uses safe-area padding, `dvh` height and 44 px touch targets.

## Guardrails

- Timeline event labels must never overlap; fall back to diamonds rather than crowd them.
- The desktop rail must never scroll; it shows one panel at a time.
- Toggling a what-if must not move the layout; the mode lives in the header chip (wide) or the Controls button (narrow).
- Never let the width scale adapt to the current frame. Honesty across hours depends on `dayBounds`.
- Keep color for clients and failure only. Ribbons and nodes stay neutral.
- Dot speed must stay a function of latency alone, never of the playback speed.
- Event copy must match the model's output. If you retune `scenario`, re-measure. The recovery event's figures are computed, but its times and the others are not.
- What-if replays keep the actual day's width scale (the larger of the two), so a lever never makes the diagram look calmer by rescaling.
- Retries must keep routing like the calls that failed; spreading them like fresh traffic removes the cause of the outage.
- No markup in strings. Everything is cloned from `index.html` templates.

## Credits

- Fonts: IBM Plex Sans Condensed and IBM Plex Mono (SIL Open Font License), via Google Fonts.
- Icons: paths from Tabler Icons (MIT).
- Traffic data is synthetic and generated by the pen's own model.
