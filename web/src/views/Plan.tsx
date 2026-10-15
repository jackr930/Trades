import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import LineChart from "../components/LineChart";
import { ErrorBox, Help, Spinner, StatTile, Tabs } from "../components/ui";
import { fmtMoney, fmtPct, isNum } from "../format";
import { useApp, type Route } from "../state";
import { useChartColors } from "../theme";
import type { AllocationResult, DragResult, GoalResult, HarvestCandidate, HoldingsState, Settings } from "../types";

type Tab = "goal" | "costs" | "allocation" | "harvest";
const CLASS_LABEL: Record<string, string> = { stocks: "Stocks", bonds: "Bonds", cash: "Cash", real_estate: "Real estate" };

/** Planning tools for your own money. Arithmetic and suggestions only: nothing here trades. */
export default function Plan({ route }: { route: Route }) {
  const [tab, setTab] = useState<Tab>(() => (["goal", "costs", "allocation", "harvest"].includes(route.params.get("tab") ?? "") ? (route.params.get("tab") as Tab) : "goal"));
  const [h, setH] = useState<HoldingsState | null>(null);
  useEffect(() => {
    api.holdings().then(setH, () => undefined);
  }, []);
  return (
    <div className="stack" style={{ maxWidth: 1100 }}>
      <div>
        <h1>Plan</h1>
        <p className="secondary" style={{ marginTop: 4 }}>
          Simple tools for the decisions that matter more than any trading signal: how much to save, what fees and taxes cost, how
          your money is spread, and when a loss can lower your tax bill. Estimates, not advice.
        </p>
      </div>
      <Tabs
        tabs={[
          ["goal", "Reach a goal"],
          ["costs", "Fees and taxes"],
          ["allocation", "Rebalance"],
          ["harvest", "Tax losses"],
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "goal" ? <Goal holdings={h} /> : null}
      {tab === "costs" ? <Costs /> : null}
      {tab === "allocation" ? <Allocation holdings={h} /> : null}
      {tab === "harvest" ? <Harvest holdings={h} /> : null}
    </div>
  );
}

function NeedHoldings() {
  const { navigate } = useApp();
  return (
    <div className="callout info">
      This tool works on your real holdings.{" "}
      <button className="btn" onClick={() => navigate("/portfolio")}>
        Import them on the Portfolio page
      </button>
    </div>
  );
}

function pctInput(value: number, set: (v: number) => void, step = 1) {
  return {
    value: String(Math.round(value * 1000) / 10),
    step,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => set(e.target.value === "" ? 0 : Number(e.target.value) / 100),
  };
}

// ------------------------------------------------------------------------------- goal planner

function Goal({ holdings }: { holdings: HoldingsState | null }) {
  const { settings } = useApp() as { settings: Settings };
  const colors = useChartColors();
  const [form, setForm] = useState({ start_value: 0, monthly: 500, years: 20, goal: 500_000, inflation: 0.025 });
  const [mix, setMix] = useState({ stocks: 0.6, bonds: 0.4, cash: 0 });
  const [res, setRes] = useState<GoalResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = Math.round(form.start_value || holdings?.summary.total_value || settings.account_equity);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setRes(await api.planGoal({ ...form, start_value: start, mix }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const lines = useMemo(() => {
    if (!res) return [];
    const now = new Date().getUTCFullYear();
    const t = res.years.map((y) => Date.UTC(now + y, 0, 1) / 1000);
    return [
      { id: "p90", label: "A good outcome (1 in 10 do better)", data: { t, v: res.p90 }, color: colors.series[1], style: "dashed" as const },
      { id: "p50", label: "The middle outcome", data: { t, v: res.p50 }, color: colors.series[0] },
      { id: "p10", label: "A bad outcome (1 in 10 do worse)", data: { t, v: res.p10 }, color: colors.series[3] ?? colors.series[2], style: "dashed" as const },
    ];
  }, [res, colors]);

  const total = mix.stocks + mix.bonds + mix.cash;
  return (
    <div className="card">
      <div className="card-header">
        <h2>Will my savings reach a goal?</h2>
        <span className="sub">In today&apos;s dollars</span>
      </div>
      <div className="form-grid">
        <label className="field">
          <span>Starting amount ($)</span>
          <input className="input num" type="number" min={0} step={1000} value={start} onChange={(e) => setForm({ ...form, start_value: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>
            Added each month ($) <Help text="Negative for withdrawals, e.g. -2000 in retirement." />
          </span>
          <input className="input num" type="number" step={100} value={form.monthly} onChange={(e) => setForm({ ...form, monthly: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>Years</span>
          <input className="input num" type="number" min={1} max={60} value={form.years} onChange={(e) => setForm({ ...form, years: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>Goal ($, today&apos;s money)</span>
          <input className="input num" type="number" min={0} step={10000} value={form.goal} onChange={(e) => setForm({ ...form, goal: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>Stocks (%)</span>
          <input className="input num" type="number" min={0} max={100} {...pctInput(mix.stocks, (v) => setMix({ ...mix, stocks: v }), 5)} />
        </label>
        <label className="field">
          <span>Bonds (%)</span>
          <input className="input num" type="number" min={0} max={100} {...pctInput(mix.bonds, (v) => setMix({ ...mix, bonds: v }), 5)} />
        </label>
        <label className="field">
          <span>Cash (%)</span>
          <input className="input num" type="number" min={0} max={100} {...pctInput(mix.cash, (v) => setMix({ ...mix, cash: v }), 5)} />
        </label>
        <label className="field">
          <span>Inflation (% a year)</span>
          <input className="input num" type="number" min={0} max={15} {...pctInput(form.inflation, (v) => setForm({ ...form, inflation: v }), 0.5)} />
        </label>
      </div>
      {Math.abs(total - 1) > 0.001 ? <p className="small muted">The mix adds up to {fmtPct(total, 0, false)}; it will be scaled to 100%.</p> : null}
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" disabled={busy || total <= 0} onClick={() => void run()}>
          {busy ? "Simulating..." : "Simulate"}
        </button>
      </div>
      <ErrorBox error={error} />
      {busy ? <Spinner label="Drawing 2,000 possible futures from the past..." /> : null}
      {res && !busy ? (
        <div className="col" style={{ marginTop: 12 }}>
          <div className="tiles">
            {isNum(res.p_goal) ? <StatTile label={`Chance of ${fmtMoney(form.goal)} or more`} value={fmtPct(res.p_goal, 0, false)} /> : null}
            <StatTile label="Middle outcome" value={fmtMoney(res.p50.at(-1))} />
            <StatTile label="Bad outcome (1 in 10)" value={fmtMoney(res.p10.at(-1))} />
            <StatTile label="Good outcome (1 in 10)" value={fmtMoney(res.p90.at(-1))} />
            <StatTile label="You put in" value={fmtMoney(res.contributed)} />
          </div>
          <LineChart lines={lines} height={260} format={(v) => fmtMoney(v)} label="Balance in today's dollars" />
          <StayTheCourse res={res} start={start} />
          <p className="small muted">
            Each of {res.paths.toLocaleString()} paths strings together random 12-month stretches of the mix&apos;s own history (
            {Object.values(res.proxies).join(", ") || "cash"}, {res.history.from} to {res.history.to}, rebalanced monthly,
            dividends included), deflated by {fmtPct(res.inflation, 1, false)} inflation. That history is short, and the future
            can be worse than anything in it. Fees and taxes are not included: see Fees and taxes.
            {res.demo ? " Demo market: the prices are made up." : ""}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function StayTheCourse({ res, start }: { res: GoalResult; start: number }) {
  const h = res.history;
  return (
    <div className="callout warn">
      <b>Could you have stayed the course?</b> In its own history this mix&apos;s worst fall was{" "}
      <b>{fmtPct(h.max_drawdown, 0, false).replace("−", "")}</b>, from {h.peak} to {h.trough}
      {h.recovered ? `, and it was back at its old high by ${h.recovered}` : ", and it has not recovered yet"} ({h.months_below_peak}{" "}
      months below the peak). On your {fmtMoney(start)} that would have meant watching it fall to about{" "}
      <b>{fmtMoney(h.start_value_at_trough)}</b>. Its worst 12 months lost {fmtPct(h.worst_12_months, 0, false).replace("−", "")}.
      If you would have sold at the bottom, choose a gentler mix now: selling after a fall locks the loss in.
    </div>
  );
}

// ------------------------------------------------------------------------- fees and taxes

const PRESETS: [string, { expense_ratio: number; advisory_fee: number; turnover: number; trade_cost_bps: number }][] = [
  ["An active trading strategy", { expense_ratio: 0, advisory_fee: 0, turnover: 3, trade_cost_bps: 5 }],
  ["A financial adviser (1% a year)", { expense_ratio: 0.0005, advisory_fee: 0.01, turnover: 0.2, trade_cost_bps: 2 }],
  ["An actively managed fund", { expense_ratio: 0.007, advisory_fee: 0, turnover: 0.5, trade_cost_bps: 5 }],
];

function Costs() {
  const { settings } = useApp() as { settings: Settings };
  const [form, setForm] = useState({ amount: settings.account_equity, years: 25, gross_return: 0.07, ...PRESETS[0][1] });
  const [res, setRes] = useState<{ index_fund: DragResult; scenario: DragResult; taxable: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setError(null);
    try {
      setRes(await api.planDrag(form));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>What do fees, trading and taxes cost?</h2>
        <span className="sub">The same market return, with and without them</span>
      </div>
      <div className="chips" role="group" aria-label="Examples">
        {PRESETS.map(([label, p]) => (
          <button key={label} type="button" className="chip" onClick={() => setForm({ ...form, ...p })}>
            {label}
          </button>
        ))}
      </div>
      <div className="form-grid" style={{ marginTop: 12 }}>
        <label className="field">
          <span>Amount ($)</span>
          <input className="input num" type="number" min={1} step={1000} value={form.amount} onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>Years</span>
          <input className="input num" type="number" min={1} max={60} value={form.years} onChange={(e) => setForm({ ...form, years: Number(e.target.value) })} />
        </label>
        <label className="field">
          <span>
            Market return (% a year) <Help text="Before any costs. US stocks returned roughly 7% a year after inflation over the long run, with long stretches far below that." />
          </span>
          <input className="input num" type="number" {...pctInput(form.gross_return, (v) => setForm({ ...form, gross_return: v }), 0.5)} />
        </label>
        <label className="field">
          <span>Fund fees (% a year)</span>
          <input className="input num" type="number" min={0} {...pctInput(form.expense_ratio, (v) => setForm({ ...form, expense_ratio: v }), 0.05)} />
        </label>
        <label className="field">
          <span>Adviser fee (% a year)</span>
          <input className="input num" type="number" min={0} {...pctInput(form.advisory_fee, (v) => setForm({ ...form, advisory_fee: v }), 0.25)} />
        </label>
        <label className="field">
          <span>
            Turnover (% a year) <Help text="How much of the portfolio is sold and replaced each year. Buy and hold: about 2%. The strategies here often trade 200-500% a year." />
          </span>
          <input className="input num" type="number" min={0} {...pctInput(form.turnover, (v) => setForm({ ...form, turnover: v }), 10)} />
        </label>
        <label className="field">
          <span>Cost per trade (bps)</span>
          <input className="input num" type="number" min={0} value={form.trade_cost_bps} onChange={(e) => setForm({ ...form, trade_cost_bps: Number(e.target.value) })} />
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" onClick={() => void run()}>
          Compare
        </button>
      </div>
      <ErrorBox error={error} />
      {res ? (
        <div className="col" style={{ marginTop: 12 }}>
          <div className="callout info">
            After {form.years} years, this scenario leaves you <b>{fmtMoney(res.index_fund.final - res.scenario.final)}</b> less than a
            low-cost index fund held throughout: {fmtMoney(res.scenario.final)} against {fmtMoney(res.index_fund.final)}.
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th />
                  <th className="num">Index fund, held</th>
                  <th className="num">This scenario</th>
                </tr>
              </thead>
              <tbody>
                {res.scenario.rows.map((r, i) => (
                  <tr key={r.label}>
                    <td>{r.label}</td>
                    <td className="num">{fmtMoney(res.index_fund.rows[i]?.value)}</td>
                    <td className="num">{fmtMoney(r.value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">
            Index fund: 0.03% fees, 2% turnover. Each turn of the portfolio pays the trading cost on the sale and on the purchase.
            {res.taxable
              ? " Taxes use your rates from Settings: selling realises gains every year, at short-term rates when turnover is above 100% (positions held under a year); buy and hold defers them to the end."
              : " Your account is tax-advantaged, so no tax is charged."}{" "}
            Losses are not harvested, and every year earns the same return: real returns vary, and so would these numbers.
          </p>
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------------------ allocation

function Allocation({ holdings }: { holdings: HoldingsState | null }) {
  const [targets, setTargets] = useState({ stocks: 0.6, bonds: 0.4, cash: 0, real_estate: 0 });
  const [band, setBand] = useState(0.05);
  const [res, setRes] = useState<AllocationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (holdings && !holdings.holdings.length) return <NeedHoldings />;

  const run = async () => {
    setError(null);
    try {
      setRes(await api.planAllocation(targets, band));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>Rebalance, and keep taxes in mind</h2>
        <span className="sub">Your holdings by asset class against a target</span>
      </div>
      <div className="form-grid">
        {(Object.keys(targets) as (keyof typeof targets)[]).map((k) => (
          <label key={k} className="field">
            <span>{CLASS_LABEL[k]} target (%)</span>
            <input className="input num" type="number" min={0} max={100} {...pctInput(targets[k], (v) => setTargets({ ...targets, [k]: v }), 5)} />
          </label>
        ))}
        <label className="field">
          <span>
            Band (%) <Help text="Rebalance only when a class is more than this far from its target: trading on every small drift costs money and tax for little benefit." />
          </span>
          <input className="input num" type="number" min={0} max={50} {...pctInput(band, setBand, 1)} />
        </label>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" onClick={() => void run()}>
          Check my holdings
        </button>
      </div>
      <ErrorBox error={error} />
      {res ? (
        <div className="col" style={{ marginTop: 12 }}>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Class</th>
                  <th className="num">Value</th>
                  <th className="num">Now</th>
                  <th className="num">Target</th>
                  <th className="num">To reach the target</th>
                </tr>
              </thead>
              <tbody>
                {res.rows.map((r) => (
                  <tr key={r.class}>
                    <td>{CLASS_LABEL[r.class] ?? r.class}</td>
                    <td className="num">{fmtMoney(r.value)}</td>
                    <td className="num">{fmtPct(r.share, 0, false)}</td>
                    <td className="num">{fmtPct(r.target, 0, false)}</td>
                    <td className="num">{fmtMoney(r.difference, 0, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {res.moves.length ? (
            <ul>
              {res.moves.map((m) => (
                <li key={m.class}>
                  {m.action === "add" ? "Add" : "Reduce"} {CLASS_LABEL[m.class]?.toLowerCase()} by <b>{fmtMoney(Math.abs(m.amount))}</b>,{" "}
                  {m.where}.
                </li>
              ))}
            </ul>
          ) : (
            <p className="secondary">Every class is within {fmtPct(res.band, 0, false)} of its target: nothing to do.</p>
          )}
          <h3 style={{ marginTop: 8 }}>Asset location</h3>
          {res.location.map((n) => (
            <p key={n} className="secondary" style={{ marginTop: 0 }}>
              {n}
            </p>
          ))}
          {res.guessed.length ? (
            <p className="small muted">
              Classed as stocks without certainty: {res.guessed.join(", ")}. Funds holding bonds or property under unfamiliar names may
              be misclassified.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// -------------------------------------------------------------------------- tax losses

function Harvest({ holdings }: { holdings: HoldingsState | null }) {
  const [minLoss, setMinLoss] = useState(200);
  const [res, setRes] = useState<{ candidates: HarvestCandidate[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.planHarvest(minLoss).then(setRes, (e: Error) => setError(e.message));
  }, [minLoss]);
  if (holdings && !holdings.holdings.length) return <NeedHoldings />;
  return (
    <div className="card">
      <div className="card-header">
        <h2>Losses that could lower your tax bill</h2>
        <span className="sub">Taxable accounts only; suggestions, not orders</span>
      </div>
      <label className="field" style={{ maxWidth: 220 }}>
        <span>Smallest loss worth it ($)</span>
        <input className="input num" type="number" min={0} step={50} value={minLoss} onChange={(e) => setMinLoss(Number(e.target.value))} />
      </label>
      <ErrorBox error={error} />
      {!res ? <Spinner /> : null}
      {res && !res.candidates.length ? <p className="secondary">No taxable position is that far below its cost.</p> : null}
      {res?.candidates.map((c) => (
        <div key={`${c.account}-${c.symbol}`} className="vote-card" style={{ marginTop: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>
              {c.symbol} <span className="small muted">in {c.account}</span>
            </b>
            <span className="down">
              {fmtMoney(-c.loss)} ({fmtPct(isNum(c.loss_share) ? -c.loss_share : null, 0)})
            </span>
          </div>
          <p className="small" style={{ margin: "6px 0" }}>
            Selling would realise the loss and could cut this year&apos;s tax by about {fmtMoney(c.tax_deferred[0])} to{" "}
            {fmtMoney(c.tax_deferred[1])} (long-term to short-term rate).{" "}
            {c.replacement
              ? `To stay invested, a similar fund that tracks a different index, such as ${c.replacement}, is a common choice.`
              : "An individual stock has no similar-but-different twin; a broad fund keeps you invested meanwhile."}
          </p>
          <ul className="small" style={{ margin: 0 }}>
            {c.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      ))}
      <p className="small muted" style={{ marginTop: 12 }}>
        Harvesting defers tax rather than avoiding it: the replacement&apos;s lower cost means a larger gain when you sell it later.
        Losses offset gains first, then up to $3,000 a year of other income, and the rest carries forward. Whether two funds are
        &quot;substantially identical&quot; has never been defined by the IRS. Not tax advice: check with a tax professional.
      </p>
    </div>
  );
}
