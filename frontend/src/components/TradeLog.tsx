import React from 'react';
import { Shield, Wifi } from 'lucide-react';

// Step 8: Added Entry, SL, TP columns to show full position data
export const TradeLog: React.FC<{ positions: any[]; onClear?: () => Promise<void> }> = ({ positions }) => {
    return (
        <section className="bg-surface-container-low p-4 space-y-4">
            <div className="flex justify-between items-center border-b border-outline-variant/10 pb-3">
                <div className="flex items-center gap-4">
                    <h2 className="text-[10px] tracking-[0.2em] uppercase font-bold text-primary">Active Positions</h2>
                </div>
                <div className="flex items-center gap-2 px-3 py-1 bg-primary/5 border border-primary/10">
                    <Wifi size={10} className="text-primary animate-pulse" />
                    <span className="text-[9px] font-bold text-primary uppercase tracking-widest">Live</span>
                </div>
            </div>

            <div className="overflow-auto max-h-[300px] custom-scrollbar">
                <table className="w-full text-left border-collapse font-mono">
                    <thead className="sticky top-0 z-20 bg-surface-container-high">
                        <tr className="border-b border-outline-variant/30">
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest">Symbol</th>
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest">Type</th>
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Vol</th>
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Entry</th>
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest text-right">SL</th>
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest text-right">TP</th>
                            <th className="p-3 text-[8px] font-bold text-on-surface-variant uppercase tracking-widest text-right">PnL</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-outline-variant/10">
                        {(positions || []).length > 0 ? (
                            positions.map((pos, i) => (
                                <tr key={i} className="hover:bg-surface-container-highest transition-colors">
                                    <td className="p-3">
                                        <div className="flex flex-col">
                                            <span className="text-[10px] font-bold text-white uppercase tracking-wider">{pos.symbol}</span>
                                            <span className="text-[7px] text-on-surface-variant font-bold">ID: {pos.ticket}</span>
                                        </div>
                                    </td>
                                    <td className="p-3">
                                        <span className={`text-[9px] font-bold uppercase ${
                                            pos.type === 'BUY' ? 'text-primary' : 'text-error'
                                        }`}>
                                            {pos.type}
                                        </span>
                                    </td>
                                    <td className="p-3 text-right text-[10px] text-white">
                                        {typeof pos.volume === 'number' ? pos.volume.toFixed(2) : '--'}
                                    </td>
                                    <td className="p-3 text-right text-[10px] text-white">
                                        {pos.price_open != null ? pos.price_open.toLocaleString() : '--'}
                                    </td>
                                    <td className="p-3 text-right text-[10px] text-error">
                                        {pos.sl != null ? pos.sl.toLocaleString() : '--'}
                                    </td>
                                    <td className="p-3 text-right text-[10px] text-primary">
                                        {pos.tp != null ? pos.tp.toLocaleString() : '--'}
                                    </td>
                                    <td className={`p-3 text-[10px] font-bold text-right ${
                                        (pos.profit || 0) >= 0 ? 'text-primary' : 'text-error'
                                    }`}>
                                        {(pos.profit || 0) >= 0 ? '+' : ''}{pos.profit?.toFixed(2) || '0.00'}
                                    </td>
                                </tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={7} className="p-12 text-center">
                                     <div className="flex flex-col items-center gap-3 opacity-20">
                                          <Shield size={32} className="text-on-surface-variant" />
                                          <span className="text-[8px] font-bold uppercase tracking-[0.4em]">No Active Trades</span>
                                     </div>
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </section>
    );
};