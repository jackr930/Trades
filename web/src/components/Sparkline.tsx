import { useChartColors } from "../theme";

/** Tiny trend line: context in the de-emphasis grey, the latest point in the accent. */
export default function Sparkline({ values, width = 90, height = 26 }: { values: number[]; width?: number; height?: number }) {
  const colors = useChartColors();
  const pts = values.filter((v) => Number.isFinite(v));
  if (pts.length < 2) return <svg width={width} height={height} className="spark" aria-hidden="true" />;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const pad = 3;
  const x = (i: number) => pad + (i * (width - 2 * pad)) / (pts.length - 1);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} className="spark" aria-hidden="true">
      <path d={d} fill="none" stroke={colors.deemph} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(pts.length - 1)} cy={y(last)} r={2.5} fill={last >= pts[0] ? colors.up : colors.down} />
    </svg>
  );
}
