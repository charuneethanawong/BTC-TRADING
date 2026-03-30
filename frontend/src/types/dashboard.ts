export interface AIState {
    enabled: boolean;
    bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
    confidence: number;
    action: 'TRADE' | 'WAIT' | 'CAUTION';
    reason: string;
    key_level: number;
    last_update: string;
}

export interface MarketContext {
    ema9: number;
    ema20: number;
    ema50: number;
    ema_trend: string;
    h1_dist_pct: number;
    pullback_status: string;
    wall_info: string;
}

export interface BiasLayers {
    lc: string;
    lr: string;
    lr_count: number;
    l0: string;
    l1: string;
    l2: string;
    l3: string;
}

export interface ModeResult {
    active: boolean;
    score: number;
    threshold: number;
    direction: string;
    signal_sent: boolean;
    breakdown: Record<string, any>;
}

export interface SignalInfo {
    signal_id: string;
    mode: string;
    direction: string;
    entry_price: number;
    stop_loss: number;
    take_profit: number;
    score: number;
    rr: number;
    time: string;
}

export interface MLVPState {
    composite_poc: number;
    composite_vah: number;
    composite_val: number;
    current_session: string;
    confluence_zones: Array<{
        price: number;
        layers: string[];
        strength: number;
    }>;
}

export interface AIStats {
    total: number;
    wins: number;
    losses: number;
    win_rate: number;
    skipped: number;
    opened: number;
}

export interface OrderFlowState {
    delta: number;
    volume_24h: number;
    oi: number;
    oi_change: number;
    liquidations: number;
    der: number;
    funding_rate: number;
}

export interface AccountInfo {
    balance: number;
    equity: number;
    profit: number;
    leverage: number;       // v25.0: from config
    drawdown_pct: number;   // v25.0: current drawdown %
}

export interface Position {
    ticket: number;
    symbol: string;
    type: 'BUY' | 'SELL';
    volume: number;
    price_open: number;
    sl: number;
    tp: number;
    profit: number;
}

export interface DashboardState {
    price: number;
    session: string;
    regime: string;
    timestamp: string;
    cycle_time: number;
    cycle_count: number;
    bot_uptime: string;
    ai: AIState;
    market: MarketContext;
    bias_layers: BiasLayers;
    modes: Record<string, ModeResult>;
    last_signal: SignalInfo;
    mlvp: MLVPState;
    order_flow: OrderFlowState;  // v25.0: added
    ai_stats: AIStats;
    account: AccountInfo;
    positions: Position[];
    price_history: number[];  // v25.0: last 50 M5 close prices for chart
}
// Dummy export to prevent the module from being empty after interface removal in JS
export const DASHBOARD_TYPES_LOADED = true;
