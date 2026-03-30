import React from 'react'

interface HeroMetricsProps {
  totalPnl: number;
  winRate: number;
  profitFactor: number;
  avgWin: number;
  avgLoss: number;
  totalClosed: number;
}

const HeroMetrics: React.FC<HeroMetricsProps> = ({ totalPnl, winRate, profitFactor, avgWin, avgLoss, totalClosed }) => {
  return (
    <section className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 font-headline">
      <div className="bg-surface-container-high p-6 border-l-4 border-primary">
        <p className="text-xs font-bold text-on-surface-variant mb-2 uppercase tracking-widest">Total PnL</p>
        <h3 className="text-3xl font-black tracking-tighter text-primary">${totalPnl.toFixed(2)}</h3>
        <p className="mt-4 text-[10px] text-primary/60 uppercase">OPERATIONAL_CAPITAL_FLOW</p>
      </div>
      <div className="bg-surface-container-high p-6 border-l-4 border-secondary">
        <p className="text-xs font-bold text-on-surface-variant mb-2 uppercase tracking-widest">Win Rate</p>
        <h3 className="text-3xl font-black tracking-tighter text-on-surface">{winRate.toFixed(1)}%</h3>
        <p className="mt-4 text-[10px] text-secondary/60 uppercase">SAMPLES: {totalClosed}</p>
      </div>
      <div className="bg-surface-container-high p-6 border-l-4 border-tertiary">
        <p className="text-xs font-bold text-on-surface-variant mb-2 uppercase tracking-widest">Profit Factor</p>
        <h3 className="text-3xl font-black tracking-tighter text-on-surface">{profitFactor.toFixed(2)}</h3>
        <p className="mt-4 text-[10px] text-tertiary/60 uppercase">EFFICIENCY_METRIC_V4</p>
      </div>
      <div className="bg-surface-container-high p-6 border-l-4 border-error">
        <p className="text-xs font-bold text-on-surface-variant mb-2 uppercase tracking-widest">Avg Win/Loss</p>
        <div className="flex items-baseline gap-2 font-mono">
          <span className="text-xl font-bold text-primary">+${avgWin.toFixed(2)}</span>
          <span className="text-lg font-bold text-on-surface-variant">/</span>
          <span className="text-xl font-bold text-error">${avgLoss.toFixed(2)}</span>
        </div>
        <p className="mt-4 text-[10px] text-error/60 uppercase">NEGATIVE_EXPECTANCY_WARN</p>
      </div>
    </section>
  )
}

export default HeroMetrics