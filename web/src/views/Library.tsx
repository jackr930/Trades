import { useMemo, useState } from "react";
import { useApp, type Route } from "../state";
import type { Meta, StrategyMeta } from "../types";
import { EvidenceBadge, ReferenceList, Tabs } from "../components/ui";
import { CONCEPTS } from "../concepts";

function shortCite(authors: string): string {
  const first = authors.split(",")[0].trim();
  const many = authors.includes("&") || (authors.match(/,/g) ?? []).length > 1;
  return many ? `${first} et al.` : first;
}

const EVIDENCE_ORDER = { strong: 0, moderate: 1, practitioner: 2, experimental: 3, benchmark: 4 } as const;

type Family = "all" | "modern" | "classic";
const FAMILIES: [Family, string][] = [
  ["all", "All"],
  ["modern", "Modern quant methods"],
  ["classic", "Classic published rules"],
];

export default function Library({ route }: { route: Route }) {
  const { meta, navigate } = useApp() as { meta: Meta; navigate: (p: string, q?: Record<string, string>) => void };
  const selectedId = route.params.get("strategy");
  const [tab, setTab] = useState<"strategies" | "concepts">(route.params.get("tab") === "concepts" ? "concepts" : "strategies");
  const [family, setFamily] = useState<Family>(() => FAMILIES.find(([f]) => f === route.params.get("family"))?.[0] ?? "all");
  const selected = meta.strategies.find((s) => s.id === selectedId) ?? null;
  const sorted = useMemo(
    () =>
      [...meta.strategies].sort(
        (a, b) => EVIDENCE_ORDER[a.evidence] - EVIDENCE_ORDER[b.evidence] || a.category.localeCompare(b.category),
      ),
    [meta],
  );
  const shown = sorted.filter((s) => family === "all" || s.family === family);

  if (selected) {
    const counterpart = meta.strategies.find((s) => s.id === selected.counterpart);
    return (
      <StrategyDetail
        s={selected}
        counterpart={counterpart}
        onBack={() => navigate("/library", family === "all" ? {} : { family })}
        onTry={() => navigate("/lab", { strategy: selected.id })}
        onRace={() => navigate("/sim", { strategies: [selected.id, ...(counterpart ? [counterpart.id] : [])].join(",") })}
        onOpen={(id) => navigate("/library", { strategy: id })}
      />
    );
  }
  return (
    <div className="stack">
      <div>
        <h1>Library</h1>
        <p className="secondary" style={{ marginTop: 4, maxWidth: "80ch" }}>
          Every strategy here comes from published research or a well-documented practitioner source. The evidence badge rates how
          strong the support is. Read the failure modes before trusting any of them.
        </p>
      </div>
      <Tabs
        tabs={[
          ["strategies", "Strategies"],
          ["concepts", "Concepts every quant should know"],
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "strategies" ? (
        <>
          <div className="chips" role="group" aria-label="Show strategies">
            {FAMILIES.map(([f, label]) => (
              <button key={f} type="button" className="chip toggle" aria-pressed={family === f} onClick={() => setFamily(f)}>
                {label}
                <span className="muted">{f === "all" ? meta.strategies.length : meta.strategies.filter((s) => s.family === f).length}</span>
              </button>
            ))}
          </div>
          {family === "modern" ? (
            <div className="callout info" style={{ maxWidth: "90ch" }}>
              The methods of today&apos;s quant funds and prop desks: volatility-scaled trend signals, factor-neutral statistical
              arbitrage, Kalman filters, hidden Markov models and machine learning. They adapt where the classic rules are fixed, and
              that flexibility makes them easier to overfit. Each one names the classic rule it refines: race the two in the simulator and
              see whether the extra machinery pays for itself.
            </div>
          ) : null}
          <div className="row small secondary">
            <span className="legend-item">
              <span className="badge ev-strong">Strong evidence</span> replicated across markets and decades
            </span>
            <span className="legend-item">
              <span className="badge ev-moderate">Moderate evidence</span> peer-reviewed but mixed or decayed
            </span>
            <span className="legend-item">
              <span className="badge ev-practitioner">Practitioner rule</span> popular, less rigorously tested
            </span>
            <span className="legend-item">
              <span className="badge ev-experimental">Experimental</span> promising, too new or too flexible to rate
            </span>
          </div>
          {(family === "all" ? (["modern", "classic"] as const) : [family]).map((f) => (
            <section key={f} className="stack" style={{ gap: 10 }}>
              {family === "all" ? <h2>{f === "modern" ? "Modern quant methods" : "Classic published rules"}</h2> : null}
              <div className="lib-grid">
                {shown
                  .filter((s) => s.family === f)
                  .map((s) => (
                    <StrategyCard key={s.id} s={s} onOpen={() => navigate("/library", { strategy: s.id })} />
                  ))}
              </div>
            </section>
          ))}
        </>
      ) : (
        <div className="stack">
          {CONCEPTS.map((c) => (
            <div key={c.id} className="card prose" id={c.id}>
              <h2 style={{ marginBottom: 8 }}>{c.title}</h2>
              {c.body.map((p, i) => (
                <p key={i}>{p}</p>
              ))}
              {c.refs.length ? <ReferenceList refs={c.refs} /> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StrategyCard({ s, onOpen }: { s: StrategyMeta; onOpen: () => void }) {
  return (
    <button className="lib-card" onClick={onOpen}>
      <span className="row tight" style={{ justifyContent: "space-between", width: "100%" }}>
        <span className="small muted">{s.category}</span>
        <EvidenceBadge level={s.evidence} />
      </span>
      <span style={{ fontWeight: 600, fontSize: 15 }}>{s.name}</span>
      <span className="small secondary">{s.summary}</span>
      <span className="small muted">
        {s.kind === "single" ? "Single symbol" : s.kind === "pair" ? "Two symbols (pair)" : `Universe of ${s.min_symbols}+ symbols`}
        {s.references[0] ? ` · ${shortCite(s.references[0].authors)} (${s.references[0].year})` : ""}
      </span>
    </button>
  );
}

function StrategyDetail({ s, counterpart, onBack, onTry, onRace, onOpen }: {
  s: StrategyMeta;
  counterpart?: StrategyMeta;
  onBack: () => void;
  onTry: () => void;
  onRace: () => void;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="stack">
      <div className="row">
        <button className="btn small ghost" onClick={onBack}>
          ← All strategies
        </button>
      </div>
      <div className="card prose">
        <div className="card-header">
          <div>
            <div className="small muted">
              {s.family === "modern" ? "Modern quant method · " : ""}
              {s.category}
            </div>
            <h1>{s.name}</h1>
          </div>
          <div className="row">
            <EvidenceBadge level={s.evidence} />
            <button className="btn primary" onClick={onTry}>
              Backtest it
            </button>
            {s.id !== "buy_hold" ? (
              <button className="btn" onClick={onRace}>
                {counterpart ? "Race it against the classic rule" : "Test it in the simulator"}
              </button>
            ) : null}
          </div>
        </div>
        <p style={{ color: "var(--text-primary)", fontSize: 15 }}>{s.summary}</p>
        {s.needs ? (
          <p className="small">
            <strong>Works best with:</strong> {s.needs}
          </p>
        ) : null}
        {counterpart ? (
          <p className="small">
            <strong>Refines a classic rule:</strong>{" "}
            <button type="button" className="linklike" onClick={() => onOpen(counterpart.id)}>
              {counterpart.name}
            </button>
            . The race button starts a simulation with both, plus buy-and-hold, on the same market.
          </p>
        ) : null}
        <h3 className="section-title">Rules</h3>
        <ol>
          {s.rules.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ol>
        <h3 className="section-title">Why it might work</h3>
        <p>{s.rationale}</p>
        <h3 className="section-title">When it fails</h3>
        <p>{s.failure_modes}</p>
        <h3 className="section-title">What the evidence says</h3>
        <p>{s.evidence_text}</p>
        <h3 className="section-title">References</h3>
        <ReferenceList refs={s.references} />
      </div>
      <div className="card">
        <h3 className="section-title">Parameters</h3>
        {s.params.length ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Parameter</th>
                  <th>Default</th>
                  <th>Range</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {s.params.map((p) => (
                  <tr key={p.name}>
                    <td>{p.label}</td>
                    <td className="num">{String(p.default) || "(blank)"}</td>
                    <td className="num">
                      {p.kind === "choice" ? p.choices.join(" / ") : p.min !== null && p.max !== null ? `${p.min} – ${p.max}` : "–"}
                    </td>
                    <td className="small secondary">{p.help}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No parameters.</p>
        )}
        <p className="small muted" style={{ marginTop: 10 }}>
          Default sizing: {String(s.default_sizing.method ?? "fixed").replace("_", " ")}
          {s.uses_short ? " · designed to use short selling" : ""}
        </p>
      </div>
    </div>
  );
}
