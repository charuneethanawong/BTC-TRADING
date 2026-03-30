import React from 'react';
import type { DashboardState } from '../types/dashboard';

export const DashboardGrid: React.FC<{ data: DashboardState }> = ({ data }) => {
    const confluenceItems = [
        { label: 'IPA', val: data.modes?.IPA?.direction || '—', isError: false },
        { label: 'IOF', val: data.modes?.IOF?.direction || '—', isError: false },
        { label: 'IPAF', val: data.modes?.IPAF?.direction || '—', isError: false },
        { label: 'IOFF', val: data.modes?.IOFF?.direction || '—', isError: false },
    ];

    const totalScore = Object.values(data.modes || {}).reduce((acc: number, m: any) => acc + (m?.score || 0), 0);
    
    // v25.0: Dynamic max score based on thresholds (IPA=10, IOF=6, IPAF=10, IOFF=6 = 32)
    const maxScore = Object.values(data.modes || {}).reduce((acc: number, m: any) => acc + (m?.threshold || 10), 0);

    return (
        <section className="bg-surface-container border border-outline-variant/10" style={{ padding: '16px' }}>
            <h2 className="text-[10px] tracking-[0.2em] uppercase font-bold text-secondary font-label" style={{ marginBottom: '16px' }}>Logical Confluence Matrix</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div className="flex justify-between items-center text-[10px] font-mono">
                    <span className="text-on-surface-variant">AGGREGATE SCORE</span>
                    <span className="text-primary font-bold">{String(totalScore).padStart(2, '0')}/{maxScore}</span>
                </div>
                <div className="h-1 bg-surface-container-highest w-full overflow-hidden">
                    <div className="h-full bg-primary" style={{ width: `${Math.min((totalScore / maxScore) * 100, 100)}%` }}></div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', paddingTop: '8px' }}>
                    {confluenceItems.map((item, i) => {
                        // v25.0: Dynamic color based on direction
                        const isLong = item.val === 'LONG';
                        const isShort = item.val === 'SHORT';
                        const colorClass = isLong ? 'text-primary' : isShort ? 'text-error' : 'text-on-surface-variant';
                        return (
                            <div key={i} className="bg-surface-container-low p-2 border border-outline-variant/10">
                                <div className="text-[8px] text-on-surface-variant font-mono">{item.label}</div>
                                <div className={`text-[10px] font-bold font-mono ${colorClass}`}>{item.val}</div>
                            </div>
                        );
                    })}
                </div>
            </div>
        </section>
    );
};
