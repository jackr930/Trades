import type {
  BacktestResult,
  ChartResponse,
  LiveSnapshot,
  Meta,
  OptimizeResult,
  Recommendation,
  Settings,
  SimHistoryRow,
  SimOrder,
  SimState,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string };
        const loc = (item.loc ?? []).filter((x) => x !== "body").join(".");
        return loc ? `${loc}: ${item.msg}` : String(item.msg);
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Cannot reach the Trades server. Is `trades serve` running?", 0);
  }
  if (!res.ok) {
    let message = res.statusText || `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail !== undefined) message = describe(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, res.status);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface BacktestRequest {
  strategy: { id: string; params: Record<string, unknown>; sizing?: Record<string, unknown> | null };
  symbols: string[];
  timeframe: string;
  start?: string | null;
  end?: string | null;
  provider?: string | null;
  config?: Record<string, unknown>;
}

export interface OptimizeRequest extends BacktestRequest {
  grid: Record<string, { min?: number; max?: number; step?: number; values?: unknown[] }>;
  objective: string;
  mode: "grid" | "walk_forward";
  train_bars?: number;
  test_bars?: number;
  anchored?: boolean;
}

export interface OrderRequest {
  side: "buy" | "sell";
  qty: number;
  type: "market" | "limit" | "stop";
  limit_price?: number | null;
  stop_price?: number | null;
  tif?: "gtc" | "day";
  stop_loss?: number | null;
  take_profit?: number | null;
  note?: string;
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (patch: Partial<Settings> | Record<string, unknown>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  testProvider: (id: string) => post<{ ok: boolean; message: string }>(`/api/providers/${id}/test`),
  backtest: (body: BacktestRequest) => post<BacktestResult>("/api/backtest", body),
  optimize: (body: OptimizeRequest) => post<OptimizeResult>("/api/optimize", body),
  recommendations: (body: Record<string, unknown>) =>
    post<{ recommendations: Recommendation[]; notes: string[]; errors: Record<string, string> }>(
      "/api/recommendations",
      body,
    ),
  chart: (q: { symbol: string; strategy: string; params?: Record<string, unknown>; count?: number }) => {
    const qs = new URLSearchParams({ symbol: q.symbol, strategy: q.strategy, count: String(q.count ?? 400) });
    if (q.params && Object.keys(q.params).length) qs.set("params", JSON.stringify(q.params));
    return request<ChartResponse>(`/api/chart?${qs.toString()}`);
  },
  live: () => request<LiveSnapshot>("/api/live"),
  liveStart: () => post<LiveSnapshot>("/api/live/start"),
  liveStop: () => post<LiveSnapshot>("/api/live/stop"),
  setWatchlist: (symbols: string[]) =>
    request<LiveSnapshot>("/api/live/watchlist", { method: "PUT", body: JSON.stringify({ symbols }) }),
  simCreate: (config: Record<string, unknown>) => post<SimState>("/api/sim", config),
  simState: (id: string, since?: number) =>
    request<SimState>(`/api/sim/${id}${since !== undefined ? `?since=${since}` : ""}`),
  simStep: (id: string, n: number, since: number) => post<SimState>(`/api/sim/${id}/step`, { n, since }),
  simOrder: (id: string, order: OrderRequest) => post<{ order: SimOrder; state: SimState }>(`/api/sim/${id}/orders`, order),
  simCancel: (id: string, orderId: string) =>
    request<{ order: SimOrder; state: SimState }>(`/api/sim/${id}/orders/${orderId}`, { method: "DELETE" }),
  simClose: (id: string, note = "") => post<{ order: SimOrder; state: SimState }>(`/api/sim/${id}/close`, { note }),
  simJournal: (id: string, text: string) =>
    post<{ entry: unknown; journal: SimState["journal"] }>(`/api/sim/${id}/journal`, { text }),
  simFinish: (id: string) => post<SimState>(`/api/sim/${id}/finish`),
  simHistory: () => request<SimHistoryRow[]>("/api/sim/history"),
};

export function liveSocketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/live`;
}
