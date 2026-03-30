import { useState } from 'react';
import { AIPanel } from './components/AIPanel';
import { SidePanel } from './components/SidePanel';

import { LastSignal } from './components/LastSignal';



import AIAnalysisPage from './components/AIAnalysisPage';

import { useDashboard } from './hooks/useDashboard';
import { RefreshCw } from 'lucide-react';

// TODO: Remove unused import after cleaning up
import AnalysisDashboard from './components/AnalysisDashboard';

function App() {
  const { data, toggleAI, tradeHistory, runAIAnalysis, isAnalyzing, aiInsight } = useDashboard();
  const [page, setPage] = useState<'live' | 'analysis' | 'ai_analysis'>('live');

  if (!data) {
    return (
      <div className="flex flex-col items-center justify-center bg-[#0e0e0e] text-white h-screen">
        <RefreshCw className="animate-spin mb-8 text-[#00e3fd]" size={64} />
        <span className="text-xl font-black uppercase tracking-[0.5em] opacity-60 font-headline">Initializing Neural Terminal</span>
      </div>
    );
  }

  return (
    <div className="bg-[#0e0e0e] text-white min-h-screen flex flex-col font-body selection:bg-primary selection:text-on-primary">
      {/* Fixed Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-[#0e0e0e]/90 backdrop-blur-md flex justify-between items-center px-6 py-3 border-b border-outline-variant/15">
        <div className="flex items-center gap-8">
          <span className="text-[#00FF41] font-black italic text-xl tracking-tighter font-headline">BTC SNIPER SF</span>
          <nav className="flex gap-6">
            <TabLink active={page === 'live'} label="LIVE_FEED" onClick={() => setPage('live')} />
            <TabLink active={page === 'ai_analysis'} label="LOG" onClick={() => setPage('ai_analysis')} />
            <TabLink active={page === 'analysis'} label="ANALYSIS" onClick={() => setPage('analysis')} />
          </nav>
        </div>
        <HeaderStats data={data} />
      </header>

      {/* Scrollable Content Area */}
      <main className="flex-1 pt-20 pb-12 px-6 overflow-y-auto">
        {page === 'live' && (
          <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-4 max-w-[1600px] mx-auto">      
            <div className="flex flex-col gap-4">
              <AIPanel data={data} />
              <LastSignal signal={data.last_signal} />
              
            </div>
            <div className="flex flex-col gap-4">
              <SidePanel data={data} onToggle={toggleAI} />
              
            </div>
          </div>
        )}

        {page === 'ai_analysis' && (
          <AIAnalysisPage trades={tradeHistory || []} />
        )}

        {page === 'analysis' && (
          <AnalysisDashboard
            data={data}
            tradeHistory={tradeHistory || []}
            runAIAnalysis={runAIAnalysis}
            isAnalyzing={isAnalyzing}
            aiInsight={aiInsight}
          />
        )}
      </main>

      {/* Fixed Status Badge */}
      <div className="fixed bottom-4 right-4 z-40">
        <div className="bg-[#1f1f1f] border border-[#9cff93] px-4 py-2 flex items-center gap-3">
          <div className="w-2 h-2 bg-[#9cff93] animate-pulse"></div>
          <span className="text-[10px] font-bold tracking-widest text-[#9cff93] uppercase">Uptime: {data.bot_uptime || '00:00:00'}</span>
        </div>
      </div>
    </div>
  );
}

const TabLink = ({ active, label, onClick }: any) => (
  <a 
    onClick={onClick} 
    className={`font-headline uppercase tracking-tighter text-sm font-bold cursor-pointer pb-1 transition-all ${
      active ? 'text-[#00FF41] border-b-2 border-[#00FF41]' : 'text-[#ababab] hover:text-[#00FF41]'
    }`}
  >
    {label}
  </a>
);

const HeaderStats = ({ data }: any) => (
  <div className="flex items-center gap-4 text-[10px] tracking-widest text-[#ababab] uppercase font-mono">
    <StatBox label="BTC PRICE" value={data.price.toLocaleString(undefined, { minimumFractionDigits: 1 })} color="#00fc40" />
    <div className="w-px h-8 bg-outline-variant/30"></div>
    <StatBox label="SESSION" value={data.session || 'NY'} color="#00e3fd" />
    <div className="w-px h-8 bg-outline-variant/30"></div>
    <StatBox label="REGIME" value={data.regime || 'VOLATILE'} color="#ff7351" />
  </div>
);

const StatBox = ({ label, value, color }: any) => (
  <div className="flex flex-col items-end">
    <span style={{ color }}>{label}</span>
    <span className="text-white font-bold text-sm tracking-tight">{value}</span>
  </div>
);

export default App;