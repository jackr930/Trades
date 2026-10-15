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

  // When saved settings change, refresh only the fields that were saved: unsaved edits in the
  // other cards survive saving one card.
  const [saved, setSaved] = useState<Settings>(settings);
  useEffect(() => {
    setDraft((d) => {
      const next = { ...d } as Record<string, unknown>;
      for (const k of Object.keys(settings) as (keyof Settings)[]) {
        if (JSON.stringify(settings[k]) !== JSON.stringify(saved[k])) next[k] = settings[k];
      }
      return next as unknown as Settings;
    });
    setSaved(settings);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

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
    value: String(Math.round(Number(draft[key]) * scale * 1e6) / 1e6), // 1.15 x 100 is 114.99999999999999
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
          <h2>Your account</h2>
          <span className="sub">The default for the Strategy Lab, the simulator, practice mode and the Live Desk</span>
        </div>
        <div className="form-grid">
          <label className="field">
            <span>Starting equity ($)</span>
            <input className="input num" type="number" min={100} {...num("account_equity")} />
          </label>
          <label className="field">
            <span>Account type</span>
            <select
              className="input"
              value={draft.account_type}
              onChange={(e) => setDraft({ ...draft, account_type: e.target.value as Settings["account_type"] })}
            >
              <option value="taxable">Taxable (brokerage account)</option>
              <option value="tax_advantaged">Tax-advantaged (IRA, 401(k))</option>
            </select>
          </label>
          <label className="field">
            <span>
              Short-term tax rate (%){" "}
              <Help text="Federal tax on gains held one year or less (taxed like income). 22% is only an estimate: replace it with your own bracket." />
            </span>
            <input className="input num" type="number" step={1} disabled={draft.account_type !== "taxable"} {...num("short_term_tax_rate", 100)} />
          </label>
          <label className="field">
            <span>
              Long-term tax rate (%){" "}
              <Help text="Federal tax on gains held more than a year (0%, 15% or 20% depending on income). 15% is only an estimate: replace it with your own." />
            </span>
            <input className="input num" type="number" step={1} disabled={draft.account_type !== "taxable"} {...num("long_term_tax_rate", 100)} />
          </label>
          <label className="field">
            <span>
              State tax rate (%) <Help text="A flat state rate on all capital gains, added to the federal rates. 0 if your state has no income tax." />
            </span>
            <input className="input num" type="number" step={0.5} disabled={draft.account_type !== "taxable"} {...num("state_tax_rate", 100)} />
          </label>
          <label className="field">
            <span>
              Idle cash earns <Help text="T-bills: cash a strategy is not using earns what a 1-3 month T-bill ETF (BIL) returned, as it could in a real account. Sharpe ratios are then in excess of T-bills." />
            </span>
            <select className="input" value={draft.cash_yield} onChange={(e) => setDraft({ ...draft, cash_yield: e.target.value as Settings["cash_yield"] })}>
              <option value="tbill">T-bill returns (BIL)</option>
              <option value="none">Nothing</option>
            </select>
          </label>
        </div>
        <label className="check" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={draft.fractional_shares} onChange={(e) => setDraft({ ...draft, fractional_shares: e.target.checked })} />
          My broker supports fractional shares
        </label>
        <p className="small muted" style={{ marginTop: 8 }}>
          The tax rates are estimates for illustration; replace them with your own federal bracket. After-tax results use
          average cost (not tax lots), use one flat state rate, ignore wash sales, and never offset losses against other income.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            onClick={() =>
              void save({
                account_equity: draft.account_equity,
                fractional_shares: draft.fractional_shares,
                account_type: draft.account_type,
                short_term_tax_rate: draft.short_term_tax_rate,
                long_term_tax_rate: draft.long_term_tax_rate,
                state_tax_rate: draft.state_tax_rate,
                cash_yield: draft.cash_yield,
              })
            }
          >
            Save account
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
            <span>
              Portfolio cap (% of account){" "}
              <Help text="All suggested positions together. If they add up to more, every suggestion shrinks in proportion. 100% means no borrowing." />
            </span>
            <input className="input num" type="number" step={5} {...num("max_gross_exposure", 100)} />
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
                risk_per_trade: draft.risk_per_trade,
                stop_atr: draft.stop_atr,
                max_position_pct: draft.max_position_pct,
                max_gross_exposure: draft.max_gross_exposure,
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
            .filter((s) => s.id !== "buy_hold" && !s.sizes_itself)
            .map((s) => (
              <label key={s.id} className="check">
                <input type="checkbox" checked={advisorIds.has(s.id)} onChange={(e) => toggleAdvisor(s.id, e.target.checked)} />
                <span>{s.name}</span>
                <EvidenceBadge level={s.evidence} />
                <span className="small muted">{s.kind === "cross_sectional" ? "ranks the watchlist" : s.kind === "pair" ? "needs a configured pair" : ""}</span>
              </label>
            ))}
        </div>
        <label className="field" style={{ marginTop: 14, maxWidth: 420 }}>
          <span>
            Combine the votes{" "}
            <Help text="Equal: every strategy's vote counts once, so four trend rules that agree count four times. By category: votes are averaged within each category (trend, mean reversion, momentum, ...) first, then the categories are averaged." />
          </span>
          <select
            className="input"
            value={draft.consensus_weighting}
            onChange={(e) => setDraft({ ...draft, consensus_weighting: e.target.value as Settings["consensus_weighting"] })}
          >
            <option value="equal">Equally (one vote per strategy)</option>
            <option value="by_category">By category (one vote per kind of strategy)</option>
          </select>
        </label>
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
            onClick={() =>
              void save({
                advisors: draft.advisors,
                pairs: draft.pairs.filter((p) => p[0] && p[1]),
                consensus_weighting: draft.consensus_weighting,
              })
            }
          >
            Save strategies
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Paper trading (Alpaca paper account)</h2>
          <span className="sub">Practice money only: there is no live-trading endpoint in this app</span>
        </div>
        <dl className="kv">
          <dt>Endpoint</dt>
          <dd>
            <code>https://paper-api.alpaca.markets</code> (fixed)
          </dd>
          <dt>API key</dt>
          <dd>{draft.has_alpaca_credentials ? "Uses your Alpaca key above" : "Add an Alpaca paper key under Market data source"}</dd>
        </dl>
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label className="field">
            <span>
              Max daily loss (%) <Help text="No new orders if the paper account fell more than this since the prior close." />
            </span>
            <input className="input num" type="number" step={0.5} {...num("paper_max_daily_loss", 100)} />
          </label>
          <label className="field">
            <span>Max orders per run</span>
            <input className="input num" type="number" step={1} min={1} {...num("paper_max_orders")} />
          </label>
        </div>
        <label className="check" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={draft.paper_halted} onChange={(e) => setDraft({ ...draft, paper_halted: e.target.checked })} />
          Kill switch: send no paper orders
        </label>
        <p className="small muted" style={{ marginTop: 8 }}>
          Orders are planned and sent from the command line: <code>trades paper</code> prints them, <code>trades paper --submit</code>{" "}
          sends them, <code>trades paper halt</code> turns the kill switch on (and writes <code>journal/PAPER_HALTED</code>, which
          stops the GitHub Actions job once committed). Set your Alpaca paper balance to the amount you would really trade.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            onClick={() =>
              void save({
                paper_halted: draft.paper_halted,
                paper_max_daily_loss: draft.paper_max_daily_loss,
                paper_max_orders: draft.paper_max_orders,
              })
            }
          >
            Save paper trading
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Track record source</h2>
          <span className="sub">Where the Track Record page reads the committed journal</span>
        </div>
        <label className="field">
          <span>
            GitHub address <Help text="Leave empty to read the journal/ folder next to where the server runs. A hosted copy never sees the workflow's later commits, so point it at your repository instead." />
          </span>
          <input
            className="input"
            placeholder="https://raw.githubusercontent.com/<you>/Trades/<branch>/journal"
            value={draft.journal_source}
            onChange={(e) => setDraft({ ...draft, journal_source: e.target.value.trim() })}
          />
        </label>
        <p className="small muted" style={{ marginTop: 8 }}>
          Only <code>raw.githubusercontent.com</code> addresses are accepted. For a private repository, give the server a read-only
          GitHub token in the <code>TRADES_JOURNAL_TOKEN</code> environment variable.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={() => void save({ journal_source: draft.journal_source })}>
            Save source
          </button>
        </div>
      </div>

      <BackupCard onRestored={() => void refreshMeta()} toast={toast} />

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
        <div className="col" style={{ marginBottom: 12 }}>
          <label className="check">
            <input
              type="radio"
              name="ui_mode"
              checked={settings.ui_mode === "simple"}
              onChange={() => void save({ ui_mode: "simple" }, "Showing the essentials")}
            />
            Essentials: Today, Portfolio, Plan, Track Record, Library and Settings
          </label>
          <label className="check">
            <input type="radio" name="ui_mode" checked={settings.ui_mode === "full"} onChange={() => void save({ ui_mode: "full" }, "Showing everything")} />
            Everything: adds the Live Desk, the Strategy Lab and the simulators
          </label>
          <div>
            <button className="btn" onClick={() => void save({ onboarded: false }, "Starting the guide")}>
              Run the first-run guide again
            </button>
          </div>
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

/** Download everything this server remembers (except API keys) and load it back, e.g. after a redeploy. */
function BackupCard({ onRestored, toast }: { onRestored: () => void; toast: (m: string) => void }) {
  const { refreshSettings } = useApp();
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    setError(null);
    try {
      const backup = await api.backup();
      const blob = new Blob([JSON.stringify(backup, null, 1)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `trades-backup-${backup.created.slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const restore = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    try {
      const r = await api.restore(JSON.parse(await file.text()));
      await refreshSettings();
      onRestored();
      const lines = Object.values(r.lines_added).reduce((a, b) => a + b, 0);
      toast(`Restored ${r.settings.length} settings and ${lines} log entries`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const wipe = async () => {
    const typed = window.prompt('This deletes your settings, API keys, holdings, research log and simulator history from this server. Type "delete everything" to confirm.');
    if (typed !== "delete everything") return;
    setError(null);
    try {
      const r = await api.deleteMyData();
      await refreshSettings();
      onRestored();
      toast(`Deleted ${r.deleted.join(", ")}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>Your data</h2>
        <span className="sub">Settings, holdings, the research log and simulator history (backups never include API keys)</span>
      </div>
      <ErrorBox error={error} />
      <p className="secondary" style={{ marginTop: 0 }}>
        A hosted copy without a disk forgets everything when it restarts, including the research log that counts how many
        configurations you have tried (which the Deflated Sharpe Ratio needs to be honest). Download a backup now and then; restoring
        adds back anything missing and never deletes. See <a href="#/privacy">Privacy</a> for what is kept and where it goes.
      </p>
      <div className="row">
        <button className="btn" onClick={() => void download()}>
          Download all my data
        </button>
        <label className="btn">
          Restore from file
          <input type="file" accept="application/json,.json" style={{ display: "none" }} onChange={(e) => void restore(e.target.files?.[0])} />
        </label>
        <button className="btn danger" onClick={() => void wipe()}>
          Delete everything
        </button>
      </div>
    </div>
  );
}
