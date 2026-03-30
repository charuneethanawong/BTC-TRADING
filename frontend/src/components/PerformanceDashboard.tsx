import React from 'react'
import Chart from 'react-apexcharts'
import type { AIMarketResult } from '../types'
import { Target, TrendingUp, Gauge } from 'lucide-react'

const PerformanceDashboard: React.FC<{ data: AIMarketResult[] }> = ({ data }) => {
  const latestResult = data[data.length - 1];
  const totalCorrect = data.filter(r => r.correct).length;
  const accuracy = (totalCorrect / data.length) * 100;

  // Chart Config (Price Delta)
  const chartSeries = [
    {
      name: 'Price Change %',
      data: data.map(r => r.price_change_pct)
    }
  ];

  const chartOptions: ApexCharts.ApexOptions = {
    chart: { type: 'area', height: 150, toolbar: { show: false }, background: 'transparent' },
    colors: ['#9cff93'],
    fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.4, opacityTo: 0.05, stops: [0, 90, 100] } },
    dataLabels: { enabled: false },
    stroke: { curve: 'smooth', width: 2 },
    grid: { borderColor: '#171717', strokeDashArray: 4 },
    xaxis: { 
      categories: data.map(r => new Date(r.analysis_time).toLocaleTimeString()), 
      labels: { show: false },
      axisBorder: { show: false },
      axisTicks: { show: false }
    },
    yaxis: { labels: { style: { colors: '#ababab', fontSize: '10px' } } },
    tooltip: { theme: 'dark' }
  };

  return (
    <div className="flex flex-col h-full space-y-6 overflow-hidden">
      {/* Accuracy Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="p-4 bg-[#1f1f1f]/60 glass-module border-l-2 border-[#9cff93] flex items-center justify-between">
          <div className="flex flex-col">
            <span className="text-[10px] text-[#ababab] font-bold tracking-widest uppercase">OVERALL_ACCURACY</span>
            <span className="text-2xl font-black text-[#9cff93] tracking-tighter font-mono">{accuracy.toFixed(1)}%</span>
          </div>
          <Target className="w-8 h-8 text-[#9cff93]/20" />
        </div>
        
        <div className="p-4 bg-[#1f1f1f]/60 glass-module border-l-2 border-[#00e3fd] flex items-center justify-between">
          <div className="flex flex-col">
            <span className="text-[10px] text-[#ababab] font-bold tracking-widest uppercase">LATEST_CONFIDENCE</span>
            <span className="text-2xl font-black text-[#00e3fd] tracking-tighter font-mono">{latestResult?.ai_confidence}%</span>
          </div>
          <Gauge className="w-8 h-8 text-[#00e3fd]/20" />
        </div>

        <div className="p-4 bg-[#1f1f1f]/60 glass-module border-l-2 border-[#ffc15b] flex items-center justify-between">
          <div className="flex flex-col">
            <span className="text-[10px] text-[#ababab] font-bold tracking-widest uppercase">SAMPLES_EVALUATED</span>
            <span className="text-2xl font-black text-[#ffc15b] tracking-tighter font-mono">{data.length}</span>
          </div>
          <TrendingUp className="w-8 h-8 text-[#ffc15b]/20" />
        </div>
      </div>

      {/* Accuracy History List */}
      <div className="flex-1 flex flex-col md:flex-row space-y-6 md:space-y-0 md:space-x-6 min-h-0">
        <div className="flex-1 flex flex-col bg-[#000000] border border-[#171717]">
          <div className="p-3 bg-[#1f1f1f] text-[10px] text-[#ababab] font-bold flex items-center space-x-2">
            <TrendingUp className="w-4 h-4" />
            <span>AI_VS_MARKET_REALIZATION_LOG</span>
          </div>
          <div className="flex-1 overflow-auto custom-scrollbar">
             {data.slice().reverse().map((result, idx) => (
               <div key={idx} className="p-3 border-b border-[#171717] flex items-center justify-between">
                 <div className="flex flex-col">
                   <div className="flex items-center space-x-2">
                      <span className="text-[10px] font-bold">
                       {result.correct ? '[CORRECT]' : '[FAILED]'}
                     </span>
                     <span className="text-[10px] font-mono text-[#ababab]">@{new Date(result.analysis_time).toLocaleTimeString()}</span>
                   </div>
                   <div className="text-[11px] font-mono flex space-x-2 mt-1">
                     <span className="text-[#ababab]">AI:</span>
                     <span className="{result.ai_bias === 'BEARISH' ? 'text-[#FF0000]' : 'text-[#00e3fd]'}">{result.ai_bias}</span>
                     <span className="text-[#ababab] ml-2">ACTUAL:</span>
                     <span className="{result.actual_direction === 'BEARISH' ? 'text-[#FF0000]' : 'text-[#00e3fd]'}">{result.actual_direction}</span>
                   </div>
                 </div>
                 <div className="text-right flex flex-col items-end">
                    <div className="text-[12px] font-bold">
                     {result.price_change_pct > 0 ? '+' : ''}{result.price_change_pct}%
                   </div>
                   <span className="text-[9px] font-mono text-[#ababab]">P_DELTA</span>
                 </div>
               </div>
             ))}
          </div>
        </div>

        {/* Price Delta Chart */}
        <div className="w-full md:w-1/3 flex flex-col bg-[#1f1f1f]/20 glass-module border border-[#171717]">
           <div className="p-3 text-[10px] text-[#ababab] font-bold">MARKET_MOMENTUM_TREND (1H_DELTA)</div>
           <div className="flex-1 flex items-center justify-center pr-4">
             <Chart options={chartOptions} series={chartSeries} type="area" width="100%" height="200" />
           </div>
        </div>
      </div>
    </div>
  )
}

export default PerformanceDashboard
