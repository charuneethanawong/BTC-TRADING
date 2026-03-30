import { useState, useEffect } from 'react';
import type { DashboardState } from '../types/dashboard';

interface TradeRecord {
    timestamp: string;
    mode: string;
    direction: string;
    ai_bias: string;
    status: string;
    pnl?: number;
    signal_id?: string;
}

export const useDashboard = () => {
    const [data, setData] = useState<DashboardState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [refreshTrigger, setRefreshTrigger] = useState(0);
    const [tradeHistory, setTradeHistory] = useState<TradeRecord[]>([]);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [aiInsight, setAiInsight] = useState<string | null>(null);

    const fetchData = async () => {
        try {
            const response = await fetch('http://localhost:8000/api/dashboard');
            if (!response.ok) throw new Error('API unreachable');
            const result = await response.json();
            setData(result);
            setError(null);
            setRefreshTrigger(prev => prev + 1);
        } catch (err) {
            setError('Connection Lost - Retrying...');
            console.error('Fetch error:', err);
        }
    };

    const fetchTradeHistory = async () => {
        try {
            const response = await fetch('http://localhost:8000/api/trades/log?limit=200&exclude_skipped=true');
            if (response.ok) {
                const result = await response.json();
                setTradeHistory(result.trades || []);
            }
        } catch (err) {
            console.error('Trade history fetch error:', err);
        }
    };

    useEffect(() => {
        fetchData();
        fetchTradeHistory();
        const interval = setInterval(fetchData, 5000);
        const historyInterval = setInterval(fetchTradeHistory, 30000);
        return () => {
            clearInterval(interval);
            clearInterval(historyInterval);
        };
    }, []);

    const toggleAI = async () => {
        try {
            const response = await fetch('http://localhost:8000/api/ai/toggle', { method: 'POST' });
            const result = await response.json();
            if (data) {
                setData({
                    ...data,
                    ai: { ...data.ai, enabled: result.enabled }
                });
            }
        } catch (err) {
            console.error('Toggle error:', err);
        }
    };

    // v29.1: Manual AI Analysis Trigger with Insight Storage
    const runAIAnalysis = async (engine: string) => {
        setIsAnalyzing(true);
        try {
            const response = await fetch(`http://localhost:8000/api/ai/analyze-trades?model=${engine.toLowerCase()}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ trades: tradeHistory || [] }),
            });
            if (!response.ok) throw new Error(`API error: ${response.status}`);
            const result = await response.json();
            setAiInsight(result.insight || 'No response from AI.');
            await fetchData();
        } catch (err) {
            console.error('Analysis error:', err);
            setAiInsight(`Analysis failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
        } finally {
            setIsAnalyzing(false);
        }
    };

    const clearTradeLog = async () => {
        try {
            const response = await fetch('http://localhost:8000/api/trades/clear', { method: 'POST' });
            if (response.ok) {
                setTradeHistory([]);
                await fetchData();
            }
        } catch (err) {
            console.error('Clear error:', err);
        }
    };

    return { data, error, toggleAI, refreshTrigger, tradeHistory, clearTradeLog, runAIAnalysis, isAnalyzing, aiInsight };
};