import React from 'react'
import type { AIAnalysisEntry } from '../types'
import { Brain, Activity, Zap, RefreshCcw, Layers } from 'lucide-react'

const AILogicPanel: React.FC<{ data: AIAnalysisEntry[] }> = ({ data }) => {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between mb-4 px-2">
        <div className="flex items-center space-x-2">
          <Brain className="w-5 h-5 text-[#9cff93]" />
          <h2 className="text-sm font-bold tracking-widest uppercase text-[#ffffff]">AI_THOUGHT_STREAM</h2>
        </div>
      </div>
      
      <div className="flex-1 overflow-auto space-y-4 pr-2 custom-scrollbar">
        {data.map((entry, idx) => (
          <div 
            key={idx} 
            className="p-4 bg-[#1f1f1f]/40 border-l-2 glass-module flex flex-col space-y-3"
            style={{ 
              borderColor: entry.bias === 'BULLISH' ? '#00e3fd' : 
                          entry.bias === 'BEARISH' ? '#FF0000' : '#ababab' 
            }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <span className="text-[10px] font-bold px-1.5 py-0.5 bg-[#1f1f1f] border border-[#303030]">
                  {entry.bias}
                </span>
                <span className="font-mono text-[11px] text-[#ababab]">@{new Date(entry.timestamp).toLocaleTimeString()}</span>
              </div>
              <div className="flex items-center space-x-2">
                <span className="text-[10px] font-mono text-[#9cff93]">{entry.confidence}%_CONF</span>
                <span className="text-[10px] font-mono px-2 border border-[#303030]">
                  {entry.action}
                </span>
              </div>
            </div>

            <p className="text-xs font-mono text-[#ffffff] leading-relaxed tracking-tight">
              {entry.reason}
            </p>

            <div className="flex items-center space-x-4 pt-1">
              <div className="flex items-center space-x-1 text-[9px] font-mono text-[#ababab]">
                <Activity className="w-3 h-3" />
                <span>LEVEL: {entry.key_level.toFixed(1)}</span>
              </div>
              <div className="flex items-center space-x-1 text-[9px] font-mono text-[#ababab]">
                {entry.reason.toLowerCase().includes('momentum') && <span className="flex items-center"><Zap className="w-3 h-3 mr-1" /> [M]</span>}
                {entry.reason.toLowerCase().includes('structure') && <span className="flex items-center"><Layers className="w-3 h-3 mr-1" /> [R]</span>}
                {entry.reason.toLowerCase().includes('pullback') && <span className="flex items-center"><RefreshCcw className="w-3 h-3 mr-1" /> [MR]</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default AILogicPanel
