/**
 * The Live Desk: what the published strategies say about the watchlist right now, and why.
 *
 * Laid out like an instrument (docs/DESIGN.md): a header with the screen's readouts and the
 * feed strip; the watchlist as a terminal table with the risk profile "as modeled" beside the
 * inspector for the selected symbol (consensus gauge, suggested position, key values) and its
 * price chart; then the votes, strategy by strategy, with their evidence on demand.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { api, liveSocketUrl } from "../api";
import { useApp, useStatusLine } from "../state";
import type {
  ChartResponse,
  Evidence,
  LiveSnapshot,
  PortfolioSummary,
  Recommendation,
  Settings,
  StrategyMeta,
  Vote,
} from "../types";
import PriceChart, { type PriceMarker } from "../components/PriceChart";
import Sparkline from "../components/Sparkline";
import { Figure, Hatch, KeyValues, Panel, Readout, Tag } from "../components/instrument";
import { DirectionBadge, ErrorBox, EvidenceBadge, Help, Spinner, VoteGlyph } from "../components/ui";
import { dirClass, fmtCompact, fmtDate, fmtMoney, fmtNum, fmtPct, fmtPrice, isNum } from "../format";
import { useChartColors } from "../theme";
import "../styles/livedesk.css";

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

/** The market's clock as [label, text]: the demo's accelerated clock, or the US session. */
function marketText(s: LiveSnapshot): [string, string] {
  const m = s.market;
  if (s.demo) {
    return [
      "Demo clock",
      `${m.clock ?? ""} ET on ${m.session_date} (1 real second = ${Math.round((m.speed ?? 60) / 60)} simulated minute${(m.speed ?? 60) >= 120 ? "s" : ""})`,
    ];
  }
  const fmt = (iso?: string) =>
    iso
      ? new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit" }).format(
          new Date(iso),
        ) + " ET"
      : "";
  if (m.is_open) return ["Market", `US market open · closes ${fmt(m.next_close)}`];
  const phase = m.phase === "pre" ? "Pre-market" : m.phase === "post" ? "After hours" : "US market closed";
  return ["Market", `${phase} · opens ${fmt(m.next_open)}`];
}

// ---- words and definitions ----------------------------------------------------------------

const HELP = {
  consensus:
    "The average of the enabled strategies' votes (+1 bullish, 0 neutral, −1 bearish), or the average of each category's average if you chose that in Settings. From ±0.2 it is a buy or a sell, from ±0.5 a strong one; in between it is neutral, which means no position. NEW marks a signal that changed on the latest bar.",
  votes:
    "One cell per strategy, in the same order on every row. A bar above the line is a bullish vote, below the line a bearish one, a short dash is neutral, and a dashed box is a strategy still warming up or in error (it does not count). An empty slot means the strategy does not cover this symbol.",
  position:
    "What following the consensus would hold on your account: the side and the share of equity, sized by your risk profile. A suggestion only; nothing is sent to a broker.",
  exposure:
    "The sum of all suggested positions on the watchlist. Each one is sized on its own first; if together they exceed the portfolio cap in Settings, all of them shrink in proportion.",
  atr: "Average True Range: the typical daily high-low range, used for stops and sizing.",
  agreement: "The share of the votes on the most common side (bullish, bearish or neutral). 100% means every strategy agrees.",
  weighting:
    "Equal: every strategy's vote counts the same. By category: votes are averaged within each category (trend following, mean reversion and so on) first, so one crowded family cannot dominate.",
  record:
    "How the strategy's rule did on this symbol over the last few years, after costs. CAGR is the yearly growth rate; B&H is buy and hold, simply owning the symbol; Sharpe is return per unit of risk (above 1 is good). Under 30 trades a record is anecdotal.",
  psr: "Probabilistic Sharpe ratio: the chance the true Sharpe ratio is above zero. Below 95% the record could be luck.",
  cagr: "Compound annual growth rate: the steady yearly return that would turn the starting value into the final one.",
  maxdd: "Maximum drawdown: the deepest fall from a peak before a new high, in percent. It is the pain you would have sat through.",
  riskPerTrade: "Loss if the protective stop is hit, as a share of the account. 0.5–2% is typical.",
  stop: "The protective stop sits this many ATRs from the price; the position is sized so that being stopped out loses the risk per trade.",
  cap: "All suggested positions together. If they add up to more, every suggestion shrinks in proportion. 100% means no borrowing.",
};

const EVIDENCE_KEY: [Vote["evidence_level"], string][] = [
  ["strong", "peer-reviewed, replicated across markets and decades"],
  ["moderate", "peer-reviewed, but mixed or weaker since publication"],
  ["practitioner", "a well-known trading rule with little academic testing"],
  ["experimental", "a promising idea with little evidence in this form"],
];

type Lean = "bull" | "bear" | "neutral";
const leanOf = (r: Recommendation): Lean =>
  r.consensus.action === "BUY" ? "bull" : r.consensus.action === "HOLD" ? "neutral" : "bear";

type VoteKind = "bull" | "bear" | "flat" | "off";
function voteKind(v: Vote): VoteKind {
  if (v.state === "warming_up" || v.state === "error") return "off";
  if (v.state === "hedge") return "flat";
  return v.vote > 0 ? "bull" : v.vote < 0 ? "bear" : "flat";
}

function voteWord(v: Vote): string {
  if (v.state === "warming_up") return "warming up (not counted)";
  if (v.state === "error") return "error (not counted)";
  if (v.state === "hedge") return "hedge leg (no view)";
  return v.vote > 0 ? "bullish" : v.vote < 0 ? "bearish" : "neutral";
}

/** A vote's slot: its strategy, and which occurrence on this symbol (a symbol can sit in two pairs). */
function voteKeys(votes: Vote[]): string[] {
  const seen = new Map<string, number>();
  return votes.map((v) => {
    const n = seen.get(v.strategy_id) ?? 0;
    seen.set(v.strategy_id, n + 1);
    return `${v.strategy_id}#${n}`;
  });
}

interface StripColumn {
  key: string;
  name: string;
}

/** The vote-strip columns: every strategy slot on the watchlist, in the order of the strategies in Settings. */
function stripColumns(recs: Recommendation[], advisors: string[]): StripColumn[] {
  const cols = new Map<string, string>();
  for (const r of recs) {
    voteKeys(r.votes).forEach((k, i) => {
      if (!cols.has(k)) cols.set(k, r.votes[i].strategy_name);
    });
  }
  const rank = (k: string) => {
    const i = advisors.indexOf(k.split("#")[0]);
    return i < 0 ? advisors.length : i;
  };
  return [...cols.entries()]
    .map(([key, name]) => ({ key, name }))
    .sort((a, b) => rank(a.key) - rank(b.key) || a.key.localeCompare(b.key));
}

function defaultOverlay(rec: Recommendation): string {
  return rec.votes.find((v) => !v.group)?.strategy_id ?? "tsmom";
}

/** "SIMTEC SIMBNK SIMUTL +2" */
function tickers(list: string[], n = 3): string {
  if (!list.length) return "none";
  return list.slice(0, n).join(" ") + (list.length > n ? ` +${list.length - n}` : "");
}

/** Up/down colour for a change shown to 2 decimals in percent: none when it reads 0.00%. */
const chgClass = (x: number | null | undefined) => dirClass(x, 0.00005); // shown as a % with 2 decimals

const tick = (t: number) => (t === 0 ? "0" : `${t > 0 ? "+" : "−"}${Math.abs(t)}`);
const clamp = (x: number) => Math.max(-1, Math.min(1, x));
const pct = (x: number) => ((clamp(x) + 1) / 2) * 100;

function feedState(snap: LiveSnapshot, noData: boolean): { word: string; dot: "live" | "info" | "bad" | "warn" } {
  if (!snap.running) return { word: "Paused", dot: "info" };
  if (snap.status === "error") return { word: "Error", dot: "bad" };
  if (noData) return { word: "No data", dot: "warn" };
  if (snap.status === "running") return { word: "Live", dot: "live" };
  return { word: "Starting", dot: "info" };
}

function narrate(snap: LiveSnapshot | null, g: Record<Lean, string[]>, noData: boolean): string {
  if (!snap) return "Starting the live feed: the desk connects to the server, then runs every strategy on your watchlist.";
  const n = snap.symbols.length;
  const s = n === 1 ? "" : "s";
  const where = snap.demo ? "the synthetic demo market" : `${snap.provider_label} data`;
  if (!n) return "Your watchlist is empty. Add a ticker in the **Watchlist** panel to see what the strategies say about it.";
  if (!snap.running) {
    const at = snap.last_update ? ` at ${new Date(snap.last_update).toLocaleTimeString()}` : "";
    return `The live feed is paused, so these numbers are frozen${at}. Press **Start feed** to resume.`;
  }
  if (snap.status === "error") return `The live feed hit an error on ${where} and keeps retrying. If it persists, check the **Data source**.`;
  if (noData) return `No prices came back from ${snap.provider_label} for your watchlist. Check the **Data source** in Settings.`;
  if (!snap.recommendations.length) {
    return `Computing recommendations for ${n} symbol${s} on ${where}: the first pass backtests every strategy, so it takes a few seconds.`;
  }
  return `Watching **${n}** symbol${s} on ${where}; **${g.bull.length}** bullish, **${g.bear.length}** bearish. Select a row (or use ↑ ↓) to see why each strategy votes as it does.`;
}

/** Seconds since an update, re-rendered every second (only this text, not the screen). */
function Age({ iso }: { iso: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  if (!iso) return <>no update yet</>;
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  const text = s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return <>updated {text} ago</>;
}

// ---- the screen ------------------------------------------------------------------------------

export default function LiveDesk() {
  const { meta, settings, navigate, toast } = useApp();
  const { snap, setSnap, ws } = useLiveFeed();
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The strategy drawn on the chart; it follows you from symbol to symbol while that symbol has it.
  const [overlayPick, setOverlayPick] = useState<string | null>(null);

  const recs = useMemo(() => {
    const m = new Map<string, Recommendation>();
    snap?.recommendations.forEach((r) => m.set(r.symbol, r));
    return m;
  }, [snap]);

  useEffect(() => {
    if (!snap) return;
    if (!selected || !snap.symbols.includes(selected)) setSelected(snap.symbols[0] ?? null);
  }, [snap, selected]);

  const groups = useMemo(() => {
    const out: Record<Lean, string[]> = { bull: [], bear: [], neutral: [] };
    for (const sym of snap?.symbols ?? []) {
      const r = recs.get(sym);
      if (r) out[leanOf(r)].push(sym);
    }
    return out;
  }, [snap, recs]);

  const advisorIds = useMemo(() => (settings?.advisors ?? []).map((a) => a.id), [settings]);
  const columns = useMemo(() => stripColumns(snap?.recommendations ?? [], advisorIds), [snap, advisorIds]);
  const strategies = useMemo(() => new Map((meta?.strategies ?? []).map((s) => [s.id, s] as const)), [meta]);

  const noData = !!snap && snap.status === "running" && !Object.keys(snap.quotes).length && Object.keys(snap.errors).length > 0;
  useStatusLine(narrate(snap, groups, noData));

  const updateWatchlist = async (symbols: string[]): Promise<boolean> => {
    setError(null);
    try {
      setSnap(await api.setWatchlist(symbols));
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
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

  if (!snap || !meta || !settings) {
    return (
      <div className="ld">
        <PageTitle />
        <section className="card ld-loading">
          <Hatch />
          <Spinner label="Starting the live feed..." />
        </section>
      </div>
    );
  }

  const rec = selected ? recs.get(selected) : undefined;
  const quote = selected ? snap.quotes[selected] : undefined;
  const updated = snap.last_update ? new Date(snap.last_update).toLocaleTimeString() : "–";
  const fs = feedState(snap, noData);
  const tf = snap.timeframe === "1d" ? "Daily signals" : `${snap.timeframe} bars`;
  const p = snap.portfolio;
  const ready = snap.recommendations.length > 0;
  const waiting = !snap.symbols.length ? "no symbols" : noData ? "no data" : !snap.running ? "feed paused" : "computing";
  const [clockLabel, clockText] = marketText(snap);
  const overlay =
    rec && overlayPick && rec.votes.some((v) => !v.group && v.strategy_id === overlayPick) ? overlayPick : rec ? defaultOverlay(rec) : "tsmom";
  const symbolErrors = Object.entries(snap.errors).filter(([k]) => !k.startsWith("_"));
  const feedErrors = Object.entries(snap.errors).filter(([k]) => k.startsWith("_"));

  const showOnChart = (id: string) => {
    setOverlayPick(id);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById("ld-fig-1")?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
  };

  return (
    <div className="ld">
      <PageTitle>
        <button className={`btn${snap.running ? "" : " primary"}`} onClick={toggleLive} disabled={busy}>
          {snap.running ? "Pause feed" : "Start feed"}
        </button>
        <button className="btn" onClick={() => navigate("/settings")}>
          Data source
        </button>
        <button
          className="btn"
          title="Let each strategy trade its own paper account on this market as bars complete"
          onClick={() => navigate("/sim", { source: snap.demo ? "simulated" : "realtime" })}
        >
          Forward-test strategies
        </button>
      </PageTitle>

      <section className="card ld-summary" aria-label="The desk at a glance">
        <div className="ld-readouts">
          <Readout label="Watching" value={snap.symbols.length} unit="sym" note={tf.toLowerCase()} />
          {ready ? (
            <>
              <Readout
                label="Bullish"
                value={<span className={groups.bull.length ? "upc" : ""}>{groups.bull.length}</span>}
                unit="sym"
                note={tickers(groups.bull)}
              />
              <Readout
                label="Bearish"
                value={<span className={groups.bear.length ? "downc" : ""}>{groups.bear.length}</span>}
                unit="sym"
                note={tickers(groups.bear)}
              />
              <Readout label="Neutral" value={groups.neutral.length} unit="sym" note={tickers(groups.neutral)} />
            </>
          ) : (
            <>
              <Readout label="Bullish" value="–" note={waiting} />
              <Readout label="Bearish" value="–" note={waiting} />
              <Readout label="Neutral" value="–" note={waiting} />
            </>
          )}
          <Readout
            label={
              <span className="ld-label-help">
                Exposure <Help text={HELP.exposure} />
              </span>
            }
            value={p ? fmtNum(p.gross * 100, 0) : "–"}
            unit="%"
            note={p ? `${p.positions} position${p.positions === 1 ? "" : "s"} · cap ${fmtPct(p.cap, 0, false)}` : "after the first update"}
          />
          <Readout
            label="Feed"
            value={
              <span className={`ld-feedword ${fs.dot}`}>
                <span className={`status ${fs.dot}`}>
                  <span className="dot" aria-hidden="true" />
                </span>
                {fs.word}
              </span>
            }
            note={
              <>
                <Age iso={snap.last_update} />
                <span className="ld-feedsrc"> · {snap.provider}</span>
              </>
            }
          />
        </div>
        <div className="ld-strip" aria-label="Feed details">
          <span>
            <span className="ld-k">Data</span>
            <span>
              <b>{snap.provider_label}</b>
              {snap.realtime ? (
                <span className="muted">
                  {" "}
                  ({snap.realtime}
                  {snap.streaming ? ", streaming" : ""})
                </span>
              ) : null}
            </span>
          </span>
          <span>
            <span className="ld-k">{clockLabel}</span>
            {clockText}
          </span>
          <span>
            <span className="ld-k">Updated</span>
            {updated}
          </span>
          <span>
            <span className="ld-k">Socket</span>
            <span className={`status ${ws === "open" ? "good" : "warn"}`}>
              <span className="dot" aria-hidden="true" />
              {ws === "open" ? "Connected" : ws === "connecting" ? "Connecting" : "Reconnecting"}
            </span>
          </span>
        </div>
      </section>

      {snap.demo ? (
        <div className="callout info ld-callout">
          <Tag kind="ink">Demo</Tag>
          <span>
            You are looking at the <b>synthetic demo market</b> on an accelerated clock, so everything works offline. It is not real data.
            To see real-time prices, pick <b>Yahoo Finance</b> (no key needed) or <b>Alpaca</b> (free API key, true real-time stream)
            under <a href="#/settings">Settings</a>.
          </span>
        </div>
      ) : null}
      {error ?? snap.error ? (
        <div className="ld-callout-gap">
          <ErrorBox error={error ?? snap.error} />
        </div>
      ) : null}
      {symbolErrors.length ? (
        <div className="callout warn ld-callout">
          <Tag kind="warn">Warn</Tag>
          <span>Some symbols could not be loaded: {symbolErrors.map(([s, e]) => `${s} (${e})`).join("; ")}</span>
        </div>
      ) : null}
      {feedErrors.map(([k, e]) => (
        <div key={k} className="callout warn ld-callout">
          <Tag kind="warn">Warn</Tag>
          <span>{k === "_quotes" ? `Quotes could not be loaded: ${e}` : e}</span>
        </div>
      ))}

      <div className="ld-grid">
        <div className="ld-col">
          <Watchlist
            snap={snap}
            recs={recs}
            columns={columns}
            selected={selected}
            tf={tf}
            onSelect={setSelected}
            onChange={(s) => void updateWatchlist(s)}
            onRemove={(sym) =>
              void updateWatchlist(snap.symbols.filter((x) => x !== sym)).then((ok) => {
                if (ok) toast(`Removed ${sym} from the watchlist.`);
              })
            }
          />
          <RiskProfile settings={settings} portfolio={p} onEdit={() => navigate("/settings")} />
          <KeyPanel />
          {snap.notes.length ? (
            <Panel title="Notes" aside="from the recommender" className="ld-area-notes">
              <ul className="ld-feed">
                {snap.notes.map((n) => (
                  <li key={n}>
                    <Tag kind="info">Info</Tag>
                    <span>{n}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}
        </div>

        <div className="ld-col">
          {rec && quote ? (
            <>
              <Inspector
                rec={rec}
                snap={snap}
                equity={settings.account_equity}
                index={snap.symbols.indexOf(rec.symbol)}
                onSettings={() => navigate("/settings")}
                onPractice={() => navigate("/sim", { symbol: rec.symbol })}
              />
              <div id="ld-fig-1" className="ld-area-chart">
                <ChartFigure key={rec.symbol} rec={rec} snap={snap} strategy={overlay} onStrategy={setOverlayPick} />
              </div>
            </>
          ) : (
            <EmptyInspector snap={snap} selected={selected} busy={busy} onStart={toggleLive} />
          )}
        </div>

        {rec && quote ? <WhyPanel rec={rec} overlay={overlay} strategies={strategies} onChart={showOnChart} /> : null}
      </div>
    </div>
  );
}

function PageTitle({ children }: { children?: ReactNode }) {
  return (
    <header className="ld-head">
      <div className="ld-title">
        <h1>Live Desk</h1>
        <p className="ld-purpose">
          What the published strategies say about your watchlist right now, and why. Recommendations, not advice.
        </p>
      </div>
      {children ? <div className="ld-actions">{children}</div> : null}
    </header>
  );
}

// ---- the watchlist ---------------------------------------------------------------------------

function Watchlist({
  snap,
  recs,
  columns,
  selected,
  tf,
  onSelect,
  onChange,
  onRemove,
}: {
  snap: LiveSnapshot;
  recs: Map<string, Recommendation>;
  columns: StripColumn[];
  selected: string | null;
  tf: string;
  onSelect: (sym: string) => void;
  onChange: (symbols: string[]) => void;
  onRemove: (sym: string) => void;
}) {
  const rows = useRef(new Map<string, HTMLTableRowElement>());
  const list = snap.symbols;

  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, sym: string) => {
    const i = list.indexOf(sym);
    const go = (j: number) => {
      const next = list[Math.max(0, Math.min(list.length - 1, j))];
      if (!next) return;
      e.preventDefault();
      onSelect(next);
      rows.current.get(next)?.focus();
    };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelect(sym);
    }
  };

  return (
    <Panel
      marks
      title="Watchlist"
      aside={
        <>
          {tf.toLowerCase()}
          {snap.provisional ? " · bar forming" : ""}
          {list.length > 1 ? <span className="ld-kbd"> · ↑↓ to move</span> : null}
        </>
      }
      className="ld-watch ld-area-watch"
    >
      {list.length ? (
        <div className="ld-tablewrap">
          <table className="data ld-table" aria-label="Watchlist">
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">Last</th>
                <th className="num ld-c-chg">Chg</th>
                <th className="ld-c-trend">Trend</th>
                <th className="ld-c-cons">
                  <span className="ld-label-help">
                    Consensus <Help text={HELP.consensus} />
                  </span>
                </th>
                <th className="ld-c-votes">
                  <span className="ld-label-help">
                    Votes <Help text={HELP.votes} />
                  </span>
                </th>
                <th className="num ld-c-pos">
                  <span className="ld-label-help">
                    Position <Help text={HELP.position} />
                  </span>
                </th>
                <th className="ld-c-x">
                  <span className="sr-only">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((sym, i) => {
                const q = snap.quotes[sym];
                const r = recs.get(sym);
                const sel = sym === selected;
                return (
                  <tr
                    key={sym}
                    ref={(el) => {
                      if (el) rows.current.set(sym, el);
                      else rows.current.delete(sym);
                    }}
                    className={`clickable ld-row${sel ? " selected" : ""}`}
                    onClick={() => onSelect(sym)}
                    onKeyDown={(e) => onKey(e, sym)}
                    tabIndex={sel || (!selected && i === 0) ? 0 : -1}
                    aria-selected={sel}
                  >
                    <td className="ld-c-sym">
                      <span className="ld-tkr">
                        {sym}
                        {r?.fresh_signals.length ? (
                          <Tag kind="warn" title={`New today: ${r.fresh_signals.join(", ")}`}>
                            New
                          </Tag>
                        ) : null}
                      </span>
                      {r?.name ? (
                        <span className="ld-name" title={r.name}>
                          {r.name}
                        </span>
                      ) : null}
                    </td>
                    <td className="num ld-c-last">
                      {q ? fmtPrice(q.price) : "–"}
                      <span className={`ld-chg-inline ${chgClass(q?.change_pct)}`}>{q ? fmtPct(q.change_pct, 2) : ""}</span>
                    </td>
                    <td className={`num ld-c-chg ${chgClass(q?.change_pct)}`}>{q ? fmtPct(q.change_pct, 2) : ""}</td>
                    <td className="ld-c-trend">{q ? <Sparkline values={q.spark} width={52} height={22} /> : null}</td>
                    <td className="ld-c-cons">
                      {r ? (
                        <Consensus c={r.consensus} />
                      ) : snap.errors[sym] ? (
                        <Tag kind="warn" title={snap.errors[sym]}>
                          No data
                        </Tag>
                      ) : (
                        <span className="muted">…</span>
                      )}
                    </td>
                    <td className="ld-c-votes">{r ? <VoteStrip rec={r} columns={columns} /> : null}</td>
                    <td className="num ld-c-pos">{r ? <PositionCell s={r.sizing} /> : null}</td>
                    <td className="ld-c-x">
                      <button
                        type="button"
                        className="ld-remove"
                        tabIndex={sel ? 0 : -1}
                        aria-label={`Remove ${sym} from the watchlist`}
                        title={`Remove ${sym} from the watchlist`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onRemove(sym);
                        }}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <Hatch />
          <div className="ld-empty">
            <p>Your watchlist is empty. Add a ticker below and the strategies vote on it within seconds.</p>
          </div>
        </>
      )}
      <div className="ld-watch-foot">
        <AddTickers
          symbols={list}
          onChange={onChange}
          placeholder={snap.provider === "synthetic" ? "Add (any ticker)" : "Add ticker"}
        />
        {snap.provider === "synthetic" ? <p className="ld-fine">In the demo market any ticker gets its own synthetic price series.</p> : null}
      </div>
    </Panel>
  );
}

/** Add tickers to the watchlist: type one or several (space or comma separated), then Enter. */
function AddTickers({ symbols, onChange, placeholder, max = 30 }: {
  symbols: string[];
  onChange: (s: string[]) => void;
  placeholder: string;
  max?: number;
}) {
  const [text, setText] = useState("");
  const add = () => {
    const parts = text
      .split(/[\s,]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    setText("");
    const next = [...symbols];
    for (const p of parts) if (!next.includes(p) && next.length < max) next.push(p);
    if (next.length !== symbols.length) onChange(next);
  };
  const full = symbols.length >= max;
  return (
    <div className="ld-add">
      <label className="ld-add-field">
        <span className="ld-k">Add</span>
        <input
          className="input ld-add-input"
          value={text}
          placeholder={full ? `Watchlist full (${max})` : placeholder}
          disabled={full}
          spellCheck={false}
          autoCapitalize="characters"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={() => text && add()}
          aria-label={placeholder}
        />
      </label>
      <button type="button" className="btn small" onMouseDown={(e) => e.preventDefault()} onClick={add} disabled={!text.trim()}>
        Add
      </button>
      <span className="ld-add-count" title="Symbols on the watchlist, of the most it holds">
        {symbols.length} / {max}
      </span>
    </div>
  );
}

/** The score and its label. The table shows the label's first part ("Strong sell" for "Strong sell /
 *  avoid"); the full label is the tooltip and is spelled out in the inspector. */
function Consensus({ c }: { c: Recommendation["consensus"] }) {
  return (
    <span className="ld-cons" title={`${c.label} (score ${fmtNum(c.score, 2, true)})`}>
      <span className="ld-score">{fmtNum(c.score, 2, true)}</span>
      <DirectionBadge score={c.score} label={c.label.split(" / ")[0]} />
    </span>
  );
}

function VoteStrip({ rec, columns }: { rec: Recommendation; columns: StripColumn[] }) {
  const keys = voteKeys(rec.votes);
  const byKey = new Map(keys.map((k, i) => [k, rec.votes[i]] as const));
  const c = rec.consensus;
  return (
    <span className="ld-vstrip" role="img" aria-label={`Votes: ${c.bullish} bullish, ${c.bearish} bearish, ${c.neutral} neutral`}>
      {columns.map((col) => {
        const v = byKey.get(col.key);
        return (
          <span
            key={col.key}
            className={`ld-vc ${v ? voteKind(v) : "none"}`}
            title={v ? `${v.strategy_name}: ${voteWord(v)}` : `${col.name}: does not cover ${rec.symbol}`}
          />
        );
      })}
    </span>
  );
}

function PositionCell({ s }: { s: Recommendation["sizing"] }) {
  if (s.side === "flat") return <span className="ld-flat">Flat</span>;
  const long = s.side === "long";
  return (
    <span className="ld-pos">
      <span className={long ? "upc" : "downc"}>
        <span aria-hidden="true">{long ? "▲" : "▼"} </span>
        <span className="sr-only">{long ? "Long " : "Short "}</span>
        {fmtPct(Math.abs(s.weight), 1, false)}
      </span>
      <span className="ld-sub">{s.shares.toLocaleString()} sh</span>
    </span>
  );
}

// ---- the risk profile, as modeled --------------------------------------------------------------

function RiskProfile({ settings: st, portfolio, onEdit }: { settings: Settings; portfolio: PortfolioSummary | null; onEdit: () => void }) {
  const stop = String(Number(st.stop_atr.toFixed(2)));
  return (
    <Panel
      title="Risk profile"
      aside="as modeled"
      actions={
        <button type="button" className="btn small" onClick={onEdit} title="The risk profile is edited in Settings">
          Edit in Settings
        </button>
      }
      className="ld-risk ld-area-risk"
    >
      <KeyValues
        rows={[
          ["Account equity", fmtMoney(st.account_equity)],
          [
            <span className="ld-label-help" key="r">
              Risk per trade <Help text={HELP.riskPerTrade} />
            </span>,
            <>
              {fmtPct(st.risk_per_trade, 1, false)} <span className="muted">· {fmtMoney(st.risk_per_trade * st.account_equity)} if stopped out</span>
            </>,
          ],
          [
            <span className="ld-label-help" key="s">
              Stop distance <Help text={HELP.stop} />
            </span>,
            `${stop} × ATR (20)`,
          ],
          ["Max position", `${fmtPct(st.max_position_pct, 0, false)} of equity`],
          [
            <span className="ld-label-help" key="c">
              Portfolio cap <Help text={HELP.cap} />
            </span>,
            `${fmtPct(st.max_gross_exposure, 0, false)} of equity`,
          ],
          ["Short selling", st.allow_short ? "Allowed: bearish means short" : "Off: bearish means avoid"],
          ["Shares", st.fractional_shares ? "Fractional" : "Whole shares only"],
          [
            <span className="ld-label-help" key="w">
              Weighting <Help text={HELP.weighting} />
            </span>,
            st.consensus_weighting === "equal" ? "Equal: one vote each" : "By category",
          ],
          ["Costs", `${fmtNum(st.slippage_bps, 1)} bps slippage · ${fmtNum(st.commission_bps, 1)} bps commission`],
        ]}
      />
      {portfolio ? <Exposure p={portfolio} /> : null}
    </Panel>
  );
}

/** All suggested positions added up: what following the whole Live Desk would put to work. */
function Exposure({ p }: { p: PortfolioSummary }) {
  const top = Math.max(p.cap, p.uncapped_gross, 1e-9);
  const w = (x: number) => `${Math.min(100, (Math.max(0, x) / top) * 100)}%`;
  return (
    <div className="ld-sec">
      <div className="ld-sec-head">
        <h3 className="ld-label-help">
          Suggested exposure <Help text={HELP.exposure} />
        </h3>
        <span>
          <b>{fmtPct(p.gross, 0, false)}</b> of a {fmtPct(p.cap, 0, false)} cap
        </span>
      </div>
      <div
        className="ld-meter"
        role="img"
        aria-label={`Suggested exposure ${fmtPct(p.gross, 0, false)} of equity, against a cap of ${fmtPct(p.cap, 0, false)}`}
      >
        {p.uncapped_gross > p.gross + 1e-9 ? <span className="ld-meter-ghost" style={{ width: w(p.uncapped_gross) }} /> : null}
        <span className="ld-meter-fill" style={{ width: w(p.gross) }} />
        <span className="ld-meter-cap" style={{ left: w(p.cap) }} />
      </div>
      <p className="ld-fine">
        Total suggested exposure <b>{fmtPct(p.gross, 0, false)}</b> of equity in {p.positions} position{p.positions === 1 ? "" : "s"} (cap{" "}
        {fmtPct(p.cap, 0, false)}).
        {p.scale < 1
          ? ` Every suggestion was scaled to ${fmtPct(p.scale, 0, false)} because together they came to ${fmtPct(p.uncapped_gross, 0, false)}.`
          : ""}
      </p>
    </div>
  );
}

function KeyPanel() {
  return (
    <Panel title="Key" aside="how to read the desk" className="ld-key ld-area-key">
      <dl className="ld-keylist">
        <dt>Consensus</dt>
        <dd>
          The average vote of the strategies, from −1 (all bearish) to +1 (all bullish). From ±0.2 it is a buy or a sell, from ±0.5 a strong
          one; in between it is neutral, which means no position.
        </dd>
        <dt>Votes</dt>
        <dd>
          <span className="ld-keyvotes">
            {(
              [
                ["bull", "bullish"],
                ["bear", "bearish"],
                ["flat", "neutral"],
                ["off", "not voting"],
              ] as const
            ).map(([k, word]) => (
              <span key={k}>
                <span className="ld-vstrip" aria-hidden="true">
                  <span className={`ld-vc ${k}`} />
                </span>
                {word}
              </span>
            ))}
          </span>
          One cell per strategy, in the same order on every row.
        </dd>
        <dt>Evidence</dt>
        <dd>
          <ul className="ld-evlist">
            {EVIDENCE_KEY.map(([level, text]) => (
              <li key={level}>
                <EvidenceBadge level={level} />
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </dd>
        <dt>Position</dt>
        <dd>What following the consensus would hold, sized by your risk profile. A suggestion, not an order: nothing is sent to a broker.</dd>
      </dl>
    </Panel>
  );
}

// ---- the inspector -------------------------------------------------------------------------------

const ZONES = [
  { from: -1, to: -0.5, label: "Strong sell" },
  { from: -0.5, to: -0.2, label: "Sell" },
  { from: -0.2, to: 0.2, label: "Neutral" },
  { from: 0.2, to: 0.5, label: "Buy" },
  { from: 0.5, to: 1, label: "Strong buy" },
];
const TICKS = [-1, -0.5, -0.2, 0, 0.2, 0.5, 1];
/** The zone a score falls in, with the thresholds of the consensus rule (trades/strategies/consensus.py). */
const zoneOf = (s: number) => (s >= 0.5 ? 4 : s >= 0.2 ? 3 : s <= -0.5 ? 0 : s <= -0.2 ? 1 : 2);

/** The consensus score on its scale, with the five zones the rule maps it to. */
function Gauge({ c }: { c: Recommendation["consensus"] }) {
  const s = clamp(c.score);
  const z = zoneOf(s);
  const tone = z > 2 ? "up" : z < 2 ? "down" : "flat";
  return (
    <div
      className="ld-gauge"
      role="img"
      aria-label={`Consensus ${fmtNum(c.score, 2, true)} on a scale from −1 (every strategy bearish) to +1 (every strategy bullish): ${c.label}.`}
    >
      <div className="ld-gauge-pinrow" aria-hidden="true">
        <span className="ld-gauge-pinlabel" style={{ left: `${Math.min(96, Math.max(4, pct(s)))}%` }}>
          {fmtNum(c.score, 2, true)}
        </span>
      </div>
      <div className="ld-gauge-band" aria-hidden="true">
        {ZONES.map((zn, i) => (
          <span
            key={zn.label}
            className={`ld-zone${i === 2 ? " neutral" : ""}${i === z ? ` on ${tone}` : ""}`}
            style={{ width: `${(zn.to - zn.from) * 50}%` }}
          >
            <span>{zn.label}</span>
          </span>
        ))}
        <span
          className={`ld-gauge-fill ${s >= 0 ? "up" : "down"}`}
          style={s >= 0 ? { left: "50%", width: `${s * 50}%` } : { right: "50%", width: `${-s * 50}%` }}
        />
        <span className="ld-gauge-pin" style={{ left: `${pct(s)}%` }} />
      </div>
      <div className="ld-gauge-scale" aria-hidden="true">
        {TICKS.map((t) => (
          <span key={t} style={{ left: `${pct(t)}%` }}>
            {tick(t)}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Where the price sits in its 52-week range. */
function RangeBar({ lo, hi, px }: { lo: number | null; hi: number | null; px: number }) {
  if (!isNum(lo) || !isNum(hi) || hi <= lo) return <>{`${fmtPrice(lo)} – ${fmtPrice(hi)}`}</>;
  const at = Math.max(0, Math.min(100, ((px - lo) / (hi - lo)) * 100));
  return (
    <span className="ld-range">
      <span className="ld-range-track" role="img" aria-label={`The price is ${Math.round(at)}% of the way from the 52-week low to the high`}>
        <span className="ld-range-pin" style={{ left: `${at}%` }} />
      </span>
      <span className="ld-range-ends">
        <span>{fmtPrice(lo)}</span>
        <span>{fmtPrice(hi)}</span>
      </span>
    </span>
  );
}

function Inspector({ rec, snap, equity, index, onSettings, onPractice }: {
  rec: Recommendation;
  snap: LiveSnapshot;
  equity: number;
  index: number;
  onSettings: () => void;
  onPractice: () => void;
}) {
  const c = rec.consensus;
  const s = rec.sizing;
  const r = rec.risk;
  const long = s.side === "long";
  return (
    <Panel
      title="Inspector"
      aside={`${rec.symbol} · ${index + 1} of ${snap.symbols.length}`}
      className="ld-insp ld-area-insp"
    >
      <div className="ld-id">
        <div className="ld-id-main">
          <div className="ld-id-tkr">
            {rec.symbol}
            <DirectionBadge score={c.score} label={c.label} />
          </div>
          <div className="ld-id-name">
            {rec.name ? `${rec.name} · ` : ""}as of {fmtDate(rec.as_of)}
            {rec.provisional ? ", bar still forming" : ""}
          </div>
        </div>
        <div className="ld-id-px">
          <Readout label="Price" value={fmtPrice(rec.price)} />
          <Readout label="Change" value={<span className={chgClass(rec.change_pct)}>{fmtPct(rec.change_pct, 2)}</span>} />
        </div>
      </div>

      <Gauge c={c} />

      <div className="ld-sec">
        <div className="ld-sec-head">
          <h3>Suggested position</h3>
          <span>
            on your {fmtMoney(equity)} account (
            <button type="button" className="linklike" onClick={onSettings}>
              Settings
            </button>
            )
          </span>
        </div>
        {s.side === "flat" ? (
          <div className="ld-posgrid ld-posflat">
            <Readout size="sm" label="Side" value="Flat" note="no position" />
            <p className="ld-expl">{s.explanation}</p>
          </div>
        ) : (
          <>
            <div className="ld-posgrid">
              <Readout
                size="sm"
                label="Side"
                value={<span className={long ? "upc" : "downc"}>{long ? "▲ LONG" : "▼ SHORT"}</span>}
                note={`conviction ${fmtPct(s.conviction, 0, false)}`}
              />
              <Readout size="sm" label="Shares" value={s.shares.toLocaleString()} note={fmtMoney(s.notional)} />
              <Readout size="sm" label="Weight" value={fmtNum(Math.abs(s.weight) * 100, 1)} unit="%" note="of equity" />
              <Readout size="sm" label="Stop" value={fmtPrice(s.stop)} note={`${fmtPrice(s.stop_distance)} away`} />
              <Readout
                size="sm"
                label="Risk at stop"
                value={fmtMoney(s.risk_amount)}
                note={`${fmtPct(equity ? s.risk_amount / equity : null, 2, false)} of equity`}
              />
            </div>
            <p className="ld-expl">{s.explanation.replace(/ -> /g, " → ")}</p>
          </>
        )}
        <p className="ld-fine">
          Sizing rules come from your settings (risk per trade, stop distance, position cap). Nothing is sent to a broker.
        </p>
      </div>

      <div className="ld-sec ld-kvs">
        <div>
          <div className="ld-sec-head">
            <h3>Consensus</h3>
          </div>
          <KeyValues
            rows={[
              [
                "Score",
                <>
                  {fmtNum(c.score, 2, true)} <span className="muted">on −1 … +1</span>
                </>,
              ],
              ["Bullish", <span key="b" className={c.bullish ? "upc" : ""}>{`${c.bullish} of ${c.n_votes} votes`}</span>],
              ["Bearish", <span key="s" className={c.bearish ? "downc" : ""}>{`${c.bearish} of ${c.n_votes} votes`}</span>],
              ["Neutral", `${c.neutral} of ${c.n_votes} votes`],
              [
                <span className="ld-label-help" key="a">
                  Agreement <Help text={HELP.agreement} />
                </span>,
                fmtPct(c.agreement, 0, false),
              ],
              [
                <span className="ld-label-help" key="w">
                  Weighting <Help text={HELP.weighting} />
                </span>,
                c.weighting === "equal" ? "Equal" : "By category",
              ],
              ["New today", rec.fresh_signals.length ? rec.fresh_signals.join(", ") : "no change"],
            ]}
          />
        </div>
        <div>
          <div className="ld-sec-head">
            <h3>Market risk</h3>
          </div>
          <KeyValues
            rows={[
              [
                <span className="ld-label-help" key="atr">
                  ATR (20) <Help text={HELP.atr} />
                </span>,
                <>
                  {fmtPrice(r.atr)} <span className="muted">({fmtPct(r.atr_pct, 1, false)})</span>
                </>,
              ],
              ["Volatility (20 bars)", fmtPct(r.vol_20, 0, false)],
              ["Volatility (1 year)", fmtPct(r.vol_1y, 0, false)],
              ["Avg. traded value / bar", `$${fmtCompact(r.avg_dollar_volume)}`],
              ["52-week range", <RangeBar key="rng" lo={r.low_52w} hi={r.high_52w} px={rec.price} />],
            ]}
          />
        </div>
      </div>

      {r.flags.length ? (
        <ul className="ld-feed ld-flags" aria-label="Notes on this symbol">
          {r.flags.map((f, i) => {
            const forming = rec.provisional && i === 0;
            return (
              <li key={f}>
                <Tag kind={forming ? "info" : "warn"}>{forming ? "Info" : "Warn"}</Tag>
                <span>{f}</span>
              </li>
            );
          })}
        </ul>
      ) : null}

      {snap.provider !== "synthetic" ? (
        <div className="ld-sec">
          <button className="btn small" onClick={onPractice}>
            Practise {rec.symbol} on hidden history
          </button>
        </div>
      ) : null}
    </Panel>
  );
}

function EmptyInspector({ snap, selected, busy, onStart }: { snap: LiveSnapshot; selected: string | null; busy: boolean; onStart: () => void }) {
  const err = selected ? snap.errors[selected] ?? snap.errors._quotes : undefined;
  return (
    <Panel title="Inspector" className="ld-insp ld-area-insp">
      <Hatch />
      <div className="ld-empty">
        {!snap.symbols.length ? (
          <p>Add a symbol to your watchlist to get started.</p>
        ) : selected && err ? (
          <ErrorBox error={`Could not load ${selected}: ${err}`} />
        ) : !snap.running ? (
          <>
            <p>The live feed is paused. Press Start feed to resume.</p>
            <button className="btn primary" onClick={onStart} disabled={busy}>
              Start feed
            </button>
          </>
        ) : (
          <>
            <Spinner label="Computing recommendations..." />
            <p className="ld-fine">The first pass backtests every strategy on every symbol, so it can take a few seconds.</p>
          </>
        )}
      </div>
    </Panel>
  );
}

// ---- the chart -----------------------------------------------------------------------------------

function ChartFigure({ rec, snap, strategy, onStrategy }: {
  rec: Recommendation;
  snap: LiveSnapshot;
  strategy: string;
  onStrategy: (id: string) => void;
}) {
  const colors = useChartColors();
  const singleVotes = rec.votes.filter((v) => !v.group);
  const [chart, setChart] = useState<ChartResponse | null>(null);
  const [chartErr, setChartErr] = useState<string | null>(null);
  const lastFetch = useRef(0);
  const inflight = useRef(false);
  // The strategy now wanted on the chart, and the loader that fetches it: a switch made while a
  // request is in flight is fetched as soon as that request returns.
  const wanted = useRef(strategy);
  const latestLoad = useRef<() => Promise<void>>(async () => undefined);
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
    if (wanted.current !== strategy) void latestLoad.current();
  }, [rec.symbol, strategy]);

  useEffect(() => {
    wanted.current = strategy;
    latestLoad.current = load;
    void load();
  }, [load, strategy]);
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
  const s = rec.sizing;
  const name = singleVotes.find((v) => v.strategy_id === strategy)?.strategy_name ?? strategy;
  const bars = snap.timeframe === "1d" ? "daily bars" : `${snap.timeframe} bars`;

  return (
    <Figure
      fig="Fig. 1"
      title={
        <>
          {rec.symbol} · {bars}
          <span className="sr-only"> with the signals of {name}</span>
        </>
      }
      aside={
        singleVotes.length ? (
          <label className="ld-overlay">
            <span>Signals of</span>
            <select className="input ld-overlay-select" value={strategy} onChange={(e) => onStrategy(e.target.value)}>
              {singleVotes.map((v) => (
                <option key={v.strategy_id} value={v.strategy_id}>
                  {v.strategy_name}
                </option>
              ))}
            </select>
          </label>
        ) : undefined
      }
      className="ld-chart"
    >
      {chart?.explanation ? (
        <p className="ld-say">
          <span aria-hidden="true">◆</span>
          {chart.explanation.headline}
        </p>
      ) : null}
      <ErrorBox error={chartErr} />
      {chart ? (
        <PriceChart
          bars={chart.bars}
          overlays={overlays}
          markers={markers}
          priceLines={s.stop !== null && s.side !== "flat" ? [{ price: s.stop, label: "Suggested stop", color: colors.axisText, style: "dashed" }] : []}
          height={280}
          intraday={snap.timeframe !== "1d"}
          follow
          initialBars={160}
          label={`${rec.symbol} price chart`}
        />
      ) : (
        <div className="ld-chart-wait">
          <Spinner label="Loading chart..." />
        </div>
      )}
    </Figure>
  );
}

// ---- why: strategy by strategy -------------------------------------------------------------------

function WhyPanel({ rec, overlay, strategies, onChart }: {
  rec: Recommendation;
  overlay: string;
  strategies: Map<string, StrategyMeta>;
  onChart: (id: string) => void;
}) {
  const keys = voteKeys(rec.votes);
  // Open rows stay open as you move between symbols, to compare one strategy across the watchlist.
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const allOpen = keys.length > 0 && keys.every((k) => open.has(k));
  const setOne = (k: string, on: boolean) =>
    setOpen((cur) => {
      if (cur.has(k) === on) return cur;
      const next = new Set(cur);
      if (on) next.add(k);
      else next.delete(k);
      return next;
    });
  const c = rec.consensus;
  return (
    <Panel
      title="Why: strategy by strategy"
      aside={`${rec.symbol} · ${c.bullish} bullish · ${c.bearish} bearish · ${c.neutral} neutral`}
      actions={
        <button type="button" className="btn small" onClick={() => setOpen(allOpen ? new Set() : new Set(keys))} aria-expanded={allOpen}>
          {allOpen ? "Collapse all" : "Expand all"}
        </button>
      }
      className="ld-why"
    >
      <p className="ld-why-intro">
        Each vote comes with the rule it applied and how that rule has done on {rec.symbol}. Open a row for the rule checks and the full track
        record.
      </p>
      <div className="ld-why-list">
        <div className="ld-vrow ld-vhead">
          <span>Vote</span>
          <span>Strategy</span>
          <span className="ld-v-ev">Evidence</span>
          <span>Reason</span>
          <span className="ld-v-rec">
            <span className="ld-label-help">
              Track record <Help text={HELP.record} />
            </span>
          </span>
          <span className="ld-v-since">Since</span>
          <span />
        </div>
        {rec.votes.map((v, i) => (
          <VoteRow
            key={keys[i]}
            vote={v}
            symbol={rec.symbol}
            open={open.has(keys[i])}
            onToggle={(on) => setOne(keys[i], on)}
            onChart={!v.group ? () => onChart(v.strategy_id) : undefined}
            onFigure={!v.group && overlay === v.strategy_id}
            summary={strategies.get(v.strategy_id)?.summary}
          />
        ))}
      </div>
    </Panel>
  );
}

function RecordBrief({ ev }: { ev: Evidence | null }) {
  if (!ev) return <span className="muted">–</span>;
  if (ev.error) return <span className="muted">unavailable</span>;
  return (
    <>
      <span>
        <span className="ld-k">CAGR</span> {fmtPct(ev.cagr)} <span className="muted">B&amp;H {fmtPct(ev.benchmark_cagr)}</span>
      </span>
      <span>
        <span className="ld-k">Sharpe</span> {fmtNum(ev.sharpe ?? null)}{" "}
        <span className="muted">
          · {isNum(ev.n_trades) ? ev.n_trades : "–"} trade{ev.n_trades === 1 ? "" : "s"}
        </span>
      </span>
    </>
  );
}

function VoteRow({ vote, symbol, open, onToggle, onChart, onFigure, summary }: {
  vote: Vote;
  symbol: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  onChart?: () => void;
  onFigure: boolean;
  summary?: string;
}) {
  const ev = vote.evidence;
  return (
    <details className={`ld-vote ${voteKind(vote)}`} open={open} onToggle={(e) => onToggle(e.currentTarget.open)}>
      <summary className="ld-vrow">
        <span className="ld-v-glyph">
          <VoteGlyph vote={vote.vote} state={vote.state} />
        </span>
        <span className="ld-v-name">
          <span className="ld-v-title">{vote.strategy_name}</span>
          <span className="ld-v-cat">
            {vote.category}
            {vote.group ? ` · ${vote.group.join(" / ")}` : ""}
          </span>
        </span>
        <span className="ld-v-ev">
          <EvidenceBadge level={vote.evidence_level} />
        </span>
        <span className="ld-v-reason">
          {vote.fresh ? <Tag kind="warn">New signal</Tag> : null}
          {vote.headline}
        </span>
        <span className="ld-v-rec">
          <RecordBrief ev={ev} />
        </span>
        <span className="ld-v-since">{vote.since ? fmtDate(vote.since) : "–"}</span>
        <span className="ld-v-tog" aria-hidden="true" />
      </summary>
      <div className="ld-v-body">
        <div>
          <h4 className="ld-k">Rules checked</h4>
          {vote.rules.length ? (
            <ul className="ld-rules">
              {vote.rules.map((r) => (
                <li key={r.label}>
                  <span className={`ld-mark ${r.passed === true ? "ok" : r.passed === false ? "no" : ""}`} aria-hidden="true">
                    {r.passed === true ? "✓" : r.passed === false ? "✕" : "•"}
                  </span>
                  <span className="sr-only">{r.passed === true ? "Passed: " : r.passed === false ? "Not met: " : ""}</span>
                  <span>{r.label}</span>
                  <span className="ld-val">{r.value}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ld-fine">This strategy reports no rule checks for this vote.</p>
          )}
          <p className="ld-v-exit">
            {vote.since ? `In this state since ${fmtDate(vote.since)}. ` : ""}
            {vote.exit_rule}
          </p>
          {summary ? (
            <p className="ld-v-what">
              <span className="ld-k">The rule</span> {summary}
            </p>
          ) : null}
        </div>
        <div>
          {ev && !ev.error ? (
            <>
              <h4 className="ld-k">
                Track record on {vote.group ? vote.group.join(" / ") : symbol} (last {fmtNum(ev.years ?? null, 1)} years, after costs)
              </h4>
              <table className="ld-record">
                <thead>
                  <tr>
                    <th />
                    <th scope="col">Strategy</th>
                    <th scope="col" className="ld-bench">
                      Buy &amp; hold
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">
                      <span className="ld-label-help">
                        CAGR <Help text={HELP.cagr} />
                      </span>
                    </th>
                    <td>{fmtPct(ev.cagr)}</td>
                    <td className="ld-bench">{fmtPct(ev.benchmark_cagr)}</td>
                  </tr>
                  <tr>
                    <th scope="row">Sharpe</th>
                    <td>{fmtNum(ev.sharpe ?? null)}</td>
                    <td className="ld-bench">{fmtNum(ev.benchmark_sharpe ?? null)}</td>
                  </tr>
                  <tr>
                    <th scope="row">
                      <span className="ld-label-help">
                        Max drawdown <Help text={HELP.maxdd} />
                      </span>
                    </th>
                    <td>{fmtPct(ev.max_drawdown)}</td>
                    <td className="ld-bench">{fmtPct(ev.benchmark_max_drawdown)}</td>
                  </tr>
                  <tr>
                    <th scope="row">Win rate</th>
                    <td>{fmtPct(ev.win_rate, 0, false)}</td>
                    <td />
                  </tr>
                  <tr>
                    <th scope="row">Trades</th>
                    <td>{isNum(ev.n_trades) ? ev.n_trades : "–"}</td>
                    <td />
                  </tr>
                  <tr>
                    <th scope="row">
                      <span className="ld-label-help">
                        P(SR&gt;0) <Help text={HELP.psr} />
                      </span>
                    </th>
                    <td>{fmtPct(ev.psr, 0, false)}</td>
                    <td />
                  </tr>
                </tbody>
              </table>
              {isNum(ev.n_trades) && ev.n_trades < 30 ? (
                <p className="ld-fine">Only {ev.n_trades} trades: treat this track record as anecdotal.</p>
              ) : null}
            </>
          ) : ev?.error ? (
            <p className="ld-fine">Track record unavailable: {ev.error}</p>
          ) : (
            <p className="ld-fine">No track record for this vote.</p>
          )}
          <div className="ld-v-links">
            {onChart ? (
              onFigure ? (
                <Tag kind="ink">On Fig. 1</Tag>
              ) : (
                <button type="button" className="btn small" onClick={onChart}>
                  Show on chart
                </button>
              )
            ) : null}
            <a className="small" href={`#/library?strategy=${vote.strategy_id}`}>
              How this strategy works
            </a>
          </div>
        </div>
      </div>
    </details>
  );
}
