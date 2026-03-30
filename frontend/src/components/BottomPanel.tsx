import React from 'react';
import { ShieldCheck } from 'lucide-react';
import type { AccountInfo } from '../types/dashboard';

export const BottomPanel: React.FC<{ account: AccountInfo }> = ({ account }) => {
    const safeAccount = account || { balance: 0, equity: 0, profit: 0, leverage: 10, drawdown_pct: 0 };
    
    // v25.0: Dynamic risk level based on drawdown
    const drawdownPct = safeAccount.drawdown_pct || 0;
    const riskLevel = drawdownPct > 5 ? 'DANGER' : drawdownPct > 2 ? 'CAUTION' : 'SAFE';
    const riskColor = riskLevel === 'DANGER' ? '#ff7351' : riskLevel === 'CAUTION' ? '#ffc107' : '#9cff93';
    const riskText = riskLevel === 'DANGER' ? 'CRITICAL - STOP TRADING' : 
                     riskLevel === 'CAUTION' ? 'CAUTION - MONITOR CLOSELY' : 
                     'ACTIVE / SAFE';
    const riskSubtext = riskLevel === 'DANGER' ? `MAX DRAWDOWN ${drawdownPct.toFixed(1)}% EXCEEDED` : 
                        riskLevel === 'CAUTION' ? `DRAWDOWN ${drawdownPct.toFixed(1)}% - WATCH CLOSELY` : 
                        'VOLATILITY COMPRESSION DETECTED - NO OVER-EXPOSURE';

    return (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '16px' }}>
            {/* Financial Telemetry */}
            <section className="bg-surface-container border border-outline-variant/10" style={{ padding: '16px' }}>
                <div className="flex justify-between items-start" style={{ marginBottom: '16px' }}>
                    <h2 className="text-[10px] tracking-[0.2em] uppercase font-bold text-on-surface font-label">Financial Telemetry</h2>
                    <span className="text-[8px] px-2 py-0.5 bg-primary/10 text-primary border border-primary/20 font-mono">LIVE FEED</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', rowGap: '16px' }}>
                    <div>
                        <label className="text-[8px] text-on-surface-variant uppercase block font-mono">Balance</label>
                        <span className="text-sm font-bold font-mono">${safeAccount.balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    </div>
                    <div className="text-right">
                        <label className="text-[8px] text-on-surface-variant uppercase block font-mono">Equity</label>
                        <span className="text-sm font-bold font-mono">${safeAccount.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                    </div>
                    <div>
                        <label className="text-[8px] text-on-surface-variant uppercase block font-mono">Floating PnL</label>
                        <span className={`text-sm font-bold font-mono ${safeAccount.profit >= 0 ? 'text-primary' : 'text-error'}`}>
                            {safeAccount.profit >= 0 ? '+' : ''}${safeAccount.profit.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                        </span>
                    </div>
                    <div className="text-right">
                        <label className="text-[8px] text-on-surface-variant uppercase block font-mono">Leverage</label>
                        <span className="text-sm font-bold font-mono">{safeAccount.leverage?.toFixed(2) || '10.00'}x</span>
                    </div>
                </div>
            </section>

            {/* Institutional Risk Sentry */}
            <section className="bg-surface-container border border-outline-variant/10 relative overflow-hidden" style={{ padding: '16px' }}>
                <div className="flex items-center" style={{ gap: '8px', marginBottom: '16px' }}>
                    <ShieldCheck className="text-primary" size={16} />
                    <h2 className="text-[10px] tracking-[0.2em] uppercase font-bold text-on-surface font-label">Institutional Risk Sentry</h2>
                </div>
                <div className="flex flex-col h-[70px] justify-center text-center">
                    <div className="text-xs font-black uppercase tracking-widest font-label" style={{ color: riskColor }}>{riskText}</div>
                    <div className="text-[8px] text-on-surface-variant mt-1 font-mono">{riskSubtext}</div>
                </div>
                <div className="absolute bottom-0 left-0 h-1 w-full opacity-20" style={{ background: riskColor }}></div>
                <div className="absolute bottom-0 left-0 h-1" style={{ background: riskColor, width: `${Math.min(drawdownPct * 10, 100)}%` }}></div>
            </section>
        </div>
    );
};
