import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorBox, Help, Spinner } from "../components/ui";
import { fmtMoney, fmtNum, isNum, pnlClass } from "../format";
import { useApp } from "../state";
import type { AccountType, HoldingsState } from "../types";

/** Your real holdings, imported from a broker's CSV export. Read-only: nothing here trades. */
export default function Portfolio() {
  const { refreshSettings, toast } = useApp();
  const [h, setH] = useState<HoldingsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);

  useEffect(() => {
    api.holdings().then(setH, (e: Error) => setError(e.message));
  }, []);

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    try {
      const r = await api.importHoldings(await file.text());
      setH(r);
      setNotes(r.notes);
      await refreshSettings();
      toast(`Imported ${r.imported} positions from ${r.broker}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const setType = async (account: string, type: AccountType) => {
    if (!h) return;
    try {
      await api.saveSettings({ account_types: { ...h.account_types, [account]: type } });
      setH(await api.holdings());
      await refreshSettings();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const forget = async (account?: string) => {
    if (!window.confirm(account ? `Forget the holdings of ${account}?` : "Forget all imported holdings?")) return;
    setH(await api.deleteHoldings(account));
    await refreshSettings();
  };

  if (!h && !error) return <Spinner label="Loading your holdings..." />;
  const accounts = Object.entries(h?.summary.accounts ?? {});
  return (
    <div className="stack" style={{ maxWidth: 1100 }}>
      <div>
        <h1>Portfolio</h1>
        <p className="secondary" style={{ marginTop: 4 }}>
          Your real holdings, from your broker&apos;s export. They stay on this server (and in its backups), are never sent anywhere
          else, and nothing here places an order.
        </p>
      </div>
      <ErrorBox error={error} />
      <div className="card">
        <div className="card-header">
          <h2>Import</h2>
          <span className="sub">Re-importing an account replaces it; other accounts stay</span>
        </div>
        <ul className="small" style={{ marginTop: 0 }}>
          <li>
            <b>Schwab:</b> Accounts → Positions → Export.
          </li>
          <li>
            <b>Fidelity:</b> Positions → Download.
          </li>
          <li>
            <b>Vanguard:</b> Balances and holdings → Download (CSV).
          </li>
          <li>
            <b>Robinhood:</b> Account → Reports and statements → account activity CSV. Positions are rebuilt from your buys and sells
            at average cost; splits and transfers are not handled.
          </li>
        </ul>
        <label className="btn primary">
          Choose a CSV file
          <input type="file" accept=".csv,text/csv" style={{ display: "none" }} onChange={(e) => void importFile(e.target.files?.[0])} />
        </label>
        {notes.length ? (
          <ul className="small muted">
            {notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {h && h.holdings.length ? (
        <>
          <div className="card">
            <div className="card-header">
              <h2>Accounts</h2>
              <span className="sub">
                {fmtMoney(h.summary.total_value)} in all; unrealised{" "}
                <span className={pnlClass(h.summary.unrealised)}>{fmtMoney(h.summary.unrealised, 0, true)}</span>
              </span>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>
                      Tax treatment <Help text="Taxable: gains are taxed when you sell. Tax-advantaged: an IRA, 401(k), HSA or similar, where trades inside the account are not taxed as you go. Guessed from the account's name; correct it here." />
                    </th>
                    <th className="num">Value</th>
                    <th className="num">Cash</th>
                    <th className="num">Positions</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {accounts.map(([name, a]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>
                        <select className="input" value={a.type} onChange={(e) => void setType(name, e.target.value as AccountType)}>
                          <option value="taxable">Taxable</option>
                          <option value="tax_advantaged">Tax-advantaged</option>
                        </select>
                      </td>
                      <td className="num">{fmtMoney(a.value)}</td>
                      <td className="num">{fmtMoney(a.cash)}</td>
                      <td className="num">{a.positions}</td>
                      <td>
                        <button className="btn" onClick={() => void forget(name)}>
                          Forget
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card">
            <div className="card-header">
              <h2>Holdings</h2>
              <span className="sub">Values as of the export, not live prices</span>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th className="hide-sm">Account</th>
                    <th className="num">Quantity</th>
                    <th className="num">Value</th>
                    <th className="num">Cost</th>
                    <th className="num">Gain / loss</th>
                  </tr>
                </thead>
                <tbody>
                  {h.holdings.map((x, i) => {
                    const gain = isNum(x.value) && isNum(x.cost_basis) && !x.cash ? x.value - x.cost_basis : null;
                    return (
                      <tr key={`${x.account}-${x.symbol}-${i}`}>
                        <td>{x.cash ? "Cash" : x.symbol}</td>
                        <td className="hide-sm">{x.account}</td>
                        <td className="num">{x.cash ? "–" : fmtNum(x.quantity, 3)}</td>
                        <td className="num">{fmtMoney(x.value)}</td>
                        <td className="num">{x.cash ? "–" : fmtMoney(x.cost_basis)}</td>
                        <td className={`num ${pnlClass(gain)}`}>{fmtMoney(gain, 0, true)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn" onClick={() => void forget()}>
                Forget all holdings
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
