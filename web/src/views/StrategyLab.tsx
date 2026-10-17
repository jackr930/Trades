/**
 * The Strategy Lab (#/lab): would this have worked after costs, and how fragile is it?
 *
 * Laid out like an instrument bench (docs/DESIGN.md). On the left, the apparatus: the experiment
 * (a backtest or an optimisation), the strategy, the market data, its parameters, the optimiser's
 * search, position sizing and costs, with one RUN button that stays in view. On the right, the
 * readouts and the numbered figures, then the tables. Every run goes through the API
 * (/api/backtest, /api/optimize), which uses the engine and its cost model.
 */
import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { api, type BacktestRequest, type OptimizeRequest } from "../api";
import { useApp, useStatusLine, type Route } from "../state";
import type {
  BacktestResult,
  CostSensitivity,
  CostSensitivityRow,
  GridResult,
  Meta,
  Metrics,
  Num,
  OptimizeResult,
  OptimizeRow,
  ParamSpec,
  Settings,
  Sizing,
  StrategyMeta,
  WalkForwardResult,
  WalkWindow,
} from "../types";
import PriceChart, { type PriceMarker } from "../components/PriceChart";
import LineChart from "../components/LineChart";
import Heatmap from "../components/Heatmap";
import { Figure, Hatch, Panel, Readout, Tag, type Tone } from "../components/instrument";
import {
  ErrorBox,
  EvidenceBadge,
  Help,
  MetricsTable,
  ParamForm,
  SizingForm,
  Spinner,
  SymbolInput,
  TradesTable,
  Warnings,
} from "../components/ui";
import { fmtDate, fmtInt, fmtMoney, fmtNum, fmtPct, isNum, pnlClass } from "../format";
import { useChartColors } from "../theme";
import "../styles/lab.css";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const REAL_SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"];
// Stock-level methods want stocks, not a mix of index and bond funds.
const REAL_BANKS = ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC"];
const REAL_LARGE_CAPS = ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "XOM", "JNJ", "PG", "KO", "WMT"];
const SIM_STOCKS = ["SIMTEC", "SIMBNK", "SIMNRG", "SIMUTL", "SIMHLC"];
const STOCK_UNIVERSE = new Set(["stat_arb", "residual_momentum", "ml_ranker"]);

type Experiment = "backtest" | "optimize";

/** One press of RUN: the request behind the results on screen, its number in this session, its time. */
interface RunInfo {
  key: string;
  n: number;
  ms: number;
}

/** The apparatus sticks under the 44px top bar, 12px below it. */
const STICKY_TOP = 56;
/** Below this width the apparatus stacks above the results (matches the shared .split breakpoint). */
const STACKED = "(max-width: 1100px)";

const KIND_LABEL: Record<StrategyMeta["kind"], string> = {
  single: "Single symbol",
  pair: "Pair of symbols",
  cross_sectional: "Ranks a universe",
};
const FAMILY_LABEL: Record<StrategyMeta["family"], string> = {
  classic: "Classic rule",
  modern: "Modern method",
  intraday: "Intraday",
};
/** What a bar is, in words, for the Bars control. */
const BARS_NOTE: Record<string, string> = {
  "1d": "One bar per trading day.",
  "1h": "Hourly bars: 7 in each session.",
  "30m": "30-minute bars: 13 in each session.",
  "15m": "15-minute bars: 26 in each session.",
  "5m": "5-minute bars: 78 in each session.",
  "1m": "1-minute bars: 390 in each session.",
};
const BAR_WORD: Record<string, string> = {
  "1d": "daily",
  "1h": "hourly",
  "30m": "30-minute",
  "15m": "15-minute",
  "5m": "5-minute",
  "1m": "1-minute",
};

function defaultSymbols(s: StrategyMeta, provider: string, settings: Settings): string[] {
  const synthetic = provider === "synthetic";
  if (s.id === "consensus") {
    // The Live Desk's own watchlist: the consensus is what the Live Desk suggests for it.
    const wl = settings.watchlists[provider] ?? [];
    return wl.length ? wl : synthetic ? [...SIM_STOCKS, "SIMIDX", "SIMPRA", "SIMPRB"] : ["SPY", "QQQ", "TLT", "GLD"];
  }
  if (s.kind === "pair") return synthetic ? ["SIMPRA", "SIMPRB"] : ["KO", "PEP"];
  if (s.id === "dual_momentum") return synthetic ? ["SIMIDX", "SIMGLD", "SIMBND"] : ["SPY", "EFA", "AGG"];
  if (s.id === "cta_trend") return synthetic ? ["SIMIDX", "SIMBND", "SIMGLD"] : ["SPY", "TLT", "GLD"]; // diversify across markets
  if (s.id === "stat_arb") return synthetic ? SIM_STOCKS : REAL_BANKS; // one sector: stocks that share a factor
  if (s.kind === "cross_sectional") {
    const wl = (settings.watchlists[provider] ?? []).filter((x) => !["SIMPRA", "SIMPRB"].includes(x));
    if (STOCK_UNIVERSE.has(s.id)) return synthetic ? [...SIM_STOCKS, "SIMIDX", "SIMGLD", "SIMBND"] : REAL_LARGE_CAPS;
    return wl.length >= Math.max(s.min_symbols, 3) ? wl : synthetic ? [...SIM_STOCKS, "SIMIDX", "SIMGLD", "SIMBND"] : REAL_SECTORS;
  }
  return synthetic ? ["SIMIDX"] : ["SPY"];
}

function defaultParams(s: StrategyMeta, provider: string, settings: Settings): Record<string, unknown> {
  const p: Record<string, unknown> = {};
  s.params.forEach((x) => (p[x.name] = x.default));
  if (s.id === "dual_momentum") p.safe_symbol = provider === "synthetic" ? "SIMBND" : "AGG";
  if (s.id === "consensus") {
    // Reproduce the Live Desk: its strategies, pairs, weighting and risk settings.
    Object.assign(p, {
      members: settings.advisors.map((a) => ({ id: a.id, params: a.params ?? {} })),
      pairs: settings.pairs,
      weighting: settings.consensus_weighting,
      allow_short: settings.allow_short,
      risk_per_trade: settings.risk_per_trade,
      stop_atr: settings.stop_atr,
      max_position_pct: settings.max_position_pct,
      max_gross: settings.max_gross_exposure,
    });
  }
  return p;
}

function baseSizing(s: StrategyMeta): Partial<Sizing> {
  return {
    method: "fixed",
    allocation: 1,
    target_vol: 0.15,
    vol_com: 30,
    risk_per_trade: 0.01,
    stop_atr: 2,
    atr_length: 20,
    max_leverage: 1,
    rebalance_every: 0,
    ...(s.default_sizing as Partial<Sizing>),
  };
}

/** Intraday rules decide within the session: they need intraday bars (5 minutes when offered). */
function barsFor(s: StrategyMeta, offered: string[], current: string): string {
  if (s.family !== "intraday" || current !== "1d") return current;
  return offered.includes("5m") ? "5m" : offered.find((t) => t !== "1d") ?? "5m";
}

/** How many parameters differ from the published defaults (or the Live Desk's settings). */
function changedCount(spec: ParamSpec[], values: Record<string, unknown>, defaults: Record<string, unknown>): number {
  return spec.filter((p) => JSON.stringify(values[p.name] ?? p.default) !== JSON.stringify(defaults[p.name] ?? p.default)).length;
}

function sizingSummary(s: StrategyMeta, z: Partial<Sizing>): string {
  if (s.sizes_itself) return "set by the strategy";
  if (z.method === "vol_target") return `vol target ${fmtNum((z.target_vol ?? 0) * 100, 0)}%`;
  if (z.method === "atr_risk") return `ATR risk ${fmtNum((z.risk_per_trade ?? 0) * 100, 1)}%`;
  return `fixed ${fmtNum(z.allocation ?? 1, 1)}×`;
}

function symbolList(symbols: string[], max = 3): string {
  if (!symbols.length) return "no symbols";
  return symbols.length <= max ? symbols.join(", ") : `${symbols.slice(0, max).join(", ")} +${symbols.length - max}`;
}

/** Symbols in a sentence: the list when short, a count when long. */
function symbolText(symbols: string[]): string {
  if (!symbols.length) return "no symbols";
  return symbols.length > 3 ? `${symbols.length} symbols` : symbols.join(", ");
}

function bps(x: unknown): string {
  const v = Number(x);
  return Number.isFinite(v) ? fmtNum(v, Number.isInteger(v) ? 0 : 1) : "–";
}

/** The length of a test, in years when it is one or more. */
function spanText(start: number, end: number): string {
  const days = (end - start) / 86400;
  return days >= 365 ? `${(days / 365.25).toFixed(1)} yr` : `${Math.max(1, Math.round(days))} days`;
}

/** A readout's number and its unit, e.g. "+369.5" and "%". */
function pctParts(x: Num | undefined, plus = false): [string, string] {
  const t = fmtPct(x ?? null, 1, plus);
  return t.endsWith("%") ? [t.slice(0, -1), "%"] : [t, ""];
}

function toneOf(x: Num | undefined): Tone | undefined {
  if (!isNum(x) || x === 0) return undefined;
  return x > 0 ? "pos" : "neg";
}

const cols = (n: number) => ({ "--cols": n }) as CSSProperties;

function runLabel(run: RunInfo): string {
  return `Run ${String(run.n).padStart(2, "0")} · ${(run.ms / 1000).toFixed(1)} s`;
}

function backtestSentence(res: BacktestResult): string {
  const m = res.metrics;
  const b = res.benchmark?.metrics;
  const y0 = new Date(res.start_time * 1000).getUTCFullYear();
  const y1 = new Date(res.end_time * 1000).getUTCFullYear();
  const when = y1 - y0 >= 1 ? `${y0}–${y1}` : `${fmtDate(res.start_time)} to ${fmtDate(res.end_time)}`;
  const bench = b ? ` (buy & hold ${fmtPct(b.cagr)}, ${fmtNum(b.sharpe)})` : "";
  return `${res.strategy.name} on ${symbolText(res.symbols)}, ${when}: CAGR ${fmtPct(m.cagr)}, Sharpe ${fmtNum(m.sharpe)} after costs${bench}. Next: stress it in the optimiser.`;
}

export default function StrategyLab({ route }: { route: Route }) {
  const { meta, settings, navigate } = useApp() as ReturnType<typeof useApp> & { meta: Meta; settings: Settings };
  const byId = useMemo(() => new Map(meta.strategies.map((s) => [s.id, s])), [meta]);
  const offeredFor = (p: string) => meta.providers.find((x) => x.id === p)?.timeframes ?? [];
  const initial = byId.get(route.params.get("strategy") ?? "") ?? byId.get("tsmom") ?? meta.strategies[0];
  const [strategyId, setStrategyId] = useState(initial.id);
  const strategy = byId.get(strategyId) ?? initial;
  const [provider, setProvider] = useState(settings.provider);
  const [symbols, setSymbols] = useState<string[]>(() => defaultSymbols(initial, settings.provider, settings));
  // A strategy opened by link (#/lab?strategy=orb) starts on bars it can use: intraday rules need intraday bars.
  const [timeframe, setTimeframe] = useState(() => barsFor(initial, offeredFor(settings.provider), "1d"));
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [params, setParams] = useState<Record<string, unknown>>(() => defaultParams(initial, settings.provider, settings));
  const [sizing, setSizing] = useState<Partial<Sizing>>(() => baseSizing(initial));
  const [config, setConfig] = useState({
    initial_cash: settings.account_equity, // your account profile
    fractional: settings.fractional_shares,
    commission_bps: settings.commission_bps,
    slippage_bps: settings.slippage_bps,
    allow_short: settings.allow_short || initial.uses_short,
    execution: "next_open",
  });
  const [experiment, setExperiment] = useState<Experiment>("backtest");

  // The backtest and what it returned.
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [ran, setRan] = useState<RunInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The optimiser's search and what it returned (kept while you switch between experiments).
  const [ranges, setRanges] = useState<Record<string, RangeSpec>>(() => initialRanges(initial));
  const [objective, setObjective] = useState("sharpe");
  const [optMode, setOptMode] = useState<"grid" | "walk_forward">("grid");
  const [trainBars, setTrainBars] = useState(756);
  const [testBars, setTestBars] = useState(252);
  const [anchored, setAnchored] = useState(false);
  const [optResult, setOptResult] = useState<OptimizeResult | null>(null);
  const [optRan, setOptRan] = useState<RunInfo | null>(null);
  const [optLoading, setOptLoading] = useState(false);
  const [optError, setOptError] = useState<string | null>(null);

  const runSeq = useRef(0);
  const showSearch = useRef(false);
  const appRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  // Every section starts open on a wide screen; on a phone the long ones start folded to their summaries.
  const [wide] = useState(() => !window.matchMedia(STACKED).matches);
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);

  const chooseStrategy = (id: string) => {
    const s = byId.get(id);
    if (!s) return;
    setStrategyId(id);
    setTimeframe((tf) => barsFor(s, offeredFor(provider), tf));
    setParams(defaultParams(s, provider, settings));
    setSizing(baseSizing(s));
    setSymbols(defaultSymbols(s, provider, settings));
    setConfig((c) => ({ ...c, allow_short: settings.allow_short || s.uses_short }));
    setResult(null);
    // The optimiser starts again from this strategy's own parameters.
    setRanges(initialRanges(s));
    setOptResult(null);
  };

  useEffect(() => {
    const id = route.params.get("strategy");
    if (id && id !== strategyId && byId.has(id)) chooseStrategy(id);
  }, [route]);

  const changeProvider = (p: string) => {
    setProvider(p);
    setSymbols(defaultSymbols(strategy, p, settings));
    setParams(defaultParams(strategy, p, settings));
    // Keep the bar size when the new source offers it; otherwise take one it has.
    const offered = offeredFor(p);
    if (offered.length && !offered.includes(timeframe)) setTimeframe(barsFor(strategy, offered, offered.includes("1d") ? "1d" : offered[0]));
  };

  const request = (p: Record<string, unknown> = params): BacktestRequest => ({
    strategy: { id: strategy.id, params: p, sizing },
    symbols,
    timeframe,
    start: start || null,
    end: end || null,
    provider,
    config,
  });

  const numeric = numericParams(strategy);
  const active = Object.entries(ranges).filter(([, r]) => r.on);
  const combos = active.reduce((acc, [, r]) => acc * Math.max(countValues(r), 1), 1);
  const limit = optMode === "grid" ? 400 : 200;
  const optRequest = (): OptimizeRequest => {
    const grid: OptimizeRequest["grid"] = {};
    active.forEach(([name, r]) => (grid[name] = { min: r.min, max: r.max, step: r.step }));
    return { ...request(), grid, objective, mode: optMode, train_bars: trainBars, test_bars: testBars, anchored };
  };

  // On a phone the results sit below the apparatus: bring them into view when a run starts.
  const reveal = () => {
    if (!window.matchMedia(STACKED).matches) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" }));
  };

  const runBacktest = async (p?: Record<string, unknown>) => {
    const req = request(p);
    const n = ++runSeq.current;
    const t0 = performance.now();
    setExperiment("backtest");
    setLoading(true);
    setError(null);
    reveal();
    try {
      const res = await api.backtest(req);
      setResult(res);
      setRan({ key: JSON.stringify(req), n, ms: performance.now() - t0 });
      reveal();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const runOptimiser = async () => {
    const req = optRequest();
    const n = ++runSeq.current;
    const t0 = performance.now();
    setOptLoading(true);
    setOptError(null);
    reveal();
    try {
      const res = await api.optimize(req);
      setOptResult(res);
      setOptRan({ key: JSON.stringify(req), n, ms: performance.now() - t0 });
      reveal();
    } catch (e) {
      setOptError((e as Error).message);
    } finally {
      setOptLoading(false);
    }
  };

  const canBacktest = !loading && symbols.length > 0;
  const canOptimise = !optLoading && active.length > 0 && combos <= limit;
  const busy = experiment === "backtest" ? loading : optLoading;
  const runCurrent = () => {
    if (experiment === "backtest") {
      if (canBacktest) void runBacktest();
    } else if (canOptimise) void runOptimiser();
  };
  // Ctrl+Enter (Cmd+Enter on a Mac) runs from anywhere in the apparatus.
  const onKeys = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.defaultPrevented) {
      e.preventDefault();
      runCurrent();
    }
  };

  // Switching to the optimiser brings its search controls into view.
  const chooseExperiment = (x: Experiment) => {
    setExperiment(x);
    showSearch.current = x === "optimize";
  };
  useEffect(() => {
    if (experiment !== "optimize" || !showSearch.current) return;
    showSearch.current = false;
    const sec = document.getElementById("lab-search");
    const body = sec?.closest<HTMLElement>(".lab-app-body");
    if (!sec || !body) return;
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    if (window.matchMedia(STACKED).matches) sec.scrollIntoView({ behavior, block: "start" });
    else body.scrollTo({ top: sec.offsetTop - body.offsetTop - 4, behavior });
  }, [experiment]);
  // A different experiment shows different results: start reading them from the top.
  useEffect(() => {
    const el = resultsRef.current;
    if (!el || window.matchMedia(STACKED).matches) return;
    if (el.getBoundingClientRect().top < STICKY_TOP) window.scrollTo({ top: 0 });
  }, [experiment]);

  const counterpart = byId.get(strategy.counterpart);

  // Replay the same test bar by bar in the strategy simulator, where it trades live next to others
  // (optionally next to the classic rule a modern method refines).
  const replay = async (compare: boolean) => {
    const req = request();
    const s = await api.arenaCreate({
      source: "replay",
      provider: req.provider,
      symbols: req.symbols,
      timeframe: req.timeframe,
      start: req.start ?? (result ? fmtDate(result.start_time) : undefined),
      end: req.end ?? undefined,
      strategies: [req.strategy, ...(compare && counterpart ? [{ id: counterpart.id }] : [])],
      initial_cash: config.initial_cash,
      fractional: config.fractional,
      slippage_bps: config.slippage_bps,
      commission_bps: config.commission_bps,
      allow_short: config.allow_short,
      speed: 16,
    });
    navigate("/sim", { run: s.id });
  };

  const grouped = useMemo(() => {
    // Modern quant methods first, in their own group; classic rules grouped by category.
    const m = new Map<string, StrategyMeta[]>([["Modern quant methods", []]]);
    meta.strategies.forEach((s) => {
      const key = s.family === "modern" ? "Modern quant methods" : s.category;
      m.set(key, [...(m.get(key) ?? []), s]);
    });
    return [...m.entries()].filter(([, list]) => list.length);
  }, [meta]);

  // Keep the whole apparatus, RUN included, inside the window while the page scrolls under it.
  useLayoutEffect(() => {
    const el = appRef.current;
    if (!el) return;
    let frame = 0;
    const fit = () => {
      frame = 0;
      const top = Math.max(el.getBoundingClientRect().top, STICKY_TOP);
      el.style.setProperty("--lab-app-max", `${Math.max(360, Math.floor(window.innerHeight - top - 16))}px`);
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(fit);
    };
    fit();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  const provInfo = meta.providers.find((p) => p.id === provider);
  const symbolHint = strategy.sizes_itself
    ? "The watchlist the Live Desk would watch (your Live Desk watchlist by default)."
    : strategy.kind === "pair"
      ? "Exactly two symbols: A then B."
      : strategy.kind === "cross_sectional"
        ? `A universe to rank (${strategy.min_symbols}+ symbols; more is better).`
        : "One or more symbols (capital is split equally).";
  const intraday = strategy.family === "intraday";
  const barChoices = [...meta.timeframes].reverse().filter((t) => provInfo?.timeframes.includes(t.value) ?? true);
  const changed = changedCount(strategy.params, params, defaultParams(strategy, provider, settings));
  const objLabel = OBJECTIVE_LABELS[objective] ?? objective;

  // Do the results on screen still match the apparatus?
  const btStale = Boolean(result && ran && ran.key !== JSON.stringify(request()));
  const optStale = Boolean(optResult && optRan && optRan.key !== JSON.stringify(optRequest()));

  // The status line: what is true on this screen now, and what to do next.
  let status: string;
  if (experiment === "backtest") {
    if (loading) status = `Running ${strategy.name} on ${symbolText(symbols)}, net of costs…`;
    else if (error) status = "The backtest stopped with an error, shown below. Change the set-up and press **Run backtest** again.";
    else if (result && btStale)
      status = `The set-up changed after run ${ran?.n ?? ""}: the results below are out of date. Press **Run backtest** to update them.`;
    else if (result) status = backtestSentence(result);
    else if (!symbols.length) status = "Add at least one symbol in the apparatus, then press **Run backtest**.";
    else status = `Nothing has run yet. ${strategy.name} on ${symbolText(symbols)} is set up: press **Run backtest** to test it after costs.`;
  } else if (!numeric.length) {
    status = `${strategy.name} has no numeric parameters to optimise. Pick another strategy, or switch back to a backtest.`;
  } else if (optLoading) {
    status = `Evaluating ${combos} parameter combination${combos === 1 ? "" : "s"}${optMode === "walk_forward" ? " in every walk-forward window" : ""}…`;
  } else if (optError) {
    status = "The optimiser stopped with an error, shown below. Change the search and press **Run optimiser** again.";
  } else if (optResult && optStale) {
    status = `The set-up changed after run ${optRan?.n ?? ""}: the results below are out of date. Press **Run optimiser** to update them.`;
  } else if (optResult) {
    status = optimiseSentence(optResult);
  } else if (!active.length) {
    status = "Tick at least one parameter to vary in the apparatus, then press **Run optimiser**.";
  } else if (combos > limit) {
    status = `${combos} combinations is more than the ${limit} allowed: narrow a range or raise its step.`;
  } else {
    status = `Ready to try ${combos} combination${combos === 1 ? "" : "s"} of ${active.map(([k]) => k).join(" and ")}. Press **Run optimiser**, then judge the winner by its neighbours, not by itself.`;
  }
  useStatusLine(status);

  const period = start || end ? `${start || "first bar"} → ${end || "last bar"}` : "all history";
  const n = (i: number) => String(i).padStart(2, "0");
  const sizingN = experiment === "optimize" ? 6 : 5;

  return (
    <div className="lab">
      <header className="lab-head">
        <h1>Strategy Lab</h1>
        <p>Would this have worked after costs, and how fragile is it? Set up the apparatus, run it, then stress it.</p>
      </header>

      <div className="lab-app-wrap" ref={appRef} onKeyDown={onKeys}>
        <Panel className="lab-app" title="Apparatus" aside={experiment === "backtest" ? "set up, then run" : "set up the search"}>
          <div className="lab-app-body">
            <div className="lab-sec lab-sec-fixed">
              <div className="lab-sec-head">
                <span className="lab-sec-n">01</span>
                <span className="lab-sec-title">Experiment</span>
              </div>
              <div className="lab-sec-body">
                <div className="segmented lab-seg lab-exp" role="group" aria-label="Experiment">
                  <button type="button" aria-pressed={experiment === "backtest"} onClick={() => chooseExperiment("backtest")}>
                    Backtest
                  </button>
                  <button type="button" aria-pressed={experiment === "optimize"} onClick={() => chooseExperiment("optimize")}>
                    Optimise &amp; validate
                  </button>
                </div>
                <p className="lab-hint">
                  {experiment === "backtest"
                    ? "One run of the rules over history, after every cost, beside buying and holding."
                    : "Many runs across parameter ranges, scored for luck: the Deflated Sharpe Ratio, or walk-forward on unseen data."}
                </p>
              </div>
            </div>

            <Section n="02" title="Strategy" summary={strategy.category}>
              <select className="input" aria-label="Strategy" value={strategy.id} onChange={(e) => chooseStrategy(e.target.value)}>
                {grouped.map(([cat, list]) => (
                  <optgroup key={cat} label={cat}>
                    {list.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <div className="lab-strat-meta">
                <EvidenceBadge level={strategy.evidence} />
                <span className="lab-kind">
                  {FAMILY_LABEL[strategy.family]} · {KIND_LABEL[strategy.kind]}
                </span>
              </div>
              <p className="lab-strat-sum">{strategy.summary}</p>
              {strategy.needs ? (
                <p className="lab-note">
                  <b>Works best with:</b> {strategy.needs}
                </p>
              ) : null}
              {counterpart ? (
                <p className="lab-note">
                  A modern refinement of{" "}
                  <button type="button" className="linklike" onClick={() => chooseStrategy(counterpart.id)}>
                    {counterpart.name}
                  </button>
                  . Backtest both on the same symbols, or race them bar by bar from the results.
                </p>
              ) : null}
              <a className="lab-more" href={`#/library?strategy=${strategy.id}`}>
                Rules, research and caveats
              </a>
            </Section>

            <Section n="03" title="Market data" summary={`${symbolList(symbols, 2)} · ${timeframe}`}>
              <label className="field">
                <span>Data</span>
                <select className="input" value={provider} onChange={(e) => changeProvider(e.target.value)}>
                  {meta.providers.map((p) => (
                    <option key={p.id} value={p.id} disabled={!p.configured}>
                      {p.label}
                      {p.configured ? "" : " (needs setup)"}
                    </option>
                  ))}
                </select>
              </label>
              <div className="field">
                <span id="lab-bars">Bars</span>
                <div className="segmented lab-seg" role="group" aria-labelledby="lab-bars">
                  {barChoices.map((t) => {
                    const off = intraday && t.value === "1d";
                    return (
                      <button
                        key={t.value}
                        type="button"
                        aria-pressed={timeframe === t.value}
                        disabled={off}
                        title={off ? `${t.label} (intraday rules need intraday bars)` : t.label}
                        onClick={() => setTimeframe(t.value)}
                      >
                        {t.value}
                      </button>
                    );
                  })}
                </div>
                <span className="hint">
                  {BARS_NOTE[timeframe] ?? timeframe}
                  {intraday ? " Intraday rules need intraday bars, so Daily is off." : ""}
                </span>
              </div>
              <div className="lab-pair">
                <label className="field">
                  <span>Start</span>
                  <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                </label>
                <label className="field">
                  <span>End</span>
                  <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
                </label>
              </div>
              <div className="field">
                <span>Symbols</span>
                <SymbolInput symbols={symbols} onChange={setSymbols} max={strategy.max_symbols ?? 30} />
                <span className="hint">
                  {symbolHint}
                  {start ? "" : " Leave dates blank to use all available history."} Indicator warm-up history before the start date
                  is loaded automatically.
                </span>
              </div>
            </Section>

            <Section n="04" title="Parameters" summary={strategy.params.length ? (changed ? `${changed} changed` : "defaults") : "none"} open={wide}>
              <div className="lab-form">
                <ParamForm spec={strategy.params} values={params} onChange={setParams} />
              </div>
              <button type="button" className="btn small ghost lab-reset" onClick={() => setParams(defaultParams(strategy, provider, settings))}>
                {strategy.sizes_itself ? "Reset to my Live Desk settings" : "Reset to published defaults"}
              </button>
            </Section>

            {experiment === "optimize" ? (
              <Section id="lab-search" n="05" title="Search" summary={numeric.length ? `${combos} combination${combos === 1 ? "" : "s"}` : "nothing to vary"}>
                <SearchControls
                  numeric={numeric}
                  ranges={ranges}
                  setRanges={setRanges}
                  params={params}
                  objective={objective}
                  setObjective={setObjective}
                  mode={optMode}
                  setMode={setOptMode}
                  trainBars={trainBars}
                  setTrainBars={setTrainBars}
                  testBars={testBars}
                  setTestBars={setTestBars}
                  anchored={anchored}
                  setAnchored={setAnchored}
                  timeframe={timeframe}
                />
              </Section>
            ) : null}

            <Section n={n(sizingN)} title="Position sizing" summary={sizingSummary(strategy, sizing)} open={wide}>
              {strategy.sizes_itself ? (
                <p className="lab-note">
                  This strategy sizes its own positions exactly as the Live Desk suggests them: risk per trade, stop distance and
                  position cap scaled by conviction, then the portfolio cap across all symbols. Change those in the parameters
                  above.
                </p>
              ) : (
                <div className="lab-form">
                  <SizingForm value={sizing} onChange={setSizing} />
                </div>
              )}
            </Section>

            <Section
              n={n(sizingN + 1)}
              title="Costs & execution"
              summary={`${bps(config.slippage_bps)} + ${bps(config.commission_bps)} bps`}
              open={wide}
            >
              <div className="lab-form">
                <label className="field lab-f">
                  <span>Starting capital ($)</span>
                  <input
                    className="input num"
                    type="number"
                    value={config.initial_cash}
                    onChange={(e) => setConfig({ ...config, initial_cash: Number(e.target.value) })}
                  />
                </label>
                <label className="field lab-f" title="Adverse price impact on every fill (half the bid-ask spread plus market impact).">
                  <span>Slippage (bps)</span>
                  <input
                    className="input num"
                    type="number"
                    step={0.5}
                    value={config.slippage_bps}
                    onChange={(e) => setConfig({ ...config, slippage_bps: Number(e.target.value) })}
                  />
                  <span className="hint">Price lost on every fill: half the bid-ask spread plus market impact. 1 bp = 0.01%.</span>
                </label>
                <label className="field lab-f">
                  <span>Commission (bps)</span>
                  <input
                    className="input num"
                    type="number"
                    step={0.5}
                    value={config.commission_bps}
                    onChange={(e) => setConfig({ ...config, commission_bps: Number(e.target.value) })}
                  />
                  <span className="hint">The broker's fee, as a share of each trade's value.</span>
                </label>
                <div className="field">
                  <span id="lab-fill">Fill at</span>
                  <div className="segmented lab-seg" role="group" aria-labelledby="lab-fill">
                    <button
                      type="button"
                      aria-pressed={config.execution === "next_open"}
                      onClick={() => setConfig({ ...config, execution: "next_open" })}
                    >
                      Next bar's open
                    </button>
                    <button
                      type="button"
                      aria-pressed={config.execution === "next_close"}
                      onClick={() => setConfig({ ...config, execution: "next_close" })}
                    >
                      Next bar's close
                    </button>
                  </div>
                </div>
                <label className="check">
                  <input type="checkbox" checked={config.fractional} onChange={(e) => setConfig({ ...config, fractional: e.target.checked })} />
                  Fractional shares
                </label>
                <label className="check" title={strategy.sizes_itself ? "Set by the strategy's own short-selling parameter" : undefined}>
                  <input
                    type="checkbox"
                    checked={strategy.sizes_itself ? Boolean(params.allow_short) : config.allow_short}
                    disabled={strategy.sizes_itself}
                    onChange={(e) => setConfig({ ...config, allow_short: e.target.checked })}
                  />
                  Allow short selling
                </label>
              </div>
              <p className="lab-hint">
                Signals are computed at a bar's close and filled on the next bar, so a strategy can never trade on information it
                would not have had (no look-ahead bias).
              </p>
            </Section>
          </div>

          <div className="lab-app-foot">
            <div className="lab-foot-line">
              <span className="lab-foot-sum">
                {experiment === "backtest" ? (
                  `${strategy.id.replace(/_/g, " ")} · ${symbolList(symbols, 2)} · ${timeframe} · ${period}`
                ) : (
                  <>
                    <span className={combos > limit ? "neg" : undefined}>
                      {combos} combination{combos === 1 ? "" : "s"} (max {limit})
                    </span>{" "}
                    · {optMode === "grid" ? "grid search" : "walk-forward"} · {objLabel}
                  </>
                )}
              </span>
              <kbd className="lab-kbd" title={`Run from the keyboard: ${mac ? "Cmd" : "Ctrl"}+Enter`}>
                {mac ? "⌘ ↵" : "Ctrl ↵"}
              </kbd>
            </div>
            <button
              type="button"
              className="btn primary lab-run"
              onClick={runCurrent}
              disabled={experiment === "backtest" ? !canBacktest : !canOptimise}
            >
              {busy ? <span className="spinner" aria-hidden="true" /> : null}
              {busy ? "Running..." : experiment === "backtest" ? "Run backtest" : "Run optimiser"}
            </button>
          </div>
        </Panel>
      </div>

      <div className="lab-results" ref={resultsRef}>
        {experiment === "backtest" ? (
          <>
            <ErrorBox error={error} />
            {loading && !result ? <RunningPanel label="Running backtest..." /> : null}
            {result ? (
              <div className={loading ? "refetching stack" : "stack"}>
                <BacktestView
                  res={result}
                  meta={meta}
                  counterpart={counterpart}
                  onReplay={replay}
                  onOptimise={() => chooseExperiment("optimize")}
                  run={ran}
                  busy={loading}
                  stale={btStale}
                />
              </div>
            ) : !loading ? (
              <BacktestEmpty />
            ) : null}
          </>
        ) : (
          <>
            <div className="callout info">
              Trying many parameter combinations and keeping the best one is how most backtests are <b>overfit</b>. This tool
              reports the <b>Deflated Sharpe Ratio</b> (the probability the winner is real after accounting for how many you tried),
              and <b>walk-forward</b> testing re-optimises on past data and scores only on the unseen period that follows.
            </div>
            <ErrorBox error={optError} />
            {!numeric.length ? (
              <Panel marks title="Search" aside="nothing to vary">
                <Hatch />
                <p className="lab-empty-lede">
                  This strategy has no numeric parameters to optimise. Pick another strategy in the apparatus, or switch the
                  experiment back to a backtest.
                </p>
              </Panel>
            ) : optLoading && !optResult ? (
              <RunningPanel label="Evaluating parameter combinations..." />
            ) : null}
            {optResult ? (
              <div className={optLoading ? "refetching stack" : "stack"}>
                {optResult.mode === "grid" ? (
                  <GridView
                    res={optResult}
                    run={optRan}
                    busy={optLoading}
                    stale={optStale}
                    onUse={(p) => {
                      const next = { ...params, ...p };
                      setParams(next);
                      void runBacktest(next);
                    }}
                  />
                ) : (
                  <WalkForwardView res={optResult} run={optRan} busy={optLoading} stale={optStale} anchored={anchored} />
                )}
              </div>
            ) : !optLoading && numeric.length ? (
              <OptimiseEmpty />
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

/** One group of the apparatus: a mono-caps head with its number and current setting, folding open. */
function Section({ id, n, title, summary, open = true, children }: {
  id?: string;
  n: string;
  title: string;
  summary?: ReactNode;
  open?: boolean;
  children: ReactNode;
}) {
  return (
    <details className="lab-sec" id={id} open={open}>
      <summary className="lab-sec-head">
        <span className="lab-sec-n">{n}</span>
        <span className="lab-sec-title">{title}</span>
        <span className="lab-sec-sum">{summary}</span>
        <span className="lab-sec-caret" aria-hidden="true" />
      </summary>
      <div className="lab-sec-body">{children}</div>
    </details>
  );
}

function RunMeta({ run, busy }: { run: RunInfo | null; busy?: boolean }) {
  if (busy)
    return (
      <span className="lab-runmeta">
        <span className="spinner" aria-hidden="true" />
        running
      </span>
    );
  return run ? <span className="lab-runmeta">{runLabel(run)}</span> : null;
}

function StaleNote({ action }: { action: string }) {
  return (
    <div className="lab-stale" role="status">
      <Tag kind="warn">Set-up changed</Tag>
      <span>
        These results come from the previous set-up. Press <b>{action}</b> to bring them up to date.
      </span>
    </div>
  );
}

/** Small labelled numbers in a grid, beside a figure: label, value, and a muted note. */
function MiniStats({ items }: { items: [ReactNode, ReactNode, ReactNode?][] }) {
  return (
    <dl className="lab-mini">
      {items.map(([label, value, note], i) => (
        <div key={i}>
          <dt>{label}</dt>
          <dd>
            <span className="lab-mini-v">{value}</span>
            {note ? <span className="lab-mini-n">{note}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A return, signed and coloured as profit or loss. */
function Signed({ v, digits = 1 }: { v: Num | undefined; digits?: number }) {
  return <span className={pnlClass(v)}>{fmtPct(v ?? null, digits)}</span>;
}

function RoLabel({ text, help }: { text: string; help?: string }) {
  return (
    <span className="lab-ro-label">
      {text}
      {help ? <Help text={help} /> : null}
    </span>
  );
}

function RunningPanel({ label }: { label: string }) {
  return (
    <Panel marks title="Result" aside={<Spinner label={label} />}>
      <Hatch className="lab-running" />
      <p className="lab-empty-lede">Every bar is computed in order, with each fill priced through the cost model. This takes a few seconds.</p>
    </Panel>
  );
}

/** "What a run produces": a key and a sentence per row. */
function Contents({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div className="lab-contents">
      <h3>{title}</h3>
      <dl>
        {rows.map(([k, v]) => (
          <Fragment key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

function BacktestEmpty() {
  return (
    <Panel marks title="Result" aside="no run yet">
      <Hatch />
      <p className="lab-empty-lede">
        Choose a strategy and symbols, then <b>Run backtest</b>. Results include costs, a buy-and-hold benchmark and statistics that
        tell you how much to trust them.
      </p>
      <Contents
        title="What a run produces"
        rows={[
          ["Readouts", "Total return, CAGR, Sharpe ratio, max drawdown, trades, time in market and costs paid, each beside buying and holding."],
          ["Fig. 1", "Equity, net of costs, against buy & hold."],
          ["Fig. 2", "Drawdown: how far the account fell below its previous high."],
          ["Fig. 3", "Monthly returns, year by year."],
          ["Fig. 4+", "Each symbol's price with every buy and sell."],
          ["Tables", "Cost sensitivity, all statistics, and every trade."],
        ]}
      />
    </Panel>
  );
}

function OptimiseEmpty() {
  return (
    <Panel marks title="Search" aside="no run yet">
      <Hatch />
      <p className="lab-empty-lede">
        Nothing optimised yet. Tick the parameters to vary in the apparatus, pick what to maximise and a method, then press{" "}
        <b>Run optimiser</b>.
      </p>
      <div className="lab-contents-2">
        <Contents
          title="Grid search produces"
          rows={[
            ["Readouts", "The best combination, its Deflated Sharpe Ratio, and how good the best of that many tries would look by luck alone."],
            ["Fig. 1", "A heatmap of the score over one or two parameters, the best cell outlined in orange."],
            ["Table", "Every combination, each a click away from its own backtest."],
          ]}
        />
        <Contents
          title="Walk-forward produces"
          rows={[
            ["Readouts", "The in-sample score against the out-of-sample score, and the walk-forward efficiency."],
            ["Fig. 1", "Equity from only the periods the optimiser never saw, beside the default parameters."],
            ["Fig. 2", "The train and test windows on one timeline, with each test's return."],
            ["Table", "Every window, the parameters it chose and how they did."],
          ]}
        />
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------------------------
// Backtest results
// ------------------------------------------------------------------------------------

function BacktestView({ res, meta, counterpart, onReplay, onOptimise, run, busy, stale }: {
  res: BacktestResult;
  meta: Meta;
  counterpart?: StrategyMeta;
  onReplay: (compare: boolean) => Promise<void>;
  onOptimise: () => void;
  run: RunInfo | null;
  busy: boolean;
  stale: boolean;
}) {
  const colors = useChartColors();
  const [replayError, setReplayError] = useState<string | null>(null);
  const [replaying, setReplaying] = useState<"alone" | "compare" | null>(null);
  const startReplay = async (compare: boolean) => {
    setReplaying(compare ? "compare" : "alone");
    setReplayError(null);
    try {
      await onReplay(compare);
    } catch (e) {
      setReplayError((e as Error).message);
    } finally {
      setReplaying(null);
    }
  };
  const equityLines = useMemo(
    () => [
      { id: "strategy", label: res.strategy.name, data: res.equity, color: colors.series[0] },
      ...(res.after_tax_equity ? [{ id: "after_tax", label: `${res.strategy.name}, after tax`, data: res.after_tax_equity, color: colors.series[1] }] : []),
      ...(res.benchmark ? [{ id: "bench", label: res.benchmark.label, data: res.benchmark.equity, color: colors.deemph }] : []),
    ],
    [res, colors],
  );
  const ddLines = useMemo(
    () => [
      { id: "dd", label: res.strategy.name, data: res.drawdown, color: colors.down, kind: "baseline" as const },
      ...(res.benchmark ? [{ id: "bdd", label: res.benchmark.label, data: res.benchmark.drawdown, color: colors.deemph }] : []),
    ],
    [res, colors],
  );
  const monthly = useMemo(() => {
    const rows = res.monthly_returns.map((r) => String(r.year));
    const values = res.monthly_returns.map((r) => [...r.months, r.total]);
    const monthVals = res.monthly_returns.flatMap((r) => r.months).filter(isNum) as number[];
    const maxAbs = Math.max(0.01, ...monthVals.map(Math.abs));
    // The month and the year in numbers: how many made money, the extremes, the longest losing run.
    let best: { v: number; at: string } | null = null;
    let worst: { v: number; at: string } | null = null;
    let bestYear: { v: number; at: string } | null = null;
    let worstYear: { v: number; at: string } | null = null;
    let up = 0;
    let run = 0;
    let longestLoss = 0;
    for (const r of res.monthly_returns) {
      for (let i = 0; i < r.months.length; i++) {
        const v = r.months[i];
        if (!isNum(v)) continue;
        if (v > 0) up += 1;
        run = v < 0 ? run + 1 : 0;
        longestLoss = Math.max(longestLoss, run);
        const at = `${MONTHS[i]} ${r.year}`;
        if (!best || v > best.v) best = { v, at };
        if (!worst || v < worst.v) worst = { v, at };
      }
      if (isNum(r.total)) {
        if (!bestYear || r.total > bestYear.v) bestYear = { v: r.total, at: String(r.year) };
        if (!worstYear || r.total < worstYear.v) worstYear = { v: r.total, at: String(r.year) };
      }
    }
    const sortedVals = [...monthVals].sort((a, b) => a - b);
    const mid = sortedVals.length >> 1;
    const median = sortedVals.length ? (sortedVals.length % 2 ? sortedVals[mid] : (sortedVals[mid - 1] + sortedVals[mid]) / 2) : null;
    const mean = monthVals.length ? monthVals.reduce((a, b) => a + b, 0) / monthVals.length : null;
    return { rows, values, maxAbs, best, worst, bestYear, worstYear, up, count: monthVals.length, median, mean, longestLoss };
  }, [res]);

  const m = res.metrics;
  const b = res.benchmark?.metrics;
  const cash = fmtMoney(Number(res.config.initial_cash));
  const firstChartFig = monthly.rows.length ? 4 : 3;

  return (
    <>
      <BacktestReadouts res={res} meta={meta} run={run} busy={busy} stale={stale} />
      <Warnings items={res.warnings} />
      <div className="lab-next">
        <span className="lab-next-k">Next</span>
        <div className="lab-next-body">
          <p className="lab-next-text">
            Watch this test unfold: replay it bar by bar in the strategy simulator and see every decision with its reason as it
            happens.
          </p>
          <div className="lab-next-actions">
          <button type="button" className="btn small" disabled={replaying !== null} onClick={() => void startReplay(false)}>
            {replaying === "alone" ? "Preparing..." : "Replay bar by bar"}
          </button>
          {counterpart ? (
            <button type="button" className="btn small" disabled={replaying !== null} onClick={() => void startReplay(true)}>
              {replaying === "compare" ? "Preparing..." : `Race it against ${counterpart.name}`}
            </button>
          ) : null}
            <button type="button" className="btn small ghost" onClick={onOptimise}>
              Stress it in the optimiser →
            </button>
          </div>
        </div>
      </div>
      <ErrorBox error={replayError} />

      <Figure fig="Fig. 1" title="Equity, net of costs" aside={`${fmtDate(res.start_time)} → ${fmtDate(res.end_time)} · start ${cash}`}>
        <LineChart lines={equityLines} format={(v) => fmtMoney(v)} height={300} label="Equity curve" toggleable />
        <p className="lab-figcap">
          The account's value after every commission and slippage charge, starting from {cash}
          {res.benchmark ? <>, against simply buying and holding over the same dates ({res.benchmark.label})</> : null}
          {res.after_tax_equity ? <>; the second line pays tax on realised gains as it goes</> : null}. Click a key to hide its
          line.
        </p>
      </Figure>

      <Figure fig="Fig. 2" title="Drawdown" aside="distance below the previous equity high">
        <LineChart lines={ddLines} format={(v) => fmtPct(v, 1)} height={170} label="Drawdown" />
        <p className="lab-figcap">
          Worst fall from a peak: <b>{fmtPct(m.max_drawdown)}</b>
          {b ? <> (buy &amp; hold {fmtPct(b.max_drawdown)})</> : null}. The longest stretch below a previous high lasted{" "}
          {isNum(m.max_dd_duration) ? `${fmtInt(m.max_dd_duration)} bars, about ${barsAsTime(m.max_dd_duration, res.timeframe)}` : "–"}. Ask
          yourself honestly whether you could sit through it.
        </p>
      </Figure>

      {monthly.rows.length ? (
        <Figure fig="Fig. 3" title="Monthly returns" aside="per month, net of costs">
          <div className="lab-split lab-split-wide">
            <div className="lab-split-main">
              <Heatmap
                rows={monthly.rows}
                cols={[...MONTHS, "Year"]}
                values={monthly.values}
                maxAbs={monthly.maxAbs}
                format={(v) => fmtPct(v, 1)}
                rowLabel="Year"
                colLabel="Month"
              />
            </div>
            <div className="lab-split-side">
              <h3>In numbers</h3>
              <MiniStats
                items={[
                  ["Months", fmtInt(monthly.count), `${fmtInt(monthly.up)} made money`],
                  ["Hit rate", fmtPct(monthly.count ? monthly.up / monthly.count : null, 0, false), "months up"],
                  ["Average", <Signed v={monthly.mean} />, "per month"],
                  ["Median", <Signed v={monthly.median} />, "per month"],
                  ["Best month", <Signed v={monthly.best?.v} />, monthly.best?.at],
                  ["Worst month", <Signed v={monthly.worst?.v} />, monthly.worst?.at],
                  ["Best year", <Signed v={monthly.bestYear?.v} />, monthly.bestYear?.at],
                  ["Worst year", <Signed v={monthly.worstYear?.v} />, monthly.worstYear?.at],
                  ["Losing streak", fmtInt(monthly.longestLoss), `month${monthly.longestLoss === 1 ? "" : "s"} down in a row`],
                ]}
              />
            </div>
          </div>
          <p className="lab-figcap">
            Blue cells made money and red cells lost it; the deeper the colour, the bigger the month. The Year column compounds the
            months, so it is not their sum.
          </p>
        </Figure>
      ) : null}

      {res.charts.map((ch, i) => (
        <TradeChart key={ch.symbol} chart={ch} timeframe={res.timeframe} fig={firstChartFig + i} />
      ))}

      {Object.keys(res.pending_orders).length ? (
        <div className="callout info">
          <b>If you followed this strategy today:</b> at the next open it would{" "}
          {Object.entries(res.pending_orders)
            .map(([s, q]) =>
              Math.abs(q) < 1e-9
                ? `close the ${s} position`
                : `${q > 0 ? "hold" : "be short"} ${Math.abs(Math.round(q)).toLocaleString()} shares of ${s}`,
            )
            .join(", ")}
          . See the Live Desk for current signals.
        </div>
      ) : null}

      {res.cost_sensitivity ? <CostSensitivityPanel cs={res.cost_sensitivity} tax={res.tax} /> : null}
      <StatsPanel res={res} meta={meta} />
      <Panel title="Trades" aside={`${res.trades.length} round trips · newest first`}>
        <TradesTable trades={res.trades} showSymbol={res.symbols.length > 1} />
      </Panel>
    </>
  );
}

/** The headline numbers, each beside buy & hold, with the equations behind them. */
function BacktestReadouts({ res, meta, run, busy, stale }: {
  res: BacktestResult;
  meta: Meta;
  run: RunInfo | null;
  busy: boolean;
  stale: boolean;
}) {
  const m = res.metrics;
  const b = res.benchmark?.metrics;
  const info = meta.metrics;
  const label = (k: string, fallback: string) => <RoLabel text={info[k]?.label ?? fallback} help={info[k]?.help} />;
  const vsB = (text: string) => (b ? `B&H ${text}` : undefined);
  const tr = pctParts(m.total_return, true);
  const cg = pctParts(m.cagr, true);
  const dd = pctParts(m.max_drawdown);
  const at = pctParts(m.after_tax_cagr_if_sold, true);
  const ps = pctParts(m.psr);
  const ex = pctParts(m.exposure);
  const ppy = Number(res.config.periods_per_year) || 252;
  const perDay = Math.round(ppy / 252);
  return (
    <Panel marks className="lab-readouts" title="Result, net of costs" aside={<RunMeta run={run} busy={busy} />}>
      <RunLine res={res} meta={meta} />
      {stale ? <StaleNote action="Run backtest" /> : null}
      <div className="lab-ro" style={cols(4)}>
        <Readout label={label("total_return", "Total return")} value={tr[0]} unit={tr[1]} tone={toneOf(m.total_return)} note={vsB(fmtPct(b?.total_return, 1, true))} />
        <Readout label={label("cagr", "CAGR")} value={cg[0]} unit={cg[1] ? "%/yr" : ""} tone={toneOf(m.cagr)} note={vsB(fmtPct(b?.cagr, 1, true))} />
        <Readout label={label("sharpe", "Sharpe ratio")} value={fmtNum(m.sharpe)} note={vsB(fmtNum(b?.sharpe))} />
        <Readout label={label("max_drawdown", "Max drawdown")} value={dd[0]} unit={dd[1]} note={vsB(fmtPct(b?.max_drawdown))} />
      </div>
      <div className="lab-ro lab-ro-minor" style={cols(5)}>
        <Readout
          size="sm"
          label={label("after_tax_cagr_if_sold", "After tax, if sold at end")}
          value={at[0]}
          unit={at[1] ? "%/yr" : ""}
          tone={toneOf(m.after_tax_cagr_if_sold)}
          note={vsB(fmtPct(b?.after_tax_cagr_if_sold, 1, true))}
        />
        <Readout size="sm" label={label("psr", "Prob. Sharpe > 0")} value={ps[0]} unit={ps[1]} note={vsB(fmtPct(b?.psr, 1, false))} />
        <Readout size="sm" label={label("n_trades", "Trades")} value={fmtInt(m.n_trades)} note={vsB(fmtInt(b?.n_trades))} />
        <Readout size="sm" label={label("exposure", "Time in market")} value={ex[0]} unit={ex[1]} note={vsB(fmtPct(b?.exposure, 1, false))} />
        <Readout size="sm" label={label("total_costs", "Costs paid")} value={fmtMoney(m.total_costs)} note={vsB(fmtMoney(b?.total_costs))} />
      </div>
      <div className="lab-ro-foot">
        {b && res.benchmark ? <p className="lab-vs">{versus(m, b, res.benchmark.label)}</p> : null}
        <div className="lab-eq">
          <span>
            <b>Sharpe</b> = mean ÷ stdev of {BAR_WORD[res.timeframe] ?? "bar"} returns × √{ppy.toLocaleString()}
            {perDay > 1 ? ` (252 days × ${perDay} bars)` : ""}
          </span>
          <span>
            <b>CAGR</b> = (final ÷ starting equity)<sup>1/years</sup> − 1
          </span>
          <span>
            <b>Max drawdown</b> = lowest equity ÷ the peak before it − 1
          </span>
        </div>
      </div>
    </Panel>
  );
}

/** What was run: the set-up behind the numbers on screen (which may differ from the apparatus now). */
function RunLine({ res, meta }: { res: BacktestResult; meta: Meta }) {
  const c = res.config;
  const rows: [string, ReactNode][] = [
    ["Strategy", res.strategy.name],
    ["Symbols", res.symbols.join(" ")],
    ["Bars", meta.timeframes.find((t) => t.value === res.timeframe)?.label ?? res.timeframe],
    [
      "Period",
      <>
        {fmtDate(res.start_time)} → {fmtDate(res.end_time)} <span className="muted">({spanText(res.start_time, res.end_time)})</span>
      </>,
    ],
    ["Data", meta.providers.find((p) => p.id === res.provider)?.label ?? res.provider],
    ["Capital", fmtMoney(Number(c.initial_cash))],
    ["Costs", `${bps(c.slippage_bps)} bps slippage + ${bps(c.commission_bps)} bps commission`],
    ["Fills", c.execution === "next_close" ? "next bar's close" : "next bar's open"],
  ];
  return (
    <dl className="lab-runline">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One sentence comparing the strategy with buy & hold on return, risk-adjusted return and pain. */
function versus(m: Metrics, b: Metrics, label: string): ReactNode {
  const diff = (x: Num | undefined, y: Num | undefined) => (isNum(x) && isNum(y) ? x - y : null);
  const parts: ReactNode[] = [];
  const cagr = diff(m.cagr, b.cagr);
  if (cagr !== null)
    parts.push(
      Math.abs(cagr) < 0.0005 ? (
        "the same CAGR"
      ) : (
        <>
          CAGR <b>{fmtNum(Math.abs(cagr) * 100, 1)} points {cagr > 0 ? "higher" : "lower"}</b>
        </>
      ),
    );
  const sr = diff(m.sharpe, b.sharpe);
  if (sr !== null)
    parts.push(
      Math.abs(sr) < 0.005 ? (
        "the same Sharpe ratio"
      ) : (
        <>
          Sharpe ratio <b>{fmtNum(Math.abs(sr), 2)} {sr > 0 ? "higher" : "lower"}</b>
        </>
      ),
    );
  const dd = diff(m.max_drawdown, b.max_drawdown);
  if (dd !== null)
    parts.push(
      Math.abs(dd) < 0.0005 ? (
        "the same worst drawdown"
      ) : (
        <>
          worst drawdown <b>{fmtNum(Math.abs(dd) * 100, 1)} points {dd > 0 ? "shallower" : "deeper"}</b>
        </>
      ),
    );
  if (!parts.length) return null;
  return (
    <>
      <span className="lab-vs-k">Against {label} (B&amp;H)</span>{" "}
      {parts.map((p, i) => (
        <Fragment key={i}>
          {i === 0 ? null : i === parts.length - 1 ? " and " : ", "}
          {p}
        </Fragment>
      ))}
      .
    </>
  );
}

/** The same backtest at 1x, 2x and 4x slippage: does the edge survive higher trading costs? */
function CostSensitivityPanel({ cs, tax }: { cs: NonNullable<BacktestResult["cost_sensitivity"]>; tax: BacktestResult["tax"] }) {
  const taxable = tax?.account_type === "taxable";
  const columns: [string, (r: CostSensitivityRow | CostSensitivity["benchmark"]) => string][] = [
    ["CAGR", (r) => fmtPct(r.cagr)],
    [taxable ? "After tax, if sold" : "After tax", (r) => fmtPct(r.after_tax_cagr_if_sold)],
    ["Sharpe", (r) => fmtNum(r.sharpe)],
    ["Max drawdown", (r) => fmtPct(r.max_drawdown)],
    ["Cost drag / yr", (r) => fmtPct(r.cost_drag, 2, false)],
  ];
  return (
    <Panel title="Cost sensitivity" aside="the same backtest with slippage doubled and quadrupled">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Slippage</th>
              {columns.map(([label]) => (
                <th key={label} className="num">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cs.rows.map((r) => (
              <tr key={r.multiplier}>
                <td className="nowrap">
                  <span className="num">
                    {r.multiplier}× ({fmtNum(r.slippage_bps, 1)} bps)
                  </span>
                  {r.multiplier === 1 ? (
                    <>
                      {" "}
                      <Tag>as run</Tag>
                    </>
                  ) : null}
                </td>
                {columns.map(([label, f]) => (
                  <td key={label} className="num">
                    {f(r)}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="muted">
              <td>Buy &amp; hold</td>
              {columns.map(([label, f]) => (
                <td key={label} className="num">
                  {f(cs.benchmark)}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="lab-verdict">
        <span className="label">Verdict</span>
        <span>{cs.verdict}</span>
      </p>
    </Panel>
  );
}

/** Every metric, grouped so a number is quick to find; anything new lands in "Other". */
const STAT_GROUPS: [string, string[]][] = [
  ["Returns", ["total_return", "cagr", "after_tax_cagr", "after_tax_cagr_if_sold", "taxes_paid"]],
  ["Risk", ["volatility", "max_drawdown", "max_dd_duration", "sharpe", "sortino", "calmar", "psr", "skew", "kurtosis"]],
  [
    "Trading",
    ["n_trades", "win_rate", "profit_factor", "payoff_ratio", "expectancy", "avg_bars_held", "exposure", "avg_gross_exposure", "turnover", "total_costs", "cost_drag", "kelly"],
  ],
  ["Against the benchmark", ["beta", "alpha", "correlation", "excess_cagr"]],
];

function StatsPanel({ res, meta }: { res: BacktestResult; meta: Meta }) {
  const known = new Set(STAT_GROUPS.flatMap(([, keys]) => keys));
  const other = Object.keys(meta.metrics).filter((k) => !known.has(k));
  const groups = (other.length ? [...STAT_GROUPS, ["Other", other] as [string, string[]]] : STAT_GROUPS)
    .map(([title, keys]) => [title, keys.filter((k) => res.metrics[k] !== undefined || res.benchmark?.metrics[k] !== undefined)] as const)
    .filter(([, keys]) => keys.length);
  return (
    <Panel title="All statistics" aside={res.benchmark ? `strategy beside ${res.benchmark.label}` : "strategy"}>
      <div className="lab-stats">
        {groups.map(([title, keys]) => (
          <div className="lab-stats-group" key={title}>
            <h3>{title}</h3>
            <MetricsTable metrics={res.metrics} benchmark={res.benchmark?.metrics} info={meta.metrics} keys={[...keys]} />
          </div>
        ))}
      </div>
    </Panel>
  );
}

function TradeChart({ chart, timeframe, fig }: { chart: BacktestResult["charts"][number]; timeframe: string; fig: number }) {
  const colors = useChartColors();
  const markers: PriceMarker[] = useMemo(
    () =>
      chart.markers.map((m) => ({
        time: m.t,
        position: m.side === "buy" ? "belowBar" : "aboveBar",
        shape: m.side === "buy" ? "arrowUp" : "arrowDown",
        color: m.side === "buy" ? colors.up : colors.down,
      })),
    [chart, colors],
  );
  const buys = chart.markers.filter((m) => m.side === "buy").length;
  const sells = chart.markers.length - buys;
  const labels = chart.overlays.map((o) => o.label);
  const overlays = labels.length
    ? labels.length === 1
      ? `; the line is the strategy's own indicator (${labels[0]})`
      : `; the lines are the strategy's own indicators (${labels.join(", ")})`
    : "";
  return (
    <Figure
      fig={`Fig. ${fig}`}
      title={`${chart.symbol}: signals and fills`}
      aside={
        <>
          <span className="upc">▲ buy</span> · <span className="downc">▼ sell</span>
        </>
      }
    >
      <PriceChart bars={chart.bars} overlays={chart.overlays} markers={markers} height={320} intraday={timeframe !== "1d"} initialBars={400} />
      <p className="lab-figcap">
        {buys} buy{buys === 1 ? "" : "s"} and {sells} sell{sells === 1 ? "" : "s"}, each marked at the bar it was filled
        {overlays}. Scroll or drag to zoom.
      </p>
    </Figure>
  );
}

// ------------------------------------------------------------------------------------
// Optimisation
// ------------------------------------------------------------------------------------

interface RangeSpec {
  on: boolean;
  min: number;
  max: number;
  step: number;
}

function defaultRange(p: ParamSpec): RangeSpec {
  const d = Number(p.default);
  const isInt = p.kind === "int";
  let lo = d * 0.5;
  let hi = d * 1.5;
  if (d === 0) {
    lo = p.min ?? 0;
    hi = Math.min(p.max ?? 10, (p.min ?? 0) + 10);
  }
  if (p.min !== null) lo = Math.max(lo, p.min);
  if (p.max !== null) hi = Math.min(hi, p.max);
  let step = (hi - lo) / 4;
  if (isInt) {
    lo = Math.round(lo);
    hi = Math.round(hi);
    step = Math.max(1, Math.round((hi - lo) / 4));
  } else {
    step = Math.max(Number(step.toPrecision(2)), 0.01);
  }
  return { on: false, min: lo, max: hi, step };
}

function countValues(r: RangeSpec): number {
  if (r.step <= 0 || r.max < r.min) return 0;
  return Math.floor((r.max - r.min) / r.step + 1e-9) + 1;
}

function numericParams(s: StrategyMeta): ParamSpec[] {
  return s.params.filter((p) => p.kind === "int" || p.kind === "float");
}

/** The first two numeric parameters start ticked, each over half to one and a half times its default. */
function initialRanges(s: StrategyMeta): Record<string, RangeSpec> {
  const r: Record<string, RangeSpec> = {};
  numericParams(s).forEach((p, i) => (r[p.name] = { ...defaultRange(p), on: i < 2 }));
  return r;
}

/** Bars as time, for the walk-forward window sizes. */
function barsAsTime(bars: number, timeframe: string): string {
  if (!isNum(bars) || bars <= 0) return "–";
  if (timeframe === "1d") return `${fmtNum(bars / 252, 1)} yr`;
  const perSession = { "1h": 7, "30m": 13, "15m": 26, "5m": 78, "1m": 390 }[timeframe] ?? 78;
  return `${fmtNum(bars / perSession, 0)} sessions`;
}

function SearchControls(props: {
  numeric: ParamSpec[];
  ranges: Record<string, RangeSpec>;
  setRanges: (r: Record<string, RangeSpec>) => void;
  params: Record<string, unknown>;
  objective: string;
  setObjective: (o: string) => void;
  mode: "grid" | "walk_forward";
  setMode: (m: "grid" | "walk_forward") => void;
  trainBars: number;
  setTrainBars: (n: number) => void;
  testBars: number;
  setTestBars: (n: number) => void;
  anchored: boolean;
  setAnchored: (a: boolean) => void;
  timeframe: string;
}) {
  const { numeric, ranges, setRanges, params, mode } = props;
  if (!numeric.length) return <p className="lab-note">This strategy has no numeric parameters to optimise.</p>;
  return (
    <>
      <p className="lab-hint">Tick the parameters to vary; one or two draw a heatmap. A varied parameter ignores its value above.</p>
      <div className="lab-ranges">
        {numeric.map((p) => {
          const r = ranges[p.name];
          if (!r) return null;
          const set = (patch: Partial<RangeSpec>) => setRanges({ ...ranges, [p.name]: { ...r, ...patch } });
          return (
            <div className={`lab-range${r.on ? " on" : ""}`} key={p.name}>
              <label className="check lab-range-head" title={p.help}>
                <input type="checkbox" checked={r.on} onChange={(e) => set({ on: e.target.checked })} aria-label={`Vary ${p.label}`} />
                <span className="lab-range-name">{p.label}</span>
                <span className="lab-range-count">{r.on ? `${countValues(r)} values` : `fixed ${String(params[p.name] ?? p.default)}`}</span>
              </label>
              {r.on ? (
                <div className="lab-range-inputs">
                  {(["min", "max", "step"] as const).map((k) => (
                    <label className="field" key={k}>
                      <span>{k === "min" ? "From" : k === "max" ? "To" : "Step"}</span>
                      <input
                        className="input num"
                        type="number"
                        step={p.kind === "int" ? 1 : "any"}
                        value={r[k]}
                        onChange={(e) => set({ [k]: Number(e.target.value) } as Partial<RangeSpec>)}
                      />
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      <label className="field">
        <span>Objective</span>
        <select className="input" value={props.objective} onChange={(e) => props.setObjective(e.target.value)}>
          <option value="sharpe">Sharpe ratio</option>
          <option value="sortino">Sortino ratio</option>
          <option value="calmar">Calmar ratio</option>
          <option value="cagr">CAGR</option>
          <option value="total_return">Total return</option>
        </select>
        <span className="hint">The score each combination is ranked by.</span>
      </label>
      <div className="field">
        <span id="lab-method">Method</span>
        <div className="segmented lab-seg" role="group" aria-labelledby="lab-method">
          <button type="button" aria-pressed={mode === "grid"} onClick={() => props.setMode("grid")}>
            Grid search
          </button>
          <button type="button" aria-pressed={mode === "walk_forward"} onClick={() => props.setMode("walk_forward")}>
            Walk-forward
          </button>
        </div>
        <span className="hint">
          {mode === "grid"
            ? "Every combination, scored over the whole period."
            : "Re-optimise on each training window, then score only the unseen test window that follows it."}
        </span>
      </div>
      {mode === "walk_forward" ? (
        <>
          <div className="lab-pair">
            <label className="field">
              <span>Train (bars)</span>
              <input className="input num" type="number" value={props.trainBars} onChange={(e) => props.setTrainBars(Number(e.target.value))} />
              <span className="hint">≈ {barsAsTime(props.trainBars, props.timeframe)}</span>
            </label>
            <label className="field">
              <span>Test (bars)</span>
              <input className="input num" type="number" value={props.testBars} onChange={(e) => props.setTestBars(Number(e.target.value))} />
              <span className="hint">≈ {barsAsTime(props.testBars, props.timeframe)}</span>
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={props.anchored} onChange={(e) => props.setAnchored(e.target.checked)} />
            Anchored
          </label>
          <p className="lab-hint">Anchored: every training window starts at the first bar and grows. Otherwise it rolls forward at a fixed length.</p>
        </>
      ) : null}
    </>
  );
}

const OBJECTIVE_LABELS: Record<string, string> = {
  sharpe: "Sharpe ratio",
  sortino: "Sortino ratio",
  calmar: "Calmar ratio",
  cagr: "CAGR",
  total_return: "Total return",
};

function objectiveFormat(objective: string) {
  return objective === "cagr" || objective === "total_return" ? (v: number) => fmtPct(v, 1) : (v: number) => fmtNum(v, 2);
}

/** A score as a readout: percentages carry their unit apart. */
function objectiveParts(objective: string, v: Num | undefined): [string, string] {
  if (objective === "cagr" || objective === "total_return") return pctParts(v, true);
  return [fmtNum(v ?? null, 2), ""];
}

function paramText(p: Record<string, unknown>): string {
  return Object.entries(p)
    .map(([k, v]) => `${k} ${String(v)}`)
    .join(", ");
}

function optimiseSentence(res: OptimizeResult): string {
  const label = OBJECTIVE_LABELS[res.objective] ?? res.objective;
  const fmt = objectiveFormat(res.objective);
  const score = (v: Num | undefined) => (isNum(v) ? fmt(v) : "–");
  if (res.mode === "grid") {
    const dsr = res.deflated_sharpe;
    const judge =
      isNum(dsr) && dsr >= 0.95
        ? "probably more than luck. Next: confirm it walk-forward before trusting it."
        : "it may well be luck, so do not trust it yet. Next: try it walk-forward.";
    return `Best of ${res.n_trials} combinations: ${paramText(res.best.params)}, ${label} ${score(res.best.metrics[res.objective])}. Deflated Sharpe ${fmtPct(dsr, 0, false)}: ${judge}`;
  }
  const w = walkScores(res);
  const wfLabel = OBJECTIVE_LABELS[w.key] ?? w.key;
  const wfFmt = objectiveFormat(w.key);
  const wfScore = (v: Num | undefined) => (isNum(v) ? wfFmt(v) : "–");
  return `Walk-forward over ${res.windows.length} windows: out-of-sample ${wfLabel} ${wfScore(w.oos)} against ${wfScore(w.inSample)} in-sample (efficiency ${fmtNum(res.efficiency, 2)}); the default parameters scored ${wfScore(w.byDefault)}.`;
}

/** Walk-forward compares like with like: a total-return objective is compared annualised, as CAGR (as the server does). */
function walkScores(res: WalkForwardResult) {
  const key = res.objective === "total_return" ? "cagr" : res.objective;
  return { key, inSample: res.in_sample_mean, oos: res.oos_metrics[key], byDefault: res.default_metrics[key] };
}

function GridView({ res, onUse, run, busy, stale }: {
  res: GridResult;
  onUse: (p: Record<string, unknown>) => void;
  run: RunInfo | null;
  busy: boolean;
  stale: boolean;
}) {
  const names = Object.keys(res.grid);
  const fmt = objectiveFormat(res.objective);
  const label = OBJECTIVE_LABELS[res.objective] ?? res.objective;
  const heat = useMemo(() => {
    if (names.length < 1 || names.length > 2) return null;
    const [a, b] = names;
    const rowsV = res.grid[a];
    const colsV = b ? res.grid[b] : ["–"];
    const values = rowsV.map((rv) =>
      colsV.map((cv) => {
        const row = res.rows.find((r) => r.params[a] === rv && (!b || r.params[b] === cv));
        const v = row?.metrics[res.objective];
        return isNum(v) ? v : null;
      }),
    );
    const bi = rowsV.findIndex((v) => v === res.best.params[a]);
    const bj = b ? colsV.findIndex((v) => v === res.best.params[b]) : 0;
    return { a, b, rows: rowsV.map(String), cols: colsV.map(String), values, best: [bi, bj] as [number, number] };
  }, [res, names]);
  // A plateau or a spike? The average score of the cells touching the best one.
  const plateau = useMemo(() => {
    if (!heat) return null;
    const [bi, bj] = heat.best;
    const top = heat.values[bi]?.[bj];
    if (!isNum(top)) return null;
    const around: number[] = [];
    for (let di = -1; di <= 1; di++)
      for (let dj = -1; dj <= 1; dj++) {
        const v = di || dj ? heat.values[bi + di]?.[bj + dj] : null;
        if (isNum(v)) around.push(v);
      }
    if (!around.length) return null;
    const mean = around.reduce((s, v) => s + v, 0) / around.length;
    return { mean, cells: around.length, share: top > 0 ? mean / top : null };
  }, [heat]);
  // Every combination's score, to say how much of the parameter space works at all.
  const spread = useMemo(() => {
    const vals = res.rows.map((r) => r.metrics[res.objective]).filter(isNum).sort((x, y) => x - y);
    if (!vals.length) return null;
    const mid = vals.length >> 1;
    return {
      n: vals.length,
      worst: vals[0],
      median: vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2,
      above: vals.filter((v) => v > 0).length,
    };
  }, [res]);
  const sorted = [...res.rows].sort((x, y) => (y.metrics[res.objective] ?? -1e9) - (x.metrics[res.objective] ?? -1e9));
  const isBest = (r: OptimizeRow) => names.every((k) => r.params[k] === res.best.params[k]);
  const dsr = res.deflated_sharpe;
  const weak = isNum(dsr) && dsr < 0.95;
  const best = objectiveParts(res.objective, res.best.metrics[res.objective]);
  const dsrParts = pctParts(dsr);
  const bestCagr = pctParts(res.best.metrics.cagr, true);
  const bestDd = pctParts(res.best.metrics.max_drawdown);
  const bestPsr = pctParts(res.best.metrics.psr);
  const cells = heat ? heat.rows.length * heat.cols.length : 0;
  const score = (v: Num | undefined) => (isNum(v) ? fmt(v) : "–");
  return (
    <>
      <Panel marks title="Best combination" aside={<RunMeta run={run} busy={busy} />}>
        {stale ? <StaleNote action="Run optimiser" /> : null}
        <div className="lab-chosen">
          <span className="label">Parameters</span>
          <span className="lab-chosen-vals">
            {Object.entries(res.best.params).map(([k, v]) => (
              <span key={k} title={`Best ${k}`}>
                <span className="lab-chosen-k">{k}</span>
                {String(v)}
              </span>
            ))}
          </span>
          <button type="button" className="btn small" onClick={() => onUse(res.best.params)}>
            Backtest it
          </button>
        </div>
        <div className="lab-ro" style={cols(4)}>
          <Readout label={`Best ${label}`} value={best[0]} unit={best[1]} />
          <Readout
            label={
              <RoLabel
                text="Deflated Sharpe"
                help="The probability that the best combination's Sharpe ratio is real after allowing for how many combinations were tried (Bailey and López de Prado). 95% is the usual bar."
              />
            }
            value={dsrParts[0]}
            unit={dsrParts[1]}
            tone={weak ? "accent" : undefined}
            note={isNum(dsr) ? (weak ? "below the 95% bar" : "clears the 95% bar") : undefined}
          />
          <Readout label="Combinations tried" value={fmtInt(res.n_trials)} />
          <Readout
            label={
              <RoLabel
                text="Sharpe from luck"
                help="With this many tries and no real edge at all, the best annualised Sharpe ratio you would expect by chance alone."
              />
            }
            value={fmtNum(res.expected_max_sharpe_under_null, 2)}
            note={`best of ${res.n_trials} tries, no edge`}
          />
        </div>
        <div className="lab-ro lab-ro-minor" style={cols(4)}>
          <Readout size="sm" label="Its CAGR" value={bestCagr[0]} unit={bestCagr[1] ? "%/yr" : ""} tone={toneOf(res.best.metrics.cagr)} />
          <Readout size="sm" label="Its max drawdown" value={bestDd[0]} unit={bestDd[1]} />
          <Readout size="sm" label="Its trades" value={fmtNum(res.best.metrics.n_trades ?? null, 0)} />
          <Readout size="sm" label="Its prob. Sharpe > 0" value={bestPsr[0]} unit={bestPsr[1]} />
        </div>
      </Panel>
      <div className={`callout ${weak ? "warn" : "info"}`}>
        <b>Deflated Sharpe Ratio: {fmtPct(dsr, 0, false)}.</b> {res.interpretation} With {res.n_trials} trials, pure luck would be
        expected to produce a best annualised Sharpe of about {fmtNum(res.expected_max_sharpe_under_null, 2)}.
      </div>
      {heat ? (
        <Figure
          fig="Fig. 1"
          title={`${label} by ${heat.b ? `${heat.a} × ${heat.b}` : heat.a}`}
          aside={`${cells} cells · best outlined in orange`}
        >
          <div className="lab-split">
            <div className="lab-split-main">
              <Heatmap rows={heat.rows} cols={heat.cols} values={heat.values} best={heat.best} format={fmt} rowLabel={heat.a} colLabel={heat.b ?? ""} />
            </div>
            <div className="lab-split-side">
              <h3>Plateau or spike?</h3>
              <MiniStats
                items={[
                  ["Best cell", score(res.best.metrics[res.objective]), paramText(res.best.params)],
                  [
                    <RoLabel
                      text="Neighbours"
                      help="The average score of the cells touching the best one. Close to the best means a plateau (robust); far below it means a lone spike (probably luck)."
                    />,
                    plateau ? score(plateau.mean) : "–",
                    plateau ? `${plateau.share !== null ? `${fmtPct(plateau.share, 0, false)} of the best, ` : ""}${plateau.cells} cells` : undefined,
                  ],
                  ["Median cell", score(spread?.median), "half the cells score more"],
                  ["Worst cell", score(spread?.worst)],
                  ["Above zero", spread ? `${spread.above} of ${spread.n}` : "–", spread ? `${fmtPct(spread.above / spread.n, 0, false)} of the cells` : undefined],
                ]}
              />
            </div>
          </div>
          <p className="lab-figcap">
            What was optimised: the {label.toLowerCase().replace("cagr", "CAGR")} of every combination of {heat.b ? `${heat.a} and ${heat.b}` : heat.a},
            over the whole period. The orange outline marks the best cell. Do not trust it blindly: with {res.n_trials} tries, the
            best one looks good partly by luck (overfitting). A robust strategy shows a broad plateau of good cells, not one
            lucky spike.
          </p>
        </Figure>
      ) : (
        <Panel title="Heatmap" aside="needs one or two varied parameters">
          <Hatch />
          <p className="lab-empty-lede">The heatmap shows one or two varied parameters at a time. With {names.length}, read the table below.</p>
        </Panel>
      )}
      <Panel title="All combinations" aside={`sorted by ${label}`}>
        <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
          <table className="data">
            <thead>
              <tr>
                {names.map((k) => (
                  <th key={k} className="num">
                    {k}
                  </th>
                ))}
                <th className="num">Sharpe</th>
                <th className="num">CAGR</th>
                <th className="num">Max DD</th>
                <th className="num">Trades</th>
                <th className="num">P(SR&gt;0)</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 200).map((r, i) => {
                const top = isBest(r);
                return (
                  <tr key={i} className={top ? "selected" : undefined}>
                    {names.map((k) => (
                      <td key={k} className="num">
                        {String(r.params[k])}
                      </td>
                    ))}
                    <td className="num">{fmtNum(r.metrics.sharpe ?? null)}</td>
                    <td className="num">{fmtPct(r.metrics.cagr)}</td>
                    <td className="num">{fmtPct(r.metrics.max_drawdown)}</td>
                    <td className="num">{fmtNum(r.metrics.n_trades ?? null, 0)}</td>
                    <td className="num">{fmtPct(r.metrics.psr, 0, false)}</td>
                    <td className="lab-row-act">
                      {top ? <Tag kind="warn">best</Tag> : null}
                      <button type="button" className="btn small ghost" onClick={() => onUse(r.params)}>
                        Backtest
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {res.rejected.length ? <p className="small muted lab-after-table">{res.rejected.length} invalid combinations were skipped.</p> : null}
      </Panel>
    </>
  );
}

function WalkForwardView({ res, run, busy, stale, anchored }: {
  res: WalkForwardResult;
  run: RunInfo | null;
  busy: boolean;
  stale: boolean;
  anchored: boolean;
}) {
  const colors = useChartColors();
  const lines = useMemo(
    () => [
      { id: "oos", label: "Re-optimised (out of sample)", data: res.oos_equity, color: colors.series[0] },
      { id: "default", label: "Default parameters", data: res.default_equity, color: colors.deemph },
    ],
    [res, colors],
  );
  const fmt = objectiveFormat(res.objective);
  const label = OBJECTIVE_LABELS[res.objective] ?? res.objective;
  const w = walkScores(res);
  const wLabel = OBJECTIVE_LABELS[w.key] ?? w.key;
  const score = (v: Num | undefined): [string, string] => (isNum(v) ? objectiveParts(w.key, v) : ["–", ""]);
  const ins = score(w.inSample);
  const oos = score(w.oos);
  const def = score(w.byDefault);
  // The server's own test: out of sample below half of in sample means mostly curve-fitting.
  const faded = isNum(res.efficiency) && res.efficiency < 0.5;
  const o = res.oos_metrics;
  const ret = pctParts(o.total_return, true);
  const dd = pctParts(o.max_drawdown);
  return (
    <>
      <Panel marks title="Walk-forward" aside={<RunMeta run={run} busy={busy} />}>
        {stale ? <StaleNote action="Run optimiser" /> : null}
        <div className="lab-ro" style={cols(4)}>
          <Readout label={<RoLabel text={`Avg in-sample ${wLabel}`} help="What the optimiser 'saw' on each training window." />} value={ins[0]} unit={ins[1]} />
          <Readout label={`Out-of-sample ${wLabel}`} value={oos[0]} unit={oos[1]} note="periods it never saw" />
          <Readout label="Default params, same periods" value={def[0]} unit={def[1]} note="no re-optimising" />
          <Readout
            label={
              <RoLabel
                text="Walk-forward efficiency"
                help="Out-of-sample result divided by the average in-sample result. Near 1 = the edge carried over; near 0 = it was mostly curve-fitting."
              />
            }
            value={fmtNum(res.efficiency, 2)}
            tone={faded ? "accent" : undefined}
            note={faded ? "under 0.5: mostly curve-fitting" : "out-of-sample ÷ in-sample"}
          />
        </div>
        <div className="lab-ro lab-ro-minor" style={cols(4)}>
          <Readout size="sm" label="Windows" value={fmtInt(res.windows.length)} />
          <Readout size="sm" label="Out-of-sample return" value={ret[0]} unit={ret[1]} tone={toneOf(o.total_return)} />
          <Readout size="sm" label="Out-of-sample max drawdown" value={dd[0]} unit={dd[1]} />
          <Readout size="sm" label="Out-of-sample trades" value={fmtInt(o.n_trades)} />
        </div>
      </Panel>
      <div className={`callout ${faded ? "warn" : "info"}`}>{res.interpretation}</div>
      <Figure fig="Fig. 1" title="Out-of-sample equity" aside="only periods the optimiser never saw">
        <LineChart lines={lines} format={(v) => fmtMoney(v)} height={260} />
        <p className="lab-figcap">
          Each test period is traded with the parameters chosen on the training window just before it, and the periods are stitched
          together. The grey line keeps the default parameters over the same periods: if re-optimising cannot beat it, the
          optimisation is not adding anything.
        </p>
      </Figure>
      <Figure fig="Fig. 2" title="Walk-forward windows" aside={anchored ? "anchored: training grows from the first bar" : "rolling: training keeps a fixed length"}>
        <WindowsTimeline windows={res.windows} />
      </Figure>
      <Panel title="Windows" aside={`${res.windows.length} train and test splits`}>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Train</th>
                <th>Test</th>
                <th>Chosen parameters</th>
                <th className="num">Train {label}</th>
                <th className="num">Test Sharpe</th>
                <th className="num">Test return</th>
              </tr>
            </thead>
            <tbody>
              {res.windows.map((w) => (
                <tr key={w.test_start}>
                  <td className="nowrap num">
                    {fmtDate(w.train_start)} – {fmtDate(w.train_end)}
                  </td>
                  <td className="nowrap num">
                    {fmtDate(w.test_start)} – {fmtDate(w.test_end)}
                  </td>
                  <td className="num">
                    {Object.entries(w.best_params)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ")}
                  </td>
                  <td className="num">{isNum(w.in_sample[res.objective]) ? fmt(w.in_sample[res.objective] as number) : "–"}</td>
                  <td className="num">{fmtNum(w.out_of_sample.sharpe ?? null)}</td>
                  <td className={`num ${pnlClass(w.out_of_sample.total_return)}`}>{fmtPct(w.out_of_sample.total_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

/** Every window on one time axis: where the optimiser fitted, and where it was scored. */
function WindowsTimeline({ windows }: { windows: WalkWindow[] }) {
  if (!windows.length) return <p className="lab-empty-lede">No windows fitted in the data available.</p>;
  const t0 = Math.min(...windows.map((w) => w.train_start));
  const t1 = Math.max(...windows.map((w) => w.test_end));
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * 100;
  const years: number[] = [];
  for (let y = new Date(t0 * 1000).getUTCFullYear() + 1; y <= new Date(t1 * 1000).getUTCFullYear(); y++) years.push(y);
  const every = Math.max(1, Math.ceil(years.length / 8));
  return (
    <div className="lab-wf">
      <div className="lab-wf-legend">
        <span>
          <span className="lab-wf-key train" aria-hidden="true" />
          Train: the optimiser picks the parameters
        </span>
        <span>
          <span className="lab-wf-key test" aria-hidden="true" />
          Test: those parameters, on bars it never saw
        </span>
        <span className="lab-wf-legend-r">test return</span>
      </div>
      {windows.map((w, i) => {
        const r = w.out_of_sample.total_return;
        return (
          <div className="lab-wf-row" key={w.test_start}>
            <span className="lab-wf-n">{String(i + 1).padStart(2, "0")}</span>
            <span className="lab-wf-track">
              <span
                className="lab-wf-train"
                style={{ left: `${x(w.train_start)}%`, width: `${Math.max(0.3, x(w.train_end) - x(w.train_start))}%` }}
                title={`Train ${fmtDate(w.train_start)} – ${fmtDate(w.train_end)}`}
              />
              <span
                className="lab-wf-test"
                style={{ left: `${x(w.test_start)}%`, width: `${Math.max(0.6, x(w.test_end) - x(w.test_start))}%` }}
                title={`Test ${fmtDate(w.test_start)} – ${fmtDate(w.test_end)}: ${fmtPct(r)}`}
              />
            </span>
            <span className={`lab-wf-ret ${pnlClass(r)}`}>{fmtPct(r)}</span>
          </div>
        );
      })}
      <div className="lab-wf-row lab-wf-axis" aria-hidden="true">
        <span />
        <span className="lab-wf-track">
          {years.map((y, i) =>
            i % every ? null : (
              <span
                key={y}
                className={`lab-wf-tick${(i / every) % 2 ? " alt" : ""}`}
                style={{ left: `${x(Date.UTC(y, 0, 1) / 1000)}%` }}
              >
                {y}
              </span>
            ),
          )}
        </span>
        <span />
      </div>
    </div>
  );
}
