import type { MetricInfo, Num } from "./types";

const MINUS = "−";

function signed(text: string, value: number, showPlus: boolean): string {
  if (!/[1-9]/.test(text)) return text; // rounds to zero: no sign ("0.0%", not "−0.0%")
  if (value < 0) return MINUS + text.replace(/^-/, "");
  return (showPlus && value > 0 ? "+" : "") + text;
}

export function isNum(x: unknown): x is number {
  return typeof x === "number" && Number.isFinite(x);
}

export function fmtPct(x: Num | undefined, digits = 1, plus = true): string {
  if (!isNum(x)) return "–";
  return signed(Math.abs(x * 100).toFixed(digits) + "%", x, plus);
}

export function fmtNum(x: Num | undefined, digits = 2, plus = false): string {
  if (!isNum(x)) return "–";
  const text = Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return signed(text, x, plus);
}

export function fmtPrice(x: Num | undefined): string {
  if (!isNum(x)) return "–";
  const digits = Math.abs(x) >= 1000 ? 2 : Math.abs(x) >= 1 ? 2 : 4;
  return fmtNum(x, digits);
}

export function fmtMoney(x: Num | undefined, digits = 0, plus = false): string {
  if (!isNum(x)) return "–";
  const text = "$" + Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return signed(text, x, plus);
}

export function fmtCompact(x: Num | undefined): string {
  if (!isNum(x)) return "–";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(x);
}

export function fmtInt(x: Num | undefined): string {
  if (!isNum(x)) return "–";
  return Math.round(x).toLocaleString();
}

/** Dates are UTC-stamped by the API (daily bars at 00:00 UTC of the trading date). */
export function fmtDate(t: number | null | undefined): string {
  if (!isNum(t)) return "–";
  return new Date(t * 1000).toISOString().slice(0, 10);
}

export function fmtDateTime(t: number | null | undefined): string {
  if (!isNum(t)) return "–";
  const d = new Date(t * 1000);
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function fmtMetric(value: Num | undefined, info: MetricInfo | undefined): string {
  if (!info) return fmtNum(value ?? null);
  switch (info.fmt) {
    case "pct":
      return fmtPct(value ?? null, 1, false);
    case "ratio":
      return fmtNum(value ?? null, 2);
    case "x":
      return isNum(value) ? fmtNum(value, 1) + "×" : "–";
    case "money":
      return fmtMoney(value ?? null);
    case "int":
      return fmtInt(value ?? null);
    case "bars":
      return isNum(value) ? `${fmtInt(value)} bars` : "–";
  }
}

/** CSS class for signed numbers that represent *your* profit or loss. */
export function pnlClass(x: Num | undefined): string {
  if (!isNum(x) || x === 0) return "";
  return x > 0 ? "pos" : "neg";
}

/** CSS class for market direction (price up/down). */
export function dirClass(x: Num | undefined): string {
  if (!isNum(x) || x === 0) return "";
  return x > 0 ? "upc" : "downc";
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function evidenceLabel(level: string): string {
  return (
    { strong: "Strong evidence", moderate: "Moderate evidence", practitioner: "Practitioner rule", benchmark: "Benchmark" }[
      level
    ] ?? level
  );
}
