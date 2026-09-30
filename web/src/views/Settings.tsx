import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { Meta, Settings } from "../types";
import { ErrorBox, EvidenceBadge, Help, StatusDot } from "../components/ui";
import { applyCandles, applyTheme, loadCandles, loadTheme, type CandleChoice, type ThemeChoice } from "../theme";

export default function SettingsView() {
  const { meta, settings, saveSettings, refreshMeta, toast } = useApp() as {
    meta: Meta;
    settings: Settings;
    saveSettings: (p: Record<string, unknown>) => Promise<Settings>;
    refreshMeta: () => Promise<void>;
    toast: (m: string) => void;
  };
  const [draft, setDraft] = useState<Settings>(settings);
  const [error, setError] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; message: string } | "running">>({});
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme());
  const [candles, setCandles] = useState<CandleChoice>(loadCandles());

  useEffect(() => setDraft(settings), [settings]);

  const save = async (patch: Record<string, unknown>, message = "Settings saved") => {
    setError(null);
    try {
      await saveSettings(patch);
      await refreshMeta();
      toast(message);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const test = async (id: string) => {
    setTests((t) => ({ ...t, [id]: "running" }));
    try {
      const r = await api.testProvider(id);
      setTests((t) => ({ ...t, [id]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [id]: { ok: false, message: (e as Error).message } }));
    }
  };

  const num = (key: keyof Settings, scale = 1) => ({
    value: String(Number(draft[key]) * scale),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setDraft({ ...draft, [key]: e.target.value === "" ? 0 : Number(e.target.value) / scale }),
  });

  const advisorIds = new Set(draft.advisors.map((a) => a.id));
  const toggleAdvisor = (id: string, on: boolean) => {
    const next = on ? [...draft.advisors, { id, params: {} }] : draft.advisors.filter((a) => a.id !== id);
    setDraft({ ...draft, advisors: next });
  };

  return (
    <div className="stack" style={{ maxWidth: 1100 }}>
      <div>
        <h1>Settings</h1>
        <p className="secondary" style={{ marginTop: 4 }}>
          Stored on this computer only ({draft.csv_dir_resolved.replace(/data$/, "")}settings.json). API keys are sent only to the
          provider they belong to and are never shown again after saving.
        </p>
      </div>
      <ErrorBox error={error} />

      <div className="card">
        <div className="card-header">
          <h2>Market data source</h2>
          <span className="sub">Used by the Live Desk and as the default elsewhere</span>
        </div>
        <div className="col">
          {meta.providers.map((p) => {
            const t = tests[p.id];
            return (
              <div key={p.id} className="vote-card">
                <div className="vote-head">
                  <label className="check title">
                    <input
                      type="radio"
                      name="provider"
                      checked={draft.provider === p.id}
                      onChange={() => setDraft({ ...draft, provider: p.id })}
                    />
                    {p.label}
                  </label>
                  <span className="badge neutral">{p.realtime}</span>
                  {p.requires_key ? (
                    <StatusDot status={p.configured ? "good" : "warn"}>{p.configured ? "Key saved" : "Needs API key"}</StatusDot>
                  ) : null}
                  <button className="btn small" onClick={() => void test(p.id)} disabled={t === "running"}>
                    {t === "running" ? "Testing..." : "Test connection"}
                  </button>
                </div>
                <p className="small secondary" style={{ margin: "6px 0 0" }}>
                  {p.description} {p.docs_url ? <a href={p.docs_url} target="_blank" rel="noreferrer">Docs</a> : null}
                </p>
                {t && t !== "running" ? (
                  <div className="small" style={{ marginTop: 6 }}>
                    <StatusDot status={t.ok ? "good" : "bad"}>{t.ok ? "Connected" : "Failed"}</StatusDot> <span className="secondary">{t.message}</span>
                  </div>
                ) : null}
                {p.id === "alpaca" ? (
                  <div className="form-grid" style={{ marginTop: 10 }}>
                    <label className="field">
                      <span>API key ID</span>
                      <input className="input" value={draft.alpaca_key_id} autoComplete="off" onChange={(e) => setDraft({ ...draft, alpaca_key_id: e.target.value })} />
                    </label>
                    <label className="field">
                      <span>Secret key</span>
                      <input
                        className="input"
                        type="password"
                        autoComplete="new-password"
                        value={draft.alpaca_secret_key}
                        onChange={(e) => setDraft({ ...draft, alpaca_secret_key: e.target.value })}
                      />
                    </label>
                    <label className="field">
                      <span>Feed</span>
                      <select className="input" value={draft.alpaca_feed} onChange={(e) => setDraft({ ...draft, alpaca_feed: e.target.value })}>
                        <option value="iex">IEX (free)</option>
                        <option value="sip">SIP (paid)</option>
                        <option value="delayed_sip">SIP, 15-min delay</option>
                      </select>
                    </label>
                  </div>
                ) : null}
                {p.id === "csv" ? (
                  <label className="field" style={{ marginTop: 10 }}>
                    <span>CSV folder (blank = default)</span>
                    <input className="input" value={draft.csv_dir} placeholder={draft.csv_dir_resolved} onChange={(e) => setDraft({ ...draft, csv_dir: e.target.value })} />
                  </label>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label className="field">
            <span>Live bar size</span>
            <select className="input" value={draft.timeframe} onChange={(e) => setDraft({ ...draft, timeframe: e.target.value })}>
              {meta.timeframes.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Poll every (seconds)</span>
            <input className="input num" type="number" min={2} {...num("poll_seconds")} />
          </label>
          <label className="field" title="Simulated seconds per real second in the synthetic demo market.">
            <span>Demo clock speed (x)</span>
            <input className="input num" type="number" min={1} {...num("demo_speed")} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            onClick={() =>
              void save(
                {
                  provider: draft.provider,
                  alpaca_key_id: draft.alpaca_key_id,
                  alpaca_secret_key: draft.alpaca_secret_key,
                  alpaca_feed: draft.alpaca_feed,
                  csv_dir: draft.csv_dir,
                  timeframe: draft.timeframe,
                  poll_seconds: draft.poll_seconds,
                  demo_speed: draft.demo_speed,
                },
                "Data settings saved; the live feed is reloading.",
              )
            }
          >
            Save data settings
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Risk and sizing</h2>
          <span className="sub">How the Live Desk sizes its suggested positions</span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>Account size ($)</span>
            <input className="input num" type="number" {...num("account_equity")} />
          </label>
          <label className="field">
            <span>
              Risk per trade (%) <Help text="Loss if the protective stop is hit, as a share of the account. 0.5-2% is typical." />
            </span>
            <input className="input num" type="number" step={0.1} {...num("risk_per_trade", 100)} />
          </label>
          <label className="field">
            <span>Stop distance (ATRs)</span>
            <input className="input num" type="number" step={0.25} {...num("stop_atr")} />
          </label>
          <label className="field">
            <span>Max position (% of account)</span>
            <input className="input num" type="number" step={1} {...num("max_position_pct", 100)} />
          </label>
          <label className="field">
            <span>Slippage (bps)</span>
            <input className="input num" type="number" step={0.5} {...num("slippage_bps")} />
          </label>
          <label className="field">
            <span>Commission (bps)</span>
            <input className="input num" type="number" step={0.5} {...num("commission_bps")} />
          </label>
        </div>
        <label className="check" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={draft.allow_short} onChange={(e) => setDraft({ ...draft, allow_short: e.target.checked })} />
          Allow short-selling recommendations
        </label>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            onClick={() =>
              void save({
                account_equity: draft.account_equity,
                risk_per_trade: draft.risk_per_trade,
                stop_atr: draft.stop_atr,
                max_position_pct: draft.max_position_pct,
                slippage_bps: draft.slippage_bps,
                commission_bps: draft.commission_bps,
                allow_short: draft.allow_short,
              })
            }
          >
            Save risk settings
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Strategies used for live recommendations</h2>
          <span className="sub">Each enabled strategy casts one vote per symbol</span>
        </div>
        <div className="col" style={{ gap: 6 }}>
          {meta.strategies
            .filter((s) => s.id !== "buy_hold")
            .map((s) => (
              <label key={s.id} className="check">
                <input type="checkbox" checked={advisorIds.has(s.id)} onChange={(e) => toggleAdvisor(s.id, e.target.checked)} />
                <span>{s.name}</span>
                <EvidenceBadge level={s.evidence} />
                <span className="small muted">{s.kind === "cross_sectional" ? "ranks the watchlist" : s.kind === "pair" ? "needs a configured pair" : ""}</span>
              </label>
            ))}
        </div>
        <h3 className="section-title" style={{ marginTop: 14 }}>
          Pairs for pairs trading
        </h3>
        <div className="col" style={{ gap: 6 }}>
          {draft.pairs.map((p, i) => (
            <div className="row tight" key={i}>
              <input className="input" style={{ width: 110 }} value={p[0]} onChange={(e) => {
                const next = draft.pairs.map((x) => [...x]);
                next[i][0] = e.target.value.toUpperCase();
                setDraft({ ...draft, pairs: next });
              }} aria-label="Pair leg A" />
              <span className="muted">vs</span>
              <input className="input" style={{ width: 110 }} value={p[1]} onChange={(e) => {
                const next = draft.pairs.map((x) => [...x]);
                next[i][1] = e.target.value.toUpperCase();
                setDraft({ ...draft, pairs: next });
              }} aria-label="Pair leg B" />
              <button className="btn small ghost" onClick={() => setDraft({ ...draft, pairs: draft.pairs.filter((_, j) => j !== i) })}>
                Remove
              </button>
            </div>
          ))}
          <div>
            <button className="btn small" onClick={() => setDraft({ ...draft, pairs: [...draft.pairs, ["", ""]] })}>
              Add pair
            </button>
          </div>
          <p className="small muted">Both legs must be in your watchlist. Classic real-market examples: KO/PEP, XOM/CVX, GLD/GDX.</p>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            onClick={() => void save({ advisors: draft.advisors, pairs: draft.pairs.filter((p) => p[0] && p[1]) })}
          >
            Save strategies
          </button>
        </div>
      </div>

      {meta.auth_enabled ? (
        <div className="card">
          <div className="card-header">
            <h2>Access</h2>
          </div>
          <p className="secondary">
            This server is password-protected. Your login lasts 30 days on this browser; changing the server&apos;s password logs
            everyone out.
          </p>
          <div className="row" style={{ marginTop: 10 }}>
            <button
              className="btn"
              onClick={async () => {
                await api.logout();
                window.location.assign("/login");
              }}
            >
              Log out
            </button>
          </div>
        </div>
      ) : null}

      <div className="card">
        <div className="card-header">
          <h2>Appearance</h2>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>Theme</span>
            <select
              className="input"
              value={theme}
              onChange={(e) => {
                const t = e.target.value as ThemeChoice;
                setTheme(t);
                applyTheme(t);
              }}
            >
              <option value="system">Match system</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
          <label className="field">
            <span>
              Up / down colours{" "}
              <Help text="Blue/red stays distinguishable for people with red-green colour-vision deficiency (about 8% of men); green/red is the traditional trading convention." />
            </span>
            <select
              className="input"
              value={candles}
              onChange={(e) => {
                const c = e.target.value as CandleChoice;
                setCandles(c);
                applyCandles(c);
              }}
            >
              <option value="bluered">Blue / red</option>
              <option value="classic">Green / red</option>
            </select>
          </label>
        </div>
      </div>
    </div>
  );
}
