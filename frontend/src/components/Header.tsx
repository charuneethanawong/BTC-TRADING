import React from 'react';
import type { DashboardState } from '../types/dashboard';

export const Header: React.FC<{ data: DashboardState }> = ({ data }) => {
    return (
        <header
            className="bg-[#0e0e0e] flex justify-between items-center w-full fixed top-0 z-50"
            style={{ padding: '12px 24px', borderBottom: '1px solid rgba(72,72,72,0.15)' }}
        >
            <div className="flex items-center" style={{ gap: '32px' }}>
                <span style={{ color: '#00FF41', fontWeight: 900, letterSpacing: '-0.05em', fontSize: '20px', fontStyle: 'italic' }}>
                    BTC SNIPER SF
                </span>
                <span style={{ color: '#00FF41', borderBottom: '2px solid #00FF41', paddingBottom: '4px', fontFamily: "'Space Grotesk'", textTransform: 'uppercase', letterSpacing: '-0.05em', fontSize: '14px', fontWeight: 700 }}>LIVE_FEED</span>
            </div>

            <div className="flex items-center" style={{ gap: '24px' }}>
                <div className="hidden lg:flex items-center" style={{ gap: '16px', fontSize: '10px', letterSpacing: '0.2em', color: '#ababab', textTransform: 'uppercase' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                        <span style={{ color: '#00fc40' }}>BTC PRICE</span>
                        <span style={{ color: '#fff', fontWeight: 700, fontSize: '14px' }}>{data.price.toLocaleString(undefined, { minimumFractionDigits: 1 })}</span>
                    </div>
                    <div style={{ width: '1px', height: '32px', background: 'rgba(72,72,72,0.3)' }}></div>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                        <span style={{ color: '#00e3fd' }}>SESSION</span>
                        <span style={{ color: '#fff', fontWeight: 700, fontSize: '14px' }}>{data.session || 'NY'}</span>
                    </div>
                    <div style={{ width: '1px', height: '32px', background: 'rgba(72,72,72,0.3)' }}></div>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                        <span style={{ color: '#ff7351' }}>REGIME</span>
                        <span style={{ color: '#fff', fontWeight: 700, fontSize: '14px' }}>{data.regime || 'VOLATILE'}</span>
                    </div>
                </div>
            </div>
        </header>
    );
};
