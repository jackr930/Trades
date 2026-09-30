import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, liveSocketUrl } from "../api";
import { useApp } from "../state";
import type { ChartResponse, LiveSnapshot, Recommendation, Vote } from "../types";
import PriceChart, { type PriceMarker } from "../components/PriceChart";
import Sparkline from "../components/Sparkline";
import {
  DirectionBadge,
  ErrorBox,
  EvidenceBadge,
  Help,
  Spinner,
  StatusDot,
  SymbolInput,
  VoteGlyph,
} from "../components/ui";
import { dirClass, fmtCompact, fmtDate, fmtMoney, fmtNum, fmtPct, fmtPrice, isNum } from "../format";
import { useChartColors } from "../theme";

type WsState = "connecting" | "open" | "closed";

function useLiveFeed() {
  const [snap, setSnap] = useState<LiveSnapshot | null>(null);
  const [ws, setWs] = useState<WsState>("connecting");
  useEffect(() => {
    let socket: WebSocket | null = null;
    let stopped = false;
    let retry = 1000;
    let timer: number | undefined;
    const connect = () => {
      setWs("connecting");
      socket = new WebSocket(liveSocketUrl());
      socket.onopen = () => {
        setWs("open");
        retry = 1000;
      };
      socket.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string);
          if (msg.type === "update") setSnap(msg.data as LiveSnapshot);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        setWs("closed");
        if (!stopped) timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    api.live().then((s) => setSnap((cur) => cur ?? s)).catch(() => undefined);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, []);
  return { snap, setSnap, ws };
}

function marketText(s: LiveSnapshot): string {
  const m = s.market;
  if (s.demo) {
    return `Demo clock ${m.clock ?? ""} ET on ${m.session_date} (1 real second = ${Math.round((m.speed ?? 60) / 60)} simulated minute${(m.speed ?? 60) >= 120 ? "s" : ""})`;
  }
  const fmt = (iso?: string) =>
    iso
      ? new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit" }).format(
          new Date(iso),
        ) + " ET"
      : "";
  if (m.is_open) return `US market open · closes ${fmt(m.next_close)}`;
  const phase = m.phase === "pre" ? "Pre-market" : m.phase === "post" ? "After hours" : "US market closed";
  return `${phase} · opens ${fmt(m.next_open)}`;
}

export default function LiveDesk() {
  const { meta, settings, navigate, toast } = useApp();
  const { snap, setSnap, ws } = useLiveFeed();
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recs = useMemo(() => {
    const m = new Map<string, Recommendation>();
    snap?.recommendations.forEach((r) => m.set(r.symbol, r));
    return m;
  }, [snap]);

  useEffect(() => {
    if (!snap) return;
    if (!selected || !snap.symbols.includes(selected)) setSelected(snap.symbols[0] ?? null);
  }, [snap, selected]);

  const updateWatchlist = async (symbols: string[]) => {
    setError(null);
    try {
      setSnap(await api.setWatchlist(symbols));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggleLive = async () => {
    if (!snap) return;
    setBusy(true);
    try {
      setSnap(snap.running ? await api.liveStop() : await api.liveStart());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!snap || !meta || !settings) return <Spinner label="Starting the live feed..." />;
  const rec = selected ? recs.get(selected) : undefined;
  const quote = selected ? snap.quotes[selected] : undefined;
  const updated = snap.last_update ? new Date(snap.last_update).toLocaleTimeString() : "–";
  const noData = snap.status === "running" && !Object.keys(snap.quotes).length && Object.keys(snap.errors).length > 0;

  return (
    <div>
      <div className="statusbar">
        <StatusDot status={!snap.running ? "info" : snap.status === "error" ? "bad" : noData ? "warn" : snap.status === "running" ? "live" : "info"}>
          {!snap.running ? "Stopped" : snap.status === "error" ? "Error" : noData ? "No data" : snap.status === "running" ? "Live" : "Starting"}
        </StatusDot>
        <span className="item">
          Data: <b>{snap.provider_label}</b>
          {snap.realtime ? <span className="muted">({snap.realtime}{snap.streaming ? ", streaming" : ""})</span> : null}
        </span>
        <span className="item">{marketText(snap)}</span>
        <span className="item">Updated {updated}</span>
        <span className="item">
          <span className={`status ${ws === "open" ? "good" : "warn"}`}>
            <span className="dot" aria-hidden="true" />
            {ws === "open" ? "Connected" : ws === "connecting" ? "Connecting" : "Reconnecting"}
          </span>
        </span>
        <span className="grow" />
        <button className="btn small" onClick={toggleLive} disabled={busy}>
          {snap.running ? "Pause feed" : "Start feed"}
        </button>
        <button className="btn small" onClick={() => navigate("/settings")}>
          Data source
        </button>
        <button
          className="btn small"
          title="Let each strategy trade its own paper account on this market as bars complete"
          onClick={() => navigate("/sim", { source: snap.demo ? "simulated" : "realtime" })}
        >
          Forward-test strategies
        </button>
      </div>

      {snap.demo ? (
        <div className="callout info" style={{ marginBottom: 16 }}>
          You are looking at the <b>synthetic demo market</b> on an accelerated clock, so everything works offline. It is not real
          data. To see real-time prices, pick <b>Yahoo Finance</b> (no key needed) or <b>Alpaca</b> (free API key, true real-time
          stream) under <a href="#/settings">Settings</a>.
        </div>
      ) : null}
      <ErrorBox error={error ?? snap.error} />
      {Object.keys(snap.errors).length ? (
        <div className="callout warn" style={{ marginBottom: 16 }}>
          Some symbols could not be loaded: {Object.entries(snap.errors).map(([s, e]) => `${s} (${e})`).join("; ")}
        </div>
      ) : null}

      <div className="split wide-left">
        <div className="card flush">
          <div className="card-header" style={{ padding: "12px 12px 0" }}>
            <h2>Watchlist</h2>
            <span className="sub">{snap.timeframe === "1d" ? "Daily signals" : `${snap.timeframe} bars`}</span>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">Last</th>
                  <th className="hide-sm">Trend</th>
                  <th>
                    Signal <Help text="Consensus of the enabled strategies: the average of their votes (+1 bullish, 0 neutral, -1 bearish). A dot marks a signal that changed on the latest bar." />
                  </th>
                </tr>
              </thead>
              <tbody>
                {snap.symbols.map((sym) => {
                  const q = snap.quotes[sym];
                  const r = recs.get(sym);
                  return (
                    <tr
                      key={sym}
                      className={`watch-row clickable ${sym === selected ? "selected" : ""}`}
                      onClick={() => setSelected(sym)}
                      tabIndex={0}
                      onKeyDown={(e) => e.key === "Enter" && setSelected(sym)}
                      aria-selected={sym === selected}
                    >
                      <td>
                        {sym}
                        {r?.name ? <span className="name">{r.name}</span> : null}
                      </td>
                      <td className="num">
                        {q ? fmtPrice(q.price) : "–"}
                        <span className={`name ${dirClass(q?.change_pct)}`} style={{ marginLeft: "auto" }}>
                          {q ? fmtPct(q.change_pct, 2) : ""}
                        </span>
                      </td>
                      <td className="hide-sm">{q ? <Sparkline values={q.spark} width={64} /> : null}</td>
                      <td>
                        {r ? (
                          <span className="row tight nowrap">
                            <DirectionBadge score={r.consensus.score} label={r.consensus.label} />
                            {r.fresh_signals.length ? <span className="fresh-dot" title={`New today: ${r.fresh_signals.join(", ")}`} /> : null}
                          </span>
                        ) : (
                          <span className="muted small">{snap.errors[sym] ? "No data" : "…"}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ padding: 12 }}>
            <SymbolInput
              symbols={snap.symbols}
              onChange={(s) => void updateWatchlist(s)}
              placeholder={snap.provider === "synthetic" ? "Add (any ticker)" : "Add ticker"}
            />
            {snap.provider === "synthetic" ? (
              <p className="small muted" style={{ marginTop: 8 }}>
                In the demo market any ticker gets its own synthetic price series.
              </p>
            ) : null}
          </div>
        </div>

        <div className="stack">
          {rec && quote ? (
            <SymbolDetail key={rec.symbol} rec={rec} snap={snap} onPractice={() => navigate("/sim", { symbol: rec.symbol })} toast={toast} />
          ) : (
            <div className="card empty">
              {!snap.symbols.length ? (
                "Add a symbol to your watchlist to get started."
              ) : selected && (snap.errors[selected] || snap.errors._quotes) ? (
                <ErrorBox error={`Could not load ${selected}: ${snap.errors[selected] ?? snap.errors._quotes}`} />
              ) : !snap.running ? (
                "The live feed is paused. Press Start feed to resume."
              ) : (
                <Spinner label="Computing recommendations..." />
              )}
            </div>
          )}
        </div>
      </div>
      {snap.notes.length ? (
        <div className="callout" style={{ marginTop: 16 }}>
          {snap.notes.join(" ")}
        </div>
      ) : null}
    </div>
  );
}

function SymbolDetail({ rec, snap, onPractice }: {
  rec: Recommendation;
  snap: LiveSnapshot;
  onPractice: () => void;
  toast: (m: string) => void;
}) {
  const { settings } = useApp();
  const colors = useChartColors();
  const singleVotes = rec.votes.filter((v) => !v.group);
  const [strategy, setStrategy] = useState<string>(singleVotes[0]?.strategy_id ?? "tsmom");
  const [chart, setChart] = useState<ChartResponse | null>(null);
  const [chartErr, setChartErr] = useState<string | null>(null);
  const lastFetch = useRef(0);
  const inflight = useRef(false);
  const params = useMemo(() => rec.votes.find((v) => v.strategy_id === strategy)?.params ?? {}, [rec, strategy]);

  const load = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      setChart(await api.chart({ symbol: rec.symbol, strategy, params, count: 320 }));
      setChartErr(null);
    } catch (e) {
      setChartErr((e as Error).message);
    } finally {
      inflight.current = false;
      lastFetch.current = Date.now();
    }
  }, [rec.symbol, strategy]);

  useEffect(() => {
    void load();
  }, [load]);
  // Refresh the chart as live updates arrive (at most every 5 seconds).
  useEffect(() => {
    if (Date.now() - lastFetch.current > 5000) void load();
  }, [snap, load]);

  const markers: PriceMarker[] = useMemo(
    () =>
      (chart?.markers ?? []).map((m) => {
        const long = m.kind === "long" || m.kind === "reverse_long";
        const short = m.kind === "short" || m.kind === "reverse_short";
        return {
          time: m.t,
          position: long ? "belowBar" : "aboveBar",
          shape: long ? "arrowUp" : short ? "arrowDown" : "circle",
          color: long ? colors.up : short ? colors.down : colors.axisText,
          text: long ? "Long" : short ? "Short" : "Exit",
        } as PriceMarker;
      }),
    [chart, colors],
  );
  const overlays = useMemo(() => chart?.overlays ?? [], [chart]);
  const c = rec.consensus;
  const s = rec.sizing;
  const pinLeft = `${((c.score + 1) / 2) * 100}%`;

  return (
    <>
      <div className="card">
        <div className="card-header" style={{ marginBottom: 8 }}>
          <div>
            <h1>
              {rec.symbol} {rec.name ? <span className="muted" style={{ fontWeight: 400, fontSize: 14 }}>{rec.name}</span> : null}
            </h1>
            <div className="row" style={{ marginTop: 4 }}>
              <span style={{ fontSize: 24, fontWeight: 600 }}>{fmtPrice(rec.price)}</span>
              <span className={dirClass(rec.change_pct)} style={{ fontWeight: 600 }}>
                {fmtPct(rec.change_pct, 2)}
              </span>
              <span className="muted small">as of {fmtDate(rec.as_of)}</span>
            </div>
          </div>
          <div className="col" style={{ alignItems: "flex-end", gap: 6, minWidth: 220 }}>
            <DirectionBadge score={c.score} label={c.label} />
            <div style={{ width: 240 }}>
              <div className="consensus-bar" role="img" aria-label={`Consensus score ${c.score.toFixed(2)} from -1 (all bearish) to +1 (all bullish)`}>
                <div className="pin" style={{ left: pinLeft }} />
              </div>
              <div className="small muted" style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
                <span>Bearish</span>
                <span>Bullish</span>
              </div>
              <div className="small secondary" style={{ textAlign: "right", marginTop: 2 }}>
                {c.bullish} bullish · {c.neutral} neutral · {c.bearish} bearish
              </div>
            </div>
          </div>
        </div>
        {rec.risk.flags.length ? (
          <div className="callout warn" style={{ marginBottom: 12 }}>
            <ul style={{ margin: 0 }}>
              {rec.risk.flags.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="row" style={{ marginBottom: 8 }}>
          <label className="field" style={{ minWidth: 240 }}>
            <span>Chart overlay</span>
            <select className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {singleVotes.map((v) => (
                <option key={v.strategy_id} value={v.strategy_id}>
                  {v.strategy_name}
                </option>
              ))}
            </select>
          </label>
          {chart?.explanation ? <span className="secondary small grow">{chart.explanation.headline}</span> : null}
        </div>
        <ErrorBox error={chartErr} />
        {chart ? (
          <PriceChart
            bars={chart.bars}
            overlays={overlays}
            markers={markers}
            priceLines={s.stop !== null && s.side !== "flat" ? [{ price: s.stop, label: "Suggested stop", color: colors.axisText, style: "dashed" }] : []}
            height={300}
            intraday={snap.timeframe !== "1d"}
            follow
            initialBars={160}
            label={`${rec.symbol} price chart`}
          />
        ) : (
          <Spinner label="Loading chart..." />
        )}
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-header">
            <h2>Suggested position</h2>
            <span className="sub">on a hypothetical {fmtMoney(settings?.account_equity ?? null)} account</span>
          </div>
          {s.side === "flat" ? (
            <p className="secondary">{s.explanation}</p>
          ) : (
            <>
              <div className="row" style={{ alignItems: "baseline" }}>
                <span className={`badge ${s.side === "long" ? "up" : "down"}`}>{s.side === "long" ? "▲ Long" : "▼ Short"}</span>
                <span style={{ fontSize: 22, fontWeight: 600 }}>{s.shares.toLocaleString()} shares</span>
                <span className="muted">
                  {fmtMoney(s.notional)} ({fmtPct(s.weight, 1, false)} of equity)
                </span>
              </div>
              <dl className="kv" style={{ marginTop: 12 }}>
                <dt>Protective stop</dt>
                <dd>{fmtPrice(s.stop)}</dd>
                <dt>Stop distance</dt>
                <dd>{fmtPrice(s.stop_distance)}</dd>
                <dt>Loss if stopped out</dt>
                <dd>{fmtMoney(s.risk_amount)}</dd>
                <dt>Conviction</dt>
                <dd>{fmtPct(s.conviction, 0, false)}</dd>
              </dl>
              <p className="small secondary" style={{ marginTop: 10 }}>
                {s.explanation}
              </p>
            </>
          )}
          <p className="small muted" style={{ marginTop: 8 }}>
            Sizing rules come from your settings (risk per trade, stop distance, position cap). Nothing is sent to a broker.
          </p>
          {snap.provider !== "synthetic" ? (
            <button className="btn small" onClick={onPractice}>
              Practise {rec.symbol} on hidden history
            </button>
          ) : null}
        </div>
        <div className="card">
          <div className="card-header">
            <h2>Risk profile</h2>
          </div>
          <dl className="kv">
            <dt>
              ATR (20) <Help text="Average True Range: the typical daily high-low range, used for stops and sizing." />
            </dt>
            <dd>
              {fmtPrice(rec.risk.atr)} ({fmtPct(rec.risk.atr_pct, 1, false)})
            </dd>
            <dt>Volatility (20 bars)</dt>
            <dd>{fmtPct(rec.risk.vol_20, 0, false)}</dd>
            <dt>Volatility (1 year)</dt>
            <dd>{fmtPct(rec.risk.vol_1y, 0, false)}</dd>
            <dt>Avg. traded value / bar</dt>
            <dd>${fmtCompact(rec.risk.avg_dollar_volume)}</dd>
            <dt>52-week range</dt>
            <dd>
              {fmtPrice(rec.risk.low_52w)} – {fmtPrice(rec.risk.high_52w)}
            </dd>
          </dl>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Why: strategy by strategy</h2>
          <span className="sub">Each vote comes with the rule it applied and how that rule has done on {rec.symbol}.</span>
        </div>
        {rec.votes.map((v) => (
          <VoteCard key={`${v.strategy_id}-${(v.group ?? []).join("-")}`} vote={v} symbol={rec.symbol} />
        ))}
      </div>
    </>
  );
}

function VoteCard({ vote, symbol }: { vote: Vote; symbol: string }) {
  const ev = vote.evidence;
  return (
    <div className="vote-card">
      <div className="vote-head">
        <span className="title">
          {vote.strategy_name}
          {vote.group ? <span className="muted small"> ({vote.group.join(" / ")})</span> : null}
        </span>
        {vote.fresh ? <span className="badge neutral">New signal</span> : null}
        <EvidenceBadge level={vote.evidence_level} />
        <VoteGlyph vote={vote.vote} state={vote.state} />
      </div>
      <p className="secondary" style={{ margin: "6px 0 0" }}>
        {vote.headline}
      </p>
      {vote.rules.length ? (
        <ul className="rules">
          {vote.rules.map((r) => (
            <li key={r.label}>
              <span className={`mark ${r.passed === true ? "ok" : r.passed === false ? "no" : ""}`} aria-hidden="true">
                {r.passed === true ? "✓" : r.passed === false ? "✕" : "•"}
              </span>
              <span>{r.label}</span>
              <span className="val">{r.value}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="small muted" style={{ marginTop: 6 }}>
        {vote.since ? `In this state since ${fmtDate(vote.since)}. ` : ""}
        {vote.exit_rule}
      </div>
      {ev && !ev.error ? (
        <details className="more">
          <summary>
            Track record on {vote.group ? vote.group.join(" / ") : symbol} (last {fmtNum(ev.years ?? null, 1)} years, after costs)
          </summary>
          <table className="evidence-table">
            <thead>
              <tr>
                <th />
                <th>CAGR</th>
                <th>Sharpe</th>
                <th>Max DD</th>
                <th>Win rate</th>
                <th>Trades</th>
                <th>
                  P(SR&gt;0) <Help text="Probabilistic Sharpe ratio: the chance the true Sharpe ratio is above zero. Below 95% the record could be luck." />
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Strategy</td>
                <td>{fmtPct(ev.cagr)}</td>
                <td>{fmtNum(ev.sharpe ?? null)}</td>
                <td>{fmtPct(ev.max_drawdown)}</td>
                <td>{fmtPct(ev.win_rate, 0, false)}</td>
                <td>{isNum(ev.n_trades) ? ev.n_trades : "–"}</td>
                <td>{fmtPct(ev.psr, 0, false)}</td>
              </tr>
              <tr className="muted">
                <td>Buy &amp; hold</td>
                <td>{fmtPct(ev.benchmark_cagr)}</td>
                <td>{fmtNum(ev.benchmark_sharpe ?? null)}</td>
                <td>{fmtPct(ev.benchmark_max_drawdown)}</td>
                <td />
                <td />
                <td />
              </tr>
            </tbody>
          </table>
          {isNum(ev.n_trades) && ev.n_trades < 30 ? (
            <p className="small muted" style={{ marginTop: 6 }}>
              Only {ev.n_trades} trades: treat this track record as anecdotal.
            </p>
          ) : null}
        </details>
      ) : ev?.error ? (
        <div className="small muted">Track record unavailable: {ev.error}</div>
      ) : null}
      <a className="small" href={`#/library?strategy=${vote.strategy_id}`}>
        How this strategy works
      </a>
    </div>
  );
}
