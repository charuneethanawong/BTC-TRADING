import React from 'react'
import type { TradeLogEntry } from '../types'
import { CheckCircle2, XCircle, AlertCircle, FileSearch, TrendingUp, TrendingDown } from 'lucide-react'

const TradeHistoryTable: React.FC<{ data: TradeLogEntry[] }> = ({ data }) => {
  const closedTrades = data.filter(t => ['WIN', 'LOSS'].includes(t.status));
  const wins = closedTrades.filter(t => t.status === 'WIN').length;
  const winRate = closedTrades.length > 0 ? ((wins / closedTrades.length) * 100).toFixed(1) : '0.0';

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between mb-4 px-2">
        <div className="flex items-center space-x-2">
          <FileSearch className="w-5 h-5 text-[#00e3fd]" />
          <h2 className="text-sm font-bold tracking-widest uppercase text-[#ffffff]">TRADE_EXECUTION_AUDIT</h2>
        </div>
        <div className="text-[10px] font-mono text-[#ababab]">
          TRADES: {data.length} | WIN_RATE: {winRate}%
        </div>
      </div>

      <div className="flex-1 overflow-auto custom-scrollbar bg-[#000000] border border-[#171717]">
        <table className="w-full text-left border-collapse font-mono">
          <thead className="sticky top-0 bg-[#1f1f1f] text-[10px] text-[#ababab] border-b border-[#303030]">
            <tr>
              <th className="p-3 font-normal uppercase tracking-tighter">ID</th>
              <th className="p-3 font-normal uppercase tracking-tighter text-center">MODE</th>
              <th className="p-3 font-normal uppercase tracking-tighter text-center">TYPE</th>
              <th className="p-3 font-normal uppercase tracking-tighter">DIR</th>
              <th className="p-3 font-normal uppercase tracking-tighter text-right">ENTRY</th>
              <th className="p-3 font-normal uppercase tracking-tighter text-right">SL/TP</th>
              <th className="p-3 font-normal uppercase tracking-tighter text-center">STATUS</th>
              <th className="p-3 font-normal uppercase tracking-tighter text-right">PnL</th>
            </tr>
          </thead>
          <tbody className="text-[11px]">
            {data.map((entry, idx) => (
              <tr 
                key={idx} 
                className="border-b border-[#171717] transition-colors hover:bg-[#1f1f1f]/50"
              >
                <td className="p-3 text-[#ffffff] font-bold tracking-tighter truncate max-w-[120px]" title={entry.signal_id}>
                  {entry.signal_id}
                </td>
                <td className="p-3 text-center">
                  <span className="px-1.5 py-0.5 bg-[#1f1f1f] text-[9px] border border-[#303030] text-[#00e3fd]">
                    {entry.mode}
                  </span>
                </td>
                <td className="p-3 text-center text-[#ababab]">{entry.signal_type}</td>
                <td className="p-3 text-center">
                  <div className="flex items-center justify-center space-x-1">
                    {entry.direction === 'LONG' ? <TrendingUp className="w-3 h-3 text-[#00fc40]" /> : <TrendingDown className="w-3 h-3 text-[#FF0000]" />}
                    <span className={entry.direction === 'LONG' ? 'text-[#00fc40]' : 'text-[#FF0000]'}>{entry.direction}</span>
                  </div>
                </td>
                <td className="p-3 text-right text-[#ffffff]">{entry.entry_price.toLocaleString()}</td>
                <td className="p-3 text-right text-[10px] text-[#ababab]">
                  {entry.stop_loss.toFixed(1)} / {entry.take_profit.toFixed(1)}
                </td>
                <td className="p-3">
                  <div className="flex items-center justify-center">
                    {entry.status === 'WIN' && <CheckCircle2 className="w-4 h-4 text-[#9cff93]" />}
                    {entry.status === 'LOSS' && <XCircle className="w-4 h-4 text-[#FF0000]" />}
                    {entry.status === 'EA_SKIPPED' && <div title={entry.skip_reason}><AlertCircle className="w-4 h-4 text-[#ffc15b]" /></div>}
                    {entry.status === 'OPEN' && <div className="w-2 h-2 bg-[#00e3fd] animate-pulse"></div>}
                  </div>
                </td>
<td className="p-3 text-right font-bold">
                  {entry.pnl ? (entry.pnl > 0 ? '+' + entry.pnl.toFixed(2) : entry.pnl.toFixed(2)) : '---'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default TradeHistoryTable
