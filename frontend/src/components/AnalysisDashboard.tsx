import React from 'react'
import EngineSelector from './EngineSelector'
import HeroMetrics from './HeroMetrics'
import PerformanceHeatmap from './PerformanceHeatmap'
import AgreementMatrix from './AgreementMatrix'
import { useAnalysisData } from '../hooks/useAnalysisData'
import type { TradeRecord } from '../hooks/useAnalysisData'
import type { DashboardState } from '../types/dashboard'
import { Terminal, Network } from 'lucide-react'

// Step 5+6: Removed useDashboard() double-fetch — now receives props from App.tsx
interface AnalysisDashboardProps {
  data: DashboardState | null;
  tradeHistory: TradeRecord[];
  runAIAnalysis: (engine: string) => Promise<void>;
  isAnalyzing: boolean;
  aiInsight: string | null;
}

const AnalysisDashboard: React.FC<AnalysisDashboardProps> = ({
  data,
  tradeHistory,
  runAIAnalysis,
  isAnalyzing,
  aiInsight,
}) => {
  // useAnalysisData runs purely off tradeHistory — no API fetch
  const stats = useAnalysisData(tradeHistory)

  const heroData = {
    totalPnl: stats.overallStats.totalPnl,
    winRate: stats.overallStats.winRate,
    profitFactor: stats.overallStats.profitFactor,
    avgWin: stats.overallStats.avgWin,
    avgLoss: stats.overallStats.avgLoss,
    totalClosed: stats.overallStats.totalClosed
  }

  const heatmapData: Record<string, Record<string, { wr: number }>> = {}
  for (const [session, types] of Object.entries(stats.sessionTypeMatrix)) {
    heatmapData[session] = {}
    for (const [type, wr] of Object.entries(types)) {
      if (wr >= 0) {
        heatmapData[session][type] = { wr: Math.round(wr) }
      }
    }
  }

  const alignmentData = {
    alignedWin: stats.aiAlignment.alignedWin,
    alignedLoss: stats.aiAlignment.alignedLoss,
    conflictWin: stats.aiAlignment.conflictWin,
    conflictLoss: stats.aiAlignment.conflictLoss,
    alignedWinPnl: `${stats.aiAlignment.alignedWinPnl >= 0 ? '+' : ''}$${stats.aiAlignment.alignedWinPnl.toFixed(1)}`,
    alignedLossPnl: `${stats.aiAlignment.alignedLossPnl >= 0 ? '+' : ''}$${stats.aiAlignment.alignedLossPnl.toFixed(1)}`,
    conflictWinPnl: `${stats.aiAlignment.conflictWinPnl >= 0 ? '+' : ''}$${stats.aiAlignment.conflictWinPnl.toFixed(1)}`,
    conflictLossPnl: `${stats.aiAlignment.conflictLossPnl >= 0 ? '+' : ''}$${stats.aiAlignment.conflictLossPnl.toFixed(1)}`,
    alignedWr: stats.aiAlignment.alignedWinRate.toFixed(1),
    conflictWr: stats.aiAlignment.conflictWinRate.toFixed(1)
  }

  return (
    <div className="flex flex-col space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700 max-w-[1600px] mx-auto pb-24">
      <div className="flex items-center justify-between">
        <div className="flex flex-col">
          <h2 className="font-headline text-3xl font-black tracking-tighter text-on-surface uppercase">
            Neural Core <span className="text-primary font-light">|</span> Tactical Analysis
          </h2>
          <p className="text-[10px] font-mono text-on-surface-variant uppercase tracking-[0.2em] mt-1 font-bold">
            System Status: {isAnalyzing ? 'ENGINE_BUSY_ANALYZING' : 'Active_Intelligence_Node_V30.0'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex flex-col items-end">
            <span className="text-[10px] font-black text-primary uppercase">Encrypted_Feed</span>
            {/* Step 6: Use real session from data instead of hardcoded ASIA_NORTH */}
            <span className="text-[9px] font-mono text-on-surface-variant uppercase opacity-60">
              Node: {data?.session || 'UNKNOWN'}
            </span>
          </div>
          <div className="w-10 h-10 bg-surface-container-high border border-outline-variant/20 flex items-center justify-center">
            <Network className="text-secondary" size={20} />
          </div>
        </div>
      </div>

      <EngineSelector onExecute={runAIAnalysis} isAnalyzing={isAnalyzing} />
      
      <HeroMetrics 
        totalPnl={heroData.totalPnl}
        winRate={heroData.winRate}
        profitFactor={heroData.profitFactor}
        avgWin={heroData.avgWin}
        avgLoss={heroData.avgLoss}
        totalClosed={heroData.totalClosed}
      />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        <div className="lg:col-span-8">
          <PerformanceHeatmap data={heatmapData} />
        </div>
        <div className="lg:col-span-4">
          <AgreementMatrix alignment={alignmentData} />
        </div>
      </div>

      <section className="bg-surface-container-lowest p-6 border border-outline-variant/10 relative overflow-hidden">
        <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-primary to-transparent opacity-20"></div>
        <div className="flex items-center gap-3 mb-4">
          <Terminal className="text-primary" size={20} />
          <h2 className="font-headline text-lg font-black tracking-tight uppercase">Technical Logic &amp; Advisory</h2>
        </div>
        <div className="space-y-3 text-xs font-mono leading-relaxed text-on-surface-variant">
          <p className="border-l-2 border-primary/30 pl-4 py-1">
            <span className="text-primary font-bold">[STRUCTURE_ANALYSIS]:</span> {aiInsight || data?.ai?.reason || 'Scanning market structure...'}
          </p>
          <p className="border-l-2 border-secondary/30 pl-4 py-1">
            <span className="text-secondary font-bold">[FLOW_DIAGNOSTIC]:</span> {data?.market?.wall_info || 'Analyzing order flow dynamics...'}
          </p>
        </div>
      </section>
    </div>
  )
}

export default AnalysisDashboard