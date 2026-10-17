# The Trades design language: an instrument panel

Trades should read like a precision instrument: dense enough for someone who lives in a
Bloomberg terminal, calm and explained enough for someone opening it for the first time. The
references are three tools that manage both at once:

| Reference | What we take from it |
| --- | --- |
| **Chokepoint Watch** (strait traffic monitor; the favourite) | Monochrome paper and ink with **one signal orange**; mono caps labels; crop marks and hatched bands; big light numerals with small units (`14 VSL`); a target inspector of label/value pairs; an alert feed with timestamps and `CRIT` / `WARN` / `INFO` tags; a queue rail on the left; a breadcrumb, a clock and an `ALERTS 3` button in the top bar. |
| **FireTrace** (wildfire forensics) | Everything hangs off one timeline and one cursor; small-multiple charts that move together; a key-events list; an incident overview as key/value pairs; layers you can switch on and off. |
| **Induction bench** (physics apparatus) | Numbered figures with captions (`Fig. 2 Oscilloscope`); a live sentence that says what is happening (`◆ Magnet far away, so the flux barely changes.`); the apparatus "as modeled"; readouts with units and a sub-caption; the equation shown where it explains the number. |

## Principles

1. **Answer first, detail on demand.** Every screen opens with its state and the next action:
   the status line, a few readouts, then the detail below or one click away. A new user should
   know what the screen is for in five seconds; an expert should find any number in ten.
2. **Calm density.** Show a lot, quietly. Hairlines instead of boxes, whitespace between groups,
   square corners, no shadows on panels, no gradients. Nothing shouts unless it needs a person.
3. **Orange means attention.** Alerts, failures, the kill switch when on, the current selection,
   the anomaly in a chart. Never decoration and never a data series.
4. **Labels are mono caps, values are mono, sentences are sans.** IBM Plex Mono for labels,
   tickers, ids, times, numbers and commands; IBM Plex Sans for anything you read as a sentence.
   Units sit small and muted beside their number.
5. **Numbers are readouts.** Big numbers are light mono numerals with a unit and a label; table
   numbers are tabular and right-aligned; changes carry a sign (+/−); profit and loss is coloured
   (`pos`/`neg`) *and* signed; never more precision than the number deserves.
6. **Figures have captions.** `FIG. 2  Equity, net of costs` plus an aside (period, units).
   Annotate in place: thresholds, `LOCKED TEST`, `STOP`, the normal band. One axis per chart.
7. **Every screen narrates itself.** One sentence in the status line (`useStatusLine`): what is
   true now and what to do next, in plain words, e.g. `5 specs; none has passed research yet.
   Next: freeze one, then research it.`
8. **Explain every term.** Jargon gets a `Help` tooltip or a definition on first use. Empty
   states say what will appear here and how to make it appear (the command or the button).
9. **Words, not colour alone.** Every state is a word (`PASS`, `FAIL`, `HALT ON`, `CRIT`); colour
   only reinforces it. Text contrast at least 4.5:1; focus is a visible orange outline; dark mode
   is designed, not inverted.
10. **Motion only for life.** A pulsing dot means live data; transitions stay under 150ms;
    `prefers-reduced-motion` turns them off.

## Tokens (src/styles.css)

| Role | Light | Dark | Use |
| --- | --- | --- | --- |
| `--page` | `#ededea` | `#0b0c0d` | the paper behind panels |
| `--surface-1` | `#ffffff` | `#131416` | panels |
| `--surface-2` / `-3` | `#f6f6f3` / `#ebebe7` | `#181a1c` / `#202225` | fills, hover, pressed |
| `--border` / `-strong` | `#e1e1dc` / `#c8c8c2` | `#25282c` / `#363a3f` | hairlines |
| `--text-primary` / `secondary` / `muted` | `#121212` / `#4d4d50` / `#86868a` | `#ecece9` / `#b3b3ae` / `#85858a` | ink |
| `--accent` | `#ff5b1f` | `#ff6a2e` | attention fills and marks (ink text on top) |
| `--accent-text` | `#c2410c` | `#ff8a5c` | orange *text* (contrast-safe) |
| `--ink-fill` | `#121212` | `#ecece9` | primary buttons, pressed segments |
| `--pos-text` / `--neg-text` | `#11753a` / `#c63434` | `#2fbf55` / `#ec7b7b` | your profit and loss |
| `--up` / `--down` | blue / red | blue / red | market direction (colour-blind safe; classic green opt-in) |
| `--series-1..8` | validated palette | validated palette | categorical chart series, fixed order: blue, dark green, pink, amber, purple, red, green, and orange last (orange means attention) |

Type: body 14px sans; `h1` 22px sans 500; `h2` (panel titles) 11.5px mono caps; labels 10.5px
mono caps, tracking 0.08em; readouts 30px (22px small) mono 300. Spacing on a 4px grid (4, 8,
12, 16, 24, 32); panels pad 16px; gutters 16px. Radius 2px everywhere.

## Components

`src/components/instrument.tsx`:

| Component | Use |
| --- | --- |
| `Panel` | every container: `title` (mono caps), `fig`, `aside`, `actions`, `marks` (crop marks for the primary panel on a screen) |
| `Figure` | a chart or schematic with its caption |
| `Readout` / `Readouts` | a big number with unit, label and note; a row of them divided by hairlines |
| `KeyValues` | the inspector grid (`columns={2}` for two pairs a row) |
| `Tag` | `crit` (orange fill), `warn` (orange outline), `info`, `ok`, `ink` |
| `Hatch` | a hatched band: an inactive area, a group separator, "nothing yet" |
| `InlineText` | sentences with `code` and **bold** spans (status lines, gate details, risk messages) |

Shared classes in `styles.css`: `.btn` (outline), `.btn.primary` (ink), `.btn.danger` (orange:
the kill switch and other deliberate, risky acts), `.btn.ghost`, `.btn.small`; `.segmented`;
`.tabs`; `table.data` (mono caps headers, tabular numbers, orange left marker on the selected
row); `.field` + `.input`; `.chip.toggle`; `.callout.info|warn|bad`; `.status.good|warn|bad|info|live`
(square dots); `.badge` (square, mono caps); `.rail` (a vertical list with an orange square on the
current item); `.marks`; `.hatch`; `.label`; `.kv` and `.kv-grid`; `.sr-only` (read by screen
readers, not shown). `Help` tooltips render in a portal at fixed coordinates, so no scrolling
table or clipped panel cuts them off and they stay on screen on a phone. Anything pinned under the
top bar uses `top: var(--topbar-h)` (44px; two rows and 85px on a phone).

Existing helpers in `components/ui.tsx` (`StatTile`, `MetricTiles`, `MetricsTable`, `TradesTable`,
`Tabs`, `ParamForm`, `SizingForm`, `SymbolInput`, `Help`, `StatusDot`, `ErrorBox`, `Spinner`) are
already restyled by the shared classes; prefer them to new copies.

## Patterns

* **The shell.** Top bar: brand, breadcrumb (`TRADES / PIPELINE / STRAT-002-V1`), mono-caps
  navigation with an orange underline, then the NYSE clock and market state, `HALT ON/OFF`,
  `ALERTS n` (orange when anything needs a person) and the theme. Under it, the status line.
* **Page header.** `h1` in sans with one sentence of purpose; the screen's two to five key
  readouts on the right or in a row below it.
* **Inspector layout** (Chokepoint). A rail (queue, list of items) on the left; the main figure
  in the middle; a strip of panels below: inspector (`KeyValues`), readouts, feed.
* **Feed.** One row per event: time (mono, muted) · message (sans) · tag on the right. Newest first.
* **Timeline** (FireTrace). When data has a time axis, charts share it and a cursor; key events
  sit on the timeline as small marks.
* **Empty state.** A hatched band with one sentence and the command or button that fills it.
* **Forms.** Labels above inputs in mono caps; numbers in mono inputs; help `(?)` beside jargon;
  the primary action is the single ink button; destructive or risky acts are orange and ask first.

## Charts

TradingView lightweight-charts, coloured from the tokens (`useChartColors`). One series: an ink
line (`colors.text`), the accent only for the highlighted part. Two or more: the categorical
palette in slot order with a legend (and direct labels for up to four). Thresholds and baselines
are dashed `--axis` lines with mono labels. No dual axes, no rainbow, no 3D. The palette is
validated for colour-vision deficiency on these exact surfaces (dataviz skill); three light-mode
slots sit under 3:1 contrast, so charts always carry a legend or labels. Re-run the validator after any
change to the palette or its order: order matters, because only neighbours are checked.

## Screens: what each one answers

| Screen | The question it answers | Shape |
| --- | --- | --- |
| Pipeline (home) | Where is each strategy, and what do I do next? | queue rail of specs, an inspector for the selected one, the alert feed |
| Live Desk | What do the strategies say about my watchlist now, and why? | terminal-style watchlist, consensus readouts, the votes and their evidence |
| Lab | Would this have worked after costs, and how fragile is it? | run strip, readouts, equity and drawdown figures, trades, the optimiser heatmap |
| Simulator | How do strategies behave bar by bar, in regimes I choose? | leaderboard, equity race, event feed, regime timeline |
| Library | What is this strategy, how good is the evidence, how does it fail? | catalogue with evidence tags; each strategy as a data sheet |
| Settings | Where does my data come from, and what protects me? | forms in panels; the risk limits and the kill switch, read-only where they are Jack's |

## Rules for changing the UI

* Keep every behaviour: a redesign moves and restyles, it never drops a control or a number.
* View styles go in `src/styles/<view>.css` with the view's prefix (`pl-`, `ld-`, `lab-`, `sim-`,
  `ss-`, `lib-`, `set-`); never restyle shared classes from a view file.
* Check light, dark and a 390px-wide phone before calling a screen done.
* `npx tsc --noEmit -p web` and `npm run build` must pass.
