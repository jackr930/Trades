import { useState } from "react";
import { AppProvider, useApp, useRoute, type Route } from "./state";
import { Disclaimer, ErrorBox, Spinner } from "./components/ui";
import { applyTheme, loadTheme, type ThemeChoice } from "./theme";
import LiveDesk from "./views/LiveDesk";
import StrategyLab from "./views/StrategyLab";
import Simulator from "./views/Simulator";
import Library from "./views/Library";
import SettingsView from "./views/Settings";

const NAV: [string, string][] = [
  ["/live", "Live Desk"],
  ["/lab", "Strategy Lab"],
  ["/sim", "Simulator"],
  ["/library", "Library"],
  ["/settings", "Settings"],
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
    case "/library":
      view = <Library route={route} />;
      break;
    case "/settings":
      view = <SettingsView />;
      break;
    default:
      view = <LiveDesk />;
  }
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/live" style={{ color: "inherit", textDecoration: "none" }}>
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="7" fill="var(--accent)" />
            <path d="M6 21l6-6 5 4 9-10" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>Trades</span>
        </a>
        <nav className="nav" aria-label="Main">
          {NAV.map(([path, label]) => (
            <a key={path} href={`#${path}`} aria-current={route.path === path || (path === "/live" && route.path === "/") ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
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
      <Disclaimer text={meta?.disclaimer ?? FALLBACK_DISCLAIMER} />
    </div>
  );
}
