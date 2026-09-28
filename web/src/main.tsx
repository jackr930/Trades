import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyCandles, applyTheme, loadCandles, loadTheme } from "./theme";
import "./styles.css";

applyTheme(loadTheme());
applyCandles(loadCandles());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
