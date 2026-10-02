import { useEffect, useState, type ReactNode } from "react";
import type { EvidenceLevel, MetricInfo, Metrics, ParamSpec, Reference, Sizing, Trade } from "../types";
import { evidenceLabel, fmtDate, fmtMetric, fmtMoney, fmtPct, fmtPrice, isNum, pnlClass } from "../format";

/** Hover/focus tooltip. Deliberately not a <button>: inside a <label> a button would become
 *  the label's control and steal the input's accessible name. */
export function Help({ text }: { text: string }) {
  return (
    <span className="help" tabIndex={0} role="note" aria-label={text}>
      ?
      <span className="tip" role="tooltip" aria-hidden="true">
        {text}
      </span>
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row tight muted small" role="status">
      <span className="spinner" aria-hidden="true" />
      {label ?? "Loading..."}
    </span>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="error-box" role="alert">
      {error}
    </div>
  );
}

export function EvidenceBadge({ level }: { level: EvidenceLevel }) {
  return <span className={`badge ev-${level}`}>{evidenceLabel(level)}</span>;
}

/** Market direction badge: arrow + label + colour (never colour alone). */
export function DirectionBadge({ score, label }: { score: number; label: string }) {
  const cls = score >= 0.2 ? "up" : score <= -0.2 ? "down" : "neutral";
  const arrow = score >= 0.2 ? "▲" : score <= -0.2 ? "▼" : "–";
  return (
    <span className={`badge ${cls}`}>
      <span aria-hidden="true">{arrow}</span>
      {label}
    </span>
  );
}

export function VoteGlyph({ vote, state }: { vote: number; state?: string }) {
  if (state === "warming_up") return <span className="badge neutral">Warming up</span>;
  if (state === "error") return <span className="badge neutral">Error</span>;
  if (state === "hedge") return <span className="badge neutral">Hedge</span>;
  if (vote > 0)
    return (
      <span className="badge up">
        <span aria-hidden="true">{"▲"}</span>Bullish
      </span>
    );
  if (vote < 0)
    return (
      <span className="badge down">
        <span aria-hidden="true">{"▼"}</span>Bearish
      </span>
    );
  return <span className="badge neutral">Neutral</span>;
}

export function StatusDot({ status, children }: { status: "good" | "warn" | "bad" | "info" | "live"; children: ReactNode }) {
  const icon = { good: "✓", warn: "!", bad: "✕", info: "i", live: "" }[status];
  return (
    <span className={`status ${status}`}>
      <span className="dot" aria-hidden="true" />
      {icon ? <span className="sr-only" style={{ position: "absolute", left: -9999 }}>{icon}</span> : null}
      {children}
    </span>
  );
}

export function StatTile({ label, value, delta, help, valueClass }: {
  label: string;
  value: string;
  delta?: ReactNode;
  help?: string;
  valueClass?: string;
}) {
  return (
    <div className="tile">
      <div className="label">
        {label}
        {help ? <Help text={help} /> : null}
      </div>
      <div className={`value ${valueClass ?? ""}`}>{value}</div>
      {delta ? <div className="delta">{delta}</div> : null}
    </div>
  );
}

export function MetricTiles({ keys, metrics, benchmark, info }: {
  keys: string[];
  metrics: Metrics;
  benchmark?: Metrics;
  info: Record<string, MetricInfo>;
}) {
  return (
    <div className="tiles">
      {keys.map((k) => {
        const i = info[k];
        const v = metrics[k];
        const b = benchmark?.[k];
        const cls = ["total_return", "cagr", "alpha", "excess_cagr"].includes(k) ? pnlClass(v) : "";
        return (
          <StatTile
            key={k}
            label={i?.label ?? k}
            value={fmtMetric(v, i)}
            valueClass={cls}
            help={i?.help}
            delta={benchmark && b !== undefined ? <>Buy &amp; hold: {fmtMetric(b, i)}</> : undefined}
          />
        );
      })}
    </div>
  );
}

export function MetricsTable({ metrics, benchmark, info, keys, benchLabel = "Buy & hold" }: {
  metrics: Metrics;
  benchmark?: Metrics;
  info: Record<string, MetricInfo>;
  keys?: string[];
  benchLabel?: string;
}) {
  const rows = (keys ?? Object.keys(info)).filter((k) => metrics[k] !== undefined || benchmark?.[k] !== undefined);
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Metric</th>
            <th className="num">Strategy</th>
            {benchmark ? <th className="num">{benchLabel}</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((k) => (
            <tr key={k}>
              <td>
                <span className="row tight">
                  {info[k]?.label ?? k}
                  {info[k]?.help ? <Help text={info[k].help} /> : null}
                </span>
              </td>
              <td className="num">{fmtMetric(metrics[k], info[k])}</td>
              {benchmark ? <td className="num">{fmtMetric(benchmark[k], info[k])}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TradesTable({ trades, showSymbol = true, limit = 300 }: { trades: Trade[]; showSymbol?: boolean; limit?: number }) {
  const [all, setAll] = useState(false);
  const rows = [...trades].reverse();
  const shown = all ? rows : rows.slice(0, limit);
  if (!trades.length) return <div className="empty">No trades.</div>;
  return (
    <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
      <table className="data">
        <thead>
          <tr>
            <th>#</th>
            {showSymbol ? <th>Symbol</th> : null}
            <th>Side</th>
            <th>Entry</th>
            <th className="num">Entry price</th>
            <th>Exit</th>
            <th className="num">Exit price</th>
            <th className="num">Shares</th>
            <th className="num">P&amp;L</th>
            <th className="num">Return</th>
            <th className="num">Bars</th>
            <th className="num" title="Maximum adverse excursion">MAE</th>
            <th className="num" title="Maximum favourable excursion">MFE</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((t) => (
            <tr key={`${t.id}-${t.symbol}`}>
              <td className="num">{t.id}</td>
              {showSymbol ? <td>{t.symbol}</td> : null}
              <td>{t.direction === "long" ? "Long" : "Short"}</td>
              <td className="nowrap">{fmtDate(t.entry_time)}</td>
              <td className="num">{fmtPrice(t.entry_price)}</td>
              <td className="nowrap">{t.is_open ? <span className="badge neutral">Open</span> : fmtDate(t.exit_time)}</td>
              <td className="num">{fmtPrice(t.exit_price)}</td>
              <td className="num">{Math.round(t.qty).toLocaleString()}</td>
              <td className={`num ${pnlClass(t.pnl)}`}>{fmtMoney(t.pnl, 0, true)}</td>
              <td className={`num ${pnlClass(t.return_pct)}`}>{fmtPct(t.return_pct)}</td>
              <td className="num">{t.bars_held ?? "–"}</td>
              <td className="num">{fmtPct(t.mae)}</td>
              <td className="num">{fmtPct(t.mfe)}</td>
              <td className="small secondary">
                {[...t.tags.map((x) => x.replace("_", "-")), ...t.notes].join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit && !all ? (
        <button className="btn small ghost" onClick={() => setAll(true)}>
          Show all {rows.length} trades
        </button>
      ) : null}
    </div>
  );
}

export function ReferenceList({ refs }: { refs: Reference[] }) {
  return (
    <ol className="refs">
      {refs.map((r) => (
        <li key={`${r.authors}${r.year}${r.title}`}>
          {r.authors} ({r.year}). {r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a> : <i>{r.title}</i>}. {r.venue}.
        </li>
      ))}
    </ol>
  );
}

export function Warnings({ items, title = "Read before trusting these results" }: { items: string[]; title?: string }) {
  if (!items.length) return null;
  return (
    <div className="callout warn">
      <b>{title}</b>
      <ul>
        {items.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: [T, string][]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([id, label]) => (
        <button key={id} role="tab" aria-selected={value === id} onClick={() => onChange(id)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** A list parameter (e.g. member strategies) edited as JSON; only valid JSON lists are applied. */
function ListParam({ spec, value, disabled, onChange }: {
  spec: ParamSpec;
  value: unknown;
  disabled?: boolean;
  onChange: (v: unknown) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value));
  const [bad, setBad] = useState(false);
  const external = JSON.stringify(value);
  useEffect(() => {
    // Follow outside changes (e.g. "reset to defaults") without reformatting what is being typed.
    let typed: string | null = null;
    try {
      typed = JSON.stringify(JSON.parse(text));
    } catch {
      typed = null;
    }
    if (typed !== external) {
      setText(external);
      setBad(false);
    }
  }, [external]);
  return (
    <label className="field" title={spec.help} style={{ gridColumn: "1 / -1" }}>
      <span>{spec.label}</span>
      <textarea
        className="input"
        rows={3}
        spellCheck={false}
        value={text}
        disabled={disabled}
        aria-invalid={bad}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed: unknown = JSON.parse(e.target.value);
            setBad(!Array.isArray(parsed));
            if (Array.isArray(parsed)) onChange(parsed);
          } catch {
            setBad(true);
          }
        }}
      />
      <span className="hint">{bad ? "Not a valid JSON list yet; the last valid value is used." : spec.help}</span>
    </label>
  );
}

export function ParamForm({ spec, values, onChange, disabled }: {
  spec: ParamSpec[];
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
}) {
  if (!spec.length) return <div className="muted small">This strategy has no parameters.</div>;
  return (
    <div className="form-grid">
      {spec.map((p) => {
        const v = values[p.name] ?? p.default;
        const set = (val: unknown) => onChange({ ...values, [p.name]: val });
        if (p.kind === "bool") {
          return (
            <label key={p.name} className="check" title={p.help}>
              <input type="checkbox" checked={Boolean(v)} disabled={disabled} onChange={(e) => set(e.target.checked)} />
              {p.label}
            </label>
          );
        }
        if (p.kind === "choice") {
          return (
            <label key={p.name} className="field" title={p.help}>
              <span>{p.label}</span>
              <select className="input" value={String(v)} disabled={disabled} onChange={(e) => set(e.target.value)}>
                {p.choices.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          );
        }
        if (p.kind === "symbol") {
          return (
            <label key={p.name} className="field" title={p.help}>
              <span>{p.label}</span>
              <input className="input" value={String(v ?? "")} disabled={disabled} onChange={(e) => set(e.target.value.toUpperCase())} />
            </label>
          );
        }
        if (p.kind === "list") {
          return <ListParam key={p.name} spec={p} value={v} disabled={disabled} onChange={set} />;
        }
        return (
          <label key={p.name} className="field" title={p.help}>
            <span>{p.label}</span>
            <input
              className="input num"
              type="number"
              value={String(v)}
              min={p.min ?? undefined}
              max={p.max ?? undefined}
              step={p.step ?? (p.kind === "int" ? 1 : "any")}
              disabled={disabled}
              onChange={(e) => set(e.target.value === "" ? "" : Number(e.target.value))}
            />
            {p.help ? <span className="hint">{p.help}</span> : null}
          </label>
        );
      })}
    </div>
  );
}

const SIZING_HELP: Record<string, string> = {
  fixed: "Position = signal x allocation. Simple, but risk swings with the market's volatility.",
  vol_target:
    "Scale exposure so expected volatility matches a target (Moskowitz, Ooi & Pedersen 2012; Moreira & Muir 2017). Bigger positions in calm markets, smaller in turbulent ones.",
  atr_risk:
    "Size so a stop k x ATR away loses a fixed fraction of equity (the Turtles' 'unit'). Single-symbol strategies only.",
};

export function SizingForm({ value, onChange }: { value: Partial<Sizing>; onChange: (v: Partial<Sizing>) => void }) {
  const method = (value.method ?? "fixed") as Sizing["method"];
  const num = (k: keyof Sizing, label: string, step: number, help?: string, scale = 1) => (
    <label className="field" title={help}>
      <span>{label}</span>
      <input
        className="input num"
        type="number"
        step={step}
        value={value[k] === undefined ? "" : String(Number(value[k]) * scale)}
        onChange={(e) => onChange({ ...value, [k]: e.target.value === "" ? undefined : Number(e.target.value) / scale })}
      />
    </label>
  );
  return (
    <div className="col">
      <label className="field">
        <span>Sizing method</span>
        <select className="input" value={method} onChange={(e) => onChange({ ...value, method: e.target.value as Sizing["method"] })}>
          <option value="fixed">Fixed allocation</option>
          <option value="vol_target">Volatility target</option>
          <option value="atr_risk">ATR risk (Turtle unit)</option>
        </select>
        <span className="hint">{SIZING_HELP[method]}</span>
      </label>
      <div className="form-grid">
        {num("allocation", "Allocation (x equity)", 0.1)}
        {num("max_leverage", "Max gross leverage", 0.1, "Cap on total exposure; 1 = no borrowing.")}
        {method === "vol_target" ? num("target_vol", "Target volatility (%)", 1, undefined, 100) : null}
        {method === "vol_target" ? num("vol_com", "Vol. centre of mass (bars)", 1) : null}
        {method === "atr_risk" ? num("risk_per_trade", "Risk per trade (%)", 0.1, undefined, 100) : null}
        {method === "atr_risk" ? num("stop_atr", "Stop distance (ATRs)", 0.25) : null}
        {num("rebalance_every", "Re-size every (bars, 0 = on signal)", 1)}
      </div>
    </div>
  );
}

export function SymbolInput({ symbols, onChange, max = 30, placeholder = "Add symbol" }: {
  symbols: string[];
  onChange: (s: string[]) => void;
  max?: number;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const add = () => {
    const parts = text
      .split(/[\s,]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    const next = [...symbols];
    for (const p of parts) if (!next.includes(p) && next.length < max) next.push(p);
    onChange(next);
    setText("");
  };
  return (
    <div className="chips">
      {symbols.map((s) => (
        <span className="chip" key={s}>
          {s}
          <button type="button" aria-label={`Remove ${s}`} onClick={() => onChange(symbols.filter((x) => x !== s))}>
            {"×"}
          </button>
        </span>
      ))}
      <input
        className="input"
        style={{ width: 130, height: 28 }}
        value={text}
        placeholder={placeholder}
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
    </div>
  );
}

export function Disclaimer({ text }: { text: string }) {
  return <footer className="footer">{text}</footer>;
}

export function num(x: unknown): number | null {
  return isNum(x) ? x : null;
}
