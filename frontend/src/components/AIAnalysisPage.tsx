import React, { useState, useMemo } from 'react';
import { Search, Download, Zap, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';
import type { TradeRecord } from '../hooks/useAnalysisData';

interface JoinedLogEntry {
  timestamp: string;
  signal_id: string;
  mode: string;
  signal_type: string;
  direction: string;
  confidence: number;
  ai_bias: string;
  ai_action: string;     // v31.0: NEW
  ai_aligned: boolean;
  market_reality?: string;
  status: string;
  pnl?: number | null;
  score: number;          // v31.0: NEW
  session: string;      // v31.0: NEW
}

const AIAnalysisPage: React.FC<{ trades: TradeRecord[] }> = ({ trades }) => {
  const [filter, setFilter] = useState('');

  // Calculate real metrics from trades
  const metrics = useMemo(() => {
    const closedTrades = (trades || []).filter(t => t.status === 'WIN' || t.status === 'LOSS');
    const totalCount = closedTrades.length;
    
    // AI Avg Confidence
    const totalConfidence = closedTrades.reduce((sum, t) => sum + (t.ai_confidence || 0), 0);
    const avgConfidence = totalCount > 0 ? totalConfidence / totalCount : 0;
    
    // AI Aligned Rate (how often did we follow AI bias and win?)
    const alignedTrades = closedTrades.filter(t => t.ai_aligned === true);
    const alignedWinTrades = closedTrades.filter(t => t.ai_aligned === true && t.status === 'WIN');
    const alignedRate = alignedTrades.length > 0 ? (alignedWinTrades.length / alignedTrades.length) * 100 : 0;
    
    return {
      avgConfidence: avgConfidence.toFixed(1),
      alignedRate: alignedRate.toFixed(1),
      totalTrades: totalCount,
    };
  }, [trades]);

  const logs: JoinedLogEntry[] = useMemo(() => {
    return (trades || []).map(t => ({
      timestamp: t.timestamp,
      signal_id: t.signal_id || 'N/A',
      mode: t.mode || 'N/A',
      signal_type: t.signal_type || 'N/A',
      direction: t.direction,
      confidence: t.ai_confidence || 0,
      ai_bias: t.ai_bias || 'NEUTRAL',
      ai_action: t.ai_action || 'N/A',       // v31.0: NEW
      ai_aligned: t.ai_aligned ?? false,
      market_reality: t.actual_direction,
      status: t.status,
      pnl: t.pnl,
      score: t.score || 0,                    // v31.0: NEW
      session: t.session || 'N/A'             // v31.0: NEW
    }));
  }, [trades]);

  return (
    <div className="flex flex-col space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700 max-w-[1600px] mx-auto pb-20">
      
      {/* 1. OPERATIONAL SUMMARY METRICS - REAL DATA */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <MetricCard 
          title="AI_CONFIDENCE_AVG" 
          value={metrics.avgConfidence} 
          unit="%" 
          icon={<Zap size={18}/>} 
          color="secondary" 
        />
        <MetricCard 
          title="AI_ALIGNED_RATE" 
          value={metrics.alignedRate} 
          unit="%" 
          icon={<Zap size={18}/>} 
          color="primary" 
        />
        <MetricCard 
          title="TRADES_ANALYZED" 
          value={metrics.totalTrades.toString()} 
          unit="EVT" 
          icon={<Zap size={18}/>} 
          color="tertiary" 
        />
      </section>

      {/* 2. TACTICAL FILTER INTERFACE */}
      <div className="bg-surface-container-low border border-outline-variant/10 relative overflow-hidden">
        <div className="absolute top-0 left-0 w-1 h-full bg-primary"></div>
        <div className="p-6 flex flex-wrap justify-between items-center gap-6">
          <div className="flex flex-col">
            <h2 className="font-headline text-2xl font-black tracking-tighter text-on-surface uppercase">
              Integrated Audit <span className="text-primary font-light">|</span> v2.0
            </h2>
            <span className="text-[9px] font-mono text-on-surface-variant uppercase tracking-[0.3em] mt-1">Status: Scanning_Data_Reservoir</span>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="relative group">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-outline group-focus-within:text-primary transition-colors" />
              <input 
                className="bg-surface-container-lowest border-0 border-b-2 border-outline-variant text-[11px] font-mono pl-10 pr-4 py-2.5 w-64 focus:ring-0 focus:border-primary placeholder:text-outline-variant/50 uppercase transition-all outline-none"
                placeholder="SEARCH_SIGNAL_HASH..."
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </div>
            <button className="bg-primary text-on-primary font-headline font-bold text-xs px-6 py-3 flex items-center gap-3 hover:brightness-110 active:scale-95 transition-all shadow-[0_0_15px_rgba(156,255,147,0.2)]">
              <Download size={14} />
              EXPORT_LOGS
            </button>
          </div>
        </div>
      </div>

      {/* 3. HIGH-DENSITY AUDIT TRAIL */}
      <div className="bg-surface-container-lowest border border-outline-variant/10 overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse font-mono">
            <thead>
              <tr className="bg-surface-container-high text-[10px] text-on-surface-variant font-black uppercase tracking-widest border-b border-outline-variant/20">
                <th className="p-4 pl-6">TIMESTAMP</th>
                <th className="p-4">IDENTIFIER_HASH</th>
                <th className="p-4 text-center">DIR</th>
                <th className="p-4 text-center">MODE / SIGNAL_TYPE</th>
                <th className="p-4 text-center">SCORE</th>
                <th className="p-4 text-center">CONF_LVL</th>
                <th className="p-4 text-center">AI_ACTION</th>
                <th className="p-4 text-center">ALIGNED</th>
                <th className="p-4 text-center">SESSION</th>
                <th className="p-4 text-center">OUTCOME</th>
                <th className="p-4 text-right pr-6">PnL_REALIZED</th>
              </tr>
            </thead>
            <tbody className="text-[12px] divide-y divide-outline-variant/5">
              {logs.map((entry, idx) => (
                <tr key={idx} className="group hover:bg-surface-container-low/40 transition-all duration-200">
                  <td className="p-4 pl-6 text-on-surface-variant font-medium whitespace-nowrap opacity-60 group-hover:opacity-100 transition-opacity">
                    {new Date(entry.timestamp).toLocaleTimeString()}
                  </td>
                  <td className="p-4">
                    <span className="text-on-surface font-black tracking-tight group-hover:text-primary transition-colors cursor-pointer">{entry.signal_id}</span>
                  </td>
                  <td className="p-4 text-center">
                    <span className={`text-[10px] font-black ${
                      entry.direction === 'LONG' ? 'text-primary' : 'text-error'
                    }`}>
                      {entry.direction}
                    </span>
                  </td>
                  <td className="p-4 text-center">
                    <div className="flex flex-col items-center gap-0.5">
                      <span className="text-secondary font-black text-[10px]">{entry.mode}</span>
                      <span className="text-[8px] text-outline font-bold uppercase opacity-50 tracking-tighter">{entry.signal_type}</span>
                    </div>
                  </td>
                  <td className="p-4 text-center">
                    <span className="font-black text-on-surface">{entry.score}</span>
                  </td>
                  <td className="p-4 text-center">
                    <div className="flex flex-col items-center gap-1">
                      <span className="font-black text-on-surface">{entry.confidence}%</span>
                      <div className="w-12 h-1 bg-surface-container-highest overflow-hidden">
                        <div className="h-full bg-secondary" style={{ width: `${entry.confidence}%` }}></div>
                      </div>
                    </div>
                  </td>
                  <td className="p-4 text-center">
                    <span className={`inline-block px-2 py-1 text-[10px] font-black ${
                      entry.ai_action === 'TRADE' ? 'text-primary' :
                      entry.ai_action === 'CAUTION' ? 'text-tertiary' :
                      'text-error'
                    }`}>
                      {entry.ai_action}
                    </span>
                  </td>
                  <td className="p-4 text-center">
                    <span className={`text-[10px] font-black ${entry.ai_aligned ? 'text-primary' : 'text-outline'}`}>
                      {entry.ai_aligned ? 'YES' : 'NO'}
                    </span>
                  </td>
                  <td className="p-4 text-center">
                    <span className="text-[10px] font-bold text-on-surface-variant">{entry.session}</span>
                  </td>
                  <td className="p-4">
                    <div className="flex justify-center items-center gap-2">
                      {entry.status === 'WIN' ? <StatusIcon icon={<CheckCircle2 size={14}/>} color="primary" label="WIN" /> : 
                       entry.status === 'LOSS' ? <StatusIcon icon={<XCircle size={14}/>} color="error" label="LOSS" /> : 
                       <StatusIcon icon={<AlertTriangle size={14}/>} color="tertiary" label="SKIP" />}
                    </div>
                  </td>
                  <td className={`p-4 text-right pr-6 font-black text-[14px] ${entry.pnl && entry.pnl > 0 ? 'text-primary' : 'text-error'}`}>
                    {entry.pnl ? (entry.pnl > 0 ? `+${entry.pnl.toFixed(2)}` : entry.pnl.toFixed(2)) : '0.00'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

interface MetricCardProps {
  title: string
  value: string
  unit: string
  icon: React.ReactNode
  color: 'primary' | 'secondary' | 'tertiary'
}

const MetricCard: React.FC<MetricCardProps> = ({ title, value, unit, icon, color }) => {
  const accentColor = color === 'primary' ? 'border-primary' : color === 'secondary' ? 'border-secondary' : 'border-tertiary';
  const textColor = color === 'primary' ? 'text-primary' : color === 'secondary' ? 'text-secondary' : 'text-tertiary';
  
  return (
    <div className={`bg-surface-container-low p-6 flex flex-col justify-between border-l-4 ${accentColor} relative overflow-hidden shadow-lg group hover:bg-surface-container-high transition-colors`}>
      <div className="absolute -right-2 -bottom-2 opacity-05 text-on-surface group-hover:scale-110 transition-transform duration-500">
        {icon}
      </div>
      <div className="flex justify-between items-start mb-6">
        <span className="font-headline text-[10px] font-black tracking-[0.2em] text-on-surface-variant uppercase">{title}</span>
        <div className={`${textColor} opacity-40`}>{icon}</div>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-4xl font-black tracking-tighter text-on-surface">{value}</span>
        <span className={`font-mono text-[10px] font-bold ${textColor} uppercase tracking-widest`}>{unit}</span>
      </div>
    </div>
  );
};

interface StatusIconProps {
  icon: React.ReactNode
  color: 'primary' | 'error' | 'tertiary'
  label: string
}

const StatusIcon: React.FC<StatusIconProps> = ({ icon, color, label }) => {
  const colorClass = color === 'primary' ? 'text-primary' : color === 'error' ? 'text-error' : 'text-tertiary';
  return (
    <div className={`flex flex-col items-center ${colorClass} opacity-80 hover:opacity-100 transition-opacity`}>
      {icon}
      <span className="text-[7px] font-black mt-0.5 tracking-widest">{label}</span>
    </div>
  );
};

export default AIAnalysisPage;
