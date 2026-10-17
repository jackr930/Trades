import {
  Children,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type InputHTMLAttributes,
  type ReactNode,
  type RefObject,
} from "react";
import { api, arenaSocketUrl } from "../api";
import { useApp, useStatusLine } from "../state";
import type {
  ArenaAgent,
  ArenaAgentDetail,
  ArenaBars,
  ArenaEvent,
  ArenaEventKind,
  ArenaOptions,
  ArenaRunBrief,
  ArenaState,
  ArenaUpdate,
  Bars,
  Meta,
  Settings,
  StrategyMeta,
  Trade,
} from "../types";
import PriceChart, { type PriceMarker } from "../components/PriceChart";
import LineChart, { type LineSpec } from "../components/LineChart";
import { ErrorBox, EvidenceBadge, Help, ParamForm, Spinner, SymbolInput, VoteGlyph, Warnings } from "../components/ui";
import { Figure, KeyValues, Panel, Readout, Tag, type TagKind, type Tone } from "../components/instrument";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, fmtPct, fmtPrice, isNum, pnlClass } from "../format";
import { useChartColors, type ChartColors } from "../theme";
import "../styles/simulator.css";
import "../styles/strategysim.css";

type Source = "simulated" | "replay" | "realtime";

const SPEEDS = [0.5, 1, 2, 4, 8, 16, 32, 64, 200];
const MAX_EVENTS = 600;

export default function StrategySim({ runId, initialSource, initialStrategies, onOpen, onClose }: {
  runId: string | null;
  initialSource?: string | null;
  /** Comma-separated strategy ids to preselect (e.g. from the Library's race button). */
  initialStrategies?: string | null;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  if (!runId) {
    const src = (["simulated", "replay", "realtime"] as const).find((x) => x === initialSource) ?? "simulated";
    return (
      <Setup
        key={`${src}:${initialStrategies ?? ""}`}
        initialSource={src}
        initialStrategies={initialStrategies}
        onStarted={(s) => onOpen(s.id)}
        onOpen={onOpen}
      />
    );
  }
  return <RunView key={runId} runId={runId} onNew={onClose} onOpen={onOpen} />;
}

// ------------------------------------------------------------------------------------
// Instrument pieces shared by both simulators (Simulator.tsx imports them for practice mode).
// Built here, not in components/, because they belong to this screen; see the report.
// ------------------------------------------------------------------------------------

/** An option tile, after the Induction bench's material picker: a small value top-left, a name,
 *  one line of description. Pressed is an ink fill. */
export function OptionTile({ kicker, aside, name, desc, pressed, onClick, title, size }: {
  kicker?: ReactNode;
  aside?: ReactNode;
  name: ReactNode;
  desc?: ReactNode;
  pressed: boolean;
  onClick: () => void;
  title?: string;
  size?: "sm";
}) {
  return (
    <button type="button" className={`sim-tile${size === "sm" ? " sim-tile-sm" : ""}`} aria-pressed={pressed} onClick={onClick} title={title}>
      {kicker || aside ? (
        <span className="sim-tile-top">
          <span>{kicker}</span>
          {aside ? <span>{aside}</span> : null}
        </span>
      ) : null}
      <span className="sim-tile-name">{name}</span>
      {desc ? <span className="sim-tile-desc">{desc}</span> : null}
    </button>
  );
}

/** A one-of-n switch in the shared `.segmented` style. */
export function Segmented<T extends string | number>({ options, value, onChange, label, className }: {
  options: [T, ReactNode][];
  value: T;
  onChange: (v: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div className={`segmented${className ? ` ${className}` : ""}`} role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

/** A mono label above a group of tiles (the `.field` label look, for groups that are not one input). */
export function GroupLabel({ children }: { children: ReactNode }) {
  return <div className="sim-group-label">{children}</div>;
}

/** Transport glyphs, drawn so they look the same in every font. */
export function TransportIcon({ kind }: { kind: "play" | "pause" | "step" | "stop" }) {
  const shape = {
    play: <path d="M3.5 1.5v11l9-5.5z" />,
    pause: <path d="M3 2h3v10H3zM8 2h3v10H8z" />,
    step: <path d="M2 1.5v11l7-5.5zM10 2h2.2v10H10z" />,
    stop: <path d="M3 3h8v8H3z" />,
  }[kind];
  return (
    <svg className="sim-icon" width="12" height="12" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
      {shape}
    </svg>
  );
}

/** A keyboard shortcut shown on a button. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <span className="sim-kbd" aria-hidden="true">
      {children}
    </span>
  );
}

/** A run's state as a word with a square dot (the dot pulses only while live). */
export function StateBadge({ state, children }: { state: string; children: ReactNode }) {
  return (
    <span className="sim-state" data-state={state}>
      <i aria-hidden="true" />
      {children}
    </span>
  );
}

/** A row of readouts divided by hairlines; the column count follows the number of readouts. */
export function ReadoutGrid({ children, className }: { children: ReactNode; className?: string }) {
  const n = Children.toArray(children).length;
  return (
    <div className={`sim-readouts${className ? ` ${className}` : ""}`} style={{ "--n": n } as CSSProperties}>
      {children}
    </div>
  );
}

/** A mono number input with its unit (or currency) printed inside the box. */
export function UnitInput({ unit, prefix, className, ...rest }: InputHTMLAttributes<HTMLInputElement> & { unit?: string; prefix?: string }) {
  return (
    <span className={`sim-unit-input${prefix ? " sim-has-prefix" : ""}${unit ? " sim-has-unit" : ""}`}>
      {prefix ? (
        <span className="sim-unit sim-unit-pre" aria-hidden="true">
          {prefix}
        </span>
      ) : null}
      <input {...rest} className={`input num${className ? ` ${className}` : ""}`} />
      {unit ? (
        <span className="sim-unit" aria-hidden="true">
          {unit}
        </span>
      ) : null}
    </span>
  );
}

/** A line key in a series colour; dashed for the benchmark and for slots past the eighth. */
export function Swatch({ color, dashed }: { color?: string; dashed?: boolean }) {
  return <span className={`sim-swatch${dashed ? " sim-swatch-dashed" : ""}`} style={{ color }} aria-hidden="true" />;
}

/** The playback bar: transport controls, the timeline, the position and the way out. Sticky while live. */
export function Transport({ label, controls, timeline, position, end, sticky = true }: {
  label: string;
  controls?: ReactNode;
  timeline?: ReactNode;
  position: ReactNode;
  end?: ReactNode;
  sticky?: boolean;
}) {
  return (
    <div className={`sim-transport${sticky ? " sim-transport-sticky" : ""}${controls ? "" : " sim-transport-bare"}`} role="region" aria-label={label}>
      {controls ? <div className="sim-tp-controls">{controls}</div> : null}
      <div className="sim-tp-timeline">{timeline}</div>
      <div className="sim-tp-pos">{position}</div>
      {end ? <div className="sim-tp-end">{end}</div> : null}
    </div>
  );
}

export interface TimelineSegment {
  label: string;
  n: number;
  tone: "up" | "down" | "flat" | "unknown" | "plain";
  title?: string;
}

export interface TimelineMark {
  at: number;
  kind: "shock" | "buy" | "sell";
  title: string;
}

/** Width of an element, kept current as it resizes (measured after layout, outside the observer). */
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    let frame = 0;
    setWidth(Math.round(el.getBoundingClientRect().width));
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const next = Math.round(el.getBoundingClientRect().width);
        setWidth((w) => (Math.abs(next - w) >= 1 ? next : w));
      });
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [ref]);
  return width;
}

/**
 * The run's timeline (FireTrace): played bars as labelled segments (the hidden regimes, when the
 * market has them), the bars still to come hatched, key events as marks above, a cursor at the
 * current bar and bar numbers below. `band` is the larger version used in the debriefs.
 */
export function Timeline({ total, done, segments, marks = [], futureLabel = "Not played yet", band = false, label, valueText }: {
  total: number;
  done: number;
  segments: TimelineSegment[];
  marks?: TimelineMark[];
  futureLabel?: string;
  band?: boolean;
  label: string;
  valueText?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const span = Math.max(1, total);
  const at = (x: number) => Math.min(100, Math.max(0, (x / span) * 100));
  // A label goes inside its segment only when it fits with padding; otherwise the tooltip carries it.
  const charWidth = band ? 6.9 : 6.3;
  const fits = (text: string, n: number) => width > 0 && (n / span) * width >= text.length * charWidth + 14;
  let x = 0;
  const parts = segments.map((s, i) => {
    const left = at(x);
    const first = x + 1;
    x += s.n;
    const text = s.tone === "up" ? `▲ ${s.label}` : s.tone === "down" ? `▼ ${s.label}` : s.label;
    return (
      <span
        key={i}
        className="sim-tl-seg"
        data-tone={s.tone}
        style={{ left: `${left}%`, width: `${at(x) - left}%` }}
        title={s.title ?? (s.label ? `${s.label}: bars ${first}-${x} (${s.n} bars)` : `bars ${first}-${x}`)}
      >
        {text && fits(text, s.n) ? text : null}
      </span>
    );
  });
  const future = Math.max(0, total - done);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => Math.round(total * k));
  return (
    <div className={`sim-tl${band ? " sim-tl-band" : ""}`}>
      <div className="sim-tl-body" ref={box}>
        <div className="sim-tl-marks">
          {marks.map((m, i) => (
            <span key={i} className="sim-tl-mark" data-kind={m.kind} style={{ left: `${at(m.at)}%` }} title={m.title} />
          ))}
        </div>
        <div
          className="sim-tl-track"
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={done}
          aria-valuetext={valueText}
        >
          {parts}
          {future > 0 ? (
            <span className="sim-tl-future" style={{ left: `${at(done)}%` }} title={`${future} bars to come`}>
              {fits(futureLabel, future) ? <em>{futureLabel}</em> : null}
            </span>
          ) : null}
        </div>
        {band && future === 0 ? null : <span className="sim-tl-cursor" style={{ left: `${at(done)}%` }} aria-hidden="true" />}
      </div>
      <div className="sim-tl-ticks" aria-hidden="true">
        {ticks.map((t, i) => (
          <span key={i} style={{ left: `${i * 25}%` }}>
            {t.toLocaleString()}
          </span>
        ))}
      </div>
    </div>
  );
}

/** "+6.2%" as a readout: the number, and the per-cent sign as its unit. */
export function pctParts(x: number | null | undefined, digits = 1, plus = true): { value: string; unit?: string } {
  const text = fmtPct(x, digits, plus);
  return text.endsWith("%") ? { value: text.slice(0, -1), unit: "%" } : { value: text };
}

export function toneOf(x: number | null | undefined): Tone | undefined {
  const c = pnlClass(x);
  return c === "pos" ? "pos" : c === "neg" ? "neg" : undefined;
}

function firstSentence(text: string): string {
  const i = text.indexOf(". ");
  return i > 0 ? text.slice(0, i + 1) : text;
}

function plural(n: number, word: string, many = `${word}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? word : many}`;
}

// ------------------------------------------------------------------------------------
// Set-up
// ------------------------------------------------------------------------------------

const MAX_STRATEGIES = 16;

const LENGTHS: [number, string, string][] = [
  [0, "Full", "Scenario"],
  [250, "250", "1 year"],
  [500, "500", "2 years"],
  [1000, "1,000", "4 years"],
  [2500, "2,500", "10 years"],
];

const BAR_SIZES: [string, string][] = [
  ["1d", "Daily"],
  ["1h", "1 hour"],
  ["15m", "15 min"],
  ["5m", "5 min"],
  ["1m", "1 min"],
];

const SOURCE_LABEL: Record<Source, string> = {
  simulated: "Simulated market",
  replay: "Historical replay",
  realtime: "Real-time market",
};

function Setup({ initialSource, initialStrategies, onStarted, onOpen }: {
  initialSource: Source;
  initialStrategies?: string | null;
  onStarted: (s: ArenaState) => void;
  onOpen: (id: string) => void;
}) {
  const { meta, settings } = useApp() as { meta: Meta; settings: Settings };
  const [options, setOptions] = useState<ArenaOptions | null>(null);
  const [runs, setRuns] = useState<ArenaRunBrief[]>([]);
  const [source, setSource] = useState<Source>(initialSource);
  const [scenario, setScenario] = useState("random");
  const [simSymbols, setSimSymbols] = useState<string[]>([]);
  const [length, setLength] = useState(0);
  const [seed, setSeed] = useState("");
  const realDefault = settings.provider !== "synthetic" ? settings.provider : "yahoo";
  const [provider, setProvider] = useState(initialSource === "realtime" && settings.provider === "synthetic" ? realDefault : settings.provider);
  const [symbols, setSymbols] = useState<string[]>(settings.watchlists?.[settings.provider]?.slice(0, 6) ?? []);
  const [timeframe, setTimeframe] = useState(initialSource === "realtime" ? "5m" : "1d");
  const [start, setStart] = useState("2019-01-02");
  const [replayLength, setReplayLength] = useState(500);
  const strategies = meta.strategies.filter((s) => s.id !== "buy_hold");
  const [chosen, setChosen] = useState<string[]>([]);
  const [params, setParams] = useState<Record<string, Record<string, unknown>>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [cash, setCash] = useState(settings.account_equity); // your account profile
  const [slippage, setSlippage] = useState(settings.slippage_bps);
  const [commission, setCommission] = useState(settings.commission_bps);
  const [allowShort, setAllowShort] = useState(true);
  const [speed, setSpeed] = useState(8);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .arenaOptions()
      .then((o) => {
        setOptions(o);
        setSimSymbols(o.default_symbols);
        const wanted = (initialStrategies ?? "").split(",").filter((id) => strategies.some((s) => s.id === id));
        setChosen(wanted.length ? wanted : o.default_strategies.map((s) => s.id));
      })
      .catch((e) => setError((e as Error).message));
    api.arenaRuns().then(setRuns).catch(() => undefined);
  }, []);

  useEffect(() => {
    // Real-time runs need a real provider and suit intraday bars.
    if (source === "realtime") {
      if (provider === "synthetic") setProvider(realDefault);
      if (timeframe === "1d") setTimeframe("5m");
    }
  }, [source]); // eslint-disable-line react-hooks/exhaustive-deps

  const syms = source === "simulated" ? simSymbols : symbols;
  const scenarioMeta = options?.scenarios.find((s) => s.id === scenario);
  const where =
    source === "simulated"
      ? `the **${scenarioMeta?.label ?? "simulated"}** scenario (${plural(syms.length, "symbol")})`
      : source === "replay"
        ? `a replay of ${syms.join(", ")}`
        : `the live market (${syms.join(", ")})`;
  const openNote = runs.length ? ` ${plural(runs.length, "open simulation")} listed below.` : "";
  useStatusLine(
    !options
      ? error
        ? "The simulation options did not load; see the error below."
        : "Loading the simulation options..."
      : busy
        ? source === "simulated"
          ? "Generating the market's warm-up history..."
          : "Downloading the history for the strategies to warm up on..."
        : error
          ? "The simulation did not start; the reason is next to **Start simulation**. Change the set-up and try again."
          : chosen.length > MAX_STRATEGIES
          ? `${chosen.length} strategies picked; a race takes at most ${MAX_STRATEGIES}. Untick a few.`
          : !syms.length
            ? "Pick at least one symbol for the strategies to trade."
            : chosen.length < 2
              ? `Pick two or more strategies and a market, then press **Start simulation**.${openNote}`
              : `**${chosen.length} strategies** on ${where}. Press **Start simulation** to race them bar by bar.${openNote}`,
  );

  if (!options) return error ? <ErrorBox error={error} /> : <Spinner label="Loading simulation options..." />;

  const start_ = async () => {
    setBusy(true);
    setError(null);
    const cfg: Record<string, unknown> = {
      source,
      symbols: syms,
      strategies: chosen.map((id) => ({ id, params: params[id] ?? {} })),
      initial_cash: cash,
      fractional: settings.fractional_shares,
      slippage_bps: slippage,
      commission_bps: commission,
      allow_short: allowShort,
      speed,
    };
    if (source === "simulated") Object.assign(cfg, { scenario, length, seed: seed.trim() ? Number(seed) : undefined });
    if (source === "replay") Object.assign(cfg, { provider, timeframe, start: start || undefined, length: replayLength });
    if (source === "realtime") Object.assign(cfg, { provider, timeframe });
    try {
      onStarted(await api.arenaCreate(cfg));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const editingMeta = editing ? strategies.find((s) => s.id === editing) : undefined;
  const realProviders = meta.providers.filter((p) => p.id !== "synthetic");
  const modern = strategies.filter((s) => s.family === "modern");
  const presets: [string, string[]][] = [
    ["Classic rules", options.default_strategies.map((s) => s.id)],
    ["Modern quant", modern.map((s) => s.id)],
    ["Modern vs classic", [...new Set(modern.flatMap((s) => [s.id, s.counterpart].filter(Boolean)))]],
  ];
  const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
  const shorters = strategies.filter((s) => s.uses_short && chosen.includes(s.id)).map((s) => s.name);
  const canStart = !busy && chosen.length > 0 && chosen.length <= MAX_STRATEGIES && syms.length > 0;
  const lengthLabel = LENGTHS.find(([v]) => v === length)?.[1] ?? String(length);

  return (
    <div className="stack">
      <div className="stack">
        <header className="card marks sim-intro">
          <div className="sim-intro-text">
            <div className="sim-kicker">Simulator · strategies</div>
            <h1>Strategy simulator</h1>
            <p>
              Every strategy gets its own paper account and reacts to each new bar as it arrives: it decides at the close using only
              the past, and trades at the next open, exactly as in a backtest. Shock a simulated market and watch who adapts, replay
              real history at any speed, or forward-test on the live market.
            </p>
          </div>
          <ol className="sim-steps" aria-label="How it works">
            <li>
              <b>01</b> Pick a market
            </li>
            <li>
              <b>02</b> Pick the strategies
            </li>
            <li>
              <b>03</b> Set cash and costs
            </li>
            <li>
              <b>
                <TransportIcon kind="play" />
              </b>
              Start, then shock it
            </li>
          </ol>
        </header>

        <Panel
          fig="01"
          title="Market"
          aside={
            source === "simulated"
              ? `${plural(simSymbols.length, "symbol")} · ${length ? `${lengthLabel} bars` : "full scenario"} · ${seed ? `seed ${seed}` : "random seed"}`
              : `${plural(symbols.length, "symbol")} · ${BAR_SIZES.find(([v]) => v === timeframe)?.[1] ?? timeframe} bars${source === "replay" ? ` · from ${start || "the start"}` : " · live"}`
          }
        >
          <Segmented
            label="Market source"
            className="sim-source"
            value={source}
            onChange={setSource}
            options={[
              ["simulated", "Simulated market"],
              ["replay", "Historical replay"],
              ["realtime", "Real-time market"],
            ]}
          />
          {source === "simulated" ? (
            <div className="sim-section">
              <div>
                <GroupLabel>Scenario</GroupLabel>
                <div className="sim-tiles" role="group" aria-label="Scenario">
                  {options.scenarios.map((s) => (
                    <OptionTile
                      key={s.id}
                      kicker={s.difficulty}
                      name={s.label}
                      desc={firstSentence(s.description)}
                      title={s.description}
                      pressed={scenario === s.id}
                      onClick={() => setScenario(s.id)}
                    />
                  ))}
                </div>
                {scenarioMeta ? (
                  <p className="sim-note">
                    <span className="sim-note-mark" aria-hidden="true">
                      ◆
                    </span>
                    <span>
                      <b>{scenarioMeta.label}.</b> {scenarioMeta.description}
                    </span>
                  </p>
                ) : null}
              </div>
              <div>
                <GroupLabel>
                  Symbols
                  <Help text="A one-factor market: stocks move with the index by their beta plus their own noise. SIMPRA and SIMPRB are a cointegrated pair for the pairs strategy. β (beta) is how far a stock moves for each 1% move of the index; σ is its own volatility per year, on top of the market's." />
                  <span className="sim-group-count">
                    {simSymbols.length} of {options.symbols.length}
                  </span>
                </GroupLabel>
                <div className="sim-tiles sim-tiles-syms" role="group" aria-label="Symbols">
                  {options.symbols.map((s) => (
                    <OptionTile
                      key={s.symbol}
                      size="sm"
                      kicker={
                        isNum(s.beta) ? (
                          <>
                            <span className="sim-greek">β</span> {s.beta.toFixed(2)}
                            {isNum(s.idio_vol) ? (
                              <>
                                {" · "}
                                <span className="sim-greek">σ</span> {Math.round(s.idio_vol * 100)}%
                              </>
                            ) : null}
                          </>
                        ) : (
                          "Pair"
                        )
                      }
                      name={s.symbol}
                      desc={s.name}
                      title={s.name}
                      pressed={simSymbols.includes(s.symbol)}
                      onClick={() =>
                        setSimSymbols(simSymbols.includes(s.symbol) ? simSymbols.filter((x) => x !== s.symbol) : [...simSymbols, s.symbol])
                      }
                    />
                  ))}
                </div>
              </div>
              <div className="sim-split">
                <div>
                  <GroupLabel>Length</GroupLabel>
                  <div className="sim-tiles sim-tiles-lens" role="group" aria-label="Length">
                    {LENGTHS.map(([v, name, kicker]) => (
                      <OptionTile
                        key={v}
                        size="sm"
                        kicker={kicker}
                        name={name}
                        desc={v ? "bars" : "its own length"}
                        pressed={length === v}
                        onClick={() => setLength(v)}
                      />
                    ))}
                  </div>
                </div>
                <label className="field sim-seed">
                  <span>
                    Seed{" "}
                    <Help text="The same seed reproduces the same market, so you can re-run it with different strategies or parameters. Blank = random." />
                  </span>
                  <UnitInput inputMode="numeric" placeholder="random" value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} />
                  <span className="hint">Blank picks a new market each time.</span>
                </label>
              </div>
            </div>
          ) : (
            <div className="sim-section">
              {source === "realtime" ? (
                <div className="callout info">
                  A forward test on live data: strategies act only when a bar completes, so nothing happens while the market is closed and a
                  daily run moves once a day. Use 1-15 minute bars to watch strategies react during the session. Parameters are in bars (the
                  published defaults assume daily bars).
                </div>
              ) : null}
              {source === "realtime" && !options.realtime_available ? (
                <div className="callout warn">
                  You are on the offline synthetic data source. Choose Yahoo Finance (no key) or Alpaca under Settings, or pick a provider
                  below.
                </div>
              ) : null}
              <div className="field">
                <span>Symbols</span>
                <SymbolInput symbols={symbols} onChange={setSymbols} max={12} />
              </div>
              <div className="sim-split">
                <label className="field sim-provider">
                  <span>Data</span>
                  <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
                    {(source === "realtime" ? realProviders : meta.providers).map((p) => (
                      <option key={p.id} value={p.id} disabled={!p.configured}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="field">
                  <span>Bar size</span>
                  <Segmented label="Bar size" value={timeframe} onChange={setTimeframe} options={BAR_SIZES} className="sim-barsize" />
                </div>
              </div>
              {source === "replay" ? (
                <div className="form-grid">
                  <label className="field">
                    <span>First bar traded</span>
                    <input className="input num" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                  </label>
                  <label className="field">
                    <span>Bars to replay</span>
                    <UnitInput unit="bars" type="number" min={20} max={5000} value={replayLength} onChange={(e) => setReplayLength(Number(e.target.value))} />
                  </label>
                </div>
              ) : null}
            </div>
          )}
        </Panel>

        <Panel
          fig="02"
          title="Strategies"
          aside={
            <>
              <b className={chosen.length > MAX_STRATEGIES ? "ss-over" : ""}>{chosen.length}</b> selected of up to {MAX_STRATEGIES}, plus
              buy-and-hold as the benchmark
            </>
          }
        >
          <div className="ss-quick" role="group" aria-label="Quick picks">
            <span className="label">Quick picks</span>
            {presets.map(([label, ids]) => (
              <button key={label} type="button" className="chip toggle" aria-pressed={sameSet(chosen, ids)} onClick={() => setChosen(ids)}>
                {label}
                <span className="muted">{ids.length}</span>
              </button>
            ))}
            <button type="button" className="chip toggle" aria-pressed={false} onClick={() => setChosen([])}>
              Clear
            </button>
          </div>
          {(["modern", "classic"] as const).map((fam) => {
            const members = strategies.filter((s) => s.family === fam);
            const picked = members.filter((s) => chosen.includes(s.id)).length;
            return (
              <div key={fam} className="ss-family">
                <div className="ss-family-head">
                  <h3 className="section-title">{fam === "modern" ? "Modern quant methods" : "Classic published rules"}</h3>
                  <span className="label">
                    {picked} of {members.length} picked
                  </span>
                </div>
                <div className="ss-picks">
                  {members.map((s) => (
                    <StrategyPick
                      key={s.id}
                      s={s}
                      checked={chosen.includes(s.id)}
                      customised={Object.keys(params[s.id] ?? {}).length > 0}
                      editing={editing === s.id}
                      onToggle={(on) => setChosen(on ? [...chosen, s.id] : chosen.filter((x) => x !== s.id))}
                      onEdit={() => setEditing(editing === s.id ? null : s.id)}
                    />
                  ))}
                </div>
                {editingMeta && editingMeta.family === fam ? (
                  <div className="ss-params">
                    <div className="ss-params-head">
                      <h3>
                        <span className="muted">Parameters ·</span> {editingMeta.name}
                      </h3>
                      <span className="row tight">
                        <button className="btn ghost small" onClick={() => setParams({ ...params, [editingMeta.id]: {} })}>
                          Reset to published defaults
                        </button>
                        <button className="btn small" onClick={() => setEditing(null)}>
                          Done
                        </button>
                      </span>
                    </div>
                    <ParamForm spec={editingMeta.params} values={params[editingMeta.id] ?? {}} onChange={(v) => setParams({ ...params, [editingMeta.id]: v })} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </Panel>

        <Panel fig="03" title={source === "realtime" ? "Accounts and costs" : "Accounts, costs and speed"} aside="the same for every strategy">
          <div className="sim-fields">
            <label className="field">
              <span>Cash per strategy</span>
              <UnitInput prefix="$" type="number" min={100} value={cash} onChange={(e) => setCash(Number(e.target.value))} />
            </label>
            <label className="field">
              <span>
                Slippage <Help text="Price lost to the bid-ask spread and market impact on every fill, in basis points: 1 bp is 0.01% of the trade." />
              </span>
              <UnitInput unit="bps / fill" type="number" step={0.5} min={0} value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} />
            </label>
            <label className="field">
              <span>
                Commission <Help text="The broker's fee on every fill, in basis points of the trade's value (1 bp = 0.01%)." />
              </span>
              <UnitInput unit="bps" type="number" step={0.5} min={0} value={commission} onChange={(e) => setCommission(Number(e.target.value))} />
            </label>
          </div>
          <label className="check sim-check">
            <input type="checkbox" checked={allowShort} onChange={(e) => setAllowShort(e.target.checked)} />
            Allow short selling
          </label>
          <p className="sim-fine">
            {shorters.length ? `Designed to short: ${shorters.join(", ")}. ` : ""}
            Shorts pay a 0.25%/yr borrow fee and borrowed cash costs 2%/yr above cash, as in the Strategy Lab.
          </p>
          {source !== "realtime" ? (
            <div className="sim-section">
              <GroupLabel>
                Speed <span className="sim-group-count">bars per second · you can change it while it runs</span>
              </GroupLabel>
              <div className="sim-tiles sim-tiles-speeds" role="group" aria-label="Speed">
                {SPEEDS.map((v) => (
                  <OptionTile
                    key={v}
                    size="sm"
                    name={v >= 200 ? "Max" : String(v)}
                    desc={v >= 200 ? "flat out" : v === 1 ? "bar/s" : "bars/s"}
                    title={speedLabel(v)}
                    pressed={speed === v}
                    onClick={() => setSpeed(v)}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </Panel>

        <div className="sim-launch" role="region" aria-label="Start the simulation">
          {error ? (
            <div className="sim-launch-error">
              <ErrorBox error={error} />
            </div>
          ) : null}
          <dl className="sim-launch-sum">
            <div className="sim-launch-key">
              <dt>Market</dt>
              <dd>{source === "simulated" ? (scenarioMeta?.label ?? scenario) : SOURCE_LABEL[source]}</dd>
            </div>
            <div>
              <dt>Symbols</dt>
              <dd>{syms.length}</dd>
            </div>
            <div className="sim-launch-key">
              <dt>Strategies</dt>
              <dd className={chosen.length > MAX_STRATEGIES ? "ss-over" : ""}>{chosen.length} + B&amp;H</dd>
            </div>
            {source === "simulated" ? (
              <div>
                <dt>Length</dt>
                <dd>{length ? `${lengthLabel} bars` : "Full"}</dd>
              </div>
            ) : null}
            <div>
              <dt>Cash</dt>
              <dd>{fmtMoney(cash)}</dd>
            </div>
            <div>
              <dt>Costs</dt>
              <dd>{fmtNum(slippage + commission, 1)} bps</dd>
            </div>
            {source !== "realtime" ? (
              <div>
                <dt>Speed</dt>
                <dd>{speedLabel(speed)}</dd>
              </div>
            ) : null}
          </dl>
          <div className="sim-launch-go">
            {busy ? <Spinner label={source === "simulated" ? "Generating the warm-up history..." : "Downloading history..."} /> : null}
            <button className="btn primary sim-go" disabled={!canStart} onClick={() => void start_()}>
              <TransportIcon kind="play" />
              {busy ? "Preparing..." : "Start simulation"}
            </button>
          </div>
        </div>
      </div>

      {runs.length ? (
        <Panel title="Open simulations" aside="kept in memory while the server runs">
          <div className="table-wrap">
            <table className="data ss-runs">
              <thead>
                <tr>
                  <th className="hide-sm">Started</th>
                  <th>Market</th>
                  <th>Status</th>
                  <th className="num">Bars</th>
                  <th className="hide-sm">Leader</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap mono hide-sm">{new Date(r.created_at * 1000).toLocaleTimeString()}</td>
                    <td>
                      {r.title} <span className="sim-dim">{r.source}</span>
                    </td>
                    <td>
                      <StateBadge state={r.status}>{statusLabel(r.status)}</StateBadge>
                    </td>
                    <td className="num">
                      {r.progress.done}
                      {r.progress.total ? <span className="sim-dim"> / {r.progress.total}</span> : null}
                      {r.progress.total ? (
                        <span className="ss-runbar" aria-hidden="true">
                          <span style={{ width: `${Math.min(100, (r.progress.done / r.progress.total) * 100)}%` }} />
                        </span>
                      ) : null}
                    </td>
                    <td className="hide-sm">
                      {r.leader.name} <span className={`num ${pnlClass(r.leader.return)}`}>{fmtPct(r.leader.return)}</span>
                    </td>
                    <td className="right nowrap">
                      <button className="btn small" onClick={() => onOpen(r.id)}>
                        Open
                      </button>{" "}
                      <button
                        className="btn ghost small"
                        onClick={async () => {
                          await api.arenaDelete(r.id).catch(() => undefined);
                          setRuns(runs.filter((x) => x.id !== r.id));
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function StrategyPick({ s, checked, customised, editing, onToggle, onEdit }: {
  s: StrategyMeta;
  checked: boolean;
  customised: boolean;
  editing: boolean;
  onToggle: (on: boolean) => void;
  onEdit: () => void;
}) {
  const note = s.kind === "pair" ? "Trades one pair" : s.kind === "cross_sectional" ? `Ranks ${s.min_symbols}+ symbols` : "Each symbol";
  return (
    <div className="ss-pick" data-checked={checked} data-editing={editing}>
      <label className="ss-pick-main" title={s.summary}>
        <input type="checkbox" checked={checked} onChange={(e) => onToggle(e.target.checked)} />
        <span className="ss-pick-kicker">
          {s.category} · {note}
          {s.uses_short ? <span title="Designed to sell short as well as buy"> · long/short</span> : null}
        </span>
        <span className="ss-pick-name">{s.name}</span>
        <span className="ss-pick-desc">{s.summary}</span>
      </label>
      <div className="ss-pick-foot">
        <EvidenceBadge level={s.evidence} />
        {s.params.length ? (
          <button type="button" className={`btn small${customised ? "" : " ghost"}`} aria-expanded={editing} onClick={onEdit}>
            {customised ? "Edited" : "Parameters"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------
// A running simulation
// ------------------------------------------------------------------------------------

function applyUpdate(prev: ArenaState, u: ArenaUpdate): ArenaState {
  const next: ArenaState = {
    ...prev,
    status: u.status ?? prev.status,
    progress: u.progress ?? prev.progress,
    speed: u.speed ?? prev.speed,
    summary: u.summary ?? prev.summary,
    feed_status: u.feed_status ?? prev.feed_status,
    error: u.error ?? prev.error,
    regime_drift: u.regime_drift ? { ...prev.regime_drift, ...u.regime_drift } : prev.regime_drift,
  };
  const first = Object.values(u.bars ?? {})[0];
  const n = first ? first.t.length : 0;
  if (n > 0 && u.cursor > prev.cursor) {
    const skip = Math.max(0, prev.cursor + 1 - (u.cursor - n + 1));
    const bars: Record<string, ArenaBars> = {};
    for (const [sym, b] of Object.entries(prev.bars)) {
      const add = u.bars[sym];
      bars[sym] = add
        ? {
            t: [...b.t, ...add.t.slice(skip)],
            o: [...b.o, ...add.o.slice(skip)],
            h: [...b.h, ...add.h.slice(skip)],
            l: [...b.l, ...add.l.slice(skip)],
            c: [...b.c, ...add.c.slice(skip)],
            v: [...b.v, ...add.v.slice(skip)],
          }
        : b;
    }
    next.bars = bars;
    next.cursor = u.cursor;
    if (prev.regimes && u.regimes) next.regimes = [...prev.regimes, ...u.regimes.slice(skip)];
    const times = first.t.slice(skip);
    next.agents = prev.agents.map((a) => {
      const eq = (u.equity?.[a.id] ?? []).slice(skip);
      return { ...a, equity_curve: { t: [...a.equity_curve.t, ...times], v: [...a.equity_curve.v, ...eq] } };
    });
  }
  const snaps = new Map((u.agents ?? []).map((s) => [s.id, s]));
  next.agents = (next.agents ?? prev.agents).map((a) => ({ ...a, ...(snaps.get(a.id) ?? {}) }));
  const fresh = (u.events ?? []).filter((e) => (n === 0 ? true : e.t > prev.cursor));
  if (fresh.length) {
    next.events = [...prev.events, ...fresh].slice(-MAX_EVENTS);
    const market = fresh.filter((e) => e.type === "injected" || e.type === "regime");
    if (market.length) next.market_events = [...prev.market_events, ...market];
  }
  return next;
}

function useRun(runId: string) {
  const [run, setRun] = useState<ArenaState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reloadRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    // Messages are numbered. `seq` is the last one reflected in the state (null until the first
    // snapshot): older messages are dropped, and a skipped number means some were missed (a dropped
    // connection, a sleeping laptop), so the state is reloaded instead of patched with a gap.
    let ws: WebSocket | null = null;
    let disposed = false;
    let gone = false; // the simulation no longer exists: stop reconnecting
    let retry: number | undefined;
    let timer: number | undefined;
    let seq: number | null = null;
    let queue: ArenaUpdate[] = [];
    let loading = false;
    let stale = false; // another snapshot was requested while one was loading
    let opens = 0;

    const load = async () => {
      if (loading) {
        stale = true;
        return;
      }
      loading = true;
      try {
        const s = await api.arenaState(runId);
        if (disposed) return;
        seq = s.seq;
        setRun(s);
        setError(null);
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      } finally {
        loading = false;
      }
      if (disposed) return;
      if (stale) {
        stale = false;
        void load();
      } else flush();
    };

    const flush = () => {
      timer = undefined;
      if (seq === null || loading) return; // applied once the snapshot arrives
      const fresh = queue.filter((m) => m.seq > (seq as number)).sort((a, b) => a.seq - b.seq);
      queue = [];
      let last = seq;
      for (const m of fresh) {
        if (m.seq !== last + 1) {
          queue = fresh; // keep them: the snapshot decides which are still new
          void load();
          return;
        }
        last = m.seq;
      }
      if (!fresh.length) return;
      seq = last;
      setRun((prev) => (prev ? fresh.reduce(applyUpdate, prev) : prev));
      // The final state (summary and any last bar) in one consistent snapshot.
      if (fresh.some((m) => m.type === "finished")) void load();
    };

    const connect = () => {
      ws = new WebSocket(arenaSocketUrl(runId));
      ws.onopen = () => {
        if (opens++ > 0) void load(); // reconnected: catch up on whatever happened meanwhile
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data as string) as ArenaUpdate;
        if (msg.type === "resync") {
          queue = [];
          void load();
          return;
        }
        if (msg.type === "closed" || msg.type === "error") {
          gone = true;
          setError(msg.type === "closed" ? "This simulation was removed." : (msg.detail ?? "simulation not found"));
          return;
        }
        queue.push(msg);
        if (timer === undefined) timer = window.setTimeout(flush, 120);
      };
      ws.onclose = () => {
        if (!disposed && !gone) retry = window.setTimeout(connect, 1500);
      };
    };

    reloadRef.current = () => void load();
    void load();
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(retry);
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [runId]);

  const reload = useCallback(() => reloadRef.current(), []);
  return { run, error, setError, reload };
}

/** Each account's line: a categorical hue in fixed slot order (by entry, never by rank). Past the
 *  eighth slot the hues repeat dashed (hue x dash), so no two strategies share a look. */
interface Look {
  color: string;
  dashed: boolean;
}

function looksFor(agents: ArenaAgent[], colors: ChartColors): Record<string, Look> {
  const out: Record<string, Look> = {};
  let i = 0;
  for (const a of agents) {
    if (a.kind === "benchmark") out[a.id] = { color: colors.deemph, dashed: true };
    else {
      out[a.id] = { color: colors.series[i % colors.series.length], dashed: i >= colors.series.length };
      i += 1;
    }
  }
  return out;
}

function ranked(run: ArenaState): ArenaAgent[] {
  return [...run.agents].sort((a, b) => b.return - a.return);
}

function currentRegime(run: ArenaState): string | null {
  return run.regimes ? (run.regimes[run.regimes.length - 1] ?? null) : null;
}

function narrate(run: ArenaState | null, error: string | null): string {
  if (!run) return error ? "This simulation could not be loaded; see the error below." : "Loading the simulation...";
  const order = ranked(run);
  const lead = order[0];
  const bench = run.agents.find((a) => a.kind === "benchmark");
  const n = run.agents.filter((a) => a.kind === "strategy").length;
  const { done, total } = run.progress;
  const realtime = run.feed.source === "realtime";
  const where = run.feed.source === "simulated" ? `the ${run.feed.scenario_label ?? "simulated"} scenario` : run.feed.symbols.join(", ");
  const regime = currentRegime(run);
  const regimeText = regime ? ` Hidden regime: **${regime}**, which the strategies can't see.` : "";
  const leads = lead ? `**${lead.name}** leads` : "";
  switch (run.status) {
    case "running":
      return realtime
        ? `Live forward test on ${where}: ${plural(done, "bar")} so far; strategies act only when a bar completes.${lead ? ` ${leads}.` : ""}`
        : `Running: ${plural(n, "strategy", "strategies")} racing on ${where}.${lead ? ` ${leads}.` : ""}${regimeText} \`P\` pauses.`;
    case "paused":
      return `Paused at bar ${done}${total ? ` of ${total}` : ""}.${lead ? ` ${leads} with **${fmtPct(lead.return)}**` : ""}${bench && lead?.id !== bench.id ? `; buy-and-hold ${fmtPct(bench.return)}` : ""}.${realtime ? " Press **Play** to resume." : " Press `P` to play or `N` for one bar."}`;
    case "ready":
      return `Ready: ${plural(n, "strategy", "strategies")} on ${where}${total ? `, ${total} bars ahead` : ""}. Press **Play**${realtime ? "" : " (`P`) to start, or `N` to step one bar"}.`;
    case "finished":
      return `Finished after ${plural(done, "bar")}.${lead ? ` **${lead.name}** finished first with **${fmtPct(lead.return)}**` : ""}${bench && lead?.id !== bench.id ? `; buy-and-hold made ${fmtPct(bench.return)}` : ""}. Read the results below, then run it again on a new market.`;
    case "error":
      return `The simulation stopped with an error at bar ${done}; the message is below.`;
  }
}

function RunView({ runId, onNew, onOpen }: { runId: string; onNew: () => void; onOpen: (id: string) => void }) {
  const { run, error, setError, reload } = useRun(runId);
  const colors = useChartColors();
  const [selected, setSelected] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);

  const control = useCallback(
    async (body: Parameters<typeof api.arenaControl>[1]) => {
      try {
        await api.arenaControl(runId, body);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [runId, setError],
  );

  // Keyboard: P or space = play/pause, N or right arrow = one bar.
  const statusRef = useRef(run?.status);
  statusRef.current = run?.status;
  const realtime = run?.feed.source === "realtime";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON" || realtime) return;
      const st = statusRef.current;
      if (st === "finished" || st === "error") return;
      if (e.key === "p" || e.key === " ") {
        e.preventDefault();
        void control({ action: st === "running" ? "pause" : "play" });
      } else if (e.key === "n" || e.key === "ArrowRight") {
        e.preventDefault();
        void control({ action: "step", n: 1 });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [control, realtime]);

  const looks = useMemo(() => looksFor(run?.agents ?? [], colors), [run?.agents, colors]);
  useStatusLine(narrate(run, error));

  if (!run) return error ? <ErrorBox error={error} /> : <Spinner label="Loading simulation..." />;
  const sym = symbol && run.feed.symbols.includes(symbol) ? symbol : run.feed.symbols[0];
  const agentId = selected && run.agents.some((a) => a.id === selected) ? selected : run.agents[0]?.id;
  const finished = run.status === "finished";
  const debrief = finished && run.summary;
  // Figures are numbered top to bottom: the debrief's two come first once the run is over.
  const figBase = debrief ? (run.summary?.regimes?.length ? 2 : 1) : 0;

  return (
    <div className="stack ss-run">
      <RunHeader run={run} looks={looks} />
      <RunTransport run={run} control={control} onNew={onNew} />
      <ErrorBox error={error ?? run.error} />
      {run.notes.map((n) => (
        <div className="callout info" key={n}>
          {n}
        </div>
      ))}
      {debrief ? <Results run={run} looks={looks} onNew={onNew} onOpen={onOpen} /> : null}
      <EquityRace fig={`FIG. ${figBase + 1}`} run={run} looks={looks} />
      <LeaderboardCard run={run} looks={looks} selected={agentId} onSelect={setSelected} />
      {run.feed.source === "simulated" && !finished && run.status !== "error" ? <InjectCard run={run} onError={setError} /> : null}
      <div className="ss-grid">
        <Figure
          fig={`FIG. ${figBase + 2}`}
          title={<>Market · {sym}</>}
          aside={
            <span className="segmented ss-symtabs" role="group" aria-label="Symbol">
              {run.feed.symbols.map((s) => (
                <button key={s} type="button" aria-pressed={s === sym} onClick={() => setSymbol(s)}>
                  {s}
                </button>
              ))}
            </span>
          }
          className="ss-market"
        >
          <MarketChart run={run} symbol={sym} agentId={agentId} colors={colors} />
          <p className="sim-figcap">
            Arrows: fills of <b>{run.agents.find((a) => a.id === agentId)?.name}</b> (choose another strategy in the leaderboard). Circles:
            events you injected.
          </p>
        </Figure>
        <EventFeed run={run} looks={looks} />
      </div>
      {agentId ? <AgentPanel run={run} agentId={agentId} look={looks[agentId]} onReload={reload} /> : null}
    </div>
  );
}

function speedLabel(v: number): string {
  return v >= 200 ? "Maximum" : `${v} bar${v === 1 ? "" : "s"}/s`;
}

function statusLabel(s: ArenaState["status"]): string {
  return { ready: "Ready", running: "Running", paused: "Paused", finished: "Finished", error: "Error" }[s];
}

function runTitle(run: ArenaState): string {
  if (run.feed.source === "simulated") return run.feed.scenario_label ?? "Simulated market";
  if (run.feed.source === "replay") return `Replay: ${run.feed.symbols.join(", ")}`;
  return `Live forward test: ${run.feed.symbols.join(", ")}`;
}

const TIMEFRAME_LABEL: Record<string, string> = { "1d": "daily", "1h": "1-hour", "15m": "15-minute", "5m": "5-minute", "1m": "1-minute" };

function barTime(run: ArenaState, t: number | undefined): string {
  return run.feed.timeframe === "1d" ? fmtDate(t) : fmtDateTime(t);
}

function RunHeader({ run, looks }: { run: ArenaState; looks: Record<string, Look> }) {
  const realtime = run.feed.source === "realtime";
  const market = run.feed_status.market;
  const { done, total } = run.progress;
  const lastT = run.bars[run.feed.symbols[0]]?.t.slice(-1)[0];
  const order = ranked(run);
  const lead = order[0];
  const worst = order[order.length - 1];
  const bench = run.agents.find((a) => a.kind === "benchmark");
  const strategies = run.agents.filter((a) => a.kind === "strategy");
  const ahead = bench ? strategies.filter((a) => a.return > bench.return).length : 0;
  const regime = currentRegime(run);
  const segs = regimeSegments(run);
  const regimeRun = segs.length ? segs[segs.length - 1].n : 0;
  const tone = regime ? regimeTone(run.regime_drift?.[regime]) : "flat";
  const [regimeName, regimeQualifier] = (regime ?? "").split(" (");
  const kicker = [
    run.feed.source === "simulated" ? "Simulated market" : realtime ? "Live forward test" : "Historical replay",
    run.feed.source === "simulated" ? `seed ${run.feed.seed}` : run.feed.provider,
    plural(run.feed.symbols.length, "symbol"),
    `${TIMEFRAME_LABEL[run.feed.timeframe] ?? run.feed.timeframe} bars`,
    `${plural(strategies.length, "strategy", "strategies")} + benchmark`,
  ].filter(Boolean);
  return (
    <section className="card marks ss-head">
      <div className="ss-head-top">
        <div className="ss-head-text">
          <div className="sim-kicker">{kicker.join(" · ")}</div>
          <h1>
            {runTitle(run)} <StateBadge state={run.status}>{statusLabel(run.status)}</StateBadge>
          </h1>
          <p>
            {run.feed.source === "simulated" ? run.feed.scenario_description : `Data: ${run.feed.provider} · ${run.feed.timeframe} bars.`}
          </p>
          {realtime && market ? (
            <p className="ss-live">
              {market.is_open ? "Market open" : `Market closed · next open ${new Date(market.next_open).toLocaleString()}`}
              {run.feed_status.last_poll ? ` · last checked ${new Date(run.feed_status.last_poll).toLocaleTimeString()}` : ""}
              {run.feed_status.error ? <span className="neg"> · {run.feed_status.error}</span> : null}
            </p>
          ) : null}
        </div>
      </div>
      <ReadoutGrid>
        <Readout
          size="sm"
          label={realtime ? "Live bars" : "Bar"}
          value={done.toLocaleString()}
          unit={total ? `/ ${total.toLocaleString()}` : undefined}
          note={lastT ? barTime(run, lastT) : "–"}
        />
        {regime ? (
          <Readout
            size="sm"
            label={
              <span className="sim-label-help">
                Hidden regime
                <Help text="The simulated market's true state. Strategies never see it; they have to infer it from prices. Changes also appear in the event feed." />
              </span>
            }
            value={
              <>
                {tone !== "flat" ? (
                  <span className={`ss-glyph ${tone === "up" ? "upc" : "downc"}`} aria-hidden="true">
                    {tone === "up" ? "▲" : "▼"}
                  </span>
                ) : null}
                <span className="ss-ellip" title={regime}>
                  {regimeName.toUpperCase()}
                </span>
              </>
            }
            note={`${regimeQualifier ? `${regimeQualifier.replace(")", "")} · ` : ""}for ${plural(regimeRun, "bar")} so far`}
          />
        ) : (
          <Readout size="sm" label="Feed" value={run.feed.timeframe.toUpperCase()} note={`${run.feed.provider ?? ""} ${realtime ? "live" : "replay"}`} />
        )}
        {lead ? (
          <Readout
            size="sm"
            label="Leader · best"
            {...pctParts(lead.return)}
            tone={toneOf(lead.return)}
            note={
              <>
                <Swatch {...looks[lead.id]} /> {lead.name}
              </>
            }
          />
        ) : null}
        {worst && worst !== lead ? (
          <Readout
            size="sm"
            label="Worst"
            {...pctParts(worst.return)}
            tone={toneOf(worst.return)}
            note={
              <>
                <Swatch {...looks[worst.id]} /> {worst.name}
              </>
            }
          />
        ) : null}
        {bench ? <Readout size="sm" label="Buy & hold" {...pctParts(bench.return)} tone={toneOf(bench.return)} note="the benchmark" /> : null}
        {bench && strategies.length ? (
          <Readout size="sm" label="Beating B&H" value={ahead} unit={`/ ${strategies.length}`} note="strategies ahead of it" />
        ) : null}
      </ReadoutGrid>
    </section>
  );
}

function RunTransport({ run, control, onNew }: {
  run: ArenaState;
  control: (b: Parameters<typeof api.arenaControl>[1]) => Promise<void>;
  onNew: () => void;
}) {
  const realtime = run.feed.source === "realtime";
  const active = run.status !== "finished" && run.status !== "error";
  const running = run.status === "running";
  const { done, total } = run.progress;
  const lastT = run.bars[run.feed.symbols[0]]?.t.slice(-1)[0];
  const segments: TimelineSegment[] = useMemo(() => {
    const segs = regimeSegments(run);
    if (segs.length) return segs.map((s) => ({ label: s.label === "?" ? "" : s.label, n: s.n, tone: s.label === "?" ? "unknown" : s.tone }));
    return done > 0 ? [{ label: "", n: done, tone: "plain" }] : [];
  }, [run, done]);
  const marks: TimelineMark[] = useMemo(
    () =>
      run.market_events
        .filter((e) => e.type === "injected")
        .map((e) => ({ at: e.t - run.start_index, kind: "shock", title: `Bar ${e.t - run.start_index}: ${e.headline ?? labelFor(e.kind)}` })),
    [run.market_events, run.start_index],
  );
  const market = run.feed_status.market;
  const controls = active ? (
    <>
      <button className="btn primary sim-play" onClick={() => void control({ action: running ? "pause" : "play" })}>
        <TransportIcon kind={running ? "pause" : "play"} />
        {running ? "Pause" : "Play"} {realtime ? null : <Kbd>P</Kbd>}
      </button>
      {!realtime ? (
        <>
          <button className="btn" onClick={() => void control({ action: "step", n: 1 })} disabled={running}>
            <TransportIcon kind="step" />
            Next bar <Kbd>N</Kbd>
          </button>
          <button className="btn" onClick={() => void control({ action: "step", n: 20 })} disabled={running}>
            +20
          </button>
          <select
            className="input sim-speed"
            aria-label="Speed"
            title="Playback speed"
            value={SPEEDS.includes(run.speed) ? run.speed : 8}
            onChange={(e) => void control({ action: "speed", speed: Number(e.target.value) })}
          >
            {SPEEDS.map((v) => (
              <option key={v} value={v}>
                {speedLabel(v)}
              </option>
            ))}
          </select>
        </>
      ) : null}
    </>
  ) : null;
  return (
    <Transport
      label="Playback"
      sticky={active}
      controls={controls}
      timeline={
        total ? (
          <Timeline
            total={total}
            done={done}
            segments={segments}
            marks={marks}
            label="Progress"
            valueText={`Bar ${done} of ${total}`}
            futureLabel={
              !active ? "Not played · the run ended here" : run.feed.source === "simulated" ? "Not played yet · regimes unknown" : "Not played yet"
            }
          />
        ) : (
          <div className="sim-tp-live">
            <StateBadge state={market?.is_open ? "running" : "paused"}>{market?.is_open ? "Market open" : "Market closed"}</StateBadge>
            <span>
              {market && !market.is_open ? `next open ${new Date(market.next_open).toLocaleString()}` : "a new bar arrives as each one completes"}
            </span>
          </div>
        )
      }
      position={
        <>
          <span className="sim-tp-bar">
            <span className="label">{realtime ? "Bars" : "Bar"}</span> <b>{done.toLocaleString()}</b>
            {total ? <span className="sim-dim"> / {total.toLocaleString()}</span> : null}
          </span>
          <span className="sim-tp-date">{lastT ? barTime(run, lastT) : "–"}</span>
        </>
      }
      end={
        active ? (
          <button className="btn ghost" onClick={() => void control({ action: "stop" })}>
            <TransportIcon kind="stop" />
            Finish
          </button>
        ) : (
          <button className="btn" onClick={onNew}>
            New simulation
          </button>
        )
      }
    />
  );
}

/** Market-direction tone of a hidden regime, from its drift (blue up, red down, grey flat). */
function regimeTone(drift: number | undefined): "up" | "down" | "flat" {
  if (!isNum(drift)) return "flat";
  return drift > 0.03 ? "up" : drift < -0.03 ? "down" : "flat";
}

function RegimeBadge({ regime, drift }: { regime: string; drift?: number }) {
  const tone = regimeTone(drift);
  return (
    <span className={`badge ${tone === "flat" ? "neutral" : tone}`}>
      {tone === "up" ? "▲ " : tone === "down" ? "▼ " : ""}
      {regime}
    </span>
  );
}

function EquityRace({ fig, run, looks }: { fig: string; run: ArenaState; looks: Record<string, Look> }) {
  const lines: LineSpec[] = useMemo(
    () =>
      run.agents.map((a) => {
        const base = a.equity_curve.v[0] || 1;
        return {
          id: a.id,
          label: a.name,
          color: looks[a.id]?.color ?? "",
          style: looks[a.id]?.dashed ? "dashed" : "solid",
          data: { t: a.equity_curve.t, v: a.equity_curve.v.map((v) => v / base - 1) },
        };
      }),
    [run.agents, looks],
  );
  return (
    <Figure fig={fig} title="Equity race" aside="return of each account, net of costs · click a name to hide it" className="ss-race">
      <LineChart lines={lines} height={320} format={(v) => fmtPct(v)} toggleable label="Strategy returns" />
    </Figure>
  );
}

function regimeSegments(run: ArenaState): { label: string; n: number; tone: "up" | "down" | "flat" }[] {
  if (!run.regimes) return [];
  const live = run.regimes.slice(run.start_index - run.first_index + 1);
  const out: { label: string; n: number; tone: "up" | "down" | "flat" }[] = [];
  for (const r of live) {
    const label = r ?? "?";
    if (out.length && out[out.length - 1].label === label) out[out.length - 1].n += 1;
    else out.push({ label, n: 1, tone: regimeTone(run.regime_drift?.[label]) });
  }
  return out;
}

function MarketChart({ run, symbol, agentId, colors }: { run: ArenaState; symbol: string; agentId?: string; colors: ChartColors }) {
  const b = run.bars[symbol];
  const bars: Bars = useMemo(() => {
    const forming = run.feed_status.forming?.[symbol];
    if (forming && b && forming.t > (b.t[b.t.length - 1] ?? 0)) {
      return { t: [...b.t, forming.t], o: [...b.o, forming.o], h: [...b.h, forming.h], l: [...b.l, forming.l], c: [...b.c, forming.c], v: [...b.v, forming.v] };
    }
    return b;
  }, [b, run.feed_status.forming, symbol]);
  const markers: PriceMarker[] = useMemo(() => {
    if (!b) return [];
    const times = new Set(b.t);
    const out: PriceMarker[] = [];
    for (const e of run.events) {
      if (e.type === "fill" && e.agent === agentId && e.symbol === symbol && times.has(e.time)) {
        out.push({
          time: e.time,
          position: e.side === "buy" ? "belowBar" : "aboveBar",
          shape: e.side === "buy" ? "arrowUp" : "arrowDown",
          color: e.side === "buy" ? colors.up : colors.down,
          text: `${e.side === "buy" ? "B" : "S"} ${fmtNum(e.qty ?? 0, 0)}`,
        });
      }
    }
    for (const e of run.market_events) {
      if (e.type === "injected" && times.has(e.time)) {
        out.push({ time: e.time, position: "aboveBar", shape: "circle", color: colors.text, text: labelFor(e.kind) });
      }
    }
    return out;
  }, [run.events, run.market_events, agentId, symbol, b, colors]);
  if (!b) return null;
  return (
    <PriceChart
      bars={bars}
      markers={markers}
      height={320}
      follow
      initialBars={200}
      intraday={run.feed.timeframe !== "1d"}
      label={`${symbol} price chart`}
    />
  );
}

function labelFor(kind?: string): string {
  return { crash: "Crash", rally: "Rally", vol_spike: "Vol spike", regime: "Regime", gap: "Gap", break_pair: "Pair break" }[kind ?? ""] ?? "Event";
}

function LeaderboardCard({ run, looks, selected, onSelect }: {
  run: ArenaState;
  looks: Record<string, Look>;
  selected?: string;
  onSelect: (id: string) => void;
}) {
  const rows = ranked(run);
  const bench = run.agents.find((a) => a.kind === "benchmark");
  const finals = run.summary ? Object.fromEntries(run.summary.leaderboard.map((r) => [r.id, r])) : null;
  return (
    <Panel
      title="Leaderboard"
      aside={`${run.status === "finished" ? "final" : "live"} · ${plural(rows.length, "account")} · select a row to inspect it`}
      className="ss-board"
    >
      <div className="table-wrap">
        <table className="data ss-lb">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Strategy</th>
              <th className="ss-lb-holdcol">Holding</th>
              <th className="num hide-sm">Equity</th>
              <th className="num">Return</th>
              <th className="num hide-sm">
                <span className="sim-label-help">
                  vs B&amp;H <Help text="This account's return minus buy-and-hold's, in percentage points." />
                </span>
              </th>
              <th className="num">
                <span className="sim-label-help">
                  DD <Help text="Drawdown: loss from the account's highest value so far." />
                </span>
              </th>
              <th className="num hide-sm">
                <span className="sim-label-help">
                  Expo <Help text="Gross exposure (long + short) as a share of equity." />
                </span>
              </th>
              <th className="num">
                <span className="sim-label-help">
                  Trades <Help text="Closed round trips: a position opened and closed again." />
                </span>
              </th>
              <th className="num hide-sm">
                <span className="sim-label-help">
                  Costs <Help text="Slippage and commissions paid so far." />
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a, i) => {
              const vs = bench && a.id !== bench.id ? a.return - bench.return : null;
              return (
                <tr
                  key={a.id}
                  className={`${a.id === selected ? "selected" : ""}${i === 0 ? " ss-lead" : ""}`}
                  onClick={() => onSelect(a.id)}
                  tabIndex={0}
                  onKeyDown={(e) => (e.key === "Enter" ? onSelect(a.id) : undefined)}
                  aria-selected={a.id === selected}
                >
                  <td className="num ss-rank">{String(i + 1).padStart(2, "0")}</td>
                  <td className="ss-lb-strat">
                    <span className="ss-lb-name">
                      <Swatch {...looks[a.id]} />
                      <span className="ss-lb-n">
                        {a.name}
                        {i === 0 ? <Tag kind="ink">Lead</Tag> : null}
                        {a.kind === "benchmark" ? <Tag>Bench</Tag> : null}
                        {a.stopped ? <Tag kind="crit">Wiped out</Tag> : null}
                      </span>
                    </span>
                    <span className="ss-lb-sub">{positionsText(a)}</span>
                  </td>
                  <td className="ss-lb-holdcol ss-lb-hold">{positionsText(a)}</td>
                  <td className="num hide-sm">{fmtMoney(a.equity)}</td>
                  <td className={`num ${pnlClass(a.return)}`}>{fmtPct(a.return)}</td>
                  <td className={`num hide-sm ${pnlClass(vs)}`}>{vs === null ? "–" : fmtPct(vs)}</td>
                  <td className="num">{fmtPct(a.drawdown, 1, false)}</td>
                  <td className="num hide-sm">{fmtPct(a.gross, 0, false)}</td>
                  <td className="num">{a.trades}</td>
                  <td className="num hide-sm">{fmtMoney(finals?.[a.id]?.total_costs ?? a.costs)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function positionsText(a: ArenaAgent): string {
  const entries = Object.entries(a.weights).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]));
  if (a.stopped) return "account wiped out";
  if (!entries.length) return Object.keys(a.pending).length ? "orders pending" : "in cash";
  const parts = entries.slice(0, 2).map(([s, w]) => `${s} ${fmtPct(w, 0)}`);
  return parts.join(", ") + (entries.length > 2 ? ` +${entries.length - 2} more` : "");
}

const REGIME_NAMES: Record<string, string> = { bull: "Bull", bear: "Bear", range: "Range-bound", crash: "Crash" };

function InjectCard({ run, onError }: { run: ArenaState; onError: (e: string | null) => void }) {
  const kinds = run.feed.events_available ?? [];
  const [custom, setCustom] = useState<ArenaEventKind | null>(null);
  const [size, setSize] = useState<number | "">("");
  const [bars, setBars] = useState<number | "">("");
  const [symbol, setSymbol] = useState(run.feed.symbols[0]);
  const [regime, setRegime] = useState("range");
  const [busy, setBusy] = useState(false);
  const hasPair = run.feed.symbols.includes("SIMPRA") && run.feed.symbols.includes("SIMPRB");

  const fire = async (kind: string, body: Record<string, unknown> = {}) => {
    setBusy(true);
    try {
      await api.arenaInject(run.id, { kind, ...body });
      onError(null);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const quick = (k: ArenaEventKind) => {
    if (k.kind === "gap") return fire("gap", { symbol });
    if (k.kind === "regime") return fire("regime", { regime });
    return fire(k.kind);
  };
  const pctKinds = ["crash", "rally", "gap", "break_pair"];
  const shocks = run.market_events.filter((e) => e.type === "injected").length;
  return (
    <Panel
      title="Shock the market"
      aside={shocks ? `takes effect on the next bar · ${plural(shocks, "shock")} so far` : "takes effect on the next bar"}
      className="ss-shock"
    >
      <div className="ss-keys" role="group" aria-label="Shocks">
        {kinds
          .filter((k) => k.kind !== "break_pair" || hasPair)
          .map((k) => (
            <button
              key={k.kind}
              type="button"
              className={`ss-key${k.kind === "crash" ? " ss-key-danger" : ""}`}
              disabled={busy}
              onClick={() => void quick(k)}
              title={k.help.replace(/`/g, "")}
            >
              <span className="ss-key-label">{k.label}</span>
              <span className="ss-key-val">
                {k.kind === "regime" ? (REGIME_NAMES[regime] ?? regime) : isNum(k.size) ? (pctKinds.includes(k.kind) ? fmtPct(k.size, 0) : `${k.size}×`) : ""}
                {k.kind === "gap" ? ` ${symbol}` : ""}
              </span>
              <span className="ss-key-note">{isNum(k.bars) && k.bars > 1 ? `for ${k.bars} bars` : "one-off"}</span>
            </button>
          ))}
      </div>
      <div className="ss-shock-opts">
        <label className="field">
          <span>Gap symbol</span>
          <select className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {run.feed.symbols.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Forced regime</span>
          <select className="input" value={regime} onChange={(e) => setRegime(e.target.value)}>
            <option value="bull">Bull</option>
            <option value="bear">Bear</option>
            <option value="range">Range-bound</option>
            <option value="crash">Crash</option>
          </select>
        </label>
        <details className="sim-more ss-custom">
          <summary>Custom size and duration</summary>
          <div className="sim-more-body">
            <label className="field">
              <span>Event</span>
              <select
                className="input"
                value={custom?.kind ?? ""}
                onChange={(e) => {
                  const k = kinds.find((x) => x.kind === e.target.value) ?? null;
                  setCustom(k);
                  setSize(k && isNum(k.size) ? (pctKinds.includes(k.kind) ? Math.round(k.size * 100) : k.size) : "");
                  setBars(k && isNum(k.bars) ? k.bars : "");
                }}
              >
                <option value="">Choose...</option>
                {kinds.map((k) => (
                  <option key={k.kind} value={k.kind}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            {custom ? (
              <>
                <p className="sim-fine">{custom.help.replace(/`/g, "")}</p>
                <div className="sim-fields">
                  {custom.kind !== "regime" ? (
                    <label className="field">
                      <span>{pctKinds.includes(custom.kind) ? "Size" : "Multiplier"}</span>
                      <UnitInput
                        unit={pctKinds.includes(custom.kind) ? "%" : "×"}
                        type="number"
                        value={size}
                        onChange={(e) => setSize(e.target.value === "" ? "" : Number(e.target.value))}
                      />
                    </label>
                  ) : null}
                  {!["gap", "break_pair"].includes(custom.kind) ? (
                    <label className="field">
                      <span>Duration</span>
                      <UnitInput
                        unit="bars"
                        type="number"
                        min={1}
                        max={500}
                        value={bars}
                        onChange={(e) => setBars(e.target.value === "" ? "" : Number(e.target.value))}
                      />
                    </label>
                  ) : null}
                </div>
                <div>
                  <button
                    className={`btn ${custom.kind === "crash" ? "danger" : "primary"}`}
                    disabled={busy}
                    onClick={() => {
                      const body: Record<string, unknown> = {};
                      if (size !== "") body.size = pctKinds.includes(custom.kind) ? Number(size) / 100 : Number(size);
                      if (bars !== "") body.bars = Number(bars);
                      if (custom.kind === "gap") body.symbol = symbol;
                      if (custom.kind === "regime") body.regime = regime;
                      void fire(custom.kind, body);
                    }}
                  >
                    Inject {custom.label.toLowerCase()}
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </details>
      </div>
    </Panel>
  );
}

/** The tag a feed row carries: what kind of event it is, in a word. */
function eventTag(e: ArenaEvent): { text: string; kind: TagKind; title: string } {
  if (e.type === "injected") return { text: "Shock", kind: "crit", title: "An event you injected into the market" };
  if (e.type === "regime") return { text: "Regime", kind: "warn", title: "The hidden market regime changed" };
  if (e.type === "signal") return { text: "Signal", kind: "info", title: "A strategy's view of a symbol changed" };
  const pos = e.position;
  if (isNum(pos) && pos === 0) return { text: "Exit", kind: "info", title: "Closed the position: now flat" };
  if (e.side === "sell" && isNum(pos) && pos < 0) return { text: "Short", kind: "info", title: "Sold short (or added to a short)" };
  if (e.side === "buy" && isNum(pos) && pos < 0) return { text: "Cover", kind: "info", title: "Bought back part of a short" };
  return e.side === "buy"
    ? { text: "Buy", kind: "info", title: "Bought (opened or added to a long)" }
    : { text: "Sell", kind: "info", title: "Sold part of a long" };
}

function EventFeed({ run, looks }: { run: ArenaState; looks: Record<string, Look> }) {
  const [who, setWho] = useState("all");
  const [kinds, setKinds] = useState({ fill: true, signal: true, market: true });
  const names = useMemo(() => Object.fromEntries(run.agents.map((a) => [a.id, a.name])), [run.agents]);
  const items = useMemo(() => {
    const out: ArenaEvent[] = [];
    for (let i = run.events.length - 1; i >= 0 && out.length < 150; i--) {
      const e = run.events[i];
      const isMarket = e.type === "regime" || e.type === "injected";
      if (isMarket ? !kinds.market : !kinds[e.type as "fill" | "signal"]) continue;
      if (who !== "all" && !isMarket && e.agent !== who) continue;
      out.push(e);
    }
    return out;
  }, [run.events, who, kinds]);
  const counts = useMemo(() => {
    const c = { fill: 0, signal: 0, market: 0 };
    for (const e of run.events) {
      const isMarket = e.type === "regime" || e.type === "injected";
      if (who !== "all" && !isMarket && e.agent !== who) continue;
      c[isMarket ? "market" : (e.type as "fill" | "signal")] += 1;
    }
    return c;
  }, [run.events, who]);
  return (
    <Panel title="What the strategies did, and why" aside="newest first" className="ss-feed">
      <div className="ss-feed-filter">
        <select className="input" value={who} onChange={(e) => setWho(e.target.value)} aria-label="Strategy filter">
          <option value="all">All strategies</option>
          {run.agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <span className="chips" role="group" aria-label="Event kinds">
          {(["fill", "signal", "market"] as const).map((k) => (
            <button
              key={k}
              type="button"
              className="chip toggle"
              aria-pressed={kinds[k]}
              onClick={() => setKinds({ ...kinds, [k]: !kinds[k] })}
            >
              {{ fill: "Trades", signal: "Signals", market: "Market events" }[k]}
              <span className="muted">{counts[k]}</span>
            </button>
          ))}
        </span>
      </div>
      <ol className="sim-feed" aria-live="off">
        {items.length === 0 ? (
          <li className="sim-feed-empty">
            Nothing yet. Trades, signal changes, regime changes and your shocks appear here as bars arrive; press <b>Play</b> or{" "}
            <b>Next bar</b>.
          </li>
        ) : null}
        {items.map((e, i) => {
          const tag = eventTag(e);
          const why = e.type === "fill" ? e.reason : e.type === "signal" ? e.headline : "";
          return (
            <li key={`${e.t}-${e.agent}-${e.type}-${e.symbol}-${i}`} className={`sim-feed-row${e.type === "injected" || e.type === "regime" ? " sim-feed-hot" : ""}`}>
              <span className="sim-feed-when">
                <b>
                  <i>Bar</i> {(e.t - run.start_index).toLocaleString()}
                </b>
                {barTime(run, e.time)}
              </span>
              <span className="sim-feed-body">
                <span className="sim-feed-msg">{eventText(e)}</span>
                <span className="sim-feed-who">
                  {e.agent ? (
                    <>
                      <Swatch {...looks[e.agent]} />
                      {names[e.agent] ?? e.agent}
                    </>
                  ) : e.type === "injected" ? (
                    "You"
                  ) : (
                    "Market"
                  )}
                </span>
                {why ? <span className="sim-feed-why">{why}</span> : null}
              </span>
              <Tag kind={tag.kind} title={tag.title}>
                {tag.text}
              </Tag>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

function eventText(e: ArenaEvent) {
  if (e.type === "fill") {
    const verb = e.side === "buy" ? "Bought" : "Sold";
    const pos = isNum(e.position) ? (e.position === 0 ? "now flat" : `now ${e.position > 0 ? "long" : "short"} ${fmtNum(Math.abs(e.position), 0)}`) : "";
    return (
      <>
        <span className={e.side === "buy" ? "upc" : "downc"}>{verb}</span> {fmtNum(e.qty ?? 0, 0)} {e.symbol} @ {fmtPrice(e.price)}{" "}
        <span className="sim-dim">({pos})</span>
      </>
    );
  }
  if (e.type === "signal") {
    return (
      <>
        {e.symbol}: signal {e.from ?? "none"} → <b>{e.to}</b>
      </>
    );
  }
  return <>{e.headline}</>;
}

function AgentPanel({ run, agentId, look, onReload }: { run: ArenaState; agentId: string; look?: Look; onReload: () => void }) {
  const [detail, setDetail] = useState<ArenaAgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cursorRef = useRef(run.cursor);
  cursorRef.current = run.cursor;
  const lastLoaded = useRef(-1);

  useEffect(() => {
    let cancelled = false;
    lastLoaded.current = -1;
    setDetail(null);
    const load = async () => {
      if (cursorRef.current === lastLoaded.current) return;
      try {
        const d = await api.arenaAgent(run.id, agentId);
        if (!cancelled) {
          setDetail(d);
          lastLoaded.current = cursorRef.current;
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
        if ((e as { status?: number }).status === 404) onReload();
      }
    };
    void load();
    const id = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [run.id, agentId, onReload]);

  const live = run.agents.find((a) => a.id === agentId);
  if (!live) return null;
  const rank = ranked(run).findIndex((a) => a.id === agentId) + 1;
  const paramText = Object.entries(live.params ?? {})
    .map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
  const sizing = live.sizing && typeof live.sizing.method === "string" ? String(live.sizing.method).replace(/_/g, " ") : "";
  return (
    <Panel
      title={
        <span className="ss-insp-title">
          <span className="muted">Inspector ·</span>
          <Swatch {...look} />
          {live.name}
        </span>
      }
      aside={`rank ${rank} of ${run.agents.length}`}
      actions={
        <>
          {live.kind === "strategy" ? <EvidenceBadge level={live.evidence} /> : <span className="badge neutral">Benchmark</span>}
          {live.stopped ? <Tag kind="crit">Wiped out</Tag> : null}
        </>
      }
      className="ss-insp"
    >
      {live.error ? <div className="callout bad">{live.error}</div> : null}
      <ReadoutGrid>
        <Readout size="sm" label="Equity" value={fmtMoney(live.equity)} />
        <Readout size="sm" label="Return" {...pctParts(live.return)} tone={toneOf(live.return)} />
        <Readout size="sm" label="Drawdown" {...pctParts(live.drawdown, 1, false)} />
        <Readout
          size="sm"
          label={
            <span className="sim-label-help">
              Exposure <Help text="Gross exposure (long + short) as a share of equity." />
            </span>
          }
          {...pctParts(live.gross, 0, false)}
        />
        <Readout size="sm" label="Closed trades" value={String(live.trades)} />
        <Readout
          size="sm"
          label={
            <span className="sim-label-help">
              Costs + financing <Help text="Slippage and commissions, plus borrow fees on shorts and interest on borrowed cash." />
            </span>
          }
          value={detail ? fmtMoney(detail.costs + detail.financing) : "–"}
        />
      </ReadoutGrid>
      <div className="ss-insp-kv">
        <KeyValues
          columns={2}
          rows={[
            ["Category", live.category],
            ["Symbols", live.symbols.join(" ")],
            ["Cash", fmtMoney(live.cash)],
            ["Unrealised", <span className={pnlClass(live.unrealized)}>{fmtMoney(live.unrealized, 0, true)}</span>],
            ["Holding", positionsText(live)],
            ["Sizing", sizing || "–"],
            ...(paramText ? ([["Parameters", paramText]] as [ReactNode, ReactNode][]) : []),
          ]}
        />
      </div>
      <ErrorBox error={error} />
      <h3 className="section-title ss-insp-sec">Current view per symbol</h3>
      {!detail ? (
        <Spinner />
      ) : (
        <div className="ss-views">
          {live.symbols.map((s) => {
            const ex = detail.explanations[s];
            const w = live.weights[s];
            const state = live.signals[s];
            return (
              <div key={s} className="ss-view">
                <div className="ss-view-head">
                  <span className="ss-view-sym">{s}</span>
                  <VoteGlyph vote={state === "long" ? 1 : state === "short" ? -1 : 0} state={state} />
                  <span className="ss-view-w">
                    weight {fmtPct(w ?? 0, 0)}
                    {live.pending[s] ? ` · order pending ${fmtNum(live.pending[s], 0)} sh` : ""}
                  </span>
                </div>
                {ex ? (
                  <>
                    <div className="ss-view-headline">{ex.headline}</div>
                    {ex.rules.length ? (
                      <ul className="ss-rules">
                        {ex.rules.map((r) => (
                          <li key={r.label}>
                            <span className={`ss-mark${r.passed === true ? " ss-mark-ok" : r.passed === false ? " ss-mark-no" : ""}`} aria-hidden="true">
                              {r.passed === true ? "✓" : r.passed === false ? "✗" : "·"}
                            </span>
                            <span>{r.label}</span>
                            <span className="ss-val">{r.value}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {ex.exit_rule ? <div className="ss-view-exit">{ex.exit_rule}</div> : null}
                  </>
                ) : (
                  <div className="ss-view-exit">No explanation yet.</div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {detail && detail.trades_list.length ? (
        <>
          <h3 className="section-title ss-insp-sec">
            Trades <span className="sim-dim">newest first · {detail.trades_list.length}</span>
          </h3>
          <CompactTrades trades={detail.trades_list} />
        </>
      ) : null}
    </Panel>
  );
}

function CompactTrades({ trades }: { trades: Trade[] }) {
  const rows = [...trades].reverse().slice(0, 40);
  return (
    <div className="table-wrap ss-trades">
      <table className="data">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Opened</th>
            <th>Closed</th>
            <th className="num">P&amp;L</th>
            <th className="num">Return</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id}>
              <td className="mono">{t.symbol}</td>
              <td>{t.direction === "long" ? "Long" : "Short"}</td>
              <td className="nowrap mono">{fmtDate(t.entry_time)}</td>
              <td className="nowrap mono">{t.is_open ? <span className="badge neutral">Open</span> : fmtDate(t.exit_time)}</td>
              <td className={`num ${pnlClass(t.pnl)}`}>{fmtMoney(t.pnl, 0, true)}</td>
              <td className={`num ${pnlClass(t.return_pct)}`}>{fmtPct(t.return_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The debrief once a run is over: the final standings and how each account did in each regime. */
function Results({ run, looks, onNew, onOpen }: {
  run: ArenaState;
  looks: Record<string, Look>;
  onNew: () => void;
  onOpen: (id: string) => void;
}) {
  const s = run.summary!;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rerun = async (sameSeed: boolean) => {
    setBusy(true);
    try {
      const { seed, ...rest } = run.config as Record<string, unknown> & { seed?: number };
      const cfg: Record<string, unknown> = { ...rest };
      if (sameSeed) cfg.seed = seed;
      onOpen((await api.arenaCreate(cfg)).id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const names = Object.fromEntries(run.agents.map((a) => [a.id, a.name]));
  const winner = s.leaderboard[0];
  const bench = s.leaderboard.find((r) => r.kind === "benchmark");
  const strategies = s.leaderboard.filter((r) => r.kind === "strategy");
  const beat = bench && isNum(bench.total_return) ? strategies.filter((r) => isNum(r.total_return) && r.total_return > (bench.total_return as number)).length : null;
  const segs = regimeSegments(run);
  const bandTotal = segs.reduce((n, x) => n + x.n, 0);
  const shockMarks: TimelineMark[] = run.market_events
    .filter((e) => e.type === "injected")
    .map((e) => ({ at: e.t - run.start_index, kind: "shock", title: `Bar ${e.t - run.start_index}: ${e.headline ?? labelFor(e.kind)}` }));
  return (
    <>
      <section className="card marks ss-debrief">
        <div className="ss-debrief-top">
          <div>
            <div className="sim-kicker">Debrief · results after {plural(s.bars, "bar")}</div>
            {winner ? (
              <p className="ss-debrief-title">
                <Swatch {...looks[winner.id]} />
                {winner.name} finished first with <span className={`num ${pnlClass(winner.total_return)}`}>{fmtPct(winner.total_return)}</span>.
              </p>
            ) : null}
            <p className="ss-debrief-sub">
              {bench ? `Buy-and-hold made ${fmtPct(bench.total_return)}; ` : ""}
              {beat !== null ? `${beat} of ${strategies.length} strategies beat it. ` : ""}
              All returns are net of costs.
            </p>
          </div>
          <div className="row tight ss-debrief-actions">
            {run.feed.source === "simulated" ? (
              <>
                <button className="btn primary" disabled={busy} onClick={() => void rerun(false)}>
                  <TransportIcon kind="play" />
                  Run again on a new market
                </button>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void rerun(true)}
                  title="Same seed: the same prices, so you can compare parameter changes fairly."
                >
                  Same market again
                </button>
              </>
            ) : null}
            <button className="btn ghost" onClick={onNew}>
              New simulation
            </button>
          </div>
        </div>
        <ErrorBox error={error} />
      </section>
      <Figure fig="FIG. 1" title="Final standings" aside={`${plural(s.bars, "bar")} · net of costs · sorted by total return`} className="ss-final">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Strategy</th>
                <th className="num">Return</th>
                <th className="num">
                  <span className="sim-label-help">
                    CAGR <Help text="Compound annual growth rate: the yearly return that would compound to this result." />
                  </span>
                </th>
                <th className="num">
                  <span className="sim-label-help">
                    Sharpe <Help text="Return per unit of volatility, per year. Above 1 is good; over a short run it is mostly luck." />
                  </span>
                </th>
                <th className="num">
                  <span className="sim-label-help">
                    Prob. Sharpe &gt; 0{" "}
                    <Help text="Probabilistic Sharpe Ratio: how likely the true Sharpe ratio is positive given this sample's length and fat tails." />
                  </span>
                </th>
                <th className="num">
                  <span className="sim-label-help">
                    Max DD <Help text="Maximum drawdown: the deepest fall from a peak in the account's value." />
                  </span>
                </th>
                <th className="num">Trades</th>
                <th className="num hide-sm">Win rate</th>
                <th className="num hide-sm">Time in market</th>
                <th className="num hide-sm">Costs</th>
              </tr>
            </thead>
            <tbody>
              {s.leaderboard.map((r, i) => (
                <tr key={r.id}>
                  <td className="num ss-rank">{String(i + 1).padStart(2, "0")}</td>
                  <td>
                    <span className="ss-lb-name">
                      <Swatch {...looks[r.id]} />
                      {r.name}
                      {r.kind === "benchmark" ? <Tag>Bench</Tag> : null}
                    </span>
                  </td>
                  <td className={`num ${pnlClass(r.total_return)}`}>{fmtPct(r.total_return)}</td>
                  <td className={`num ${pnlClass(r.cagr)}`}>{fmtPct(r.cagr)}</td>
                  <td className="num">{fmtNum(r.sharpe)}</td>
                  <td className="num">{fmtPct(r.psr, 0, false)}</td>
                  <td className="num">{fmtPct(r.max_drawdown, 1, false)}</td>
                  <td className="num">{fmtNum(r.trades, 0)}</td>
                  <td className="num hide-sm">{fmtPct(r.win_rate, 0, false)}</td>
                  <td className="num hide-sm">{fmtPct(r.exposure, 0, false)}</td>
                  <td className="num hide-sm">{fmtMoney(r.total_costs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Figure>
      {s.regimes && s.regimes.length ? (
        <Figure
          fig="FIG. 2"
          title={
            <span className="sim-label-help">
              Return in each hidden regime
              <Help text="Each account's compounded return over the bars spent in each regime. Trend followers tend to earn in long trends and bleed in chop; mean-reversion is the opposite." />
            </span>
          }
          aside={`${plural(s.regimes.length, "regime")} over ${plural(s.bars, "bar")}`}
          className="ss-regimes"
        >
          {bandTotal ? (
            <Timeline
              band
              total={bandTotal}
              done={bandTotal}
              segments={segs.map((x) => ({ label: x.label === "?" ? "" : x.label, n: x.n, tone: x.label === "?" ? "unknown" : x.tone }))}
              marks={shockMarks}
              label="Hidden regimes over the run"
              valueText={segs.map((x) => `${x.label} for ${x.n} bars`).join(", ")}
            />
          ) : null}
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Strategy</th>
                  {s.regimes.map((g) => (
                    <th key={g.regime} className="num">
                      <RegimeBadge regime={g.regime} drift={g.drift ?? undefined} /> <span className="sim-dim">{g.bars} bars</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.leaderboard.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <span className="ss-lb-name">
                        <Swatch {...looks[r.id]} />
                        {names[r.id] ?? r.name}
                      </span>
                    </td>
                    {s.regimes!.map((g) => (
                      <td key={g.regime} className={`num ${pnlClass(g.returns[r.id])}`}>
                        {fmtPct(g.returns[r.id])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Figure>
      ) : null}
      {s.cautions.length ? <Warnings items={s.cautions} title="Before you draw conclusions" /> : null}
    </>
  );
}
