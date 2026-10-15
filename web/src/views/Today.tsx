import { useEffect, useState } from "react";
import { api } from "../api";
import VsSpy from "../components/VsSpy";
import { DirectionBadge, ErrorBox, Spinner } from "../components/ui";
import { fmtMoney, fmtPct, pnlClass } from "../format";
import { useApp } from "../state";
import type { HoldingsState, Recommendation, Settings, TrackRecord } from "../types";

const MIN_DAYS = 60;

/** The home screen: what the Live Desk suggests today, how much to trust it, and your money. */
export default function Today() {
  const { settings, navigate } = useApp() as { settings: Settings; navigate: (p: string) => void };
  const demo = settings.provider === "synthetic";
  return (
    <div className="stack" style={{ maxWidth: 1100 }}>
      <div>
        <h1>Today</h1>
        <p className="secondary" style={{ marginTop: 4 }}>
          {new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" })}
        </p>
      </div>
      {demo ? (
        <div className="callout info">
          You are looking at the <b>demo market</b>: its prices are made up, so nothing here is about real stocks.{" "}
          <button className="btn" style={{ marginLeft: 8 }} onClick={() => navigate("/settings")}>
            Use real prices
          </button>
        </div>
      ) : null}
      <ProvenCard />
      <Suggestions />
      <VsSpy demo={demo} />
      <MoneyCard />
    </div>
  );
}

/** How far the forward test has got: the honest answer to "should I follow these calls?" */
function ProvenCard() {
  const { navigate } = useApp();
  const [rec, setRec] = useState<TrackRecord | null | undefined>(undefined);
  useEffect(() => {
    api.trackRecord().then((r) => setRec(r.record), () => setRec(null));
  }, []);
  if (rec === undefined) return null;
  const exp = rec?.experiments.find((e) => e.id === rec.experiment_id) ?? rec?.experiments.at(-1);
  return (
    <div className="card">
      <div className="card-header">
        <h2>Are these calls proven?</h2>
        <button className="btn" onClick={() => navigate("/track")}>
          Track record
        </button>
      </div>
      {rec?.decision && rec.decision.status !== "NOT YET" ? (
        <p className="secondary" style={{ margin: 0 }}>
          <b>{rec.decision.status}.</b> {rec.decision.summary}
        </p>
      ) : !exp ? (
        <p className="secondary" style={{ margin: 0 }}>
          <b>Not yet.</b> The forward test has not started, so nobody knows whether these calls beat simply holding SPY. Treat them
          as untested ideas.
        </p>
      ) : exp.verdict?.status === "PASS" ? (
        <p className="secondary" style={{ margin: 0 }}>
          <b>The forward test passed its pre-registered rule</b> ({exp.verdict.detail}) Past success still does not guarantee the
          future.
        </p>
      ) : exp.verdict?.status === "FAIL" ? (
        <p className="secondary" style={{ margin: 0 }}>
          <b>No: the forward test failed its pre-registered rule</b> ({exp.verdict.detail}) Simply holding SPY did as well or better.
        </p>
      ) : (
        <p className="secondary" style={{ margin: 0 }}>
          <b>Not yet.</b> The forward test has {exp.independent_days_21} of the {MIN_DAYS} independent results its rule needs; a
          verdict is possible around {exp.conclusive_from}. Until then, nobody knows whether these calls beat simply holding SPY.
        </p>
      )}
    </div>
  );
}

function Suggestions() {
  const { navigate } = useApp();
  const [recs, setRecs] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.recommendations({}).then(
      (r) => setRecs(r.recommendations),
      (e: Error) => setError(e.message),
    );
  }, []);
  if (error) return <ErrorBox error={error} />;
  if (!recs) return <Spinner label="Asking every strategy about your watchlist..." />;
  const held = (r: Recommendation) => r.sizing.held_weight ?? r.sizing.weight; // what whole shares hold
  const buys = recs.filter((r) => held(r) > 0).sort((a, b) => held(b) - held(a));
  const avoid = recs.filter((r) => r.consensus.action === "SELL" || r.consensus.action === "SHORT");
  const changed = recs.filter((r) => r.fresh_signals.length);
  const invested = buys.reduce((a, r) => a + held(r), 0);
  const provisional = recs.some((r) => r.provisional);
  return (
    <div className="card">
      <div className="card-header">
        <h2>What the Live Desk suggests</h2>
        <button className="btn" onClick={() => navigate("/live")}>
          Details
        </button>
      </div>
      {buys.length ? (
        <>
          <p className="secondary" style={{ marginTop: 0 }}>
            Hold {buys.length === 1 ? "one position" : `${buys.length} positions`}, {fmtPct(invested, 0, false)} of your account in
            all; keep the rest in cash or T-bills.
          </p>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Call</th>
                  <th className="num">Share of account</th>
                </tr>
              </thead>
              <tbody>
                {buys.map((r) => (
                  <tr key={r.symbol}>
                    <td>
                      {r.symbol}
                      {r.name ? <span className="name">{r.name}</span> : null}
                    </td>
                    <td>
                      <DirectionBadge score={r.consensus.score} label={r.consensus.label} />
                    </td>
                    <td className="num">{fmtPct(held(r), 1, false)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="secondary" style={{ marginTop: 0 }}>
          No buys today: the strategies see nothing worth holding on your watchlist. Cash (or T-bills) is the suggestion.
        </p>
      )}
      {avoid.length ? (
        <p className="small" style={{ marginBottom: 0 }}>
          Avoid: {avoid.map((r) => r.symbol).join(", ")}.
        </p>
      ) : null}
      {changed.length ? (
        <p className="small" style={{ marginTop: 10, marginBottom: 0 }}>
          Changed on the latest bar: {changed.map((r) => `${r.symbol} (${r.fresh_signals.join(", ")})`).join("; ")}.
        </p>
      ) : null}
      <p className="small muted" style={{ marginBottom: 0 }}>
        {provisional ? "Today's bar is still forming, so these calls can change before the close. " : ""}
        Suggestions only: this app never places real orders, and they are not advice for your circumstances.
      </p>
    </div>
  );
}

function MoneyCard() {
  const { navigate } = useApp();
  const [h, setH] = useState<HoldingsState | null>(null);
  useEffect(() => {
    api.holdings().then(setH, () => undefined);
  }, []);
  if (!h) return null;
  if (!h.holdings.length)
    return (
      <div className="card">
        <div className="card-header">
          <h2>Your money</h2>
        </div>
        <p className="secondary" style={{ marginTop: 0 }}>
          Import a positions file from your broker (Schwab, Fidelity, Vanguard or Robinhood) to see your holdings next to these
          suggestions and plan with them. It stays on this server; nothing is traded.
        </p>
        <button className="btn primary" onClick={() => navigate("/portfolio")}>
          Import holdings
        </button>
      </div>
    );
  const s = h.summary;
  return (
    <div className="card">
      <div className="card-header">
        <h2>Your money</h2>
        <button className="btn" onClick={() => navigate("/portfolio")}>
          Portfolio
        </button>
      </div>
      <p className="secondary" style={{ margin: 0 }}>
        {fmtMoney(s.total_value)} across {Object.keys(s.accounts).length} account(s) as of your last import; unrealised gain{" "}
        <span className={pnlClass(s.unrealised)}>{fmtMoney(s.unrealised, 0, true)}</span> where the cost is known.
      </p>
    </div>
  );
}
