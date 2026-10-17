import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyCandles, applyTheme, loadCandles, loadTheme } from "./theme";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/300.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./styles.css";

applyTheme(loadTheme());
applyCandles(loadCandles());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
