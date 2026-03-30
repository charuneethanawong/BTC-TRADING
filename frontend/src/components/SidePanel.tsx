import React from 'react';
import type { DashboardState } from '../types/dashboard';
import { ToggleLeft, ToggleRight } from 'lucide-react';

export const SidePanel: React.FC<{ data: DashboardState; onToggle: () => Promise<void> }> = ({ data, onToggle }) => {
    const market = data.market || {};
    const layers = data.bias_layers || {};
    const flow = data.order_flow || {};
    
    // Technical Data Points (Bot-Native) - no hardcoded defaults, show "--" if missing
    const adx = (data as any).adx_h1;
    const plusDi = (data as any).plus_di;
    const minusDi = (data as any).minus_di;
    const diSpread = (data as any).di_spread;
    const botBias = market.ema_trend || 'NEUTRAL';
    const biasLevel = (data as any).bias_level;
    
    const m5State = (data as any).m5_state || 'RANGING';
    const m5ER = (data as any).m5_efficiency;
    
    const fundingPct = flow.funding_rate != null ? (flow.funding_rate * 100).toFixed(4) : '0.0000';
    const oiChangePct = flow.oi_change != null ? flow.oi_change.toFixed(3) : '0.000';

    return (
        <aside className="flex flex-col gap-4 min-w-[320px] animate-in fade-in slide-in-from-right-4 duration-500">
            
            {/* 1. STRUCTURAL CORE ENGINE (H1 Analysis) */}
            <section className="bg-surface-container-low border-l-4 border-primary overflow-hidden relative shadow-2xl">
                <div className="absolute top-0 right-0 p-2 opacity-5">
                    <span className="material-symbols-outlined text-5xl">database</span>
                </div>
                <div className="p-5">
                    <header className="flex justify-between items-center mb-5">
                        <h3 className="font-headline text-[12px] font-black tracking-[0.2em] text-primary uppercase flex items-center gap-2">
                            <span className="material-symbols-outlined text-primary text-base">terminal</span>
                            Structural_Core
                        </h3>
                        <span className="text-[9px] font-mono text-outline uppercase tracking-widest bg-surface-container-highest px-2 py-0.5">NATIVE</span>
                    </header>

                    <div className="bg-surface-container-lowest p-5 border border-outline-variant/10">
                        <p className="text-[11px] font-bold text-on-surface-variant uppercase tracking-widest mb-2 font-headline">H1_Structural_Trend</p>
                        <div className="flex justify-between items-end">
                            <span className={`text-3xl font-black tracking-tighter font-headline ${botBias === 'BULLISH' ? 'text-primary' : botBias === 'BEARISH' ? 'text-error' : 'text-tertiary'}`}>
                                {botBias}
                            </span>
                            <span className="text-[12px] font-mono text-on-surface-variant mb-1 font-black opacity-80">LVL: {biasLevel != null ? `0${biasLevel}` : '--'}</span>
                        </div>
                        <div className="flex gap-1.5 mt-4">
                            {[1, 2, 3, 4].map(lvl => (
                                <div key={lvl} className={`h-2 flex-1 ${biasLevel != null && lvl <= (biasLevel + 1) ? (botBias === 'BULLISH' ? 'bg-primary shadow-[0_0_12px_#9cff93]' : 'bg-error shadow-[0_0_12px_#ff7351]') : 'bg-surface-container-highest'}`}></div>
                            ))}
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-x-8 gap-y-6 mt-6">
                        <MetricBlock label="ADX_TREND" value={adx != null ? adx.toFixed(1) : '--'} color={adx != null && adx > 25 ? "text-secondary" : "text-on-surface"} />
                        <MetricBlock label="DI_SPREAD" value={diSpread != null ? diSpread.toFixed(1) : '--'} />
                        <MetricBlock label="PLUS_DI" value={plusDi != null ? plusDi.toFixed(1) : '--'} color="text-primary" />
                        <MetricBlock label="MINUS_DI" value={minusDi != null ? minusDi.toFixed(1) : '--'} color="text-error" />
                    </div>
                </div>
            </section>

            {/* 2. EXECUTION REGIME GATE (M5 Logic) */}
            <section className="bg-surface-container-low border-l-4 border-secondary overflow-hidden shadow-2xl">
                <div className="p-5">
                    <header className="flex justify-between items-center mb-5">
                        <h3 className="font-headline text-[12px] font-black tracking-[0.2em] text-secondary uppercase flex items-center gap-2">
                            <span className="material-symbols-outlined text-secondary text-base">bolt</span>
                            Execution_Gate
                        </h3>
                    </header>

                    <div className={`p-5 transition-all border-2 ${m5State === 'TRENDING' ? 'bg-primary/10 border-primary/30' : m5State === 'SIDEWAY' ? 'bg-error/10 border-error/30' : 'bg-surface-container-lowest border-outline-variant/20'}`}>
                        <div className="flex justify-between items-center mb-2">
                            <span className="text-[11px] font-bold text-on-surface-variant uppercase tracking-widest font-headline">M5 Market Regime</span>
                            <span className="material-symbols-outlined text-[16px] text-secondary">analytics</span>
                        </div>
                        <div className={`text-2xl font-black tracking-tight font-headline ${m5State === 'TRENDING' ? 'text-primary' : m5State === 'SIDEWAY' ? 'text-error' : 'text-on-surface'}`}>
                            {m5State}
                        </div>
                        <div className="mt-4 flex flex-col gap-2">
                            <div className="flex justify-between text-[10px] font-mono font-black uppercase text-on-surface-variant">
                                <span>Efficiency Ratio (ER)</span>
                                <span className="text-secondary">{m5ER != null ? m5ER.toFixed(3) : '--'}</span>
                            </div>
                            <div className="h-1.5 bg-surface-container-highest w-full overflow-hidden">
                                <div className="h-full bg-secondary shadow-[0_0_10px_#00e3fd]" style={{ width: `${m5ER != null ? Math.min(m5ER * 100, 100) : 0}%` }}></div>
                            </div>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-x-8 gap-y-6 mt-6">
                        <MetricBlock label="DELTA_EFF" value={flow.der != null ? flow.der.toFixed(3) : '--'} color="text-secondary" />
                        <MetricBlock label="VOL_RATIO" value={((flow as any).volume_ratio_m5 != null ? ((flow as any).volume_ratio_m5).toFixed(2) : '--')} />
                        <MetricBlock label="OI_CHANGE" value={flow.oi_change != null ? `${oiChangePct}%` : '--'} color={flow.oi_change != null && flow.oi_change >= 0 ? 'text-primary' : 'text-error'} />
                        <MetricBlock label="FUNDING" value={flow.funding_rate != null ? `${fundingPct}%` : '--'} color="text-tertiary" />
                    </div>
                </div>
            </section>

            <section className="bg-surface-container-low p-5 border border-outline-variant/15">
                <h3 className="font-headline text-[11px] font-black tracking-[0.2em] text-on-surface-variant uppercase mb-4 px-1">Bias_Layer_Matrix</h3>
                <div className="grid grid-cols-3 gap-2">
                    <LayerTag label="LC" value={layers.lc} />
                    <LayerTag label={layers.lr_count ? `LR(${layers.lr_count})` : 'LR'} value={layers.lr} />
                    <LayerTag label="L0" value={layers.l0} />
                    <LayerTag label="L1" value={layers.l1} />
                    <LayerTag label="L2" value={layers.l2} />
                    <LayerTag label="L3" value={layers.l3} />
                </div>
            </section>

            {/* 4. SYSTEM OVERRIDE */}
            <section className="mt-auto bg-surface-container-lowest p-5 border-t border-outline-variant/20 flex justify-between items-center group hover:bg-surface-container-low transition-all">
                <div className="flex flex-col">
                    <span className="text-[12px] text-on-surface font-black uppercase tracking-[0.15em] font-headline group-hover:text-primary transition-colors">Neural_Override</span>
                    <span className="text-[9px] font-mono text-outline font-bold mt-1 opacity-60 uppercase">SYSTEM_NODE_V4.2</span>
                </div>
                <button onClick={onToggle} className="active:scale-90 transition-all outline-none p-1">
                    {data.ai.enabled ? 
                        <ToggleRight className="text-primary drop-shadow-[0_0_10px_rgba(156,255,147,0.6)]" size={36} /> : 
                        <ToggleLeft className="text-outline-variant" size={36} />
                    }
                </button>
            </section>
        </aside>
    );
};

const MetricBlock = ({ label, value, color = "text-on-surface" }: any) => (
    <div className="flex flex-col border-l-2 border-outline-variant/20 pl-3">
        <span className="text-[10px] text-on-surface-variant font-black uppercase tracking-widest mb-1 font-headline">{label}</span>
        <span className={`text-[18px] font-black font-mono tracking-tighter leading-none ${color}`}>{value || '---'}</span>
    </div>
);

const LayerTag = ({ label, value }: any) => {
    const isBull = value === 'BULLISH';
    const isBear = value === 'BEARISH';
    const accentColor = isBull ? 'border-primary' : isBear ? 'border-error' : 'border-outline-variant';
    const bgColor = isBull ? 'bg-primary/10' : isBear ? 'bg-error/10' : 'bg-surface-container-highest';
    const textColor = isBull ? 'text-primary' : isBear ? 'text-error' : 'text-on-surface-variant';

    return (
        <div className={`py-3 border-b-4 text-center transition-all ${accentColor} ${bgColor}`}>
            <div className="text-[10px] font-black uppercase tracking-widest opacity-70 mb-1 font-headline">{label}</div>
            <div className={`text-[13px] font-black tracking-tighter font-mono ${textColor}`}>{value?.slice(0, 4) || 'NEUT'}</div>
        </div>
    );
};
