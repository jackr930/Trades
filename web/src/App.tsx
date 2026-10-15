import { useEffect, useRef, useState } from "react";
import { AppProvider, useApp, useRoute, type Route } from "./state";
import { Disclaimer, ErrorBox, Spinner } from "./components/ui";
import { applyTheme, loadTheme, type ThemeChoice } from "./theme";
import LiveDesk from "./views/LiveDesk";
import StrategyLab from "./views/StrategyLab";
import Simulator from "./views/Simulator";
import Library from "./views/Library";
import TrackRecordView from "./views/TrackRecord";
import Today from "./views/Today";
import Portfolio from "./views/Portfolio";
import Plan from "./views/Plan";
import { Privacy, Terms } from "./views/Legal";
import Onboarding from "./components/Onboarding";
import SettingsView from "./views/Settings";

// [path, label, shown in the simple mode too]
const NAV: [string, string, boolean][] = [
  ["/today", "Today", true],
  ["/live", "Live Desk", false],
  ["/lab", "Strategy Lab", false],
  ["/track", "Track Record", true],
  ["/portfolio", "Portfolio", true],
  ["/plan", "Plan", true],
  ["/sim", "Simulator", false],
  ["/library", "Library", true],
  ["/settings", "Settings", true],
];

const FALLBACK_DISCLAIMER =
  "Educational software. Not investment advice. This app never places real-money orders; its optional paper trader uses an Alpaca paper (practice) account only.";

export default function App() {
  const [route, navigate] = useRoute();
  return (
    <AppProvider navigate={navigate}>
      <Shell route={route} />
    </AppProvider>
  );
}

function ThemeSelect() {
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme());
  return (
    <select
      className="input theme-select"
      style={{ width: 110, height: 30 }}
      value={theme}
      aria-label="Colour theme"
      onChange={(e) => {
        const t = e.target.value as ThemeChoice;
        setTheme(t);
        applyTheme(t);
      }}
    >
      <option value="system">System</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
    </select>
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
    case "/track":
      view = <TrackRecordView />;
      break;
    case "/portfolio":
      view = <Portfolio />;
      break;
    case "/privacy":
      view = <Privacy />;
      break;
    case "/terms":
      view = <Terms />;
      break;
    case "/plan":
      view = <Plan route={route} />;
      break;
    case "/live":
      view = <LiveDesk />;
      break;
    case "/library":
      view = <Library route={route} />;
      break;
    case "/settings":
      view = <SettingsView />;
      break;
    default:
      view = <Today />;
  }
  const simple = settings?.ui_mode === "simple";
  const nav = NAV.filter(([path, , essential]) => essential || !simple || path === route.path);
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/today" style={{ color: "inherit", textDecoration: "none" }}>
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="7" fill="var(--accent)" />
            <path d="M6 21l6-6 5 4 9-10" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>Trades</span>
        </a>
        <Nav items={nav} current={route.path} />
        <span className="spacer" />
        <ThemeSelect />
      </header>
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
      {settings && meta && !settings.onboarded ? <Onboarding settings={settings} /> : null}
      <Disclaimer text={meta?.disclaimer ?? FALLBACK_DISCLAIMER} />
      <nav className="footer small" aria-label="Legal" style={{ paddingTop: 0 }}>
        <a href="#/privacy">Privacy</a> · <a href="#/terms">Terms</a>
      </nav>
    </div>
  );
}

function Nav({ items, current }: { items: [string, string, boolean][]; current: string }) {
  const ref = useRef<HTMLElement>(null);
  // On a phone the links scroll sideways: keep the current page's link in view.
  useEffect(() => {
    ref.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [current]);
  return (
    <nav className="nav" aria-label="Main" ref={ref}>
      {items.map(([path, label]) => (
        <a key={path} href={`#${path}`} aria-current={current === path || (path === "/today" && current === "/") ? "page" : undefined}>
          {label}
        </a>
      ))}
    </nav>
  );
}
