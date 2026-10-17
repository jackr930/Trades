import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useApp, useStatusLine, type Route } from "../state";
import type { EvidenceLevel, Meta, ParamSpec, StrategyMeta } from "../types";
import { EvidenceBadge, Help, ReferenceList, Tabs } from "../components/ui";
import { Hatch, KeyValues, Panel, Readout, Readouts, Tag } from "../components/instrument";
import { evidenceLabel } from "../format";
import { CONCEPTS, type Concept } from "../concepts";
import "../styles/library.css";

/*
 * The Library answers: what is this strategy, how good is the evidence, and how does it fail?
 *
 *   #/library                      the catalogue: a sortable data grid with family chips, a text filter
 *                                  and the evidence key (which doubles as an evidence filter)
 *   #/library?strategy=<id>        one strategy as a data sheet: identity and failure modes first,
 *                                  then the specification, rules, rationale, evidence, parameters
 *   #/library?tab=concepts         the essays, with a contents rail (&concept=<id> scrolls to one)
 */

type Family = StrategyMeta["family"];
type FamilyFilter = "all" | Family;
type LibTab = "strategies" | "concepts";
type SortKey = "name" | "evidence" | "category" | "needs" | "source";
interface SortState {
  key: SortKey;
  dir: 1 | -1;
}

const EVIDENCE_ORDER: Record<EvidenceLevel, number> = { strong: 0, moderate: 1, practitioner: 2, experimental: 3, benchmark: 4 };
const EVIDENCE_LEVELS: EvidenceLevel[] = ["strong", "moderate", "practitioner", "experimental", "benchmark"];
/** The evidence key: what each level means. */
const EVIDENCE_MEANS: Record<EvidenceLevel, string> = {
  strong: "replicated across markets and decades",
  moderate: "peer-reviewed but mixed or decayed",
  practitioner: "popular, less rigorously tested",
  experimental: "promising, too new or too flexible to rate",
  benchmark: "the yardstick every strategy must beat",
};

const FAMILIES: Family[] = ["classic", "modern", "intraday"];
const FAMILY_TITLE: Record<Family, string> = {
  classic: "Classic published rules",
  modern: "Modern quant methods",
  intraday: "Intraday rules",
};
const FAMILY_ONE: Record<Family, string> = {
  classic: "Classic published rule",
  modern: "Modern quant method",
  intraday: "Intraday rule",
};
const FAMILY_NOTE: Record<Family, string> = {
  classic:
    "Rules from published research and well-documented practitioner sources, most with only a few parameters. They are the baselines: each modern method names the classic rule it refines, and buy-and-hold is the yardstick for all of them.",
  modern:
    "The methods of today's quant funds and prop desks: volatility-scaled trend signals, factor-neutral statistical arbitrage, Kalman filters, hidden Markov models and machine learning. They adapt where the classic rules are fixed, and that flexibility makes them easier to overfit. Each one names the classic rule it refines: race the two in the simulator and see whether the extra machinery pays for itself.",
  intraday:
    "Rules that trade within the session on 1- to 30-minute bars and are flat by the close: the opening range breakout and intraday momentum. Costs decide them: a spread of a few basis points, paid twice a day, can turn a gross edge into a net loss. Test them in the Lab on intraday bars, and read their evidence first: much of it comes from working papers.",
};

const SORTS: { key: SortKey; label: string; first: 1 | -1 }[] = [
  { key: "evidence", label: "Evidence, strongest first", first: 1 },
  { key: "name", label: "Name, A to Z", first: 1 },
  { key: "category", label: "Category", first: 1 },
  { key: "needs", label: "Symbols needed", first: 1 },
  { key: "source", label: "Source, newest first", first: -1 },
];

/** Essays worth reading next, by strategy category (ids in concepts.ts). */
const READ_NEXT: Record<string, string[]> = {
  "Trend following": ["regimes", "drawdowns"],
  "Mean reversion": ["costs", "regimes"],
  Momentum: ["regimes", "decay"],
  "Statistical arbitrage": ["neutral", "costs"],
  "Regime detection": ["filters", "regimes"],
  "Machine learning": ["ml", "overfitting"],
  "Defensive factor": ["decay", "drawdowns"],
  "Intraday breakout": ["costs", "overfitting"],
  "Intraday momentum": ["costs", "decay"],
  Ensemble: ["overfitting", "sizing"],
  Benchmark: ["drawdowns", "behaviour"],
};

const pad2 = (n: number) => String(n).padStart(2, "0");

function shortCite(authors: string): string {
  const first = authors.split(",")[0].trim();
  const many = authors.includes("&") || (authors.match(/,/g) ?? []).length > 1;
  return many ? `${first} et al.` : first;
}

/** "1 symbol", "Pair", "4+ symbols": what a strategy trades, in a few characters. */
function kindShort(s: StrategyMeta): string {
  return s.kind === "single" ? "1 symbol" : s.kind === "pair" ? "Pair" : `${s.min_symbols}+ symbols`;
}

function kindLong(s: StrategyMeta): string {
  return s.kind === "single" ? "Single symbol" : s.kind === "pair" ? "Two symbols (pair)" : `Universe of ${s.min_symbols}+ symbols`;
}

function refYear(s: StrategyMeta): number {
  return s.references[0]?.year ?? 0;
}

const SIZING_NAME: Record<string, string> = {
  fixed: "Fixed allocation",
  vol_target: "Volatility target",
  atr_risk: "ATR risk (Turtle unit)",
  stop_risk: "Stop risk",
};

function pctText(x: number): string {
  return `${+(x * 100).toFixed(2)}%`;
}

/** A strategy's default sizing in words: "Volatility target · 15% a year · re-size every 21 bars". */
function sizingText(sizing: Record<string, number | string>): string {
  const method = String(sizing.method ?? "fixed");
  const parts: string[] = [SIZING_NAME[method] ?? method.replace(/_/g, " ")];
  for (const [k, v] of Object.entries(sizing)) {
    const n = Number(v);
    if (k === "method") continue;
    if (k === "target_vol") parts.push(`${pctText(n)} a year`);
    else if (k === "risk_per_trade") parts.push(`${pctText(n)} risk per trade`);
    else if (k === "stop_atr") parts.push(`stop ${n} ATR`);
    else if (k === "atr_length") parts.push(`ATR(${n})`);
    else if (k === "allocation") parts.push(`${n}x allocation`);
    else if (k === "max_leverage") parts.push(`max ${n}x leverage`);
    else if (k === "rebalance_every") parts.push(n ? `re-size every ${n} bars` : "re-size on signal");
    else parts.push(`${k.replace(/_/g, " ")} ${String(v)}`);
  }
  return parts.join(" · ");
}

/** Split a paragraph into its sentences, without breaking "e.g." and friends. */
function sentences(text: string): string[] {
  const parts = text.trim().split(/(?<!\b(?:e\.g|i\.e|vs|etc|cf|al))\.\s+(?=[A-Z'"(])/);
  return parts.map((p, i) => (i < parts.length - 1 ? `${p}.` : p).trim()).filter(Boolean);
}

/** Typography for rule text: arrows and comparison signs. */
function ruleText(r: string): string {
  return r.replace(/\s->\s/g, " → ").replace(/>=/g, "≥").replace(/<=/g, "≤");
}

function haystack(s: StrategyMeta): string {
  return [
    s.name,
    s.id,
    s.category,
    FAMILY_TITLE[s.family],
    s.summary,
    s.needs,
    kindLong(s),
    evidenceLabel(s.evidence),
    ...s.references.map((r) => `${r.authors} ${r.year} ${r.title}`),
  ]
    .join(" | ")
    .toLowerCase();
}

function compare(a: StrategyMeta, b: StrategyMeta, key: SortKey): number {
  const byName = a.name.localeCompare(b.name);
  const byEvidence = EVIDENCE_ORDER[a.evidence] - EVIDENCE_ORDER[b.evidence];
  switch (key) {
    case "name":
      return byName;
    case "evidence":
      return byEvidence || a.category.localeCompare(b.category) || byName;
    case "category":
      return a.category.localeCompare(b.category) || byEvidence || byName;
    case "needs": {
      const order = { single: 0, pair: 1, cross_sectional: 2 };
      return order[a.kind] - order[b.kind] || a.min_symbols - b.min_symbols || byName;
    }
    case "source":
      return refYear(a) - refYear(b) || byName;
  }
}

/** Every strategy in a fixed order: by family, then strongest evidence first (the rail and prev/next). */
function railOrder(all: StrategyMeta[]): StrategyMeta[] {
  return FAMILIES.flatMap((f) => all.filter((s) => s.family === f).sort((a, b) => compare(a, b, "evidence")));
}

function scrollToConcept(id: string) {
  const el = document.getElementById(`concept-${id}`);
  if (!el) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
}

// ---- the view ------------------------------------------------------------------------------

export default function Library({ route }: { route: Route }) {
  const { meta, navigate } = useApp() as { meta: Meta; navigate: (p: string, q?: Record<string, string>) => void };
  const all = meta.strategies;
  const selectedId = route.params.get("strategy");
  const selected = all.find((s) => s.id === selectedId) ?? null;
  const tab: LibTab = route.params.get("tab") === "concepts" ? "concepts" : "strategies";
  const focusConcept = route.params.get("concept");

  // The family filter starts from the address (the data sheet's back button keeps it there) and
  // follows it when it changes; the other filters live here, so they survive a visit to a sheet.
  const routeFamily = route.params.get("family");
  const parseFamily = (f: string | null): FamilyFilter => (FAMILIES as string[]).includes(f ?? "") ? (f as Family) : "all";
  const [family, setFamily] = useState<FamilyFilter>(() => parseFamily(routeFamily));
  const [seenFamily, setSeenFamily] = useState(routeFamily);
  if (routeFamily !== seenFamily) {
    setSeenFamily(routeFamily);
    if (routeFamily) setFamily(parseFamily(routeFamily));
  }
  const [query, setQuery] = useState("");
  const [evidence, setEvidence] = useState<EvidenceLevel | null>(null);
  const [sort, setSort] = useState<SortState>({ key: "evidence", dir: 1 });

  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hay = useMemo(() => new Map(all.map((s) => [s.id, haystack(s)])), [all]);
  const textOk = (s: StrategyMeta) => terms.every((t) => hay.get(s.id)?.includes(t));
  const familyOk = (s: StrategyMeta) => family === "all" || s.family === family;
  const evidenceOk = (s: StrategyMeta) => evidence === null || s.evidence === evidence;
  const shown = all
    .filter((s) => textOk(s) && familyOk(s) && evidenceOk(s))
    .sort((a, b) => sort.dir * compare(a, b, sort.key));
  // Facet counts: each filter counts what the *other* filters leave.
  const familyCount = (f: FamilyFilter) => all.filter((s) => textOk(s) && evidenceOk(s) && (f === "all" || s.family === f)).length;
  const evidenceCount = (e: EvidenceLevel) => all.filter((s) => textOk(s) && familyOk(s) && s.evidence === e).length;
  const strong = all.filter((s) => s.evidence === "strong").length;
  const filtered = family !== "all" || evidence !== null || terms.length > 0;

  // Opening a sheet scrolls to the top; coming back restores the catalogue where it was.
  const catalogueScroll = useRef(0);
  const prevSelected = useRef(selectedId);
  useLayoutEffect(() => {
    if (prevSelected.current === selectedId) return;
    window.scrollTo(0, selectedId ? 0 : catalogueScroll.current);
    prevSelected.current = selectedId;
  }, [selectedId]);

  let status: string;
  if (selected) {
    status =
      selected.evidence === "benchmark"
        ? `**${selected.name}** is the benchmark: the yardstick every strategy must beat. Read how it fails, too.`
        : `**${selected.name}**: ${evidenceLabel(selected.evidence).toLowerCase()}, ${selected.category.toLowerCase()}. Read how it fails before you try it in the Lab.`;
  } else if (tab === "concepts") {
    status = `${CONCEPTS.length} short essays on testing a strategy honestly. Start with look-ahead bias and overfitting.`;
  } else if (filtered) {
    const what = [
      family !== "all" ? FAMILY_TITLE[family].toLowerCase() : "",
      evidence ? evidenceLabel(evidence).toLowerCase() : "",
      terms.length ? `matching "${query.trim()}"` : "",
    ].filter(Boolean);
    status = `Showing **${shown.length}** of ${all.length} strategies (${what.join(", ")}). Open one for its rules, evidence and failure modes.`;
  } else {
    status = `${all.length} strategies; **${strong}** with strong evidence. Read the failure modes before trusting any of them.`;
  }
  useStatusLine(status);

  if (selected) {
    const counterpart = all.find((s) => s.id === selected.counterpart);
    return (
      <StrategySheet
        s={selected}
        all={all}
        counterpart={counterpart}
        onBack={() => navigate("/library", family === "all" ? {} : { family })}
        onTry={() => navigate("/lab", { strategy: selected.id })}
        onRace={() => navigate("/sim", { strategies: [selected.id, ...(counterpart ? [counterpart.id] : [])].join(",") })}
        onOpen={(id) => navigate("/library", { strategy: id })}
      />
    );
  }

  const refs = new Map<string, number>();
  for (const s of all) for (const r of s.references) refs.set(`${r.authors}|${r.year}|${r.title}`, r.year);
  const years = [...refs.values()];

  return (
    <div className="stack lib">
      <header className="lib-head">
        <div className="lib-head-text">
          <h1>Library</h1>
          <p>
            Every strategy here comes from published research or a well-documented practitioner source. The evidence badge rates
            how strong the support is. Read the failure modes before trusting any of them.
          </p>
        </div>
        <Readouts>
          <Readout label="Strategies" value={all.length} note={`in ${FAMILIES.length} families`} />
          <Readout label="Strong evidence" value={strong} note="replicated widely" />
          <Readout
            label="Sources cited"
            value={refs.size}
            note={years.length ? `${Math.min(...years)}–${Math.max(...years)}` : "none"}
          />
          <Readout label="Concepts" value={CONCEPTS.length} note="essays on testing" />
        </Readouts>
      </header>

      <Tabs
        tabs={[
          ["strategies", "Strategies"],
          ["concepts", "Concepts every quant should know"],
        ]}
        value={tab}
        onChange={(t) => navigate("/library", t === "concepts" ? { tab: "concepts" } : family === "all" ? {} : { family })}
      />

      {tab === "strategies" ? (
        <Catalogue
          all={all}
          shown={shown}
          family={family}
          setFamily={setFamily}
          familyCount={familyCount}
          evidence={evidence}
          setEvidence={setEvidence}
          evidenceCount={evidenceCount}
          query={query}
          setQuery={setQuery}
          sort={sort}
          setSort={setSort}
          onOpen={(id) => {
            catalogueScroll.current = window.scrollY;
            navigate("/library", { strategy: id });
          }}
        />
      ) : (
        <Concepts focus={focusConcept} onPick={(id) => navigate("/library", { tab: "concepts", concept: id })} />
      )}
    </div>
  );
}

// ---- the catalogue -------------------------------------------------------------------------

function Catalogue({
  all,
  shown,
  family,
  setFamily,
  familyCount,
  evidence,
  setEvidence,
  evidenceCount,
  query,
  setQuery,
  sort,
  setSort,
  onOpen,
}: {
  all: StrategyMeta[];
  shown: StrategyMeta[];
  family: FamilyFilter;
  setFamily: (f: FamilyFilter) => void;
  familyCount: (f: FamilyFilter) => number;
  evidence: EvidenceLevel | null;
  setEvidence: (e: EvidenceLevel | null) => void;
  evidenceCount: (e: EvidenceLevel) => number;
  query: string;
  setQuery: (q: string) => void;
  sort: SortState;
  setSort: (s: SortState) => void;
  onOpen: (id: string) => void;
}) {
  const search = useRef<HTMLInputElement>(null);

  // "/" jumps to the filter, as in most terminals and code hosts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable='true']")) return;
      e.preventDefault();
      search.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const clearAll = () => {
    setFamily("all");
    setEvidence(null);
    setQuery("");
  };
  const anyFilter = family !== "all" || evidence !== null || query.trim() !== "";

  const head = (key: SortKey, label: string, help?: string) => {
    const active = sort.key === key;
    const first = SORTS.find((x) => x.key === key)?.first ?? 1;
    return (
      <th aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : undefined} className={`lib-th-${key}`}>
        <span className="lib-th">
          <button
            type="button"
            className="lib-sort"
            onClick={() => setSort(active ? { key, dir: sort.dir === 1 ? -1 : 1 } : { key, dir: first })}
          >
            {label}
            <span className="lib-sort-arrow" aria-hidden="true">
              {active ? (sort.dir === 1 ? "▲" : "▼") : ""}
            </span>
          </button>
          {help ? <Help text={help} /> : null}
        </span>
      </th>
    );
  };

  return (
    <Panel
      title="Catalogue"
      marks
      className="lib-cat"
      aside={
        <span className="lib-count">
          <b>{shown.length}</b> of {all.length} shown
        </span>
      }
    >
      <div className="lib-toolbar">
        <div className="chips" role="group" aria-label="Show strategies by family">
          {(["all", ...FAMILIES] as FamilyFilter[]).map((f) => (
            <button key={f} type="button" className="chip toggle" aria-pressed={family === f} onClick={() => setFamily(f)}>
              {f === "all" ? "All" : f}
              <span className="muted">{familyCount(f)}</span>
            </button>
          ))}
        </div>
        <div className="lib-tools">
          <label className="lib-sortpick">
            <span className="label">Sort</span>
            <select
              className="input"
              value={`${sort.key}:${sort.dir}`}
              onChange={(e) => {
                const [key, dir] = e.target.value.split(":");
                setSort({ key: key as SortKey, dir: Number(dir) === -1 ? -1 : 1 });
              }}
            >
              {SORTS.map((o) => (
                <option key={o.key} value={`${o.key}:${o.first}`}>
                  {o.label}
                </option>
              ))}
              {SORTS.some((o) => o.key === sort.key && o.first === sort.dir) ? null : (
                <option value={`${sort.key}:${sort.dir}`}>{SORTS.find((o) => o.key === sort.key)?.label}, reversed</option>
              )}
            </select>
          </label>
          <label className="lib-search">
            <span className="sr-only">Filter strategies</span>
            <input
              ref={search}
              className="input"
              type="search"
              placeholder="Filter by name, category or author"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setQuery("");
              }}
            />
            {query ? null : (
              <kbd className="lib-kbd" aria-hidden="true">
                /
              </kbd>
            )}
          </label>
          {anyFilter ? (
            <button type="button" className="btn small ghost" onClick={clearAll}>
              Clear
            </button>
          ) : null}
        </div>
      </div>

      {family !== "all" ? (
        <div className="callout info lib-family-note">
          <span className="label">{FAMILY_TITLE[family]}</span>
          <p>{FAMILY_NOTE[family]}</p>
        </div>
      ) : null}

      <div className="lib-key" role="group" aria-label="Evidence key. Pick a level to show only those strategies.">
        <div className="lib-key-title">
          <span className="label">Evidence key</span>
          <span className="lib-key-hint">Pick a level to show only those.</span>
        </div>
        {EVIDENCE_LEVELS.map((level) => (
          <button
            key={level}
            type="button"
            className={`lib-key-item${evidenceCount(level) ? "" : " zero"}`}
            aria-pressed={evidence === level}
            onClick={() => setEvidence(evidence === level ? null : level)}
          >
            <span className="lib-key-top">
              <EvidenceBadge level={level} />
              <span className="lib-key-n">{evidenceCount(level)}</span>
            </span>
            <span className="lib-key-means">{EVIDENCE_MEANS[level]}</span>
          </button>
        ))}
      </div>

      {shown.length ? (
        <div className="lib-grid">
          <table className="data lib-table">
            <colgroup>
              <col className="lib-col-name" />
              <col className="lib-col-ev" />
              <col className="lib-col-cat" />
              <col />
              <col className="lib-col-needs" />
              <col className="lib-col-src" />
            </colgroup>
            <thead>
              <tr>
                {head("name", "Strategy")}
                {head("evidence", "Evidence", "How strong the published support is. The key above explains each level.")}
                {head("category", "Category")}
                <th>Summary</th>
                {head("needs", "Needs", "What the strategy trades (one symbol, a pair or a group of symbols), whether it sells short, and any data it depends on.")}
                {head("source", "Source")}
              </tr>
            </thead>
            <tbody>
              {shown.map((s, i) => {
                const ref = s.references[0];
                const before = shown[i - 1];
                const breaks =
                  before !== undefined &&
                  ((sort.key === "evidence" && before.evidence !== s.evidence) ||
                    (sort.key === "category" && before.category !== s.category));
                return (
                  <tr
                    key={s.id}
                    className={`clickable${breaks ? " lib-break" : ""}`}
                    onClick={() => {
                      if (window.getSelection()?.toString()) return; // selecting text is not a click
                      onOpen(s.id);
                    }}
                  >
                    <td className="lib-c-name">
                      <a
                        href={`#/library?strategy=${encodeURIComponent(s.id)}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                          e.preventDefault();
                          onOpen(s.id);
                        }}
                      >
                        {s.name}
                      </a>
                      <span className="lib-id">{s.id}</span>
                    </td>
                    <td className="lib-c-ev">
                      <EvidenceBadge level={s.evidence} />
                    </td>
                    <td className="lib-c-cat">
                      <span className="lib-cat-name">{s.category}</span>
                      <span className="lib-fam">{s.family}</span>
                    </td>
                    <td className="lib-c-sum">
                      <span className="lib-clamp" title={s.summary}>
                        {s.summary}
                      </span>
                    </td>
                    <td className="lib-c-needs">
                      <span className="lib-kind">
                        {kindShort(s)}
                        {s.uses_short ? " · shorts" : ""}
                      </span>
                      {s.needs ? (
                        <span className="lib-needs lib-clamp" title={s.needs}>
                          {s.needs}
                        </span>
                      ) : null}
                    </td>
                    <td className="lib-c-src">
                      {ref ? (
                        <>
                          <span className="lib-src-who">{shortCite(ref.authors)}</span>
                          <span className="lib-src-year">{ref.year}</span>
                        </>
                      ) : (
                        <span className="muted">–</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="lib-empty">
          <Hatch />
          <p>
            No strategy matches {query.trim() ? <b>&ldquo;{query.trim()}&rdquo;</b> : "these filters"}
            {family !== "all" ? <> in {FAMILY_TITLE[family].toLowerCase()}</> : null}
            {evidence ? <> with {evidenceLabel(evidence).toLowerCase()}</> : null}. Try fewer words, or show everything again.
          </p>
          <button type="button" className="btn small" onClick={clearAll}>
            Show all {all.length}
          </button>
        </div>
      )}
    </Panel>
  );
}

// ---- one strategy as a data sheet ------------------------------------------------------------

function ParamDefault({ p }: { p: ParamSpec }) {
  if (p.kind === "list") return <>{(p.default as unknown[]).length} items</>;
  return <>{String(p.default) || "(blank)"}</>;
}

function paramRange(p: ParamSpec): string {
  if (p.kind === "choice") return p.choices.join(" / ");
  if (p.min !== null && p.max !== null) return `${p.min} – ${p.max}`;
  return "–";
}

function StrategySheet({
  s,
  all,
  counterpart,
  onBack,
  onTry,
  onRace,
  onOpen,
}: {
  s: StrategyMeta;
  all: StrategyMeta[];
  counterpart?: StrategyMeta;
  onBack: () => void;
  onTry: () => void;
  onRace: () => void;
  onOpen: (id: string) => void;
}) {
  const order = railOrder(all);
  const at = order.findIndex((x) => x.id === s.id);
  const prev = order[(at - 1 + order.length) % order.length];
  const next = order[(at + 1) % order.length];
  const refinedBy = all.filter((x) => x.counterpart === s.id);
  const fails = sentences(s.failure_modes);
  const anyNotes = s.params.some((p) => p.help);
  const readNext = (READ_NEXT[s.category] ?? ["overfitting", "decay"])
    .map((id) => CONCEPTS.find((c) => c.id === id))
    .filter((c): c is Concept => Boolean(c));
  const link = (x: StrategyMeta) => (
    <button key={x.id} type="button" className="linklike" onClick={() => onOpen(x.id)}>
      {x.name}
    </button>
  );

  const spec: [ReactNode, ReactNode][] = [
    ["Id", s.id],
    ["Category", s.category],
    ["Family", FAMILY_ONE[s.family]],
    [
      <>
        Kind <Help text="What it trades: one symbol at a time, a pair of related symbols, or a group (universe) of symbols it ranks against each other." />
      </>,
      kindLong(s),
    ],
    [
      <>
        Evidence <Help text={`${evidenceLabel(s.evidence)}: ${EVIDENCE_MEANS[s.evidence]}.`} />
      </>,
      <EvidenceBadge level={s.evidence} />,
    ],
    [
      <>
        Uses short <Help text="Short selling: borrowing shares to sell now and buy back later, which profits when the price falls. Some accounts cannot do it." />
      </>,
      s.uses_short ? "Yes, designed to sell short" : "Not required",
    ],
    [
      <>
        Default sizing <Help text="How big its positions are unless you choose otherwise in the Lab. The sizing method sets how much of the account each signal uses." />
      </>,
      s.sizes_itself ? "Its own: the signals are already target weights, so sizing settings don't apply" : sizingText(s.default_sizing),
    ],
    ["Needs", s.needs || <span className="muted">Nothing special</span>],
    [
      <>
        Counterpart <Help text="For a modern method, the classic rule it refines. Race the two to see whether the extra machinery pays for itself." />
      </>,
      counterpart ? link(counterpart) : <span className="muted">–</span>,
    ],
  ];
  if (refinedBy.length) spec.push(["Refined by", <span className="lib-links">{refinedBy.map(link)}</span>]);
  spec.push(["Parameters", s.params.length ? `${s.params.length}` : "none"]);

  return (
    <div className="lib-sheet">
      <nav className="lib-rail-wrap" aria-label="All strategies">
        <div className="rail lib-rail">
          <div className="rail-title">Strategies</div>
          {FAMILIES.map((f) => {
            const items = order.filter((x) => x.family === f);
            return (
              <div key={f} className="lib-rail-group">
                <div className="label lib-rail-label">
                  {FAMILY_TITLE[f]} <span>{items.length}</span>
                </div>
                {items.map((x) => (
                  <a
                    key={x.id}
                    href={`#/library?strategy=${encodeURIComponent(x.id)}`}
                    aria-current={x.id === s.id ? "page" : undefined}
                  >
                    {x.name}
                  </a>
                ))}
              </div>
            );
          })}
        </div>
      </nav>

      <div className="lib-bar">
        <button type="button" className="btn small ghost" onClick={onBack}>
          ← All strategies
        </button>
        <span className="lib-pager">
          <button type="button" className="btn small ghost" onClick={() => onOpen(prev.id)} title={prev.name} aria-label={`Previous: ${prev.name}`}>
            ‹ Prev
          </button>
          <span className="lib-pager-n">
            {pad2(at + 1)} / {pad2(order.length)}
          </span>
          <button type="button" className="btn small ghost" onClick={() => onOpen(next.id)} title={next.name} aria-label={`Next: ${next.name}`}>
            Next ›
          </button>
        </span>
      </div>

      <Panel marks className="lib-ident">
        <div className="lib-ident-grid">
          <div className="lib-ident-main">
            <div className="label">
              {s.category} · {FAMILY_ONE[s.family]}
            </div>
            <h1>{s.name}</h1>
            <div className="lib-tags">
              <EvidenceBadge level={s.evidence} />
              <Tag>{kindShort(s)}</Tag>
              {s.uses_short ? <Tag>Uses shorts</Tag> : null}
              {s.sizes_itself ? <Tag>Sizes itself</Tag> : null}
              <span className="lib-id">{s.id}</span>
            </div>
            <p className="lib-summary">{s.summary}</p>
            <div className="lib-actions">
              <button type="button" className="btn primary" onClick={onTry}>
                Try in Lab
              </button>
              {s.id !== "buy_hold" ? (
                <button type="button" className="btn" onClick={onRace}>
                  Race in simulator
                </button>
              ) : null}
            </div>
            <p className="lib-actions-note">
              {counterpart ? (
                <>
                  Race starts a simulation with this method, the classic rule it refines ({counterpart.name}) and buy-and-hold, on the
                  same market.
                </>
              ) : s.id !== "buy_hold" ? (
                <>The Lab backtests it on history, net of costs; the simulator trades it bar by bar on a market you can steer.</>
              ) : (
                <>The Lab backtests it on history. Every other strategy is raced against it.</>
              )}
            </p>
          </div>

          <section className="callout bad lib-fails" aria-labelledby="lib-fails-title">
            <div className="lib-fails-head">
              <h2 id="lib-fails-title">How it fails</h2>
              <Tag kind="warn">Read first</Tag>
            </div>
            <p className="lib-fails-lede">Every strategy loses money in some markets. This is how this one does.</p>
            {fails.length > 1 ? (
              <ul className="lib-fails-list">
                {fails.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            ) : (
              <p className="lib-fails-one">{s.failure_modes}</p>
            )}
          </section>
        </div>
      </Panel>

      <aside className="lib-side">
        <Panel title="Specification" aside="as modeled">
          <KeyValues rows={spec} />
        </Panel>
      </aside>

      <Panel className="lib-body">
        <section className="lib-sec">
          <h2>Rules</h2>
          <ol className="lib-rules">
            {s.rules.map((r, i) => (
              <li key={r}>
                <span className="lib-rule-n">{pad2(i + 1)}</span>
                <span>{ruleText(r)}</span>
              </li>
            ))}
          </ol>
        </section>
        <section className="lib-sec">
          <h2>Why it might work</h2>
          <p className="lib-prose">{s.rationale}</p>
        </section>
        <section className="lib-sec">
          <h2>What the evidence says</h2>
          <div>
            <p className="lib-ev-line">
              <EvidenceBadge level={s.evidence} />
              <span>{EVIDENCE_MEANS[s.evidence]}</span>
            </p>
            <p className="lib-prose">{s.evidence_text}</p>
          </div>
        </section>
        <section className="lib-sec">
          <h2>
            Parameters <span className="lib-sec-n">{s.params.length}</span>
          </h2>
          <div>
          {s.params.length ? (
            <div className="table-wrap">
              <table className="data lib-params">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th className="num">Default</th>
                    <th className="num">Range</th>
                    {anyNotes ? <th>Notes</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {s.params.map((p) => (
                    <tr key={p.name}>
                      <td className="lib-p-name">
                        {p.label}
                        <span className="lib-id">{p.name}</span>
                      </td>
                      <td className="num lib-p-def">
                        <ParamDefault p={p} />
                      </td>
                      <td className="num lib-p-range">
                        {/* a list of choices wraps between choices, each slash kept with its word; a range never breaks */}
                        {p.kind === "choice" ? (
                          p.choices.map((c, i) => (
                            <Fragment key={c}>
                              {i ? " " : ""}
                              <span className="nowrap">
                                {c}
                                {i < p.choices.length - 1 ? " /" : ""}
                              </span>
                            </Fragment>
                          ))
                        ) : (
                          <span className="nowrap">{paramRange(p)}</span>
                        )}
                      </td>
                      {anyNotes ? <td className="lib-p-help">{p.help || <span className="muted">–</span>}</td> : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted lib-none">No parameters.</p>
          )}
          <p className="lib-sec-foot">
            Default sizing: {String(s.default_sizing.method ?? "fixed").replace("_", " ")}
            {s.uses_short ? " · designed to use short selling" : ""}. Change any parameter in the Lab before a backtest.
          </p>
          </div>
        </section>
        <section className="lib-sec">
          <h2>
            References <span className="lib-sec-n">{s.references.length}</span>
          </h2>
          <ReferenceList refs={s.references} />
        </section>
        {readNext.length ? (
          <section className="lib-sec">
            <h2>Read next</h2>
            <ul className="lib-readnext">
              {readNext.map((c) => (
                <li key={c.id}>
                  <a href={`#/library?tab=concepts&concept=${c.id}`}>{c.title}</a>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </Panel>
    </div>
  );
}

// ---- concepts --------------------------------------------------------------------------------

function Concepts({ focus, onPick }: { focus: string | null; onPick: (id: string) => void }) {
  const [active, setActive] = useState(focus ?? CONCEPTS[0].id);

  // Scroll to the essay named in the address (a link from a data sheet, or a contents click).
  useEffect(() => {
    if (focus) scrollToConcept(focus);
  }, [focus]);

  // Mark the essay being read in the contents rail.
  useEffect(() => {
    const visible = new Set<string>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = e.target.id.replace(/^concept-/, "");
          if (e.isIntersecting) visible.add(id);
          else visible.delete(id);
        }
        const first = CONCEPTS.find((c) => visible.has(c.id));
        if (first) setActive(first.id);
      },
      { rootMargin: "-64px 0px -55% 0px" },
    );
    for (const c of CONCEPTS) {
      const el = document.getElementById(`concept-${c.id}`);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, []);

  const pick = (id: string) => {
    scrollToConcept(id);
    onPick(id);
  };
  const refs = CONCEPTS.reduce((n, c) => n + c.refs.length, 0);

  return (
    <div className="lib-concepts">
      <nav className="lib-toc-wrap" aria-label="Concepts">
        <div className="rail lib-toc">
          <div className="rail-title">Contents</div>
          {CONCEPTS.map((c, i) => (
            <button key={c.id} type="button" aria-current={active === c.id ? "true" : undefined} onClick={() => pick(c.id)}>
              <span className="lib-toc-n">{pad2(i + 1)}</span>
              <span>{c.title}</span>
            </button>
          ))}
          <div className="lib-toc-foot">
            {CONCEPTS.length} essays · {refs} references
          </div>
        </div>
      </nav>

      <details className="lib-toc-compact">
        <summary>
          <span className="label">Contents</span>
          <span className="muted">{CONCEPTS.length} essays</span>
        </summary>
        <ol>
          {CONCEPTS.map((c) => (
            <li key={c.id}>
              <button type="button" className="linklike" onClick={() => pick(c.id)}>
                {c.title}
              </button>
            </li>
          ))}
        </ol>
      </details>

      <Panel className="lib-essays">
        {CONCEPTS.map((c, i) => (
          <section key={c.id} id={`concept-${c.id}`} className="lib-essay" aria-labelledby={`concept-${c.id}-t`}>
            <div className="lib-essay-main prose">
              <div className="label lib-essay-n">Concept {pad2(i + 1)}</div>
              <h2 id={`concept-${c.id}-t`}>{c.title}</h2>
              {c.body.map((p, j) => (
                <p key={j}>{p}</p>
              ))}
            </div>
            {c.refs.length ? (
              <aside className="lib-essay-refs">
                <div className="label">References</div>
                <ReferenceList refs={c.refs} />
              </aside>
            ) : null}
          </section>
        ))}
      </Panel>
    </div>
  );
}
