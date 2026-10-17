import { useEffect, useState, type KeyboardEvent } from "react";
import { api } from "../api";
import { signalsChanged } from "../state";
import type { KillSwitch } from "../types";
import { ErrorBox, Help, Spinner } from "./ui";
import { InlineText, Readout } from "./instrument";
import "../styles/pipeline.css";

/** The HALT file's own words, "Trading halted 2026-10-17 16:55 UTC: why", as a time and a reason. */
function splitReason(reason: string): { since: string | null; why: string } {
  const m = /^Trading halted (.+? UTC):\s*([\s\S]*)$/.exec(reason.trim());
  if (m) return { since: m[1], why: m[2].trim() || "no reason given" };
  return { since: null, why: reason.trim() || "no reason given" };
}

/**
 * The kill switch (the HALT file) as an instrument: its state in big letters, why it is on, and the
 * one action. On, nothing trades. Off is a deliberate choice, so turning it off asks first.
 * Used by the Pipeline (in a panel) and by Settings (under its own heading).
 */
export function KillSwitchControl({ initial, onChange }: { initial?: KillSwitch; onChange?: (k: KillSwitch) => void }) {
  const [state, setState] = useState<KillSwitch | null>(initial ?? null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (initial) setState(initial);
    else api.killSwitch().then(setState, (e: Error) => setError(e.message));
  }, [initial]);

  const flip = async (on: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const next = await api.setKillSwitch(on, reason);
      setState(next);
      setReason("");
      setConfirming(false);
      onChange?.(next);
      signalsChanged(); // the top bar's HALT ON/OFF follows at once
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!state) return error ? <ErrorBox error={error} /> : <Spinner label="Reading the kill switch..." />;
  const { since, why } = splitReason(state.reason);
  const cancelOnEscape = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") setConfirming(false);
  };
  return (
    <div className={`pl-ks${state.on ? " on" : ""}`}>
      <div className="pl-ks-state" role="status" aria-live="polite">
        <span className="pl-ks-lamp" aria-hidden="true" />
        <Readout
          value={state.on ? "HALT ON" : "HALT OFF"}
          tone={state.on ? "accent" : undefined}
          label={state.on ? "Nothing trades" : "Paper orders allowed"}
        />
      </div>

      {state.on ? (
        <dl className="kv-grid pl-ks-kv">
          <dt>Reason</dt>
          <dd>{why}</dd>
          {since ? (
            <>
              <dt>Since</dt>
              <dd>{since}</dd>
            </>
          ) : null}
        </dl>
      ) : null}

      {state.on ? (
        confirming ? (
          <div className="pl-ks-confirm" role="group" aria-label="Confirm turning the kill switch off" onKeyDown={cancelOnEscape}>
            <p>Turn the kill switch off? Paper runners will start sending orders again at their next run.</p>
            <div className="pl-ks-row">
              <button className="btn danger" disabled={busy} autoFocus onClick={() => void flip(false)}>
                {busy ? "Turning off..." : "Yes, turn it off"}
              </button>
              <button className="btn ghost" disabled={busy} onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="pl-ks-row">
            <button className="btn" disabled={busy} onClick={() => setConfirming(true)}>
              Turn off
            </button>
          </div>
        )
      ) : (
        <div className="pl-ks-row pl-ks-on">
          <label className="field pl-ks-field">
            <span>Reason</span>
            <input
              className="input"
              placeholder="Why (optional)"
              value={reason}
              maxLength={300}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <button className="btn danger" disabled={busy} onClick={() => void flip(true)}>
            {busy ? "Turning on..." : "Turn on"}
          </button>
        </div>
      )}

      <p className="pl-ks-note">
        <InlineText text="Stops new paper orders; it does not close positions (`trades paper flatten` does)." />{" "}
        <Help text="The switch is a file named HALT in the repository: every paper runner checks it before every order. To stop the scheduled GitHub job too, commit the file." />
      </p>
      <ErrorBox error={error} />
    </div>
  );
}
