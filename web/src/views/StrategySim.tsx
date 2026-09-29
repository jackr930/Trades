import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, arenaSocketUrl } from "../api";
import { useApp } from "../state";
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
import { ErrorBox, EvidenceBadge, Help, ParamForm, Spinner, StatTile, SymbolInput, Tabs, VoteGlyph, Warnings } from "../components/ui";
import { fmtDate, fmtDateTime, fmtMoney, fmtNum, fmtPct, fmtPrice, isNum, pnlClass } from "../format";
import { useChartColors, type ChartColors } from "../theme";

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
// Set-up
// ------------------------------------------------------------------------------------

const MAX_STRATEGIES = 16;

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
  const [cash, setCash] = useState(100000);
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

  if (!options) return error ? <ErrorBox error={error} /> : <Spinner label="Loading simulation options..." />;

  const syms = source === "simulated" ? simSymbols : symbols;
  const start_ = async () => {
    setBusy(true);
    setError(null);
    const cfg: Record<string, unknown> = {
      source,
      symbols: syms,
      strategies: chosen.map((id) => ({ id, params: params[id] ?? {} })),
      initial_cash: cash,
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

  return (
    <div className="stack">
      <div className="card">
        <div className="card-header">
          <div>
            <h1>Strategy simulator</h1>
            <p className="secondary" style={{ marginTop: 4, maxWidth: "80ch" }}>
              Every strategy gets its own paper account and reacts to each new bar as it arrives: it decides at the close using only
              the past, and trades at the next open, exactly as in a backtest. Shock a simulated market and watch who adapts, replay
              real history at any speed, or forward-test on the live market.
            </p>
          </div>
        </div>
        <Tabs
          tabs={[
            ["simulated", "Simulated market"],
            ["replay", "Historical replay"],
            ["realtime", "Real-time market"],
          ]}
          value={source}
          onChange={setSource}
        />
        {source === "simulated" ? (
          <div className="col">
            <div className="scenario-grid">
              {options.scenarios.map((s) => (
                <button key={s.id} className="option-card" aria-pressed={scenario === s.id} onClick={() => setScenario(s.id)}>
                  <span className="title">{s.label}</span>
                  <span className="desc">{s.description}</span>
                </button>
              ))}
            </div>
            <div className="field">
              <span>
                Symbols <Help text="A one-factor market: stocks move with the index by their beta plus their own noise. SIMPRA and SIMPRB are a cointegrated pair for the pairs strategy." />
              </span>
              <div className="chips">
                {options.symbols.map((s) => (
                  <button
                    key={s.symbol}
                    type="button"
                    className="chip toggle"
                    aria-pressed={simSymbols.includes(s.symbol)}
                    title={s.name}
                    onClick={() =>
                      setSimSymbols(simSymbols.includes(s.symbol) ? simSymbols.filter((x) => x !== s.symbol) : [...simSymbols, s.symbol])
                    }
                  >
                    {s.symbol}
                    <span className="muted small hide-sm"> {s.name}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="form-grid">
              <label className="field">
                <span>Length</span>
                <select className="input" value={length} onChange={(e) => setLength(Number(e.target.value))}>
                  <option value={0}>Full scenario</option>
                  <option value={250}>250 bars (1y)</option>
                  <option value={500}>500 bars (2y)</option>
                  <option value={1000}>1,000 bars (4y)</option>
                  <option value={2500}>2,500 bars (10y)</option>
                </select>
              </label>
              <label className="field">
                <span>
                  Seed <Help text="The same seed reproduces the same market, so you can re-run it with different strategies or parameters. Blank = random." />
                </span>
                <input className="input num" inputMode="numeric" placeholder="random" value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))} />
              </label>
            </div>
          </div>
        ) : (
          <div className="col">
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
            <div className="form-grid">
              <label className="field">
                <span>Data</span>
                <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
                  {(source === "realtime" ? realProviders : meta.providers).map((p) => (
                    <option key={p.id} value={p.id} disabled={!p.configured}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Bar size</span>
                <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                  <option value="1d">Daily</option>
                  <option value="1h">1 hour</option>
                  <option value="15m">15 minutes</option>
                  <option value="5m">5 minutes</option>
                  <option value="1m">1 minute</option>
                </select>
              </label>
              {source === "replay" ? (
                <>
                  <label className="field">
                    <span>First bar traded</span>
                    <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                  </label>
                  <label className="field">
                    <span>Bars to replay</span>
                    <input className="input num" type="number" min={20} max={5000} value={replayLength} onChange={(e) => setReplayLength(Number(e.target.value))} />
                  </label>
                </>
              ) : null}
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Strategies</h2>
          <span className="sub">
            {chosen.length} selected (up to {MAX_STRATEGIES}), plus buy-and-hold as the benchmark
          </span>
        </div>
        <div className="chips" role="group" aria-label="Quick picks">
          <span className="small muted">Quick picks:</span>
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
        {(["modern", "classic"] as const).map((fam) => (
          <div key={fam}>
            <h3 className="section-title" style={{ marginTop: 14 }}>
              {fam === "modern" ? "Modern quant methods" : "Classic published rules"}
            </h3>
            <div className="strategy-picks">
              {strategies
                .filter((s) => s.family === fam)
                .map((s) => (
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
          </div>
        ))}
        {editingMeta ? (
          <div className="subcard" style={{ marginTop: 12 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3>{editingMeta.name}: parameters</h3>
              <button className="btn ghost small" onClick={() => setParams({ ...params, [editingMeta.id]: {} })}>
                Reset to published defaults
              </button>
            </div>
            <ParamForm spec={editingMeta.params} values={params[editingMeta.id] ?? {}} onChange={(v) => setParams({ ...params, [editingMeta.id]: v })} />
          </div>
        ) : null}
      </div>

      <div className="card">
        <h3 className="section-title">Accounts and costs</h3>
        <div className="form-grid">
          <label className="field">
            <span>Cash per strategy ($)</span>
            <input className="input num" type="number" min={1000} value={cash} onChange={(e) => setCash(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Slippage (bps per fill)</span>
            <input className="input num" type="number" step={0.5} min={0} value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Commission (bps)</span>
            <input className="input num" type="number" step={0.5} min={0} value={commission} onChange={(e) => setCommission(Number(e.target.value))} />
          </label>
          {source !== "realtime" ? (
            <label className="field">
              <span>Speed</span>
              <select className="input" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                {SPEEDS.map((v) => (
                  <option key={v} value={v}>
                    {speedLabel(v)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
        <label className="check" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={allowShort} onChange={(e) => setAllowShort(e.target.checked)} />
          Allow short selling
        </label>
        <p className="small muted" style={{ marginTop: 8 }}>
          {shorters.length ? `Designed to short: ${shorters.join(", ")}. ` : ""}
          Shorts pay a 0.25%/yr borrow fee and borrowed cash costs 2%/yr above cash, as in the Strategy Lab.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            disabled={busy || !chosen.length || chosen.length > MAX_STRATEGIES || !syms.length}
            onClick={() => void start_()}
          >
            {busy ? "Preparing..." : "Start simulation"}
          </button>
          {busy ? <Spinner label={source === "simulated" ? "Generating the warm-up history..." : "Downloading history..."} /> : null}
        </div>
        <ErrorBox error={error} />
      </div>

      {runs.length ? (
        <div className="card">
          <div className="card-header">
            <h2>Open simulations</h2>
            <span className="sub">Kept in memory while the server runs</span>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Market</th>
                  <th>Status</th>
                  <th className="num">Bars</th>
                  <th>Leader</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">{new Date(r.created_at * 1000).toLocaleTimeString()}</td>
                    <td>
                      {r.title} <span className="muted small">{r.source}</span>
                    </td>
                    <td>{statusLabel(r.status)}</td>
                    <td className="num">
                      {r.progress.done}
                      {r.progress.total ? ` / ${r.progress.total}` : ""}
                    </td>
                    <td>
                      {r.leader.name} <span className={pnlClass(r.leader.return)}>{fmtPct(r.leader.return)}</span>
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
        </div>
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
    <div className="pick" data-checked={checked}>
      <label className="check">
        <input type="checkbox" checked={checked} onChange={(e) => onToggle(e.target.checked)} />
        <span className="pick-name">{s.name}</span>
      </label>
      <div className="row tight small">
        <span className="muted">{s.category}</span>
        <span className="muted">· {note}</span>
      </div>
      <div className="row tight" style={{ marginTop: "auto" }}>
        <EvidenceBadge level={s.evidence} />
        {s.params.length ? (
          <button type="button" className="btn small" style={{ marginLeft: "auto" }} aria-expanded={editing} onClick={onEdit}>
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

  const agentColors = useMemo(() => colorMap(run?.agents ?? [], colors), [run?.agents, colors]);

  if (!run) return error ? <ErrorBox error={error} /> : <Spinner label="Loading simulation..." />;
  const sym = symbol && run.feed.symbols.includes(symbol) ? symbol : run.feed.symbols[0];
  const agentId = selected && run.agents.some((a) => a.id === selected) ? selected : run.agents[0]?.id;
  const finished = run.status === "finished";

  return (
    <div className="stack">
      <RunHeader run={run} control={control} onNew={onNew} />
      <ErrorBox error={error ?? run.error} />
      {run.notes.map((n) => (
        <div className="callout info" key={n}>
          {n}
        </div>
      ))}
      {finished && run.summary ? (
        <SummaryCard run={run} colors={agentColors} onNew={onNew} onOpen={onOpen} />
      ) : null}
      <div className="split side-right">
        <div className="stack">
          <EquityRace run={run} agentColors={agentColors} />
          <div className="card">
            <div className="card-header">
              <h2>Market</h2>
              <div className="segmented symbol-tabs" role="group" aria-label="Symbol">
                {run.feed.symbols.map((s) => (
                  <button key={s} aria-pressed={s === sym} onClick={() => setSymbol(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <MarketChart run={run} symbol={sym} agentId={agentId} colors={colors} />
            <p className="small muted" style={{ marginTop: 6 }}>
              Arrows: fills of <b>{run.agents.find((a) => a.id === agentId)?.name}</b> (choose another strategy in the leaderboard).
              Circles: events you injected.
            </p>
          </div>
        </div>
        <div className="stack">
          <LeaderboardCard run={run} agentColors={agentColors} selected={agentId} onSelect={setSelected} />
          {run.feed.source === "simulated" && !finished && run.status !== "error" ? (
            <InjectCard run={run} onError={setError} />
          ) : null}
        </div>
      </div>
      <div className="split halves">
        <EventFeed run={run} agentColors={agentColors} />
        {agentId ? <AgentPanel run={run} agentId={agentId} color={agentColors[agentId]} onReload={reload} /> : null}
      </div>
    </div>
  );
}

function speedLabel(v: number): string {
  return v >= 200 ? "Maximum" : `${v} bar${v === 1 ? "" : "s"}/s`;
}

function statusLabel(s: ArenaState["status"]): string {
  return { ready: "Ready", running: "Running", paused: "Paused", finished: "Finished", error: "Error" }[s];
}

function colorMap(agents: ArenaAgent[], colors: ChartColors): Record<string, string> {
  const out: Record<string, string> = {};
  let i = 0;
  for (const a of agents) {
    if (a.kind === "benchmark") out[a.id] = colors.deemph;
    else out[a.id] = colors.series[i++ % colors.series.length];
  }
  return out;
}

function runTitle(run: ArenaState): string {
  if (run.feed.source === "simulated") return run.feed.scenario_label ?? "Simulated market";
  if (run.feed.source === "replay") return `Replay: ${run.feed.symbols.join(", ")}`;
  return `Live forward test: ${run.feed.symbols.join(", ")}`;
}

function RunHeader({ run, control, onNew }: {
  run: ArenaState;
  control: (b: Parameters<typeof api.arenaControl>[1]) => Promise<void>;
  onNew: () => void;
}) {
  const realtime = run.feed.source === "realtime";
  const done = run.progress.done;
  const total = run.progress.total;
  const pct = total ? Math.min(done / total, 1) : 0;
  const lastT = run.bars[run.feed.symbols[0]]?.t.slice(-1)[0];
  const regime = run.regimes ? run.regimes[run.regimes.length - 1] : null;
  const active = run.status !== "finished" && run.status !== "error";
  const market = run.feed_status.market;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="col" style={{ gap: 4 }}>
          <h1>
            {runTitle(run)}{" "}
            <span className={`badge ${run.status === "running" ? "up" : "neutral"}`}>
              {run.status === "running" ? <span className="live-dot" aria-hidden="true" /> : null}
              {statusLabel(run.status)}
            </span>
          </h1>
          <div className="small secondary">
            {run.feed.source === "simulated" ? `${run.feed.scenario_description ?? ""} Seed ${run.feed.seed}.` : null}
            {run.feed.source !== "simulated" ? `Data: ${run.feed.provider} · ${run.feed.timeframe} bars.` : null}
          </div>
          {regime ? (
            <div className="row tight small">
              <span className="muted">Hidden regime:</span>
              <RegimeBadge regime={regime} drift={run.regime_drift?.[regime]} />
              <Help text="The simulated market's true state. Strategies never see it; they have to infer it from prices. Changes also appear in the event feed." />
            </div>
          ) : null}
          {realtime && market ? (
            <div className="small secondary">
              {market.is_open ? "Market open" : `Market closed · next open ${new Date(market.next_open).toLocaleString()}`}
              {run.feed_status.last_poll ? ` · last checked ${new Date(run.feed_status.last_poll).toLocaleTimeString()}` : ""}
              {run.feed_status.error ? <span className="neg"> · {run.feed_status.error}</span> : null}
            </div>
          ) : null}
        </div>
        <div className="row">
          <div className="col" style={{ gap: 4, minWidth: 180 }}>
            <span className="small muted">
              {realtime ? `${done} live bar${done === 1 ? "" : "s"}` : `Bar ${done}${total ? ` of ${total}` : ""}`} · {fmtDate(lastT)}
            </span>
            {total ? (
              <div className="progress" role="progressbar" aria-valuenow={done} aria-valuemax={total} aria-label="Progress">
                <div style={{ width: `${pct * 100}%` }} />
              </div>
            ) : null}
          </div>
          {active ? (
            <>
              <button className="btn primary" onClick={() => void control({ action: run.status === "running" ? "pause" : "play" })}>
                {run.status === "running" ? "Pause" : "Play"} {realtime ? null : <span className="kbd">P</span>}
              </button>
              {!realtime ? (
                <>
                  <button className="btn" onClick={() => void control({ action: "step", n: 1 })} disabled={run.status === "running"}>
                    Next bar <span className="kbd">N</span>
                  </button>
                  <button className="btn" onClick={() => void control({ action: "step", n: 20 })} disabled={run.status === "running"}>
                    +20
                  </button>
                  <select
                    className="input"
                    style={{ width: 120 }}
                    aria-label="Speed"
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
              <button className="btn ghost" onClick={() => void control({ action: "stop" })}>
                Finish
              </button>
            </>
          ) : (
            <button className="btn" onClick={onNew}>
              New simulation
            </button>
          )}
        </div>
      </div>
    </div>
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

function EquityRace({ run, agentColors }: { run: ArenaState; agentColors: Record<string, string> }) {
  const lines: LineSpec[] = useMemo(
    () =>
      run.agents.map((a) => {
        const base = a.equity_curve.v[0] || 1;
        return {
          id: a.id,
          label: a.name,
          color: agentColors[a.id],
          style: a.kind === "benchmark" ? "dashed" : "solid",
          data: { t: a.equity_curve.t, v: a.equity_curve.v.map((v) => v / base - 1) },
        };
      }),
    [run.agents, agentColors],
  );
  const segments = useMemo(() => regimeSegments(run), [run]);
  return (
    <div className="card">
      <div className="card-header">
        <h2>Equity race</h2>
        <span className="sub">Return of each strategy's account; click a name to hide it</span>
      </div>
      <LineChart lines={lines} height={280} format={(v) => fmtPct(v)} toggleable label="Strategy returns" />
      {segments.length ? (
        <div style={{ marginTop: 8 }}>
          <div className="small muted" style={{ marginBottom: 4 }}>
            Hidden regime over the live period (strategies can't see it)
          </div>
          <div className="regimes" role="img" aria-label={segments.map((s) => `${s.label} for ${s.n} bars`).join(", ")}>
            {segments.map((s, i) => (
              <div key={i} className={`regime-${s.tone}`} style={{ flexGrow: s.n }} title={`${s.label}: ${s.n} bars`}>
                {s.n > 12 ? s.label : ""}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function regimeSegments(run: ArenaState): { label: string; n: number; tone: string }[] {
  if (!run.regimes) return [];
  const live = run.regimes.slice(run.start_index - run.first_index + 1);
  const out: { label: string; n: number; tone: string }[] = [];
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

function LeaderboardCard({ run, agentColors, selected, onSelect }: {
  run: ArenaState;
  agentColors: Record<string, string>;
  selected?: string;
  onSelect: (id: string) => void;
}) {
  const rows = [...run.agents].sort((a, b) => b.return - a.return);
  return (
    <div className="card">
      <div className="card-header">
        <h2>Leaderboard</h2>
        <span className="sub">Live; select a row for details</span>
      </div>
      <div className="table-wrap">
        <table className="data leaderboard">
          <thead>
            <tr>
              <th>Strategy</th>
              <th className="num">Return</th>
              <th className="num">
                Drawdown <Help text="Loss from the account's highest value so far." />
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr
                key={a.id}
                className={a.id === selected ? "selected" : ""}
                onClick={() => onSelect(a.id)}
                tabIndex={0}
                onKeyDown={(e) => (e.key === "Enter" ? onSelect(a.id) : undefined)}
                aria-selected={a.id === selected}
              >
                <td>
                  <span className="row tight" style={{ flexWrap: "nowrap" }}>
                    <span className="swatch-line" style={{ background: agentColors[a.id] }} />
                    <span className="lb-name">{a.name}</span>
                  </span>
                  <span className="small muted lb-pos">{positionsText(a)}</span>
                </td>
                <td className={`num ${pnlClass(a.return)}`}>{fmtPct(a.return)}</td>
                <td className="num">{fmtPct(a.drawdown, 1, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function positionsText(a: ArenaAgent): string {
  const entries = Object.entries(a.weights).sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]));
  if (a.stopped) return "account wiped out";
  if (!entries.length) return Object.keys(a.pending).length ? "orders pending" : "in cash";
  const parts = entries.slice(0, 2).map(([s, w]) => `${s} ${fmtPct(w, 0)}`);
  return parts.join(", ") + (entries.length > 2 ? ` +${entries.length - 2} more` : "");
}

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
  return (
    <div className="card">
      <div className="card-header">
        <h2>Shock the market</h2>
        <span className="sub">Takes effect on the next bar</span>
      </div>
      <div className="inject-grid">
        {kinds
          .filter((k) => k.kind !== "break_pair" || hasPair)
          .map((k) => (
            <button key={k.kind} className={`btn inject ${k.kind === "crash" ? "danger" : ""}`} disabled={busy} onClick={() => void quick(k)} title={k.help.replace(/`/g, "")}>
              {k.label}
              <span className="small muted">
                {k.kind === "regime" ? regime : isNum(k.size) ? (pctKinds.includes(k.kind) ? fmtPct(k.size, 0) : `${k.size}x`) : ""}
                {k.kind === "gap" ? ` ${symbol}` : ""}
              </span>
            </button>
          ))}
      </div>
      <div className="form-grid" style={{ marginTop: 10 }}>
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
      </div>
      <details className="more">
        <summary>Custom size and duration</summary>
        <div className="col" style={{ marginTop: 8 }}>
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
              <p className="small secondary">{custom.help.replace(/`/g, "")}</p>
              <div className="form-grid">
                {custom.kind !== "regime" ? (
                  <label className="field">
                    <span>{pctKinds.includes(custom.kind) ? "Size (%)" : "Multiplier (x)"}</span>
                    <input className="input num" type="number" value={size} onChange={(e) => setSize(e.target.value === "" ? "" : Number(e.target.value))} />
                  </label>
                ) : null}
                {!["gap", "break_pair"].includes(custom.kind) ? (
                  <label className="field">
                    <span>Duration (bars)</span>
                    <input className="input num" type="number" min={1} max={500} value={bars} onChange={(e) => setBars(e.target.value === "" ? "" : Number(e.target.value))} />
                  </label>
                ) : null}
              </div>
              <div>
                <button
                  className="btn primary"
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
  );
}

function EventFeed({ run, agentColors }: { run: ArenaState; agentColors: Record<string, string> }) {
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
  return (
    <div className="card">
      <div className="card-header">
        <h2>What the strategies did, and why</h2>
      </div>
      <div className="row" style={{ marginBottom: 8 }}>
        <select className="input" style={{ width: 220 }} value={who} onChange={(e) => setWho(e.target.value)} aria-label="Strategy filter">
          <option value="all">All strategies</option>
          {run.agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        {(["fill", "signal", "market"] as const).map((k) => (
          <label key={k} className="check small">
            <input type="checkbox" checked={kinds[k]} onChange={(e) => setKinds({ ...kinds, [k]: e.target.checked })} />
            {{ fill: "Trades", signal: "Signals", market: "Market events" }[k]}
          </label>
        ))}
      </div>
      <ol className="event-feed" aria-live="off">
        {items.length === 0 ? <li className="muted small">Nothing yet. Events appear as bars arrive.</li> : null}
        {items.map((e, i) => (
          <li key={`${e.t}-${e.agent}-${e.type}-${e.symbol}-${i}`} className={`ev ev-${e.type}`}>
            <div className="ev-head">
              <span className="muted small nowrap">{run.feed.timeframe === "1d" ? fmtDate(e.time) : fmtDateTime(e.time)}</span>
              {e.agent ? (
                <span className="row tight" style={{ flexWrap: "nowrap" }}>
                  <span className="swatch-line" style={{ background: agentColors[e.agent] }} />
                  <b>{names[e.agent] ?? e.agent}</b>
                </span>
              ) : (
                <b>{e.type === "injected" ? "You" : "Market"}</b>
              )}
            </div>
            <div className="ev-body">{eventText(e)}</div>
            {e.type === "fill" && e.reason ? <div className="ev-why">{e.reason}</div> : null}
            {e.type === "signal" && e.headline ? <div className="ev-why">{e.headline}</div> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

function eventText(e: ArenaEvent) {
  if (e.type === "fill") {
    const verb = e.side === "buy" ? "Bought" : "Sold";
    const pos = isNum(e.position) ? (e.position === 0 ? "now flat" : `now ${e.position > 0 ? "long" : "short"} ${fmtNum(Math.abs(e.position), 0)}`) : "";
    return (
      <>
        <span className={e.side === "buy" ? "upc" : "downc"}>{verb}</span> {fmtNum(e.qty ?? 0, 0)} {e.symbol} @ {fmtPrice(e.price)}{" "}
        <span className="muted small">({pos})</span>
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

function AgentPanel({ run, agentId, color, onReload }: { run: ArenaState; agentId: string; color?: string; onReload: () => void }) {
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
  return (
    <div className="card">
      <div className="card-header">
        <h2 className="row tight">
          <span className="swatch-line" style={{ background: color }} />
          {live.name}
        </h2>
        {live.kind === "strategy" ? <EvidenceBadge level={live.evidence} /> : <span className="badge neutral">Benchmark</span>}
      </div>
      <div className="tiles" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))" }}>
        <StatTile label="Equity" value={fmtMoney(live.equity)} />
        <StatTile label="Return" value={fmtPct(live.return)} valueClass={pnlClass(live.return)} />
        <StatTile label="Drawdown" value={fmtPct(live.drawdown, 1, false)} />
        <StatTile label="Exposure" value={fmtPct(live.gross, 0, false)} help="Gross exposure (long + short) as a share of equity." />
        <StatTile label="Closed trades" value={String(live.trades)} />
        <StatTile
          label="Costs + financing"
          value={detail ? fmtMoney(detail.costs + detail.financing) : "–"}
          help="Slippage and commissions, plus borrow fees on shorts and interest on borrowed cash."
        />
      </div>
      <ErrorBox error={error} />
      <h3 className="section-title" style={{ marginTop: 12 }}>
        Current view per symbol
      </h3>
      {!detail ? (
        <Spinner />
      ) : (
        <div className="col" style={{ gap: 10 }}>
          {live.symbols.map((s) => {
            const ex = detail.explanations[s];
            const w = live.weights[s];
            const state = live.signals[s];
            return (
              <div key={s} className="vote-card">
                <div className="vote-head">
                  <span className="title">{s}</span>
                  <VoteGlyph vote={state === "long" ? 1 : state === "short" ? -1 : 0} state={state} />
                  <span className="small muted">
                    weight {fmtPct(w ?? 0, 0)}
                    {live.pending[s] ? ` · order pending ${fmtNum(live.pending[s], 0)} sh` : ""}
                  </span>
                </div>
                {ex ? (
                  <>
                    <div className="small" style={{ marginTop: 4 }}>
                      {ex.headline}
                    </div>
                    {ex.rules.length ? (
                      <ul className="rules">
                        {ex.rules.map((r) => (
                          <li key={r.label}>
                            <span className={`mark ${r.passed === true ? "ok" : r.passed === false ? "no" : ""}`} aria-hidden="true">
                              {r.passed === true ? "✓" : r.passed === false ? "✗" : "·"}
                            </span>
                            <span>{r.label}</span>
                            <span className="val">{r.value}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {ex.exit_rule ? <div className="small muted">{ex.exit_rule}</div> : null}
                  </>
                ) : (
                  <div className="small muted">No explanation yet.</div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {detail && detail.trades_list.length ? (
        <>
          <h3 className="section-title" style={{ marginTop: 12 }}>
            Trades
          </h3>
          <CompactTrades trades={detail.trades_list} />
        </>
      ) : null}
    </div>
  );
}

function CompactTrades({ trades }: { trades: Trade[] }) {
  const rows = [...trades].reverse().slice(0, 40);
  return (
    <div className="table-wrap">
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
              <td>{t.symbol}</td>
              <td>{t.direction === "long" ? "Long" : "Short"}</td>
              <td className="nowrap">{fmtDate(t.entry_time)}</td>
              <td className="nowrap">{t.is_open ? <span className="badge neutral">Open</span> : fmtDate(t.exit_time)}</td>
              <td className={`num ${pnlClass(t.pnl)}`}>{fmtMoney(t.pnl, 0, true)}</td>
              <td className={`num ${pnlClass(t.return_pct)}`}>{fmtPct(t.return_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SummaryCard({ run, colors, onNew, onOpen }: {
  run: ArenaState;
  colors: Record<string, string>;
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
  return (
    <div className="card">
      <div className="card-header">
        <h2>Results after {s.bars} bars</h2>
        <div className="row tight">
          {run.feed.source === "simulated" ? (
            <>
              <button className="btn primary" disabled={busy} onClick={() => void rerun(false)}>
                Run again on a new market
              </button>
              <button className="btn" disabled={busy} onClick={() => void rerun(true)} title="Same seed: the same prices, so you can compare parameter changes fairly.">
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
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>#</th>
              <th>Strategy</th>
              <th className="num">Return</th>
              <th className="num">CAGR</th>
              <th className="num">Sharpe</th>
              <th className="num">
                Prob. Sharpe &gt; 0 <Help text="Probabilistic Sharpe Ratio: how likely the true Sharpe ratio is positive given this sample's length and fat tails." />
              </th>
              <th className="num">Max DD</th>
              <th className="num">Trades</th>
              <th className="num hide-sm">Win rate</th>
              <th className="num hide-sm">Time in market</th>
              <th className="num hide-sm">Costs</th>
            </tr>
          </thead>
          <tbody>
            {s.leaderboard.map((r, i) => (
              <tr key={r.id}>
                <td>{i + 1}</td>
                <td>
                  <span className="row tight" style={{ flexWrap: "nowrap" }}>
                    <span className="swatch-line" style={{ background: colors[r.id] }} />
                    {r.name}
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
      {s.regimes && s.regimes.length ? (
        <>
          <h3 className="section-title" style={{ marginTop: 14 }}>
            Return in each hidden regime{" "}
            <Help text="Each account's compounded return over the bars spent in each regime. Trend followers tend to earn in long trends and bleed in chop; mean-reversion is the opposite." />
          </h3>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Strategy</th>
                  {s.regimes.map((g) => (
                    <th key={g.regime} className="num">
                      <RegimeBadge regime={g.regime} drift={g.drift ?? undefined} /> <span className="muted small">({g.bars} bars)</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {s.leaderboard.map((r) => (
                  <tr key={r.id}>
                    <td>{names[r.id] ?? r.name}</td>
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
        </>
      ) : null}
      {s.cautions.length ? (
        <div style={{ marginTop: 12 }}>
          <Warnings items={s.cautions} title="Before you draw conclusions" />
        </div>
      ) : null}
    </div>
  );
}

