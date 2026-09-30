import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bars, Overlay } from "../types";
import { fmtCompact, fmtPct, fmtPrice, isNum } from "../format";
import { useChartColors, withAlpha, type ChartColors } from "../theme";

export interface PriceMarker {
  time: number;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text?: string;
}

export interface PriceLineSpec {
  price: number;
  label: string;
  color: string;
  style?: "solid" | "dashed" | "dotted";
}

interface Props {
  bars: Bars;
  overlays?: Overlay[];
  markers?: PriceMarker[];
  priceLines?: PriceLineSpec[];
  height?: number;
  showVolume?: boolean;
  intraday?: boolean;
  follow?: boolean;
  initialBars?: number;
  label?: string;
}

const NY_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const NY_DATE = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" });
const UTC_DATE = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });

function lineStyle(style: string | undefined): LineStyle {
  return style === "dashed" ? LineStyle.Dashed : style === "dotted" ? LineStyle.Dotted : LineStyle.Solid;
}

/** Solid overlays take categorical slots 2..n (slot 1's blue is the up-candle colour);
 *  dotted/dashed reference lines (bands, channels, stops) stay in muted ink. */
export function overlayColors(overlays: Overlay[], colors: ChartColors): Record<string, string> {
  const out: Record<string, string> = {};
  let slot = 1;
  for (const ov of overlays) {
    if (ov.style === "solid" || !ov.style) {
      out[ov.column] = colors.series[Math.min(slot, colors.series.length - 1)];
      slot += 1;
    } else {
      out[ov.column] = colors.axisText;
    }
  }
  return out;
}

function lastIndex<T>(arr: (T | null)[], i: number): number {
  let k = Math.min(i, arr.length - 1);
  while (k >= 0 && arr[k] == null) k--;
  return k;
}

export default function PriceChart({
  bars,
  overlays = [],
  markers = [],
  priceLines = [],
  height = 360,
  showVolume = true,
  intraday = false,
  follow = false,
  initialBars,
  label,
}: Props) {
  const colors = useChartColors();
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRefs = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const levelRefs = useRef<IPriceLine[]>([]);
  const markerApi = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLineRefs = useRef<IPriceLine[]>([]);
  const loadedRef = useRef(0);
  const [hover, setHover] = useState<number | null>(null);

  const lower = overlays.filter((o) => o.pane === "lower");
  const priceOverlays = overlays.filter((o) => o.pane !== "lower");
  const ovColors = useMemo(() => overlayColors(priceOverlays, colors), [priceOverlays, colors]);
  const lowerColors = useMemo(() => overlayColors(lower, colors), [lower, colors]);
  const volumePane = showVolume ? 1 : -1;
  const lowerPane = showVolume ? 2 : 1;
  const totalHeight = height + (showVolume ? 70 : 0) + (lower.length ? 120 : 0);

  const timeIndex = useMemo(() => {
    const m = new Map<number, number>();
    bars.t.forEach((t, i) => m.set(t, i));
    return m;
  }, [bars]);

  // Create the chart once.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: true },
      timeScale: {
        borderVisible: true,
        rightOffset: 4,
        minBarSpacing: 0.05,
        timeVisible: intraday,
        secondsVisible: false,
        // Intraday axis in exchange time (ET); the session's first bar shows the date.
        tickMarkFormatter: intraday
          ? (t: Time) => {
              const d = new Date((t as number) * 1000);
              const hm = NY_TIME.format(d);
              return hm === "09:30" ? NY_DATE.format(d) : hm;
            }
          : undefined,
      },
      localization: {
        timeFormatter: (t: Time) => {
          const d = new Date((t as number) * 1000);
          return intraday ? `${NY_DATE.format(d)} ${NY_TIME.format(d)} ET` : UTC_DATE.format(d);
        },
      },
    });
    const candles = chart.addSeries(CandlestickSeries, { borderVisible: false, priceLineVisible: false });
    candleRef.current = candles;
    markerApi.current = createSeriesMarkers(candles, []);
    chartRef.current = chart;
    const onMove = (p: MouseEventParams<Time>) => {
      if (p.time === undefined) setHover(null);
      else setHover(p.time as number);
    };
    chart.subscribeCrosshairMove(onMove);
    const overlaysMap = overlayRefs.current;
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volRef.current = null;
      overlaysMap.clear();
      loadedRef.current = 0;
    };
  }, [intraday]);

  // Theme colours.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.applyOptions({
      layout: {
        background: { color: colors.surface },
        textColor: colors.axisText,
        fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
        fontSize: 11,
        attributionLogo: true,
        panes: { separatorColor: colors.grid, separatorHoverColor: colors.axis },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.axis },
      timeScale: { borderColor: colors.axis },
      crosshair: {
        vertLine: { color: colors.axisText, labelBackgroundColor: colors.textSecondary, style: LineStyle.Solid, width: 1 },
        horzLine: { color: colors.axisText, labelBackgroundColor: colors.textSecondary, style: LineStyle.Solid, width: 1 },
      },
    });
    candleRef.current?.applyOptions({
      upColor: colors.up,
      downColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    });
    volRef.current?.applyOptions({ color: withAlpha(colors.deemph, 0.45) });
    overlayRefs.current.forEach((s, key) => {
      const c = ovColors[key] ?? lowerColors[key];
      if (c) s.applyOptions({ color: c });
    });
  }, [colors, ovColors, lowerColors]);

  // Data.
  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    if (!chart || !candles) return;
    const data = [];
    for (let i = 0; i < bars.t.length; i++) {
      const o = bars.o[i], h = bars.h[i], l = bars.l[i], c = bars.c[i];
      if (isNum(o) && isNum(h) && isNum(l) && isNum(c)) {
        data.push({ time: bars.t[i] as UTCTimestamp, open: o, high: h, low: l, close: c });
      }
    }
    const logical = chart.timeScale().getVisibleLogicalRange();
    const wasAtEnd = logical ? logical.to >= loadedRef.current - 2 : true;
    candles.setData(data);

    // volume pane
    if (showVolume) {
      if (!volRef.current) {
        volRef.current = chart.addSeries(
          HistogramSeries,
          { color: withAlpha(colors.deemph, 0.45), priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false },
          volumePane,
        );
      }
      volRef.current.setData(
        bars.t.map((t, i) => ({ time: t as UTCTimestamp, value: isNum(bars.v[i]) ? (bars.v[i] as number) : 0 })),
      );
    } else if (volRef.current) {
      chart.removeSeries(volRef.current);
      volRef.current = null;
    }

    // overlays
    const wanted = new Set(overlays.map((o) => o.column));
    overlayRefs.current.forEach((s, key) => {
      if (!wanted.has(key)) {
        chart.removeSeries(s);
        overlayRefs.current.delete(key);
      }
    });
    levelRefs.current = [];
    for (const ov of overlays) {
      const isLower = ov.pane === "lower";
      const color = (isLower ? lowerColors : ovColors)[ov.column] ?? colors.series[1];
      let s = overlayRefs.current.get(ov.column);
      if (s) {
        // pane may have changed; recreate to keep it simple
        chart.removeSeries(s);
        overlayRefs.current.delete(ov.column);
      }
      s = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 2,
          lineStyle: lineStyle(ov.style),
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          title: "",
        },
        isLower ? lowerPane : 0,
      );
      overlayRefs.current.set(ov.column, s);
      const values = ov.values ?? [];
      s.setData(
        bars.t.map((t, i) =>
          isNum(values[i]) ? { time: t as UTCTimestamp, value: values[i] as number } : { time: t as UTCTimestamp },
        ),
      );
      for (const level of ov.levels ?? []) {
        levelRefs.current.push(
          s.createPriceLine({ price: level, color: colors.axis, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: false }),
        );
      }
    }
    const panes = chart.panes();
    if (panes[0]) panes[0].setStretchFactor(3);
    if (showVolume && panes[1]) panes[1].setStretchFactor(0.6);
    if (lower.length && panes[lowerPane]) panes[lowerPane].setStretchFactor(1.1);

    // viewport
    if (loadedRef.current === 0) {
      if (initialBars && data.length > initialBars) {
        chart.timeScale().setVisibleLogicalRange({ from: data.length - initialBars, to: data.length + 3 });
      } else {
        chart.timeScale().fitContent();
      }
    } else if (follow && wasAtEnd) {
      chart.timeScale().scrollToRealTime();
    }
    loadedRef.current = data.length;
  }, [bars, overlays, showVolume]);

  // Markers.
  useEffect(() => {
    const api = markerApi.current;
    if (!api) return;
    const sorted: SeriesMarker<Time>[] = [...markers]
      .sort((a, b) => a.time - b.time)
      .map((m) => ({ time: m.time as UTCTimestamp, position: m.position, shape: m.shape, color: m.color, text: m.text }));
    api.setMarkers(sorted);
  }, [markers, bars]);

  // Horizontal price lines (stops, entries).
  useEffect(() => {
    const candles = candleRef.current;
    if (!candles) return;
    priceLineRefs.current.forEach((pl) => candles.removePriceLine(pl));
    priceLineRefs.current = priceLines.map((pl) =>
      candles.createPriceLine({
        price: pl.price,
        color: pl.color,
        lineWidth: 1,
        lineStyle: lineStyle(pl.style ?? "dashed"),
        axisLabelVisible: true,
        title: pl.label,
      }),
    );
  }, [priceLines, bars]);

  // Readout: hovered bar, or the latest bar when not hovering (values never hidden behind hover).
  const n = bars.t.length;
  const idx = hover !== null && timeIndex.has(hover) ? (timeIndex.get(hover) as number) : lastIndex(bars.c, n - 1);
  const o = idx >= 0 ? bars.o[idx] : null;
  const h = idx >= 0 ? bars.h[idx] : null;
  const l = idx >= 0 ? bars.l[idx] : null;
  const c = idx >= 0 ? bars.c[idx] : null;
  const prevIdx = lastIndex(bars.c, idx - 1);
  const prevClose = prevIdx >= 0 ? bars.c[prevIdx] : null;
  const chg = isNum(c) && isNum(prevClose) && prevClose ? c / prevClose - 1 : null;
  const date = idx >= 0 ? bars.t[idx] : null;

  return (
    <figure className="chart-box" style={{ margin: 0 }} aria-label={label ?? "Price chart"}>
      <div className="chart-readout" aria-live="off">
        <span>
          {date !== null
            ? intraday
              ? `${NY_DATE.format(new Date(date * 1000))} ${NY_TIME.format(new Date(date * 1000))} ET`
              : UTC_DATE.format(new Date(date * 1000))
            : ""}
        </span>
        <span>
          O <b>{fmtPrice(o)}</b>
        </span>
        <span>
          H <b>{fmtPrice(h)}</b>
        </span>
        <span>
          L <b>{fmtPrice(l)}</b>
        </span>
        <span>
          C <b>{fmtPrice(c)}</b>
        </span>
        <span className={chg == null ? "" : chg >= 0 ? "upc" : "downc"}>{fmtPct(chg, 2)}</span>
        {showVolume && idx >= 0 ? (
          <span>
            Vol <b>{fmtCompact(bars.v[idx])}</b>
          </span>
        ) : null}
        {overlays.map((ov) => {
          const val = idx >= 0 && ov.values ? ov.values[idx] : null;
          const color = (ov.pane === "lower" ? lowerColors : ovColors)[ov.column];
          return (
            <span className="key" key={ov.column}>
              <span
                className={`line ${ov.style === "dotted" ? "dotted" : ov.style === "dashed" ? "dashed" : ""}`}
                style={{ background: color }}
              />
              {ov.label} <b>{isNum(val) ? (Math.abs(val) < 10 && ov.pane === "lower" ? val.toFixed(2) : fmtPrice(val)) : "–"}</b>
            </span>
          );
        })}
      </div>
      <div ref={boxRef} style={{ height: totalHeight, width: "100%" }} />
    </figure>
  );
}
