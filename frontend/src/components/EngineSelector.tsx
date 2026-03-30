import React, { useState } from 'react'
import { Loader2 } from 'lucide-react'

interface EngineSelectorProps {
  onExecute: (engine: string) => void;
  isAnalyzing: boolean;
}

const EngineSelector: React.FC<EngineSelectorProps> = ({ onExecute, isAnalyzing }) => {
  const [selected, setSelected] = useState('CLAUDE')

  return (
    <section className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center bg-surface-container-low p-6 border border-outline-variant/10">
      <div className="lg:col-span-4 flex flex-col gap-4">
        <h2 className="font-headline text-sm font-bold tracking-widest text-on-surface-variant uppercase">Engine Selection</h2>
        <div className="flex gap-2">
          {['CLAUDE', 'GEMINI', 'DEEPSEEK'].map((engine) => (
            <button
              key={engine}
              onClick={() => setSelected(engine)}
              disabled={isAnalyzing}
              className={`flex-1 py-3 px-4 font-bold text-xs tracking-tighter transition-all active:scale-95 border ${
                selected === engine 
                  ? 'bg-surface-container-highest border-primary text-primary shadow-[0_0_10px_rgba(156,255,147,0.2)]' 
                  : 'bg-surface-container-high border-outline-variant text-on-surface-variant hover:border-secondary hover:text-secondary'
              } ${isAnalyzing ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              {engine}
            </button>
          ))}
        </div>
      </div>
      <div className="lg:col-span-8 flex justify-end">
        <button 
          onClick={() => onExecute(selected)}
          disabled={isAnalyzing}
          className="group relative w-full lg:w-auto overflow-hidden bg-primary text-on-primary font-headline font-black text-xl px-12 py-5 tracking-tighter hover:brightness-110 active:scale-[0.98] transition-all glow-primary disabled:opacity-70 disabled:cursor-not-allowed"
        >
          {/* pointer-events-none added to prevent blocking click */}
          <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.2)_0%,transparent_50%)] pointer-events-none"></div>
          <span className="relative flex items-center justify-center gap-3">
            {isAnalyzing ? (
              <>
                ANALYZING_MARKET
                <Loader2 className="animate-spin" size={24} />
              </>
            ) : (
              <>
                EXECUTE AI ANALYSIS
                <span className="material-symbols-outlined">bolt</span>
              </>
            )}
          </span>
        </button>
      </div>
    </section>
  )
}

export default EngineSelector