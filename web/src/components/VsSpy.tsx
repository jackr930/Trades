import { useState } from "react";
import { api } from "../api";
import type { VsSpyResult } from "../types";
import { fmtDate, fmtMoney, fmtNum, fmtPct, isNum } from "../format";
import { ErrorBox, Help, Spinner } from "./ui";

const START = 10_000;

/** One click: would the Live Desk's consensus have beaten simply buying SPY, after costs and taxes? */
export default function VsSpy({ demo }: { demo: boolean }) {
  const [universe, setUniverse] = useState<"watchlist" | "sectors">("watchlist");
  const [res, setRes] = useState<VsSpyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setRes(await api.vsSpy(universe));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>Is this better than just buying SPY?</h2>
        <span className="sub">The Live Desk&apos;s consensus since 2010, after costs{res?.taxable === false ? "" : " and taxes"}</span>
      </div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <div className="chips" role="group" aria-label="Which symbols">
          <button type="button" className="chip toggle" aria-pressed={universe === "watchlist"} onClick={() => setUniverse("watchlist")}>
            My watchlist
          </button>
          <button
            type="button"
            className="chip toggle"
            aria-pressed={universe === "sectors"}
            disabled={demo}
            title={demo ? "Needs real prices: choose Yahoo as the data source in Settings" : undefined}
            onClick={() => setUniverse("sectors")}
          >
            Nine sector ETFs (no hindsight)
          </button>
        </div>
        <button className="btn primary" disabled={busy} onClick={() => void run()}>
          {busy ? "Testing..." : "Find out"}
        </button>
      </div>
      <ErrorBox error={error} />
      {busy ? <Spinner label="Backtesting since 2010 (this can take a minute)..." /> : null}
      {res && !busy ? <Result r={res} /> : null}
    </div>
  );
}

function Result({ r }: { r: VsSpyResult }) {
  const key = r.taxable ? "after_tax_cagr_if_sold" : "cagr";
  const mine = r.strategy[key];
  const spy = r.spy[key];
  const p = r.p_beats;
  const bench = r.demo ? "the demo index" : "SPY";
  let verdict: string;
  let tone: "good" | "warn" | "bad";
  if (!isNum(mine) || !isNum(spy)) {
    verdict = "Not enough history to compare.";
    tone = "warn";
  } else if (mine <= spy) {
    verdict = `No. Buying ${bench} and holding it did better: ${fmtPct(spy, 1, false)} a year against ${fmtPct(mine, 1, false)}.`;
    tone = "bad";
  } else if (isNum(p) && p >= 0.95) {
    verdict = `In this test, yes (${fmtPct(mine, 1, false)} a year against ${fmtPct(spy, 1, false)}), and luck is an unlikely explanation. It is still one backtest: the Track Record is the real test.`;
    tone = "good";
  } else {
    verdict = `Ahead in this test (${fmtPct(mine, 1, false)} a year against ${fmtPct(spy, 1, false)}), but that could easily be luck: the chance it really beats ${bench} is ${fmtPct(p, 0, false)}, short of the 95% the decision rule asks for.`;
    tone = "warn";
  }
  const grown = (x: number | null | undefined) => (isNum(x) ? fmtMoney(START * (1 + x)) : "–");
  return (
    <div className="col" style={{ marginTop: 12 }}>
      <div className={`callout ${tone === "good" ? "info" : tone}`}>
        <b>{verdict}</b>
      </div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>
                {fmtDate(r.start_time)} to {fmtDate(r.end_time)}
              </th>
              <th className="num">Live Desk consensus</th>
              <th className="num">{r.benchmark ?? "SPY"}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{fmtMoney(START)} became (before tax)</td>
              <td className="num">{grown(r.strategy.total_return)}</td>
              <td className="num">{grown(r.spy.total_return)}</td>
            </tr>
            <tr>
              <td>Growth a year{r.taxable ? ", after tax if sold at the end" : ""}</td>
              <td className="num">{fmtPct(mine, 1)}</td>
              <td className="num">{fmtPct(spy, 1)}</td>
            </tr>
            <tr>
              <td>
                Worst fall from a peak <Help text="The largest drop from a high to a later low. Ask yourself whether you would have kept following the strategy through it." />
              </td>
              <td className="num">{fmtPct(r.strategy.max_drawdown, 0)}</td>
              <td className="num">{fmtPct(r.spy.max_drawdown, 0)}</td>
            </tr>
            <tr>
              <td>
                Sharpe ratio <Help text="Return per unit of risk, above what T-bills paid. Higher is better; SPY's long-run figure is around 0.5." />
              </td>
              <td className="num">{fmtNum(r.strategy.sharpe, 2)}</td>
              <td className="num">{fmtNum(r.spy.sharpe, 2)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="small muted">
        Probability the consensus really grows faster than {bench}: <b>{fmtPct(p, 0, false)}</b> (a block bootstrap of daily returns,
        before tax).{" "}
        {r.universe === "watchlist"
          ? "Your watchlist was picked today, knowing which stocks did well since 2010, so this test flatters it; the sector ETFs were not picked that way."
          : "The nine sector ETFs cover the whole US market and were not picked for having won."}{" "}
        {r.demo ? "Demo market: the prices are made up, so the result says nothing about real markets." : ""}
      </p>
      {r.research_log ? <p className="small muted">{r.research_log.interpretation}</p> : null}
    </div>
  );
}
