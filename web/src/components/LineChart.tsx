import { useEffect, useMemo, useRef, useState } from "react";
import {
  BaselineSeries,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Series } from "../types";
import { isNum } from "../format";
import { useChartColors, withAlpha } from "../theme";

export interface LineSpec {
  id: string;
  label: string;
  data: Series;
  color: string;
  kind?: "line" | "baseline";
  style?: "solid" | "dashed";
}

interface Props {
  lines: LineSpec[];
  height?: number;
  format: (v: number) => string;
  /** Show a legend box. Defaults to true for 2+ series (a single series is named by its title). */
  legend?: boolean;
  label?: string;
  toggleable?: boolean;
}

const UTC_DATE = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });

export default function LineChart({ lines, height = 240, format, legend, label, toggleable = false }: Props) {
  const colors = useChartColors();
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Line"> | ISeriesApi<"Baseline">>>(new Map());
  const [hover, setHover] = useState<number | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const showLegend = legend ?? lines.length >= 2;

  const lookups = useMemo(
    () =>
      lines.map((l) => {
        const m = new Map<number, number | null>();
        l.data.t.forEach((t, i) => m.set(t, l.data.v[i]));
        return m;
      }),
    [lines],
  );

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: { borderVisible: true },
      timeScale: { borderVisible: true, rightOffset: 2 },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
      handleScale: { mouseWheel: false },
      localization: {
        priceFormatter: (p: number) => format(p),
        timeFormatter: (t: Time) => UTC_DATE.format(new Date((t as number) * 1000)),
      },
    });
    chartRef.current = chart;
    const onMove = (p: MouseEventParams<Time>) => setHover(p.time === undefined ? null : (p.time as number));
    chart.subscribeCrosshairMove(onMove);
    const map = seriesRef.current;
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      map.clear();
    };
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: {
        background: { color: colors.surface },
        textColor: colors.axisText,
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        fontSize: 11,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.axis },
      timeScale: { borderColor: colors.axis },
      crosshair: {
        vertLine: { color: colors.axisText, labelBackgroundColor: colors.textSecondary, style: LineStyle.Solid, width: 1 },
        horzLine: { visible: false, labelVisible: false },
      },
    });
  }, [colors]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const wanted = new Set(lines.map((l) => l.id));
    seriesRef.current.forEach((s, id) => {
      if (!wanted.has(id)) {
        chart.removeSeries(s);
        seriesRef.current.delete(id);
      }
    });
    lines.forEach((l) => {
      let s = seriesRef.current.get(l.id);
      if (!s) {
        s =
          l.kind === "baseline"
            ? chart.addSeries(BaselineSeries, {
                baseValue: { type: "price", price: 0 },
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: false,
              })
            : chart.addSeries(LineSeries, {
                lineWidth: 2,
                priceLineVisible: false,
                lastValueVisible: false,
                crosshairMarkerRadius: 4,
              });
        seriesRef.current.set(l.id, s);
      }
      if (l.kind === "baseline") {
        (s as ISeriesApi<"Baseline">).applyOptions({
          topLineColor: l.color,
          bottomLineColor: l.color,
          topFillColor1: withAlpha(l.color, 0.1),
          topFillColor2: withAlpha(l.color, 0.1),
          bottomFillColor1: withAlpha(l.color, 0.1),
          bottomFillColor2: withAlpha(l.color, 0.1),
          visible: !hidden.has(l.id),
        });
      } else {
        (s as ISeriesApi<"Line">).applyOptions({
          color: l.color,
          lineStyle: l.style === "dashed" ? LineStyle.Dashed : LineStyle.Solid,
          visible: !hidden.has(l.id),
        });
      }
      s.setData(
        l.data.t.map((t, i) =>
          isNum(l.data.v[i]) ? { time: t as UTCTimestamp, value: l.data.v[i] as number } : { time: t as UTCTimestamp },
        ),
      );
    });
    chart.timeScale().fitContent();
  }, [lines, hidden]);

  const toggle = (id: string) => {
    if (!toggleable) return;
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const valueAt = (i: number): number | null => {
    const l = lines[i];
    if (hover !== null) {
      const v = lookups[i].get(hover);
      if (v !== undefined) return v;
    }
    for (let k = l.data.v.length - 1; k >= 0; k--) if (isNum(l.data.v[k])) return l.data.v[k] as number;
    return null;
  };
  const shownTime = hover ?? (lines[0]?.data.t[lines[0].data.t.length - 1] ?? null);

  return (
    <figure className="chart-box" style={{ margin: 0 }} aria-label={label}>
      <div className={showLegend ? "legend" : "chart-readout"}>
        {shownTime !== null ? <span className="muted">{UTC_DATE.format(new Date(shownTime * 1000))}</span> : null}
        {lines.map((l, i) => {
          const v = valueAt(i);
          const content = (
            <>
              <span className="line" style={{ background: l.color }} />
              {showLegend ? `${l.label} ` : ""}
              <b>{isNum(v) ? format(v) : "–"}</b>
            </>
          );
          return toggleable ? (
            <button
              type="button"
              className="key"
              key={l.id}
              aria-pressed={!hidden.has(l.id)}
              onClick={() => toggle(l.id)}
              title="Show/hide"
            >
              {content}
            </button>
          ) : (
            <span className="key" key={l.id}>
              {content}
            </span>
          );
        })}
      </div>
      <div ref={boxRef} style={{ height, width: "100%" }} />
    </figure>
  );
}
