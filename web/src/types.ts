// Shapes of the JSON returned by the Python API (see trades/api/routes.py).

export type Num = number | null;

export interface ParamSpec {
  name: string;
  label: string;
  default: number | string | boolean;
  kind: "int" | "float" | "bool" | "choice" | "symbol";
  min: Num;
  max: Num;
  step: Num;
  choices: string[];
  help: string;
}

export interface Reference {
  authors: string;
  year: number;
  title: string;
  venue: string;
  url: string | null;
}

export interface Overlay {
  column: string;
  label: string;
  pane: "price" | "lower";
  style: "solid" | "dashed" | "dotted";
  levels: number[];
  values?: Num[];
}

export type EvidenceLevel = "strong" | "moderate" | "practitioner" | "benchmark";

export interface StrategyMeta {
  id: string;
  name: string;
  category: string;
  kind: "single" | "pair" | "cross_sectional";
  summary: string;
  rules: string[];
  rationale: string;
  failure_modes: string;
  evidence: EvidenceLevel;
  evidence_text: string;
  references: Reference[];
  params: ParamSpec[];
  default_sizing: Record<string, number | string>;
  overlays: Overlay[];
  min_symbols: number;
  max_symbols: number | null;
  uses_short: boolean;
}

export interface MetricInfo {
  label: string;
  fmt: "pct" | "ratio" | "x" | "money" | "int" | "bars";
  better: "higher" | "lower" | null;
  help: string;
}

export interface ProviderInfo {
  id: string;
  label: string;
  description: string;
  requires_key: boolean;
  realtime: string;
  timeframes: string[];
  docs_url: string | null;
  key_fields: string[];
  configured: boolean;
  active: boolean;
}

export interface Scenario {
  id: string;
  label: string;
  description: string;
  difficulty: string;
}

export interface Preset {
  id: string;
  label: string;
  symbol: string;
  start: string;
  description: string;
}

export interface Meta {
  version: string;
  strategies: StrategyMeta[];
  metrics: Record<string, MetricInfo>;
  providers: ProviderInfo[];
  timeframes: { value: string; label: string }[];
  scenarios: Scenario[];
  presets: Preset[];
  default_advisors: { id: string }[];
  universe: { symbol: string; name: string }[];
  disclaimer: string;
}

export interface Bars {
  t: number[];
  o: Num[];
  h: Num[];
  l: Num[];
  c: Num[];
  v: Num[];
}

export interface Series {
  t: number[];
  v: Num[];
}

export type Metrics = Record<string, Num>;

export interface Trade {
  id: number;
  symbol: string;
  direction: "long" | "short";
  entry_time: number;
  entry_price: number;
  exit_time: number | null;
  exit_price: number | null;
  qty: number;
  pnl: number;
  return_pct: number;
  commission: number;
  slippage: number;
  bars_held: number | null;
  mfe: number;
  mae: number;
  is_open: boolean;
  tags: string[];
  notes: string[];
}

export interface FillMarker {
  t: number;
  side: "buy" | "sell";
  qty: number;
  price: number;
}

export interface ChartPayload {
  symbol: string;
  bars: Bars;
  overlays: Overlay[];
  markers: FillMarker[];
  signal: Num[] | null;
}

export interface Sizing {
  method: "fixed" | "vol_target" | "atr_risk";
  allocation: number;
  target_vol: number;
  vol_com: number;
  risk_per_trade: number;
  stop_atr: number;
  atr_length: number;
  max_leverage: number;
  rebalance_every: number;
}

export interface BacktestResult {
  strategy: StrategyMeta & { params: Record<string, unknown> };
  sizing: Sizing;
  config: Record<string, number | boolean | string>;
  symbols: string[];
  start_time: number;
  end_time: number;
  equity: Series;
  drawdown: Series;
  exposure: Series;
  metrics: Metrics;
  monthly_returns: { year: number; months: Num[]; total: Num }[];
  trades: Trade[];
  charts: ChartPayload[];
  warnings: string[];
  pending_orders: Record<string, number>;
  benchmark?: { label: string; equity: Series; drawdown: Series; metrics: Metrics };
  provider: string;
  timeframe: string;
}

export interface OptimizeRow {
  params: Record<string, number | string | boolean>;
  metrics: Metrics;
}

export interface GridResult {
  mode: "grid";
  objective: string;
  rows: OptimizeRow[];
  rejected: { params: Record<string, unknown>; error: string }[];
  best: OptimizeRow;
  n_trials: number;
  deflated_sharpe: Num;
  psr_best: Num;
  expected_max_sharpe_under_null: Num;
  interpretation: string;
  grid: Record<string, (number | string | boolean)[]>;
}

export interface WalkWindow {
  train_start: number;
  train_end: number;
  test_start: number;
  test_end: number;
  best_params: Record<string, number | string | boolean>;
  in_sample: Metrics;
  out_of_sample: Metrics;
}

export interface WalkForwardResult {
  mode: "walk_forward";
  objective: string;
  windows: WalkWindow[];
  oos_equity: Series;
  default_equity: Series;
  oos_metrics: Metrics;
  default_metrics: Metrics;
  in_sample_mean: Num;
  efficiency: Num;
  interpretation: string;
  grid: Record<string, (number | string | boolean)[]>;
}

export type OptimizeResult = GridResult | WalkForwardResult;

export interface RuleCheck {
  label: string;
  value: string;
  passed: boolean | null;
}

export interface Evidence {
  cagr?: Num;
  sharpe?: Num;
  max_drawdown?: Num;
  win_rate?: Num;
  n_trades?: Num;
  psr?: Num;
  exposure?: Num;
  total_return?: Num;
  years?: Num;
  benchmark_cagr?: Num;
  benchmark_sharpe?: Num;
  benchmark_max_drawdown?: Num;
  error?: string;
}

export interface Vote {
  strategy_id: string;
  strategy_name: string;
  category: string;
  evidence_level: EvidenceLevel;
  vote: -1 | 0 | 1;
  state: "long" | "short" | "flat" | "warming_up" | "error";
  headline: string;
  rules: RuleCheck[];
  since: number | null;
  fresh: boolean;
  exit_rule: string;
  evidence: Evidence | null;
  group: string[] | null;
  params: Record<string, unknown>;
}

export interface Recommendation {
  symbol: string;
  name: string | null;
  price: number;
  change_pct: Num;
  as_of: number;
  provisional: boolean;
  consensus: {
    score: number;
    action: "BUY" | "SELL" | "SHORT" | "HOLD";
    label: string;
    bullish: number;
    bearish: number;
    neutral: number;
    n_votes: number;
    agreement: Num;
  };
  fresh_signals: string[];
  votes: Vote[];
  sizing: {
    side: "long" | "short" | "flat";
    shares: number;
    weight: number;
    notional: number;
    stop: Num;
    stop_distance?: number;
    risk_amount: number;
    conviction: number;
    limited_by?: string;
    explanation: string;
  };
  risk: {
    atr: Num;
    atr_pct: Num;
    vol_20: Num;
    vol_1y: Num;
    avg_dollar_volume: Num;
    high_52w: Num;
    low_52w: Num;
    flags: string[];
  };
}

export interface LiveQuote {
  symbol: string;
  price: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  prev_close: number;
  change_pct: number;
  time: number;
  spark: number[];
}

export interface MarketInfo {
  is_open: boolean;
  phase: string;
  session_date: string;
  next_open?: string;
  next_close?: string;
  progress?: number;
  clock?: string;
  speed?: number;
}

export interface LiveSnapshot {
  status: string;
  error: string | null;
  last_update: string | null;
  running: boolean;
  provider: string;
  provider_label: string;
  realtime: string | null;
  timeframe: string;
  symbols: string[];
  quotes: Record<string, LiveQuote>;
  errors: Record<string, string>;
  recommendations: Recommendation[];
  notes: string[];
  provisional: boolean;
  market: MarketInfo;
  demo: boolean;
  streaming: boolean;
}

export interface Explanation {
  symbol: string;
  state: string;
  signal: number;
  headline: string;
  rules: RuleCheck[];
  since: number | null;
  fresh: boolean;
  exit_rule: string;
}

export interface ChartResponse {
  symbol: string;
  strategy: string;
  timeframe: string;
  bars: Bars;
  overlays: Overlay[];
  markers: { t: number; kind: string }[];
  explanation: Explanation | null;
}

export interface Settings {
  provider: string;
  timeframe: string;
  watchlists: Record<string, string[]>;
  advisors: { id: string; params?: Record<string, unknown>; sizing?: Record<string, unknown> | null }[];
  pairs: string[][];
  alpaca_key_id: string;
  alpaca_secret_key: string;
  alpaca_feed: string;
  csv_dir: string;
  csv_dir_resolved: string;
  synthetic_seed: number;
  account_equity: number;
  risk_per_trade: number;
  stop_atr: number;
  max_position_pct: number;
  allow_short: boolean;
  commission_bps: number;
  slippage_bps: number;
  poll_seconds: number;
  demo_speed: number;
  has_alpaca_credentials: boolean;
}

// ---- simulator -------------------------------------------------------------------

export interface SimOrder {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  type: "market" | "limit" | "stop";
  limit_price: Num;
  stop_price: Num;
  tif: string;
  status: "open" | "pending" | "filled" | "canceled" | "rejected" | "expired";
  created_index: number;
  created_time: number;
  tag: string;
  parent_id: string | null;
  oco_group: string | null;
  note: string;
  filled_index: number | null;
  filled_time: number | null;
  fill_price: Num;
  filled_qty: number;
  reason: string;
}

export interface Diagnostic {
  id: string;
  title: string;
  status: "good" | "warn" | "bad" | "info";
  value: string;
  detail: string;
  lesson: string;
  reference: Reference | null;
}

export interface LeaderRow {
  name: string;
  kind: "you" | "benchmark" | "strategy";
  total_return: Num;
  max_drawdown: Num;
  sharpe: Num;
  trades: Num;
}

export interface Scorecard {
  you: Metrics;
  benchmark: Metrics;
  leaderboard: LeaderRow[];
  rank: number;
  of: number;
  excess_return: number;
  process_score: number | null;
  verdict: string;
  diagnostics: Diagnostic[];
  bars: number;
}

export interface SimAdvisor {
  id: string;
  name: string;
  category: string;
  explanation: Explanation;
  equity: number[];
  return: number;
}

export interface SimState {
  id: string;
  title: string;
  description: string;
  symbol: string;
  config: Record<string, unknown> & { reveal: string; allow_short: boolean; initial_cash: number };
  cursor: number;
  start_index: number;
  end_index: number;
  progress: { done: number; total: number };
  finished: boolean;
  bars_from: number;
  bars: Bars;
  time: number;
  account: {
    initial: number;
    cash: number;
    equity: number;
    return_pct: number;
    buying_power: number;
    realized_pnl: number;
    costs: number;
    position: { qty: number; avg_price: Num; market_value: number; unrealized_pnl: number; side: string };
  };
  equity_curve: Series;
  benchmark_curve: Series;
  orders: SimOrder[];
  trades: Trade[];
  fills: { order_id: string | null; time: number; qty: number; price: number; tag: string }[];
  advisors: SimAdvisor[];
  consensus: Num;
  journal: { index: number; time: number; text: string }[];
  scorecard: Scorecard | null;
  reveal: {
    kind: "scenario" | "history";
    label?: string;
    scenario?: string;
    seed?: number;
    regimes?: { regime: string; start: number; end: number }[];
    symbol?: string;
    provider?: string;
    blind?: boolean;
    first_date?: string;
    last_date?: string;
    preset?: string | null;
    scale?: number;
  } | null;
  new_fills?: { order_id: string; side: string; qty: number; price: number; tag: string; time: number }[];
}

export interface SimHistoryRow {
  id: string;
  finished_at: number;
  title: string;
  symbol: string;
  bars: number;
  return: Num;
  benchmark_return: Num;
  max_drawdown: Num;
  trades: Num;
  process_score: number | null;
  rank: number | null;
}
