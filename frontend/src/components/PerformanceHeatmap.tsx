import React from 'react'

interface HeatmapData {
  [session: string]: {
    [signal: string]: {
      wr: number;
      pnl?: string;
    }
  }
}

interface PerformanceHeatmapProps {
  data: HeatmapData;
}

const PerformanceHeatmap: React.FC<PerformanceHeatmapProps> = ({ data }) => {
  const sessions = ['ASIA', 'LONDON', 'NY']
  // Must match canonicalType() keys in useAnalysisData.ts
  const signals = ['IPA', 'IOF', 'MOMENTUM', 'ABSORPTION', 'REVERSAL', 'MEAN_REVERT', 'FVG', 'EMA', 'POC']

  const getCellClass = (wr: number | undefined) => {
    if (wr === undefined) return 'bg-surface-container-high text-on-surface-variant'
    if (wr >= 70) return 'bg-primary text-on-primary font-black'
    if (wr >= 50) return 'bg-secondary/10 text-secondary border border-secondary/20'
    return 'bg-error/10 text-error'
  }

  return (
    <section className="bg-surface-container-low border border-outline-variant/10 p-6 h-full">
      <h2 className="font-headline text-sm font-bold tracking-widest text-on-surface-variant uppercase mb-6 flex items-center gap-2">
        <span className="material-symbols-outlined text-secondary text-lg">calendar_view_month</span>
        Session Performance Heatmap
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse min-w-[600px]">
          <thead>
            <tr className="text-[10px] uppercase text-on-surface-variant border-b border-outline-variant/30">
              <th className="py-3 px-4 font-black">Session \ Signal</th>
              {signals.map(s => <th key={s} className="py-3 px-4 font-black">{s}</th>)}
            </tr>
          </thead>
          <tbody className="text-xs font-mono">
            {sessions.map(session => (
              <tr key={session} className="border-b border-outline-variant/10 group hover:bg-surface-container-lowest transition-colors">
                <td className="py-4 px-4 font-black text-on-surface font-headline bg-surface-container-low sticky left-0">{session}</td>
                {signals.map(signal => {
                  const cell = data[session]?.[signal]
                  const cls = getCellClass(cell?.wr)
                  return (
                    <td key={signal} className={`py-4 px-4 text-center transition-all ${cls}`}>
                      {cell ? `${cell.wr}%` : '---'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default PerformanceHeatmap