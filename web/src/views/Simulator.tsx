import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type OrderRequest } from "../api";
import { useApp, useStatusLine, type Route } from "../state";
import StrategySim, {
  GroupLabel,
  Kbd,
  OptionTile,
  ReadoutGrid,
  Segmented,
  Timeline,
  Transport,
  TransportIcon,
  UnitInput,
  pctParts,
  toneOf,
  type TimelineMark,
  type TimelineSegment,
} from "./StrategySim";
import type { Bars, Meta, Settings, SimHistoryRow, SimState } from "../types";
import PriceChart, { type PriceLineSpec, type PriceMarker } from "../components/PriceChart";
import LineChart, { type LineSpec } from "../components/LineChart";
import { DirectionBadge, ErrorBox, Help, ReferenceList, Spinner, Tabs, TradesTable, VoteGlyph } from "../components/ui";
import { Figure, Hatch, Panel, Readout, Tag, type TagKind } from "../components/instrument";
import { fmtDate, fmtMoney, fmtNum, fmtPct, fmtPrice, isNum, pnlClass } from "../format";
import { useChartColors } from "../theme";
import "../styles/simulator.css";

export default function Simulator({ route }: { route: Route }) {
  const { navigate } = useApp();
  const practice = route.params.get("mode") === "practice" || route.params.has("symbol");
  const runId = route.params.get("run");
  return (
    <div className="stack">
      <div className="sim-modes">
        <Tabs
          tabs={[
            ["strategies", "Test strategies"],
            ["practice", "Practice trading yourself"],
          ]}
          value={practice ? "practice" : "strategies"}
          onChange={(m) => navigate("/sim", m === "practice" ? { mode: "practice" } : {})}
        />
      </div>
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

function firstSentence(text: string): string {
  const i = text.indexOf(". ");
  return i > 0 ? text.slice(0, i + 1) : text;
}

/** Bar number (0 = the session's first decision) of each of your fills, as marks for the timeline. */
function fillMarks(state: SimState, bars: Bars): TimelineMark[] {
  const index = new Map(bars.t.map((t, i) => [t, i]));
  return state.fills.map((f) => {
    const at = (index.get(f.time) ?? state.cursor) - state.start_index;
    const tag = f.tag && f.tag !== "entry" && f.tag !== "exit" ? ` (${f.tag.replace("_", "-")})` : "";
    return {
      at,
      kind: f.qty > 0 ? "buy" : "sell",
      title: `Bar ${at}: ${f.qty > 0 ? "bought" : "sold"} ${Math.abs(f.qty).toLocaleString()} @ ${fmtPrice(f.price)}${tag}`,
    };
  });
}

function lastReturn(curve: { v: (number | null)[] }): number | null {
  const v = curve.v.filter(isNum);
  return v.length > 1 && v[0] ? v[v.length - 1] / v[0] - 1 : null;
}

const ADVICE_LABEL: Record<string, string> = { end: "advice after the end", live: "advice while trading", off: "no advice" };

// ------------------------------------------------------------------------------------
// Setup
// ------------------------------------------------------------------------------------

const PRACTICE_LENGTHS: [number, string][] = [
  [60, "A quarter"],
  [125, "Six months"],
  [250, "One year"],
  [500, "Two years"],
];

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

  const scenarioMeta = meta.scenarios.find((s) => s.id === scenario);
  const presetMeta = preset ? meta.presets.find((p) => p.id === preset) : undefined;
  const market =
    source === "scenario"
      ? `the **${scenarioMeta?.label ?? scenario}** scenario`
      : presetMeta
        ? blind
          ? "a hidden period of real history"
          : `**${presetMeta.label}**`
        : symbol
          ? `**${symbol}**${start ? ` from ${start}` : ""}`
          : "a random large company and year";
  useStatusLine(
    loading
      ? source === "history"
        ? "Downloading the history for your session..."
        : "Generating your market..."
      : error
        ? "The session did not start; the reason is next to **Start session**. Try another market or data source."
        : `Practice on ${market}: ${length} trading days with ${fmtMoney(cash)}. Press **Start session**; the future stays hidden while you trade.`,
  );

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
      <div className="stack">
        <header className="card marks sim-intro">
          <div className="sim-intro-text">
            <div className="sim-kicker">Simulator · practice</div>
            <h1>Practice trading yourself</h1>
            <p>
              Practise decisions one bar at a time with a paper account. The future stays hidden. Race the strategies, then get a
              scorecard that grades your <b>process</b> separately from your <b>outcome</b>.
            </p>
          </div>
          <ol className="sim-steps" aria-label="How it works">
            <li>
              <b>01</b> Pick a market
            </li>
            <li>
              <b>02</b> Set the session
            </li>
            <li>
              <b>03</b> Pick your rivals
            </li>
            <li>
              <b>
                <TransportIcon kind="play" />
              </b>
              Trade bar by bar
            </li>
          </ol>
        </header>

        <Panel fig="01" title="Market" aside={source === "scenario" ? "synthetic scenario" : "real history"}>
          <Segmented
            label="Market source"
            className="sim-source"
            value={source}
            onChange={setSource}
            options={[
              ["scenario", "Synthetic scenario"],
              ["history", "Real history"],
            ]}
          />
          {source === "scenario" ? (
            <div className="sim-section">
              <div>
                <div className="sim-tiles" role="group" aria-label="Scenario">
                  {meta.scenarios.map((s) => (
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
            </div>
          ) : (
            <div className="sim-section">
              {!realProviders.length ? (
                <div className="callout warn">Real history needs a data provider. Yahoo Finance works without a key.</div>
              ) : null}
              <div className="sim-tiles" role="group" aria-label="Period">
                {meta.presets.map((p) => (
                  <OptionTile
                    key={p.id}
                    kicker={blind ? "Hidden until the end" : `${p.symbol} from ${p.start}`}
                    name={p.label}
                    desc={blind ? undefined : p.description}
                    pressed={preset === p.id}
                    onClick={() => setPreset(p.id)}
                  />
                ))}
                <OptionTile
                  kicker="Your pick"
                  name="Custom or random"
                  desc="Pick a ticker and start date, or leave them blank for a random large company and year."
                  pressed={preset === null}
                  onClick={() => setPreset(null)}
                />
              </div>
              {preset === null ? (
                <div className="sim-fields">
                  <label className="field">
                    <span>Symbol (blank = random)</span>
                    <input className="input num" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="e.g. AAPL" />
                  </label>
                  <label className="field">
                    <span>Session start (blank = random)</span>
                    <input className="input num" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
                  </label>
                </div>
              ) : null}
              <div className="sim-split">
                <label className="field sim-provider">
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
                <span className="sim-blind">
                  <label className="check" title="Hides the ticker and dates and rescales prices to 100, so you can't rely on remembering what happened.">
                    <input type="checkbox" checked={blind} onChange={(e) => setBlind(e.target.checked)} />
                    Blind mode (recommended: prevents hindsight bias)
                  </label>
                  <Help text="Hides the ticker and dates and rescales prices to 100, so you can't rely on remembering what happened." />
                </span>
              </div>
            </div>
          )}
        </Panel>

        <Panel fig="02" title="Session" aside="your paper account">
          <div className="sim-fields">
            <label className="field">
              <span>Starting cash</span>
              <UnitInput prefix="$" type="number" value={cash} min={100} onChange={(e) => setCash(Number(e.target.value))} />
            </label>
            <label className="field" title="Adverse price impact per fill.">
              <span>
                Slippage <Help text="Adverse price impact per fill, in basis points: 1 bp is 0.01% of the trade." />
              </span>
              <UnitInput unit="bps" type="number" step={0.5} value={slippage} onChange={(e) => setSlippage(Number(e.target.value))} />
            </label>
          </div>
          <div className="sim-section">
            <div>
              <GroupLabel>
                Length <span className="sim-group-count">trading days</span>
              </GroupLabel>
              <div className="sim-tiles sim-tiles-lens" role="group" aria-label="Length">
                {PRACTICE_LENGTHS.map(([v, k]) => (
                  <OptionTile key={v} size="sm" kicker={k} name={String(v)} desc="trading days" pressed={length === v} onClick={() => setLength(v)} />
                ))}
              </div>
            </div>
            <div>
              <GroupLabel>Show strategy advice</GroupLabel>
              <div className="sim-tiles sim-tiles-advice" role="group" aria-label="Show strategy advice">
                <OptionTile
                  kicker="Recommended"
                  name="After the session"
                  desc="Decide for yourself first; see how the strategies traded in the scorecard."
                  pressed={reveal === "end"}
                  onClick={() => setReveal("end")}
                />
                <OptionTile
                  kicker="Coached"
                  name="While trading"
                  desc="Each strategy's current view sits beside your order ticket."
                  pressed={reveal === "live"}
                  onClick={() => setReveal("live")}
                />
                <OptionTile kicker="Solo" name="Never" desc="No strategy advice in this session." pressed={reveal === "off"} onClick={() => setReveal("off")} />
              </div>
            </div>
          </div>
          <label className="check sim-check">
            <input type="checkbox" checked={allowShort} onChange={(e) => setAllowShort(e.target.checked)} />
            Allow short selling
          </label>
        </Panel>

        <Panel fig="03" title="Strategies to race against" aside={`${advisors.length} of ${singles.length} picked, plus buy-and-hold`}>
          <div className="sim-rivals">
            {singles.map((s) => (
              <label key={s.id} className="sim-rival" data-checked={advisors.includes(s.id)} title={s.summary}>
                <input
                  type="checkbox"
                  checked={advisors.includes(s.id)}
                  onChange={(e) => setAdvisors(e.target.checked ? [...advisors, s.id] : advisors.filter((x) => x !== s.id))}
                />
                <span className="sim-rival-name">{s.name}</span>
                <span className="sim-rival-cat">{s.category}</span>
              </label>
            ))}
          </div>
        </Panel>

        <div className="sim-launch" role="region" aria-label="Start the session">
          {error ? (
            <div className="sim-launch-error">
              <ErrorBox error={error} />
            </div>
          ) : null}
          <dl className="sim-launch-sum">
            <div className="sim-launch-key">
              <dt>Market</dt>
              <dd>{source === "scenario" ? (scenarioMeta?.label ?? scenario) : presetMeta ? presetMeta.label : symbol || "Random"}</dd>
            </div>
            <div className="sim-launch-key">
              <dt>Length</dt>
              <dd>{length} days</dd>
            </div>
            <div>
              <dt>Cash</dt>
              <dd>{fmtMoney(cash)}</dd>
            </div>
            <div>
              <dt>Advice</dt>
              <dd>{{ end: "After", live: "Live", off: "Never" }[reveal]}</dd>
            </div>
            <div>
              <dt>Rivals</dt>
              <dd>{advisors.length} + B&amp;H</dd>
            </div>
            <div>
              <dt>Shorts</dt>
              <dd>{allowShort ? "Allowed" : "Off"}</dd>
            </div>
          </dl>
          <div className="sim-launch-go">
            {loading ? <Spinner label={source === "history" ? "Downloading history..." : "Generating market..."} /> : null}
            <button className="btn primary sim-go" onClick={() => void start_()} disabled={loading}>
              <TransportIcon kind="play" />
              {loading ? "Preparing..." : "Start session"}
            </button>
          </div>
        </div>
      </div>

      {history.length ? (
        <Panel title="Your progress" aside="completed sessions (stored locally)">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Session</th>
                  <th className="num hide-sm">Bars</th>
                  <th className="num">Your return</th>
                  <th className="num">Buy &amp; hold</th>
                  <th className="num hide-sm">Max DD</th>
                  <th className="num hide-sm">Trades</th>
                  <th className="num">Process score</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td className="nowrap mono">{new Date(h.finished_at * 1000).toLocaleDateString()}</td>
                    <td>
                      {h.title} <span className="sim-dim">{h.symbol}</span>
                    </td>
                    <td className="num hide-sm">{h.bars}</td>
                    <td className={`num ${pnlClass(h.return)}`}>{fmtPct(h.return)}</td>
                    <td className="num">{fmtPct(h.benchmark_return)}</td>
                    <td className="num hide-sm">{fmtPct(h.max_drawdown)}</td>
                    <td className="num hide-sm">{fmtNum(h.trades, 0)}</td>
                    <td className="num">{h.process_score ?? "–"}</td>
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
  const marks = useMemo(() => fillMarks(state, bars), [state, bars]);

  const lastClose = bars.c[bars.c.length - 1] as number;
  const a = atr(bars);
  const { done, total } = state.progress;
  const segments: TimelineSegment[] = done > 0 ? [{ label: "", n: done, tone: "plain", title: `${done} bars played` }] : [];
  const bench = lastReturn(state.benchmark_curve);
  const posText = pos.qty
    ? `**${pos.qty > 0 ? "long" : "short"} ${Math.abs(pos.qty).toLocaleString()} shares** (unrealised ${fmtMoney(pos.unrealized_pnl, 0, true)})`
    : `flat with **${fmtMoney(state.account.cash)}** in cash`;
  useStatusLine(
    playing
      ? `Playing at ${speed} bar${speed === 1 ? "" : "s"}/s. You are ${posText}. Press \`P\` to pause and trade.`
      : `Bar ${done} of ${total}, ${fmtDate(state.time)}. You are ${posText}; your return **${fmtPct(state.account.return_pct, 2)}**${isNum(bench) ? ` vs buy-and-hold ${fmtPct(bench, 2)}` : ""}. Place an order, or press \`N\` for the next bar.`,
  );
  const source = String(state.config.source ?? "");
  const kicker = [
    "Practice",
    source === "history" ? "real history" : source === "scenario" ? "synthetic scenario" : "",
    ADVICE_LABEL[state.config.reveal] ?? "",
    state.config.allow_short ? "shorts allowed" : "long only",
    `${fmtMoney(state.config.initial_cash)} to start`,
  ].filter(Boolean);

  return (
    <div className="stack sim-session">
      <section className="card marks sim-session-head">
        <div className="sim-kicker">{kicker.join(" · ")}</div>
        <h1>
          {state.title} <span className="sim-sym">{state.symbol}</span>
        </h1>
        <p>{state.description}</p>
      </section>
      <Transport
        label="Playback"
        controls={
          <>
            <button className="btn primary" onClick={() => void step(1)} disabled={busy || playing}>
              <TransportIcon kind="step" />
              Next bar <Kbd>N</Kbd>
            </button>
            <button className="btn" onClick={() => void step(5)} disabled={busy || playing}>
              +5
            </button>
            <button className="btn" onClick={() => void step(20)} disabled={busy || playing}>
              +20
            </button>
            <button className="btn sim-play" onClick={() => setPlaying(!playing)} aria-pressed={playing}>
              <TransportIcon kind={playing ? "pause" : "play"} />
              {playing ? "Pause" : "Play"} <Kbd>P</Kbd>
            </button>
            <select className="input sim-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
              <option value={1}>1 bar/s</option>
              <option value={2}>2 bars/s</option>
              <option value={4}>4 bars/s</option>
            </select>
          </>
        }
        timeline={
          <Timeline
            total={total}
            done={done}
            segments={segments}
            marks={marks}
            label="Progress"
            valueText={`Bar ${done} of ${total}`}
            futureLabel="Not played yet · hidden"
          />
        }
        position={
          <>
            <span className="sim-tp-bar">
              <span className="label">Bar</span> <b>{done}</b>
              <span className="sim-dim"> / {total}</span>
            </span>
            <span className="sim-tp-date">{fmtDate(state.time)}</span>
          </>
        }
        end={
          <button className="btn ghost" onClick={() => void finish()}>
            <TransportIcon kind="stop" />
            Finish
          </button>
        }
      />
      <ErrorBox error={error} />
      {lastFills ? (
        <div className="sim-notice" role="status">
          <Tag kind="ink">Filled</Tag>
          <span>{lastFills}</span>
        </div>
      ) : null}

      <div className="sim-grid">
        <div className="stack">
          <Figure fig="FIG. 1" title={<>Price · {state.symbol}</>} aside="your fills as arrows · entry, stop and target as lines" className="sim-price">
            <PriceChart bars={bars} markers={markers} priceLines={priceLines} height={380} follow initialBars={180} label="Simulator price chart" />
          </Figure>
          <RaceCard fig="FIG. 2" state={state} />
          <Panel title="Your trades" aside={state.trades.length ? `${state.trades.length} · newest first` : undefined}>
            <TradesTable trades={state.trades} showSymbol={false} />
          </Panel>
        </div>
        <div className="stack">
          <AccountCard state={state} />
          <OrderTicket state={state} lastClose={lastClose} atrValue={a} accept={accept} />
          <OrdersCard state={state} accept={accept} />
          {state.config.reveal === "live" ? (
            <AdvisorsCard state={state} />
          ) : (
            <Panel title="Strategy advice" aside={state.config.reveal === "end" ? "after the session" : "off"}>
              <Hatch />
              <p className="sim-empty-line">
                {state.config.reveal === "end"
                  ? "The strategies' opinions are hidden until the end, so your decisions are your own. You'll see how they traded in the scorecard."
                  : "Strategy advice is turned off for this session."}
              </p>
            </Panel>
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
    <Panel title="Account" aside="paper money" className="sim-account">
      <ReadoutGrid>
        <Readout size="sm" label="Equity" value={fmtMoney(a.equity)} />
        <Readout size="sm" label="Return" {...pctParts(a.return_pct, 2)} tone={toneOf(a.return_pct)} />
        <Readout
          size="sm"
          label="Position"
          value={p.qty ? Math.abs(p.qty).toLocaleString() : "Flat"}
          unit={p.qty ? "sh" : undefined}
          note={p.qty ? (p.qty > 0 ? "long" : "short") : "no shares held"}
        />
      </ReadoutGrid>
      <dl className="kv sim-kv">
        <dt>Cash</dt>
        <dd>{fmtMoney(a.cash)}</dd>
        <dt>Buying power</dt>
        <dd>{fmtMoney(a.buying_power)}</dd>
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
    </Panel>
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
  /** How far a protective level sits from the entry: signed per cent, and in ATRs. */
  const distance = (level: string) => {
    const v = Number(level);
    if (!level || !isNum(v) || !entryRef) return null;
    const pct = v / entryRef - 1;
    return `${fmtPct(pct, 1)}${atrValue ? ` · ${fmtNum(Math.abs(v - entryRef) / atrValue, 1)} ATR` : ""}`;
  };

  return (
    <Panel title="Order ticket" aside="fills at the next bar" className={`sim-ticket sim-ticket-${side}`}>
      <div className="sim-ticket-body">
        <div className="segmented buy-sell sim-ticket-side" role="group" aria-label="Side">
          <button data-side="buy" aria-pressed={side === "buy"} onClick={() => setSide("buy")}>
            Buy
          </button>
          <button data-side="sell" aria-pressed={side === "sell"} onClick={() => setSide("sell")}>
            Sell{pos > 0 ? "" : state.config.allow_short ? " short" : ""}
          </button>
        </div>
        <div className="sim-ticket-row">
          <label className="field sim-ticket-qty">
            <span>Shares</span>
            <UnitInput unit="sh" type="number" min={0} value={qty || ""} onChange={(e) => setQty(Math.max(0, Math.floor(Number(e.target.value))))} />
          </label>
          <div className="sim-ticket-quick" role="group" aria-label="Quick size">
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
        </div>
        <div className="sim-ticket-row">
          <div className="field">
            <span>
              Type{" "}
              <Help text="Market: fills at the next bar's open. Limit: fills only at your price or better. Stop: becomes a market order once the price touches your stop price." />
            </span>
            <Segmented
              label="Order type"
              className="sim-ticket-type"
              value={type}
              onChange={setType}
              options={[
                ["market", "Market"],
                ["limit", "Limit"],
                ["stop", "Stop"],
              ]}
            />
          </div>
          {type !== "market" ? (
            <label className="field sim-ticket-price">
              <span>{type === "limit" ? "Limit price" : "Stop price"}</span>
              <UnitInput prefix="$" type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} placeholder={lastClose.toFixed(2)} />
            </label>
          ) : (
            <div className="sim-ticket-mkt">
              <span className="label">Next open, near</span>
              <b className="num">{fmtPrice(lastClose)}</b>
            </div>
          )}
        </div>
        {!exiting ? (
          <fieldset className="sim-protect">
            <legend className="label">Protective orders</legend>
            <div className="sim-protect-row">
              <label className="check">
                <input type="checkbox" checked={useStop} onChange={(e) => setUseStop(e.target.checked)} />
                Stop-loss
              </label>
              <Help text="Default: 2 x ATR(20) from the last close, a common volatility-based stop. It becomes an order that closes the position if hit. ATR, the average true range, is the typical size of one bar's move." />
              <UnitInput
                prefix="$"
                type="number"
                step="0.01"
                value={stopLoss}
                disabled={!useStop}
                onChange={(e) => setStopLoss(e.target.value)}
                aria-label="Stop-loss price"
              />
              <span className="sim-protect-dist">{useStop ? distance(stopLoss) : "off"}</span>
            </div>
            <div className="sim-protect-row">
              <label className="check">
                <input type="checkbox" checked={useTarget} onChange={(e) => setUseTarget(e.target.checked)} />
                Take-profit
              </label>
              <Help text="Default: 3 x ATR(20) from the last close. A limit order that closes the position at this price to lock in the gain; it is cancelled if the stop-loss fills first." />
              <UnitInput
                prefix="$"
                type="number"
                step="0.01"
                value={target}
                disabled={!useTarget}
                onChange={(e) => setTarget(e.target.value)}
                aria-label="Take-profit price"
              />
              <span className="sim-protect-dist">{useTarget ? distance(target) : "off"}</span>
            </div>
          </fieldset>
        ) : null}
        <label className="field">
          <span>Why are you making this trade?</span>
          <textarea
            className="input"
            rows={2}
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Your reasoning, written before you know the outcome"
          />
        </label>
        <dl className="sim-ticket-est">
          <dt>Notional</dt>
          <dd>
            ≈ {fmtMoney(est)} <span className="sim-dim">at {fmtPrice(entryRef)}</span>
          </dd>
          {risk !== null ? (
            <>
              <dt>At risk</dt>
              <dd>
                loses ≈ {fmtMoney(risk)} <span className="sim-dim">({fmtPct(risk / state.account.equity, 1, false)} of equity) if the stop is hit</span>
              </dd>
            </>
          ) : null}
        </dl>
        <button className={`btn ${side === "buy" ? "buy" : "sell"} sim-ticket-go`} onClick={() => void submit()} disabled={busy || qty <= 0}>
          {side === "buy" ? "Place buy order" : "Place sell order"}
          {qty > 0 ? <span className="sim-ticket-go-qty">{qty.toLocaleString()} sh</span> : null}
        </button>
        <ErrorBox error={error} />
      </div>
    </Panel>
  );
}

const FILL_TAG: Record<string, string> = { stop_loss: "Stop", take_profit: "Take-profit", entry: "Entry", exit: "Exit" };

function OrdersCard({ state, accept }: { state: SimState; accept: (s: SimState) => void }) {
  const [error, setError] = useState<string | null>(null);
  const open = state.orders.filter((o) => o.status === "open" || o.status === "pending");
  const recent = state.orders.filter((o) => !(o.status === "open" || o.status === "pending")).slice(-6).reverse();
  const fills = state.fills.slice(-8).reverse();
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
    <Panel
      title="Orders"
      aside={open.length ? `${open.length} working` : undefined}
      actions={
        state.account.position.qty ? (
          <button className="btn small" onClick={() => void close()}>
            Close position
          </button>
        ) : null
      }
      className="sim-orders"
    >
      <ErrorBox error={error} />
      {!open.length && !recent.length ? <p className="sim-empty-line">No orders yet. Use the ticket above; an order fills at the next bar.</p> : null}
      {open.length ? (
        <table className="data sim-mini">
          <thead>
            <tr>
              <th>Side</th>
              <th className="num">Qty</th>
              <th>Order</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {open.map((o) => (
              <tr key={o.id}>
                <td>
                  <b className={o.side === "buy" ? "upc" : "downc"}>{o.side === "buy" ? "Buy" : "Sell"}</b>
                </td>
                <td className="num">{o.qty}</td>
                <td>
                  <span className="mono">
                    {o.type}
                    {o.limit_price ? ` @ ${fmtPrice(o.limit_price)}` : ""}
                    {o.stop_price ? (o.type === "stop" ? ` @ ${fmtPrice(o.stop_price)}` : ` stop ${fmtPrice(o.stop_price)}`) : ""}
                  </span>
                  <span className="sim-dim">
                    {" "}
                    · {o.tag.replace("_", "-")}
                    {o.status === "pending" ? " (waits for entry)" : ""}
                  </span>
                </td>
                <td className="right">
                  <button className="btn small ghost" onClick={() => void cancel(o.id)}>
                    Cancel
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {recent.length ? (
        <details className="sim-more">
          <summary>
            Recent <span className="sim-dim">{recent.length}</span>
          </summary>
          <table className="data sim-mini">
            <tbody>
              {recent.map((o) => (
                <tr key={o.id}>
                  <td>{o.side}</td>
                  <td className="num">{o.filled_qty || o.qty}</td>
                  <td>
                    {o.type} · {o.status}
                    {o.fill_price ? ` @ ${fmtPrice(o.fill_price)}` : ""}
                    {o.reason ? <span className="sim-dim"> · {o.reason}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
      {fills.length ? (
        <>
          <h3 className="section-title sim-sub">
            Fills <span className="sim-dim">newest first</span>
          </h3>
          <table className="data sim-mini">
            <tbody>
              {fills.map((f, i) => (
                <tr key={`${f.order_id}-${f.time}-${i}`}>
                  <td className="mono nowrap">{fmtDate(f.time)}</td>
                  <td>
                    <b className={f.qty > 0 ? "upc" : "downc"}>{f.qty > 0 ? "Buy" : "Sell"}</b>
                  </td>
                  <td className="num">{Math.abs(f.qty).toLocaleString()}</td>
                  <td className="num">{fmtPrice(f.price)}</td>
                  <td className="right">{f.tag ? <Tag>{FILL_TAG[f.tag] ?? f.tag.replace("_", "-")}</Tag> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </Panel>
  );
}

function AdvisorsCard({ state }: { state: SimState }) {
  const c = state.consensus;
  return (
    <Panel
      title="Strategy advice"
      actions={isNum(c) ? <DirectionBadge score={c} label={c >= 0.2 ? "Lean long" : c <= -0.2 ? "Lean short" : "Mixed"} /> : null}
      className="sim-advice"
    >
      <ul className="sim-advice-list">
        {state.advisors.map((a) => (
          <li key={a.id}>
            <div className="sim-advice-head">
              <span className="sim-advice-name">{a.name}</span>
              <VoteGlyph vote={Math.sign(a.explanation.signal)} state={a.explanation.state} />
            </div>
            <div className="sim-advice-why">{a.explanation.headline}</div>
          </li>
        ))}
      </ul>
    </Panel>
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
    <Panel
      title="Journal"
      actions={<Help text="Write down what you see and plan before the next bar. Reviewing it later is how you learn whether you were skilled or lucky." />}
      className="sim-journal"
    >
      <div className="sim-journal-add">
        <input
          className="input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void add()}
          placeholder="Observation or plan"
          aria-label="Journal entry"
        />
        <button className="btn small" onClick={() => void add()}>
          Add
        </button>
      </div>
      {journal.length ? (
        <ol className="sim-feed sim-feed-compact">
          {journal
            .slice(-5)
            .reverse()
            .map((j, i) => (
              <li key={i} className="sim-feed-row">
                <span className="sim-feed-when">
                  <b>
                    <i>Bar</i> {j.index - state.start_index}
                  </b>
                  {fmtDate(j.time)}
                </span>
                <span className="sim-feed-body">
                  <span className="sim-feed-msg">{j.text}</span>
                </span>
                <Tag>Note</Tag>
              </li>
            ))}
        </ol>
      ) : (
        <p className="sim-empty-line">Nothing written yet. A line before each decision makes the review worth reading.</p>
      )}
    </Panel>
  );
}

function raceLines(state: SimState, colors: ReturnType<typeof useChartColors>): LineSpec[] {
  const t = state.equity_curve.t;
  const lines: LineSpec[] = [
    { id: "you", label: "You", data: state.equity_curve, color: colors.series[0] },
    { id: "bh", label: "Buy & hold", data: state.benchmark_curve, color: colors.deemph, style: "dashed" },
  ];
  state.advisors.slice(0, 5).forEach((a, i) => {
    lines.push({ id: a.id, label: a.name, data: { t, v: a.equity }, color: colors.series[i + 1] });
  });
  return lines;
}

function RaceCard({ fig, state }: { fig: string; state: SimState }) {
  const colors = useChartColors();
  const lines = useMemo(() => raceLines(state, colors), [state, colors]);
  const showGhosts = state.config.reveal === "live";
  const shown = showGhosts ? lines : lines.slice(0, 2);
  return (
    <Figure fig={fig} title="Equity race" aside={showGhosts ? "you vs buy-and-hold vs the strategies" : "you vs buy-and-hold"}>
      <LineChart lines={shown} format={(v) => fmtMoney(v)} height={200} toggleable />
    </Figure>
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
  const marks = useMemo(() => fillMarks(state, bars), [state, bars]);
  useStatusLine(
    sc
      ? `Session complete: you made **${fmtPct(sc.you.total_return, 1)}**, buy-and-hold ${fmtPct(sc.benchmark.total_return, 1)}; you finished **#${sc.rank} of ${sc.of}**. Process score ${sc.process_score ?? "–"}/100. Read the process review, then play again.`
      : "Scoring your session...",
  );
  if (!sc) return <Spinner />;
  const rv = state.reveal;
  const statusLabel = { good: "Good", warn: "Needs work", bad: "Problem", info: "Note" } as const;
  const statusTag: Record<string, TagKind> = { good: "ok", warn: "warn", bad: "crit", info: "info" };
  const segments: TimelineSegment[] = (rv?.regimes ?? []).map((r) => {
    const up = REGIME_UP.includes(r.regime);
    const down = REGIME_DOWN.includes(r.regime);
    return { label: r.regime, n: Math.max(1, r.end - r.start + 1), tone: up ? "up" : down ? "down" : "flat", title: `${r.regime}: bars ${r.start}-${r.end}` };
  });
  const bandTotal = segments.reduce((n, s) => n + s.n, 0);
  const figReveal = rv ? 1 : 0;
  const score = sc.process_score ?? 0;
  return (
    <div className="stack sim-debrief">
      <section className="card marks sim-debrief-head">
        <div className="sim-debrief-top">
          <div>
            <div className="sim-kicker">Session complete · {state.title}</div>
            <div className={`hero ${pnlClass(sc.you.total_return)}`}>{fmtPct(sc.you.total_return, 1)}</div>
            <p className="sim-debrief-sub">
              Your return. Buy &amp; hold: {fmtPct(sc.benchmark.total_return, 1)} · you finished <b>#{sc.rank}</b> of {sc.of}
            </p>
          </div>
          <div className="sim-score">
            <div className="sim-score-head">
              <span className="label">Process score</span>
              <span className="sim-score-val">
                {sc.process_score ?? "–"}
                <small>/100</small>
              </span>
            </div>
            <div className="meter" role="meter" aria-label="Process score" aria-valuenow={score} aria-valuemin={0} aria-valuemax={100}>
              <div style={{ width: `${score}%` }} />
            </div>
            <p className="sim-score-verdict">{sc.verdict}</p>
          </div>
        </div>
        <ReadoutGrid>
          <Readout size="sm" label="Buy & hold" {...pctParts(sc.benchmark.total_return)} tone={toneOf(sc.benchmark.total_return)} />
          <Readout size="sm" label="Vs buy & hold" {...pctParts(sc.excess_return)} tone={toneOf(sc.excess_return)} note="your return minus the benchmark's" />
          <Readout size="sm" label="Rank" value={sc.rank} unit={`of ${sc.of}`} note="you, the strategies and buy-and-hold" />
          <Readout size="sm" label="Max drawdown" {...pctParts(sc.you.max_drawdown, 1, false)} note="your deepest fall from a peak" />
          <Readout size="sm" label="Trades" value={fmtNum(sc.you.n_trades, 0)} note={`over ${sc.bars} bars`} />
        </ReadoutGrid>
        <div className="row sim-debrief-actions">
          {onReplay ? (
            <button className="btn primary" onClick={() => void onReplay()}>
              <TransportIcon kind="play" />
              Play again (new market, same settings)
            </button>
          ) : null}
          <button className="btn" onClick={onNew}>
            Set up a new session
          </button>
        </div>
      </section>

      {rv ? (
        <Figure fig="FIG. 1" title="The reveal" aside={rv.kind === "history" ? "what you were trading" : "the regimes you could not see"}>
          {rv.kind === "history" ? (
            <p className="sim-reveal">
              It was <b>{rv.symbol}</b> from <b>{rv.first_date}</b> to <b>{rv.last_date}</b>
              {rv.preset ? ` (${rv.preset})` : ""}. Data: {rv.provider}.{rv.blind ? " Prices were rescaled so the session started at 100." : ""}
            </p>
          ) : (
            <>
              <p className="sim-reveal">
                The hidden market regimes during your session (synthetic scenario "{rv.label}", seed {rv.seed}). Your fills are marked above
                the band.
              </p>
              {bandTotal ? (
                <Timeline
                  band
                  total={bandTotal}
                  done={bandTotal}
                  segments={segments}
                  marks={marks}
                  label="Regime timeline"
                  valueText={segments.map((s) => `${s.label} for ${s.n} bars`).join(", ")}
                />
              ) : null}
            </>
          )}
        </Figure>
      ) : null}

      <div className="grid-2">
        <Figure fig={`FIG. ${figReveal + 1}`} title="Equity race" aside="you, buy-and-hold and the strategies">
          <LineChart lines={lines} format={(v) => fmtMoney(v)} height={240} toggleable />
        </Figure>
        <Figure fig={`FIG. ${figReveal + 2}`} title="Leaderboard" aside="by total return">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">#</th>
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
                    <td className="num">{String(i + 1).padStart(2, "0")}</td>
                    <td>
                      <span className="sim-trader">
                        {r.kind === "you" ? <b>You</b> : r.name}
                        {r.kind === "benchmark" ? <Tag>Bench</Tag> : null}
                      </span>
                    </td>
                    <td className={`num ${pnlClass(r.total_return)}`}>{fmtPct(r.total_return)}</td>
                    <td className="num">{fmtPct(r.max_drawdown)}</td>
                    <td className="num">{fmtNum(r.sharpe)}</td>
                    <td className="num">{fmtNum(r.trades, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Figure>
      </div>

      <Figure
        fig={`FIG. ${figReveal + 3}`}
        title="Process review"
        aside="how you traded, independent of how the market happened to move"
        className="sim-review"
      >
        <div className="sim-diags">
          {sc.diagnostics.map((d, i) => (
            <article key={d.id} className={`sim-diag sim-diag-${d.status}`}>
              <header className="sim-diag-head">
                <span className="sim-diag-n">
                  {figReveal + 3}.{i + 1}
                </span>
                <span className="sim-diag-title">{d.title}</span>
                <Tag kind={statusTag[d.status]}>{statusLabel[d.status]}</Tag>
              </header>
              <div className="sim-diag-value">{d.value}</div>
              <p className="sim-diag-detail">{d.detail}</p>
              {d.lesson ? (
                <p className="sim-diag-lesson">
                  <span aria-hidden="true">◆</span> {d.lesson}
                </p>
              ) : null}
              {d.reference ? (
                <div className="sim-diag-ref">
                  <ReferenceList refs={[d.reference]} />
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </Figure>

      <Figure fig={`FIG. ${figReveal + 4}`} title="Your fills on the full chart" aside="buys below the bar, sells above">
        <PriceChart bars={bars} markers={markers} height={320} label="Session chart with your fills" />
      </Figure>
      <Figure fig={`FIG. ${figReveal + 5}`} title="Trades and notes" aside={`${state.trades.length} round trips`}>
        <TradesTable trades={state.trades} showSymbol={false} />
      </Figure>
      {state.journal.length ? (
        <Panel title="Your journal" aside="what you wrote while trading">
          <ol className="sim-feed sim-feed-compact">
            {[...state.journal].reverse().map((j, i) => (
              <li key={i} className="sim-feed-row">
                <span className="sim-feed-when">
                  <b>
                    <i>Bar</i> {j.index - state.start_index}
                  </b>
                  {fmtDate(j.time)}
                </span>
                <span className="sim-feed-body">
                  <span className="sim-feed-msg">{j.text}</span>
                </span>
                <Tag>Note</Tag>
              </li>
            ))}
          </ol>
        </Panel>
      ) : null}
    </div>
  );
}
