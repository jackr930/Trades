import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import LineChart from "../components/LineChart";
import { ErrorBox, Help, Spinner, StatTile, StatusDot, Tabs } from "../components/ui";
import { fmtNum, fmtPct, pnlClass } from "../format";
import { useChartColors } from "../theme";
import type { JournalStat, TrackExperiment, TrackRecord, Verdict } from "../types";

const LABEL_ORDER = ["Strong buy", "Buy", "Neutral", "Sell / avoid", "Strong sell / avoid", "Sell / short", "Strong sell / short"];
const MIN_DAYS = 60;

/** The forward journal: calls logged after each close, before anyone knew how they would turn out. */
export default function TrackRecordView() {
  const [data, setData] = useState<{ source: string; record: TrackRecord | null } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.trackRecord().then(setData, (e: Error) => setError(e.message));
  }, []);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner label="Reading the track record..." />;
  const rec = data.record;
  return (
    <div className="stack" style={{ maxWidth: 1100 }}>
      <div>
        <h1>Track record</h1>
        <p className="secondary" style={{ marginTop: 4 }}>
          Every evening the journal workflow logs the Live Desk&apos;s calls for the session, before anyone knows how they turn
          out, then scores the ones whose time has come against {rec?.benchmark ?? "SPY"}. This is the only evidence here that
          no backtest can fake.
        </p>
        <p className="small muted" style={{ marginTop: 4 }}>
          Read from {data.source}
          {rec?.generated_at ? `, scored ${rec.generated_at.slice(0, 16).replace("T", " ")} UTC with prices through ${rec.prices_through}` : ""}.
          Change the source under Settings.
        </p>
      </div>
      {!rec ? <NoRecord /> : <Record rec={rec} />}
    </div>
  );
}

function NoRecord() {
  return (
    <div className="callout info">
      <b>No track record yet.</b> It appears after <code>trades journal score</code> has run, which the journal workflow does
      every weekday evening and commits as <code>journal/track_record.json</code>. On a hosted copy, point Settings → Track
      record source at your repository on GitHub, since the server&apos;s own copy of the repository never changes after it is
      deployed.
    </div>
  );
}

function Record({ rec }: { rec: TrackRecord }) {
  const current = rec.experiments.find((e) => e.id === rec.experiment_id) ?? rec.experiments[rec.experiments.length - 1];
  const older = rec.experiments.filter((e) => e !== current);
  return (
    <>
      <HealthCard problems={rec.health} />
      <RuleCard rec={rec} />
      {current ? <ExperimentCard exp={current} benchmark={rec.benchmark} current /> : <div className="callout">The journal has no rows yet.</div>}
      {older.length ? (
        <details className="card">
          <summary>
            <b>Earlier experiments ({older.length})</b>{" "}
            <span className="small muted">A changed watchlist or rule starts a new experiment; each is scored on its own.</span>
          </summary>
          {older.map((e) => (
            <ExperimentCard key={e.id} exp={e} benchmark={rec.benchmark} />
          ))}
        </details>
      ) : null}
      {rec.paper ? <PaperCard paper={rec.paper} verdict={rec.rule?.paper ?? null} /> : null}
    </>
  );
}

function HealthCard({ problems }: { problems?: string[] }) {
  if (!problems) return null;
  return problems.length ? (
    <div className="callout warn">
      <b>The journal needs attention</b>
      <ul>
        {problems.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  ) : (
    <div>
      <StatusDot status="good">Journal healthy: no missed sessions or stuck paper orders.</StatusDot>
    </div>
  );
}

const MONTH_YEAR = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", year: "numeric" });

function RuleCard({ rec }: { rec: TrackRecord }) {
  const rule = rec.rule;
  if (!rule) return null;
  return (
    <div className="card">
      <div className="card-header">
        <h2>The pre-registered decision rule</h2>
        <span className="sub">What counts as success, fixed before any result was seen</span>
      </div>
      <p className="secondary" style={{ marginTop: 0 }}>
        Registered {rule.registered?.slice(0, 10) ?? "(not yet committed)"}, last changed {rule.last_changed?.slice(0, 10) ?? "never"}.
        {rule.forward ? ` Forward test: ${rule.forward}` : ""}
      </p>
      {rule.changed_after_first_row ? (
        <div className="callout bad">
          The rule was changed after the journal&apos;s first row. A rule edited once results were visible no longer protects
          you from moving the goalposts.
        </div>
      ) : null}
    </div>
  );
}

function ExperimentCard({ exp, benchmark, current = false }: { exp: TrackExperiment; benchmark: string; current?: boolean }) {
  const colors = useChartColors();
  const [h, setH] = useState<"21" | "5">("21");
  const lines = useMemo(
    () => [
      {
        id: "bullish",
        label: `Buy calls held one day each, minus ${benchmark}`,
        data: exp.curve,
        color: colors.series[0],
        kind: "baseline" as const,
      },
    ],
    [exp, benchmark, colors],
  );
  const labels = exp.labels[h] ?? {};
  const strategies = exp.strategies[h] ?? {};
  const progress = Math.min(exp.independent_days_21 / MIN_DAYS, 1);
  return (
    <div className="card">
      <div className="card-header">
        <h2>{current ? "Current experiment" : "Experiment"} <code>{exp.id}</code></h2>
        <span className="sub">
          {exp.first_session} to {exp.last_session}
        </span>
      </div>
      <div className="tiles">
        <StatTile label="Sessions recorded" value={String(exp.sessions)} delta={<>{exp.rows} rows</>} />
        <StatTile
          label="Independent 21-session results"
          value={`${exp.independent_days_21} of ${MIN_DAYS}`}
          help="Calls 21 sessions apart, so their outcomes do not overlap. The decision rule needs 60 of them: about five years of trading days."
          delta={<progress value={progress} max={1} style={{ width: "100%" }} aria-label="Progress to a verdict" />}
        />
        <StatTile
          label="Verdict possible from"
          value={MONTH_YEAR.format(new Date(`${exp.conclusive_from}T00:00:00Z`))}
          help="When the 60th independent result arrives, if the journal keeps running every session."
        />
        {exp.verdict ? (
          <StatTile label="Decision rule (forward)" value={exp.verdict.status} delta={exp.verdict.detail} />
        ) : null}
      </div>
      {exp.curve.t.length > 1 ? (
        <>
          <LineChart lines={lines} height={220} format={(v) => fmtPct(v, 2)} label="Buy calls minus the benchmark" />
          <p className="small muted">
            A picture, not a test: each day&apos;s Buy and Strong buy symbols bought at the next open and sold at the open after,
            minus {benchmark} over the same day, before costs. Small samples are mostly noise; only the rule above decides.
          </p>
        </>
      ) : null}
      <div className="row" style={{ marginTop: 12, justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>
          Results by label <Help text={`Return from the next open to the close 5 or 21 sessions later, minus ${benchmark}'s over the same window, averaged per day.`} />
        </h3>
        <Tabs tabs={[["21", "21 sessions"], ["5", "5 sessions"]]} value={h} onChange={setH} />
      </div>
      <StatTable rows={sortLabels(labels)} benchmark={benchmark} />
      {Object.keys(strategies).length ? (
        <>
          <h3 style={{ marginTop: 18 }}>By strategy</h3>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th className="num">Bullish: days</th>
                  <th className="num">Bullish: mean excess</th>
                  <th className="num">Not bullish: days</th>
                  <th className="num">Not bullish: mean excess</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(strategies).map(([k, s]) => (
                  <tr key={k}>
                    <td>{k}</td>
                    <td className="num">{s.bullish.n}</td>
                    <td className={`num ${pnlClass(s.bullish.mean)}`}>{fmtPct(s.bullish.mean, 2)}</td>
                    <td className="num">{s.other.n}</td>
                    <td className={`num ${pnlClass(s.other.mean)}`}>{fmtPct(s.other.mean, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <h3 style={{ marginTop: 18 }}>Latest calls ({exp.last_session})</h3>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Call</th>
              <th className="num">Score</th>
              <th className="num">Suggested weight</th>
            </tr>
          </thead>
          <tbody>
            {exp.latest.map((r) => (
              <tr key={r.symbol}>
                <td>{r.symbol}</td>
                <td>{r.label}</td>
                <td className="num">{fmtNum(r.score, 2, true)}</td>
                <td className="num">{fmtPct(r.weight, 1, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {exp.code_versions.length > 1 ? (
        <p className="small muted">
          The code changed during this experiment ({exp.code_versions.length} versions). The rules did not (the experiment id
          would have changed), but check the commits if a result looks odd.
        </p>
      ) : null}
    </div>
  );
}

function sortLabels(labels: Record<string, JournalStat>): [string, JournalStat][] {
  const rank = (k: string) => (LABEL_ORDER.includes(k) ? LABEL_ORDER.indexOf(k) : 99);
  return Object.entries(labels).sort((a, b) => rank(a[0]) - rank(b[0]));
}

function StatTable({ rows, benchmark }: { rows: [string, JournalStat][]; benchmark: string }) {
  if (!rows.length) return <p className="muted small">No finished windows yet.</p>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Call</th>
            <th className="num">Independent days</th>
            <th className="num">Mean excess vs {benchmark}</th>
            <th className="num">Hit rate</th>
            <th className="num">
              t-stat <Help text="Mean divided by its standard error. Below about 2 (or 3, given how many numbers this page shows) is no evidence of skill." />
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, s]) => (
            <tr key={label}>
              <td>{label}</td>
              <td className="num">{s.n}</td>
              <td className={`num ${pnlClass(s.mean)}`}>{fmtPct(s.mean, 2)}</td>
              <td className="num">{fmtPct(s.hit_rate, 0, false)}</td>
              <td className="num">{fmtNum(s.t, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PaperCard({ paper, verdict }: { paper: NonNullable<TrackRecord["paper"]>; verdict: Verdict | null }) {
  return (
    <div className="card">
      <div className="card-header">
        <h2>Paper account</h2>
        <span className="sub">Alpaca paper fills against the open the backtests assume</span>
      </div>
      <div className="tiles">
        <StatTile label="Orders logged" value={String(paper.orders)} delta={<>{paper.filled} filled</>} />
        <StatTile
          label="Mean slippage"
          value={paper.mean_slippage_bps === null ? "–" : `${fmtNum(paper.mean_slippage_bps, 1, true)} bps`}
          help="Fill price against that session's open, in basis points; positive is a cost. The backtests assume 5 bps."
        />
        <StatTile label="Worst fill" value={paper.worst_slippage_bps === null ? "–" : `${fmtNum(paper.worst_slippage_bps, 1, true)} bps`} />
        {verdict ? <StatTile label="Decision rule (paper)" value={verdict.status} delta={verdict.detail} /> : null}
      </div>
      <p className="small muted">
        Alpaca&apos;s paper account simulates fills from quotes, so this is only a rough check of the cost assumption.
      </p>
    </div>
  );
}
