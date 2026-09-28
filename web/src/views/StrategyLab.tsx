import { useEffect, useMemo, useState } from "react";
import { api, type BacktestRequest } from "../api";
import { useApp, type Route } from "../state";
import type {
  BacktestResult,
  GridResult,
  Meta,
  OptimizeResult,
  ParamSpec,
  Settings,
  Sizing,
  StrategyMeta,
  WalkForwardResult,
} from "../types";
import PriceChart, { type PriceMarker } from "../components/PriceChart";
import LineChart from "../components/LineChart";
import Heatmap from "../components/Heatmap";
import {
  ErrorBox,
  EvidenceBadge,
  Help,
  MetricTiles,
  MetricsTable,
  ParamForm,
  SizingForm,
  Spinner,
  SymbolInput,
  Tabs,
  TradesTable,
  Warnings,
} from "../components/ui";
import { fmtDate, fmtMoney, fmtNum, fmtPct, isNum } from "../format";
import { useChartColors } from "../theme";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const REAL_SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"];

function defaultSymbols(s: StrategyMeta, provider: string, settings: Settings): string[] {
  const synthetic = provider === "synthetic";
  if (s.kind === "pair") return synthetic ? ["SIMPRA", "SIMPRB"] : ["KO", "PEP"];
  if (s.id === "dual_momentum") return synthetic ? ["SIMIDX", "SIMGLD", "SIMBND"] : ["SPY", "EFA", "AGG"];
  if (s.kind === "cross_sectional") {
    const wl = settings.watchlists[provider] ?? [];
    return synthetic ? wl.filter((x) => !["SIMPRA", "SIMPRB"].includes(x)) : wl.length >= 3 ? wl : REAL_SECTORS;
  }
  return synthetic ? ["SIMIDX"] : ["SPY"];
}

function defaultParams(s: StrategyMeta, provider: string): Record<string, unknown> {
  const p: Record<string, unknown> = {};
  s.params.forEach((x) => (p[x.name] = x.default));
  if (s.id === "dual_momentum") p.safe_symbol = provider === "synthetic" ? "SIMBND" : "AGG";
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

export default function StrategyLab({ route }: { route: Route }) {
  const { meta, settings } = useApp() as { meta: Meta; settings: Settings };
  const byId = useMemo(() => new Map(meta.strategies.map((s) => [s.id, s])), [meta]);
  const initial = byId.get(route.params.get("strategy") ?? "") ?? byId.get("tsmom") ?? meta.strategies[0];
  const [strategyId, setStrategyId] = useState(initial.id);
  const strategy = byId.get(strategyId) ?? initial;
  const [provider, setProvider] = useState(settings.provider);
  const [symbols, setSymbols] = useState<string[]>(() => defaultSymbols(initial, settings.provider, settings));
  const [timeframe, setTimeframe] = useState("1d");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [params, setParams] = useState<Record<string, unknown>>(() => defaultParams(initial, settings.provider));
  const [sizing, setSizing] = useState<Partial<Sizing>>(() => baseSizing(initial));
  const [config, setConfig] = useState({
    initial_cash: 100000,
    commission_bps: settings.commission_bps,
    slippage_bps: settings.slippage_bps,
    allow_short: settings.allow_short || initial.uses_short,
    execution: "next_open",
  });
  const [tab, setTab] = useState<"backtest" | "optimize">("backtest");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chooseStrategy = (id: string) => {
    const s = byId.get(id);
    if (!s) return;
    setStrategyId(id);
    setParams(defaultParams(s, provider));
    setSizing(baseSizing(s));
    setSymbols(defaultSymbols(s, provider, settings));
    setConfig((c) => ({ ...c, allow_short: settings.allow_short || s.uses_short }));
    setResult(null);
  };

  useEffect(() => {
    const id = route.params.get("strategy");
    if (id && id !== strategyId && byId.has(id)) chooseStrategy(id);
  }, [route]);

  const changeProvider = (p: string) => {
    setProvider(p);
    setSymbols(defaultSymbols(strategy, p, settings));
    setParams(defaultParams(strategy, p));
  };

  const request = (): BacktestRequest => ({
    strategy: { id: strategy.id, params, sizing },
    symbols,
    timeframe,
    start: start || null,
    end: end || null,
    provider,
    config,
  });

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await api.backtest(request()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const grouped = useMemo(() => {
    const m = new Map<string, StrategyMeta[]>();
    meta.strategies.forEach((s) => m.set(s.category, [...(m.get(s.category) ?? []), s]));
    return [...m.entries()];
  }, [meta]);

  const provInfo = meta.providers.find((p) => p.id === provider);
  const symbolHint =
    strategy.kind === "pair"
      ? "Exactly two symbols: A then B."
      : strategy.kind === "cross_sectional"
        ? `A universe to rank (${strategy.min_symbols}+ symbols; more is better).`
        : "One or more symbols (capital is split equally).";

  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field" style={{ minWidth: 260 }}>
            <span>Strategy</span>
            <select className="input" value={strategy.id} onChange={(e) => chooseStrategy(e.target.value)}>
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
          </label>
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
          <label className="field">
            <span>Bars</span>
            <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {meta.timeframes
                .filter((t) => provInfo?.timeframes.includes(t.value) ?? true)
                .map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>Start</span>
            <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="field">
            <span>End</span>
            <input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <button
            className="btn primary"
            onClick={() => {
              setTab("backtest");
              void run();
            }}
            disabled={loading || !symbols.length}
          >
            {loading ? "Running..." : "Run backtest"}
          </button>
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <span>Symbols</span>
          <SymbolInput symbols={symbols} onChange={setSymbols} max={strategy.max_symbols ?? 30} />
          <span className="hint">
            {symbolHint}
            {start ? "" : " Leave dates blank to use all available history."} Indicator warm-up history before the start date is
            loaded automatically.
          </span>
        </div>
      </div>

      <div className="split">
        <div className="stack">
          <div className="card">
            <div className="card-header">
              <h2>{strategy.name}</h2>
              <EvidenceBadge level={strategy.evidence} />
            </div>
            <p className="secondary">{strategy.summary}</p>
            <a className="small" href={`#/library?strategy=${strategy.id}`}>
              Rules, research and caveats
            </a>
          </div>
          <div className="card">
            <h3 className="section-title">Parameters</h3>
            <ParamForm spec={strategy.params} values={params} onChange={setParams} />
            <button className="btn small ghost" style={{ marginTop: 8 }} onClick={() => setParams(defaultParams(strategy, provider))}>
              Reset to published defaults
            </button>
          </div>
          <div className="card">
            <h3 className="section-title">Position sizing</h3>
            <SizingForm value={sizing} onChange={setSizing} />
          </div>
          <div className="card">
            <h3 className="section-title">Costs and execution</h3>
            <div className="form-grid">
              <label className="field">
                <span>Starting capital ($)</span>
                <input
                  className="input num"
                  type="number"
                  value={config.initial_cash}
                  onChange={(e) => setConfig({ ...config, initial_cash: Number(e.target.value) })}
                />
              </label>
              <label className="field" title="Adverse price impact on every fill (half the bid-ask spread plus market impact).">
                <span>Slippage (bps)</span>
                <input
                  className="input num"
                  type="number"
                  step={0.5}
                  value={config.slippage_bps}
                  onChange={(e) => setConfig({ ...config, slippage_bps: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Commission (bps)</span>
                <input
                  className="input num"
                  type="number"
                  step={0.5}
                  value={config.commission_bps}
                  onChange={(e) => setConfig({ ...config, commission_bps: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                <span>Fill at</span>
                <select className="input" value={config.execution} onChange={(e) => setConfig({ ...config, execution: e.target.value })}>
                  <option value="next_open">Next bar's open</option>
                  <option value="next_close">Next bar's close</option>
                </select>
              </label>
            </div>
            <label className="check" style={{ marginTop: 10 }}>
              <input type="checkbox" checked={config.allow_short} onChange={(e) => setConfig({ ...config, allow_short: e.target.checked })} />
              Allow short selling
            </label>
            <p className="small muted" style={{ marginTop: 8 }}>
              Signals are computed at a bar's close and filled on the next bar, so a strategy can never trade on information it
              would not have had (no look-ahead bias).
            </p>
          </div>
        </div>

        <div className="stack">
          <Tabs
            tabs={[
              ["backtest", "Backtest"],
              ["optimize", "Optimise & validate"],
            ]}
            value={tab}
            onChange={setTab}
          />
          {tab === "backtest" ? (
            <>
              <ErrorBox error={error} />
              {loading && !result ? <Spinner label="Running backtest..." /> : null}
              {result ? (
                <div className={loading ? "refetching stack" : "stack"}>
                  <BacktestView res={result} meta={meta} />
                </div>
              ) : !loading ? (
                <div className="card empty">
                  Choose a strategy and symbols, then <b>Run backtest</b>. Results include costs, a buy-and-hold benchmark and
                  statistics that tell you how much to trust them.
                </div>
              ) : null}
            </>
          ) : (
            <OptimizePanel
              strategy={strategy}
              baseRequest={request()}
              onUse={(p) => {
                setParams({ ...params, ...p });
                setTab("backtest");
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function BacktestView({ res, meta }: { res: BacktestResult; meta: Meta }) {
  const colors = useChartColors();
  const equityLines = useMemo(
    () => [
      { id: "strategy", label: res.strategy.name, data: res.equity, color: colors.series[0] },
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
    return { rows, values, maxAbs };
  }, [res]);

  return (
    <>
      <Warnings items={res.warnings} />
      <MetricTiles
        keys={["total_return", "cagr", "sharpe", "max_drawdown", "psr", "n_trades", "win_rate", "exposure"]}
        metrics={res.metrics}
        benchmark={res.benchmark?.metrics}
        info={meta.metrics}
      />
      <div className="card">
        <div className="card-header">
          <h2>Equity</h2>
          <span className="sub">
            {fmtDate(res.start_time)} to {fmtDate(res.end_time)} · starting capital {fmtMoney(Number(res.config.initial_cash))}
          </span>
        </div>
        <LineChart lines={equityLines} format={(v) => fmtMoney(v)} height={260} label="Equity curve" toggleable />
      </div>
      <div className="card">
        <div className="card-header">
          <h2>Drawdown</h2>
          <span className="sub">Distance below the previous equity high</span>
        </div>
        <LineChart lines={ddLines} format={(v) => fmtPct(v, 1)} height={160} label="Drawdown" />
      </div>
      {res.charts.map((ch) => (
        <TradeChart key={ch.symbol} chart={ch} timeframe={res.timeframe} />
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
      {monthly.rows.length ? (
        <div className="card">
          <div className="card-header">
            <h2>Monthly returns</h2>
          </div>
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
      ) : null}
      <div className="card">
        <div className="card-header">
          <h2>All statistics</h2>
        </div>
        <MetricsTable metrics={res.metrics} benchmark={res.benchmark?.metrics} info={meta.metrics} />
      </div>
      <div className="card">
        <div className="card-header">
          <h2>Trades</h2>
          <span className="sub">{res.trades.length} round trips</span>
        </div>
        <TradesTable trades={res.trades} showSymbol={res.symbols.length > 1} />
      </div>
    </>
  );
}

function TradeChart({ chart, timeframe }: { chart: BacktestResult["charts"][number]; timeframe: string }) {
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
  return (
    <div className="card">
      <div className="card-header">
        <h2>{chart.symbol}: signals and fills</h2>
        <span className="sub">
          <span className="upc">▲ buy</span> · <span className="downc">▼ sell</span> · scroll or drag to zoom
        </span>
      </div>
      <PriceChart bars={chart.bars} overlays={chart.overlays} markers={markers} height={320} intraday={timeframe !== "1d"} initialBars={400} />
    </div>
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

function OptimizePanel({ strategy, baseRequest, onUse }: {
  strategy: StrategyMeta;
  baseRequest: BacktestRequest;
  onUse: (p: Record<string, unknown>) => void;
}) {
  const numeric = strategy.params.filter((p) => p.kind === "int" || p.kind === "float");
  const [ranges, setRanges] = useState<Record<string, RangeSpec>>(() => {
    const r: Record<string, RangeSpec> = {};
    numeric.forEach((p, i) => (r[p.name] = { ...defaultRange(p), on: i < 2 }));
    return r;
  });
  const [objective, setObjective] = useState("sharpe");
  const [mode, setMode] = useState<"grid" | "walk_forward">("grid");
  const [trainBars, setTrainBars] = useState(756);
  const [testBars, setTestBars] = useState(252);
  const [anchored, setAnchored] = useState(false);
  const [result, setResult] = useState<OptimizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const r: Record<string, RangeSpec> = {};
    numeric.forEach((p, i) => (r[p.name] = { ...defaultRange(p), on: i < 2 }));
    setRanges(r);
    setResult(null);
  }, [strategy.id]);

  const active = Object.entries(ranges).filter(([, r]) => r.on);
  const combos = active.reduce((acc, [, r]) => acc * Math.max(countValues(r), 1), 1);
  const limit = mode === "grid" ? 400 : 200;

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const grid: Record<string, { min: number; max: number; step: number }> = {};
      active.forEach(([name, r]) => (grid[name] = { min: r.min, max: r.max, step: r.step }));
      setResult(
        await api.optimize({
          ...baseRequest,
          grid,
          objective,
          mode,
          train_bars: trainBars,
          test_bars: testBars,
          anchored,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  if (!numeric.length) return <div className="card empty">This strategy has no numeric parameters to optimise.</div>;

  return (
    <div className="stack">
      <div className="card">
        <div className="callout info" style={{ marginBottom: 12 }}>
          Trying many parameter combinations and keeping the best one is how most backtests are <b>overfit</b>. This tool reports
          the <b>Deflated Sharpe Ratio</b> (the probability the winner is real after accounting for how many you tried), and
          <b> walk-forward</b> testing re-optimises on past data and scores only on the unseen period that follows.
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Vary</th>
                <th>Parameter</th>
                <th className="num">From</th>
                <th className="num">To</th>
                <th className="num">Step</th>
                <th className="num">Values</th>
              </tr>
            </thead>
            <tbody>
              {numeric.map((p) => {
                const r = ranges[p.name];
                if (!r) return null;
                const set = (patch: Partial<RangeSpec>) => setRanges({ ...ranges, [p.name]: { ...r, ...patch } });
                return (
                  <tr key={p.name}>
                    <td>
                      <input type="checkbox" checked={r.on} onChange={(e) => set({ on: e.target.checked })} aria-label={`Vary ${p.label}`} />
                    </td>
                    <td>{p.label}</td>
                    {(["min", "max", "step"] as const).map((k) => (
                      <td key={k} className="num">
                        <input
                          className="input num"
                          style={{ width: 90 }}
                          type="number"
                          step={p.kind === "int" ? 1 : "any"}
                          value={r[k]}
                          disabled={!r.on}
                          onChange={(e) => set({ [k]: Number(e.target.value) } as Partial<RangeSpec>)}
                        />
                      </td>
                    ))}
                    <td className="num">{r.on ? countValues(r) : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="row" style={{ marginTop: 12, alignItems: "flex-end" }}>
          <label className="field">
            <span>Objective</span>
            <select className="input" value={objective} onChange={(e) => setObjective(e.target.value)}>
              <option value="sharpe">Sharpe ratio</option>
              <option value="sortino">Sortino ratio</option>
              <option value="calmar">Calmar ratio</option>
              <option value="cagr">CAGR</option>
              <option value="total_return">Total return</option>
            </select>
          </label>
          <div className="field">
            <span>Method</span>
            <div className="segmented" role="group">
              <button aria-pressed={mode === "grid"} onClick={() => setMode("grid")}>
                Grid search
              </button>
              <button aria-pressed={mode === "walk_forward"} onClick={() => setMode("walk_forward")}>
                Walk-forward
              </button>
            </div>
          </div>
          {mode === "walk_forward" ? (
            <>
              <label className="field">
                <span>Train (bars)</span>
                <input className="input num" style={{ width: 100 }} type="number" value={trainBars} onChange={(e) => setTrainBars(Number(e.target.value))} />
              </label>
              <label className="field">
                <span>Test (bars)</span>
                <input className="input num" style={{ width: 100 }} type="number" value={testBars} onChange={(e) => setTestBars(Number(e.target.value))} />
              </label>
              <label className="check">
                <input type="checkbox" checked={anchored} onChange={(e) => setAnchored(e.target.checked)} />
                Anchored
              </label>
            </>
          ) : null}
          <span className={`small ${combos > limit ? "neg" : "muted"}`}>
            {combos} combination{combos === 1 ? "" : "s"} (max {limit})
          </span>
          <button className="btn primary" onClick={() => void run()} disabled={loading || !active.length || combos > limit}>
            {loading ? "Running..." : "Run"}
          </button>
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !result ? <Spinner label="Evaluating parameter combinations..." /> : null}
      {result ? (
        <div className={loading ? "refetching stack" : "stack"}>
          {result.mode === "grid" ? <GridView res={result} onUse={onUse} /> : <WalkForwardView res={result} />}
        </div>
      ) : null}
    </div>
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

function GridView({ res, onUse }: { res: GridResult; onUse: (p: Record<string, unknown>) => void }) {
  const names = Object.keys(res.grid);
  const fmt = objectiveFormat(res.objective);
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
  const sorted = [...res.rows].sort((x, y) => (y.metrics[res.objective] ?? -1e9) - (x.metrics[res.objective] ?? -1e9));
  const dsr = res.deflated_sharpe;
  return (
    <>
      <div className={`callout ${isNum(dsr) && dsr < 0.95 ? "warn" : "info"}`}>
        <b>Deflated Sharpe Ratio: {fmtPct(dsr, 0, false)}.</b> {res.interpretation} With {res.n_trials} trials, pure luck would be
        expected to produce a best annualised Sharpe of about {fmtNum(res.expected_max_sharpe_under_null, 2)}.
      </div>
      <div className="tiles">
        {Object.entries(res.best.params).map(([k, v]) => (
          <div className="tile" key={k}>
            <div className="label">Best {k}</div>
            <div className="value">{String(v)}</div>
          </div>
        ))}
        <div className="tile">
          <div className="label">Best {OBJECTIVE_LABELS[res.objective] ?? res.objective}</div>
          <div className="value">{fmt(res.best.metrics[res.objective] ?? NaN)}</div>
        </div>
        <div className="tile">
          <div className="label">Its max drawdown</div>
          <div className="value">{fmtPct(res.best.metrics.max_drawdown)}</div>
        </div>
        <div className="tile">
          <div className="label">Its trades</div>
          <div className="value">{fmtNum(res.best.metrics.n_trades ?? null, 0)}</div>
        </div>
      </div>
      {heat ? (
        <div className="card">
          <div className="card-header">
            <h2>{OBJECTIVE_LABELS[res.objective] ?? res.objective} by parameter</h2>
            <span className="sub">A robust strategy shows a broad plateau, not one lucky spike. The best cell is outlined.</span>
          </div>
          <Heatmap
            rows={heat.rows}
            cols={heat.cols}
            values={heat.values}
            best={heat.best}
            format={fmt}
            rowLabel={heat.a}
            colLabel={heat.b ?? ""}
          />
        </div>
      ) : null}
      <div className="card">
        <div className="card-header">
          <h2>All combinations</h2>
          <span className="sub">sorted by {OBJECTIVE_LABELS[res.objective] ?? res.objective}</span>
        </div>
        <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
          <table className="data">
            <thead>
              <tr>
                {names.map((n) => (
                  <th key={n}>{n}</th>
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
              {sorted.slice(0, 200).map((r, i) => (
                <tr key={i}>
                  {names.map((n) => (
                    <td key={n}>{String(r.params[n])}</td>
                  ))}
                  <td className="num">{fmtNum(r.metrics.sharpe ?? null)}</td>
                  <td className="num">{fmtPct(r.metrics.cagr)}</td>
                  <td className="num">{fmtPct(r.metrics.max_drawdown)}</td>
                  <td className="num">{fmtNum(r.metrics.n_trades ?? null, 0)}</td>
                  <td className="num">{fmtPct(r.metrics.psr, 0, false)}</td>
                  <td>
                    <button className="btn small ghost" onClick={() => onUse(r.params)}>
                      Backtest
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {res.rejected.length ? <p className="small muted">{res.rejected.length} invalid combinations were skipped.</p> : null}
      </div>
    </>
  );
}

function WalkForwardView({ res }: { res: WalkForwardResult }) {
  const colors = useChartColors();
  const lines = useMemo(
    () => [
      { id: "oos", label: "Re-optimised (out of sample)", data: res.oos_equity, color: colors.series[0] },
      { id: "default", label: "Default parameters", data: res.default_equity, color: colors.deemph },
    ],
    [res, colors],
  );
  const fmt = objectiveFormat(res.objective);
  return (
    <>
      <div className="callout info">{res.interpretation}</div>
      <div className="tiles">
        <div className="tile">
          <div className="label">
            Avg in-sample {res.objective} <Help text="What the optimiser 'saw' on each training window." />
          </div>
          <div className="value">{isNum(res.in_sample_mean) ? fmt(res.in_sample_mean) : "–"}</div>
        </div>
        <div className="tile">
          <div className="label">Out-of-sample {res.objective}</div>
          <div className="value">{isNum(res.oos_metrics[res.objective]) ? fmt(res.oos_metrics[res.objective] as number) : "–"}</div>
        </div>
        <div className="tile">
          <div className="label">Default params, same periods</div>
          <div className="value">
            {isNum(res.default_metrics[res.objective]) ? fmt(res.default_metrics[res.objective] as number) : "–"}
          </div>
        </div>
        <div className="tile">
          <div className="label">
            Walk-forward efficiency <Help text="Out-of-sample result divided by the average in-sample result. Near 1 = the edge carried over; near 0 = it was mostly curve-fitting." />
          </div>
          <div className="value">{fmtNum(res.efficiency, 2)}</div>
        </div>
      </div>
      <div className="card">
        <div className="card-header">
          <h2>Out-of-sample equity</h2>
          <span className="sub">Only periods the optimiser never saw</span>
        </div>
        <LineChart lines={lines} format={(v) => fmtMoney(v)} height={240} />
      </div>
      <div className="card">
        <div className="card-header">
          <h2>Windows</h2>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Train</th>
                <th>Test</th>
                <th>Chosen parameters</th>
                <th className="num">Train {OBJECTIVE_LABELS[res.objective] ?? res.objective}</th>
                <th className="num">Test Sharpe</th>
                <th className="num">Test return</th>
              </tr>
            </thead>
            <tbody>
              {res.windows.map((w) => (
                <tr key={w.test_start}>
                  <td className="nowrap">
                    {fmtDate(w.train_start)} – {fmtDate(w.train_end)}
                  </td>
                  <td className="nowrap">
                    {fmtDate(w.test_start)} – {fmtDate(w.test_end)}
                  </td>
                  <td>
                    {Object.entries(w.best_params)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ")}
                  </td>
                  <td className="num">{isNum(w.in_sample[res.objective]) ? fmt(w.in_sample[res.objective] as number) : "–"}</td>
                  <td className="num">{fmtNum(w.out_of_sample.sharpe ?? null)}</td>
                  <td className="num">{fmtPct(w.out_of_sample.total_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
