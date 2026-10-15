import type {
  ArenaAgentDetail,
  ArenaEvent,
  ArenaOptions,
  ArenaRunBrief,
  ArenaState,
  ArenaSummary,
  BacktestResult,
  Backup,
  ChartResponse,
  LiveSnapshot,
  Meta,
  OptimizeResult,
  Recommendation,
  Settings,
  SimHistoryRow,
  SimOrder,
  SimState,
  TrackRecord,
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

// What a missing server looks like: the page came from a static host (such as Vercel) that answers
// the API's addresses with its own "not found" page instead of the Trades server's JSON.
const NO_SERVER =
  "The Trades server isn't running at this address: this page is only the app's front end. Trades also needs its " +
  "Python server, which static hosts such as Vercel can't run. Run it on your computer or on a server " +
  "(see \"Put it online\" in the README).";

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
    let code: string | undefined;
    let fromServer = false;
    try {
      const body = await res.json();
      if (body && body.detail !== undefined) {
        message = describe(body.detail);
        fromServer = true;
      }
      code = body?.code;
    } catch {
      /* not JSON */
    }
    if (!fromServer && (res.status === 404 || res.status === 405) && path.startsWith("/api/")) message = NO_SERVER;
    if (res.status === 401 && code === "login_required") {
      // A password-protected server and no (or an expired) login: log in, then come back here.
      window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}${window.location.hash}`);
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
  /** Password-protected servers only: end this browser's login. */
  logout: () => fetch("/api/logout", { method: "POST" }).then(() => undefined),
  meta: () => request<Meta>("/api/meta"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (patch: Partial<Settings> | Record<string, unknown>) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(patch) }),
  trackRecord: () => request<{ source: string; record: TrackRecord | null }>("/api/track-record"),
  backup: () => request<Backup>("/api/backup"),
  restore: (backup: unknown) =>
    post<{ settings: string[]; lines_added: Record<string, number>; settings_now: Settings }>("/api/restore", backup),
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
  arenaOptions: () => request<ArenaOptions>("/api/arena/options"),
  arenaRuns: () => request<ArenaRunBrief[]>("/api/arena/runs"),
  arenaCreate: (config: Record<string, unknown>) => post<ArenaState>("/api/arena/runs", config),
  arenaState: (id: string) => request<ArenaState>(`/api/arena/runs/${id}`),
  arenaAgent: (id: string, agentId: string) => request<ArenaAgentDetail>(`/api/arena/runs/${id}/agents/${agentId}`),
  arenaControl: (id: string, body: { action: "play" | "pause" | "step" | "speed" | "stop"; n?: number; speed?: number }) =>
    post<{ status: ArenaState["status"]; cursor: number; speed: number; summary: ArenaSummary | null }>(
      `/api/arena/runs/${id}/control`,
      body,
    ),
  arenaInject: (id: string, body: { kind: string; size?: number; bars?: number; symbol?: string; regime?: string }) =>
    post<ArenaEvent>(`/api/arena/runs/${id}/inject`, body),
  arenaDelete: (id: string) => request<{ deleted: string }>(`/api/arena/runs/${id}`, { method: "DELETE" }),
};

export function arenaSocketUrl(id: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/arena/${id}`;
}

export function liveSocketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/live`;
}
