import { useEffect, useRef, useState } from "react";
import { useApp } from "../state";
import type { Settings } from "../types";
import { ErrorBox } from "./ui";

// Federal ordinary-income brackets and the long-term capital gains rate that usually goes with
// each (approximate: the thresholds of the two schedules do not line up exactly).
const BRACKETS: [number, number][] = [
  [0.1, 0],
  [0.12, 0],
  [0.22, 0.15],
  [0.24, 0.15],
  [0.32, 0.15],
  [0.35, 0.15],
  [0.37, 0.2],
];

const STEPS = ["Welcome", "Prices", "Your account", "What to show"];

/** The first-run guide: four short questions, all changeable later in Settings. */
export default function Onboarding({ settings }: { settings: Settings }) {
  const { saveSettings, navigate } = useApp();
  const ref = useRef<HTMLDialogElement>(null);
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState({
    provider: settings.provider === "alpaca" ? "alpaca" : settings.provider === "synthetic" ? "synthetic" : "yahoo",
    account_equity: settings.account_equity,
    account_type: settings.account_type,
    short_term_tax_rate: settings.short_term_tax_rate,
    long_term_tax_rate: settings.long_term_tax_rate,
    state_tax_rate: settings.state_tax_rate,
    ui_mode: "simple" as Settings["ui_mode"],
  });

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  const finish = async (patch: Record<string, unknown>) => {
    setError(null);
    try {
      await saveSettings({ ...patch, onboarded: true });
      ref.current?.close();
      navigate("/today");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const bracket = BRACKETS.find(([st]) => Math.abs(st - draft.short_term_tax_rate) < 1e-9)?.[0] ?? "";

  return (
    <dialog ref={ref} className="dialog" aria-labelledby="onboarding-title" onCancel={(e) => e.preventDefault()}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="small muted">
          Step {step + 1} of {STEPS.length}: {STEPS[step]}
        </span>
        <button className="btn" onClick={() => void finish({})}>
          Skip
        </button>
      </div>
      <ErrorBox error={error} />
      {step === 0 ? (
        <>
          <h2 id="onboarding-title">Welcome to Trades</h2>
          <p>Trades tests trading strategies honestly and shows what they suggest today. Before you start:</p>
          <ul>
            <li>
              <b>Nothing here is proven.</b> Whether its calls beat simply buying and holding SPY is exactly what the app is trying to
              find out, with a forward test that takes years. The Track Record page shows how far it has got.
            </li>
            <li>
              <b>It never places real orders.</b> At most it trades a practice (paper) account.
            </li>
            <li>
              <b>It is not advice.</b> It knows nothing about your goals, debts or other savings.
            </li>
          </ul>
        </>
      ) : null}
      {step === 1 ? (
        <>
          <h2 id="onboarding-title">Which prices?</h2>
          <div className="col">
            {[
              ["yahoo", "Real prices from Yahoo Finance", "Free, no account needed, delayed about 15 minutes. The usual choice."],
              ["alpaca", "Real-time prices from Alpaca", "Needs a free Alpaca key: add it under Settings afterwards."],
              ["synthetic", "The demo market", "Made-up prices that run offline: good for learning the app, meaningless for real decisions."],
            ].map(([id, title, text]) => (
              <label key={id} className="check" style={{ alignItems: "flex-start" }}>
                <input type="radio" name="provider" checked={draft.provider === id} onChange={() => setDraft({ ...draft, provider: id })} />
                <span>
                  <b>{title}</b>
                  <br />
                  <span className="small muted">{text}</span>
                </span>
              </label>
            ))}
          </div>
        </>
      ) : null}
      {step === 2 ? (
        <>
          <h2 id="onboarding-title">Your account</h2>
          <p className="small muted">Used to size suggestions and to compare results after tax. Estimates are fine.</p>
          <div className="form-grid">
            <label className="field">
              <span>Amount you would invest ($)</span>
              <input
                className="input num"
                type="number"
                min={100}
                step={1000}
                value={draft.account_equity}
                onChange={(e) => setDraft({ ...draft, account_equity: Number(e.target.value) })}
              />
            </label>
            <label className="field">
              <span>Account type</span>
              <select
                className="input"
                value={draft.account_type}
                onChange={(e) => setDraft({ ...draft, account_type: e.target.value as Settings["account_type"] })}
              >
                <option value="taxable">Taxable brokerage account</option>
                <option value="tax_advantaged">IRA, 401(k) or similar</option>
              </select>
            </label>
            <label className="field">
              <span>Federal tax bracket</span>
              <select
                className="input"
                disabled={draft.account_type !== "taxable"}
                value={bracket}
                onChange={(e) => {
                  const pick = BRACKETS.find(([st]) => String(st) === e.target.value);
                  if (pick) setDraft({ ...draft, short_term_tax_rate: pick[0], long_term_tax_rate: pick[1] });
                }}
              >
                {bracket === "" ? <option value="">Custom ({Math.round(draft.short_term_tax_rate * 100)}%)</option> : null}
                {BRACKETS.map(([st, lt]) => (
                  <option key={st} value={st}>
                    {Math.round(st * 100)}% (long-term gains {Math.round(lt * 100)}%)
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>State tax on gains (%)</span>
              <input
                className="input num"
                type="number"
                min={0}
                max={20}
                step={0.5}
                disabled={draft.account_type !== "taxable"}
                value={Math.round(draft.state_tax_rate * 1000) / 10}
                onChange={(e) => setDraft({ ...draft, state_tax_rate: Number(e.target.value) / 100 })}
              />
            </label>
          </div>
          <p className="small muted">
            Long-term rates are approximate. Above about $200,000 of income ($250,000 married) the 3.8% net investment income tax
            applies too: add it to both rates under Settings.
          </p>
        </>
      ) : null}
      {step === 3 ? (
        <>
          <h2 id="onboarding-title">How much should the app show?</h2>
          <div className="col">
            {[
              ["simple", "Essentials", "Today, your portfolio, planning tools, the track record and the library: what you need to decide whether to use it."],
              ["full", "Everything", "Adds the Live Desk, the Strategy Lab and the simulators, for researching strategies yourself."],
            ].map(([id, title, text]) => (
              <label key={id} className="check" style={{ alignItems: "flex-start" }}>
                <input
                  type="radio"
                  name="ui_mode"
                  checked={draft.ui_mode === id}
                  onChange={() => setDraft({ ...draft, ui_mode: id as Settings["ui_mode"] })}
                />
                <span>
                  <b>{title}</b>
                  <br />
                  <span className="small muted">{text}</span>
                </span>
              </label>
            ))}
          </div>
          <p className="small muted">You can switch at any time under Settings.</p>
        </>
      ) : null}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
        {step > 0 ? (
          <button className="btn" onClick={() => setStep(step - 1)}>
            Back
          </button>
        ) : null}
        {step < STEPS.length - 1 ? (
          <button className="btn primary" onClick={() => setStep(step + 1)}>
            Next
          </button>
        ) : (
          <button className="btn primary" onClick={() => void finish(draft)}>
            Done
          </button>
        )}
      </div>
    </dialog>
  );
}
