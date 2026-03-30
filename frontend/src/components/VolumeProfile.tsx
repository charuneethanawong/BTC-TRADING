import React from 'react';
import type { MLVPState, OrderFlowState } from '../types/dashboard';

// v25.0: Added orderFlow prop for real data
export const VolumeProfile: React.FC<{ mlvp: MLVPState; orderFlow?: OrderFlowState }> = ({ mlvp, orderFlow }) => {
    // Helper functions for formatting
    const formatDelta = (val: number) => {
        if (Math.abs(val) >= 1e9) return (val / 1e9).toFixed(2) + 'B';
        if (Math.abs(val) >= 1e6) return (val / 1e6).toFixed(2) + 'M';
        if (Math.abs(val) >= 1e3) return (val / 1e3).toFixed(1) + 'K';
        return val.toFixed(0);
    };
    
    const formatVolume = (val: number) => {
        if (val >= 1e9) return (val / 1e9).toFixed(2) + 'B';
        if (val >= 1e6) return (val / 1e6).toFixed(2) + 'M';
        if (val >= 1e3) return (val / 1e3).toFixed(1) + 'K';
        return val.toFixed(0);
    };
    
    const formatFunding = (val: number) => (val * 100).toFixed(4) + '%';
    
    const safeOrderFlow = orderFlow || { delta: 0, volume_24h: 0, oi: 0, oi_change: 0, liquidations: 0, der: 0, funding_rate: 0 };
    
    const stats = [
        { label: 'DELTA', val: formatDelta(safeOrderFlow.delta), color: safeOrderFlow.delta >= 0 ? 'text-primary' : 'text-error' },
        { label: 'VOL 24H', val: formatVolume(safeOrderFlow.volume_24h), color: 'text-on-surface' },
        { label: 'OI', val: formatVolume(safeOrderFlow.oi), color: 'text-on-surface' },
        { label: 'FUNDING', val: formatFunding(safeOrderFlow.funding_rate), color: safeOrderFlow.funding_rate >= 0 ? 'text-primary' : 'text-error' },
    ];
    return (
        <section className="bg-surface-container border border-outline-variant/10 relative overflow-hidden" style={{ padding: '16px', height: '400px' }}>
            {/* Dot Grid Background */}
            <div className="absolute inset-0 opacity-10" style={{ backgroundImage: 'radial-gradient(#484848 1px, transparent 1px)', backgroundSize: '20px 20px' }}></div>

            {/* Header */}
            <div className="relative z-10 flex justify-between items-start">
                <div>
                    <h2 className="text-[10px] tracking-[0.2em] uppercase font-bold text-on-surface font-label">Market Microstructure</h2>
                    <div className="text-[9px] text-primary-dim font-mono">LIVE CLUSTER ANALYSIS</div>
                </div>
                <div style={{ display: 'flex', gap: '16px' }}>
                    <div className="text-right">
                        <div className="text-[8px] text-on-surface-variant font-mono">VAH</div>
                        <div className="text-[11px] font-bold text-on-surface font-mono">{(mlvp.composite_vah || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                    </div>
                    <div className="text-right">
                        <div className="text-[8px] text-primary font-mono">POC</div>
                        <div className="text-[11px] font-bold text-primary font-mono">{(mlvp.composite_poc || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                    </div>
                    <div className="text-right">
                        <div className="text-[8px] text-on-surface-variant font-mono">VAL</div>
                        <div className="text-[11px] font-bold text-on-surface font-mono">{(mlvp.composite_val || 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}</div>
                    </div>
                </div>
            </div>

            {/* Chart Area */}
            <div className="mt-8 relative h-64 border-l border-b border-outline-variant/30">
                <div className="absolute top-10 left-10 right-10 bottom-10 flex flex-col justify-between">
                    {/* VAH Line */}
                    <div className="h-px w-full bg-outline-variant/20 relative">
                        <span className="absolute -left-12 -top-1.5 text-[8px] text-on-surface-variant font-mono">VAH</span>
                        <div className="absolute inset-0 bg-primary/5 h-12"></div>
                    </div>
                    {/* POC Line */}
                    <div className="h-px w-full bg-primary/40 relative">
                        <span className="absolute -left-12 -top-1.5 text-[8px] text-primary font-bold font-mono">POC</span>
                        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-4 h-4 bg-primary rotate-45 opacity-20 animate-pulse"></div>
                    </div>
                    {/* VAL Line */}
                    <div className="h-px w-full bg-outline-variant/20 relative">
                        <span className="absolute -left-12 -top-1.5 text-[8px] text-on-surface-variant font-mono">VAL</span>
                    </div>
                </div>

                {/* Confluence Zones - real data from mlvp.confluence_zones */}
                {(mlvp.confluence_zones || []).slice(0, 2).map((zone, i) => (
                    <div
                        key={i}
                        className="absolute right-4 w-32 border-l border-primary/50 bg-primary/5 p-2"
                        style={{ top: `${20 + i * 60}px` }}
                    >
                        <div className="text-[8px] font-black text-primary uppercase mb-1 font-label">
                            Zone {String.fromCharCode(65 + i)} @ {zone.price.toLocaleString()}
                        </div>
                        <div className="text-[7px] text-on-surface-variant font-mono">
                            STR: {(zone.strength * 100).toFixed(0)}% | {(zone.layers || []).join('+')}
                        </div>
                    </div>
                ))}
                {(mlvp.confluence_zones || []).length === 0 && (
                    <div className="absolute right-4 top-20 w-36 border-l border-outline-variant/30 bg-surface-container-lowest p-2">
                        <div className="text-[8px] text-on-surface-variant font-mono uppercase">No Confluence Zones</div>
                    </div>
                )}
            </div>

            {/* Bottom Stats - v25.0: Use real data from orderFlow */}
            <div style={{ position: 'absolute', bottom: '16px', left: '16px', right: '16px', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
                {stats.map((stat, i) => (
                    <div key={i} className={`text-center p-2 bg-surface-container-low ${i === 0 ? 'border-t border-primary/20' : ''}`}>
                        <div className="text-[7px] text-on-surface-variant font-mono">{stat.label}</div>
                        <div className={`text-[10px] font-mono ${stat.color}`}>{stat.val}</div>
                    </div>
                ))}
            </div>
        </section>
    );
};
