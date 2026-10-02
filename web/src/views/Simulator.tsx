import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type OrderRequest } from "../api";
import { useApp, type Route } from "../state";
import StrategySim from "./StrategySim";
import type { Bars, Meta, Settings, SimHistoryRow, SimState } from "../types";
import PriceChart, { type PriceLineSpec, type PriceMarker } from "../components/PriceChart";
import LineChart, { type LineSpec } from "../components/LineChart";
import {
  DirectionBadge,
  ErrorBox,
  Help,
  ReferenceList,
  Spinner,
  StatusDot,
  Tabs,
  TradesTable,
  VoteGlyph,
} from "../components/ui";
import { fmtDate, fmtMoney, fmtNum, fmtPct, fmtPrice, isNum, pnlClass } from "../format";
import { useChartColors } from "../theme";

export default function Simulator({ route }: { route: Route }) {
  const { navigate } = useApp();
  const practice = route.params.get("mode") === "practice" || route.params.has("symbol");
  const runId = route.params.get("run");
  return (
    <div className="stack">
      <Tabs
        tabs={[
          ["strategies", "Test strategies"],
          ["practice", "Practice trading yourself"],
        ]}
        value={practice ? "practice" : "strategies"}
        onChange={(m) => navigate("/sim", m === "practice" ? { mode: "practice" } : {})}
      />
      {practice ? (
        <PracticeSimulator route={route} />
      ) : (
        <StrategySim
          runId={runId}
          initialSource={route.params.get("source")}
          initialStrategies={route.params.get("strategies")}
          onOpen={(id) => navigate("/sim", { run: id })}
          onClose={() => navigate("/sim")}
        />
      )}
    </div>
  );
}

function PracticeSimulator({ route }: { route: Route }) {
  const [state, setState] = useState<SimState | null>(null);
  const [bars, setBars] = useState<Bars | null>(null);
  const [lastConfig, setLastConfig] = useState<Record<string, unknown> | null>(null);

  const accept = useCallback((s: SimState) => {
    setState(s);
    setBars((prev) => mergeBars(prev, s));
  }, []);

  if (!state || !bars) {
    return (
      <SimSetup
        initialSymbol={route.params.get("symbol")}
        onStart={(s, cfg) => {
          setLastConfig(cfg);
          setBars(null);
          accept(s);
        }}
      />
    );
  }
  if (state.finished) {
    return (
      <ScorecardView
        state={state}
        bars={bars}
        onNew={() => {
          setState(null);
          setBars(null);
        }}
        onReplay={
          lastConfig
            ? async () => {
                const { seed: _seed, ...rest } = lastConfig as Record<string, unknown> & { seed?: number };
                void _seed;
                const s = await api.simCreate(rest);
                setBars(null);
                accept(s);
              }
            : undefined
        }
      />
    );
  }
  return <SessionView state={state} bars={bars} accept={accept} />;
}

function mergeBars(prev: Bars | null, s: SimState): Bars {
  if (!prev || s.bars_from === 0) return s.bars;
  const keep = s.bars_from;
  const cut = <T,>(a: T[], b: T[]) => [...a.slice(0, keep), ...b];
  return {
    t: cut(prev.t, s.bars.t),
    o: cut(prev.o, s.bars.o),
    h: cut(prev.h, s.bars.h),
    l: cut(prev.l, s.bars.l),
    c: cut(prev.c, s.bars.c),
    v: cut(prev.v, s.bars.v),
  };
}

function atr(bars: Bars, n = 20): number | null {
  const len = bars.c.length;
  if (len < n + 1) return null;
  let sum = 0;
  for (let i = len - n; i < len; i++) {
    const h = bars.h[i] as number;
    const l = bars.l[i] as number;
    const pc = bars.c[i - 1] as number;
    sum += Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }
  return sum / n;
}

// ------------------------------------------------------------------------------------
// Setup
// ------------------------------------------------------------------------------------

function SimSetup({ onStart, initialSymbol }: { onStart: (s: SimState, cfg: Record<string, unknown>) => void; initialSymbol: string | null }) {
  const { meta, settings } = useApp() as { meta: Meta; settings: Settings };
  const [source, setSource] = useState<"scenario" | "history">(initialSymbol ? "history" : "scenario");
  const [scenario, setScenario] = useState("random");
  const [preset, setPreset] = useState<string | null>(initialSymbol ? null : "covid");
  const [symbol, setSymbol] = useState(initialSymbol ?? "");
  const [start, setStart] = useState("");
  const [blind, setBlind] = useState(true);
  const realProviders = meta.providers.filter((p) => p.id !== "synthetic" && p.configured);
  const [provider, setProvider] = useState(settings.provider !== "synthetic" ? settings.provider : "yahoo");
  const [cash, setCash] = useState(settings.account_equity); // your account profile
  const [length, setLength] = useState(250);
  const [reveal, setReveal] = useState<"live" | "end" | "off">("end");
  const [allowShort, setAllowShort] = useState(false);
  const [slippage, setSlippage] = useState(settings.slippage_bps);
  const singles = meta.strategies.filter((s) => s.kind === "single" && s.id !== "buy_hold");
  const [advisors, setAdvisors] = useState<string[]>(meta.default_advisors.map((a) => a.id));
  const [history, setHistory] = useState<SimHistoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.simHistory().then(setHistory).catch(() => undefined);
  }, []);

  const start_ = async () => {
    setLoading(true);
    setError(null);
    const cfg: Record<string, unknown> = {
      source,
      scenario,
      initial_cash: cash,
      fractional: settings.fractional_shares,
      length_bars: length,
      reveal,
      allow_short: allowShort,
      slippage_bps: slippage,
      commission_bps: settings.commission_bps,
      advisors: advisors.map((id) => ({ id })),
    };
    if (source === "history") {
      Object.assign(cfg, { provider, blind, preset: preset ?? undefined, symbol: preset ? "" : symbol, start: preset ? undefined : start || undefined });
    }
    try {
      const s = await api.simCreate(cfg);
      onStart(s, cfg);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="stack">
      <div className="card">
        <div className="card-header">
          <div>
            <h1>Practice trading yourself</h1>
            <p className="secondary" style={{ marginTop: 4 }}>
              Practise decisions one bar at a time with a paper account. The future stays hidden. Race the strategies, then get a
              scorecard that grades your <b>process</b> separately from your <b>outcome</b>.
            </p>
          </div>
        </div>
        <Tabs
          tabs={[
            ["scenario", "Synthetic scenario"],
            ["history", "Real history"],
          ]}
          value={source}
          onChange={setSource}
        />
        {source === "scenario" ? (
          <div className="scenario-grid">
            {meta.scenarios.map((s) => (
              <button key={s.id} className="option-card" aria-pressed={scenario === s.id} onClick={() => setScenario(s.id)}>
                <span className="row tight">
                  <span className="title">{s.label}</span>
                  <span className="badge neutral">{s.difficulty}</span>
                </span>
                <span className="desc">{s.description}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="col">
            {!realProviders.length ? (
              <div className="callout warn">Real history needs a data provider. Yahoo Finance works without a key.</div>
            ) : null}
            <div className="scenario-grid">
              {meta.presets.map((p) => (
                <button key={p.id} className="option-card" aria-pressed={preset === p.id} onClick={() => setPreset(p.id)}>
                  <span className="title">{p.label}</span>
                  <span className="desc">
                    {blind ? "Hidden until the end" : `${p.symbol} from ${p.start}`}. {blind ? "" : p.description}
                  </span>
                </button>
              ))}
              <button className="option-card" aria-pressed={preset === null} onClick={() => setPreset(null)}>
                <span className="title">Custom or random</span>
                <span className="desc">Pick a ticker and start date, or leave them blank for a random large company and year.</span>
              </button>
            </div>
            {preset === null ? (
              <div className="row">
                <label className="field">
                  <span>Symbol (blank = random)</span>
                  <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="e.g. AAPL" />
                </label>
                <label className="field">
                  <span>Session start (blank = random)</span>
                  <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                </label>
              </div>
            ) : null}
            <div className="row">
              <label className="field">
                <span>Data</span>
                <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
                  {meta.providers
                    .filter((p) => p.id !== "synthetic")
                    .map((p) => (
                      <option key={p.id} value={p.id} disabled={!p.configured}>
                        {p.label}
                      </option>
                    ))}
                </select>
              </label>
              <label className="check" title="Hides the ticker and dates and rescales prices to 100, so you can't rely on remembering what happened.">
                <input type="checkbox" checked={blind} onChange={(e) => setBlind(e.target.checked)} />
                Blind mode (recommended: prevents hindsight bias)
              </label>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h3 className="section-title">Session options</h3>
        <div className="form-grid">
          <label className="field">
            <span>Starting cash ($)</span>
            <input className="input num" type="number" value={cash} min={100} onChange={(e) => setCash(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Length (trading days)</span>
            <select className="input" value={length} onChange={(e) => setLength(Number(e.target.value))}>
              <option value={60}>60 (a quarter)</option>
              <option value={125}>125 (six months)</option>
              <option value={250}>250 (one year)</option>
              <option value={500}>500 (two years)</option>
            </select>
          </label>
          <label className="field">
            <span>Show strategy advice</span>
            <select className="input" value={reveal} onChange={(e) => setReveal(e.target.value as "live" | "end" | "off")}>
              <option value="end">After the session (decide for yourself first)</option>
              <option value="live">While trading</option>
              <option value="off">Never</option>
            </select>
          </label>
          <label className="field" title="Adverse price impact per fill.">
            <span>Slippage (bps)</span>
            <input className="input num" type="number" step={0.5} value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} />
          </label>
        </div>
        <label className="check" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={allowShort} onChange={(e) => setAllowShort(e.target.checked)} />
          Allow short selling
        </label>
        <h3 className="section-title" style={{ marginTop: 14 }}>
          Strategies to race against
        </h3>
        <div className="chips">
          {singles.map((s) => (
            <label key={s.id} className="check" style={{ marginRight: 12 }}>
              <input
                type="checkbox"
                checked={advisors.includes(s.id)}
                onChange={(e) => setAdvisors(e.target.checked ? [...advisors, s.id] : advisors.filter((x) => x !== s.id))}
              />
              {s.name}
            </label>
          ))}
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={() => void start_()} disabled={loading}>
            {loading ? "Preparing..." : "Start session"}
          </button>
          {loading ? <Spinner label={source === "history" ? "Downloading history..." : "Generating market..."} /> : null}
        </div>
        <ErrorBox error={error} />
      </div>

      {history.length ? (
        <div className="card">
          <div className="card-header">
            <h2>Your progress</h2>
            <span className="sub">Completed sessions (stored locally)</span>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Session</th>
                  <th className="num">Bars</th>
                  <th className="num">Your return</th>
                  <th className="num">Buy &amp; hold</th>
                  <th className="num">Max DD</th>
                  <th className="num">Trades</th>
                  <th className="num">Process score</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td className="nowrap">{new Date(h.finished_at * 1000).toLocaleDateString()}</td>
                    <td>
                      {h.title} <span className="muted small">{h.symbol}</span>
                    </td>
                    <td className="num">{h.bars}</td>
                    <td className={`num ${pnlClass(h.return)}`}>{fmtPct(h.return)}</td>
                    <td className="num">{fmtPct(h.benchmark_return)}</td>
                    <td className="num">{fmtPct(h.max_drawdown)}</td>
                    <td className="num">{fmtNum(h.trades, 0)}</td>
                    <td className="num">{h.process_score ?? "–"}</td>
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

// ------------------------------------------------------------------------------------
// Trading screen
// ------------------------------------------------------------------------------------

function SessionView({ state, bars, accept }: { state: SimState; bars: Bars; accept: (s: SimState) => void }) {
  const colors = useChartColors();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [lastFills, setLastFills] = useState<string | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const step = useCallback(
    async (n: number) => {
      const s = stateRef.current;
      if (s.finished) return;
      setBusy(true);
      try {
        const next = await api.simStep(s.id, n, s.cursor);
        accept(next);
        if (next.new_fills?.length) {
          setLastFills(
            next.new_fills
              .map((f) => `${f.side === "buy" ? "Bought" : "Sold"} ${f.qty} @ ${fmtPrice(f.price)}${f.tag !== "entry" && f.tag !== "exit" ? ` (${f.tag.replace("_", "-")})` : ""}`)
              .join("; "),
          );
        }
        setError(null);
      } catch (e) {
        setError((e as Error).message);
        setPlaying(false);
      } finally {
        setBusy(false);
      }
    },
    [accept],
  );

  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      if (!stateRef.current.finished) void step(1);
    }, 1000 / speed);
    return () => window.clearInterval(id);
  }, [playing, speed, step]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowRight" || e.key === "n") {
        e.preventDefault();
        void step(1);
      } else if (e.key === "p") {
        setPlaying((p) => !p);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  const finish = async () => {
    setPlaying(false);
    try {
      accept(await api.simFinish(state.id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const markers: PriceMarker[] = useMemo(
    () =>
      state.fills.map((f) => ({
        time: f.time,
        position: f.qty > 0 ? "belowBar" : "aboveBar",
        shape: f.qty > 0 ? "arrowUp" : "arrowDown",
        color: f.qty > 0 ? colors.up : colors.down,
        text: `${f.qty > 0 ? "B" : "S"} ${Math.abs(f.qty)}`,
      })),
    [state.fills, colors],
  );
  const pos = state.account.position;
  const priceLines: PriceLineSpec[] = useMemo(() => {
    const lines: PriceLineSpec[] = [];
    if (pos.qty && isNum(pos.avg_price)) lines.push({ price: pos.avg_price, label: "Avg entry", color: colors.textSecondary, style: "solid" });
    state.orders
      .filter((o) => o.status === "open")
      .forEach((o) => {
        const price = o.type === "limit" ? o.limit_price : o.stop_price;
        if (isNum(price)) {
          const label = o.tag === "stop_loss" ? "Stop-loss" : o.tag === "take_profit" ? "Take-profit" : `${o.side} ${o.type}`;
          lines.push({ price, label, color: colors.axisText, style: o.type === "stop" ? "dashed" : "dotted" });
        }
      });
    return lines;
  }, [state.orders, pos.qty, pos.avg_price, colors]);

  const pct = state.progress.total ? state.progress.done / state.progress.total : 0;
  const lastClose = bars.c[bars.c.length - 1] as number;
  const a = atr(bars);

  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <h1>
              {state.title} <span className="muted" style={{ fontWeight: 400, fontSize: 14 }}>{state.symbol}</span>
            </h1>
            <div className="small secondary">{state.description}</div>
          </div>
          <div className="row">
            <div className="col" style={{ gap: 4, minWidth: 180 }}>
              <span className="small muted">
                Bar {state.progress.done} of {state.progress.total} · {fmtDate(state.time)}
              </span>
              <div className="progress" role="progressbar" aria-valuenow={state.progress.done} aria-valuemax={state.progress.total}>
                <div style={{ width: `${pct * 100}%` }} />
              </div>
            </div>
            <button className="btn primary" onClick={() => void step(1)} disabled={busy || playing}>
              Next bar <span className="kbd">N</span>
            </button>
            <button className="btn" onClick={() => void step(5)} disabled={busy || playing}>
              +5
            </button>
            <button className="btn" onClick={() => void step(20)} disabled={busy || playing}>
              +20
            </button>
            <button className="btn" onClick={() => setPlaying(!playing)} aria-pressed={playing}>
              {playing ? "Pause" : "Play"} <span className="kbd">P</span>
            </button>
            <select className="input" style={{ width: 110 }} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
              <option value={1}>1 bar/s</option>
              <option value={2}>2 bars/s</option>
              <option value={4}>4 bars/s</option>
            </select>
            <button className="btn ghost" onClick={() => void finish()}>
              Finish
            </button>
          </div>
        </div>
      </div>
      <ErrorBox error={error} />
      {lastFills ? <div className="callout info">Filled: {lastFills}</div> : null}

      <div className="split side-right">
        <div className="stack">
          <div className="card">
            <PriceChart bars={bars} markers={markers} priceLines={priceLines} height={380} follow initialBars={180} label="Simulator price chart" />
          </div>
          <RaceCard state={state} />
          <div className="card">
            <div className="card-header">
              <h2>Your trades</h2>
            </div>
            <TradesTable trades={state.trades} showSymbol={false} />
          </div>
        </div>
        <div className="stack">
          <AccountCard state={state} />
          <OrderTicket state={state} lastClose={lastClose} atrValue={a} accept={accept} />
          <OrdersCard state={state} accept={accept} />
          {state.config.reveal === "live" ? <AdvisorsCard state={state} /> : (
            <div className="card small secondary">
              {state.config.reveal === "end"
                ? "The strategies' opinions are hidden until the end, so your decisions are your own. You'll see how they traded in the scorecard."
                : "Strategy advice is turned off for this session."}
            </div>
          )}
          <JournalCard state={state} />
        </div>
      </div>
    </div>
  );
}

function AccountCard({ state }: { state: SimState }) {
  const a = state.account;
  const p = a.position;
  return (
    <div className="card">
      <div className="card-header">
        <h2>Account</h2>
        <span className={`${pnlClass(a.return_pct)}`} style={{ fontWeight: 600 }}>
          {fmtPct(a.return_pct, 2)}
        </span>
      </div>
      <dl className="kv">
        <dt>Equity</dt>
        <dd>{fmtMoney(a.equity)}</dd>
        <dt>Cash</dt>
        <dd>{fmtMoney(a.cash)}</dd>
        <dt>Buying power</dt>
        <dd>{fmtMoney(a.buying_power)}</dd>
        <dt>Position</dt>
        <dd>{p.qty ? `${p.qty > 0 ? "Long" : "Short"} ${Math.abs(p.qty).toLocaleString()}` : "Flat"}</dd>
        {p.qty ? (
          <>
            <dt>Avg price</dt>
            <dd>{fmtPrice(p.avg_price)}</dd>
            <dt>Unrealised P&amp;L</dt>
            <dd className={pnlClass(p.unrealized_pnl)}>{fmtMoney(p.unrealized_pnl, 0, true)}</dd>
          </>
        ) : null}
        <dt>Realised P&amp;L</dt>
        <dd className={pnlClass(a.realized_pnl)}>{fmtMoney(a.realized_pnl, 0, true)}</dd>
        <dt>Costs paid</dt>
        <dd>{fmtMoney(a.costs)}</dd>
      </dl>
    </div>
  );
}

function OrderTicket({ state, lastClose, atrValue, accept }: {
  state: SimState;
  lastClose: number;
  atrValue: number | null;
  accept: (s: SimState) => void;
}) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState<number>(0);
  const [type, setType] = useState<"market" | "limit" | "stop">("market");
  const [price, setPrice] = useState<string>("");
  const [useStop, setUseStop] = useState(true);
  const [stopLoss, setStopLoss] = useState<string>("");
  const [useTarget, setUseTarget] = useState(false);
  const [target, setTarget] = useState<string>("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pos = state.account.position.qty;
  const exiting = (side === "sell" && pos > 0) || (side === "buy" && pos < 0);
  const entryRef = type === "market" ? lastClose : Number(price) || lastClose;

  // Reset protective levels around the current price when the side changes.
  useEffect(() => {
    if (atrValue) {
      const dir = side === "buy" ? 1 : -1;
      setStopLoss((lastClose - dir * 2 * atrValue).toFixed(2));
      setTarget((lastClose + dir * 3 * atrValue).toFixed(2));
    }
  }, [side, state.cursor]);

  const riskSize = () => {
    if (!atrValue) return;
    const stop = Number(stopLoss) || (side === "buy" ? entryRef - 2 * atrValue : entryRef + 2 * atrValue);
    const perShare = Math.abs(entryRef - stop);
    if (perShare <= 0) return;
    const shares = Math.floor((0.01 * state.account.equity) / perShare);
    const cap = Math.floor(state.account.buying_power / entryRef);
    setQty(Math.max(0, Math.min(shares, cap)));
  };
  const fraction = (f: number) => setQty(Math.max(0, Math.floor((state.account.buying_power * f) / entryRef)));

  const submit = async () => {
    setError(null);
    setBusy(true);
    const order: OrderRequest = { side, qty, type, tif: "gtc", note };
    if (type === "limit") order.limit_price = Number(price);
    if (type === "stop") order.stop_price = Number(price);
    if (!exiting && useStop && stopLoss) order.stop_loss = Number(stopLoss);
    if (!exiting && useTarget && target) order.take_profit = Number(target);
    try {
      const res = await api.simOrder(state.id, order);
      accept(res.state);
      setNote("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const est = qty * entryRef;
  const risk = !exiting && useStop && stopLoss ? qty * Math.abs(entryRef - Number(stopLoss)) : null;

  return (
    <div className="card">
      <div className="card-header">
        <h2>Order ticket</h2>
        <span className="sub">fills at the next bar</span>
      </div>
      <div className="col">
        <div className="segmented buy-sell" role="group" aria-label="Side">
          <button data-side="buy" aria-pressed={side === "buy"} onClick={() => setSide("buy")}>
            Buy
          </button>
          <button data-side="sell" aria-pressed={side === "sell"} onClick={() => setSide("sell")}>
            Sell{pos > 0 ? "" : state.config.allow_short ? " short" : ""}
          </button>
        </div>
        <label className="field">
          <span>Shares</span>
          <input className="input num" type="number" min={0} value={qty || ""} onChange={(e) => setQty(Math.max(0, Math.floor(Number(e.target.value))))} />
        </label>
        <div className="row tight">
          {exiting ? (
            <button className="btn small" onClick={() => setQty(Math.abs(pos))}>
              Whole position
            </button>
          ) : (
            <>
              <button className="btn small" onClick={riskSize} disabled={!atrValue} title="Size so that hitting the stop loses 1% of equity">
                Risk 1%
              </button>
              <button className="btn small" onClick={() => fraction(0.25)}>
                25%
              </button>
              <button className="btn small" onClick={() => fraction(0.5)}>
                50%
              </button>
              <button className="btn small" onClick={() => fraction(0.99)}>
                All
              </button>
            </>
          )}
        </div>
        <div className="row tight">
          <label className="field grow">
            <span>Type</span>
            <select className="input" value={type} onChange={(e) => setType(e.target.value as "market" | "limit" | "stop")}>
              <option value="market">Market (next open)</option>
              <option value="limit">Limit</option>
              <option value="stop">Stop</option>
            </select>
          </label>
          {type !== "market" ? (
            <label className="field grow">
              <span>{type === "limit" ? "Limit price" : "Stop price"}</span>
              <input className="input num" type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={lastClose.toFixed(2)} />
            </label>
          ) : null}
        </div>
        {!exiting ? (
          <>
            <div className="row tight">
              <label className="check">
                <input type="checkbox" checked={useStop} onChange={(e) => setUseStop(e.target.checked)} />
                Stop-loss
              </label>
              <input className="input num" style={{ width: 110 }} type="number" step="0.01" value={stopLoss} disabled={!useStop} onChange={(e) => setStopLoss(e.target.value)} aria-label="Stop-loss price" />
              <Help text="Default: 2 x ATR(20) from the last close, a common volatility-based stop. It becomes an order that closes the position if hit." />
            </div>
            <div className="row tight">
              <label className="check">
                <input type="checkbox" checked={useTarget} onChange={(e) => setUseTarget(e.target.checked)} />
                Take-profit
              </label>
              <input className="input num" style={{ width: 110 }} type="number" step="0.01" value={target} disabled={!useTarget} onChange={(e) => setTarget(e.target.value)} aria-label="Take-profit price" />
            </div>
          </>
        ) : null}
        <label className="field">
          <span>Why are you making this trade?</span>
          <textarea className="input" rows={2} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="Your reasoning, written before you know the outcome" />
        </label>
        <div className="small muted">
          ≈ {fmtMoney(est)} at {fmtPrice(entryRef)}
          {risk !== null ? ` · loses ≈ ${fmtMoney(risk)} (${fmtPct(risk / state.account.equity, 1, false)} of equity) if the stop is hit` : ""}
        </div>
        <button className={`btn ${side === "buy" ? "buy" : "sell"}`} onClick={() => void submit()} disabled={busy || qty <= 0}>
          {side === "buy" ? "Place buy order" : "Place sell order"}
        </button>
        <ErrorBox error={error} />
      </div>
    </div>
  );
}

function OrdersCard({ state, accept }: { state: SimState; accept: (s: SimState) => void }) {
  const [error, setError] = useState<string | null>(null);
  const open = state.orders.filter((o) => o.status === "open" || o.status === "pending");
  const recent = state.orders.filter((o) => !(o.status === "open" || o.status === "pending")).slice(-6).reverse();
  const cancel = async (id: string) => {
    try {
      accept((await api.simCancel(state.id, id)).state);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const close = async () => {
    try {
      accept((await api.simClose(state.id)).state);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="card">
      <div className="card-header">
        <h2>Orders</h2>
        {state.account.position.qty ? (
          <button className="btn small" onClick={() => void close()}>
            Close position
          </button>
        ) : null}
      </div>
      <ErrorBox error={error} />
      {!open.length && !recent.length ? <div className="muted small">No orders yet.</div> : null}
      {open.map((o) => (
        <div key={o.id} className="row" style={{ justifyContent: "space-between", padding: "4px 0" }}>
          <span className="small">
            <b>{o.side === "buy" ? "Buy" : "Sell"}</b> {o.qty} {o.type}
            {o.limit_price ? ` @ ${fmtPrice(o.limit_price)}` : ""}
            {o.stop_price ? ` stop ${fmtPrice(o.stop_price)}` : ""}
            <span className="muted"> · {o.tag.replace("_", "-")}{o.status === "pending" ? " (waits for entry)" : ""}</span>
          </span>
          <button className="btn small ghost" onClick={() => void cancel(o.id)}>
            Cancel
          </button>
        </div>
      ))}
      {recent.length ? (
        <details className="more">
          <summary>Recent</summary>
          {recent.map((o) => (
            <div key={o.id} className="small secondary" style={{ padding: "2px 0" }}>
              {o.side} {o.filled_qty || o.qty} {o.type} · {o.status}
              {o.fill_price ? ` @ ${fmtPrice(o.fill_price)}` : ""}
              {o.reason ? ` · ${o.reason}` : ""}
            </div>
          ))}
        </details>
      ) : null}
    </div>
  );
}

function AdvisorsCard({ state }: { state: SimState }) {
  const c = state.consensus;
  return (
    <div className="card">
      <div className="card-header">
        <h2>Strategy advice</h2>
        {isNum(c) ? <DirectionBadge score={c} label={c >= 0.2 ? "Lean long" : c <= -0.2 ? "Lean short" : "Mixed"} /> : null}
      </div>
      {state.advisors.map((a) => (
        <div key={a.id} className="vote-card">
          <div className="vote-head">
            <span className="title small">{a.name}</span>
            <VoteGlyph vote={Math.sign(a.explanation.signal)} state={a.explanation.state} />
          </div>
          <div className="small secondary" style={{ marginTop: 4 }}>
            {a.explanation.headline}
          </div>
        </div>
      ))}
    </div>
  );
}

function JournalCard({ state }: { state: SimState }) {
  const [text, setText] = useState("");
  const [journal, setJournal] = useState(state.journal);
  useEffect(() => setJournal(state.journal), [state.journal]);
  const add = async () => {
    if (!text.trim()) return;
    const res = await api.simJournal(state.id, text);
    setJournal(res.journal);
    setText("");
  };
  return (
    <div className="card">
      <div className="card-header">
        <h2>Journal</h2>
        <Help text="Write down what you see and plan before the next bar. Reviewing it later is how you learn whether you were skilled or lucky." />
      </div>
      <div className="row tight">
        <input className="input grow" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void add()} placeholder="Observation or plan" />
        <button className="btn small" onClick={() => void add()}>
          Add
        </button>
      </div>
      {journal.length ? (
        <ul className="small secondary" style={{ paddingLeft: 18, marginBottom: 0 }}>
          {journal
            .slice(-5)
            .reverse()
            .map((j, i) => (
              <li key={i}>
                <span className="muted">{fmtDate(j.time)}:</span> {j.text}
              </li>
            ))}
        </ul>
      ) : null}
    </div>
  );
}

function raceLines(state: SimState, colors: ReturnType<typeof useChartColors>): LineSpec[] {
  const t = state.equity_curve.t;
  const lines: LineSpec[] = [
    { id: "you", label: "You", data: state.equity_curve, color: colors.series[0] },
    { id: "bh", label: "Buy & hold", data: state.benchmark_curve, color: colors.deemph },
  ];
  state.advisors.slice(0, 5).forEach((a, i) => {
    lines.push({ id: a.id, label: a.name, data: { t, v: a.equity }, color: colors.series[i + 1] });
  });
  return lines;
}

function RaceCard({ state }: { state: SimState }) {
  const colors = useChartColors();
  const lines = useMemo(() => raceLines(state, colors), [state, colors]);
  const showGhosts = state.config.reveal === "live";
  const shown = showGhosts ? lines : lines.slice(0, 2);
  return (
    <div className="card">
      <div className="card-header">
        <h2>Equity race</h2>
        <span className="sub">{showGhosts ? "You vs buy-and-hold vs the strategies" : "You vs buy-and-hold"}</span>
      </div>
      <LineChart lines={shown} format={(v) => fmtMoney(v)} height={200} toggleable />
    </div>
  );
}

// ------------------------------------------------------------------------------------
// Scorecard
// ------------------------------------------------------------------------------------

const REGIME_UP = ["bull", "calm", "recovery", "melt-up", "up", "steady", "grind", "bear rally"];
const REGIME_DOWN = ["bear", "crash", "bust", "down"];

function ScorecardView({ state, bars, onNew, onReplay }: {
  state: SimState;
  bars: Bars;
  onNew: () => void;
  onReplay?: () => Promise<void>;
}) {
  const colors = useChartColors();
  const sc = state.scorecard;
  const lines = useMemo(() => raceLines(state, colors), [state, colors]);
  const markers: PriceMarker[] = useMemo(
    () =>
      state.fills.map((f) => ({
        time: f.time,
        position: f.qty > 0 ? "belowBar" : "aboveBar",
        shape: f.qty > 0 ? "arrowUp" : "arrowDown",
        color: f.qty > 0 ? colors.up : colors.down,
      })),
    [state.fills, colors],
  );
  if (!sc) return <Spinner />;
  const rv = state.reveal;
  const statusLabel = { good: "Good", warn: "Needs work", bad: "Problem", info: "Note" } as const;
  const total = Math.max(1, state.progress.total);
  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div className="small muted">Session complete · {state.title}</div>
            <div className={`hero ${pnlClass(sc.you.total_return)}`} style={{ marginTop: 6 }}>
              {fmtPct(sc.you.total_return, 1)}
            </div>
            <div className="secondary" style={{ marginTop: 6 }}>
              Buy &amp; hold: {fmtPct(sc.benchmark.total_return, 1)} · you finished <b>#{sc.rank}</b> of {sc.of}
            </div>
          </div>
          <div style={{ minWidth: 260, maxWidth: 420 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b>Process score</b>
              <span style={{ fontSize: 22, fontWeight: 700 }}>{sc.process_score ?? "–"}/100</span>
            </div>
            <div className="meter" style={{ marginTop: 6 }} role="meter" aria-valuenow={sc.process_score ?? 0} aria-valuemin={0} aria-valuemax={100}>
              <div style={{ width: `${sc.process_score ?? 0}%` }} />
            </div>
            <p className="secondary small" style={{ marginTop: 8 }}>
              {sc.verdict}
            </p>
          </div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          {onReplay ? (
            <button className="btn primary" onClick={() => void onReplay()}>
              Play again (new market, same settings)
            </button>
          ) : null}
          <button className="btn" onClick={onNew}>
            Set up a new session
          </button>
        </div>
      </div>

      {rv ? (
        <div className="card">
          <div className="card-header">
            <h2>The reveal</h2>
          </div>
          {rv.kind === "history" ? (
            <p>
              It was <b>{rv.symbol}</b> from <b>{rv.first_date}</b> to <b>{rv.last_date}</b>
              {rv.preset ? ` (${rv.preset})` : ""}. Data: {rv.provider}.
              {rv.blind ? " Prices were rescaled so the session started at 100." : ""}
            </p>
          ) : (
            <>
              <p className="secondary">
                The hidden market regimes during your session (synthetic scenario "{rv.label}", seed {rv.seed}):
              </p>
              <div className="regimes" role="img" aria-label="Regime timeline">
                {(rv.regimes ?? []).map((r, i) => {
                  const up = REGIME_UP.includes(r.regime);
                  const down = REGIME_DOWN.includes(r.regime);
                  return (
                    <div
                      key={i}
                      style={{
                        flex: Math.max(1, r.end - r.start + 1) / total,
                        background: up ? colors.upWash : down ? colors.downWash : undefined,
                      }}
                      title={`${r.regime}: bars ${r.start}-${r.end}`}
                    >
                      {r.end - r.start > total * 0.08 ? `${up ? "▲ " : down ? "▼ " : ""}${r.regime}` : ""}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      ) : null}

      <div className="grid-2">
        <div className="card">
          <div className="card-header">
            <h2>Equity race</h2>
          </div>
          <LineChart lines={lines} format={(v) => fmtMoney(v)} height={240} toggleable />
        </div>
        <div className="card">
          <div className="card-header">
            <h2>Leaderboard</h2>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Trader</th>
                  <th className="num">Return</th>
                  <th className="num">Max DD</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Trades</th>
                </tr>
              </thead>
              <tbody>
                {sc.leaderboard.map((r, i) => (
                  <tr key={r.name} className={r.kind === "you" ? "selected" : ""}>
                    <td className="num">{i + 1}</td>
                    <td>{r.kind === "you" ? <b>You</b> : r.name}</td>
                    <td className={`num ${pnlClass(r.total_return)}`}>{fmtPct(r.total_return)}</td>
                    <td className="num">{fmtPct(r.max_drawdown)}</td>
                    <td className="num">{fmtNum(r.sharpe)}</td>
                    <td className="num">{fmtNum(r.trades, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Process review</h2>
          <span className="sub">How you traded, independent of how the market happened to move</span>
        </div>
        <div className="diag-grid">
          {sc.diagnostics.map((d) => (
            <div key={d.id} className="diag">
              <div className="head">
                <span className="title">{d.title}</span>
                <StatusDot status={d.status}>{statusLabel[d.status]}</StatusDot>
              </div>
              <div style={{ fontWeight: 600, marginTop: 4 }}>{d.value}</div>
              <div className="small secondary" style={{ marginTop: 4 }}>
                {d.detail}
              </div>
              {d.lesson ? <div className="lesson">{d.lesson}</div> : null}
              {d.reference ? (
                <div className="ref">
                  <ReferenceList refs={[d.reference]} />
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Your fills on the full chart</h2>
        </div>
        <PriceChart bars={bars} markers={markers} height={320} label="Session chart with your fills" />
      </div>
      <div className="card">
        <div className="card-header">
          <h2>Trades and notes</h2>
        </div>
        <TradesTable trades={state.trades} showSymbol={false} />
      </div>
    </div>
  );
}
