import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type CandleChoice = "bluered" | "classic";

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode) - the choice just won't persist */
  }
}

export function loadTheme(): ThemeChoice {
  const v = safeGet("trades.theme");
  return v === "light" || v === "dark" ? v : "system";
}

export function loadCandles(): CandleChoice {
  return safeGet("trades.candles") === "classic" ? "classic" : "bluered";
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  safeSet("trades.theme", choice);
  window.dispatchEvent(new Event("trades-theme"));
}

export function applyCandles(choice: CandleChoice): void {
  const root = document.documentElement;
  if (choice === "classic") root.dataset.candles = "classic";
  else delete root.dataset.candles;
  safeSet("trades.candles", choice);
  window.dispatchEvent(new Event("trades-theme"));
}

export interface ChartColors {
  surface: string;
  text: string;
  textSecondary: string;
  axisText: string;
  grid: string;
  axis: string;
  up: string;
  down: string;
  upWash: string;
  downWash: string;
  deemph: string;
  series: string[];
  accent: string;
  good: string;
  critical: string;
  dark: boolean;
}

export function readChartColors(): ChartColors {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string) => cs.getPropertyValue(name).trim();
  return {
    surface: v("--surface-1"),
    text: v("--text-primary"),
    textSecondary: v("--text-secondary"),
    axisText: v("--axis-text"),
    grid: v("--grid"),
    axis: v("--axis"),
    up: v("--up"),
    down: v("--down"),
    upWash: v("--up-wash"),
    downWash: v("--down-wash"),
    deemph: v("--deemph"),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--series-${i}`)),
    accent: v("--accent"),
    good: v("--good"),
    critical: v("--critical"),
    dark: cs.colorScheme.includes("dark") || v("--page").toLowerCase() === "#0d0d0d",
  };
}

/** Chart colours that update when the theme (or OS colour scheme) changes. */
export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(() => readChartColors());
  useEffect(() => {
    const update = () => setColors(readChartColors());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", update);
    window.addEventListener("trades-theme", update);
    return () => {
      mq.removeEventListener("change", update);
      window.removeEventListener("trades-theme", update);
    };
  }, []);
  return colors;
}

/** Hex/rgb colour with an alpha channel (for area washes). */
export function withAlpha(color: string, alpha: number): string {
  const c = color.trim();
  if (c.startsWith("#") && (c.length === 7 || c.length === 4)) {
    const full = c.length === 4 ? "#" + [...c.slice(1)].map((x) => x + x).join("") : c;
    const r = parseInt(full.slice(1, 3), 16);
    const g = parseInt(full.slice(3, 5), 16);
    const b = parseInt(full.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return c;
}

/** Diverging colour for a value in [-1, 1]: red <- neutral grey -> blue (market direction). */
export function divergingColor(value: number, colors: ChartColors, max = 1): string {
  const t = Math.max(-1, Math.min(1, value / (max || 1)));
  const neutral = colors.dark ? [56, 56, 53] : [240, 239, 236];
  const target = hexToRgb(t >= 0 ? colors.up : colors.down);
  const k = Math.abs(t);
  const mix = neutral.map((n, i) => Math.round(n + (target[i] - n) * k));
  return `rgb(${mix[0]}, ${mix[1]}, ${mix[2]})`;
}

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "").trim();
  if (h.length !== 6) return [128, 128, 128];
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/** Ink that stays readable on a given background colour. */
export function inkFor(rgb: string): string {
  const m = rgb.match(/\d+/g);
  if (!m) return "inherit";
  const [r, g, b] = m.slice(0, 3).map(Number).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.35 ? "#0b0b0b" : "#ffffff";
}
