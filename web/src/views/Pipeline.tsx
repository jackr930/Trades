/**
 * The Pipeline: the app's home. Every spec's place in the four stages before real money (write it
 * down, research it, paper trade it, the gate), the next step for each and who runs it, the kill
 * switch and the alert feed; and a page per spec with its rules, research, paper record and gate.
 *
 * Laid out as an inspector (docs/DESIGN.md, after Chokepoint Watch): a queue rail of specs on the
 * left, the selected spec's inspector in the middle with the four stages drawn as a schematic, and
 * a strip of panels below (the kill switch, the alert feed).
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { api } from "../api";
import { useApp, useStatusLine, type Route } from "../state";
import type {
  AlertEvent,
  AlertFeed,
  GateCheck,
  GateResult,
  PaperDetail,
  PipelineOverview,
  PipelineSpec,
  RunBrief,
  SpecDetail,
} from "../types";
import { ErrorBox, Help, Spinner } from "../components/ui";
import { Figure, Hatch, InlineText, KeyValues, Panel, Readout, Tag, type TagKind, type Tone } from "../components/instrument";
import { KillSwitchControl } from "../components/KillSwitch";
import LineChart from "../components/LineChart";
import Markdown from "../components/Markdown";
import { fmtInt, fmtMoney, fmtPct } from "../format";
import { useChartColors } from "../theme";
import "../styles/pipeline.css";

// ---- the four stages -------------------------------------------------------------------------

type Tab = "spec" | "research" | "paper" | "gate";
const TABS: Tab[] = ["spec", "research", "paper", "gate"];

interface StageDef {
  id: Tab;
  name: string;
  /** The spec page's tab for this stage. */
  tab: string;
  /** The inspector's link to that tab. */
  link: string;
  text: string;
}

/** The four stages every strategy passes, in order, before real money. Each one has a tab on the spec page. */
const STAGES: StageDef[] = [
  {
    id: "spec",
    name: "Write it down",
    tab: "The spec",
    link: "Rules",
    text: "A spec: the hypothesis, why the edge should exist, exact rules, a locked test period. Frozen before research.",
  },
  {
    id: "research",
    name: "Research it",
    tab: "Research",
    link: "Checklist",
    text: "The 14-point acceptance checklist, ending with the locked test, opened once.",
  },
  {
    id: "paper",
    name: "Paper trade it",
    tab: "Paper trading",
    link: "Paper record",
    text: "8 to 12 weeks on Alpaca's paper account, measured against a replay of the same bars.",
  },
  {
    id: "gate",
    name: "The gate",
    tab: "The gate",
    link: "Gate",
    text: "Ten checks before real money, including the kill switch drills. Most strategies stop before here: that is the system working.",
  },
];

type StageState = "done" | "now" | "later";
const STATE_TAG: Record<StageState, TagKind> = { done: "ink", now: "crit", later: "info" };
/** The word on a stage's tag: paper trading never really ends before the gate, so once started it is "running". */
const stateWord = (id: Tab, state: StageState) => (id === "paper" && state === "done" ? "running" : state);

/** How many stages are complete, in order: a later stage never counts before the earlier ones. */
function stageReached(s: PipelineSpec): number {
  const done = [Boolean(s.frozen && !s.drift), s.latest_run?.verdict === "pass", s.paper.started];
  const first = done.indexOf(false);
  return first === -1 ? done.length : first;
}

const stageState = (i: number, reached: number): StageState => (i < reached ? "done" : i === reached ? "now" : "later");

// ---- the next step ---------------------------------------------------------------------------

type Who = "you" | "anyone";
interface Step {
  text: string;
  /** The same step as a short clause, for the status line ("Next: ..."). */
  short: string;
  command: string;
  who: Who;
}

/** What to do next for a spec, and who does it (freezing, the locked test and attestations are Jack's). */
function nextStep(s: PipelineSpec): Step {
  const r = s.latest_run;
  if (s.drift)
    return {
      text: "The spec changed after it was frozen, so its results no longer count. Start a new version.",
      short: "it changed after it was frozen, so start a new version",
      command: `trades spec version ${s.id}`,
      who: "anyone",
    };
  if (!s.frozen && !r)
    return {
      text: "Rehearse the pipeline on synthetic data, then review the rules and freeze them.",
      short: "rehearse it on synthetic data, then freeze it",
      command: `trades research ${s.id} --provider synthetic`,
      who: "anyone",
    };
  if (!s.frozen)
    return {
      text: "Review every rule, then freeze the spec: no rule changes after this.",
      short: "review every rule, then freeze it (yours to run)",
      command: `trades spec freeze ${s.id}`,
      who: "you",
    };
  if (!r || r.provider === "synthetic")
    return { text: "Run the acceptance checklist on real data.", short: "run the acceptance checklist on real data", command: `trades research ${s.id}`, who: "anyone" };
  if (r.verdict === "fail")
    return {
      text: "It failed the checklist; most strategies do. Read the report: a new idea is a new version, not a retune.",
      short: "read the report; a new idea is a new version, not a retune",
      command: `trades runs show ${r.id}`,
      who: "anyone",
    };
  if (r.verdict === "ready_for_locked_test")
    return {
      text: "Every other check passed. Open the locked test, once: its result is final.",
      short: "open the locked test, once (yours to run)",
      command: `trades research ${s.id} --open-locked-test`,
      who: "you",
    };
  if (!s.paper.started)
    return {
      text: "Paper trade it: set paper.spec in config/paper.yaml, then let the scheduled runner send the orders.",
      short: "start paper trading it (yours to run)",
      command: `trades paper --spec ${s.id} --submit`,
      who: "you",
    };
  return {
    text: "Keep paper trading. Each week, compare it with its replay; the gate needs 8 to 12 weeks.",
    short: "keep paper trading, and compare it with its replay each week",
    command: `trades track ${s.id} && trades gate ${s.id}`,
    who: "anyone",
  };
}

// ---- small helpers ---------------------------------------------------------------------------

function verdictTag(r: RunBrief | null): { kind: TagKind; text: string } {
  if (!r) return { kind: "info", text: "No research run" };
  if (r.verdict === "pass") return { kind: "ok", text: `Passed ${r.passed}/14` };
  if (r.verdict === "ready_for_locked_test") return { kind: "info", text: `Locked test sealed · ${r.passed}/14` };
  return { kind: "warn", text: `Failed ${r.failed} of 14` };
}

function verdictWord(r: RunBrief): string {
  if (r.verdict === "pass") return "passed";
  if (r.verdict === "ready_for_locked_test") return "locked test sealed";
  return "failed";
}

/** DRAFT / FROZEN date / changed after freezing, the latest verdict, and whether the paper account trades it. */
function SpecTags({ frozen, drift, run, target }: { frozen: string | null; drift: string | null; run: RunBrief | null; target: boolean }) {
  const v = verdictTag(run);
  return (
    <div className="pl-tags">
      {drift ? (
        <Tag kind="warn" title={drift}>
          Changed after freezing
        </Tag>
      ) : (
        <Tag kind={frozen ? "ink" : "info"}>{frozen ? `Frozen ${frozen}` : "Draft"}</Tag>
      )}
      <Tag kind={v.kind}>{v.text}</Tag>
      {target ? <Tag title="config/paper.yaml names this spec: the scheduled paper runner trades it">Paper account</Tag> : null}
    </div>
  );
}

function timeframeLabel(tf: string): string {
  const m = /^(\d+)\s*(m|min|h|d|w)$/i.exec(tf.trim());
  if (!m) return tf;
  const n = Number(m[1]);
  const unit = m[2].toLowerCase();
  if (unit === "d") return n === 1 ? `${tf} · daily bars` : `${tf} · ${n}-day bars`;
  if (unit === "w") return n === 1 ? `${tf} · weekly bars` : `${tf} · ${n}-week bars`;
  if (unit === "h") return n === 1 ? `${tf} · hourly bars` : `${tf} · ${n}-hour bars`;
  return `${tf} · ${n}-minute bars`;
}

function universeText(s: PipelineSpec): string {
  const shown = s.symbols.slice(0, 4);
  const more = s.n_symbols - shown.length;
  const count = `${s.n_symbols} symbol${s.n_symbols === 1 ? "" : "s"}`;
  if (!shown.length) return count;
  return `${shown.join(" ")}${more > 0 ? ` +${more}` : ""} · ${count}`;
}

const ET_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "2-digit", day: "2-digit" });
const ET_HM = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const ET_DATE = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

/** A record's UTC timestamp as New York day and time ("09-04", "15:45"), the session's own clock. */
function etParts(iso: string): { day: string; time: string } {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { day: "–", time: iso };
  return { day: ET_DAY.format(d).replace("/", "-"), time: ET_HM.format(d) };
}

function etDate(iso: string): string | null {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : ET_DATE.format(d);
}

function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

const DAY_MS = 86_400_000;
const dayNum = (date: string) => Date.parse(`${date}T00:00:00Z`);

/** Weeks between the first and last paper session, as the tracking report counts them. */
function paperWeeks(first?: string | null, last?: string | null): number | null {
  if (!first || !last) return null;
  const a = dayNum(first);
  const b = dayNum(last);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.round(((b - a) / DAY_MS / 7) * 10) / 10;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** How loud each kind of paper event is (mirrors the server's alert grading; every other kind is INFO). */
const SEVERITY: Record<string, "crit" | "warn"> = {
  error: "crit",
  halt: "crit",
  daily_loss: "crit",
  weekly_loss: "crit",
  rejection: "warn",
  data_gap: "warn",
  orders: "warn",
  positions: "warn",
};
const severityOf = (kind: string): "crit" | "warn" | "info" => SEVERITY[kind] ?? "info";

/** A gate report as `trades gate` writes it (paper/<key>/GATE.md): " 2. [PASS] Name: detail". */
interface RecordedGate {
  date: string | null;
  checks: GateCheck[];
  verdict: GateResult["verdict"];
}

function parseGateReport(md: string | null | undefined): RecordedGate | null {
  if (!md) return null;
  const lines = md.split("\n");
  const date = /\((\d{4}-\d{2}-\d{2})\)/.exec(lines[0] ?? "")?.[1] ?? null;
  const checks: GateCheck[] = [];
  for (const line of lines) {
    const m = /^\s*(\d+)\.\s+\[(PASS|FAIL|\.\.\.\.)\]\s+([^:]+):\s*(.*)$/.exec(line);
    if (m) checks.push({ n: Number(m[1]), name: m[3].trim(), status: m[2] === "PASS" ? "pass" : m[2] === "FAIL" ? "fail" : "pending", detail: m[4].trim() });
  }
  if (!checks.length) return null;
  return { date, checks, verdict: verdictOf(checks) };
}

function verdictOf(checks: GateCheck[]): GateResult["verdict"] {
  if (checks.some((c) => c.status === "fail")) return "fail";
  return checks.every((c) => c.status === "pass") ? "pass" : "not_yet";
}

function gateCounts(checks: GateCheck[]) {
  return {
    pass: checks.filter((c) => c.status === "pass").length,
    pending: checks.filter((c) => c.status === "pending").length,
    fail: checks.filter((c) => c.status === "fail").length,
  };
}

/** Weeks of paper trading the gate asks for: what the last gate report says, else the default rule. */
function weeksNeeded(s: PipelineSpec, spec?: SpecDetail["spec"], gate?: RecordedGate | null): number {
  const said = gate?.checks.find((c) => c.name === "Paper duration")?.detail.match(/of (\d+(?:\.\d+)?) weeks/);
  if (said) return Number(said[1]);
  return s.timeframe === "1d" || spec?.one_trade_per_day === true ? 12 : 8;
}

/** A spec value in plain words: objects as "key: value" pairs, lists as alternatives (or ranges). */
function show(v: unknown, lists: "or" | "range" = "or"): string {
  if (v === null || v === undefined || v === "") return "–";
  if (Array.isArray(v)) {
    if (!v.length) return "–";
    // A list of strategies (the consensus's members): their ids.
    if (v.every((x) => x !== null && typeof x === "object" && !Array.isArray(x) && "id" in x))
      return v.map((x) => String((x as { id: unknown }).id)).join(", ");
    return lists === "range" && v.length === 2 ? `${show(v[0])} to ${show(v[1])}` : v.map((x) => show(x)).join(lists === "or" ? " or " : ", ");
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? entries.map(([k, x]) => `${k.replace(/_/g, " ")}: ${show(x, lists)}`).join(" · ") : "–";
  }
  return String(v);
}

/** Copy a command to the clipboard, falling back to a hidden text area where the Clipboard API is not allowed. */
function useCopy(): [string | null, (text: string) => void] {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const copy = useCallback((text: string) => {
    const done = () => {
      setCopied(text);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(null), 1600);
    };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        if (document.execCommand("copy")) done();
      } finally {
        ta.remove();
      }
    };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [copied, copy];
}

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---- shared pieces ---------------------------------------------------------------------------

/** "Nothing here yet": a hatched band, one sentence and what fills it. */
function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="pl-empty">
      <Hatch />
      <div className="pl-empty-text">{children}</div>
    </div>
  );
}

/** A command on one line, ready to copy: `$ trades research STRAT-002`. */
function CommandLine({ command }: { command: string }) {
  const [copied, copy] = useCopy();
  const done = copied === command;
  return (
    <div className="pl-cmd">
      <span className="pl-cmd-prompt" aria-hidden="true">
        $
      </span>
      <code className="pl-cmd-text">{command}</code>
      <button type="button" className="pl-cmd-copy" onClick={() => copy(command)} aria-label={`Copy the command ${command}`}>
        {done ? "Copied" : "Copy"}
      </button>
      <span className="pl-sr" aria-live="polite">
        {done ? "Copied to the clipboard" : ""}
      </span>
    </div>
  );
}

function NextStepBlock({ step, wide }: { step: Step; wide?: boolean }) {
  return (
    <section className={`pl-next${wide ? " wide" : ""}`} aria-label="Next step">
      <div className="pl-next-main">
        <div className="pl-next-head">
          <span className="label">Next step</span>
          <span className="pl-who">
            <span className="label">Who runs it</span>
            <Tag kind={step.who === "you" ? "ink" : "info"}>{step.who === "you" ? "You only" : "You or Claude Code"}</Tag>
            <Help
              text={
                step.who === "you"
                  ? "Freezing a spec, opening a locked test, gate attestations and paper orders are yours alone: the repository's hooks stop Claude Code from running them."
                  : "Anyone can run this: you on the command line, or Claude Code for you."
              }
            />
          </span>
        </div>
        <p className="pl-next-text">{step.text}</p>
      </div>
      <div className="pl-next-cmd">
        <CommandLine command={step.command} />
        {step.who === "you" ? <p className="pl-next-note">Yours to run: the hooks stop Claude Code from doing it.</p> : null}
      </div>
    </section>
  );
}

/** A readout in a hairline-divided strip (the shared Readout inside this view's grid cell). */
function Ro(props: { label: ReactNode; value: ReactNode; unit?: ReactNode; note?: ReactNode; tone?: Tone; size?: "sm" }) {
  return (
    <div className="pl-ro">
      <Readout {...props} />
    </div>
  );
}

// ---- the schematic: four compartments, like a cross-section ----------------------------------

type CellState = "pass" | "fail" | "pending" | "na" | "unknown" | "part";
interface Cell {
  state: CellState;
  title: string;
  /** For "part": how much of the cell is done (0 to 1). */
  fill?: number;
}

function Cells({ cells, label, mark, names }: { cells: Cell[]; label: string; mark?: number; names?: string[] }) {
  const row = (
    <div className="pl-cells" role="img" aria-label={label}>
      {cells.map((c, i) => (
        <span
          key={i}
          className={`pl-cell ${c.state}`}
          title={c.title}
          style={c.fill !== undefined ? ({ "--fill": `${Math.round(c.fill * 100)}%` } as CSSProperties) : undefined}
        />
      ))}
      {mark !== undefined && mark > 0 && mark < cells.length ? (
        <span className="pl-cells-mark" style={{ left: `calc(${(mark / cells.length) * 100}% - 1px)` }} aria-hidden="true" />
      ) : null}
    </div>
  );
  if (!names) return row;
  return (
    <div className="pl-cells-named">
      {row}
      <div className="pl-cells-names" aria-hidden="true">
        {names.map((n, i) => (
          <span key={n} className={cells[i]?.state === "pass" ? "on" : undefined}>
            {n}
          </span>
        ))}
      </div>
    </div>
  );
}

const repeat = (n: number, cell: Cell): Cell[] => Array.from({ length: Math.max(0, n) }, () => cell);

interface StageView {
  cells: Cell[];
  fact: string;
  label: string;
  mark?: number;
  /** Names under the cells, when there are few enough to name. */
  names?: string[];
}

/** What each stage looks like for one spec: its cells, one line of fact and a description for screen readers. */
function stageViews(s: PipelineSpec, detail?: InspectorDetail, gateOverride?: RecordedGate | null): StageView[] {
  const spec = detail?.spec;
  const gate = gateOverride !== undefined ? gateOverride : parseGateReport(detail?.paper?.gate);

  // 1. Write it down: written, rehearsed on synthetic data (optional once frozen), frozen.
  const r = s.latest_run;
  const rehearsed = spec ? spec.runs.some((x) => x.provider === "synthetic") : r?.provider === "synthetic";
  const rehearsal: Cell = rehearsed
    ? { state: "pass", title: "Rehearsed on synthetic data" }
    : s.frozen
      ? { state: "na", title: "No rehearsal on record (it is optional)" }
      : { state: "pending", title: "Not rehearsed yet: trades research --provider synthetic" };
  const frozenCell: Cell = s.drift
    ? { state: "fail", title: `Changed after it was frozen: ${s.drift}` }
    : s.frozen
      ? { state: "pass", title: `Frozen ${s.frozen}` }
      : { state: "pending", title: "Not frozen yet" };
  const write: StageView = {
    cells: [{ state: "pass", title: "Written: the spec file exists" }, rehearsal, frozenCell],
    names: ["Written", "Rehearsed", s.drift ? "Changed" : "Frozen"],
    fact: s.drift ? "Changed after freezing" : s.frozen ? `Frozen ${s.frozen}` : rehearsed ? "Rehearsed · not frozen" : "Draft · not frozen",
    label: `Written; ${rehearsed ? "rehearsed on synthetic data" : "not rehearsed"}; ${s.drift ? "changed after it was frozen" : s.frozen ? `frozen on ${s.frozen}` : "not frozen"}`,
  };

  // 2. Research it: the 14 checks of the latest run, in order once the checklist is loaded.
  const list = spec?.checklist;
  let rcells: Cell[];
  if (list && list.length)
    rcells = list.map((c) => ({
      state: c.status === "n/a" ? "na" : c.status,
      title: `${c.n}. ${c.name}: ${c.value} (needs ${c.threshold}) · ${c.status.toUpperCase()}`,
    }));
  else if (r)
    rcells = [
      ...repeat(r.passed, { state: "pass", title: "Passed" }),
      ...repeat(r.failed, { state: "fail", title: "Failed" }),
      ...repeat(r.pending, { state: "pending", title: "Pending" }),
      ...repeat(14 - r.passed - r.failed - r.pending, { state: "na", title: "Not applicable" }),
    ];
  else rcells = repeat(14, { state: "pending", title: "Not run yet" });
  const research: StageView = {
    cells: rcells,
    fact: r ? `${r.passed} pass · ${r.failed} fail · ${r.pending} pending${r.provider === "synthetic" ? " · synthetic" : ""}` : "No research run yet",
    label: r ? `Latest run: ${r.passed} of 14 checks passed, ${r.failed} failed, ${r.pending} pending` : "No research run yet",
  };

  // 3. Paper trade it: twelve weeks, filled as the paper record grows; the tick marks the gate's minimum.
  const weeks = s.paper.started ? (paperWeeks(s.paper.first, s.paper.last) ?? 0) : 0;
  const need = weeksNeeded(s, spec?.spec, gate);
  const whole = Math.floor(weeks);
  const pcells: Cell[] = Array.from({ length: 12 }, (_, i) =>
    i < whole
      ? { state: "pass", title: `Week ${i + 1}: traded` }
      : i === whole && weeks > whole
        ? { state: "part", fill: weeks - whole, title: `Week ${i + 1}: under way` }
        : { state: "pending", title: `Week ${i + 1}` },
  );
  const paper: StageView = {
    cells: pcells,
    mark: need,
    fact: s.paper.started ? `${plural(s.paper.sessions ?? 0, "session")} · ${weeks} of ${need} wk` : `Not started · needs ${need} wk`,
    label: s.paper.started ? `${s.paper.sessions ?? 0} paper sessions, ${weeks} of ${need} weeks` : "Not paper traded yet",
  };

  // 4. The gate: ten checks, known only once someone checks it (trades gate, or the gate tab).
  const gcells: Cell[] = gate
    ? gate.checks.map((c) => ({ state: c.status, title: `${c.n}. ${c.name}: ${c.detail.replace(/`/g, "")} · ${c.status.toUpperCase()}` }))
    : repeat(10, { state: "unknown", title: "Not checked: the gate is checked on demand" });
  const gc = gate ? gateCounts(gate.checks) : null;
  const theGate: StageView = {
    cells: gcells,
    fact: gc ? `${gc.pass} pass · ${gc.pending} pending${gc.fail ? ` · ${gc.fail} fail` : ""}` : "Not checked yet",
    label: gc ? `Gate: ${gc.pass} of 10 checks pass` : "The gate has not been checked",
  };
  return [write, research, paper, theGate];
}

function Schematic({ s, detail, onOpen }: { s: PipelineSpec; detail?: InspectorDetail; onOpen: (tab: Tab) => void }) {
  const reached = stageReached(s);
  const gate = parseGateReport(detail?.paper?.gate);
  const views = stageViews(s, detail, gate);
  return (
    <figure className="pl-schem">
      <figcaption className="pl-cap">
        <span className="pl-cap-fig">Fig. 1</span>
        <span>The four stages, as this spec stands</span>
        <span className="pl-cap-aside">
          <StageKey />
        </span>
      </figcaption>
      <ol className="pl-hull">
        {STAGES.map((st, i) => {
          const state = stageState(i, reached);
          const v = views[i];
          return (
            <li key={st.id} className={`pl-comp ${state}`}>
              <div className="pl-comp-head">
                <span className="pl-comp-n">{String(i + 1).padStart(2, "0")}</span>
                <span className="pl-comp-name">{st.name}</span>
                <Tag kind={STATE_TAG[state]}>{stateWord(st.id, state)}</Tag>
              </div>
              <p className="pl-comp-desc">{st.text}</p>
              <Cells cells={v.cells} label={v.label} mark={i === 2 ? v.mark : undefined} names={v.names} />
              <span className="pl-comp-fact">
                {v.fact}
                {i === 3 && gate?.date ? <span className="pl-comp-when"> · {gate.date}</span> : null}
              </span>
              <button type="button" className="pl-comp-link" onClick={() => onOpen(st.id)}>
                {st.link} <span aria-hidden="true">›</span>
              </button>
            </li>
          );
        })}
      </ol>
    </figure>
  );
}

/** The legend for the stage cells: ink = done, orange = now, outline = later. */
function StageKey() {
  return (
    <span className="pl-key" aria-label="Key: ink is done, orange is the current stage, an outline is later">
      <span className="pl-key-item">
        <span className="pl-track-cell done" /> Done
      </span>
      <span className="pl-key-item">
        <span className="pl-track-cell now" /> Now
      </span>
      <span className="pl-key-item">
        <span className="pl-track-cell" /> Later
      </span>
    </span>
  );
}

/** The four stages in four small cells, for the queue rail. */
function StageTrack({ s }: { s: PipelineSpec }) {
  const reached = stageReached(s);
  return (
    <span className="pl-track" aria-hidden="true">
      {STAGES.map((st, i) => (
        <span key={st.id} className={`pl-track-cell ${stageState(i, reached)}`} />
      ))}
    </span>
  );
}

/** Paper equity as a small ink line, with a dashed line at the first session's close. */
function EquitySpark({ points }: { points: { date: string; equity: number }[] }) {
  const vals = points.map((p) => p.equity).filter((v) => Number.isFinite(v));
  if (vals.length < 2) return null;
  const w = 300;
  const h = 56;
  const pad = 4;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (vals.length - 1);
  const y = (v: number) => h - pad - ((v - min) / span) * (h - 2 * pad);
  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  return (
    <svg className="pl-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1={pad} x2={w - pad} y1={y(vals[0])} y2={y(vals[0])} className="pl-spark-base" vectorEffect="non-scaling-stroke" />
      <path d={d} className="pl-spark-line" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// ---- the overview ----------------------------------------------------------------------------

/** What the inspector loads for the selected spec: its checklist and runs, and its paper record. */
interface InspectorDetail {
  loading: boolean;
  spec?: SpecDetail;
  paper?: PaperDetail;
  error?: string;
}

/** The spec selected in the queue; kept while you visit a spec page and come back. */
let rememberedSelection: string | null = null;

/** The spec to show first: the one furthest along, then one with a paper record or a research run. */
function pickDefault(specs: PipelineSpec[]): PipelineSpec | undefined {
  let best: PipelineSpec | undefined;
  let score = -1;
  for (const s of specs) {
    const sc = stageReached(s) * 10 + (s.paper.started ? 5 : 0) + (s.latest_run ? 2 : 0) + (s.paper_target ? 1 : 0);
    if (sc > score) {
      best = s;
      score = sc;
    }
  }
  return best;
}

export default function Pipeline({ route }: { route: Route }) {
  const { navigate } = useApp();
  const ref = route.params.get("spec");
  const tab = TABS.find((t) => t === route.params.get("tab")) ?? "spec";
  // A new route object each time the top bar's ALERTS button is pressed: scroll to the feed again.
  const alertsTrigger = route.params.get("panel") === "alerts" ? route : null;
  return ref ? (
    <SpecPage
      key={ref}
      specRef={ref}
      initialTab={tab}
      onBack={(key) => {
        if (key) rememberedSelection = key;
        navigate("/pipeline");
      }}
    />
  ) : (
    <Overview alertsTrigger={alertsTrigger} onOpen={(key, t) => navigate("/pipeline", t && t !== "spec" ? { spec: key, tab: t } : { spec: key })} />
  );
}

function Overview({ onOpen, alertsTrigger }: { onOpen: (key: string, tab?: Tab) => void; alertsTrigger: Route | null }) {
  const [data, setData] = useState<PipelineOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [feed, setFeed] = useState<AlertFeed | null>(null);
  const [feedError, setFeedError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(rememberedSelection);
  const [details, setDetails] = useState<Record<string, InspectorDetail>>({});
  const [flash, setFlash] = useState(false);
  const inspectorRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setError(null);
    api.pipeline().then(setData, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  // The alert feed, refreshed every 30 seconds like the top bar's count.
  useEffect(() => {
    let alive = true;
    const pull = () =>
      api.alerts(200).then(
        (f) => {
          if (!alive) return;
          setFeed(f);
          setFeedError(null);
        },
        (e: Error) => alive && setFeedError(e.message),
      );
    void pull();
    const poll = window.setInterval(pull, 30_000);
    return () => {
      alive = false;
      window.clearInterval(poll);
    };
  }, []);

  const specs = data?.specs ?? [];
  const current = specs.find((s) => s.key === selected) ?? pickDefault(specs);
  const currentKey = current?.key ?? null;
  const currentPaper = Boolean(current?.paper.started);

  useEffect(() => {
    if (currentKey) rememberedSelection = currentKey;
  }, [currentKey]);

  // The selected spec's checklist and paper record (each loaded once).
  useEffect(() => {
    if (!currentKey || details[currentKey]) return;
    setDetails((d) => ({ ...d, [currentKey]: { loading: true } }));
    Promise.all([api.spec(currentKey), currentPaper ? api.paper(currentKey) : Promise.resolve(undefined)]).then(
      ([spec, paper]) => setDetails((d) => ({ ...d, [currentKey]: { loading: false, spec, paper } })),
      (e: Error) => setDetails((d) => ({ ...d, [currentKey]: { loading: false, error: e.message } })),
    );
  }, [currentKey, currentPaper, details]);

  // Arriving from the top bar's ALERTS button (#/pipeline?panel=alerts): bring the feed into view.
  const ready = data !== null;
  useEffect(() => {
    if (!alertsTrigger || !ready) return;
    const el = document.getElementById("pl-alerts");
    if (!el) return;
    el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    setFlash(true);
    const t = window.setTimeout(() => setFlash(false), 1800);
    // Back to #/pipeline without a new history entry, so the ALERTS button works again from here.
    window.history.replaceState(null, "", "#/pipeline");
    return () => window.clearTimeout(t);
  }, [alertsTrigger, ready]);

  const select = (key: string, focus = false) => {
    setSelected(key);
    if (focus) document.getElementById(`pl-q-${key}`)?.focus();
    // One column (phones, narrow windows): the inspector sits under the queue, so show it.
    if (!focus && window.matchMedia("(max-width: 1000px)").matches)
      inspectorRef.current?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  };

  const onQueueKey = (e: KeyboardEvent<HTMLElement>) => {
    if (!specs.length || !["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const i = Math.max(0, specs.findIndex((s) => s.key === currentKey));
    const next = e.key === "Home" ? 0 : e.key === "End" ? specs.length - 1 : Math.min(specs.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)));
    select(specs[next].key, true);
  };

  const counts = useMemo(() => {
    const frozen = specs.filter((s) => s.frozen && !s.drift).length;
    const passed = specs.filter((s) => s.latest_run?.verdict === "pass").length;
    const failed = specs.filter((s) => s.latest_run && s.latest_run.verdict !== "pass" && s.latest_run.verdict !== "ready_for_locked_test").length;
    const sealed = specs.filter((s) => s.latest_run?.verdict === "ready_for_locked_test").length;
    const unrun = specs.filter((s) => !s.latest_run).length;
    const paper = specs.filter((s) => s.paper.started).length;
    return { frozen, passed, failed, sealed, unrun, paper };
  }, [specs]);

  const c24 = feed?.counts_24h;
  const loud = c24 ? c24.crit + c24.warn : null;

  let status: string | null = null;
  if (error) status = "The pipeline could not be read: the reason is below. Retry once it is fixed.";
  else if (data) {
    const lead = [
      data.kill_switch.on ? "**HALT ON**: nothing trades." : "",
      c24 && c24.crit ? `**${c24.crit} CRIT** in the last 24 hours: read the alert feed.` : "",
    ]
      .filter(Boolean)
      .join(" ");
    if (!specs.length) status = `${lead ? `${lead} ` : ""}No specs yet. Next: \`trades spec new\` writes a draft from the template.`;
    else {
      const parts = [
        counts.frozen ? `${counts.frozen} frozen` : "none frozen",
        counts.passed ? `${counts.passed} passed research` : "none has passed research yet",
        counts.paper ? `${counts.paper} paper trading` : "",
      ].filter(Boolean);
      const next = current ? ` Next for **${current.key}**: ${nextStep(current).short}.` : "";
      status = `${lead ? `${lead} ` : ""}${plural(specs.length, "spec")}; ${parts.join(", ")}.${next}`;
    }
  }
  useStatusLine(status);

  if (error)
    return (
      <div className="stack">
        <ErrorBox error={error} />
        <div>
          <button className="btn" onClick={load}>
            Retry
          </button>
        </div>
      </div>
    );
  if (!data) return <Spinner label="Reading specs and research runs..." />;
  const problems = Object.entries(data.problems ?? {}); // [file, why it could not be read]

  return (
    <div className="pl-home">
      <header className="pl-head">
        <div className="pl-head-text">
          <h1>Pipeline</h1>
          <p>
            Every strategy passes the same four stages before real money, on one code path for backtest, paper trading and risk, so what
            was researched is what trades.
          </p>
        </div>
        <div className="pl-readouts">
          <Ro label="Specs" value={specs.length} note={problems.length ? `${problems.length} unreadable` : "in specs/"} />
          <Ro
            label={
              <span className="pl-label-help">
                Frozen <Help text="A frozen spec's rules can no longer change: research results only count for frozen specs. Jack freezes them." />
              </span>
            }
            value={counts.frozen}
            note={`of ${specs.length}; rules locked`}
          />
          <Ro
            label="Passed research"
            value={counts.passed}
            note={[counts.failed ? `${counts.failed} failed` : "", counts.sealed ? `${counts.sealed} sealed` : "", counts.unrun ? `${counts.unrun} not run` : ""]
              .filter(Boolean)
              .join(" · ") || "all 14 checks"}
          />
          <Ro label="Paper trading" value={counts.paper} note={data.paper_spec ? `account: ${data.paper_spec}` : "no account target"} />
          <Ro
            label="Alerts 24h"
            value={loud ?? "–"}
            tone={loud ? "accent" : undefined}
            note={c24 ? `${c24.crit} crit · ${c24.warn} warn · ${c24.info} info` : feedError ? "feed unavailable" : "reading..."}
          />
        </div>
      </header>

      {problems.length ? (
        <div className="callout warn">
          These spec files could not be read:
          <ul>
            {problems.map(([file, why]) => (
              <li key={file}>
                <code>{file}</code>: {why}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="pl-deck">
        <section className="card flush pl-rail" aria-label="Spec queue">
          <header className="pl-rail-head">
            <h2>Queue</h2>
            <span className="pl-rail-count">{plural(specs.length, "spec")}</span>
          </header>
          {specs.length ? (
            <ul className="pl-queue" onKeyDown={onQueueKey}>
              {specs.map((s) => {
                const r = s.latest_run;
                const on = s.key === currentKey;
                const meta = [
                  s.timeframe,
                  s.drift ? "changed after freezing" : s.frozen ? "frozen" : "draft",
                  r ? (r.verdict === "pass" ? `passed ${r.passed}/14` : r.verdict === "ready_for_locked_test" ? "locked test sealed" : `failed ${r.failed}/14`) : "no run",
                  s.paper.started ? `paper ${s.paper.sessions ?? 0} ses` : "",
                ].filter(Boolean);
                return (
                  <li key={s.key}>
                    <button
                      type="button"
                      id={`pl-q-${s.key}`}
                      className="pl-q"
                      aria-current={on ? "true" : undefined}
                      tabIndex={on ? 0 : -1}
                      onClick={() => select(s.key)}
                      onDoubleClick={() => onOpen(s.key)}
                      title={`${s.key}: ${s.name}. ${s.strategy}, ${s.timeframe} bars, ${plural(s.n_symbols, "symbol")}${s.paper_target ? "; the paper account trades this" : ""}. Stage ${stageReached(s) + 1} of 4, ${STAGES[Math.min(3, stageReached(s))].name}.`}
                    >
                      <span className="pl-q-key">{s.key}</span>
                      <StageTrack s={s} />
                      <span className="pl-q-name">{s.name}</span>
                      <span className="pl-q-meta">
                        {meta.map((m, i) => (
                          <span key={m} className={i === 2 && r && r.verdict !== "pass" && r.verdict !== "ready_for_locked_test" ? "pl-accent" : undefined}>
                            {m}
                          </span>
                        ))}
                        {s.paper_target ? <span className="pl-q-acct">acct</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="pl-rail-empty">
              <Empty>
                No specs yet. <code>trades spec new</code> writes a draft from <code>specs/TEMPLATE.md</code>.
              </Empty>
            </div>
          )}
          <footer className="pl-rail-foot">
            <Hatch />
            <div className="pl-rail-legend">
              <StageKey />
            </div>
            <div className="pl-rail-add">
              <span className="label">
                Add a spec{" "}
                <Help text="Writes a draft spec from specs/TEMPLATE.md. Claude Code can draft one with the new-strategy skill; only Jack freezes it." />
              </span>
              <code>trades spec new</code>
            </div>
            {specs.length ? <p className="pl-rail-hint">Select a spec to inspect it · ↑ ↓ to move · double-click to open</p> : null}
          </footer>
        </section>

        <div className="pl-insp-wrap" ref={inspectorRef}>
          {current ? (
            <Inspector s={current} detail={details[current.key]} paperSpec={data.paper_spec} onOpen={onOpen} />
          ) : (
            <Panel marks className="pl-insp">
              <Empty>
                Nothing to inspect yet. Write a spec with <code>trades spec new</code>, then rehearse it with{" "}
                <code>trades research STRAT-xxx --provider synthetic</code>.
              </Empty>
            </Panel>
          )}
        </div>
      </div>

      <div className="pl-strip">
        <Panel title="Kill switch" aside="Checked before every paper order" className="pl-ks-panel">
          <KillSwitchControl initial={data.kill_switch} onChange={(k) => setData((d) => (d ? { ...d, kill_switch: k } : d))} />
          <HaltRecord alerts={feed?.alerts ?? null} />
        </Panel>
        <AlertFeedPanel feed={feed} error={feedError} flash={flash} selected={currentKey} specKeys={specs.map((s) => s.key)} onOpen={onOpen} />
      </div>
    </div>
  );
}

function Inspector({
  s,
  detail,
  paperSpec,
  onOpen,
}: {
  s: PipelineSpec;
  detail?: InspectorDetail;
  paperSpec: string | null;
  onOpen: (key: string, tab?: Tab) => void;
}) {
  const step = nextStep(s);
  const r = s.latest_run;
  const paper = detail?.paper;
  const rows: [ReactNode, ReactNode][] = [
    ["Strategy", <code key="s">{s.strategy}</code>],
    ["Timeframe", timeframeLabel(s.timeframe)],
    ["Universe", universeText(s)],
    ["Data", s.provider],
    ["Status", s.status],
    ["Frozen", s.drift ? <span className="pl-accent">changed after freezing</span> : (s.frozen ?? "not yet")],
    ["Latest run", r ? `${fmtUtc(r.created)} · ${r.provider ?? "–"}` : "none yet"],
    [
      "Verdict",
      r ? (
        <>
          <span className={r.verdict === "pass" ? "pos" : r.verdict === "ready_for_locked_test" ? undefined : "pl-accent"}>{verdictWord(r)}</span> ·{" "}
          {r.passed} pass, {r.failed} fail, {r.pending} pending
        </>
      ) : (
        "–"
      ),
    ],
    ["Paper", s.paper.started ? `${plural(s.paper.sessions ?? 0, "session")} · ${s.paper.first ?? "–"} to ${s.paper.last ?? "–"}` : "not started"],
    ["Paper equity", s.paper.started ? fmtMoney(s.paper.equity ?? null, 2) : "–"],
    ["Orders · fills", s.paper.started ? `${s.paper.orders ?? 0} · ${s.paper.fills ?? 0}` : "–"],
    ["Account", paperSpec ? (s.paper_target ? "the paper account trades this spec" : `the paper account trades ${paperSpec}`) : "no spec in config/paper.yaml"],
  ];
  return (
    <Panel marks className="pl-insp">
      <header className="pl-insp-head">
        <div className="pl-insp-title">
          <div className="pl-kicker">
            Inspector <span aria-hidden="true">·</span> {s.key} <span aria-hidden="true">·</span> version {s.version}
          </div>
          <h2 className="pl-insp-name">{s.name}</h2>
          <SpecTags frozen={s.frozen} drift={s.drift} run={r} target={s.paper_target} />
        </div>
        <button type="button" className="btn primary" onClick={() => onOpen(s.key)}>
          Open spec <span aria-hidden="true">→</span>
        </button>
      </header>
      <Schematic s={s} detail={detail} onOpen={(tab) => onOpen(s.key, tab)} />
      {detail?.error ? <p className="pl-note">Could not load this spec&apos;s checklist and paper record: {detail.error}</p> : null}
      <div className="pl-insp-body">
        <div className="pl-insp-kv">
          <div className="label pl-sub">Details</div>
          <KeyValues rows={rows} />
        </div>
        <div className="pl-insp-side">
          <NextStepBlock step={step} />
          {s.paper.started ? (
            <div className="pl-mini">
              <div className="pl-cap">
                <span className="pl-cap-fig">Fig. 2</span>
                <span>Paper equity by session</span>
                <span className="pl-cap-aside">{fmtMoney(s.paper.equity ?? null, 0)}</span>
              </div>
              {paper && paper.equity.length > 1 ? (
                <EquitySpark points={paper.equity} />
              ) : detail?.loading ? (
                <Spinner label="Reading the paper record..." />
              ) : (
                <p className="pl-note">Fewer than two sessions on record.</p>
              )}
              <div className="pl-mini-axis">
                <span>{s.paper.first ?? ""}</span>
                <span>dashed: first session</span>
                <span>{s.paper.last ?? ""}</span>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

// ---- the alert feed --------------------------------------------------------------------------

/** The kill switch in the paper record: the newest halt (a run met the switch) and the newest drill. */
function HaltRecord({ alerts }: { alerts: AlertEvent[] | null }) {
  if (!alerts) return null;
  const line = (e: AlertEvent | undefined) => {
    if (!e) return <span className="muted">none in the recent paper events</span>;
    const t = etParts(e.time);
    return (
      <>
        <span className="pl-ks-when" title={fmtUtc(e.time)}>
          {t.day} {t.time} · {e.spec}
        </span>
        <span className="pl-ks-msg">{e.message}</span>
      </>
    );
  };
  return (
    <div className="pl-ks-record">
      <div className="label pl-sub pl-label-help">
        On record{" "}
        <Help text="From the newest paper events. A halt means a paper run met the switch. Drills are the gate's four kill switch rehearsals, which only you run (see the gate)." />
      </div>
      <KeyValues
        rows={[
          ["Last halt", line(alerts.find((a) => a.kind === "halt"))],
          ["Last drill", line(alerts.find((a) => a.kind === "drill"))],
        ]}
      />
    </div>
  );
}

type Severity = "all" | "crit" | "warn" | "info";

/** Events per New York day, the newest six weeks of the feed: grey for INFO, orange for CRIT and WARN. */
function EventStrip({ alerts }: { alerts: AlertEvent[] }) {
  const byDay = new Map<string, { loud: number; info: number }>();
  for (const a of alerts) {
    const d = etDate(a.time);
    if (!d) continue;
    const b = byDay.get(d) ?? { loud: 0, info: 0 };
    if (a.severity === "info") b.info += 1;
    else b.loud += 1;
    byDay.set(d, b);
  }
  const keys = [...byDay.keys()].sort();
  if (!keys.length) return null;
  const last = dayNum(keys[keys.length - 1]);
  const first = Math.max(dayNum(keys[0]), last - 41 * DAY_MS);
  const days: string[] = [];
  for (let t = first; t <= last; t += DAY_MS) days.push(new Date(t).toISOString().slice(0, 10));
  const max = Math.max(1, ...days.map((d) => (byDay.get(d)?.loud ?? 0) + (byDay.get(d)?.info ?? 0)));
  const bw = 10;
  const gap = 3;
  const h = 34;
  const w = days.length * (bw + gap) - gap;
  const ago = Math.round((Date.now() - (last + DAY_MS)) / DAY_MS);
  return (
    <figure className="pl-evstrip">
      <figcaption className="pl-cap">
        <span className="pl-cap-fig">Events per day</span>
        <span className="pl-cap-aside">
          max {max} a day{ago > 1 ? ` · last event ${ago} days ago` : ""}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label={`Events per day from ${days[0]} to ${days[days.length - 1]}, at most ${max} a day`}>
        <line x1={0} x2={w} y1={h - 0.5} y2={h - 0.5} className="pl-evstrip-base" vectorEffect="non-scaling-stroke" />
        {days.map((d, i) => {
          const b = byDay.get(d);
          if (!b) return null;
          const hi = ((b.info + b.loud) / max) * (h - 2);
          const hl = (b.loud / max) * (h - 2);
          const x = i * (bw + gap);
          return (
            <g key={d}>
              <title>{`${d}: ${b.loud ? `${b.loud} crit or warn, ` : ""}${b.info} info`}</title>
              <rect x={x} y={h - hi} width={bw} height={hi - hl} className="pl-evstrip-info" />
              {b.loud ? <rect x={x} y={h - hl} width={bw} height={hl} className="pl-evstrip-loud" /> : null}
            </g>
          );
        })}
      </svg>
      <div className="pl-evstrip-axis">
        <span>{days[0]}</span>
        <span>New York days</span>
        <span>{days[days.length - 1]}</span>
      </div>
    </figure>
  );
}

function FeedRow({ e, onOpen, known }: { e: AlertEvent; onOpen?: (key: string, tab?: Tab) => void; known: boolean }) {
  const t = etParts(e.time);
  return (
    <li className={`pl-ev ${e.severity}`}>
      <span className="pl-ev-time" title={fmtUtc(e.time)}>
        <span>{t.day}</span>
        <span>{t.time}</span>
      </span>
      <span className="pl-ev-body">
        <span className="pl-ev-msg">{e.message}</span>
        <span className="pl-ev-meta">
          {known && onOpen ? (
            <button type="button" className="linklike" onClick={() => onOpen(e.spec, "paper")} title={`Open ${e.spec}'s paper record`}>
              {e.spec}
            </button>
          ) : (
            e.spec
          )}
          <span aria-hidden="true"> · </span>
          {e.kind.replace(/_/g, " ")}
          {e.symbol ? <span> · {e.symbol}</span> : null}
        </span>
      </span>
      <Tag kind={e.severity}>{e.severity}</Tag>
    </li>
  );
}

function AlertFeedPanel({
  feed,
  error,
  flash,
  selected,
  specKeys,
  onOpen,
}: {
  feed: AlertFeed | null;
  error: string | null;
  flash: boolean;
  selected: string | null;
  specKeys: string[];
  onOpen: (key: string, tab?: Tab) => void;
}) {
  const [sev, setSev] = useState<Severity>("all");
  const [onlySelected, setOnlySelected] = useState(false);
  const alerts = feed?.alerts ?? [];
  const scoped = onlySelected && selected ? alerts.filter((a) => a.spec === selected) : alerts;
  const n = {
    all: scoped.length,
    crit: scoped.filter((a) => a.severity === "crit").length,
    warn: scoped.filter((a) => a.severity === "warn").length,
    info: scoped.filter((a) => a.severity === "info").length,
  };
  const shown = sev === "all" ? scoped : scoped.filter((a) => a.severity === sev);
  const c24 = feed?.counts_24h;
  const known = new Set(specKeys);
  return (
    <Panel
      id="pl-alerts"
      title="Alert feed"
      aside={
        c24 ? (
          <span className="pl-feed-aside">
            <span className="status live">
              <span className="dot" />
            </span>
            24h · <b className={c24.crit ? "pl-accent" : undefined}>{c24.crit} crit</b> · {c24.warn} warn · {c24.info} info
          </span>
        ) : undefined
      }
      className={`pl-feed-panel${flash ? " pl-flash" : ""}`}
    >
      <div className="pl-feed-controls">
        <div className="segmented" role="group" aria-label="Show alerts by severity">
          {(["all", "crit", "warn", "info"] as Severity[]).map((k) => (
            <button key={k} type="button" aria-pressed={sev === k} onClick={() => setSev(k)}>
              {k} <span className="pl-seg-n">{n[k]}</span>
            </button>
          ))}
        </div>
        {selected ? (
          <button type="button" className="chip toggle" aria-pressed={onlySelected} onClick={() => setOnlySelected((v) => !v)}>
            Only {selected}
          </button>
        ) : null}
        <span className="pl-feed-tz">
          Times ET <Help text="New York time, the market's clock. Hover a time for the record's UTC timestamp." />
        </span>
      </div>
      {error ? <ErrorBox error={`Could not read the alert feed: ${error}`} /> : null}
      {!feed && !error ? <Spinner label="Reading the paper records..." /> : null}
      {feed ? <EventStrip alerts={scoped} /> : null}
      {feed && !shown.length ? (
        <Empty>
          {alerts.length
            ? "No alerts match this filter."
            : "No alerts yet. Paper runners write events to paper/<spec>/events.jsonl; errors, rejections, risk limits and the kill switch appear here, graded CRIT, WARN or INFO."}
        </Empty>
      ) : null}
      {shown.length ? (
        <ol className="pl-feed" aria-label="Alerts, newest first">
          {shown.map((e, i) => (
            <FeedRow key={`${e.time}-${e.spec}-${i}`} e={e} onOpen={onOpen} known={known.has(e.spec)} />
          ))}
        </ol>
      ) : null}
    </Panel>
  );
}

// ---- a spec's page ---------------------------------------------------------------------------

interface GateRun {
  result: GateResult;
  at: Date;
}

function SpecPage({ specRef, initialTab, onBack }: { specRef: string; initialTab: Tab; onBack: (key?: string) => void }) {
  const [detail, setDetail] = useState<SpecDetail | null>(null);
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [overview, setOverview] = useState<PipelineSpec | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [gateRun, setGateRun] = useState<GateRun | null>(null);

  useEffect(() => setTab(initialTab), [initialTab]);

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

  const pick = (t: Tab) => {
    setTab(t);
    // Keep the address in step with the tab (no new history entry), so a reload or a shared link opens it.
    window.history.replaceState(null, "", `#/pipeline?${new URLSearchParams(t === "spec" ? { spec: specRef } : { spec: specRef, tab: t }).toString()}`);
  };

  const recorded = useMemo(() => parseGateReport(paper?.gate), [paper]);
  const gateNow: RecordedGate | null = gateRun ? { date: null, checks: gateRun.result.checks, verdict: gateRun.result.verdict } : null;
  const gate = gateNow ?? recorded;

  // The status line: what is true about this spec, then its next step.
  let status: string | null = null;
  if (error) status = `Could not load ${specRef}.`;
  else if (detail) {
    const key = detail.spec.key;
    const r = detail.runs[0] ?? null;
    let fact: string;
    if (overview?.drift) fact = `**${key}** changed after it was frozen, so its results no longer count.`;
    else if (!r) fact = `**${key}** is ${detail.spec.frozen ? `frozen (${String(detail.spec.frozen)})` : "a draft"} with no research run yet.`;
    else if (r.verdict === "pass") fact = `**${key}** passed all 14 checks.`;
    else if (r.verdict === "ready_for_locked_test") fact = `**${key}** passed ${r.passed} of 14 checks; only the locked test is left.`;
    else fact = `**${key}** failed ${r.failed} of 14 checks${r.provider === "synthetic" ? " in a rehearsal on synthetic data" : ""}.`;
    const extra = [
      paper && paper.equity.length ? `${plural(paper.equity.length, "paper session")} on record.` : "",
      gate ? `Gate: ${gate.verdict === "pass" ? "passed" : gate.verdict === "fail" ? "failed" : "not yet"} (${gateCounts(gate.checks).pass} of ${gate.checks.length} pass).` : "",
    ]
      .filter(Boolean)
      .join(" ");
    status = `${fact}${extra ? ` ${extra}` : ""}${overview ? ` Next: ${nextStep(overview).short}.` : ""}`;
  }
  useStatusLine(status);

  if (error)
    return (
      <div className="stack">
        <div>
          <button className="btn ghost small" onClick={() => onBack()}>
            ← All specs
          </button>
        </div>
        <ErrorBox error={error} />
        <div>
          <button className="btn" onClick={load}>
            Retry
          </button>
        </div>
      </div>
    );
  if (!detail || !paper) return <Spinner label="Loading the spec..." />;

  const spec = detail.spec;
  const r = detail.runs[0] ?? null;
  const reached = overview ? stageReached(overview) : 0;
  const views = overview ? stageViews(overview, { loading: false, spec: detail, paper }, gate) : null;
  const weeks = paperWeeks(paper.equity[0]?.date, paper.equity[paper.equity.length - 1]?.date);
  const need = overview ? weeksNeeded(overview, spec, gate) : null;
  const gc = gate ? gateCounts(gate.checks) : null;
  const stepFacts: string[] = [
    views ? views[0].fact : spec.frozen ? `Frozen ${String(spec.frozen)}` : "Draft · not frozen",
    r ? (r.verdict === "pass" ? "Latest run passed" : r.verdict === "ready_for_locked_test" ? "Locked test sealed" : "Latest run failed") : "No run yet",
    paper.equity.length ? `Since ${paper.equity[0].date}` : "Not started",
    gate ? (gate.verdict === "pass" ? "Passed" : gate.verdict === "fail" ? "Failed" : "Not yet") + (gateNow ? " · just now" : gate.date ? ` · ${gate.date}` : "") : "Not checked yet",
  ];

  return (
    <div className="pl-page">
      <div className="pl-back">
        <button className="btn ghost small" onClick={() => onBack(spec.key)}>
          ← All specs
        </button>
      </div>
      <Panel marks className="pl-dhead">
        <div className="pl-dhead-top">
          <div className="pl-dhead-title">
            <div className="pl-kicker">
              {spec.key} <span aria-hidden="true">·</span> version {String(spec.version ?? "–")}
              {overview ? (
                <>
                  {" "}
                  <span aria-hidden="true">·</span> {overview.strategy} <span aria-hidden="true">·</span> {overview.timeframe}{" "}
                  <span aria-hidden="true">·</span> {plural(overview.n_symbols, "symbol")}
                </>
              ) : null}
            </div>
            <h1>{spec.name}</h1>
            <SpecTags
              frozen={spec.frozen ? String(spec.frozen) : null}
              drift={overview?.drift ?? null}
              run={r}
              target={Boolean(overview?.paper_target)}
            />
          </div>
          <div className="pl-readouts pl-readouts-4">
            <Ro
              label="Checks passed"
              value={r ? r.passed : "–"}
              unit={r ? "/14" : undefined}
              tone={r && r.verdict === "pass" ? "pos" : undefined}
              note={r ? `${r.failed} fail · ${r.pending} pending` : "no research run"}
            />
            <Ro
              label={
                <span className="pl-label-help">
                  Variants tested{" "}
                  <Help text="Every parameter variant tried for this spec version is logged in runs/<id>/variants.json. Each one raises the bar: the deflated Sharpe ratio asks how good the best of that many skill-less variants would look by luck." />
                </span>
              }
              value={detail.variants_tested}
              note="each deflates the Sharpe"
            />
            <Ro label="Paper sessions" value={paper.equity.length} note={paper.equity.length ? `${weeks ?? 0} of ${need ?? "8 to 12"} wk` : "not started"} />
            {gate && gc ? (
              <Ro
                label="Gate"
                value={gc.pass}
                unit={`/${gate.checks.length}`}
                tone={gate.verdict === "fail" ? "accent" : gate.verdict === "pass" ? "pos" : undefined}
                note={gateNow ? "checked just now" : gate.date ? `recorded ${gate.date}` : "recorded"}
              />
            ) : null}
          </div>
        </div>
        {overview ? <NextStepBlock step={nextStep(overview)} wide /> : null}
      </Panel>

      <div className="pl-steps" role="tablist" aria-label="The four stages">
        {STAGES.map((st, i) => {
          const state = overview ? stageState(i, reached) : "later";
          const selectedTab = tab === st.id;
          return (
            <button
              key={st.id}
              type="button"
              role="tab"
              id={`pl-tab-${st.id}`}
              aria-selected={selectedTab}
              aria-controls="pl-tabpanel"
              tabIndex={selectedTab ? 0 : -1}
              className={`pl-step ${state}`}
              onClick={() => pick(st.id)}
              onKeyDown={(e) => {
                const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!dir) return;
                e.preventDefault();
                const next = STAGES[(i + dir + STAGES.length) % STAGES.length].id;
                pick(next);
                document.getElementById(`pl-tab-${next}`)?.focus();
              }}
            >
              <span className="pl-step-top">
                <span>
                  {String(i + 1).padStart(2, "0")}
                  <span className="pl-step-stage"> · {st.name}</span>
                </span>
                <Tag kind={STATE_TAG[state]}>{stateWord(st.id, state)}</Tag>
              </span>
              <span className="pl-step-name">{st.id === "research" ? `${st.tab} (${detail.runs.length})` : st.tab}</span>
              {views ? <Cells cells={views[i].cells} label={views[i].label} mark={i === 2 ? views[i].mark : undefined} /> : null}
              <span className="pl-step-fact">{stepFacts[i]}</span>
            </button>
          );
        })}
      </div>

      <div id="pl-tabpanel" role="tabpanel" aria-labelledby={`pl-tab-${tab}`} className="pl-tabpanel">
        {tab === "spec" ? <SpecTab detail={detail} overview={overview} /> : null}
        {tab === "research" ? <ResearchTab detail={detail} /> : null}
        {tab === "paper" ? <PaperTab paper={paper} specId={String(spec.id)} /> : null}
        {tab === "gate" ? <GateTab specRef={specRef} specKey={spec.key} recorded={recorded} run={gateRun} onRun={setGateRun} /> : null}
      </div>
    </div>
  );
}

// ---- tab: the spec ---------------------------------------------------------------------------

const PERIOD_ORDER = ["train", "validation", "test"];
const periodName = (name: string) => (name === "test" ? "Locked test" : name[0].toUpperCase() + name.slice(1));

/** The spec's data periods to scale: train, validation, and the locked test (sealed until opened). */
function PeriodsFigure({ periods, opened }: { periods: Record<string, [string, string]>; opened: boolean }) {
  const spans = Object.entries(periods)
    .map(([name, [a, b]]) => ({ name, a, b, t0: dayNum(a), t1: dayNum(b) }))
    .filter((p) => Number.isFinite(p.t0) && Number.isFinite(p.t1) && p.t1 >= p.t0)
    .sort((x, y) => (PERIOD_ORDER.indexOf(x.name) + 1 || 99) - (PERIOD_ORDER.indexOf(y.name) + 1 || 99) || x.t0 - y.t0);
  if (!spans.length) return null;
  const lo = Math.min(...spans.map((p) => p.t0));
  const hi = Math.max(...spans.map((p) => p.t1));
  const years = (p: { t0: number; t1: number }) => ((p.t1 - p.t0) / DAY_MS / 365.25).toFixed(1);
  const note = (name: string) => (name === "test" ? (opened ? "opened: its result is final" : "sealed until Jack opens it, once") : name === "train" ? "rules are fitted here" : name === "validation" ? "checked out of sample" : "");
  return (
    <Figure fig="Fig. 1" title="Data periods, to scale" aside={`${new Date(lo).toISOString().slice(0, 10)} to ${new Date(hi).toISOString().slice(0, 10)}`}>
      <div className="pl-periods" role="img" aria-label={spans.map((p) => `${periodName(p.name)} ${p.a} to ${p.b}`).join("; ")}>
        {spans.map((p) => (
          <div
            key={p.name}
            className={`pl-period ${p.name}${p.name === "test" && opened ? " opened" : ""}`}
            style={{ flex: `${Math.max(1, (p.t1 - p.t0) / DAY_MS)} 1 0` }}
            title={`${periodName(p.name)}: ${p.a} to ${p.b}`}
          >
            <span>{periodName(p.name)}</span>
          </div>
        ))}
      </div>
      <dl className="pl-periods-key">
        {spans.map((p) => (
          <div key={p.name} className="pl-period-row">
            <dt>
              <span className={`pl-period-swatch ${p.name}${p.name === "test" && opened ? " opened" : ""}`} aria-hidden="true" />
              {periodName(p.name)}
            </dt>
            <dd>
              <span className="num">
                {p.a} to {p.b}
              </span>
              <span className="pl-period-yrs">{years(p)} y</span>
              <span className="pl-period-note">{note(p.name)}</span>
            </dd>
          </div>
        ))}
      </dl>
    </Figure>
  );
}

function SpecTab({ detail, overview }: { detail: SpecDetail; overview: PipelineSpec | null }) {
  const s = detail.spec as Record<string, unknown> & { periods: Record<string, [string, string]> };
  const strategy = (s.strategy ?? {}) as { id?: string; params?: Record<string, unknown>; sizing?: Record<string, unknown> };
  const universe = (s.universe ?? {}) as { symbols?: string[]; symbols_file?: string; survivorship_free?: boolean };
  const params = strategy.params ?? {};
  const grid = (s.parameters ?? {}) as Record<string, unknown>;
  const keys = [...new Set([...Object.keys(params), ...Object.keys(grid)])];
  const symbols = universe.symbols ?? [];
  const opened = detail.runs.some((r) => r.locked_test_opened);
  const file = typeof s.path === "string" ? `specs/${s.path.split(/[\\/]/).pop()}` : null;
  const rows: [ReactNode, ReactNode][] = [
    ["Strategy", <code key="id">{strategy.id ?? "–"}</code>],
    ["Sizing", show(strategy.sizing)],
    [
      "Universe",
      <>
        {symbols.length ? (
          <>
            {symbols.slice(0, 20).join(", ")}
            {symbols.length > 20 ? " ..." : ""}
          </>
        ) : universe.symbols_file ? (
          <>
            <code>{universe.symbols_file}</code>
            {overview ? ` (${plural(overview.n_symbols, "symbol")})` : ""}
          </>
        ) : (
          "–"
        )}{" "}
        ({universe.survivorship_free ? "survivorship-free" : "not survivorship-free"})
      </>,
    ],
    ["Data", show(s.data)],
    ["Costs", `${show(s.costs)} (on top of config/base.yaml)`],
    ["Expected", show(s.expected, "range")],
    ["Retire it when", show(s.invalidation)],
  ];
  if (typeof s.one_trade_per_day === "boolean") rows.push(["One trade a day", s.one_trade_per_day ? "yes (the gate asks for 12 weeks of paper)" : "no"]);
  return (
    <div className="stack">
      <div className="pl-two">
        <Panel title="What the code runs" aside={file ?? "front matter"}>
          <KeyValues rows={rows} />
        </Panel>
        <div className="stack">
          <PeriodsFigure periods={s.periods ?? {}} opened={opened} />
          <Panel
            title={
              <span className="pl-label-help">
                Parameters{" "}
                <Help text="The values the strategy runs with, and the variants research may try (each tested variant is logged and raises the bar for the deflated Sharpe ratio)." />
              </span>
            }
            aside={`${plural(keys.length, "parameter")} · ${Object.keys(grid).length} may vary`}
          >
            {keys.length ? (
              <div className="table-wrap">
                <table className="data pl-params">
                  <thead>
                    <tr>
                      <th>Parameter</th>
                      <th>Value</th>
                      <th>Variants allowed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((k) => (
                      <tr key={k}>
                        <td className="mono">{k}</td>
                        <td className="mono">{k in params ? show(params[k]) : "–"}</td>
                        <td className="mono">{k in grid ? show(grid[k]) : <span className="muted">fixed</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="pl-note">This strategy has no parameters.</p>
            )}
          </Panel>
        </div>
      </div>
      <Panel title="The write-up" aside={file ?? undefined}>
        <Markdown text={detail.body} />
      </Panel>
    </div>
  );
}

// ---- tab: research ---------------------------------------------------------------------------

const RESULT_TAG: Record<string, TagKind> = { pass: "ok", fail: "warn", pending: "info", "n/a": "info" };

function ResearchTab({ detail }: { detail: SpecDetail }) {
  const id = String(detail.spec.id);
  if (!detail.runs.length)
    return (
      <Panel title="Research">
        <Empty>
          <p>No research run yet. The first rehearses the pipeline on synthetic data; the second runs it on the spec&apos;s data.</p>
          <div className="pl-cmds">
            <CommandLine command={`trades research ${id} --provider synthetic`} />
            <CommandLine command={`trades research ${id}`} />
          </div>
        </Empty>
      </Panel>
    );
  const latest = detail.runs[0];
  const list = detail.checklist ?? [];
  const n = {
    pass: list.filter((c) => c.status === "pass").length,
    fail: list.filter((c) => c.status === "fail").length,
    pending: list.filter((c) => c.status === "pending").length,
    na: list.filter((c) => c.status === "n/a").length,
  };
  const v = verdictTag(latest);
  return (
    <div className="stack">
      <Panel
        title={
          <>
            <span className="pl-fig">Fig. 2</span>The acceptance checklist
          </>
        }
        aside={`${latest.provider ?? "–"} data · ${fmtUtc(latest.created).slice(0, 10)}`}
      >
        <div className="pl-checkhead">
          <Cells
            cells={list.map((c) => ({ state: c.status === "n/a" ? "na" : c.status, title: `${c.n}. ${c.name}: ${c.status.toUpperCase()}` }))}
            label={`${n.pass} of ${list.length} checks passed`}
          />
          <div className="pl-checkcounts">
            <span>
              <b>{n.pass}</b> pass
            </span>
            <span>
              <b className={n.fail ? "pl-accent" : undefined}>{n.fail}</b> fail
            </span>
            <span>
              <b>{n.pending}</b> pending
            </span>
            {n.na ? (
              <span>
                <b>{n.na}</b> n/a
              </span>
            ) : null}
            <Tag kind={v.kind}>{v.text}</Tag>
          </div>
        </div>
        <p className="pl-note">
          Latest run <code>{latest.id}</code>, {fmtUtc(latest.created)}, on {latest.provider ?? "–"} data.{" "}
          {plural(detail.variants_tested, "variant")} tested so far (each one deflates the Sharpe ratio). The locked test is opened once, by
          Jack, after everything else passes.
        </p>
        <div className="table-wrap">
          <table className="data pl-checks">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Check</th>
                <th className="pl-hide-sm">Value</th>
                <th className="pl-hide-sm">Needs</th>
                <th className="pl-c-res">Result</th>
              </tr>
            </thead>
            <tbody>
              {list.map((c) => (
                <tr key={c.n} className={c.status === "fail" ? "pl-row-fail" : undefined}>
                  <td className="num pl-c-n">{c.n}</td>
                  <td className="pl-c-name">
                    <span className="pl-c-title">{c.name}</span>
                    {c.detail ? (
                      <span className="pl-c-detail">
                        <InlineText text={c.detail} />
                      </span>
                    ) : null}
                    <span className="pl-c-mobile">
                      {c.value} <span className="muted">· needs {c.threshold}</span>
                    </span>
                  </td>
                  <td className="pl-c-val pl-hide-sm">{c.value}</td>
                  <td className="pl-c-need pl-hide-sm">{c.threshold}</td>
                  <td className="pl-c-res">
                    <Tag kind={RESULT_TAG[c.status] ?? "info"}>{c.status}</Tag>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Runs" aside={`${detail.runs.length} · newest first`}>
        <div className="table-wrap">
          <table className="data pl-runs">
            <thead>
              <tr>
                <th>Run</th>
                <th>Created</th>
                <th>Data</th>
                <th>Verdict</th>
                <th className="num">Passed</th>
                <th className="num">Failed</th>
                <th className="num">Pending</th>
                <th>Locked test</th>
              </tr>
            </thead>
            <tbody>
              {detail.runs.map((r) => {
                const t = verdictTag(r);
                return (
                  <tr key={r.id}>
                    <td>
                      <code>{r.id}</code>
                    </td>
                    <td className="mono nowrap">{fmtUtc(r.created)}</td>
                    <td className="mono">{r.provider ?? "–"}</td>
                    <td>
                      <Tag kind={t.kind}>{t.text}</Tag>
                    </td>
                    <td className="num">{r.passed}</td>
                    <td className="num">{r.failed}</td>
                    <td className="num">{r.pending}</td>
                    <td className="mono">{r.locked_test_opened ? "opened" : "sealed"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
      {detail.report ? (
        <Panel title="The report" aside="report.md">
          <Markdown text={detail.report} />
        </Panel>
      ) : null}
    </div>
  );
}

// ---- tab: paper trading ----------------------------------------------------------------------

function maxDrawdown(values: number[]): number {
  let peak = -Infinity;
  let worst = 0;
  for (const v of values) {
    peak = Math.max(peak, v);
    if (peak > 0) worst = Math.max(worst, 1 - v / peak);
  }
  return worst;
}

const num = (x: string | undefined) => {
  const v = Number(x);
  return x !== undefined && x !== "" && Number.isFinite(v) ? v : null;
};

function PaperTab({ paper, specId }: { paper: PaperDetail; specId: string }) {
  const colors = useChartColors();
  const lines = useMemo(
    () => [
      {
        id: "equity",
        label: "Paper equity",
        data: {
          t: paper.equity.map((e) => Math.floor(new Date(`${e.date}T00:00:00Z`).getTime() / 1000)),
          v: paper.equity.map((e) => e.equity),
        },
        color: colors.text,
      },
    ],
    [paper.equity, colors.text],
  );
  if (!paper.equity.length && !paper.orders.length)
    return (
      <Panel title="Paper trading">
        <Empty>
          Not paper traded yet. A spec trades on paper after its research run passes the whole checklist; the records then appear in{" "}
          <code>paper/{paper.key}/</code>.
        </Empty>
      </Panel>
    );
  const eq = paper.equity;
  const firstEq = eq[0]?.equity ?? null;
  const lastEq = eq[eq.length - 1]?.equity ?? null;
  const change = firstEq && lastEq ? lastEq / firstEq - 1 : null;
  const dd = maxDrawdown(eq.map((e) => e.equity));
  const weeks = paperWeeks(eq[0]?.date, eq[eq.length - 1]?.date);
  const events = [...paper.events].reverse();
  const orders = [...paper.orders].reverse();
  return (
    <div className="stack">
      <Panel title="Paper record" aside={`paper/${paper.key}/`}>
        <div className="pl-readouts pl-readouts-6">
          <Ro label="Equity" value={fmtMoney(lastEq, 0)} note={eq.length ? `close of ${eq[eq.length - 1].date}` : "no sessions"} />
          <Ro
            label="Change"
            value={fmtPct(change, 2)}
            tone={change === null || Math.abs(change) < 0.00005 ? undefined : change > 0 ? "pos" : "neg"}
            note="since the first session's close"
          />
          <Ro label="Max drawdown" value={dd ? fmtPct(-dd, 2) : "0.00%"} tone={dd ? "neg" : undefined} note="peak to trough, by session" />
          <Ro label="Sessions" value={eq.length} note={weeks !== null ? `${weeks} weeks` : "–"} />
          <Ro label="Decisions" value={fmtInt(paper.decisions)} note="bars the runner decided on" />
          <Ro label="Orders" value={paper.orders.length} note={`${plural(paper.fills.length, "fill")}; the last 50 of each`} />
        </div>
      </Panel>
      <div className="pl-two wide-left">
        <Figure
          fig="Fig. 3"
          title="Paper equity by session"
          aside={eq.length ? `${plural(eq.length, "session")} · ${eq[0].date} to ${eq[eq.length - 1].date} · USD` : undefined}
        >
          {eq.length > 1 ? (
            <LineChart lines={lines} height={260} format={(v) => fmtMoney(v)} label="Paper equity by session" />
          ) : (
            <Empty>One session on record so far: the chart starts with the second.</Empty>
          )}
        </Figure>
        <Panel title="Recent events" aside={`${events.length} · newest first · ET`}>
          {events.length ? (
            <ol className="pl-feed pl-feed-tall">
              {events.map((e, i) => (
                <FeedRow key={`${e.time}-${i}`} e={{ ...e, spec: paper.key, severity: severityOf(e.kind) }} known={false} />
              ))}
            </ol>
          ) : (
            <Empty>No events yet: the paper runner writes them to events.jsonl as it trades.</Empty>
          )}
        </Panel>
      </div>
      <div className="pl-two">
        {paper.tracking ? (
          <Panel title="Tracking: paper against its replay" aside="TRACKING.md">
            <Markdown text={paper.tracking} />
          </Panel>
        ) : (
          <Panel title="Tracking: paper against its replay">
            <Empty>
              <p>
                No tracking report yet. This replays the paper period through the backtest engine and compares signals, fills and expectancy:
              </p>
              <CommandLine command={`trades track ${paper.key.replace(/-v\d+$/, "")}`} />
            </Empty>
          </Panel>
        )}
        {paper.latest_report ? (
          <Panel title="Latest daily report" aside="reports/">
            <Markdown text={paper.latest_report} />
          </Panel>
        ) : (
          <Panel title="Latest daily report">
            <Empty>No daily report yet: the paper runner writes one after each session.</Empty>
          </Panel>
        )}
      </div>
      {orders.length ? (
        <Panel title="Recent orders" aside={`${orders.length} · newest first · sent, ET`}>
          <div className="table-wrap pl-orders">
            <table className="data">
              <thead>
                <tr>
                  <th>Sent</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th>Type</th>
                  <th>Purpose</th>
                  <th>Status</th>
                  <th className="num">Fill</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o, i) => {
                  const t = o.run_time_utc ? etParts(o.run_time_utc) : null;
                  const fill = num(o.fill_price);
                  return (
                    <tr key={o.client_order_id || i}>
                      <td className="mono nowrap" title={o.run_time_utc ? fmtUtc(o.run_time_utc) : undefined}>
                        {t ? `${t.day} ${t.time}` : "–"}
                      </td>
                      <td className="mono">{o.symbol ?? "–"}</td>
                      <td className="mono">{o.side ?? "–"}</td>
                      <td className="num">{o.qty ?? "–"}</td>
                      <td className="mono">
                        {o.type ?? "–"}
                        {o.time_in_force ? <span className="muted"> ({o.time_in_force})</span> : null}
                      </td>
                      <td className="mono">{o.purpose ?? "–"}</td>
                      <td className="mono">{o.status ?? "–"}</td>
                      <td className="num">
                        {fill !== null ? `${num(o.filled_qty) ?? o.filled_qty} @ ${fill.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="pl-note">
            Orders go to Alpaca&apos;s paper (practice) account only, sent by <code>trades paper --spec {specId} --submit</code>, which only you run.
          </p>
        </Panel>
      ) : null}
    </div>
  );
}

// ---- tab: the gate ---------------------------------------------------------------------------

const VERDICT_TEXT: Record<GateResult["verdict"], string> = {
  pass: "Passed: the evidence supports building a live path. That is Phase 4, and you build it yourself, not Claude.",
  not_yet: "Not yet: keep paper trading and finish the pending items.",
  fail: "Failed. Most strategies fail here: that is the system working.",
};
const GATE_TAG: Record<GateCheck["status"], TagKind> = { pass: "ok", fail: "warn", pending: "info" };

function trackingRows(t: Record<string, unknown>): [ReactNode, ReactNode][] {
  const n = (k: string): number | null => (typeof t[k] === "number" && Number.isFinite(t[k] as number) ? (t[k] as number) : null);
  const fills = (t.fills ?? null) as { ratio?: number; actual_bps?: number; model_bps?: number; matched?: number } | null;
  const rows: [ReactNode, ReactNode][] = [];
  if (n("sessions") !== null) rows.push(["Sessions", `${n("sessions")} (${n("weeks") ?? "–"} weeks)`]);
  if (n("trades") !== null) rows.push(["Closed trades", String(n("trades"))]);
  if (n("signal_match") !== null) rows.push(["Signal match", `${((n("signal_match") ?? 0) * 100).toFixed(1)}% of ${n("compared") ?? "–"}`]);
  if (n("daily_correlation") !== null) rows.push(["Daily P&L corr.", `${(n("daily_correlation") ?? 0).toFixed(2)} over ${n("daily_days") ?? "–"} days`]);
  if (fills && typeof fills.actual_bps === "number")
    rows.push(["Fill cost", `${fills.actual_bps.toFixed(1)} bps vs model ${typeof fills.model_bps === "number" ? fills.model_bps.toFixed(1) : "–"} (${typeof fills.ratio === "number" ? fills.ratio.toFixed(2) : "–"}x)`]);
  if (n("errors_recent") !== null) rows.push(["Errors, 4 weeks", String(n("errors_recent"))]);
  return rows;
}

function GateTab({
  specRef,
  specKey,
  recorded,
  run,
  onRun,
}: {
  specRef: string;
  specKey: string;
  recorded: RecordedGate | null;
  run: GateRun | null;
  onRun: (r: GateRun) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = async () => {
    setBusy(true);
    setError(null);
    try {
      onRun({ result: await api.gate(specRef), at: new Date() });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const shown = run ? { checks: run.result.checks, verdict: run.result.verdict } : recorded;
  const source = run
    ? `Checked just now (${ET_HM.format(run.at)} ET): the paper period replayed through the backtest engine.`
    : recorded
      ? `Last recorded by \`trades gate\`${recorded.date ? ` on ${recorded.date}` : ""} (\`paper/${specKey}/GATE.md\`). Check it now for today's numbers.`
      : "Not checked yet. The check replays the paper period through the backtest engine, so it can take a while.";
  const gc = shown ? gateCounts(shown.checks) : null;
  const tracking = run ? trackingRows(run.result.tracking) : [];
  return (
    <div className="stack">
      <Panel
        title={
          <>
            <span className="pl-fig">Fig. 4</span>The paper-to-live gate
          </>
        }
      >
        <p className="pl-lede">Ten checks before real money. Passing turns nothing on: there is no live order path in this app.</p>
        <div className="pl-gate-bar">
          <button className="btn primary" disabled={busy} onClick={() => void check()}>
            {busy ? "Replaying the paper period..." : "Check the gate now"}
          </button>
          {busy ? <span className="spinner" aria-hidden="true" /> : null}
          <span className="pl-gate-src">
            <InlineText text={source} />
          </span>
        </div>
        <ErrorBox error={error} />
        {shown && gc ? (
          <>
            <div className="pl-gate-sum">
              <div className="pl-readouts pl-readouts-4">
                <Ro label="Pass" value={gc.pass} unit={`/${shown.checks.length}`} tone={shown.verdict === "pass" ? "pos" : undefined} />
                <Ro label="Pending" value={gc.pending} note="not enough evidence yet" />
                <Ro label="Fail" value={gc.fail} tone={gc.fail ? "accent" : undefined} />
                <Ro
                  label="Verdict"
                  value={shown.verdict === "pass" ? "Pass" : shown.verdict === "fail" ? "Fail" : "Not yet"}
                  tone={shown.verdict === "fail" ? "accent" : shown.verdict === "pass" ? "pos" : undefined}
                />
              </div>
              <Cells
                cells={shown.checks.map((c) => ({ state: c.status, title: `${c.n}. ${c.name}: ${c.status.toUpperCase()}` }))}
                label={`${gc.pass} of ${shown.checks.length} gate checks pass`}
              />
            </div>
            <div className="table-wrap">
              <table className="data pl-checks pl-gate">
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>Check</th>
                    <th>Detail</th>
                    <th className="pl-c-res">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.checks.map((c) => (
                    <tr key={c.n} className={c.status === "fail" ? "pl-row-fail" : undefined}>
                      <td className="num pl-c-n">{c.n}</td>
                      <td className="pl-c-name">
                        <span className="pl-c-title">{c.name}</span>
                      </td>
                      <td className="pl-c-detail-cell">
                        <InlineText text={c.detail} />
                      </td>
                      <td className="pl-c-res">
                        <Tag kind={GATE_TAG[c.status]}>{c.status}</Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className={`callout ${shown.verdict === "pass" ? "info" : shown.verdict === "fail" ? "bad" : "warn"}`}>{VERDICT_TEXT[shown.verdict]}</div>
            {tracking.length ? (
              <div className="pl-gate-track">
                <div className="label pl-sub">Tracking against the replay</div>
                <KeyValues rows={tracking} columns={2} />
              </div>
            ) : null}
          </>
        ) : (
          <Empty>The ten checks appear here once the gate is checked.</Empty>
        )}
        <p className="pl-note">
          Some checks only you can complete, on the command line: the four kill switch drills (<code>trades paper drill halt</code>,{" "}
          <code>flatten</code>, <code>daily-loss</code>, <code>revoke-key</code>), a test alert (<code>trades paper drill alert</code>), and your
          review of <code>config/live.yaml</code> and <code>risk/limits.yaml</code> (<code>trades gate --attest live_config</code>).
        </p>
      </Panel>
    </div>
  );
}
