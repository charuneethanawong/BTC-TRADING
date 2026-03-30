import React, { useState } from 'react';

interface TradeRecord {
    timestamp: string; signal_id?: string; direction: string; ai_bias?: string;
    ai_confidence?: number; ai_reason?: string; signal_type?: string; status: string;
    pnl?: number | null; entry_price?: number; stop_loss?: number; take_profit?: number;
    exit_reason?: string; mode?: string; ai_aligned?: boolean;
}

export const AnalysisPage: React.FC<{ trades: TradeRecord[] }> = ({ trades }) => {
    const [hoverBar, setHoverBar] = useState<number | null>(null);

    // Computed data
    const closed = trades.filter(t => t.status === 'WIN' || t.status === 'LOSS');
    const wins = closed.filter(t => t.status === 'WIN');
    const losses = closed.filter(t => t.status === 'LOSS');
    const totalPnl = closed.reduce((s, t) => s + (t.pnl || 0), 0);
    const winRate = closed.length > 0 ? (wins.length / closed.length) * 100 : 0;
    const beTrades = wins.filter(t => Math.abs(t.pnl || 0) < 1.0);
    const bePct = wins.length > 0 ? (beTrades.length / wins.length) * 100 : 0;
    const alignedWins = wins.filter(t => t.ai_aligned === true).length;
    const alignedLosses = losses.filter(t => t.ai_aligned === true).length;
    const alignedWR = (alignedWins + alignedLosses) > 0 ? Math.round(alignedWins / (alignedWins + alignedLosses) * 100) : 0;

    // Bar chart data
    const bars = closed.map(t => {
        const pnl = t.pnl ?? 0;
        const aligned = (t.ai_bias === 'BULLISH' && t.direction === 'LONG') || (t.ai_bias === 'BEARISH' && t.direction === 'SHORT');
        const h = new Date(t.timestamp || '').getUTCHours();
        const session = (h >= 1 && h < 9) ? 'ASIA' : (h >= 7 && h < 16) ? 'LONDON' : (h >= 13 && h < 22) ? 'NY' : 'ASIA';
        return { pnl, aligned, session, trade: t };
    });
    const maxP = Math.max(...bars.map(b => Math.abs(b.pnl)), 1);
    const sesColors: Record<string, string> = { ASIA: 'rgba(255,193,91,0.06)', LONDON: 'rgba(156,255,147,0.06)', NY: 'rgba(0,227,253,0.06)' };
    const sesTxt: Record<string, string> = { ASIA: '#ffc15b', LONDON: '#9cff93', NY: '#00e3fd' };
    const bands: { start: number; end: number; session: string }[] = [];
    if (bars.length > 0) { let cur = bars[0].session, s = 0; bars.forEach((b, i) => { if (b.session !== cur || i === bars.length - 1) { bands.push({ start: s, end: i, session: cur }); cur = b.session; s = i; } }); }

    return (
        <div style={{ maxWidth: '1600px', margin: '0 auto', fontFamily: "'JetBrains Mono', monospace" }} className="space-y-4">
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
                <div>
                    <h1 style={{ fontFamily: "'Space Grotesk'", fontSize: '1.75rem', fontWeight: 800, letterSpacing: '-0.04em', textTransform: 'uppercase', color: '#fff' }}>Trade Log</h1>
                    <p style={{ fontSize: '10px', color: '#ababab', textTransform: 'uppercase' }}>{closed.length} closed | {trades.length} total</p>
                </div>
            </div>

            {/* Quick stats */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px' }}>
                <div style={{ background: '#1f1f1f', padding: '12px', borderLeft: '3px solid #9cff93' }}><span style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', display: 'block', marginBottom: '4px' }}>PnL</span><p style={{ fontSize: '18px', fontWeight: 700, color: totalPnl >= 0 ? '#9cff93' : '#ff7351', fontFamily: "'Space Grotesk'" }}>{totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}</p></div>
                <div style={{ background: '#1f1f1f', padding: '12px', borderLeft: '3px solid #9cff93' }}><span style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', display: 'block', marginBottom: '4px' }}>Win Rate</span><p style={{ fontSize: '18px', fontWeight: 700, color: '#fff', fontFamily: "'Space Grotesk'" }}>{winRate.toFixed(1)}%</p></div>
                <div style={{ background: '#1f1f1f', padding: '12px', borderLeft: '3px solid #ffc15b' }}><span style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', display: 'block', marginBottom: '4px' }}>BE Rate</span><p style={{ fontSize: '18px', fontWeight: 700, color: '#ffc15b', fontFamily: "'Space Grotesk'" }}>{bePct.toFixed(0)}%</p></div>
                <div style={{ background: '#1f1f1f', padding: '12px', borderLeft: '3px solid #00e3fd' }}><span style={{ fontSize: '9px', color: '#ababab', textTransform: 'uppercase', display: 'block', marginBottom: '4px' }}>AI Aligned WR</span><p style={{ fontSize: '18px', fontWeight: 700, color: '#00e3fd', fontFamily: "'Space Grotesk'" }}>{alignedWR}%</p></div>
            </div>

            {/* PnL Bar Chart */}
            {bars.length > 0 && (
                <div style={{ background: '#000', border: '1px solid rgba(72,72,72,0.2)', height: '200px', position: 'relative', overflow: 'hidden' }}>
                    {bands.map((b, i) => <div key={i} style={{ position: 'absolute', top: 0, bottom: 0, left: `${(b.start / bars.length) * 100}%`, width: `${((b.end - b.start + 1) / bars.length) * 100}%`, background: sesColors[b.session] }}><span style={{ position: 'absolute', top: '4px', left: '50%', transform: 'translateX(-50%)', fontSize: '8px', color: sesTxt[b.session], fontWeight: 700 }}>{b.session}</span></div>)}
                    <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: '1px', background: 'rgba(72,72,72,0.4)' }}></div>
                    <div style={{ position: 'absolute', left: '4px', top: '20px', fontSize: '8px', color: '#9cff93' }}>+${maxP.toFixed(0)}</div>
                    <div style={{ position: 'absolute', left: '4px', bottom: '20px', fontSize: '8px', color: '#ff7351' }}>-${maxP.toFixed(0)}</div>
                    <div style={{ position: 'absolute', left: '28px', right: '8px', top: '16px', bottom: '16px', display: 'flex', gap: '1px' }} onMouseLeave={() => setHoverBar(null)}>
                        {bars.map((b, i) => {
                            const hPct = (Math.abs(b.pnl) / maxP) * 50;
                            const isWin = b.pnl > 0;
                            const color = b.aligned ? (isWin ? '#9cff93' : '#4a8a45') : (isWin ? '#ffc15b' : '#ff7351');
                            return <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', cursor: 'pointer' }} onMouseEnter={() => setHoverBar(i)}>
                                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>{isWin && <div style={{ width: '100%', height: `${hPct}%`, background: color, opacity: hoverBar === i ? 1 : 0.75, border: hoverBar === i ? '1px solid #fff' : 'none' }}></div>}</div>
                                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-start', justifyContent: 'center' }}>{!isWin && <div style={{ width: '100%', height: `${hPct}%`, background: color, opacity: hoverBar === i ? 1 : 0.75, border: hoverBar === i ? '1px solid #fff' : 'none' }}></div>}</div>
                            </div>;
                        })}
                    </div>
                    {/* Tooltip */}
                    {hoverBar !== null && bars[hoverBar] && (() => {
                        const b = bars[hoverBar]; const t = b.trade; const lPct = (hoverBar / bars.length) * 100;
                        return <div style={{ position: 'absolute', top: '20px', left: lPct > 60 ? undefined : `${lPct}%`, right: lPct > 60 ? `${100 - lPct}%` : undefined, background: 'rgba(0,0,0,0.95)', border: '1px solid rgba(156,255,147,0.3)', padding: '8px 12px', zIndex: 30, fontSize: '10px', lineHeight: '1.5', minWidth: '220px', pointerEvents: 'none' }}>
                            <div style={{ color: '#ababab' }}>{t.timestamp?.slice(5, 19).replace('T', ' ')} | {b.session}</div>
                            <div style={{ color: '#fff', fontWeight: 700 }}>{t.signal_id}</div>
                            <div>AI: <span style={{ color: t.ai_bias === 'BULLISH' ? '#9cff93' : t.ai_bias === 'BEARISH' ? '#ff7351' : '#ababab', fontWeight: 700 }}>{t.ai_bias || '—'} {t.ai_confidence || 0}%</span> Bot: <span style={{ color: t.direction === 'LONG' ? '#9cff93' : '#ff7351', fontWeight: 700 }}>{t.direction}</span></div>
                            <div><span style={{ color: b.aligned ? '#9cff93' : '#ff7351', fontWeight: 700 }}>{b.aligned ? '✓ ALIGNED' : '✗ CONFLICT'}</span> <span style={{ color: b.pnl >= 0 ? '#9cff93' : '#ff7351', fontWeight: 700 }}>{b.pnl >= 0 ? '+' : ''}${b.pnl.toFixed(2)} {t.status}</span></div>
                        </div>;
                    })()}
                    <div style={{ position: 'absolute', bottom: '2px', right: '8px', display: 'flex', gap: '8px', fontSize: '7px', color: '#484848' }}>
                        <span><span style={{ display: 'inline-block', width: '6px', height: '6px', background: '#9cff93', marginRight: '2px' }}></span>Aligned+Win</span>
                        <span><span style={{ display: 'inline-block', width: '6px', height: '6px', background: '#4a8a45', marginRight: '2px' }}></span>Aligned+Loss</span>
                        <span><span style={{ display: 'inline-block', width: '6px', height: '6px', background: '#ffc15b', marginRight: '2px' }}></span>Conflict+Win</span>
                        <span><span style={{ display: 'inline-block', width: '6px', height: '6px', background: '#ff7351', marginRight: '2px' }}></span>Conflict+Loss</span>
                    </div>
                </div>
            )}

            {/* Trade table */}
            <div style={{ width: '100%', overflowX: 'auto', background: '#000', border: '1px solid rgba(72,72,72,0.2)' }}>
                <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse', minWidth: '1100px', fontSize: '11px' }}>
                    <thead>
                        <tr style={{ background: '#1f1f1f', borderBottom: '1px solid rgba(72,72,72,0.2)' }}>
                            {['Time', 'Mode', 'Dir', 'AI_Bias', 'Conf', 'Type', 'Status', 'PnL', 'AI_Reason', 'Entry', 'Exit'].map(h => (
                                <th key={h} style={{ padding: '10px 10px', fontFamily: "'Space Grotesk'", fontSize: '10px', fontWeight: 700, color: '#ababab', letterSpacing: '0.08em', textTransform: 'uppercase', textAlign: ['PnL', 'Entry', 'Exit', 'Conf'].includes(h) ? 'right' : 'left' }}>{h}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {trades.slice().reverse().slice(0, 30).map((t, i) => {
                            const pnl = t.pnl ?? 0;
                            const exitPrice = t.exit_reason === 'TP' ? t.take_profit : t.exit_reason === 'SL' ? t.stop_loss : null;
                            return (
                                <tr key={i} style={{ borderBottom: '1px solid rgba(72,72,72,0.06)' }}>
                                    <td style={{ padding: '8px 10px', color: '#ababab', fontSize: '10px' }}>{t.timestamp?.slice(5, 19).replace('T', ' ')}</td>
                                    <td style={{ padding: '8px 10px', fontSize: '10px', fontWeight: 700 }}>{t.mode || '—'}</td>
                                    <td style={{ padding: '8px 10px', color: t.direction === 'LONG' ? '#9cff93' : '#ff7351', fontWeight: 700 }}>{t.direction === 'LONG' ? '↑' : '↓'} {t.direction}</td>
                                    <td style={{ padding: '8px 10px' }}><span style={{ color: t.ai_bias === 'BULLISH' ? '#9cff93' : t.ai_bias === 'BEARISH' ? '#ff7351' : '#ababab', fontSize: '10px', border: `1px solid ${t.ai_bias === 'BULLISH' ? 'rgba(156,255,147,0.2)' : t.ai_bias === 'BEARISH' ? 'rgba(255,115,81,0.2)' : 'rgba(72,72,72,0.2)'}`, padding: '1px 5px' }}>{t.ai_bias || '—'}</span></td>
                                    <td style={{ padding: '8px 10px', textAlign: 'right', fontSize: '10px' }}>{t.ai_confidence ? `${t.ai_confidence}%` : '—'}</td>
                                    <td style={{ padding: '8px 10px', fontSize: '10px' }}>{t.signal_type || '—'}</td>
                                    <td style={{ padding: '8px 10px' }}><span style={{ padding: '1px 5px', fontWeight: 700, fontSize: '9px', background: t.status === 'WIN' ? '#00FF41' : t.status === 'LOSS' ? '#ff7351' : t.status === 'EA_SKIPPED' ? '#ffc15b' : '#262626', color: t.status === 'WIN' ? '#006413' : t.status === 'LOSS' ? '#450900' : t.status === 'EA_SKIPPED' ? '#352200' : '#fff' }}>{t.status}</span></td>
                                    <td style={{ padding: '8px 10px', textAlign: 'right', color: pnl > 0 ? '#9cff93' : pnl < 0 ? '#ff7351' : '#ababab', fontWeight: 700 }}>{t.pnl != null ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}` : '—'}</td>
                                    <td style={{ padding: '8px 10px', fontSize: '9px', color: '#484848', maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.ai_reason || '—'}</td>
                                    <td style={{ padding: '8px 10px', textAlign: 'right', fontSize: '10px' }}>{t.entry_price?.toLocaleString(undefined, { minimumFractionDigits: 1 }) || '—'}</td>
                                    <td style={{ padding: '8px 10px', textAlign: 'right', fontSize: '10px' }}>{exitPrice ? exitPrice.toLocaleString(undefined, { minimumFractionDigits: 1 }) : '—'}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
};
