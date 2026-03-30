import React, { useState, useEffect } from 'react';

interface ReportProps {
    totalPnl: number; winRate: number; profitFactor: number; avgWin: number; avgLoss: number;
    bePct: number; realTpPct: number; beTrades: any[];
    initialModel?: 'claude' | 'gemini' | 'deepseek';
    autoAnalyze?: boolean;
    modeStats: Record<string, { w: number; l: number; pnl: number }>;
    typeStats: Record<string, { w: number; l: number; pnl: number; pf: number }>;
    sessionStats: Record<string, { w: number; l: number; pnl: number; aligned: number; total: number }>;
    alignedWR: number; conflictWR: number;
    confRanges: { label: string; wr: number }[];
    lossPatterns: { label: string; pct: number; desc: string }[];
    slByMode: { mode: string; avg: number }[];
    closedCount: number;
    onBack: () => void;
}

export const AIReportPage: React.FC<ReportProps> = (p) => {
    const [aiInsight, setAiInsight] = useState<string | null>(null);
    const [aiLoading, setAiLoading] = useState(false);
    const [aiModel, setAiModel] = useState<'claude' | 'gemini' | 'deepseek'>(p.initialModel || 'deepseek');

    const maxModePnl = Math.max(...Object.values(p.modeStats).map(s => Math.abs(s.pnl)), 1);
    const maxSL = Math.max(...p.slByMode.map(s => s.avg), 1);
    const typeColors: Record<string, string> = { MOMENTUM: '#00e3fd', ABSORPTION: '#ffc15b', REVERSAL_OB: '#ff7351', REVERSAL_OS: '#ff7351', MEAN_REVERT: '#9cff93' };
    const sessionColors: Record<string, string> = { LONDON: '#9cff93', NY: '#00e3fd', ASIA: '#ffc15b' };

    const runAI = async (model?: string) => {
        setAiLoading(true);
        try {
            const res = await fetch(`http://localhost:8000/api/ai/analyze-trades?model=${model || aiModel}`, { method: 'POST' });
            const data = await res.json();
            setAiInsight(data.insight || 'No response');
        } catch { setAiInsight('Connection failed'); }
        setAiLoading(false);
    };

    // Auto-analyze on mount
    useEffect(() => {
        if (p.autoAnalyze && !aiInsight && !aiLoading) {
            runAI(p.initialModel || aiModel);
        }
    }, []);

    const S: React.CSSProperties = { fontFamily: "'Space Grotesk'", fontWeight: 800, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.12em' };
    const card: React.CSSProperties = { background: '#191919', padding: '20px' };

    return (
        <div className="space-y-5" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            {/* Back button */}
            <button onClick={p.onBack} style={{ padding: '6px 16px', border: '1px solid #484848', color: '#ababab', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', cursor: 'pointer', background: 'transparent', marginBottom: '8px' }}>← BACK TO TRADE LOG</button>

            {/* Overall Performance */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
                <div style={{ background: '#1f1f1f', padding: '14px', borderLeft: '3px solid #9cff93' }}>
                    <p style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', marginBottom: '4px' }}>TOTAL PNL</p>
                    <h2 style={{ fontSize: '1.5rem', fontWeight: 900, color: p.totalPnl >= 0 ? '#9cff93' : '#ff7351', fontFamily: "'Space Grotesk'", letterSpacing: '-0.04em' }}>{p.totalPnl >= 0 ? '+' : ''}${p.totalPnl.toFixed(2)}</h2>
                    <p style={{ fontSize: '9px', color: '#ababab', marginTop: '6px' }}>{p.closedCount} trades</p>
                </div>
                <div style={{ background: '#1f1f1f', padding: '14px', borderLeft: '3px solid #9cff93' }}>
                    <p style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', marginBottom: '4px' }}>WIN RATE</p>
                    <h2 style={{ fontSize: '1.5rem', fontWeight: 900, color: '#fff', fontFamily: "'Space Grotesk'" }}>{p.winRate.toFixed(1)}%</h2>
                    <div style={{ height: '3px', background: '#000', marginTop: '6px' }}><div style={{ height: '100%', width: `${p.winRate}%`, background: '#9cff93' }}></div></div>
                </div>
                <div style={{ background: '#1f1f1f', padding: '14px', borderLeft: '3px solid #ffc15b' }}>
                    <p style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', marginBottom: '4px' }}>PROFIT FACTOR</p>
                    <h2 style={{ fontSize: '1.5rem', fontWeight: 900, color: '#ffc15b', fontFamily: "'Space Grotesk'" }}>{p.profitFactor.toFixed(1)}</h2>
                </div>
                <div style={{ background: '#1f1f1f', padding: '14px', borderLeft: '3px solid #00e3fd' }}>
                    <p style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', marginBottom: '4px' }}>AVG WIN / LOSS</p>
                    <span style={{ fontSize: '16px', fontWeight: 700, color: '#9cff93' }}>+${p.avgWin.toFixed(2)}</span>
                    <span style={{ color: '#484848', margin: '0 4px' }}>/</span>
                    <span style={{ fontSize: '16px', fontWeight: 700, color: '#ff7351' }}>${p.avgLoss.toFixed(2)}</span>
                </div>
            </div>

            {/* BE + Mode */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '16px' }}>
                <div style={card}>
                    <h3 style={{ ...S, color: '#00e3fd', borderBottom: '1px solid rgba(72,72,72,0.3)', paddingBottom: '6px', marginBottom: '12px' }}>BE_ANALYSIS</h3>
                    <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0' }}>
                        <div style={{ width: '100px', height: '100px', borderRadius: '50%', border: '6px solid #9cff93', borderRightColor: '#191919', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                            <span style={{ fontSize: '20px', fontWeight: 700 }}>{p.bePct.toFixed(0)}%</span>
                            <span style={{ fontSize: '7px', color: '#ababab' }}>BE COUNT</span>
                        </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', borderTop: '1px solid rgba(72,72,72,0.2)', paddingTop: '12px' }}>
                        <div><p style={{ fontSize: '8px', color: '#ababab', marginBottom: '2px' }}>AVG BE PNL</p><p style={{ fontSize: '16px', fontWeight: 700, color: '#9cff93' }}>${p.beTrades.length > 0 ? (p.beTrades.reduce((s: number, t: any) => s + (t.pnl || 0), 0) / p.beTrades.length).toFixed(2) : '0'}</p></div>
                        <div><p style={{ fontSize: '8px', color: '#ababab', marginBottom: '2px' }}>REAL TP%</p><p style={{ fontSize: '16px', fontWeight: 700, color: '#00e3fd' }}>{p.realTpPct.toFixed(0)}%</p></div>
                    </div>
                </div>
                <div style={card}>
                    <h3 style={{ ...S, color: '#9cff93', marginBottom: '12px' }}>PERFORMANCE_MODES</h3>
                    {Object.entries(p.modeStats).map(([mode, s]) => (
                        <div key={mode} style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                            <span style={{ width: '40px', fontSize: '10px', color: '#ababab', fontWeight: 700 }}>{mode}</span>
                            <div style={{ flex: 1, height: '20px', background: '#000' }}><div style={{ height: '100%', width: `${Math.abs(s.pnl) / maxModePnl * 100}%`, background: s.pnl >= 0 ? '#00fc40' : '#b92902' }}></div></div>
                            <span style={{ width: '55px', fontSize: '10px', textAlign: 'right', color: s.pnl >= 0 ? '#9cff93' : '#ff7351' }}>{s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(1)}</span>
                        </div>
                    ))}
                    <table style={{ width: '100%', fontSize: '10px', marginTop: '16px' }}>
                        <thead><tr style={{ color: '#ababab', borderBottom: '1px solid rgba(72,72,72,0.3)' }}><th style={{ padding: '6px 0', fontWeight: 400, textAlign: 'left' }}>Type</th><th style={{ fontWeight: 400 }}>Vol</th><th style={{ fontWeight: 400 }}>WR</th><th style={{ fontWeight: 400, textAlign: 'right' }}>PF</th></tr></thead>
                        <tbody>{Object.entries(p.typeStats).map(([type, s]) => (
                            <tr key={type} style={{ borderBottom: '1px solid rgba(72,72,72,0.1)' }}>
                                <td style={{ padding: '8px 0', fontWeight: 700, color: typeColors[type] || '#ababab' }}>{type}</td>
                                <td>{s.w + s.l}</td><td style={{ color: (s.w / Math.max(s.w + s.l, 1) * 100) > 60 ? '#9cff93' : '#ff7351' }}>{(s.w / Math.max(s.w + s.l, 1) * 100).toFixed(0)}%</td>
                                <td style={{ textAlign: 'right' }}>{s.pf.toFixed(1)}</td></tr>
                        ))}</tbody>
                    </table>
                </div>
            </div>

            {/* Session + AI + Loss */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
                <div style={card}>
                    <h3 style={{ ...S, color: '#9cff93', marginBottom: '12px' }}>SESSION_LOGS</h3>
                    {Object.entries(p.sessionStats).map(([name, s]) => (
                        <div key={name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: '#131313', padding: '10px', marginBottom: '6px', borderRight: `2px solid ${sessionColors[name] || '#484848'}` }}>
                            <div><p style={{ fontSize: '9px', color: '#ababab' }}>{name}</p><p style={{ fontSize: '13px', fontWeight: 700, color: s.pnl >= 0 ? '#fff' : '#ff7351' }}>{s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}</p></div>
                            <span style={{ fontSize: '9px', padding: '3px 6px', background: (s.w / Math.max(s.total, 1) * 100) > 60 ? 'rgba(156,255,147,0.1)' : 'rgba(255,115,81,0.1)', color: (s.w / Math.max(s.total, 1) * 100) > 60 ? '#9cff93' : '#ff7351' }}>{Math.round(s.w / Math.max(s.total, 1) * 100)}% WR</span>
                        </div>
                    ))}
                </div>
                <div style={card}>
                    <h3 style={{ ...S, color: '#00e3fd', marginBottom: '12px' }}>AI_ALIGNMENT</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '16px' }}>
                        <div style={{ background: '#000', padding: '10px', border: '1px solid rgba(156,255,147,0.2)' }}><p style={{ fontSize: '8px', color: '#ababab', marginBottom: '2px' }}>ALIGNED WR</p><p style={{ fontSize: '20px', fontWeight: 900, color: '#9cff93' }}>{p.alignedWR}%</p></div>
                        <div style={{ background: '#000', padding: '10px', border: '1px solid rgba(255,115,81,0.2)' }}><p style={{ fontSize: '8px', color: '#ababab', marginBottom: '2px' }}>CONFLICT WR</p><p style={{ fontSize: '20px', fontWeight: 900, color: '#ff7351' }}>{p.conflictWR}%</p></div>
                    </div>
                    <h3 style={{ ...S, color: '#ffc15b', marginBottom: '10px', fontSize: '11px' }}>CONFIDENCE</h3>
                    {p.confRanges.map((r, i) => (
                        <div key={i} style={{ marginBottom: '10px' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', marginBottom: '3px' }}><span style={{ color: '#ababab' }}>{r.label}</span><span style={{ fontWeight: 700, color: r.wr > 60 ? '#9cff93' : r.wr > 40 ? '#fff' : '#ff7351' }}>{r.wr}% WR</span></div>
                            <div style={{ height: '4px', background: '#000' }}><div style={{ height: '100%', width: `${r.wr}%`, background: r.wr > 60 ? '#9cff93' : r.wr > 40 ? 'rgba(255,255,255,0.4)' : '#ff7351' }}></div></div>
                        </div>
                    ))}
                </div>
                <div style={{ ...card, background: '#1f1f1f', position: 'relative', overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', top: -40, right: -40, width: '120px', height: '120px', background: 'rgba(255,115,81,0.05)', filter: 'blur(40px)' }}></div>
                    <h3 style={{ ...S, color: '#ff7351', marginBottom: '12px' }}>LOSS_PATTERNS</h3>
                    {p.lossPatterns.map((pat, i) => (
                        <div key={i} style={{ background: '#000', padding: '10px', marginBottom: '6px' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '3px' }}><span style={{ fontSize: '10px', fontWeight: 700 }}>{pat.label}</span><span style={{ fontSize: '10px', color: '#ff7351' }}>{pat.pct}%</span></div>
                            <p style={{ fontSize: '8px', color: '#ababab' }}>{pat.desc}</p>
                        </div>
                    ))}
                    {p.lossPatterns.length === 0 && <p style={{ fontSize: '10px', color: '#484848' }}>No loss data</p>}
                </div>
            </div>

            {/* SL/TP + AI Advisory */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                <div style={card}>
                    <h3 style={{ ...S, color: '#9cff93', marginBottom: '16px' }}>EXECUTION_EFFICIENCY</h3>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                        <div><p style={{ fontSize: '9px', color: '#ababab', marginBottom: '8px' }}>TP HIT RATE</p><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '80px', border: '3px solid #000' }}><span style={{ fontSize: '2rem', fontWeight: 900, color: '#9cff93' }}>{p.realTpPct.toFixed(0)}%</span></div></div>
                        <div><p style={{ fontSize: '9px', color: '#ababab', marginBottom: '8px' }}>SL DIST BY MODE</p>
                            {p.slByMode.map((s, i) => (
                                <div key={i} style={{ marginBottom: '6px' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9px', marginBottom: '2px' }}><span>{s.mode}</span><span style={{ color: '#ffc15b' }}>${s.avg.toFixed(0)}</span></div>
                                    <div style={{ height: '3px', background: '#000' }}><div style={{ height: '100%', width: `${(s.avg / maxSL) * 100}%`, background: '#ffc15b' }}></div></div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
                <div style={{ background: '#000', border: '1px solid rgba(156,255,147,0.3)', padding: '20px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
                        <div style={{ background: '#9cff93', padding: '6px 8px' }}><span style={{ color: '#000', fontSize: '12px', fontWeight: 900 }}>AI</span></div>
                        <h3 style={{ ...S, color: '#9cff93' }}>NEURAL_ADVISORY</h3>
                    </div>
                    {aiInsight ? (
                        <div style={{ fontSize: '11px', color: '#ababab', lineHeight: '1.8', whiteSpace: 'pre-wrap', marginBottom: '12px', maxHeight: '250px', overflowY: 'auto' }} className="custom-scrollbar">{aiInsight}</div>
                    ) : (
                        <p style={{ fontSize: '10px', color: '#484848', marginBottom: '12px' }}>Click analyze to generate recommendations.</p>
                    )}
                    <div style={{ display: 'flex', gap: '6px', marginBottom: '10px' }}>
                        {(['claude', 'gemini', 'deepseek'] as const).map(m => {
                            const c: Record<string, string> = { claude: '#00e3fd', gemini: '#ffc15b', deepseek: '#9cff93' };
                            return <button key={m} onClick={() => setAiModel(m)} style={{ padding: '3px 8px', fontSize: '8px', fontWeight: 700, textTransform: 'uppercase', cursor: 'pointer', background: aiModel === m ? `${c[m]}20` : 'transparent', border: `1px solid ${aiModel === m ? c[m] : '#303030'}`, color: aiModel === m ? c[m] : '#484848' }}>{m.toUpperCase()}</button>;
                        })}
                    </div>
                    <div style={{ display: 'flex', gap: '6px' }}>
                        <button onClick={() => runAI()} disabled={aiLoading} style={{ flex: 1, padding: '10px', background: aiLoading ? '#1f1f1f' : '#9cff93', color: '#000', fontWeight: 800, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em', cursor: aiLoading ? 'wait' : 'pointer', border: 'none', opacity: aiLoading ? 0.5 : 1 }}>{aiLoading ? 'ANALYZING...' : 'EXECUTE ANALYSIS'}</button>
                        <button style={{ padding: '10px 14px', border: '1px solid #484848', color: '#ababab', fontWeight: 800, fontSize: '10px', textTransform: 'uppercase', cursor: 'pointer', background: 'transparent' }}>EXPORT</button>
                    </div>
                </div>
            </div>
        </div>
    );
};
