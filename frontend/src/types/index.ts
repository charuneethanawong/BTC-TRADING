export interface AIAnalysisEntry {
  bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  confidence: number;
  action: 'TRADE' | 'WAIT' | 'CAUTION';
  reason: string;
  key_level: number;
  timestamp: string;
}

export interface TradeLogEntry {
  timestamp: string;
  signal_id: string;
  direction: 'SHORT' | 'LONG';
  signal_type: 'MOMENTUM' | 'REVERSAL' | 'MEAN_REVERSE' | 'ABSORP';
  mode?: 'IPAF' | 'IOFF';
  score: number;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  status: 'WIN' | 'LOSS' | 'EA_SKIPPED' | 'OPEN';
  ea_opened: boolean;
  pnl: number | null;
  skip_reason?: string;
}

export interface AIMarketResult {
  analysis_time: string;
  ai_bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  ai_confidence: number;
  ai_action: string;
  price_at_analysis: number;
  price_after_1h: number;
  price_change_pct: number;
  actual_direction: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  correct: boolean;
  evaluated_at: string;
}
