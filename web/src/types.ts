// Shapes of the JSON returned by the Python API (see trades/api/routes.py).

export type Num = number | null;

export interface ParamSpec {
  name: string;
  label: string;
  default: number | string | boolean | unknown[];
  /** "list": a JSON list, e.g. the consensus strategy's member strategies. */
  kind: "int" | "float" | "bool" | "choice" | "symbol" | "list";
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

export type EvidenceLevel = "strong" | "moderate" | "practitioner" | "experimental" | "benchmark";

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
  /** "classic": a published rule. "modern": a method from quant desks (filters, factor models, ML). */
  family: "classic" | "modern";
  /** What the strategy needs to work well, in plain words ("" if nothing special). */
  needs: string;
  /** For a modern method: the id of the classic rule it refines, to compare against. */
  counterpart: string;
  /** Signals are already target weights (the consensus): position-sizing settings don't apply. */
  sizes_itself: boolean;
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
  /** Symbol sets chosen without hindsight (sectors, countries, multi-asset, a frozen 2010 list). */
  universes: Record<string, { label: string; symbols: string[]; note: string }>;
  /** Benchmarks a backtest can be compared with ("ew" = equal-weight of its own symbols). */
  benchmarks: Record<string, { label: string; weights: Record<string, number> | null }>;
  disclaimer: string;
  /** The server is password-protected (hosted mode): offer to log out. */
  auth_enabled?: boolean;
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
  /** After-tax equity in a taxable account (null in a tax-advantaged one). */
  after_tax_equity: Series | null;
  tax?: { account_type: "taxable" | "tax_advantaged"; short_term_rate: number; long_term_rate: number; state_rate: number };
  cost_sensitivity?: CostSensitivity;
  /** How this result looks given every configuration you have tested on these symbols. */
  research_log?: ResearchLog;
  robustness?: Robustness;
  provider: string;
  timeframe: string;
}

export interface Robustness {
  note?: string;
  rolling?: {
    years: number;
    windows: number;
    share_beating?: number;
    median_excess?: number;
    worst_excess?: number;
    best_excess?: number;
    rows?: { start: number; end: number; strategy: number; benchmark: number }[];
  };
  starts?: { starts: number; share_ahead?: number };
  regimes?: { regime: string; share_of_time: number; strategy: number; benchmark: number }[];
  bootstrap?: Record<"strategy" | "benchmark", { cagr: number[]; max_drawdown: number[] }>;
}

export interface ResearchLog {
  runs: number;
  configurations: number;
  deflated_sharpe: Num;
  expected_max_sharpe?: Num;
  interpretation: string;
}

export interface CostSensitivityRow {
  multiplier: number;
  slippage_bps: number;
  cagr: Num;
  after_tax_cagr_if_sold: Num;
  sharpe: Num;
  max_drawdown: Num;
  cost_drag: Num;
  turnover: Num;
}

export interface CostSensitivity {
  rows: CostSensitivityRow[];
  benchmark: Omit<CostSensitivityRow, "multiplier" | "slippage_bps">;
  verdict: string;
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
  state: "long" | "short" | "flat" | "hedge" | "warming_up" | "error";
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
    weighting: "equal" | "by_category";
  };
  fresh_signals: string[];
  votes: Vote[];
  sizing: {
    side: "long" | "short" | "flat";
    shares: number;
    /** Signed target weight (negative = short), after the portfolio cap. */
    weight: number;
    /** Signed weight the rounded shares actually hold (absent when flat). */
    held_weight?: number;
    notional: number;
    stop: Num;
    stop_distance?: number;
    risk_amount: number;
    conviction: number;
    limited_by?: string;
    portfolio_scale?: number;
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

export interface PortfolioSummary {
  gross: number;
  uncapped_gross: number;
  cap: number;
  scale: number;
  positions: number;
  weighting: "equal" | "by_category";
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
  /** Suggested positions added up across the watchlist (null until the first update). */
  portfolio: PortfolioSummary | null;
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
  fractional_shares: boolean;
  account_type: "taxable" | "tax_advantaged";
  short_term_tax_rate: number;
  long_term_tax_rate: number;
  state_tax_rate: number;
  cash_yield: "tbill" | "none";
  risk_per_trade: number;
  stop_atr: number;
  max_position_pct: number;
  max_gross_exposure: number;
  consensus_weighting: "equal" | "by_category";
  allow_short: boolean;
  commission_bps: number;
  slippage_bps: number;
  paper_halted: boolean;
  paper_max_daily_loss: number;
  paper_max_orders: number;
  journal_source: string;
  onboarded: boolean;
  ui_mode: "full" | "simple";
  holdings: Holding[];
  account_types: Record<string, AccountType>;
  poll_seconds: number;
  demo_speed: number;
  has_alpaca_credentials: boolean;
}

// ---- your money ------------------------------------------------------------------------

export type AccountType = "taxable" | "tax_advantaged";

export interface Holding {
  account: string;
  symbol: string;
  description?: string;
  quantity: Num;
  value: Num;
  cost_basis: Num;
  cash: boolean;
}

export interface HoldingsState {
  holdings: Holding[];
  account_types: Record<string, AccountType>;
  summary: {
    total_value: number;
    unrealised: number;
    accounts: Record<string, { type: AccountType; value: number; cash: number; positions: number }>;
  };
}

export interface GoalResult {
  years: number[];
  p10: number[];
  p50: number[];
  p90: number[];
  p_goal: Num;
  contributed: number;
  paths: number;
  inflation: number;
  proxies: Record<string, string>;
  demo: boolean;
  history: {
    from: string;
    to: string;
    max_drawdown: number;
    peak: string;
    trough: string;
    recovered: string | null;
    months_below_peak: number;
    worst_12_months: Num;
    start_value_at_trough: number;
    annual_return: number;
  };
}

export interface DragResult {
  rows: { label: string; value: number; cost: number }[];
  final: number;
  lost: number;
  lost_share: number;
}

export interface AllocationResult {
  total: number;
  rows: { class: string; value: number; share: number; target: number; difference: number }[];
  moves: { class: string; amount: number; action: "add" | "reduce"; where: string }[];
  location: string[];
  guessed: string[];
  band: number;
}

export interface HarvestCandidate {
  account: string;
  symbol: string;
  value: number;
  loss: number;
  loss_share: Num;
  tax_deferred: [number, number];
  replacement: string | null;
  warnings: string[];
}

export interface VsSpyResult {
  universe: "watchlist" | "sectors";
  symbols: string[];
  demo: boolean;
  benchmark: string | null;
  start_time: number;
  end_time: number;
  taxable: boolean;
  strategy: Record<string, Num>;
  spy: Record<string, Num>;
  p_beats: Num;
  research_log?: ResearchLog;
  warnings: string[];
}

// ---- forward track record ----------------------------------------------------------

export interface JournalStat {
  n: number;
  mean: Num;
  hit_rate: Num;
  t: Num;
}

export interface Verdict {
  status: "PASS" | "FAIL" | "NOT YET";
  detail: string;
}

export interface TrackExperiment {
  id: string;
  first_session: string;
  last_session: string;
  sessions: number;
  rows: number;
  code_versions: string[];
  labels: Record<string, Record<string, JournalStat>>; // by horizon ("5", "21"), then label
  strategies: Record<string, Record<string, { bullish: JournalStat; other: JournalStat }>>;
  curve: Series;
  latest: { symbol: string; label: string; score: Num; weight: Num; votes: Record<string, number | null> }[];
  verdict?: Verdict;
  independent_days_21: number;
  conclusive_from: string;
}

export interface TrackRecord {
  benchmark: string;
  prices_through: string;
  experiment_id?: string;
  generated_at?: string;
  health?: string[];
  rule?: {
    registered: string | null;
    last_changed: string | null;
    changed_after_first_row: boolean;
    forward: string | null;
    paper: Verdict | null;
  };
  paper: { orders: number; filled: number; with_slippage: number; mean_slippage_bps: Num; worst_slippage_bps: Num } | null;
  experiments: TrackExperiment[];
  decision?: Decision;
}

export interface Decision {
  status: "YES, WITH CARE" | "NO" | "NOT YET" | "INVALID";
  summary: string;
  checks: { name: string; status: Verdict["status"]; detail: string }[];
}

export interface Backup {
  format: string;
  version: number;
  created: string;
  settings: Record<string, unknown>;
  files: Record<string, string>;
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

// ---- strategy simulations (arena) ---------------------------------------------------------

export interface ArenaEventKind {
  kind: string;
  label: string;
  size?: number;
  bars?: number;
  regime?: string;
  help: string;
}

export interface ArenaOptions {
  scenarios: Scenario[];
  symbols: { symbol: string; name: string; beta: number | null; idio_vol: number | null }[];
  default_symbols: string[];
  default_strategies: { id: string }[];
  events: ArenaEventKind[];
  regimes: string[];
  max_speed: number;
  provider: string;
  realtime_available: boolean;
  watchlist: string[];
}

export interface ArenaBars {
  t: number[];
  o: number[];
  h: number[];
  l: number[];
  c: number[];
  v: number[];
}

export interface ArenaAgentSnap {
  id: string;
  equity: number;
  return: number;
  drawdown: number;
  cash: number;
  gross: number;
  positions: Record<string, number>;
  weights: Record<string, number>;
  signals: Record<string, string>;
  pending: Record<string, number>;
  trades: number;
  unrealized: number;
  stopped: boolean;
}

export interface ArenaAgent extends ArenaAgentSnap {
  name: string;
  strategy_id: string;
  kind: "strategy" | "benchmark";
  category: string;
  evidence: EvidenceLevel;
  symbols: string[];
  params: Record<string, unknown>;
  sizing: Record<string, unknown>;
  pair: boolean;
  error: string | null;
  equity_curve: { t: number[]; v: number[] };
}

export interface ArenaEvent {
  t: number;
  time: number;
  agent: string | null;
  type: "fill" | "signal" | "regime" | "injected";
  symbol?: string;
  side?: "buy" | "sell";
  qty?: number;
  price?: number;
  cost?: number;
  position?: number;
  reason?: string;
  from?: string | null;
  to?: string;
  headline?: string;
  kind?: string;
}

export interface ArenaSummaryRow {
  id: string;
  name: string;
  kind: "strategy" | "benchmark";
  total_return: Num;
  cagr: Num;
  sharpe: Num;
  max_drawdown: Num;
  psr: Num;
  trades: Num;
  win_rate: Num;
  exposure: Num;
  total_costs: Num;
  beta: Num;
}

export interface ArenaSummary {
  leaderboard: ArenaSummaryRow[];
  bars: number;
  regimes: { regime: string; bars: number; drift: Num; returns: Record<string, Num> }[] | null;
  cautions: string[];
}

export interface ArenaFeedStatus {
  market?: { is_open: boolean; phase: string; next_open: string; next_close: string };
  forming?: Record<string, { t: number; o: number; h: number; l: number; c: number; v: number }>;
  last_poll?: string | null;
  error?: string | null;
}

export type ArenaStatus = "ready" | "running" | "paused" | "finished" | "error";

export interface ArenaState {
  id: string;
  /** Number of the last message this snapshot already reflects. */
  seq: number;
  created_at: number;
  status: ArenaStatus;
  error: string | null;
  config: Record<string, unknown> & { source: string; symbols: string[]; strategies: { id: string }[] };
  feed: {
    source: "simulated" | "replay" | "realtime";
    symbols: string[];
    timeframe: string;
    scenario?: string;
    scenario_label?: string;
    scenario_description?: string;
    seed?: number;
    provider?: string;
    poll_seconds?: number;
    events_available?: ArenaEventKind[];
  };
  feed_status: ArenaFeedStatus;
  notes: string[];
  cursor: number;
  start_index: number;
  first_index: number;
  progress: { done: number; total: number | null };
  speed: number;
  bars: Record<string, ArenaBars>;
  regimes: (string | null)[] | null;
  regime_drift: Record<string, number>;
  agents: ArenaAgent[];
  events: ArenaEvent[];
  market_events: ArenaEvent[];
  summary: ArenaSummary | null;
}

export interface ArenaUpdate {
  type: "update" | "finished" | "resync" | "closed" | "error";
  /** Messages are numbered in the order they are sent (not on resync/closed/error). */
  seq: number;
  status: ArenaStatus;
  cursor: number;
  progress: { done: number; total: number | null };
  bars: Record<string, ArenaBars>;
  regimes: (string | null)[];
  regime_drift?: Record<string, number>;
  equity: Record<string, number[]>;
  agents: ArenaAgentSnap[];
  events: ArenaEvent[];
  feed_status: ArenaFeedStatus;
  speed: number;
  summary: ArenaSummary | null;
  error: string | null;
  detail?: string;
}

export interface ArenaRunBrief {
  id: string;
  created_at: number;
  status: ArenaStatus;
  source: string;
  title: string;
  symbols: string[];
  strategies: number;
  progress: { done: number; total: number | null };
  leader: { name: string; return: number };
}

export interface ArenaExplanation {
  state: string;
  signal: number;
  headline: string;
  rules: { label: string; value: string; passed: boolean | null }[];
  since: number | null;
  fresh: boolean;
  exit_rule: string;
}

export interface ArenaAgentDetail extends ArenaAgent {
  explanations: Record<string, ArenaExplanation>;
  trades_list: Trade[];
  events: ArenaEvent[];
  financing: number;
  costs: number;
}
