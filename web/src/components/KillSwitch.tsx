import { useEffect, useState } from "react";
import { api } from "../api";
import type { KillSwitch } from "../types";
import { ErrorBox, StatusDot } from "./ui";

/** The kill switch (the HALT file): on, nothing trades; off is a deliberate choice, so it asks first. */
export function KillSwitchControl({ initial, onChange }: { initial?: KillSwitch; onChange?: (k: KillSwitch) => void }) {
  const [state, setState] = useState<KillSwitch | null>(initial ?? null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initial) setState(initial);
    else api.killSwitch().then(setState, (e: Error) => setError(e.message));
  }, [initial]);

  const flip = async (on: boolean) => {
    if (!on && !window.confirm("Turn the kill switch off? Paper runners will start sending orders again at their next run.")) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.setKillSwitch(on, reason);
      setState(next);
      setReason("");
      onChange?.(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!state) return error ? <ErrorBox error={error} /> : null;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ alignItems: "center" }}>
        <StatusDot status={state.on ? "bad" : "good"}>{state.on ? "Kill switch ON: nothing trades" : "Kill switch off"}</StatusDot>
        {state.on ? (
          <button className="btn" disabled={busy} onClick={() => void flip(false)}>
            Turn off
          </button>
        ) : (
          <>
            <input
              className="input"
              style={{ width: 280, maxWidth: "100%" }}
              placeholder="Why (optional)"
              value={reason}
              maxLength={300}
              onChange={(e) => setReason(e.target.value)}
            />
            <button className="btn danger" disabled={busy} onClick={() => void flip(true)}>
              Turn on
            </button>
          </>
        )}
      </div>
      {state.on && state.reason ? <div className="small muted">{state.reason}</div> : null}
      <p className="small muted" style={{ margin: 0 }}>
        The switch is a file named <code>HALT</code> in the repository: every paper runner checks it before every order. It stops new
        orders; it does not close positions (<code>trades paper flatten</code> does). To stop the scheduled GitHub job too, commit the
        file.
      </p>
      <ErrorBox error={error} />
    </div>
  );
}
