import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useApp, type Route } from "../state";
import type { GateResult, PaperDetail, PipelineOverview, PipelineSpec, RunBrief, SpecDetail } from "../types";
import { ErrorBox, Spinner, StatusDot, Tabs } from "../components/ui";
import { KillSwitchControl } from "../components/KillSwitch";
import LineChart from "../components/LineChart";
import Markdown from "../components/Markdown";
import { fmtMoney } from "../format";
import { useChartColors } from "../theme";
import "../styles/pipeline.css";

/** The four stages every strategy passes, in order, before real money. */
const STAGES: [string, string][] = [
  ["Write it down", "A spec: the hypothesis, why the edge should exist, exact rules, a locked test period. Frozen before research."],
  ["Research it", "The 14-point acceptance checklist, ending with the locked test, opened once."],
  ["Paper trade it", "8 to 12 weeks on Alpaca's paper account, measured against a replay of the same bars."],
  ["The gate", "Ten checks before real money, including the kill switch drills. Most strategies stop before here: that is the system working."],
];

type Who = "you" | "anyone";
interface Step {
  text: string;
  command: string;
  who: Who;
}

/** What to do next for a spec, and who does it (freezing, the locked test and attestations are Jack's). */
function nextStep(s: PipelineSpec): Step {
  const r = s.latest_run;
  if (s.drift) return { text: "The spec changed after it was frozen, so its results no longer count. Start a new version.", command: `trades spec version ${s.id}`, who: "anyone" };
  if (!s.frozen && !r) return { text: "Rehearse the pipeline on synthetic data, then review the rules and freeze them.", command: `trades research ${s.id} --provider synthetic`, who: "anyone" };
  if (!s.frozen) return { text: "Review every rule, then freeze the spec: no rule changes after this.", command: `trades spec freeze ${s.id}`, who: "you" };
  if (!r || r.provider === "synthetic") return { text: "Run the acceptance checklist on real data.", command: `trades research ${s.id}`, who: "anyone" };
  if (r.verdict === "fail") return { text: "It failed the checklist; most strategies do. Read the report: a new idea is a new version, not a retune.", command: `trades runs show ${r.id}`, who: "anyone" };
  if (r.verdict === "ready_for_locked_test") return { text: "Every other check passed. Open the locked test, once: its result is final.", command: `trades research ${s.id} --open-locked-test`, who: "you" };
  if (!s.paper.started) return { text: "Paper trade it: set paper.spec in config/paper.yaml, then let the scheduled runner send the orders.", command: `trades paper --spec ${s.id} --submit`, who: "you" };
  return { text: "Keep paper trading. Each week, compare it with its replay; the gate needs 8 to 12 weeks.", command: `trades track ${s.id} && trades gate ${s.id}`, who: "anyone" };
}

function verdictBadge(r: RunBrief | null) {
  if (!r) return <span className="badge neutral">No research run</span>;
  if (r.verdict === "pass") return <span className="badge up">Passed {r.passed}/14</span>;
  if (r.verdict === "ready_for_locked_test") return <span className="badge neutral">Locked test sealed · {r.passed}/14</span>;
  return <span className="badge down">Failed {r.failed} of 14</span>;
}

/** How many stages are complete, in order: a later stage never counts before the earlier ones. */
function stageReached(s: PipelineSpec): number {
  const done = [Boolean(s.frozen && !s.drift), s.latest_run?.verdict === "pass", s.paper.started];
  const first = done.indexOf(false);
  return first === -1 ? done.length : first;
}

export default function Pipeline({ route }: { route: Route }) {
  const { navigate } = useApp();
  const ref = route.params.get("spec");
  const tab = (["spec", "research", "paper", "gate"] as Tab[]).find((t) => t === route.params.get("tab")) ?? "spec";
  return ref ? (
    <SpecPage specRef={ref} initialTab={tab} onBack={() => navigate("/pipeline")} />
  ) : (
    <Overview onOpen={(key) => navigate("/pipeline", { spec: key })} />
  );
}

function NextStep({ step }: { step: Step }) {
  return (
    <div className="callout info">
      <strong>Next:</strong> {step.text}{" "}
      <code style={{ whiteSpace: "nowrap" }}>{step.command}</code>
      {step.who === "you" ? <span className="small muted"> (yours to run: the hooks stop Claude Code from doing it)</span> : null}
    </div>
  );
}

function Overview({ onOpen }: { onOpen: (key: string) => void }) {
  const [data, setData] = useState<PipelineOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.pipeline().then(setData, (e: Error) => setError(e.message));
  }, []);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner label="Reading specs and research runs..." />;
  return (
    <div className="stack">
      <div>
        <h1>Pipeline</h1>
        <p className="secondary" style={{ marginTop: 4, maxWidth: "85ch" }}>
          Every strategy passes the same four stages before real money, and backtest, paper trading and the risk limits share one
          code path, so what was researched is what trades. Pick a spec to see its rules, its checklist and its paper record.
        </p>
      </div>
      <ol className="stages">
        {STAGES.map(([title, text], i) => (
          <li key={title} className="stage">
            <span className="stage-n">{i + 1}</span>
            <div>
              <strong>{title}</strong>
              <div className="small secondary">{text}</div>
            </div>
          </li>
        ))}
      </ol>
      <div className="card">
        <div className="card-header">
          <h2>Kill switch</h2>
          <span className="sub">Checked before every paper order</span>
        </div>
        <KillSwitchControl initial={data.kill_switch} />
      </div>
      {data.problems.length ? (
        <div className="callout warn">
          These spec files could not be read:
          <ul>
            {data.problems.map(([file, why]) => (
              <li key={file}>
                <code>{file}</code>: {why}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="stack" style={{ gap: 10 }}>
        {data.specs.map((s) => (
          <button key={s.key} className="lib-card spec-card" onClick={() => onOpen(s.key)}>
            <span className="row tight" style={{ justifyContent: "space-between", width: "100%" }}>
              <span className="small muted">
                {s.key} · {s.strategy} · {s.timeframe} · {s.n_symbols} symbol{s.n_symbols === 1 ? "" : "s"}
                {s.paper_target ? " · the paper account trades this" : ""}
              </span>
              <span className="row tight">
                <span className={`badge ${s.frozen ? "up" : "neutral"}`}>{s.frozen ? `Frozen ${s.frozen}` : "Draft"}</span>
                {verdictBadge(s.latest_run)}
              </span>
            </span>
            <strong style={{ fontSize: 16 }}>{s.name}</strong>
            <span className="stage-track" aria-label={`Stage ${stageReached(s) + 1} of 4`}>
              {STAGES.map(([title], i) => (
                <span key={title} className={`stage-pip ${i < stageReached(s) ? "done" : i === stageReached(s) ? "now" : ""}`}>
                  {title}
                </span>
              ))}
            </span>
            <span className="small secondary">{nextStep(s).text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

type Tab = "spec" | "research" | "paper" | "gate";

function SpecPage({ specRef, initialTab, onBack }: { specRef: string; initialTab: Tab; onBack: () => void }) {
  const [detail, setDetail] = useState<SpecDetail | null>(null);
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [overview, setOverview] = useState<PipelineSpec | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);

  const load = useCallback(() => {
    setError(null);
    Promise.all([api.spec(specRef), api.paper(specRef), api.pipeline()]).then(
      ([d, p, o]) => {
        setDetail(d);
        setPaper(p);
        setOverview(o.specs.find((s) => s.key === d.spec.key) ?? null);
      },
      (e: Error) => setError(e.message),
    );
  }, [specRef]);
  useEffect(load, [load]);

  if (error) return <ErrorBox error={error} />;
  if (!detail || !paper) return <Spinner label="Loading the spec..." />;
  const spec = detail.spec as SpecDetail["spec"] & Record<string, unknown>;
  return (
    <div className="stack">
      <div>
        <button className="btn ghost" onClick={onBack}>
          ← All specs
        </button>
      </div>
      <div className="card">
        <div className="card-header">
          <div>
            <div className="small muted">{spec.key}</div>
            <h1>{spec.name}</h1>
          </div>
          <span className="row tight">
            <span className={`badge ${spec.frozen ? "up" : "neutral"}`}>{spec.frozen ? `Frozen ${String(spec.frozen)}` : "Draft"}</span>
            {verdictBadge(detail.runs[0] ?? null)}
          </span>
        </div>
        {overview ? <NextStep step={nextStep(overview)} /> : null}
      </div>
      <Tabs
        tabs={[
          ["spec", "The spec"],
          ["research", `Research (${detail.runs.length})`],
          ["paper", "Paper trading"],
          ["gate", "The gate"],
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "spec" ? <SpecTab detail={detail} /> : null}
      {tab === "research" ? <ResearchTab detail={detail} /> : null}
      {tab === "paper" ? <PaperTab paper={paper} /> : null}
      {tab === "gate" ? <GateTab specRef={specRef} /> : null}
    </div>
  );
}

/** A spec value in plain words: objects as "key: value" pairs, lists as alternatives (or ranges). */
function show(v: unknown, lists: "or" | "range" = "or"): string {
  if (v === null || v === undefined || v === "") return "–";
  if (Array.isArray(v)) return lists === "range" && v.length === 2 ? `${show(v[0])} to ${show(v[1])}` : v.map((x) => show(x)).join(lists === "or" ? " or " : ", ");
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? entries.map(([k, x]) => `${k.replace(/_/g, " ")}: ${show(x, lists)}`).join(" · ") : "–";
  }
  return String(v);
}

function SpecTab({ detail }: { detail: SpecDetail }) {
  const s = detail.spec as Record<string, unknown> & { periods: Record<string, [string, string]> };
  const strategy = s.strategy as { id: string; params: Record<string, unknown>; sizing: Record<string, unknown> };
  const universe = s.universe as { symbols?: string[]; survivorship_free?: boolean };
  return (
    <div className="stack">
      <div className="card">
        <h2>What the code runs</h2>
        <dl className="kv">
          <dt>Strategy</dt>
          <dd>
            <code>{strategy.id}</code> {show(strategy.params)}
          </dd>
          <dt>Sizing</dt>
          <dd>{show(strategy.sizing)}</dd>
          <dt>Universe</dt>
          <dd>
            {(universe.symbols ?? []).slice(0, 20).join(", ")}
            {(universe.symbols ?? []).length > 20 ? " ..." : ""} ({universe.survivorship_free ? "survivorship-free" : "not survivorship-free"})
          </dd>
          <dt>Data</dt>
          <dd>{show(s.data)}</dd>
          {Object.entries(s.periods).map(([name, [a, b]]) => (
            <div key={name} style={{ display: "contents" }}>
              <dt>{name === "test" ? "Locked test" : name[0].toUpperCase() + name.slice(1)}</dt>
              <dd>
                {a} to {b}
                {name === "test" ? " (sealed until Jack opens it, once)" : ""}
              </dd>
            </div>
          ))}
          <dt>Costs</dt>
          <dd>{show(s.costs)} (on top of config/base.yaml)</dd>
          <dt>Variants allowed</dt>
          <dd>{show(s.parameters)}</dd>
          <dt>Expected</dt>
          <dd>{show(s.expected, "range")}</dd>
          <dt>Retire it when</dt>
          <dd>{show(s.invalidation)}</dd>
        </dl>
      </div>
      <div className="card">
        <Markdown text={detail.body} />
      </div>
    </div>
  );
}

const MARK: Record<string, "good" | "bad" | "warn" | "info"> = { pass: "good", fail: "bad", pending: "warn", "n/a": "info" };

function ResearchTab({ detail }: { detail: SpecDetail }) {
  if (!detail.runs.length)
    return (
      <div className="callout info">
        No research run yet. <code>trades research {String(detail.spec.id)} --provider synthetic</code> rehearses the pipeline;{" "}
        <code>trades research {String(detail.spec.id)}</code> runs it on the spec&apos;s data.
      </div>
    );
  return (
    <div className="stack">
      <div className="card">
        <div className="card-header">
          <h2>The acceptance checklist</h2>
          <span className="sub">
            Latest run {detail.runs[0].id}; {detail.variants_tested} variant{detail.variants_tested === 1 ? "" : "s"} tested so far (each one
            deflates the Sharpe ratio)
          </span>
        </div>
        <ol className="checklist">
          {(detail.checklist ?? []).map((c) => (
            <li key={c.n}>
              <StatusDot status={MARK[c.status] ?? "info"}>
                <strong>{c.name}</strong>
              </StatusDot>{" "}
              <span className="small">
                {c.value} <span className="muted">(needs {c.threshold})</span>
              </span>
            </li>
          ))}
        </ol>
      </div>
      <div className="card">
        <h2>Runs</h2>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Run</th>
                <th>Data</th>
                <th>Verdict</th>
                <th>Passed</th>
                <th>Failed</th>
                <th>Pending</th>
              </tr>
            </thead>
            <tbody>
              {detail.runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <code>{r.id}</code>
                  </td>
                  <td>{r.provider ?? "–"}</td>
                  <td>{verdictBadge(r)}</td>
                  <td>{r.passed}</td>
                  <td>{r.failed}</td>
                  <td>{r.pending}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {detail.report ? (
        <div className="card">
          <Markdown text={detail.report} />
        </div>
      ) : null}
    </div>
  );
}

function PaperTab({ paper }: { paper: PaperDetail }) {
  const colors = useChartColors();
  if (!paper.equity.length && !paper.orders.length)
    return (
      <div className="callout info">
        Not paper traded yet. A spec trades on paper after its research run passes the whole checklist; the records then appear in{" "}
        <code>paper/{paper.key}/</code>.
      </div>
    );
  const series = {
    t: paper.equity.map((e) => Math.floor(new Date(`${e.date}T00:00:00Z`).getTime() / 1000)),
    v: paper.equity.map((e) => e.equity),
  };
  return (
    <div className="stack">
      <div className="card">
        <div className="card-header">
          <h2>Paper equity</h2>
          <span className="sub">
            {paper.equity.length} sessions, {paper.decisions} decisions, {paper.orders.length} recent orders
          </span>
        </div>
        {paper.equity.length > 1 ? (
          <LineChart lines={[{ id: "equity", label: "Paper equity", data: series, color: colors.series[0] }]} format={(v) => fmtMoney(v)} />
        ) : null}
      </div>
      {paper.tracking ? (
        <div className="card">
          <Markdown text={paper.tracking} />
        </div>
      ) : (
        <div className="callout info">
          No tracking report yet: <code>trades track {paper.key.replace(/-v\d+$/, "")}</code> replays the paper period through the backtest
          engine and compares signals, fills and expectancy.
        </div>
      )}
      {paper.latest_report ? (
        <div className="card">
          <Markdown text={paper.latest_report} />
        </div>
      ) : null}
      {paper.events.length ? (
        <div className="card">
          <h2>Recent events</h2>
          <ul className="event-feed">
            {[...paper.events].reverse().map((e, i) => (
              <li key={i} className="small">
                <span className="muted">{e.time.replace("T", " ").replace("Z", " UTC")}</span> <strong>{e.kind}</strong>: {e.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function GateTab({ specRef }: { specRef: string }) {
  const [result, setResult] = useState<GateResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.gate(specRef));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const verdictText = {
    pass: "Passed: the evidence supports building a live path. That is Phase 4, and you build it yourself, not Claude.",
    not_yet: "Not yet: keep paper trading and finish the pending items.",
    fail: "Failed. Most strategies fail here: that is the system working.",
  };
  return (
    <div className="stack">
      <div className="card">
        <div className="card-header">
          <h2>The paper-to-live gate</h2>
          <span className="sub">Ten checks before real money. Passing turns nothing on: there is no live order path in this app.</span>
        </div>
        <button className="btn primary" disabled={busy} onClick={() => void run()}>
          {busy ? "Replaying the paper period..." : "Check the gate now"}
        </button>
        <ErrorBox error={error} />
        {result ? (
          <>
            <ol className="checklist" style={{ marginTop: 12 }}>
              {result.checks.map((c) => (
                <li key={c.n}>
                  <StatusDot status={c.status === "pass" ? "good" : c.status === "fail" ? "bad" : "warn"}>
                    <strong>{c.name}</strong>
                  </StatusDot>{" "}
                  <span className="small">{c.detail}</span>
                </li>
              ))}
            </ol>
            <div className={`callout ${result.verdict === "pass" ? "info" : result.verdict === "fail" ? "bad" : "warn"}`}>
              {verdictText[result.verdict]}
            </div>
          </>
        ) : null}
        <p className="small muted" style={{ marginTop: 10 }}>
          Some checks only you can complete, on the command line: the four kill switch drills (<code>trades paper drill halt</code>,{" "}
          <code>flatten</code>, <code>daily-loss</code>, <code>revoke-key</code>), a test alert (<code>trades paper drill alert</code>),
          and your review of <code>config/live.yaml</code> and <code>risk/limits.yaml</code> (<code>trades gate --attest live_config</code>).
        </p>
      </div>
    </div>
  );
}
