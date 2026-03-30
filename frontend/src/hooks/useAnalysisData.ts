import { useMemo } from 'react';

// Full trade record shape as returned by GET /api/trades/log
export interface TradeRecord {
  timestamp: string;
  signal_id?: string;
  mode?: string;
  direction: string;
  signal_type?: string;
  score?: number;
  entry_price?: number;
  stop_loss?: number;
  take_profit?: number;
  session?: string;
  ai_bias?: string;
  ai_confidence?: number;
  ai_action?: string;
  ai_aligned?: boolean;
  actual_direction?: string;
  status: string;
  pnl?: number | null;
  exit_reason?: string;
  breakdown?: Record<string, unknown>;
}

export interface OverallStats {
  totalPnl: number;
  winCount: number;
  lossCount: number;
  totalClosed: number;
  winRate: number;
  profitFactor: number;
  avgWin: number;
  avgLoss: number;
}

export interface SessionStat {
  wins: number;
  losses: number;
  winRate: number;
  pnl: number;
}

export interface TypeStat {
  wins: number;
  losses: number;
  total: number;
  winRate: number;
}

export interface AIAlignment {
  alignedWin: number;
  alignedLoss: number;
  conflictWin: number;
  conflictLoss: number;
  alignedWinPnl: number;
  alignedLossPnl: number;
  conflictWinPnl: number;
  conflictLossPnl: number;
  alignedWinRate: number;
  conflictWinRate: number;
}

export interface LossPattern {
  mode: string;
  primaryCause: string;
  count: number;
  percentage: number;
}

// session -> signal_type -> winRate (0-100, -1 = no data)
export type SessionTypeMatrix = Record<string, Record<string, number>>;

export interface AnalysisData {
  overallStats: OverallStats;
  sessionStats: Record<string, SessionStat>;
  typeStats: Record<string, TypeStat>;
  aiAlignment: AIAlignment;
  lossPatterns: LossPattern[];
  sessionTypeMatrix: SessionTypeMatrix;
}
const SESSIONS = ['ASIA', 'LONDON', 'NY'] as const;
const SIGNAL_TYPES = [
  'IPA', 'IOF', 'MOMENTUM', 'ABSORPTION', 'REVERSAL',
  'MEAN_REVERT', 'FVG', 'EMA', 'POC',
] as const;

function deriveSession(trade: TradeRecord): string {
  if (trade.session) return trade.session.toUpperCase();
  try {
    const h = new Date(trade.timestamp).getUTCHours();
    if (h >= 1 && h < 9) return 'ASIA';
    if (h >= 7 && h < 16) return 'LONDON';
    return 'NY';
  } catch {
    return 'NY';
  }
}

function canonicalType(raw?: string): string {
  if (!raw) return 'UNKNOWN';
  const u = raw.toUpperCase().trim();
  if (u === 'IPAF' || u === 'IPA') return 'IPA';
  if (u === 'IOFF' || u === 'IOF') return 'IOF';
  if (u === 'MOMENTUM') return 'MOMENTUM';
  if (u === 'ABSORPTION' || u === 'ABSORP') return 'ABSORPTION';
  if (u === 'REVERSAL') return 'REVERSAL';
  if (u === 'MEAN_REVERT' || u === 'MEAN_REVERSE' || u === 'MEAN_REVERSION') return 'MEAN_REVERT';
  if (u === 'FVG') return 'FVG';
  if (u === 'EMA') return 'EMA';
  if (u === 'POC') return 'POC';
  return u;
}

function isAligned(trade: TradeRecord): boolean {
  if (typeof trade.ai_aligned === 'boolean') return trade.ai_aligned;
  const bias = (trade.ai_bias ?? '').toUpperCase();
  const dir = (trade.direction ?? '').toUpperCase();
  return (bias === 'BULLISH' && dir === 'LONG') ||
         (bias === 'BEARISH' && dir === 'SHORT');
}

function derivePrimaryCause(trade: TradeRecord): string {
  const exitReason = (trade.exit_reason ?? '').toUpperCase();
  if (exitReason.includes('SL')) return 'Stop Loss Hit';
  if (exitReason.includes('TP')) return 'TP Missed / Reversed';
  if (exitReason.includes('TIMEOUT') || exitReason.includes('TIME')) return 'Session Timeout';
  if (exitReason.includes('MANUAL')) return 'Manual Close';
  const mode = (trade.mode ?? '').toUpperCase();
  if (mode.includes('REVERSAL')) return 'Reversal Fake-out';
  if (mode.includes('MOMENTUM')) return 'Momentum Exhaustion';
  if (mode.includes('ABSORPTION')) return 'Absorption Fail';
  if (mode.includes('FVG')) return 'FVG Invalidated';
  return 'Market Divergence';
}

function emptyOverallStats(): OverallStats {
  return {
    totalPnl: 0, winCount: 0, lossCount: 0, totalClosed: 0,
    winRate: 0, profitFactor: 0, avgWin: 0, avgLoss: 0,
  };
}

function emptyAlignment(): AIAlignment {
  return {
    alignedWin: 0, alignedLoss: 0, conflictWin: 0, conflictLoss: 0,
    alignedWinPnl: 0, alignedLossPnl: 0, conflictWinPnl: 0, conflictLossPnl: 0,
    alignedWinRate: 0, conflictWinRate: 0,
  };
}
export function useAnalysisData(tradeHistory: TradeRecord[]): AnalysisData {
  return useMemo<AnalysisData>(() => {
    const closed = tradeHistory.filter(
      t => t.status === 'WIN' || t.status === 'LOSS',
    );

    if (closed.length === 0) {
      return {
        overallStats: emptyOverallStats(),
        sessionStats: Object.fromEntries(
          SESSIONS.map(s => [s, { wins: 0, losses: 0, winRate: 0, pnl: 0 }]),
        ),
        typeStats: Object.fromEntries(
          SIGNAL_TYPES.map(t => [t, { wins: 0, losses: 0, total: 0, winRate: 0 }]),
        ),
        aiAlignment: emptyAlignment(),
        lossPatterns: [],
        sessionTypeMatrix: Object.fromEntries(
          SESSIONS.map(s => [s, Object.fromEntries(SIGNAL_TYPES.map(t => [t, -1]))]),
        ),
      };
    }

    const wins = closed.filter(t => t.status === 'WIN');
    const losses = closed.filter(t => t.status === 'LOSS');

    const totalPnl = closed.reduce((acc, t) => acc + (t.pnl ?? 0), 0);
    const winCount = wins.length;
    const lossCount = losses.length;
    const winRate = (winCount / closed.length) * 100;

    const grossProfit = wins.reduce((acc, t) => acc + (t.pnl ?? 0), 0);
    const grossLoss = Math.abs(losses.reduce((acc, t) => acc + (t.pnl ?? 0), 0));
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;
    const avgWin = winCount > 0 ? grossProfit / winCount : 0;
    const avgLoss = lossCount > 0 ? -(grossLoss / lossCount) : 0;

    const overallStats: OverallStats = {
      totalPnl, winCount, lossCount, totalClosed: closed.length,
      winRate, profitFactor, avgWin, avgLoss,
    };

    // Session stats
    const sessionMap: Record<string, { wins: number; losses: number; pnl: number }> = {};
    for (const s of SESSIONS) sessionMap[s] = { wins: 0, losses: 0, pnl: 0 };

    for (const t of closed) {
      const s = deriveSession(t);
      if (!sessionMap[s]) sessionMap[s] = { wins: 0, losses: 0, pnl: 0 };
      if (t.status === 'WIN') sessionMap[s].wins++;
      else sessionMap[s].losses++;
      sessionMap[s].pnl += t.pnl ?? 0;
    }

    const sessionStats: Record<string, SessionStat> = {};
    for (const [s, v] of Object.entries(sessionMap)) {
      const total = v.wins + v.losses;
      sessionStats[s] = {
        wins: v.wins, losses: v.losses,
        winRate: total > 0 ? (v.wins / total) * 100 : 0,
        pnl: v.pnl,
      };
    }

    // Type stats
    const typeMap: Record<string, { wins: number; losses: number }> = {};
    for (const t of closed) {
      const type = canonicalType(t.signal_type);
      if (!typeMap[type]) typeMap[type] = { wins: 0, losses: 0 };
      if (t.status === 'WIN') typeMap[type].wins++;
      else typeMap[type].losses++;
    }

    const typeStats: Record<string, TypeStat> = {};
    for (const [type, v] of Object.entries(typeMap)) {
      const total = v.wins + v.losses;
      typeStats[type] = { wins: v.wins, losses: v.losses, total,
        winRate: total > 0 ? (v.wins / total) * 100 : 0 };
    }

    // AI alignment
    const alignment = emptyAlignment();
    for (const t of closed) {
      const aligned = isAligned(t);
      const pnl = t.pnl ?? 0;
      if (t.status === 'WIN') {
        if (aligned) { alignment.alignedWin++; alignment.alignedWinPnl += pnl; }
        else          { alignment.conflictWin++; alignment.conflictWinPnl += pnl; }
      } else {
        if (aligned) { alignment.alignedLoss++; alignment.alignedLossPnl += pnl; }
        else          { alignment.conflictLoss++; alignment.conflictLossPnl += pnl; }
      }
    }
    const alignedTotal = alignment.alignedWin + alignment.alignedLoss;
    const conflictTotal = alignment.conflictWin + alignment.conflictLoss;
    alignment.alignedWinRate = alignedTotal > 0 ? (alignment.alignedWin / alignedTotal) * 100 : 0;
    alignment.conflictWinRate = conflictTotal > 0 ? (alignment.conflictWin / conflictTotal) * 100 : 0;

    // Loss patterns
    const patternMap: Record<string, Record<string, number>> = {};
    for (const t of losses) {
      const mode = (t.mode ?? 'UNKNOWN').toUpperCase();
      const cause = derivePrimaryCause(t);
      if (!patternMap[mode]) patternMap[mode] = {};
      patternMap[mode][cause] = (patternMap[mode][cause] ?? 0) + 1;
    }
    const lossPatterns: LossPattern[] = Object.entries(patternMap)
      .map(([mode, causes]) => {
        const sorted = Object.entries(causes).sort((a, b) => b[1] - a[1]);
        const count = Object.values(causes).reduce((a, b) => a + b, 0);
        return { mode, primaryCause: sorted[0][0], count,
          percentage: lossCount > 0 ? (count / lossCount) * 100 : 0 };
      })
      .sort((a, b) => b.count - a.count);

    // Session x type matrix
    const matrixRaw: Record<string, Record<string, { wins: number; losses: number }>> = {};
    for (const s of SESSIONS) {
      matrixRaw[s] = {};
      for (const type of SIGNAL_TYPES) matrixRaw[s][type] = { wins: 0, losses: 0 };
    }
    for (const t of closed) {
      const s = deriveSession(t);
      const type = canonicalType(t.signal_type);
      if (!matrixRaw[s]) matrixRaw[s] = {};
      if (!matrixRaw[s][type]) matrixRaw[s][type] = { wins: 0, losses: 0 };
      if (t.status === 'WIN') matrixRaw[s][type].wins++;
      else matrixRaw[s][type].losses++;
    }
    const sessionTypeMatrix: SessionTypeMatrix = {};
    for (const [s, types] of Object.entries(matrixRaw)) {
      sessionTypeMatrix[s] = {};
      for (const [type, v] of Object.entries(types)) {
        const total = v.wins + v.losses;
        sessionTypeMatrix[s][type] = total > 0 ? (v.wins / total) * 100 : -1;
      }
    }

    return { overallStats, sessionStats, typeStats, aiAlignment: alignment, lossPatterns, sessionTypeMatrix };
  }, [tradeHistory]);
}
