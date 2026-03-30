// v26.0: Types for Analysis pages (AILogicPanel, TradeHistoryTable, PerformanceDashboard)
// Re-export dashboard types for backward compatibility
export type { DashboardState, AIState, AIStats } from './types/dashboard';

export interface AIAnalysisEntry {
    bias: string;
    confidence: number;
    action: string;
    reason: string;
    key_level: number;
    timestamp: string;
}

export interface TradeLogEntry {
    timestamp: string;
    signal_id: string;
    direction: string;
    signal_type: string;
    score: number;
    entry_price: number;
    stop_loss: number;
    take_profit: number;
    status: string;
    ea_opened: boolean;
    pnl: number | null;
    skip_reason?: string;
    mode?: string;
    ai_bias?: string;
    ai_confidence?: number;
    ai_reason?: string;
    exit_reason?: string;
}

export interface AIMarketResult {
    analysis_time: string;
    ai_bias: string;
    ai_confidence: number;
    ai_action: string;
    price_at_analysis: number;
    price_after_1h: number;
    price_change_pct: number;
    actual_direction: string;
    correct: boolean;
    evaluated_at: string;
}
