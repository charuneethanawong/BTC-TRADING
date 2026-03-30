import React from 'react'

interface AlignmentData {
  alignedWin: number;
  alignedLoss: number;
  conflictWin: number;
  conflictLoss: number;
  alignedWinPnl: string;
  alignedLossPnl: string;
  conflictWinPnl: string;
  conflictLossPnl: string;
  alignedWr: string;
  conflictWr: string;
}

interface AgreementMatrixProps {
  alignment: AlignmentData;
}

const AgreementMatrix: React.FC<AgreementMatrixProps> = ({ alignment }) => {
  return (
    <section className="bg-surface-container-low border border-outline-variant/10 p-6 h-full font-headline">
      <h2 className="text-sm font-bold tracking-widest text-on-surface-variant uppercase mb-6 flex items-center gap-2">
        <span className="material-symbols-outlined text-primary text-lg">sync_alt</span>
        AI vs Bot Agreement Matrix
      </h2>
      <div className="grid grid-cols-2 gap-4">
        <MatrixCard title="Aligned + WIN" value={alignment.alignedWin} pnl={alignment.alignedWinPnl} color="primary" />
        <MatrixCard title="Aligned + LOSS" value={alignment.alignedLoss} pnl={alignment.alignedLossPnl} color="error" />
        <MatrixCard title="Conflict + WIN" value={alignment.conflictWin} pnl={alignment.conflictWinPnl} color="secondary" />
        <MatrixCard title="Conflict + LOSS" value={alignment.conflictLoss} pnl={alignment.conflictLossPnl} color="on-surface-variant" />
      </div>
      <div className="mt-6 p-3 bg-surface-container-lowest border border-outline-variant/20 flex justify-between items-center font-mono">
        <span className="text-[10px] font-black uppercase text-primary">Aligned WR: {alignment.alignedWr}%</span>
        <span className="text-[10px] font-black uppercase text-secondary">Conflict WR: {alignment.conflictWr}%</span>
      </div>
    </section>
  )
}

const MatrixCard: React.FC<{ title: string, value: number, pnl: string, color: string }> = ({ title, value, pnl, color }) => {
  const borderColor = color === 'primary' ? 'border-primary' : color === 'error' ? 'border-error' : color === 'secondary' ? 'border-secondary' : 'border-outline-variant';
  const textColor = color === 'primary' ? 'text-primary' : color === 'error' ? 'text-error' : color === 'secondary' ? 'text-secondary' : 'text-on-surface';
  
  return (
    <div className={`bg-surface-container-lowest p-4 border-l-2 ${borderColor}`}>
      <p className="text-[9px] uppercase font-black text-on-surface-variant mb-1">{title}</p>
      <div className="flex items-baseline gap-2 font-mono">
        <span className={`text-2xl font-black ${textColor}`}>{value}</span>
        <span className="text-[10px] font-bold opacity-40">{pnl}</span>
      </div>
    </div>
  )
}

export default AgreementMatrix