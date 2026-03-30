import React from 'react';
import { Radio, Zap, ArrowUpRight, ArrowDownRight, Clock } from 'lucide-react';

export const EventDispatch: React.FC<{ trades: any[] }> = ({ trades = [] }) => {
    const dispatchedEvents = (trades || []).filter(t => 
        t.ea_opened === true || 
        ['WIN', 'LOSS', 'OPEN'].includes(t.status)
    ).slice(-10).reverse();

    return (
        <section className="bg-surface-container-low p-4 space-y-4 border-t border-outline-variant/10 h-full">
            <div className="flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <Radio className="w-4 h-4 text-primary animate-pulse" />
                    <h2 className="text-[10px] tracking-[0.3em] font-black text-primary uppercase">Event_Dispatch_v1.0</h2>
                </div>
                <span className="text-[8px] font-mono text-on-surface-variant uppercase">Real-time Execution Stream</span>
            </div>

            <div className="space-y-2 max-h-[400px] overflow-y-auto custom-scrollbar pr-2">
                {dispatchedEvents.length > 0 ? (
                    dispatchedEvents.map((event, idx) => (
                        <div key={idx} className="bg-surface-container-lowest p-3 flex justify-between items-center border-l-2 border-primary/30 hover:border-primary transition-all">
                            <div className="flex flex-col gap-1">
                                <div className="flex items-center gap-2">
                                    <span className="text-[10px] font-bold text-white tracking-tighter">{event.signal_id}</span>
                                    <span className={`text-[8px] px-1.5 py-0.5 font-bold ${event.direction === 'LONG' ? 'bg-primary/20 text-primary' : 'bg-error/20 text-error'}`}>
                                        {event.direction}
                                    </span>
                                </div>
                                <div className="flex items-center gap-2 text-[9px] text-on-surface-variant font-mono">
                                    <Clock size={10} />
                                    <span>{new Date(event.timestamp).toLocaleTimeString()}</span>
                                    <span className="opacity-30">|</span>
                                    <span>{event.mode}</span>
                                </div>
                            </div>
                            
                            <div className="text-right">
                                <div className="flex items-center gap-2 justify-end">
                                    <span className="text-xs font-black text-white font-mono">${event.entry_price?.toLocaleString()}</span>
                                    {event.status === 'WIN' ? <ArrowUpRight className="w-3 h-3 text-primary" /> : 
                                     event.status === 'LOSS' ? <ArrowDownRight className="w-3 h-3 text-error" /> : 
                                     <Zap className="w-3 h-3 text-secondary animate-pulse" />}
                                </div>
                                <span className={`text-[9px] font-bold uppercase tracking-widest ${
                                    event.status === 'WIN' ? 'text-primary' : 
                                    event.status === 'LOSS' ? 'text-error' : 'text-secondary'
                                }`}>
                                    {event.status} {event.pnl ? `(${event.pnl > 0 ? '+' : ''}${event.pnl.toFixed(2)})` : ''}
                                </span>
                            </div>
                        </div>
                    ))
                ) : (
                    <div className="h-20 flex items-center justify-center border border-dashed border-outline-variant/20">
                        <span className="text-[9px] font-bold text-on-surface-variant uppercase tracking-[0.3em] opacity-30">Standby for Events</span>
                    </div>
                )}
            </div>
        </section>
    );
};