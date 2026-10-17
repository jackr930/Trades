import { useEffect, useState } from "react";
import { AppProvider, StatusLineProvider, useApp, useRoute, useStatusText, type Route } from "./state";
import { Disclaimer, ErrorBox, Spinner } from "./components/ui";
import { InlineText } from "./components/instrument";
import { api } from "./api";
import { applyTheme, loadTheme, type ThemeChoice } from "./theme";
import type { AlertFeed, KillSwitch, MarketClock } from "./types";
import LiveDesk from "./views/LiveDesk";
import Pipeline from "./views/Pipeline";
import StrategyLab from "./views/StrategyLab";
import Simulator from "./views/Simulator";
import Library from "./views/Library";
import SettingsView from "./views/Settings";

const NAV: [string, string][] = [
  ["/pipeline", "Pipeline"],
  ["/live", "Live Desk"],
  ["/lab", "Lab"],
  ["/sim", "Simulator"],
  ["/library", "Library"],
  ["/settings", "Settings"],
];

/** What each screen is for, until the view narrates its own state (useStatusLine). */
const DEFAULT_STATUS: Record<string, string> = {
  "/pipeline": "Every strategy passes four stages before real money: spec, research, paper, the gate.",
  "/live": "Recommendations from published strategies, recomputed as each bar completes. Not advice.",
  "/lab": "Backtest a strategy on real or simulated data, net of costs, then stress it.",
  "/sim": "Race strategies on a market you can steer, bar by bar, with no look-ahead.",
  "/library": "Every strategy, its evidence and its failure modes, before you trust any of them.",
  "/settings": "Data sources, your account profile, the kill switch and the risk limits.",
};

const FALLBACK_DISCLAIMER =
  "Educational software. Not investment advice. This app never places real-money orders; its optional paper trader uses an Alpaca paper (practice) account only.";

export default function App() {
  const [route, navigate] = useRoute();
  return (
    <AppProvider navigate={navigate}>
      <StatusLineProvider>
        <Shell route={route} />
      </StatusLineProvider>
    </AppProvider>
  );
}

const NY_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const NY_HM = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
const NY_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short" });

/** "2h 14m" until a time. */
function until(iso: string, now: number): string {
  const mins = Math.max(0, Math.round((new Date(iso).getTime() - now) / 60000));
  const h = Math.floor(mins / 60);
  return h ? `${h}h ${mins % 60}m` : `${mins}m`;
}

/** The NYSE clock: New York time, and whether the market is open, with the next bell. */
function MarketStatus() {
  const [clock, setClock] = useState<MarketClock | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const load = () => api.clock().then(setClock, () => undefined);
    load();
    const poll = window.setInterval(load, 60_000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, []);
  let label = "NYSE";
  let state: "open" | "closed" | "ext" = "closed";
  if (clock) {
    if (clock.is_open) {
      label = `NYSE OPEN · CLOSES IN ${until(clock.next_close, now)}`;
      state = "open";
    } else {
      const day = NY_DAY.format(new Date(clock.next_open)).toUpperCase();
      const phase = clock.phase === "pre" ? "PRE-MARKET" : clock.phase === "post" ? "AFTER HOURS" : "CLOSED";
      label = `NYSE ${phase} · OPENS ${day} ${NY_HM.format(new Date(clock.next_open))}`;
      state = clock.phase === "closed" ? "closed" : "ext";
    }
  }
  return (
    <>
      <span className="market" title="New York Stock Exchange, regular session 9:30-16:00 ET (13:00 on early closes)">
        <span className={`status ${state === "open" ? "live" : state === "ext" ? "warn" : "info"}`}>
          <span className="dot" />
        </span>
        {label}
      </span>
      <span className="clock" title="New York time">
        {NY_TIME.format(new Date(now))} ET
      </span>
    </>
  );
}

/** The kill switch and the alert count, visible on every screen. */
function Signals() {
  const { navigate } = useApp();
  const [halt, setHalt] = useState<KillSwitch | null>(null);
  const [feed, setFeed] = useState<AlertFeed | null>(null);
  useEffect(() => {
    const load = () => {
      api.killSwitch().then(setHalt, () => undefined);
      api.alerts(1).then(setFeed, () => undefined);
    };
    load();
    const poll = window.setInterval(load, 30_000);
    return () => window.clearInterval(poll);
  }, []);
  const loud = feed ? feed.counts_24h.crit + feed.counts_24h.warn : 0;
  return (
    <>
      <button
        type="button"
        className={`halt${halt?.on ? " on" : ""}`}
        title={halt?.on ? `Kill switch ON: ${halt.reason}` : "Kill switch off: paper runners may send orders"}
        onClick={() => navigate("/pipeline")}
      >
        HALT {halt ? (halt.on ? "ON" : "OFF") : "–"}
      </button>
      <button
        type="button"
        className={`alerts${loud ? " on" : ""}`}
        title="Errors, rejections, risk limits and the kill switch from the paper records, last 24 hours"
        onClick={() => navigate("/pipeline", { panel: "alerts" })}
      >
        ALERTS {loud}
      </button>
    </>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme());
  const order: ThemeChoice[] = ["system", "light", "dark"];
  const next = order[(order.indexOf(theme) + 1) % order.length];
  return (
    <button
      type="button"
      className="theme-select"
      title={`Theme: ${theme} (click for ${next})`}
      aria-label={`Colour theme: ${theme}. Switch to ${next}.`}
      onClick={() => {
        setTheme(next);
        applyTheme(next);
      }}
    >
      {theme === "system" ? "◐ AUTO" : theme === "light" ? "○ LIGHT" : "● DARK"}
    </button>
  );
}

function Crumbs({ route }: { route: Route }) {
  const section = NAV.find(([path]) => path === route.path)?.[1] ?? "Pipeline";
  const detail = route.params.get("spec") ?? route.params.get("strategy") ?? route.params.get("run");
  return (
    <div className="crumbs" aria-label="Location">
      TRADES<span className="sep">/</span>
      {detail ? (
        <>
          {section.toUpperCase()}
          <span className="sep">/</span>
          <b>{detail.toUpperCase()}</b>
        </>
      ) : (
        <b>{section.toUpperCase()}</b>
      )}
    </div>
  );
}

function StatusLine({ route }: { route: Route }) {
  const text = useStatusText() ?? DEFAULT_STATUS[route.path] ?? DEFAULT_STATUS["/pipeline"];
  return (
    <div className="statusline" role="status" aria-live="polite">
      <span className="diamond" aria-hidden="true">
        ◆
      </span>
      <span>
        <InlineText text={text} />
      </span>
    </div>
  );
}

function Shell({ route }: { route: Route }) {
  const { meta, settings, loadError, refreshMeta, refreshSettings } = useApp();
  let view;
  switch (route.path) {
    case "/lab":
      view = <StrategyLab route={route} />;
      break;
    case "/sim":
      view = <Simulator route={route} />;
      break;
    case "/library":
      view = <Library route={route} />;
      break;
    case "/settings":
      view = <SettingsView />;
      break;
    case "/live":
      view = <LiveDesk />;
      break;
    default:
      view = <Pipeline route={route} />;
  }
  const current = route.path === "/" ? "/pipeline" : route.path;
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/pipeline">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <rect x="0.5" y="0.5" width="17" height="17" fill="none" stroke="currentColor" />
            <path d="M3 12.5l3.5-3.5 2.5 2 5.5-6" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <rect x="12.5" y="3.5" width="3" height="3" fill="var(--accent)" />
          </svg>
          <span>TRADES</span>
        </a>
        <Crumbs route={route} />
        <nav className="nav" aria-label="Main">
          {NAV.map(([path, label]) => (
            <a key={path} href={`#${path}`} aria-current={current === path ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
        <span className="spacer" />
        <div className="topbar-status">
          <MarketStatus />
          <Signals />
          <ThemeToggle />
        </div>
      </header>
      <StatusLine route={route} />
      <main className="main">
        {loadError ? (
          <div className="col">
            <ErrorBox error={loadError} />
            <div>
              <button
                className="btn"
                onClick={() => {
                  void refreshMeta();
                  void refreshSettings();
                }}
              >
                Retry
              </button>
            </div>
          </div>
        ) : !meta || !settings ? (
          <Spinner label="Connecting to the Trades server..." />
        ) : (
          view
        )}
      </main>
      <Disclaimer text={meta?.disclaimer ?? FALLBACK_DISCLAIMER} />
    </div>
  );
}
