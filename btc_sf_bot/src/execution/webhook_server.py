"""
Webhook Server Module
"""
from typing import Dict, Optional, Callable
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import asyncio
import threading
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# v13.8: Defensive .env loading - ensure env vars are loaded before use
env_path = Path(__file__).parent.parent.parent / "config" / ".env"
load_dotenv(dotenv_path=env_path)

from ..utils.logger import get_logger

import logging

logger = get_logger(__name__)

# v24.2: Log Filter to mute repetitive dashboard polling
class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Suppress logs for /api/dashboard and /api/trades/log 200 OK
        msg = record.getMessage()
        if "GET /api/dashboard" in msg or "GET /api/trades/log" in msg:
            return False
        return True

# Apply filter to uvicorn access logger
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())


# FastAPI app
app = FastAPI(title="BTC SF Bot Webhook Server")

# v24.0: CORS for Frontend Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# v24.1: Static files for production build (if dist exists)
dist_path = Path(__file__).parent.parent.parent / "frontend" / "dist"
if dist_path.exists():
    app.mount("/dashboard", StaticFiles(directory=str(dist_path), html=True), name="static")
    logger.info(f"Mounted frontend dist at /dashboard from {dist_path}")

API_KEY = os.getenv("WEBHOOK_API_KEY", "your-secret-key-12345")


async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verify X-API-KEY header."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return x_api_key


# Request/Response Models
class SignalRequest(BaseModel):
    action: str  # BUY or SELL
    symbol: str
    entry: float
    sl: float
    tp1: float
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    lot_size: float
    reason: str
    confidence: int
    timestamp: str


class SignalResponse(BaseModel):
    status: str
    message: str
    order_id: Optional[str] = None
    timestamp: str


class ConfirmationRequest(BaseModel):
    # v26.1: Changed from order_id to signal_id for field alignment
    signal_id: str
    status: str
    price: Optional[float] = None
    profit: Optional[float] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None
    timestamp: str


# Global state
signal_callback: Optional[Callable] = None
confirmation_callback: Optional[Callable] = None
last_signal: Optional[Dict] = None

# v24.0: Shared Dashboard State (Bot -> Dashboard)
dashboard_state: Dict = {
    'price': 0,
    'session': '',
    'regime': '',
    'timestamp': '',

    # AI
    'ai': {
        'enabled': True,
        'bias': 'NEUTRAL',
        'confidence': 0,
        'action': 'WAIT',
        'reason': '',
        'key_level': 0,
        'last_update': '',
    },

    # Market Context
    'market': {
        'ema9': 0, 'ema20': 0, 'ema50': 0,
        'ema_trend': 'MIXED',
        'h1_dist_pct': 0,
        'pullback_status': 'NONE',
        'wall_info': '',
    },

    # Gate 1 Bias Layers
    'bias_layers': {
        'lc': 'NEUTRAL',     # H1 Candle Bias
        'lr': 'NEUTRAL',     # Early Reversal
        'lr_count': 0,       # LR confirmations (0-4)
        'l0': 'NEUTRAL',     # H1 Structure
        'l1': 'NEUTRAL',     # M5 Break Swing
        'l2': 'NEUTRAL',     # EMA9/20
        'l3': 'NEUTRAL',     # EMA20/50
    },

    # Mode Results (4 modes)
    'modes': {
        'IPA':  {'active': False, 'score': 0, 'threshold': 10, 'direction': '', 'signal_sent': False, 'breakdown': {}},
        'IOF':  {'active': False, 'score': 0, 'threshold': 6,  'direction': '', 'signal_sent': False, 'breakdown': {}},
        'IPAF': {'active': False, 'score': 0, 'threshold': 10, 'direction': '', 'signal_sent': False, 'breakdown': {}},
        'IOFF': {'active': False, 'score': 0, 'threshold': 6,  'direction': '', 'signal_sent': False, 'breakdown': {}},
    },

    # Last Signal
    'last_signal': {
        'signal_id': '',
        'mode': '',
        'direction': '',
        'entry_price': 0,
        'stop_loss': 0,
        'take_profit': 0,
        'score': 0,
        'rr': 0,
        'time': '',
    },

    # MLVP (Volume Profile)
    'mlvp': {
        'composite_poc': 0,
        'composite_vah': 0,
        'composite_val': 0,
        'current_session': '',
        'confluence_zones': [],
    },

    # v25.0: Order Flow Data
    'order_flow': {
        'delta': 0,              # Cumulative Delta
        'volume_24h': 0,         # 24h Volume
        'oi': 0,                 # Open Interest
        'oi_change': 0,          # OI Change %
        'liquidations': 0,       # Recent liquidations
        'der': 0,                # Delta Efficiency Ratio
        'funding_rate': 0,       # Funding Rate
    },

    # AI Trade Stats
    'ai_stats': {
        'total': 0,
        'wins': 0,
        'losses': 0,
        'win_rate': 0,
        'skipped': 0,
        'opened': 0,
    },

    # Account (from EA ZMQ)
    'account': {
        'balance': 0,
        'equity': 0,
        'profit': 0,
        'leverage': 0,           # v25.0: from config
        'drawdown_pct': 0,       # v25.0: current drawdown %
    },

    # Positions (from EA ZMQ)
    'positions': [],

    # v25.0: Price history for chart (last 50 M5 candles close prices)
    'price_history': [],

    # Cycle Info
    'cycle_time': 0,
    'cycle_count': 0,
    'bot_uptime': '',
}


def _get_signal_file_path() -> str:
    """Get signal file path from environment variable."""
    signal_file = os.getenv("MT5_SIGNAL_FILE", "")
    if not signal_file:
        signal_file = "signal.json"
        logger.warning("MT5_SIGNAL_FILE not set, using default: signal.json")
    return signal_file


def _ensure_signal_dir():
    """Ensure the signal file directory exists."""
    signal_file = _get_signal_file_path()
    signal_dir = os.path.dirname(signal_file)
    if signal_dir and not os.path.exists(signal_dir):
        try:
            os.makedirs(signal_dir, exist_ok=True)
            logger.info(f"Created signal directory: {signal_dir}")
        except Exception as e:
            logger.error(f"Could not create signal directory: {e}")


def _save_signal_to_file(signal: Optional[Dict]):
    """Write signal to JSON file for MT5 using Atomic Write."""
    if signal is None:
        return
    try:
        _ensure_signal_dir()
        signal_file = _get_signal_file_path()
        target_path = Path(signal_file)
        
        # Atomic Write: Write to temp file first, then replace
        temp_path = target_path.with_suffix('.json.tmp')
        
        with open(temp_path, 'w') as f:
            json.dump(signal, f, indent=4)
            
        # Atomically replace the target file
        os.replace(temp_path, target_path)
        logger.info(f"Saved signal atomically to: {signal_file}")
    except Exception as e:
        logger.error(f"Error writing signal file: {e}")


def _save_indicators_to_file(indicators: Optional[Dict]):
    """Write indicators to separate JSON file for dashboard (NOT signal.json)."""
    if indicators is None:
        return
    try:
        signal_file = _get_signal_file_path()
        signal_dir = os.path.dirname(signal_file)
        if signal_dir:
            os.makedirs(signal_dir, exist_ok=True)
        
        # Save to indicators.json (separate file)
        indicators_file = os.path.join(signal_dir, "indicators.json")
        target_path = Path(indicators_file)
        
        # Atomic Write
        temp_path = target_path.with_suffix('.json.tmp')
        
        # Convert any Timestamp or non-serializable objects to strings
        import pandas as pd
        def convert_to_serializable(obj):
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {k: convert_to_serializable(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_to_serializable(i) for i in obj]
            elif hasattr(obj, '__dict__'):
                return str(obj)
            return obj
        
        indicators_clean = convert_to_serializable(indicators)
        
        with open(temp_path, 'w') as f:
            json.dump(indicators_clean, f, indent=4)
            
        os.replace(temp_path, target_path)
        logger.debug(f"Saved indicators to: {indicators_file}")
    except Exception as e:
        logger.error(f"Error writing indicators file: {e}")


# Routes
@app.get("/")
async def root():
    """Health check."""
    return {
        "status": "online",
        "service": "BTC SF Bot Webhook Server",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/health")
async def health():
    """Detailed health check."""
    return {
        "status": "healthy",
        "last_signal": last_signal.get("timestamp") if last_signal else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/webhook", response_model=SignalResponse)
async def receive_signal(request: SignalRequest, key: str = Depends(verify_api_key)):
    """
    Receive signal from Python analysis and forward to MT5.
    """
    global last_signal
    
    try:
        signal_data = request.dict()
        logger.info(f"Received signal: {signal_data['action']} {signal_data['symbol']} @ {signal_data['entry']}")
        
        last_signal = signal_data
        _save_signal_to_file(last_signal)
        
        # Call the callback if registered
        if signal_callback:
            result = await signal_callback(signal_data)
            return SignalResponse(
                status="success",
                message="Signal processed",
                order_id=result.get("order_id") if result else None,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
        
        return SignalResponse(
            status="success",
            message="Signal received, waiting for MT5",
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
    except Exception as e:
        logger.error(f"Error processing signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/confirm", response_model=SignalResponse)
async def receive_confirmation(request: ConfirmationRequest, key: str = Depends(verify_api_key)):
    """
    Receive confirmation from MT5.
    """
    try:
        confirmation_data = request.dict()
        logger.info(f"Received confirmation: {confirmation_data['order_id']} - {confirmation_data['status']}")
        
        # Call the callback if registered
        if confirmation_callback:
            await confirmation_callback(confirmation_data)
        
        return SignalResponse(
            status="success",
            message="Confirmation received",
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
    except Exception as e:
        logger.error(f"Error processing confirmation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/last-signal")
async def get_last_signal():
    """Get last signal for MT5 polling."""
    if last_signal is None:
        return {"action": "", "entry": 0, "sl": 0, "tp": 0}
    
    # Standardize field names for MT5
    action = last_signal.get("action", last_signal.get("direction", ""))
    entry = last_signal.get("entry", last_signal.get("entry_price", 0))
    sl = last_signal.get("sl", last_signal.get("stop_loss", 0))
    tp = last_signal.get("tp", last_signal.get("take_profit", 0))
    
    return {
        "action": action,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "tp1": last_signal.get("tp1", tp),
        "tp2": last_signal.get("tp2", tp),
        "tp3": last_signal.get("tp3", tp),
        "confidence": last_signal.get("confidence", 0),
        "reason": last_signal.get("reason", ""),
        "short_reason": last_signal.get("short_reason", ""),
        "timestamp": last_signal.get("timestamp", datetime.now(timezone.utc).isoformat())
    }


# v24.0: Dashboard Endpoints
@app.get("/api/dashboard")
async def get_dashboard():
    """Frontend poll every 5 seconds - get all data in 1 call."""
    return dashboard_state


@app.get("/api/ai/status")
async def get_ai_status():
    """AI Status (enabled/disabled + stats)."""
    return {
        'enabled': dashboard_state['ai']['enabled'],
        'stats': dashboard_state['ai_stats'],
        'last_analysis': dashboard_state['ai'],
    }


@app.get("/api/trades/log")
async def get_trade_log(limit: int = 50, exclude_skipped: bool = False):
    """Get latest N records from ai_trade_log.jsonl.
    exclude_skipped=true: filter out EA_SKIPPED for analysis page
    """
    import json
    from pathlib import Path

    # v31.0: Fix path - bot writes to btc_sf_bot/data/, not src/data/
    log_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'ai_trade_log.jsonl'

    if not log_path.exists():
        return {'trades': []}

    records = []
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        r = json.loads(line.strip())
                        if exclude_skipped and r.get('status') == 'EA_SKIPPED':
                            continue
                        records.append(r)
                    except:
                        pass
    except Exception as e:
        logger.error(f"Error reading trade log: {e}")

    return {'trades': records[-limit:]}


@app.get("/api/positions")
async def get_positions():
    """Active positions (from EA ZMQ via dashboard_state)."""
    return {'positions': dashboard_state.get('positions', [])}


@app.post("/api/ai/toggle")
async def toggle_ai():
    """Toggle AI enabled/disabled."""
    current = dashboard_state['ai']['enabled']
    dashboard_state['ai']['enabled'] = not current
    new_state = dashboard_state['ai']['enabled']
    logger.info(f"🤖 AI Toggle via Dashboard: {'ENABLED' if new_state else 'DISABLED'}")
    return {'enabled': new_state, 'message': f'AI {"enabled" if new_state else "disabled"}'}


@app.post("/api/trades/clear")
async def clear_trade_log():
    """v25.0: Clear ai_trade_log.jsonl — reset trade history."""
    from pathlib import Path
    log_path = Path(__file__).parent.parent / "data" / "ai_trade_log.jsonl"
    try:
        with open(log_path, 'w') as f:
            f.write('')
        # Reset ai_stats in dashboard
        dashboard_state['ai_stats'] = {
            'total': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0, 'skipped': 0, 'opened': 0,
        }
        logger.info("🗑️ Trade log cleared via Dashboard")
        return {'status': 'ok', 'message': 'Trade log cleared'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


@app.post("/api/ai/analyze-trades")
async def ai_analyze_trades(model: str = "deepseek"):
    """v26.0: AI analyzes trade log. model=deepseek (API) or claude (local CLI)."""
    import json, os
    from pathlib import Path
    from collections import Counter

    log_path = Path(__file__).parent.parent / "data" / "ai_trade_log.jsonl"
    if not log_path.exists():
        return {'insight': 'No trade data available.'}

    # Read trades
    records = []
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        records.append(json.loads(line.strip()))
                    except:
                        pass
    except:
        return {'insight': 'Error reading trade log.'}

    closed = [r for r in records if r.get('status') in ('WIN', 'LOSS')]
    if len(closed) < 3:
        return {'insight': f'Not enough closed trades ({len(closed)}). Need at least 3.'}

    # Build summary for AI
    wins = [r for r in closed if r['status'] == 'WIN']
    losses = [r for r in closed if r['status'] == 'LOSS']
    total_pnl = sum(r.get('pnl', 0) or 0 for r in closed)
    win_rate = len(wins) / len(closed) * 100

    # Per mode
    mode_stats = {}
    for r in closed:
        m = r.get('mode', '?')
        if m not in mode_stats:
            mode_stats[m] = {'w': 0, 'l': 0, 'pnl': 0}
        mode_stats[m]['pnl'] += r.get('pnl', 0) or 0
        if r['status'] == 'WIN':
            mode_stats[m]['w'] += 1
        else:
            mode_stats[m]['l'] += 1

    # AI aligned vs conflict
    aligned_wins = sum(1 for r in wins if r.get('ai_aligned') == True)
    aligned_losses = sum(1 for r in losses if r.get('ai_aligned') == True)
    conflict_wins = sum(1 for r in wins if r.get('ai_aligned') == False)
    conflict_losses = sum(1 for r in losses if r.get('ai_aligned') == False)

    # Session stats
    session_stats = {}
    for r in closed:
        h = 0
        try:
            h = int(r.get('timestamp', '')[11:13])
        except:
            pass
        s = 'ASIA' if 1 <= h < 9 else 'LONDON' if 7 <= h < 16 else 'NY' if 13 <= h < 22 else 'ASIA'
        if s not in session_stats:
            session_stats[s] = {'w': 0, 'l': 0, 'pnl': 0}
        session_stats[s]['pnl'] += r.get('pnl', 0) or 0
        if r['status'] == 'WIN':
            session_stats[s]['w'] += 1
        else:
            session_stats[s]['l'] += 1

    # BE trades (WIN but tiny PnL)
    be_trades = [r for r in wins if abs(r.get('pnl', 0) or 0) < 1.0]

    # Per signal type
    type_stats = {}
    for r in closed:
        st = r.get('signal_type', '?')
        if st not in type_stats:
            type_stats[st] = {'w': 0, 'l': 0, 'pnl': 0}
        type_stats[st]['pnl'] += r.get('pnl', 0) or 0
        if r['status'] == 'WIN':
            type_stats[st]['w'] += 1
        else:
            type_stats[st]['l'] += 1

    # SL/TP stats
    avg_win_pnl = sum(r.get('pnl', 0) or 0 for r in wins) / max(len(wins), 1)
    avg_loss_pnl = sum(r.get('pnl', 0) or 0 for r in losses) / max(len(losses), 1)
    real_tp_wins = [r for r in wins if abs(r.get('pnl', 0) or 0) >= 1.0]
    profit_factor = abs(sum(r.get('pnl', 0) or 0 for r in wins)) / abs(sum(r.get('pnl', 0) or 0 for r in losses)) if losses else 999

    # Build prompt
    summary = f"""Trade Log Analysis (BTC M5 Scalping Bot):
Total: {len(closed)} trades | {len(wins)}W {len(losses)}L | WR: {win_rate:.1f}% | PnL: ${total_pnl:.2f}
Profit Factor: {profit_factor:.2f} | Avg Win: ${avg_win_pnl:.2f} | Avg Loss: ${avg_loss_pnl:.2f}

BE Analysis:
  BE exits (WIN <$1): {len(be_trades)}/{len(wins)} wins ({len(be_trades)/max(len(wins),1)*100:.0f}%)
  Real TP wins (>=$1): {len(real_tp_wins)}/{len(wins)} ({len(real_tp_wins)/max(len(wins),1)*100:.0f}%)
  Avg BE PnL: ${sum(r.get('pnl',0) or 0 for r in be_trades)/max(len(be_trades),1):.2f}
  Avg Real TP PnL: ${sum(r.get('pnl',0) or 0 for r in real_tp_wins)/max(len(real_tp_wins),1):.2f}

Per Mode:
{chr(10).join(f"  {m}: {s['w']}W {s['l']}L WR:{s['w']/max(s['w']+s['l'],1)*100:.0f}% PnL:${s['pnl']:.2f}" for m, s in mode_stats.items())}

Per Signal Type:
{chr(10).join(f"  {st}: {s['w']}W {s['l']}L WR:{s['w']/max(s['w']+s['l'],1)*100:.0f}% PnL:${s['pnl']:.2f}" for st, s in type_stats.items())}

AI Alignment:
  Aligned+WIN: {aligned_wins} | Aligned+LOSS: {aligned_losses} | Aligned WR: {aligned_wins/max(aligned_wins+aligned_losses,1)*100:.0f}%
  Conflict+WIN: {conflict_wins} | Conflict+LOSS: {conflict_losses} | Conflict WR: {conflict_wins/max(conflict_wins+conflict_losses,1)*100:.0f}%

Per Session:
{chr(10).join(f"  {s}: {st['w']}W {st['l']}L WR:{st['w']/max(st['w']+st['l'],1)*100:.0f}% PnL:${st['pnl']:.2f}" for s, st in session_stats.items())}

Last 5 LOSS trades:
{chr(10).join(f"  {r.get('signal_id')} | {r.get('signal_type')} | PnL:${r.get('pnl',0):.2f} | AI:{r.get('ai_bias')} vs Bot:{r.get('direction')} | exit:{r.get('exit_reason')}" for r in losses[-5:])}

Last 5 BE trades (WIN <$1):
{chr(10).join(f"  {r.get('signal_id')} | PnL:${r.get('pnl',0):.2f} | SL_dist:${abs(r.get('entry_price',0)-r.get('stop_loss',0)):.0f} | TP_dist:${abs(r.get('take_profit',0)-r.get('entry_price',0)):.0f}" for r in be_trades[-5:])}
"""

    # Call Gemini via google-genai SDK (free tier)
    gemini_key = os.getenv('GEMINI_API_KEY', '')
    analyst_system_prompt = """You are an institutional-grade BTC M5 scalping analyst.
Analyze the trade log with forensic precision.

RULES:
- Zero bias: report 100% truth, no embellishment, no downplaying
- Numbers speak: use statistics (%, win rate, avg RR, drawdown), not feelings
- Separate fact vs opinion: present raw evidence first, then recommendations
- Find root cause: don't just say what's wrong, explain WHY

OUTPUT FORMAT:
1. EXECUTIVE SUMMARY (3-5 lines)
2. PERFORMANCE BREAKDOWN (by mode, session, signal type — with numbers)
3. AI vs BOT DIVERGENCE (every instance where AI bias and bot direction disagreed)
4. PATTERN ANALYSIS (recurring loss patterns, time clusters, regime mismatches)
5. IMPROVEMENT RECOMMENDATIONS (ranked by impact, highest first — max 5)

Be brutally honest. Data-driven only. No filler.
"""
    prompt = analyst_system_prompt + summary

    # Save prompt file (always)
    from pathlib import Path as P
    summary_path = P(__file__).parent.parent / "data" / "trade_analysis_prompt.md"
    analysis_prompt = analyst_system_prompt
    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(analysis_prompt + summary)
    except:
        pass

    # === CLAUDE: Use local Claude Code CLI (Claude Max — no API cost) ===
    if model == 'claude':
        import subprocess, asyncio, shutil
        try:
            prompt_text = analysis_prompt + summary
            claude_cmd = shutil.which('claude') or 'claude'
            proc = await asyncio.create_subprocess_shell(
                f'"{claude_cmd}" -p "{summary_path}"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            result = stdout.decode('utf-8', errors='replace').strip()
            if result:
                return {'insight': result}
            else:
                err = stderr.decode('utf-8', errors='replace').strip()
                return {'insight': f'[Claude CLI returned empty]\n{err}\n\nFallback — raw stats:\n{summary}'}
        except asyncio.TimeoutError:
            return {'insight': f'[Claude CLI timeout (120s)]\n\nRaw stats:\n{summary}'}
        except FileNotFoundError:
            return {'insight': f'[Claude CLI not found — install Claude Code first]\n\nRaw stats:\n{summary}'}
        except Exception as e:
            return {'insight': f'[Claude CLI error: {e}]\n\nRaw stats:\n{summary}'}

    # === GEMINI: Use local Gemini CLI (free) ===
    if model == 'gemini':
        import subprocess, asyncio, shutil
        try:
            gemini_cmd = shutil.which('gemini') or 'gemini'
            proc = await asyncio.create_subprocess_shell(
                f'"{gemini_cmd}" -p "{summary_path}"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            result = stdout.decode('utf-8', errors='replace').strip()
            if result:
                return {'insight': result}
            else:
                err = stderr.decode('utf-8', errors='replace').strip()
                return {'insight': f'[Gemini CLI returned empty]\n{err}\n\nRaw stats:\n{summary}'}
        except asyncio.TimeoutError:
            return {'insight': f'[Gemini CLI timeout (120s)]\n\nRaw stats:\n{summary}'}
        except FileNotFoundError:
            return {'insight': f'[Gemini CLI not found — install: npm i -g @anthropic/gemini]\n\nRaw stats:\n{summary}'}
        except Exception as e:
            return {'insight': f'[Gemini CLI error: {e}]\n\nRaw stats:\n{summary}'}

    # === DEEPSEEK: Use OpenRouter API ===
    openrouter_key = os.getenv('OPENROUTER_API_KEY', '')
    if openrouter_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1")
            response = await client.chat.completions.create(
                model="deepseek/deepseek-chat",
                messages=[
                    {"role": "system", "content": analysis_prompt},
                    {"role": "user", "content": summary}
                ],
                max_tokens=1500, temperature=0.2,
            )
            return {'insight': response.choices[0].message.content}
        except Exception as e:
            return {'insight': f'[DeepSeek API error: {e}]\n\nRaw stats:\n{summary}'}

    return {'insight': f'[No API key]\n\nRaw stats:\n{summary}'}


# Server functions
def set_signal_callback(callback: Callable):
    """Set callback for incoming signals."""
    global signal_callback
    signal_callback = callback


def set_confirmation_callback(callback: Callable):
    """Set callback for confirmations."""
    global confirmation_callback
    confirmation_callback = callback


def start_server(host: str = "0.0.0.0", port: int = 8000, log_level: str = "info"):
    """
    Start the webhook server.
    
    Args:
        host: Host to bind to
        port: Port to listen on
        log_level: Logging level
    """
    logger.info(f"Starting webhook server on {host}:{port}")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level
    )


def start_server_background(host: str = "0.0.0.0", port: int = 8000, log_level: str = "info"):
    """
    Start the webhook server in background thread.
    
    Args:
        host: Host to bind to
        port: Port to listen on
        log_level: Logging level
    """
    def run():
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=log_level
        )
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    
    # v24.1: Auto-start Frontend in Dev mode
    start_frontend_background()
    
    logger.info(f"Webhook server started in background on {host}:{port}")
    return thread


def start_frontend_background():
    """v24.1: Start Vite dev server for the frontend in a background process."""
    import subprocess
    import platform
    
    frontend_dir = Path(__file__).parent.parent.parent / "frontend"
    if not frontend_dir.exists():
        logger.warning(f"Frontend directory not found at {frontend_dir}")
        return

    logger.info(f"Starting Frontend (Vite) background process...")
    
    # Use shell=True for windows to handle npm command
    cmd = "npm run dev"
    try:
        # Start as a detached process
        subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(frontend_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0
        )
        logger.info(f"Frontend dev server command sent successfully")
    except Exception as e:
        logger.error(f"Failed to start frontend: {e}")


class WebhookClient:
    """Client for sending webhooks."""
    
    def __init__(self, url: str, api_key: Optional[str] = None):
        """
        Initialize webhook client.
        
        Args:
            url: Webhook URL
            api_key: Optional API key for authentication
        """
        self.url = url
        self.api_key = api_key
    
    async def send_signal(self, signal: Dict) -> bool:
        """
        Send signal to webhook URL.
        
        Args:
            signal: Signal dictionary
        
        Returns:
            True if successful
        """
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                headers = {"X-API-KEY": self.api_key} if self.api_key else {}
                response = await client.post(
                    self.url,
                    json=signal,
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    logger.info(f"Signal sent successfully: {signal.get('action')}")
                    return True
                else:
                    logger.error(f"Failed to send signal: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending signal: {e}")
            return False
        
        return False
