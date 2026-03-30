import React from 'react';
import type { DashboardState } from '../types/dashboard';

interface AIPanelProps {
    data: DashboardState;
}

export const AIPanel: React.FC<AIPanelProps> = ({ data }) => {
    const ai = data.ai;
    const mlvp = data.mlvp || { composite_poc: 0, composite_vah: 0, composite_val: 0, current_session: '', confluence_zones: [] };
    const prices = (data as any).price_history || [];
    
    const buildChartPath = () => {
        if (prices.length < 2) return { linePath: '', fillPath: '', lastX: 0, lastY: 0 };     
        const w = 1200, h = 420, pad = 15;
        const minP = Math.min(...prices);
        const maxP = Math.max(...prices);
        const range = maxP - minP || 1;
        const points = prices.map((p: number, i: number) => {
            const x = (i / (prices.length - 1)) * w;
            const y = pad + ((maxP - p) / range) * (h - pad * 2);
            return `${x.toFixed(0)},${y.toFixed(0)}`;
        });
        const linePath = 'M' + points.join(' L');
        const fillPath = linePath + ` L${w},${h} L0,${h} Z`;
        const lastPt = points[points.length - 1].split(',');
        return { linePath, fillPath, lastX: parseFloat(lastPt[0]), lastY: parseFloat(lastPt[1]) };
    };
    const chart = buildChartPath();

    const calcLevelTop = (level: number) => {
        if (prices.length < 2 || level <= 0) return -1;
        const minP = Math.min(...prices);
        const maxP = Math.max(...prices);
        const range = maxP - minP || 1;
        const pct = ((maxP - level) / range) * 100;
        return Math.max(5, Math.min(95, pct));
    };

    const biasColor = ai.bias === 'BULLISH' ? '#9cff93' : ai.bias === 'BEARISH' ? '#ff7351' : '#ffc15b';
    const actionColor = ai.action === 'TRADE' ? '#9cff93' : '#ffc15b';

    return (
        <section className="bg-surface-container-low" style={{ padding: '24px', borderLeft: '4px solid #9cff93' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '32px' }}>
                <div>
                    <h2 style={{ fontFamily: "'Space Grotesk'", fontWeight: 900, fontSize: '2.25rem', letterSpacing: '-0.05em', color: '#fff', marginBottom: '4px' }}>NEURAL BIAS</h2>      
                    <div className="flex items-center" style={{ gap: '12px' }}>
                        <span style={{ background: biasColor, color: '#006413', padding: '4px 12px', fontWeight: 700, fontSize: '18px' }}>{ai.bias || 'NEUTRAL'}</span>
                        <span style={{ color: biasColor, fontWeight: 700 }}>{ai.confidence || 0}% CONFIDENCE</span>
                    </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                    <span style={{ color: '#ababab', fontSize: '10px', letterSpacing: '0.2em', display: 'block', marginBottom: '8px' }}>SYSTEM ACTION</span>
                    <div style={{ background: 'rgba(156,255,147,0.1)', border: '1px solid #9cff93', padding: '8px 24px' }}>
                        <span style={{ color: actionColor, fontWeight: 900, fontSize: '24px', letterSpacing: '0.15em' }}>{ai.action || 'WAIT'}</span>
                    </div>
                </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '32px', marginBottom: '32px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>       
                    <h3 style={{ color: '#ababab', fontSize: '12px', letterSpacing: '0.15em', textTransform: 'uppercase', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ width: '8px', height: '8px', background: '#9cff93', borderRadius: '50%', display: 'inline-block' }}></span>
                        Reasoning Narrative
                    </h3>
                    <p style={{ color: '#fff', fontSize: '14px', lineHeight: '1.6', fontWeight: 300 }}>
                        {ai.reason || 'Awaiting neural core initialization.'}
                    </p>
                </div>
            </div>

            <div style={{
                background: '#050505', position: 'relative', overflow: 'hidden', height: '450px',
                border: '1px solid rgba(72,72,72,0.3)',
                backgroundImage: 'linear-gradient(to right, rgba(0,255,65,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(0,255,65,0.05) 1px, transparent 1px)',
                backgroundSize: '32px 32px'
            }}>
                <div style={{ position: 'absolute', inset: 0, zIndex: 0 }}>
                    <svg width="100%" height="100%" viewBox="0 0 1200 450" preserveAspectRatio="none">
                        <defs>
                            <linearGradient id="chart-fill" x1="0%" y1="0%" x2="0%" y2="100%">
                                <stop offset="0%" stopColor="#00FF41" stopOpacity="0.2" />    
                                <stop offset="100%" stopColor="#00FF41" stopOpacity="0" />    
                            </linearGradient>
                        </defs>
                        {chart.linePath && (
                            <>
                                <path d={chart.linePath} fill="none" stroke="#00FF41" strokeWidth="2.5" />
                                <path d={chart.fillPath} fill="url(#chart-fill)" />
                                <circle cx={chart.lastX} cy={chart.lastY} r="5" fill="#00FF41" />
                            </>
                        )}
                    </svg>
                </div>

                {mlvp.composite_vah > 0 && calcLevelTop(mlvp.composite_vah) >= 0 && (
                    <div style={{ position: 'absolute', top: calcLevelTop(mlvp.composite_vah) + '%', width: '100%', borderTop: '1px dashed rgba(0,227,253,0.5)', zIndex: 10 }}>
                        <div style={{ background: '#00E3FD', color: '#0e0e0e', fontSize: '9px', padding: '1px 8px', fontWeight: 900, width: 'fit-content' }}>
                            VAH {mlvp.composite_vah.toLocaleString()}
                        </div>
                    </div>
                )}

                {(() => {
                    const pocVal = mlvp.composite_poc > 0 ? mlvp.composite_poc : data.price;  
                    const pocTop = calcLevelTop(pocVal);
                    return pocTop >= 0 ? (
                        <div style={{ position: 'absolute', top: pocTop + '%', width: '100%', borderTop: '2px solid #00FF41', zIndex: 10 }}>
                            <div style={{ background: '#00FF41', color: '#0e0e0e', fontSize: '9px', padding: '1px 8px', fontWeight: 900, width: 'fit-content', boxShadow: '0 0 10px rgba(0,255,65,0.5)' }}>     
                                POC {pocVal.toLocaleString()}
                            </div>
                        </div>
                    ) : null;
                })()}

                {mlvp.composite_val > 0 && calcLevelTop(mlvp.composite_val) >= 0 && (
                    <div style={{ position: 'absolute', top: calcLevelTop(mlvp.composite_val) + '%', width: '100%', borderTop: '1px dashed rgba(0,227,253,0.5)', zIndex: 10 }}>
                        <div style={{ background: '#00E3FD', color: '#0e0e0e', fontSize: '9px', padding: '1px 8px', fontWeight: 900, width: 'fit-content' }}>
                            VAL {mlvp.composite_val.toLocaleString()}
                        </div>
                    </div>
                )}

                <div style={{ position: 'relative', zIndex: 20, padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ background: 'rgba(5,5,5,0.8)', borderLeft: '2px solid #9cff93', padding: '4px 12px' }}>
                        <h3 style={{ fontSize: '10px', letterSpacing: '0.15em', color: '#fff', textTransform: 'uppercase', fontWeight: 900 }}>Microstructure Accuracy</h3>     
                    </div>
                </div>
            </div>
        </section>
    );
};