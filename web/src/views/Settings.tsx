import { useEffect, useRef, useState, type InputHTMLAttributes, type ReactNode } from "react";
import { api } from "../api";
import { useApp, useRoute, useStatusLine } from "../state";
import type { KillSwitch, Meta, ProviderInfo, Settings } from "../types";
import { ErrorBox, EvidenceBadge, Help, StatusDot } from "../components/ui";
import { KeyValues, Panel, Readout, Readouts, Tag } from "../components/instrument";
import { KillSwitchControl } from "../components/KillSwitch";
import { fmtMoney } from "../format";
import { applyCandles, applyTheme, loadCandles, loadTheme, type CandleChoice, type ThemeChoice } from "../theme";
import "../styles/settings.css";

/*
 * Settings answers: where does my data come from, and what protects me?
 *
 * A rail of sections on the left (#/settings?section=data|account|desk|paper|appearance|access), the
 * section's panels on the right, and one ink SAVE per section. Edits are a draft until saved; the rail
 * marks a section with unsaved changes, and saving one section never discards edits in another.
 */

type SectionId = "data" | "account" | "desk" | "paper" | "appearance" | "access";
const SECTIONS: { id: SectionId; label: string; caption: string }[] = [
  { id: "data", label: "Data", caption: "Source, keys, live feed" },
  { id: "account", label: "Account", caption: "Equity, tax, risk, costs" },
  { id: "desk", label: "Live Desk", caption: "Voting strategies, pairs" },
  { id: "paper", label: "Paper trading", caption: "Kill switch, risk limits" },
  { id: "appearance", label: "Appearance", caption: "Theme, up/down colours" },
  { id: "access", label: "Access", caption: "Password, log out" },
];

const DATA_KEYS = [
  "provider",
  "alpaca_key_id",
  "alpaca_secret_key",
  "alpaca_feed",
  "massive_api_key",
  "massive_base_url",
  "csv_dir",
  "timeframe",
  "poll_seconds",
  "demo_speed",
] as const;
const ACCOUNT_KEYS = [
  "account_equity",
  "fractional_shares",
  "account_type",
  "short_term_tax_rate",
  "long_term_tax_rate",
  "risk_per_trade",
  "stop_atr",
  "max_position_pct",
  "max_gross_exposure",
  "slippage_bps",
  "commission_bps",
  "allow_short",
] as const;
const DESK_KEYS = ["advisors", "pairs", "consensus_weighting"] as const;
type Keys = readonly (keyof Settings)[];

const TF_WORD: Record<string, string> = { "1d": "daily", "1h": "hourly", "30m": "30-minute", "15m": "15-minute", "5m": "5-minute", "1m": "1-minute" };

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function parseSection(s: string | null): SectionId {
  return SECTIONS.some((x) => x.id === s) ? (s as SectionId) : "data";
}

/** "Synthetic demo market" -> "SYNTHETIC": the provider's name as a short machine word. */
function shortName(p: ProviderInfo | undefined, fallback: string): string {
  return (p?.label.split(" ")[0] ?? fallback).toUpperCase();
}

/** 0.015 -> "1.5" (percent, trimmed). */
function pctNum(x: unknown): string {
  return String(+(Number(x) * 100).toFixed(2));
}

// ---- small form parts --------------------------------------------------------------------------

/** A number input shown at a scale (e.g. a fraction as %), without float noise (0.7, not 0.7000000000000001).
 *  Empty means 0, as before; what is being typed is kept until the value changes from outside. */
function NumInput({
  value,
  onValue,
  scale = 1,
  ...rest
}: { value: number; onValue: (v: number) => void; scale?: number } & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange" | "type"
>) {
  const shown = (v: number) => (Number.isFinite(v) ? String(+(v * scale).toPrecision(12)) : "");
  const [text, setText] = useState(() => shown(value));
  // The last value this input typed or showed. A different value came from outside (a save, a
  // discard): show it. Adjusted during render, so there is no frame with the old text.
  const [own, setOwn] = useState(value);
  if (value !== own) {
    setOwn(value);
    setText(shown(value));
  }
  return (
    <input
      {...rest}
      className="input num"
      type="number"
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        const v = e.target.value === "" ? 0 : Number(e.target.value) / scale;
        if (Number.isFinite(v)) {
          setOwn(v);
          onValue(v);
        }
      }}
    />
  );
}

/** A form field: a mono-caps label above the control, help beside jargon, a plain hint below. */
function Field({ label, help, hint, children, className }: { label: ReactNode; help?: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={`field${className ? ` ${className}` : ""}`}>
      <span>
        {label}
        {help ? <Help text={help} /> : null}
      </span>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </label>
  );
}

/** A choice of a few words as a segmented control (labelled as a group: a <label> would capture clicks). */
function Choice<T extends string>({
  id,
  label,
  help,
  hint,
  value,
  options,
  onChange,
  disabled,
}: {
  id: string;
  label: ReactNode;
  help?: string;
  hint?: ReactNode;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="field" role="group" aria-labelledby={id}>
      <span id={id}>
        {label}
        {help ? <Help text={help} /> : null}
      </span>
      <div className="segmented set-seg">
        {options.map(([v, text]) => (
          <button key={v} type="button" aria-pressed={value === v} disabled={disabled} onClick={() => onChange(v)}>
            {text}
          </button>
        ))}
      </div>
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

/** An option tile, like the magnet tiles on a lab bench: status top-left, name, a few mono lines. */
function OptionTile({
  group,
  checked,
  onSelect,
  status,
  corner,
  name,
  lines,
  preview,
  foot,
}: {
  group: string;
  checked: boolean;
  onSelect: () => void;
  status: ReactNode;
  corner?: ReactNode;
  name: ReactNode;
  lines?: ReactNode[];
  preview?: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <div className={`set-tile${checked ? " on" : ""}`}>
      <label className="set-tile-pick">
        <input type="radio" name={group} checked={checked} onChange={onSelect} className="sr-only" />
        <span className="set-tile-top">
          <span className="set-tile-status">{status}</span>
          {corner ? <span className="set-tile-corner">{corner}</span> : null}
        </span>
        {preview}
        <span className="set-tile-name">{name}</span>
        {(lines ?? []).map((l, i) => (
          <span key={i} className="set-tile-line">
            {l}
          </span>
        ))}
      </label>
      {foot ? <div className="set-tile-foot">{foot}</div> : null}
    </div>
  );
}

/** The foot of every editable section: its one SAVE, what is unsaved, and what went wrong. */
function SaveBar({
  label,
  changed,
  saving,
  error,
  onSave,
  onDiscard,
}: {
  label: string;
  changed: number;
  saving: boolean;
  error: string | null;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className={`set-savebar${changed ? " dirty" : ""}`}>
      <div className="set-savebar-row">
        <button type="button" className="btn primary" disabled={saving} onClick={onSave}>
          {saving ? "Saving..." : label}
        </button>
        {changed ? (
          <>
            <span className="set-unsaved">
              <span className="set-sq" aria-hidden="true" />
              {changed} unsaved change{changed === 1 ? "" : "s"}
            </span>
            <button type="button" className="btn small ghost" disabled={saving} onClick={onDiscard}>
              Discard
            </button>
          </>
        ) : (
          <span className="set-clean">No unsaved changes</span>
        )}
      </div>
      <ErrorBox error={error} />
    </div>
  );
}

const THEMES: [ThemeChoice, string, string, string][] = [
  ["system", "◐ Auto", "Match system", "Follows your computer"],
  ["light", "○ Light", "Light", "Ink on paper"],
  ["dark", "● Dark", "Dark", "Designed for low light"],
];
const CANDLES: [CandleChoice, string, string, string][] = [
  ["bluered", "Default", "Blue / red", "Colour-blind safe"],
  ["classic", "Classic", "Green / red", "The trading tradition"],
];

const LIMITS: { key: string; label: string; kind: "pct" | "x" | "int" | "bool" | "list"; note?: string }[] = [
  { key: "max_risk_per_trade", label: "Risk per trade", kind: "pct", note: "the loss if the stop is hit" },
  { key: "daily_loss_limit", label: "Daily loss limit", kind: "pct", note: "then flatten; no new trades today" },
  { key: "weekly_loss_limit", label: "Weekly loss limit", kind: "pct", note: "then flatten; no new trades this week" },
  { key: "max_position_pct", label: "Largest position", kind: "pct", note: "any single position" },
  { key: "max_gross_exposure", label: "Gross exposure", kind: "x", note: "cap on all positions together" },
  { key: "max_open_positions", label: "Open positions", kind: "int", note: "at most" },
  { key: "max_orders_per_day", label: "Fills per day", kind: "int", note: "then exits only" },
  { key: "allow_short", label: "Short selling", kind: "bool" },
  { key: "allowed_symbols", label: "Allowed symbols", kind: "list" },
  { key: "blocked_symbols", label: "Blocked symbols", kind: "list" },
];

function showLimit(v: unknown, kind: string): string {
  if (kind === "pct") return `${(Number(v) * 100).toFixed(2).replace(/\.?0+$/, "")}% of equity`;
  if (kind === "x") return `${Number(v)}x equity`;
  if (kind === "bool") return v ? "allowed" : "off";
  if (kind === "list") return Array.isArray(v) && v.length ? v.join(", ") : "any in the spec's universe";
  return String(v);
}

// ---- the view ------------------------------------------------------------------------------------

export default function SettingsView() {
  const { meta, settings, saveSettings, refreshMeta, toast } = useApp() as {
    meta: Meta;
    settings: Settings;
    saveSettings: (p: Record<string, unknown>) => Promise<Settings>;
    refreshMeta: () => Promise<void>;
    toast: (m: string) => void;
  };
  const [route, navigate] = useRoute();
  const section = parseSection(route.params.get("section"));
  const go = (id: SectionId) => navigate("/settings", { section: id });

  const [draft, setDraft] = useState<Settings>(settings);
  const patch = (p: Partial<Settings>) => setDraft((d) => ({ ...d, ...p }));
  const base = useRef(settings);
  useEffect(() => {
    // New settings from the server: take them, but keep whatever the user is still editing elsewhere.
    const prev = base.current;
    base.current = settings;
    if (prev === settings) return;
    setDraft((d) => {
      const next: Record<string, unknown> = { ...settings };
      for (const k of Object.keys(settings) as (keyof Settings)[]) if (!same(d[k], prev[k])) next[k] = d[k];
      return next as unknown as Settings;
    });
  }, [settings]);

  const [saving, setSaving] = useState<SectionId | null>(null);
  const [failure, setFailure] = useState<{ section: SectionId; message: string } | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; message: string } | "running">>({});
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme());
  const [candles, setCandles] = useState<CandleChoice>(loadCandles());
  const [halt, setHalt] = useState<KillSwitch | null>(null);
  const [limits, setLimits] = useState<{ limits: Record<string, unknown>; file: string } | null>(null);
  const [limitsError, setLimitsError] = useState<string | null>(null);

  useEffect(() => {
    api.killSwitch().then(setHalt, () => undefined);
    api.riskLimits().then(setLimits, (e: Error) => setLimitsError(e.message));
  }, []);
  useEffect(() => {
    // The top bar's theme button changes the theme too: follow it.
    const sync = () => {
      setTheme(loadTheme());
      setCandles(loadCandles());
    };
    window.addEventListener("trades-theme", sync);
    return () => window.removeEventListener("trades-theme", sync);
  }, []);

  const save = async (id: SectionId, body: Record<string, unknown>, message = "Settings saved") => {
    setFailure(null);
    setSaving(id);
    try {
      const s = await saveSettings(body);
      // What was saved now reads as the server has it (masked keys, upper-case pairs, blank pairs dropped).
      setDraft((d) => {
        const next: Record<string, unknown> = { ...d };
        for (const k of Object.keys(body)) next[k] = (s as unknown as Record<string, unknown>)[k];
        return next as unknown as Settings;
      });
      await refreshMeta();
      toast(message);
    } catch (e) {
      setFailure({ section: id, message: (e as Error).message });
    } finally {
      setSaving(null);
    }
  };
  const discard = (keys: Keys) =>
    setDraft((d) => {
      const next: Record<string, unknown> = { ...d };
      for (const k of keys) next[k] = settings[k];
      return next as unknown as Settings;
    });
  const changed = (keys: Keys) => keys.filter((k) => !same(draft[k], settings[k])).length;
  const unsaved: Record<SectionId, number> = {
    data: changed(DATA_KEYS),
    account: changed(ACCOUNT_KEYS),
    desk: changed(DESK_KEYS),
    paper: 0,
    appearance: 0,
    access: 0,
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

  // ---- what is true now (saved settings, not the draft) ----
  const inUse = meta.providers.find((p) => p.id === settings.provider);
  const picked = meta.providers.find((p) => p.id === draft.provider);
  const tfWord = TF_WORD[settings.timeframe] ?? settings.timeframe;
  const keyNeeded = Boolean(inUse?.requires_key && !inUse.configured);
  const lim = limits?.limits;
  const settingsFile = settings.csv_dir ? null : `${settings.csv_dir_resolved.replace(/data[\\/]?$/, "")}settings.json`;

  const dirtyNames = SECTIONS.filter((s) => unsaved[s.id]).map((s) => `**${s.label}**`);
  useStatusLine(
    [
      `Data: **${inUse?.label ?? settings.provider}**, ${tfWord} bars.`,
      keyNeeded ? "It needs an API key: add one under Data, then save." : "",
      `Alpaca key: **${settings.has_alpaca_credentials ? "saved" : "not set"}**.`,
      halt ? (halt.on ? "Kill switch **ON**: nothing trades." : "Kill switch **off**.") : "",
      dirtyNames.length ? `Unsaved changes in ${dirtyNames.join(" and ")}: press Save there.` : "",
    ]
      .filter(Boolean)
      .join(" "),
  );

  const flag = (id: SectionId): { text: string; attention: boolean } | null => {
    if (unsaved[id]) return { text: "Unsaved", attention: true };
    if (id === "data" && picked?.requires_key && !picked.configured) return { text: "Needs key", attention: true };
    if (id === "paper" && halt?.on) return { text: "Halt on", attention: true };
    if (id === "paper" && !settings.has_alpaca_credentials) return { text: "No key", attention: false };
    return null;
  };
  const errorFor = (id: SectionId) => (failure?.section === id ? failure.message : null);

  // ---- sections ----
  const dataSection = () => {
    const sel = picked;
    const selTest = sel ? tests[sel.id] : undefined;
    return (
      <>
        <Panel title="Market data source" aside="used by the Live Desk and as the default elsewhere" marks>
          <div className="set-tiles set-tiles-providers" role="radiogroup" aria-label="Market data source">
            {meta.providers.map((p) => {
              const t = tests[p.id];
              const ready = !p.requires_key || p.configured;
              return (
                <OptionTile
                  key={p.id}
                  group="set-provider"
                  checked={draft.provider === p.id}
                  onSelect={() => patch({ provider: p.id })}
                  status={
                    <>
                      <span className={`set-sq ${ready ? "good" : "attn"}`} aria-hidden="true" />
                      {ready ? "Configured" : "Needs key"}
                    </>
                  }
                  corner={p.active ? "In use" : null}
                  name={p.label}
                  lines={[p.realtime, p.timeframes.join(" ")]}
                  foot={
                    <>
                      <button type="button" className="btn small" onClick={() => void test(p.id)} disabled={t === "running"}>
                        {t === "running" ? "Testing..." : "Test connection"}
                      </button>
                      {t && t !== "running" ? (
                        <span className={`set-test ${t.ok ? "ok" : "bad"}`} title={t.message}>
                          {t.ok ? "OK" : "Failed"}
                        </span>
                      ) : null}
                    </>
                  }
                />
              );
            })}
          </div>
          {sel ? (
            <div className="set-provider">
              <span className="set-diamond" aria-hidden="true">
                ◆
              </span>
              <div className="set-provider-text">
                <p>
                  <b>{sel.label}.</b> {sel.description}{" "}
                  {sel.docs_url ? (
                    <a href={sel.docs_url} target="_blank" rel="noreferrer">
                      Docs
                    </a>
                  ) : null}
                </p>
                {selTest && selTest !== "running" ? (
                  <p className="set-test-line">
                    <StatusDot status={selTest.ok ? "good" : "bad"}>{selTest.ok ? "Connected" : "Failed"}</StatusDot>{" "}
                    <span className="secondary">{selTest.message}</span>
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}
        </Panel>

        <Panel title="Keys and files" aside="each sent only to its own provider">
          <div className="set-rows">
            <section className="set-row" aria-labelledby="set-row-alpaca">
              <div className="set-row-head">
                <h3 id="set-row-alpaca">Alpaca</h3>
                <Tag kind={settings.has_alpaca_credentials ? "ok" : "info"}>{settings.has_alpaca_credentials ? "Key saved" : "No key"}</Tag>
              </div>
              <div className="set-row-body">
                <div className="set-form">
                  <Field label="API key ID">
                    <input
                      className="input mono"
                      value={draft.alpaca_key_id}
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(e) => patch({ alpaca_key_id: e.target.value })}
                    />
                  </Field>
                  <Field label="Secret key">
                    <input
                      className="input mono"
                      type="password"
                      autoComplete="new-password"
                      value={draft.alpaca_secret_key}
                      onChange={(e) => patch({ alpaca_secret_key: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Feed"
                    help="IEX: free, prices from the IEX exchange only. SIP: every US exchange combined (paid plan). Delayed SIP: the full feed, 15 minutes late."
                  >
                    <select className="input" value={draft.alpaca_feed} onChange={(e) => patch({ alpaca_feed: e.target.value })}>
                      <option value="iex">IEX (free)</option>
                      <option value="sip">SIP (paid)</option>
                      <option value="delayed_sip">SIP, 15-min delay</option>
                    </select>
                  </Field>
                </div>
                <p className="set-note">
                  One key serves Alpaca market data and the paper trader. Saved keys come back masked (<code>****</code> and the last
                  four characters); to replace one, clear the field and paste the new key.
                </p>
              </div>
            </section>

            <section className="set-row" aria-labelledby="set-row-massive">
              <div className="set-row-head">
                <h3 id="set-row-massive">Massive</h3>
                <Tag kind={settings.has_massive_credentials ? "ok" : "info"}>{settings.has_massive_credentials ? "Key saved" : "No key"}</Tag>
              </div>
              <div className="set-row-body">
                <div className="set-form">
                  <Field label="API key">
                    <input
                      className="input mono"
                      type="password"
                      autoComplete="new-password"
                      value={draft.massive_api_key}
                      onChange={(e) => patch({ massive_api_key: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Base URL (optional)"
                    help="Where requests for Massive data go. Leave it empty for https://api.massive.com; any other address must start with https://."
                    className="set-span-2"
                  >
                    <input
                      className="input mono"
                      type="url"
                      value={draft.massive_base_url}
                      placeholder="https://api.massive.com"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(e) => patch({ massive_base_url: e.target.value })}
                    />
                  </Field>
                </div>
                <p className="set-note">
                  Polygon.io renamed itself Massive in October 2025; <code>https://api.polygon.io</code> also works. The key comes back
                  masked like the Alpaca keys.
                </p>
              </div>
            </section>

            <section className="set-row" aria-labelledby="set-row-csv">
              <div className="set-row-head">
                <h3 id="set-row-csv">CSV files</h3>
                <span className="set-row-sub">your own data</span>
              </div>
              <div className="set-row-body">
                <Field label="CSV folder (blank = default)" hint="One file per symbol, named SYMBOL.csv." className="set-wide">
                  <input
                    className="input mono"
                    value={draft.csv_dir}
                    placeholder={draft.csv_dir_resolved}
                    spellCheck={false}
                    onChange={(e) => patch({ csv_dir: e.target.value })}
                  />
                </Field>
              </div>
            </section>
          </div>
        </Panel>

        <Panel title="Live feed" aside="how the Live Desk follows the market">
          <div className="set-form">
            <Field label="Live bar size" help="The bar length the Live Desk works on: each strategy decides once per completed bar.">
              <select className="input" value={draft.timeframe} onChange={(e) => patch({ timeframe: e.target.value })}>
                {meta.timeframes.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Poll every (seconds)" help="How often the Live Desk asks the data source for new prices.">
              <NumInput min={2} value={draft.poll_seconds} onValue={(v) => patch({ poll_seconds: v })} />
            </Field>
            <Field label="Demo clock speed (x)" help="Simulated seconds per real second in the synthetic demo market.">
              <NumInput min={1} value={draft.demo_speed} onValue={(v) => patch({ demo_speed: v })} />
            </Field>
          </div>
        </Panel>

        <SaveBar
          label="Save data settings"
          changed={unsaved.data}
          saving={saving === "data"}
          error={errorFor("data")}
          onDiscard={() => discard(DATA_KEYS)}
          onSave={() =>
            void save(
              "data",
              {
                provider: draft.provider,
                alpaca_key_id: draft.alpaca_key_id,
                alpaca_secret_key: draft.alpaca_secret_key,
                alpaca_feed: draft.alpaca_feed,
                massive_api_key: draft.massive_api_key,
                massive_base_url: draft.massive_base_url,
                csv_dir: draft.csv_dir,
                timeframe: draft.timeframe,
                poll_seconds: draft.poll_seconds,
                demo_speed: draft.demo_speed,
              },
              "Data settings saved; the live feed is reloading.",
            )
          }
        />
      </>
    );
  };

  const accountSection = () => {
    const eq = draft.account_equity;
    const taxable = draft.account_type === "taxable";
    return (
      <>
        <Panel title="Profile" aside="the default for the Lab, the simulators, practice mode and the Live Desk" marks>
          <div className="set-form">
            <Field label="Starting equity ($)" help="The account size every backtest, simulation and suggestion starts from.">
              <NumInput min={100} value={draft.account_equity} onValue={(v) => patch({ account_equity: v })} />
            </Field>
            <Choice
              id="set-account-type"
              label="Account type"
              help="Taxable: a regular brokerage account, taxed on gains when you sell. Tax-advantaged: an IRA or 401(k), with no tax as you trade inside it."
              value={draft.account_type}
              options={[
                ["taxable", "Taxable"],
                ["tax_advantaged", "Tax-advantaged"],
              ]}
              onChange={(v) => patch({ account_type: v })}
              hint={taxable ? "A brokerage account." : "An IRA or 401(k): no tax as you go."}
            />
            <Field
              label="Short-term tax rate (%)"
              help="Federal tax on gains held one year or less (taxed like income). 22% is only an estimate: replace it with your own bracket."
              hint={taxable ? undefined : "Not used in a tax-advantaged account."}
            >
              <NumInput
                step={1}
                scale={100}
                disabled={!taxable}
                value={draft.short_term_tax_rate}
                onValue={(v) => patch({ short_term_tax_rate: v })}
              />
            </Field>
            <Field
              label="Long-term tax rate (%)"
              help="Federal tax on gains held more than a year (0%, 15% or 20% depending on income). 15% is only an estimate: replace it with your own."
              hint={taxable ? undefined : "Not used in a tax-advantaged account."}
            >
              <NumInput
                step={1}
                scale={100}
                disabled={!taxable}
                value={draft.long_term_tax_rate}
                onValue={(v) => patch({ long_term_tax_rate: v })}
              />
            </Field>
          </div>
          <label className="check set-check">
            <input type="checkbox" checked={draft.fractional_shares} onChange={(e) => patch({ fractional_shares: e.target.checked })} />
            My broker supports fractional shares
            <Help text="Buying part of a share (say 0.37 of one), so a small account can match its target weights closely." />
          </label>
          <p className="set-note">
            The tax rates are estimates for illustration; replace them with your own federal bracket. After-tax results use average cost
            (not tax lots), ignore wash sales and state tax, and never offset losses against other income.
          </p>
        </Panel>

        <Panel title="Risk and sizing" aside="how the Live Desk sizes its suggested positions">
          <div className="set-form">
            <Field label="Risk per trade (%)" help="Loss if the protective stop is hit, as a share of the account. 0.5-2% is typical.">
              <NumInput step={0.1} scale={100} value={draft.risk_per_trade} onValue={(v) => patch({ risk_per_trade: v })} />
            </Field>
            <Field
              label="Stop distance (ATRs)"
              help="Where the protective stop sits, in ATRs. ATR (average true range) is how far the price typically moves in one bar, so a 2-ATR stop is two typical moves away."
            >
              <NumInput step={0.25} value={draft.stop_atr} onValue={(v) => patch({ stop_atr: v })} />
            </Field>
            <Field label="Max position (% of account)" help="The largest single suggested position, as a share of the account.">
              <NumInput step={1} scale={100} value={draft.max_position_pct} onValue={(v) => patch({ max_position_pct: v })} />
            </Field>
            <Field
              label="Portfolio cap (% of account)"
              help="All suggested positions together. If they add up to more, every suggestion shrinks in proportion. 100% means no borrowing."
            >
              <NumInput step={5} scale={100} value={draft.max_gross_exposure} onValue={(v) => patch({ max_gross_exposure: v })} />
            </Field>
          </div>
          <label className="check set-check">
            <input type="checkbox" checked={draft.allow_short} onChange={(e) => patch({ allow_short: e.target.checked })} />
            Allow short-selling recommendations
          </label>
          <p className="set-live">
            <span className="set-diamond" aria-hidden="true">
              ◆
            </span>
            <span>
              With <b>{fmtMoney(eq)}</b>, a stop-out loses at most <b>{fmtMoney(eq * draft.risk_per_trade)}</b>. One position is at most{" "}
              <b>{fmtMoney(eq * draft.max_position_pct)}</b>; all of them together at most <b>{fmtMoney(eq * draft.max_gross_exposure)}</b>.
            </span>
          </p>

          <h3 className="set-subhead">Costs</h3>
          <div className="set-form">
            <Field
              label="Slippage (bps)"
              help="What each fill loses to the bid-ask spread and to moving the price. A basis point (bp) is 0.01%, so 5 bps of a $10,000 fill is $5."
            >
              <NumInput step={0.5} value={draft.slippage_bps} onValue={(v) => patch({ slippage_bps: v })} />
            </Field>
            <Field label="Commission (bps)" help="The broker's fee per fill, as a share of its value. Many US brokers charge nothing for stocks.">
              <NumInput step={0.5} value={draft.commission_bps} onValue={(v) => patch({ commission_bps: v })} />
            </Field>
          </div>
          <p className="set-live">
            <span className="set-diamond" aria-hidden="true">
              ◆
            </span>
            <span>
              A $10,000 fill pays about <b>{fmtMoney((10_000 * draft.slippage_bps) / 10_000, 2)}</b> in slippage and{" "}
              <b>{fmtMoney((10_000 * draft.commission_bps) / 10_000, 2)}</b> in commission; a round trip pays both twice.
            </span>
          </p>
        </Panel>

        <SaveBar
          label="Save account"
          changed={unsaved.account}
          saving={saving === "account"}
          error={errorFor("account")}
          onDiscard={() => discard(ACCOUNT_KEYS)}
          onSave={() =>
            void save(
              "account",
              {
                account_equity: draft.account_equity,
                fractional_shares: draft.fractional_shares,
                account_type: draft.account_type,
                short_term_tax_rate: draft.short_term_tax_rate,
                long_term_tax_rate: draft.long_term_tax_rate,
                risk_per_trade: draft.risk_per_trade,
                stop_atr: draft.stop_atr,
                max_position_pct: draft.max_position_pct,
                max_gross_exposure: draft.max_gross_exposure,
                slippage_bps: draft.slippage_bps,
                commission_bps: draft.commission_bps,
                allow_short: draft.allow_short,
              },
              "Account and risk settings saved",
            )
          }
        />
      </>
    );
  };

  const deskSection = () => {
    const eligible = meta.strategies.filter((s) => s.id !== "buy_hold" && !s.sizes_itself);
    const advisorIds = new Set(draft.advisors.map((a) => a.id));
    const toggleAdvisor = (id: string, on: boolean) => {
      const next = on ? [...draft.advisors, { id, params: {} }] : draft.advisors.filter((a) => a.id !== id);
      patch({ advisors: next });
    };
    const voting = eligible.filter((s) => advisorIds.has(s.id)).length;
    const setLeg = (i: number, leg: 0 | 1, value: string) => {
      const next = draft.pairs.map((x) => [...x]);
      next[i][leg] = value.toUpperCase();
      patch({ pairs: next });
    };
    return (
      <>
        <Panel title="Voting strategies" aside={`${voting} of ${eligible.length} vote`} marks>
          <p className="set-lede">Each enabled strategy casts one vote per symbol on the Live Desk; the votes are then combined below.</p>
          {voting === 0 ? (
            <div className="callout warn set-warn">
              No strategy votes, so the Live Desk has nothing to say. Tick at least one, then save.
            </div>
          ) : null}
          <div className="set-votes-wrap">
            <table className="data set-votes">
              <thead>
                <tr>
                  <th className="set-v-check">Vote</th>
                  <th>Strategy</th>
                  <th>Evidence</th>
                  <th className="set-v-cat">Category</th>
                  <th className="set-v-note">Note</th>
                </tr>
              </thead>
              <tbody>
                {eligible.map((s) => {
                  const on = advisorIds.has(s.id);
                  return (
                    <tr key={s.id} className={on ? "set-on" : undefined}>
                      <td className="set-v-check">
                        <input id={`set-adv-${s.id}`} type="checkbox" checked={on} onChange={(e) => toggleAdvisor(s.id, e.target.checked)} />
                      </td>
                      <td className="set-v-name">
                        <label htmlFor={`set-adv-${s.id}`}>{s.name}</label>
                      </td>
                      <td className="set-v-ev">
                        <EvidenceBadge level={s.evidence} />
                      </td>
                      <td className="set-v-cat">{s.category}</td>
                      <td className="set-v-note">
                        {s.kind === "cross_sectional" ? "ranks the watchlist" : s.kind === "pair" ? "needs a configured pair" : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="set-combine">
            <Choice
              id="set-weighting"
              label="Combine the votes"
              help="Equal: every strategy's vote counts once, so four trend rules that agree count four times. By category: votes are averaged within each category (trend, mean reversion, momentum, ...) first, then the categories are averaged."
              value={draft.consensus_weighting}
              options={[
                ["equal", "Equally"],
                ["by_category", "By category"],
              ]}
              onChange={(v) => patch({ consensus_weighting: v })}
              hint={
                draft.consensus_weighting === "equal" ? "One vote per strategy." : "One vote per kind of strategy: trend, mean reversion, momentum, ..."
              }
            />
          </div>
        </Panel>

        <Panel title="Pairs" aside="for pairs trading">
          <div className="set-pairs">
            {draft.pairs.length ? (
              draft.pairs.map((p, i) => (
                <div className="set-pair" key={i}>
                  <span className="set-pair-n">{String(i + 1).padStart(2, "0")}</span>
                  <input className="input mono" value={p[0]} onChange={(e) => setLeg(i, 0, e.target.value)} aria-label="Pair leg A" />
                  <span className="set-vs">vs</span>
                  <input className="input mono" value={p[1]} onChange={(e) => setLeg(i, 1, e.target.value)} aria-label="Pair leg B" />
                  <button type="button" className="btn small ghost" onClick={() => patch({ pairs: draft.pairs.filter((_, j) => j !== i) })}>
                    Remove
                  </button>
                </div>
              ))
            ) : (
              <p className="set-empty">No pairs yet: the pairs strategies have nothing to trade. Add one below.</p>
            )}
            <div>
              <button type="button" className="btn small" onClick={() => patch({ pairs: [...draft.pairs, ["", ""]] })}>
                Add pair
              </button>
            </div>
            <p className="set-note">Both legs must be in your watchlist. Classic real-market examples: KO/PEP, XOM/CVX, GLD/GDX.</p>
          </div>
        </Panel>

        <SaveBar
          label="Save strategies"
          changed={unsaved.desk}
          saving={saving === "desk"}
          error={errorFor("desk")}
          onDiscard={() => discard(DESK_KEYS)}
          onSave={() =>
            void save(
              "desk",
              {
                advisors: draft.advisors,
                pairs: draft.pairs.filter((p) => p[0] && p[1]),
                consensus_weighting: draft.consensus_weighting,
              },
              "Live Desk strategies saved",
            )
          }
        />
      </>
    );
  };

  const paperSection = () => {
    const rows: [ReactNode, ReactNode][] = lim
      ? LIMITS.filter(({ key, kind }) => key in lim && (kind !== "list" || key === "allowed_symbols" || (lim[key] as unknown[]).length)).map(
          ({ key, label, kind, note }) => [
            label,
            <span className="set-lim">
              <span>{showLimit(lim[key], kind)}</span>
              {note ? <span className="set-lim-note">{note}</span> : null}
            </span>,
          ],
        )
      : [];
    return (
      <>
        <Panel title="Kill switch" aside="the HALT file" marks className={`set-halt${halt?.on ? " on" : ""}`}>
          <KillSwitchControl onChange={setHalt} />
        </Panel>
        <div className="set-two">
          <Panel title="Alpaca paper account" aside="practice money only">
            <KeyValues
              rows={[
                [
                  "Endpoint",
                  <>
                    <code>https://paper-api.alpaca.markets</code> <span className="muted">(fixed)</span>
                  </>,
                ],
                ["Live trading", <span className="set-sans">None: there is no live-trading endpoint in this app.</span>],
                [
                  "API key",
                  settings.has_alpaca_credentials ? (
                    <span className="set-sans">Uses your Alpaca key (Data)</span>
                  ) : (
                    <button type="button" className="linklike set-sans" onClick={() => go("data")}>
                      Add an Alpaca paper key under Data
                    </button>
                  ),
                ],
                ["Orders", <span className="set-sans">From the command line or a scheduled job, never from this page.</span>],
              ]}
            />
            <p className="set-note">
              <code>trades paper</code> prints the plan; <code>trades paper --submit</code> sends it for a spec that passed the acceptance
              checklist. The <a href="#/pipeline">Pipeline</a> page shows each spec&apos;s progress.
            </p>
          </Panel>
          <Panel
            title={
              <>
                Risk limits <Help text="Every target passes through these before any order, in backtests and in paper trading alike." />
              </>
            }
            aside="as modeled"
          >
            <div className="set-readonly">
              <Tag>Read-only</Tag>
              <span>
                Checked before every order, in backtests and paper trading alike. They live in <code>{limits?.file ?? "risk/limits.yaml"}</code>,
                which only you edit (Claude Code is blocked from it).
              </span>
            </div>
            {limitsError ? <ErrorBox error={limitsError} /> : lim ? <KeyValues rows={rows} /> : <p className="muted small">Reading the limits...</p>}
          </Panel>
        </div>
      </>
    );
  };

  const appearanceSection = () => (
    <>
      <Panel title="Theme" aside="this browser only" marks>
        <div className="set-tiles set-tiles-themes" role="radiogroup" aria-label="Theme">
          {THEMES.map(([id, code, name, sub]) => (
            <OptionTile
              key={id}
              group="set-theme"
              checked={theme === id}
              onSelect={() => {
                setTheme(id);
                applyTheme(id);
              }}
              status={code}
              corner={theme === id ? "In use" : null}
              preview={<span className={`set-preview set-preview-${id}`} aria-hidden="true" />}
              name={name}
              lines={[sub]}
            />
          ))}
        </div>
      </Panel>
      <Panel
        title={
          <>
            Up / down colours{" "}
            <Help text="Blue/red stays distinguishable for people with red-green colour-vision deficiency (about 8% of men); green/red is the traditional trading convention." />
          </>
        }
        aside="charts and prices"
      >
        <div className="set-tiles set-tiles-candles" role="radiogroup" aria-label="Up and down colours">
          {CANDLES.map(([id, code, name, sub]) => (
            <OptionTile
              key={id}
              group="set-candles"
              checked={candles === id}
              onSelect={() => {
                setCandles(id);
                applyCandles(id);
              }}
              status={code}
              corner={candles === id ? "In use" : null}
              preview={
                <span className={`set-candles set-candles-${id}`} aria-hidden="true">
                  <i className="u" style={{ height: 14, marginTop: 8 }} />
                  <i className="d" style={{ height: 18, marginTop: 4 }} />
                  <i className="u" style={{ height: 22, marginTop: 2 }} />
                  <i className="u" style={{ height: 12, marginTop: 0 }} />
                  <i className="d" style={{ height: 16, marginTop: 6 }} />
                </span>
              }
              name={name}
              lines={[sub]}
            />
          ))}
        </div>
      </Panel>
      <p className="set-note set-note-out">Both apply at once and are remembered by this browser; nothing is sent to the server.</p>
    </>
  );

  const accessSection = () => (
    <Panel title="Access" aside={meta.auth_enabled ? "password-protected" : "this computer only"} marks>
      <KeyValues
        rows={[
          ["Password", meta.auth_enabled ? "On" : "Off"],
          [
            "Who can connect",
            <span className="set-sans">{meta.auth_enabled ? "Anyone with the password" : "Only this computer"}</span>,
          ],
          ...(meta.auth_enabled ? ([["Login lasts", "30 days on this browser"]] as [ReactNode, ReactNode][]) : []),
        ]}
      />
      {meta.auth_enabled ? (
        <>
          <p className="set-note">
            This server is password-protected. Your login lasts 30 days on this browser; changing the server&apos;s password logs everyone
            out.
          </p>
          <div className="row set-actions">
            <button
              type="button"
              className="btn"
              onClick={async () => {
                await api.logout();
                window.location.assign("/login");
              }}
            >
              Log out
            </button>
          </div>
        </>
      ) : (
        <p className="set-note">
          This server has no password, so it only answers this computer. To run it where others can reach it, set{" "}
          <code>TRADES_PASSWORD</code> (and <code>TRADES_SECRET_KEY</code> to keep logins across restarts) before starting it.
        </p>
      )}
    </Panel>
  );

  const body: Record<SectionId, () => ReactNode> = {
    data: dataSection,
    account: accountSection,
    desk: deskSection,
    paper: paperSection,
    appearance: appearanceSection,
    access: accessSection,
  };
  const current = SECTIONS.find((s) => s.id === section) ?? SECTIONS[0];

  return (
    <div className="stack set">
      <header className="set-head">
        <div className="set-head-text">
          <h1>Settings</h1>
          <p>Where your data comes from, your account profile, and what protects you.</p>
          <p className="set-store">
            Stored on this computer only, in {settingsFile ? <code>{settingsFile}</code> : <>settings.json in your Trades folder</>}. API keys
            are sent only to the provider they belong to and are never shown again after saving.
          </p>
        </div>
        <Readouts>
          <Readout
            size="sm"
            label="Data source"
            value={shortName(inUse, settings.provider)}
            tone={keyNeeded ? "accent" : undefined}
            note={inUse && inUse.realtime.length <= 24 ? `${inUse.realtime} · ${tfWord} bars` : `${tfWord} bars`}
          />
          <Readout
            size="sm"
            label="Alpaca key"
            value={settings.has_alpaca_credentials ? "SAVED" : "NOT SET"}
            tone={keyNeeded && inUse?.id === "alpaca" ? "accent" : undefined}
            note="data and paper"
          />
          <Readout
            size="sm"
            label="Kill switch"
            value={halt ? (halt.on ? "ON" : "OFF") : "–"}
            tone={halt?.on ? "accent" : undefined}
            note={halt ? (halt.on ? "nothing trades" : "paper orders allowed") : "reading..."}
          />
          <Readout
            size="sm"
            label="Risk cap per trade"
            value={lim ? pctNum(lim.max_risk_per_trade) : "–"}
            unit={lim ? "%" : undefined}
            note={lim ? `${pctNum(lim.daily_loss_limit)}%/day · ${pctNum(lim.weekly_loss_limit)}%/week` : "risk/limits.yaml"}
          />
        </Readouts>
      </header>

      <div className="set-layout">
        <nav className="set-rail-wrap" aria-label="Settings sections">
          <div className="rail set-rail">
            {SECTIONS.map((s) => {
              const f = flag(s.id);
              return (
                <button key={s.id} type="button" aria-current={section === s.id ? "true" : undefined} onClick={() => go(s.id)}>
                  <span className="set-rail-text">
                    <span className="set-rail-label">{s.label}</span>
                    <span className="set-rail-cap">{s.caption}</span>
                  </span>
                  {f ? <span className={`set-rail-flag${f.attention ? " attn" : ""}`}>{f.text}</span> : null}
                </button>
              );
            })}
          </div>
        </nav>
        <section className="set-content" aria-label={current.label}>
          {body[section]()}
        </section>
      </div>
    </div>
  );
}
