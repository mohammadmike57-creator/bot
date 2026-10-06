export interface AccountSummary {
  login: string;
  balance: number;
  equity: number;
  free_margin: number;
  margin_level: number;
  margin: number;
  currency: string;
  daily_pnl: number;
  total_pnl: number;
  drawdown: number;
  daily_loss_locked: boolean;
  drawdown_locked: boolean;
}

export interface Controls {
  kill_switch: boolean;
  kill_reason: string;
  close_positions: boolean;
  paused: boolean;
  safe_mode: boolean;
  safe_mode_reason: string;
}

export interface Quote {
  bid: number;
  ask: number;
  spread: number;
  time: number;
}

export interface Status {
  time: string;
  trading_mode: "PAPER" | "DEMO" | "LIVE";
  configured_mode: string;
  live_permitted: boolean;
  backend: { status: string; database: string };
  ea: {
    status: "ONLINE" | "OFFLINE";
    last_heartbeat: string | null;
    age_seconds?: number;
    account_login?: string;
    ea_version?: string;
    ea_mode?: string;
    terminal_connected?: boolean;
    trade_allowed?: boolean;
    local_kill_switch?: boolean;
    latency_ms?: number;
    quotes?: Record<string, Quote>;
    open_positions?: number;
    last_signal_at?: string | null;
    last_trade_at?: string | null;
  };
  controls: Controls | null;
  session: { primary: string; active: string[]; activity: string; market_open: boolean; market_reason: string };
  regimes: Record<string, string>;
  confidence: Record<string, number>;
  news: { status: string };
  model: { mode: string; version: string; problems: string[] };
  accounts: { mt5: AccountSummary | null; paper: AccountSummary | null };
  trades_today: number;
  error_rate: number;
}

export interface Trade {
  ticket: string;
  source: string;
  symbol: string;
  logical_symbol: string;
  strategy: string;
  direction: "BUY" | "SELL";
  entry: number;
  current: number | null;
  sl: number;
  tp: number;
  lot: number;
  risk: number;
  profit: number;
  r_multiple: number | null;
  duration_minutes: number;
  confidence: number;
  session: string;
  regime: string;
  status: "OPEN" | "CLOSED";
  close_reason: string;
  opened_at: string;
  closed_at: string | null;
  magic: number;
  be_applied: boolean;
  partial_done: boolean;
}

export interface GroupStats {
  trades: number;
  net: number;
  win_rate: number;
  profit_factor: number | null;
  avg_r: number;
}

export interface Performance {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  gross_profit: number;
  gross_loss: number;
  net_profit: number;
  profit_factor: number | null;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  avg_r: number;
  max_drawdown: number;
  total_return: number;
  sharpe: number | null;
  sortino: number | null;
  by_symbol: Record<string, GroupStats>;
  by_strategy: Record<string, GroupStats>;
  by_session: Record<string, GroupStats>;
  by_regime: Record<string, GroupStats>;
  by_direction: Record<string, GroupStats>;
  daily_pnl: Record<string, number>;
  monthly_pnl: Record<string, number>;
  equity_curve: { t: string; equity: number }[];
  drawdown_curve: { t: string; dd: number }[];
}

export interface Portfolio {
  source: string;
  equity: number;
  open_positions: number;
  total_risk: number;
  total_risk_pct: number;
  unrealized_pnl: number;
  risk_by_symbol: Record<string, number>;
  risk_by_strategy: Record<string, number>;
  risk_by_direction: Record<string, number>;
  net_notional: Record<string, number>;
  hedged_symbols: string[];
  positions_without_stop: number;
  limits: Record<string, number>;
  correlation: Record<string, Record<string, number>>;
  margin_usage: number | null;
}

export interface NewsEvent {
  event: string;
  currency: string;
  country: string;
  importance: string;
  scheduled_at: string;
  previous: string;
  forecast: string;
  actual: string;
  relevant_to: string[];
}

export interface News {
  status: string;
  provider: string;
  provider_error: string;
  last_success: string | null;
  last_error: string;
  blackout: Record<string, string>;
  events: NewsEvent[];
}

export interface ModelMeta {
  model_id: string;
  version: string;
  status: string;
  created_at: string;
  dataset_start: string | null;
  dataset_end: string | null;
  oos_metrics: Record<string, unknown>;
  validation_metrics: Record<string, unknown>;
}

export interface ModelStatus {
  mode: string;
  version: string;
  model: ModelMeta | null;
  load_error: string;
  health: {
    predictions: number;
    errors: number;
    drift_psi: number;
    recent_trades: number;
    recent_profit_factor: number | null;
    mean_confidence: number | null;
    problems: string[];
  };
  registry: ModelMeta[];
}

export interface DecisionLog {
  ts: string;
  symbol: string;
  decision: string;
  reason: string;
  signal_id: string;
  line: string;
}

export type Source = "PAPER" | "MT5";
