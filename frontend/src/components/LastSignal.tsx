import React from 'react';
import { TrendingUp, TrendingDown, Clock, Zap } from 'lucide-react';
import type { SignalInfo } from '../types/dashboard';

export const LastSignal: React.FC<{ signal: SignalInfo }> = ({ signal }) => {
    if (!signal || !signal.signal_id) return (
        <div className="stitch-card h-full justify-center items-center opacity-30 bg-base">
            <Zap className="text-surface-highest mb-6" size={64} />
            <span className="text-[10px] font-bold tracking-[0.5em] text-surface-highest uppercase px-12 text-center">Neural Link Offline • Awaiting Event</span>
        </div>
    );

    const isLong = signal.direction === 'LONG';

    return (
        <section className="stitch-card p-4 space-y-4">
            <div className="flex justify-between items-center border-b border-line pb-3">
                <div className="flex items-center gap-3">
                    <div className={`p-1.5 ${isLong ? 'bg-primary/20 text-primary' : 'bg-secondary/20 text-secondary'}`}>
                        {isLong ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                    </div>
                    <h2 className="text-[10px] tracking-[0.2em] uppercase font-bold text-white">Event Dispatch</h2>
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-[9px] text-text-dim uppercase font-bold">MODE: {signal.mode}</span>
                    <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse"></div>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                    <label className="text-[8px] text-text-dim uppercase font-bold">Signal Direction</label>
                    <div className={`text-lg font-display font-black ${isLong ? 'text-primary' : 'text-secondary'}`}>
                        {signal.direction} CONFIRMED
                    </div>
                </div>
                <div className="space-y-1 text-right">
                    <label className="text-[8px] text-text-dim uppercase font-bold">Dispatch ID</label>
                    <div className="text-white text-[10px] font-data font-bold tracking-widest leading-loose">
                        {signal.signal_id}
                    </div>
                </div>
            </div>

            <div className="bg-base p-4 border-l-2 border-primary grid grid-cols-2 gap-4">
                <div>
                    <label className="text-[8px] text-text-dim uppercase font-bold block mb-1">Entry Point</label>
                    <div className="text-white text-xl font-display font-medium tracking-tighter">
                        ${signal.entry_price.toLocaleString()}
                    </div>
                </div>
                <div className="text-right">
                    <label className="text-[8px] text-text-dim uppercase font-bold block mb-1">Target TP</label>
                    <div className="text-primary text-xl font-display font-medium tracking-tighter">
                        ${signal.take_profit.toLocaleString()}
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-3 gap-2">
                {[
                    { label: 'Stop Loss', val: `$${signal.stop_loss.toLocaleString()}`, color: 'text-secondary' },
                    { label: 'Risk Ratio', val: `1:${(signal.rr || 3.0).toFixed(1)}`, color: 'text-white' },
                    { label: 'Precision', val: `${signal.score}/10`, color: 'text-primary' },
                ].map((item, i) => (
                    <div key={i} className="bg-surface-low p-2 border border-line">
                        <div className="text-[7px] text-text-dim font-bold uppercase">{item.label}</div>
                        <div className={`text-[9px] font-bold ${item.color}`}>{item.val}</div>
                    </div>
                ))}
            </div>

            <div className="flex justify-between items-center pt-2">
                <div className="flex items-center gap-2">
                    <Clock size={10} className="text-text-dimmer" />
                    <span className="text-[8px] text-text-dimmer font-bold tracking-widest">{signal.time}</span>
                </div>
                <span className="text-[8px] text-primary font-bold tracking-[0.3em]">
                    {signal.mode || 'UNKNOWN'} / {signal.direction}
                </span>
            </div>
            
            {/* Background Texture Effect */}
            <div className="absolute bottom-0 right-0 p-12 opacity-[0.02] pointer-events-none group-hover:opacity-[0.05] transition-opacity">
                 <Zap size={140} />
            </div>
        </section>
    );
};
