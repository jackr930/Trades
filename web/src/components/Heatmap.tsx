import { useState } from "react";
import type { Num } from "../types";
import { isNum } from "../format";
import { divergingColor, inkFor, useChartColors } from "../theme";

interface Props {
  rows: string[];
  cols: string[];
  values: Num[][];
  format: (v: number) => string;
  /** Symmetric colour range; defaults to the largest absolute value. */
  maxAbs?: number;
  best?: [number, number] | null;
  rowLabel?: string;
  colLabel?: string;
  scaleLabel?: [string, string];
  caption?: string;
}

/** Diverging heatmap (red = negative, neutral grey = zero, blue = positive) with values
 *  printed in each cell and a hover readout. */
export default function Heatmap({ rows, cols, values, format, maxAbs, best, rowLabel, colLabel, scaleLabel, caption }: Props) {
  const colors = useChartColors();
  const [hover, setHover] = useState<[number, number] | null>(null);
  const flat = values.flat().filter(isNum) as number[];
  const m = maxAbs ?? Math.max(1e-9, ...flat.map((v) => Math.abs(v)));
  const hv = hover ? values[hover[0]]?.[hover[1]] : null;
  return (
    <div>
      {caption ? <div className="small muted" style={{ marginBottom: 6 }}>{caption}</div> : null}
      <div className="table-wrap">
        <table className="heatmap" role="grid">
          <thead>
            <tr>
              <th className="rowhead">{rowLabel && colLabel ? `${rowLabel} \\ ${colLabel}` : ""}</th>
              {cols.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r}>
                <th scope="row" className="rowhead">
                  {r}
                </th>
                {cols.map((c, j) => {
                  const v = values[i]?.[j];
                  if (!isNum(v)) return <td key={c} className="empty" />;
                  const bg = divergingColor(v, colors, m);
                  const isBest = best && best[0] === i && best[1] === j;
                  return (
                    <td
                      key={c}
                      className={isBest ? "best" : ""}
                      style={{ background: bg, color: inkFor(bg), cursor: "default" }}
                      onPointerEnter={() => setHover([i, j])}
                      onPointerLeave={() => setHover(null)}
                      onFocus={() => setHover([i, j])}
                      tabIndex={0}
                      title={`${rowLabel ?? ""} ${r} / ${colLabel ?? ""} ${c}: ${format(v)}`}
                    >
                      {format(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="scale-legend">
        <span>{scaleLabel?.[0] ?? format(-m)}</span>
        <span
          className="bar"
          style={{
            background: `linear-gradient(90deg, ${divergingColor(-1, colors)}, ${divergingColor(0, colors)}, ${divergingColor(1, colors)})`,
          }}
        />
        <span>{scaleLabel?.[1] ?? format(m)}</span>
        {hover && isNum(hv) ? (
          <span style={{ marginLeft: 12 }}>
            {rowLabel} <b>{rows[hover[0]]}</b>, {colLabel} <b>{cols[hover[1]]}</b>: <b>{format(hv)}</b>
          </span>
        ) : null}
      </div>
    </div>
  );
}
