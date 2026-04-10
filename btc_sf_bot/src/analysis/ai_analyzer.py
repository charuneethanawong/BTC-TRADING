"""
AI Market Analyzer — ใช้ Gemini Free วิเคราะห์สภาวะตลาด
Level 1: Pre-Trade Analysis

Version: 1.0
Date: 2026-03-25
"""
import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pathlib import Path

# Check if openai is available
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from src.utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)


class AIMarketAnalyzer:
    """
    AI Market Analyzer using Gemini Free Tier.
    
    Level 1: Pre-Trade Analysis (Log Only Phase)
    - Analyzes market every 5 minutes
    - Returns bias, confidence, action, reason
    - Logs only (no score adjustment in Phase 1)
    """
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        
        # v36.0: Use SQLite DB instead of JSONL
        from src.data.db_manager import get_db
        self._db = get_db()
        
        # v36.2: Removed legacy JSONL paths - use DB only
        # Legacy paths removed - all logging now goes to SQLite
        
        # v36.2: Enable auto-flush for DB writes
        self._db_auto_flush = True
        
        # ปิด log รกๆ ของ httpx เวลายิง API ออกไป
        import logging
        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        self.api_key = self.config.get('openrouter_api_key', '')
        # v18.7: OpenRouter — ใช้ DeepSeek Chat (free tier)
        self.model = self.config.get('ai_model', 'deepseek/deepseek-v3.2')
        
        # Enable only if API key provided (Disabled per user request)
        self.enabled = False # bool(self.api_key) and OPENAI_AVAILABLE
        
        # Rate limiting
        self.call_interval = self.config.get('call_interval', 300)  # 5 นาที
        self._last_call_time = None
        self._cached_result = None
        
        # v35.2: Cache for close context
        self._last_binance_data = {}
        
        # v18.5: Accuracy tracking
        self._ai_history: List[Dict] = []  # เก็บ AI analysis + price ทุกครั้ง
        self._evaluation_interval = 3600  # 1 ชม.
        self._last_evaluation = None
        
        # OpenRouter client
        self._client = None
        if self.enabled:
            try:
                self._client = AsyncOpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=self.api_key
                )
                logger.info("[AI] OpenRouter Analyzer initialized")
            except Exception as e:
                logger.warning(f"[AI] Failed to initialize: {e}")
                self.enabled = False
        else:
            logger.info("[AI] AI Analyzer disabled (no API key or openai not available)")

    def _build_market_context(self, candles_h1, candles_m5,
                               binance_data: Dict, current_price: float) -> str:
        """v27.1: Market context from unified engines — same data as MARKET display"""

        # v27.1: H1 Bias from H1BiasEngine (single source of truth)
        h1b = binance_data.get('h1_bias_result')
        if h1b and hasattr(h1b, 'ema9'):
            e9, e20, e50 = h1b.ema9, h1b.ema20, h1b.ema50
            lc = h1b.lc if h1b.lc != 'NEUTRAL' else 'N'
            lr = h1b.lr if h1b.lr != 'NEUTRAL' else 'N'
            lr_c = h1b.lr_count
            l0 = h1b.l0[0] if h1b.l0 != 'NEUTRAL' else 'N'  # B/S/N
            bias = h1b.bias
            bias_level = h1b.bias_level
        else:
            e9 = binance_data.get('ema9', 0)
            e20 = binance_data.get('ema20', 0)
            e50 = binance_data.get('ema50', 0)
            lc = binance_data.get('h1_candle_bias', 'N')
            lr = binance_data.get('lr_bias', 'N')
            lr_c = binance_data.get('lr_count', 0)
            l0 = 'N'
            bias = binance_data.get('h1_bias', 'NEUTRAL')
            bias_level = ''

        h1_dist = binance_data.get('h1_ema_dist_pct', 0)
        ema_dir = "BULL" if e9 > e20 > e50 else "BEAR" if e9 < e20 < e50 else "MIX"

        # v27.1: Regime from RegimeResult
        regime_obj = binance_data.get('regime_result')
        if regime_obj and hasattr(regime_obj, 'regime'):
            adx = regime_obj.adx_h1
            regime = regime_obj.regime
            di_spread = regime_obj.di_spread
            atr_ratio = regime_obj.atr_ratio
            plus_di = regime_obj.plus_di
            minus_di = regime_obj.minus_di
        else:
            adx = binance_data.get('adx_h1', 25)
            regime = 'RANGING'
            di_spread = 0
            atr_ratio = 1.0
            plus_di = 0
            minus_di = 0

        # v27.1: Order flow from MarketSnapshot (via binance_data propagation)
        of = binance_data.get('order_flow_summary', {}) or {}
        der_val = of.get('der', 0)
        # v34.6: Use wall_info string (same as gate uses), fallback to wall_dom/wall_ratio
        wall_info = binance_data.get('wall_info', '')
        if not wall_info or wall_info == 'NONE':
            wall_dom = binance_data.get('wall_dom', 'N')
            wall_ratio = binance_data.get('wall_ratio', 1.0)
            wall = f"{wall_dom} {wall_ratio:.1f}x"
        else:
            wall = wall_info

        # v27.3: Raw pullback data — let AI assess significance
        pb = binance_data.get('pullback', {}) or {}
        pb_vol_declining = pb.get('vol_declining', False) if pb else False

        # v27.3: M5 last 3 candles raw (same format as H1)
        snap = binance_data.get('snapshot')

        # v27.2: DER persistence from snapshot
        der_persist = 0
        der_sustain = ''
        if snap and hasattr(snap, 'der_persistence'):
            der_persist = snap.der_persistence
            der_sustain = snap.der_sustainability

        der_extra = f" {der_persist}candle {der_sustain}" if der_persist >= 2 else ""

        # v27.3: M5 state from Efficiency Ratio
        m5_sw = ""
        if snap and hasattr(snap, 'm5_state'):
            m5_sw = f" m5:{snap.m5_state}(ER:{snap.m5_efficiency:.2f})"

        # v27.3: News context
        news_str = binance_data.get('news_context', 'none')

        # v27.3: Candle raw data helper — same format for H1 and M5
        def _candle_str(candles, n=3):
            if candles is None or len(candles) < n:
                return ""
            parts = []
            for _, r in candles.tail(n).iterrows():
                o, h, l, c = float(r['open']), float(r['high']), float(r['low']), float(r['close'])
                rng = h - l
                if rng > 0:
                    body_pct = int(abs(c - o) / rng * 100)
                    upper_wick = h - max(o, c)
                    lower_wick = min(o, c) - l
                    wick = "↑" if upper_wick > lower_wick * 1.5 else "↓" if lower_wick > upper_wick * 1.5 else "─"
                    direction = "▲" if c > o else "▼" if c < o else "─"
                    parts.append(f"{direction}{rng:.0f}b{body_pct}%{wick}")
                else:
                    parts.append("─0")
            return " ".join(parts)

        h1c_str = _candle_str(candles_h1, 3)
        m5c_str = _candle_str(candles_m5, 5)

        # v27.3: Raw data — let AI analyze, not just confirm bot's labels
        news_part = f"\nnews:{news_str}" if news_str != 'none' else ""

        # H1 EMA raw values (AI decides trend, not bot)
        # Pullback raw (AI decides significance)
        pb_dist = pb.get('h1_ema_dist_pct', 0) if pb else 0

        # v27.3: Pullback raw — distance + volume declining (AI decides if significant)
        pb_raw = f"ema_dist:{pb_dist:.1f}%"
        if pb_vol_declining:
            pb_raw += " vol_declining"

        h1c_part = f"\nh1(3): {h1c_str}" if h1c_str else ""
        m5c_part = f"\nm5(5): {m5c_str}" if m5c_str else ""
        # v27.3: Pre-compute EMA relationships — AI mistakes 66733>66635 as <
        ema9v20 = ">" if e9 > e20 else "<" if e9 < e20 else "="
        ema20v50 = ">" if e20 > e50 else "<" if e20 < e50 else "="
        ema_summary = f"h1_ema: 9({e9:.0f}){ema9v20}20({e20:.0f}){ema20v50}50({e50:.0f})"

        # v30.3: Read pre-computed swing levels from binance_data (single source)
        h1_sh = binance_data.get('h1_swing_highs', [])
        h1_sl = binance_data.get('h1_swing_lows', [])
        m5_sh = binance_data.get('m5_swing_highs', [])
        m5_sl = binance_data.get('m5_swing_lows', [])

        # H1 structure: HH/HL = uptrend, LH/LL = downtrend
        h1_struct_str = ''
        if len(h1_sh) >= 2 and len(h1_sl) >= 2:
            hh = "HH" if h1_sh[-1] > h1_sh[-2] else "LH"
            hl = "HL" if h1_sl[-1] > h1_sl[-2] else "LL"
            h1_struct_str = f"\nh1_swing: {hh}+{hl} highs:[{','.join(f'{v:.0f}' for v in h1_sh)}] lows:[{','.join(f'{v:.0f}' for v in h1_sl)}]"

        m5_struct_str = ''
        if len(m5_sh) >= 2 and len(m5_sl) >= 2:
            hh = "HH" if m5_sh[-1] > m5_sh[-2] else "LH"
            hl = "HL" if m5_sl[-1] > m5_sl[-2] else "LL"
            m5_struct_str = f"\nm5_swing: {hh}+{hl} highs:[{','.join(f'{v:.0f}' for v in m5_sh)}] lows:[{','.join(f'{v:.0f}' for v in m5_sl)}]"

        # H1 bias level + EMA50 distance
        bias_lvl_str = f" bias:{bias_level}" if bias_level and bias_level != 'NONE' else " bias:NONE"
        ema50_dist = ((current_price - e50) / e50 * 100) if e50 > 0 else 0
        ema50_str = f" ema50_dist:{ema50_dist:+.2f}%"

        

        # v29.1: POC/Funding/OI — existing data not yet sent to AI
        poc = binance_data.get('poc') or binance_data.get('composite_poc', 0)
        funding = binance_data.get('funding_rate', 0) or 0
        oi = binance_data.get('oi', 0) or 0
        oi_prev = binance_data.get('oi_1min_ago', oi) or oi
        oi_chg_pct = ((oi - oi_prev) / oi_prev * 100) if oi_prev > 0 else 0
        extra_data_str = f"\npoc:{poc:.0f} funding:{funding:.6f} oi_chg:{oi_chg_pct:+.2f}%" if poc > 0 else ""

        # v37.3: Add 7 missing fields for AI context
        # Session
        session = binance_data.get('session', 'LONDON')
        
        # M5 EMA position
        m5_ema_pos = ''
        if snap and hasattr(snap, 'm5_ema_position'):
            m5_ema_pos = f" ema_pos:{snap.m5_ema_position}"
        
        # DER direction (flow_dir)
        der_dir = of.get('der_direction', 'NEUTRAL')
        
        # Regime confidence
        regime_conf = 'HIGH'
        if regime_obj and hasattr(regime_obj, 'regime_confidence'):
            regime_conf = regime_obj.regime_confidence
        
        # ATR
        atr = 0
        if regime_obj and hasattr(regime_obj, 'atr_m5'):
            atr = regime_obj.atr_m5
        
        # v37.3: VAH/VAL
        vah = binance_data.get('vah', 0)
        val = binance_data.get('val', 0)
        
        # v28.1: M5 EMA position + range + candle pattern
        m5_range_str = ''
        

        return f"""price:{current_price:.0f} sess:{session} {ema_summary}{bias_lvl_str}{ema50_str}
regime:{regime}({regime_conf}) adx:{adx:.0f} +di:{plus_di:.0f} -di:{minus_di:.0f} atr:{atr:.0f}{m5_sw}{m5_ema_pos}
order_flow:{der_val:.3f} flow_dir:{der_dir}{der_extra} delta:{of.get('delta', 0):+.1f} wall:{wall}
pullback:{pb_raw}
poc:{poc:.0f} vah:{vah:.0f} val:{val:.0f} funding:{funding:.6f} oi_chg:{oi_chg_pct:+.2f}%{m5_range_str}{extra_data_str}{h1_struct_str}{m5_struct_str}{h1c_part}{m5c_part}{news_part}"""

    def _build_prompt(self, context: str) -> str:
        """v37.4: System prompt with role + skills + data format (cached)"""
        return [
            {"role": "system", "content": """You are a BTC futures institutional order flow analyst.
Timeframe: M5 scalping (5-30 minute trades).
Your edge: reading institutional footprint through order flow, walls, and market structure.

Skills:
1. Order flow: order_flow shows institutional conviction. flow_dir shows their direction. High order_flow (>0.6) + aligned delta = strong institutional move.
2. Wall analysis: Large ASK wall = price ceiling (sellers defending). Large BID wall = price floor (buyers defending). Wall >=5x = very strong barrier.
3. EMA structure: 9>20>50 = bullish cascade. Inverse = bearish. Mixed = no clear trend.
4. Swing structure: HH+HL = uptrend. LH+LL = downtrend. Confirms or denies EMA signals.
5. Mean reversion: Price far from H1 EMA (high pullback_dist) + large wall blocking = price likely reverts to mean.
6. Regime: RANGING/CHOPPY = breakouts often fake. Only trust strong order_flow (>0.6) with wall confirmation.

Decision:
- BUY: order_flow strong + flow_dir LONG + wall BID supporting or no ASK blocking
- SELL: order_flow strong + flow_dir SHORT + wall ASK supporting or no BID blocking
- WAIT: order_flow weak (<0.3) OR signals conflict OR no clear edge

Data keys:
- order_flow: 0-1 (>0.6=strong <0.3=noise), flow_dir: LONG/SHORT
- persist: candles of flow, sustain: TOO_EARLY=fresh FADING=exhausting
- wall: ASK/BID + ratio (>=5x=strong barrier)
- ema_pos: ABOVE_ALL/BETWEEN/BELOW_ALL, h1_bias: trend + level
- regime(conf): TRENDING/CHOPPY/RANGING + HIGH/LOW
- candles: dir|range$|body%|wick (▲bull ▼bear ↑upper ↓lower)
- swing: HH+HL=up LH+LL=down

JSON only: {"signal":"BUY/SELL/WAIT","reason":"80 chars, cite values"}"""},
            {"role": "user", "content": context}
        ]

    @log_errors
    @timed_metric("AIMarketAnalyzer.analyze")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    async def analyze(self, candles_h1, candles_m5,
                      binance_data: Dict, current_price: float) -> Optional[Dict]:
        """
        วิเคราะห์ตลาดด้วย AI
        Return: {'bias', 'confidence', 'action', 'reason', 'key_level'} หรือ None
        """
        if not self.enabled or not self._client:
            return None
            
        # v24.0: Check real-time toggle from dashboard
        from src.execution.webhook_server import dashboard_state
        if not dashboard_state.get('ai', {}).get('enabled', True):
            # Only log once when disabled to avoid cluttering logs
            return None
        
        # v27.2: Rate limiting removed — new_candle gate in main.py handles timing (every 5 min)
        now = datetime.now(timezone.utc)
        
        try:
            # Build context + prompt
            context = self._build_market_context(
                candles_h1, candles_m5, binance_data, current_price
            )
            prompt = self._build_prompt(context)

            # v27.3: Log context for debugging AI vs MARKET mismatch
            logger.debug(f"[AI] Context: {context}")

            # Call OpenRouter (async via AsyncOpenAI)
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=prompt  # v37.3: [system, user] format
            )
            
            # Parse JSON response
            raw_content = response.choices[0].message.content
            if not raw_content or not raw_content.strip():
                logger.warning("[AI] Empty response from API")
                return self._cached_result
            text = raw_content.strip()
            # ลบ ```json ``` ถ้ามี
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text[3:]
                text = text.rsplit('```', 1)[0]
                text = text.strip()
            if not text:
                logger.warning("[AI] Empty after cleanup")
                return self._cached_result

            # v27.3: Extract JSON from mixed response (AI may wrap JSON in text)
            if not text.startswith('{'):
                import re
                json_match = re.search(r'\{[^{}]*\}', text)
                if json_match:
                    text = json_match.group()
                else:
                    logger.warning(f"[AI] No JSON found in response: {text[:100]}")
                    return self._cached_result

            result = json.loads(text)
            
            # v37.5: Map new 2-field format to internal format (no confidence)
            # New: {"signal":"BUY/SELL/WAIT","reason":"..."}
            # Internal: {"bias","confidence","action","reason"}
            signal = result.get('signal', 'WAIT').upper()
            result['bias'] = 'BULLISH' if signal == 'BUY' else 'BEARISH' if signal == 'SELL' else 'NEUTRAL'
            result['confidence'] = 0  # v37.5: not used - set 0 for backward compat
            result['action'] = 'TRADE' if signal == 'BUY' else 'TRADE' if signal == 'SELL' else 'WAIT'
            result['reason'] = result.get('reason', '')[:200]
            result['key_level'] = float(result.get('key_level', 0))
            result['timestamp'] = now.isoformat()
            
            # v35.1: Add market context to AI log
            result['price'] = current_price
            result['regime'] = binance_data.get('regime', '')
            result['m5_state'] = binance_data.get('m5_state', '')
            result['h1_dist'] = binance_data.get('h1_ema_dist_pct', 0)
            # Get DER from order_flow_summary
            of = binance_data.get('order_flow_summary', {})
            result['der'] = of.get('der', 0)
            # Get wall info
            ws = binance_data.get('wall_scan', {})
            wall_dom = ws.get('raw_dominant', 'N')
            wall_ratio = ws.get('raw_ratio', 1)
            result['wall'] = f"{wall_dom} {wall_ratio:.1f}x"
            
            # Cache
            self._cached_result = result
            self._last_call_time = now
            
            # Log result
            logger.info(
                f"[AI] {result['bias']} {result['confidence']}% | "
                f"{result['action']} | {result['reason']}"
            )
            
            # Save to log file
            self._save_to_log(result)
            
            # v18.5: Track for accuracy evaluation
            self.track_analysis(result, current_price)
            
            return result
            
        except json.JSONDecodeError as e:
            logger.warning(f"[AI] JSON parse error: {e} | raw: {text[:100] if text else 'EMPTY'}")
            return self._cached_result
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"[AI] Analysis error: {err_msg[:250]}...")
            return self._cached_result

    async def evaluate_signal(self, signal: dict, binance_data: dict,
                               candles_h1, candles_m5, current_price: float) -> dict:
        """
        v28.1: AI evaluates a specific signal before sending to EA.
        Called ONLY when a signal passes all gates — replaces periodic analysis.

        Returns: {'approve': bool, 'confidence': int, 'reason': str}
        """
        if not self.enabled or not self._client:
            return {'approve': True, 'confidence': 0, 'reason': 'AI disabled'}

        try:
            # Build signal-specific context
            mode = signal.get('mode', 'UNKNOWN')
            direction = signal.get('direction', '')
            entry = signal.get('entry_price', 0)
            sl = signal.get('stop_loss', 0)
            tp = signal.get('take_profit', 0)
            rr = signal.get('actual_rr', 0)
            score = signal.get('score', 0)
            signal_type = signal.get('signal_type', mode)

            # Market context (reuse existing builder)
            market_ctx = self._build_market_context(candles_h1, candles_m5, binance_data, current_price)

            prompt = f"""You are a BTC institutional order flow analyst. A trading signal passed all technical gates. Evaluate if it should be executed.

SIGNAL: {mode} {signal_type} {direction} entry:{entry:.0f} sl:{sl:.0f} tp:{tp:.0f} rr:{rr:.2f} score:{score}

MARKET:
{market_ctx}

Evaluate: Does the market context support this {direction} trade?
Respond JSON ONLY: {{"approve":true/false,"confidence":0-100,"reason":"max 80 chars, cite values"}}"""

            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}]
            )

            raw = response.choices[0].message.content
            if not raw or not raw.strip():
                return {'approve': True, 'confidence': 0, 'reason': 'AI empty response'}

            text = raw.strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text[3:]
                text = text.rsplit('```', 1)[0].strip()
            if not text.startswith('{'):
                import re
                m = re.search(r'\{[^{}]*\}', text)
                text = m.group() if m else ''
            if not text:
                return {'approve': True, 'confidence': 0, 'reason': 'AI parse fail'}

            result = json.loads(text)
            # v37.5: Map new 2-field format (no confidence)
            signal = result.get('signal', 'WAIT').upper()
            approve = signal in ('BUY', 'SELL')
            reason = result.get('reason', '')[:80]
            
            # v37.5: confidence not used
            confidence = 0

            self._last_ai_result = {
                'bias': direction if approve else 'NEUTRAL',
                'confidence': confidence,
                'action': 'TRADE' if approve else 'REJECT',
                'reason': reason,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
            self._cached_result = self._last_ai_result

            tag = "APPROVE" if approve else "REJECT"
            logger.info(f"[AI] Signal {tag} | {mode} {direction} | conf:{confidence}% | {reason}")

            return {'approve': approve, 'confidence': confidence, 'reason': reason}

        except Exception as e:
            logger.warning(f"[AI] Signal eval error: {str(e)[:150]}")
            return {'approve': True, 'confidence': 0, 'reason': f'AI error: {str(e)[:50]}'}

    def _save_to_log(self, result: Dict):
        """v36.2: Save to database instead of JSONL"""
        try:
            self._db.insert_ai_analysis(result)
        except Exception as e:
            logger.debug(f"[AI] DB save error: {e}")
    
    # ==================== Auto Accuracy Tracking ====================
    
    def track_analysis(self, ai_result: Dict, current_price: float):
        """
        เก็บ AI analysis + price ไว้สำหรับคำนวณ accuracy ทีหลัง
        เรียกทุกครั้งหลัง AI วิเคราะห์
        """
        if not ai_result:
            return
        
        self._ai_history.append({
            'timestamp': ai_result.get('timestamp'),
            'bias': ai_result.get('bias'),
            'confidence': ai_result.get('confidence'),
            'action': ai_result.get('action'),
            'price': current_price,
            'evaluated': False
        })
        
        # เก็บไว้ max 24 records (12 ชม. ทุก 5 นาที)
        if len(self._ai_history) > 288:
            self._ai_history = self._ai_history[-144:]
    
    async def evaluate_and_log_accuracy(self, candles_m5, current_price: float) -> Optional[Dict]:
        """
        ประเมินความแม่นยำอัตโนมัติ (v18.6)
        ใช้ราคาจริงจาก candle data หลังผ่านไป 1 ชม. จากจุดที่ AI วิเคราะห์
        """
        if not self.enabled:
            return None

        now = datetime.now(timezone.utc)
        
        # หา records ที่ยังไม่ได้ evaluate
        unevaluated = [r for r in self._ai_history if not r.get('evaluated', False)]
        if not unevaluated:
            return None

        correct = 0
        total = 0
        from datetime import timedelta

        for record in unevaluated:
            price_at_analysis = record.get('price', 0)
            if price_at_analysis <= 0:
                continue

            # v18.6: Parse and make timezone-aware
            analysis_time_str = record.get('timestamp', '')
            try:
                analysis_time = datetime.fromisoformat(analysis_time_str.replace('Z', '+00:00'))
            except (ValueError, TypeError, AttributeError):
                analysis_time = datetime.now(timezone.utc)
            
            # v18.6: เป้าหมายคือหาราคาหลังผ่านไป 1 ชม. จริงๆ
            target_time = analysis_time + timedelta(hours=1)
            
            price_after = None
            # M2 FIX: Vectorized lookup replaces iterrows() O(n) scan.
            # Build a timezone-aware DatetimeIndex from the 'time' column (or the
            # DataFrame index) once, then use searchsorted for an O(log n) lookup.
            if candles_m5 is not None and not candles_m5.empty:
                import pandas as pd
                try:
                    # Resolve the time series: prefer 'time' column, else use the index
                    if 'time' in candles_m5.columns:
                        raw_times = candles_m5['time']
                    else:
                        raw_times = candles_m5.index.to_series()

                    # Convert to UTC-aware DatetimeIndex in one vectorized step
                    times_utc = pd.to_datetime(raw_times, unit='s', utc=True, errors='coerce')
                    if times_utc.isna().all():
                        # Fallback: try direct datetime parsing (already datetime objects)
                        times_utc = pd.to_datetime(raw_times, utc=True, errors='coerce')

                    # searchsorted finds the first index where times_utc >= target_time (O(log n))
                    target_ts = pd.Timestamp(target_time)
                    idx = times_utc.searchsorted(target_ts, side='left')
                    if idx < len(candles_m5):
                        price_after = candles_m5['close'].iloc[idx]
                except Exception:
                    # Graceful fallback: skip this record rather than crash
                    price_after = None
            
            # ถ้ายังไม่มีข้อมูลที่เวลาถึง 1 ชม. (ยังไม่ถึงเวลา) ให้ข้ามไปก่อน
            if price_after is None:
                continue

            # คำนวณทิศทางจริง
            price_change_pct = ((price_after - price_at_analysis) / price_at_analysis) * 100
            
            if price_change_pct > 0.2:
                actual = 'BULLISH'
            elif price_change_pct < -0.2:
                actual = 'BEARISH'
            else:
                actual = 'NEUTRAL'

            ai_bias = record.get('bias', 'NEUTRAL')
            is_correct = (ai_bias == actual)

            if ai_bias != 'NEUTRAL':
                total += 1
                if is_correct:
                    correct += 1

            # Mark evaluated + save result
            record['evaluated'] = True
            record['actual_direction'] = actual
            record['price_after_1h'] = price_after
            record['price_change_pct'] = round(price_change_pct, 2)
            record['correct'] = is_correct

            # บันทึก market result แยกไฟล์ (v18.6)
            self._save_market_result({
                'analysis_time': record.get('timestamp'),
                'ai_bias': ai_bias,
                'ai_confidence': record.get('confidence'),
                'ai_action': record.get('action'),
                'price_at_analysis': price_at_analysis,
                'price_after_1h': price_after,
                'price_change_pct': round(price_change_pct, 2),
                'actual_direction': actual,
                'correct': is_correct,
                'evaluated_at': now.isoformat()
            })

        if total > 0:
            self._last_evaluation = now
            accuracy = round((correct / total) * 100, 1)
            
            result = {
                'total': total,
                'correct': correct,
                'accuracy': accuracy,
                'cumulative': self._calculate_cumulative_accuracy()
            }
            
            # บันทึก accuracy log
            self._save_accuracy_log(result)
            
            logger.info(f"[AI Accuracy] Evaluated {total} records | Accuracy: {accuracy}%")
            return result
            
        return None

    def _save_market_result(self, result: Dict):
        """v36.2: Save to database instead of JSONL"""
        try:
            self._db.insert_ai_market_result(result)
        except Exception as e:
            logger.debug(f"[AI] Market result DB save error: {e}")
    
    def _save_accuracy_log(self, result: Dict):
        """v36.2: Save to database instead of JSONL"""
        try:
            result['timestamp'] = datetime.now(timezone.utc).isoformat()
            self._db.insert_ai_accuracy_log(result)
        except Exception as e:
            logger.debug(f"[AI] Accuracy log DB save error: {e}")
    
    def _calculate_cumulative_accuracy(self) -> Dict:
        """v36.2: Calculate cumulative accuracy from database"""
        try:
            results = self._db.get_ai_market_results(limit=1000)
            
            correct = 0
            total = 0
            
            for r in results:
                ai_bias = r.get('bias', 'NEUTRAL')
                actual = r.get('actual_direction', 'NEUTRAL')
                
                # NEUTRAL ไม่นับ
                if ai_bias == 'NEUTRAL':
                    continue
                
                if ai_bias == actual:
                    correct += 1
                total += 1
            
            accuracy = round((correct / total) * 100, 1) if total > 0 else 0
            
            return {
                'total': total,
                'correct': correct,
                'accuracy': accuracy
            }
        except Exception as e:
            logger.debug(f"[AI] Cumulative accuracy error: {e}")
            return {'total': 0, 'correct': 0, 'accuracy': 0}
    
    # ==================== Manual Accuracy Tracking ====================
    
    def log_market_result(self, ai_timestamp: str, actual_direction: str, 
                          price_at_analysis: float, price_after_1h: float):
        """
        บันทึกผลตลาดจริงหลังจาก AI วิเคราะห์
        
        Args:
            ai_timestamp: timestamp ตอนที่ AI วิเคราะห์
            actual_direction: ทิศทางจริง (BULLISH/BEARISH/NEUTRAL)
            price_at_analysis: ราคาตอน AI วิเคราะห์
            price_after_1h: ราคาหลังจาก 1 ชม.
        """
        try:
            # คำนวณ price change
            price_change_pct = ((price_after_1h - price_at_analysis) / price_at_analysis) * 100
            
            result = {
                'ai_timestamp': ai_timestamp,
                'actual_direction': actual_direction,
                'price_at_analysis': price_at_analysis,
                'price_after_1h': price_after_1h,
                'price_change_pct': round(price_change_pct, 2),
                'result_timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            # v36.2: Save to database instead of JSONL
            self._db.insert_ai_market_result(result)
            
            logger.info(
                f"[AI] Market Result | Actual:{actual_direction} | "
                f"Price:{price_at_analysis:.0f}→{price_after_1h:.0f} ({price_change_pct:+.2f}%)"
            )
            
        except Exception as e:
            logger.warning(f"[AI] Market result log error: {e}")
    
    async def evaluate_accuracy(self, candles_m5, current_price: float) -> Optional[Dict]:
        """
        v36.2: Evaluate AI accuracy using database instead of JSONL
        
        Returns:
            {'total': N, 'correct': M, 'accuracy': P%}
        """
        try:
            # Get AI analysis from DB
            ai_logs = self._db.get_ai_analysis(limit=500)
            if len(ai_logs) < 5:
                return None
            
            # Get market results from DB
            market_results = self._db.get_ai_market_results(limit=500)
            if not market_results:
                return None
            
            # Create lookup dict
            market_dict = {r.get('timestamp') or r.get('ai_timestamp'): r for r in market_results}
            
            # Compare AI bias vs actual direction
            correct = 0
            total = 0
            
            for ai in ai_logs:
                ts = ai.get('timestamp')
                if ts not in market_dict:
                    continue
                
                market = market_dict[ts]
                ai_bias = ai.get('bias', 'NEUTRAL')
                actual = market.get('actual_direction', 'NEUTRAL')
                
                # ถ้า AI บอก BULLISH และตลาดจริงก็ขึ้น = ถูก
                if ai_bias == actual:
                    correct += 1
                total += 1
            
            accuracy = (correct / total * 100) if total > 0 else 0
            
            result = {
                'total': total,
                'correct': correct,
                'accuracy': round(accuracy, 1),
                'last_updated': datetime.now(timezone.utc).isoformat()
            }
            
            logger.info(
                f"[AI] Accuracy: {correct}/{total} = {accuracy:.1f}%"
            )
            
            return result
            
        except Exception as e:
            logger.warning(f"[AI] Accuracy evaluation error: {e}")
            return None
    
    # ==================== Phase 1.5: Trade Logging ====================
    
    def _check_aligned(self, direction: str, ai_analysis: Optional[Dict]) -> Optional[bool]:
        """ตรวจสอบว่า AI ตรงกับ signal หรือไม่"""
        if not ai_analysis:
            return None
        bias = ai_analysis.get('bias', 'NEUTRAL')
        if bias == 'NEUTRAL':
            return None
        return (direction == 'LONG' and bias == 'BULLISH') or \
               (direction == 'SHORT' and bias == 'BEARISH')
    
    def update_market_context(self, binance_data: dict):
        """v35.2: Cache latest market data for close context."""
        self._last_binance_data = binance_data
    
    def log_trade_entry(self, signal_id: str, signal: Dict, ai_analysis: Optional[Dict], market_context: Optional[Dict] = None):
        """
        v26.0: บันทึก trade entry + AI + market context ทั้งหมดที่บอทวิเคราะห์
        """
        try:
            aligned = self._check_aligned(signal.get('direction'), ai_analysis)

            entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'signal_id': signal_id,
                'mode': signal.get('mode'),
                'direction': signal.get('direction'),
                'signal_type': signal.get('signal_type', 'MOMENTUM'),
                'score': signal.get('score'),
                'entry_price': signal.get('entry_price'),
                'stop_loss': signal.get('stop_loss'),
                'take_profit': signal.get('take_profit'),
                'session': signal.get('session'),
                'actual_rr': signal.get('actual_rr'),
                'sl_reason': signal.get('sl_reason'),

                # AI
                'ai_bias': ai_analysis.get('bias') if ai_analysis else None,
                'ai_confidence': ai_analysis.get('confidence') if ai_analysis else None,
                'ai_action': ai_analysis.get('action') if ai_analysis else None,
                'ai_reason': ai_analysis.get('reason', '')[:100] if ai_analysis else None,
                'ai_aligned': aligned,
                # v34.6: Track NEUTRAL + low confidence for analysis
                'ai_neutral_low_conf': (
                    ai_analysis.get('bias') == 'NEUTRAL' and
                    ai_analysis.get('confidence', 100) < 50
                ) if ai_analysis else False,

                # Status tracking
                'status': 'SIGNAL_SENT',
                'ea_opened': False,
                'pnl': None,
                'exit_reason': None,
                'closed_at': None,
            }

            # v26.0: Add market context (all bot analysis data)
            if market_context:
                entry.update({
                    # H1 Bias Layers
                    'lc': market_context.get('lc'),
                    'lr': market_context.get('lr'),
                    'l0': market_context.get('l0'),
                    'l1': market_context.get('l1'),
                    'l2': market_context.get('l2'),
                    'l3': market_context.get('l3'),
                    # H1 EMA
                    'h1_ema9': round(market_context.get('h1_ema9') or 0, 0),
                    'h1_ema20': round(market_context.get('h1_ema20') or 0, 0),
                    'h1_ema50': round(market_context.get('h1_ema50') or 0, 0),
                    'h1_dist_pct': round(market_context.get('h1_dist_pct') or 0, 2),
                    'ema_trend': market_context.get('ema_trend'),
                    # Pullback
                    'pullback': market_context.get('pullback'),
                    # Order Flow
                    'wall_info': market_context.get('wall_info'),
                    'delta': round(market_context.get('delta') or 0, 3),
                    'der': round(market_context.get('der') or 0, 3),
                    'oi': market_context.get('oi'),
                    'funding': market_context.get('funding'),
                    # MLVP
                    'poc': market_context.get('poc'),
                    'vah': market_context.get('vah'),
                    'val': market_context.get('val'),
                    # Score breakdown (MOD-9: Fixed - get from signal, not market_context)
                    'breakdown': signal.get('score_breakdown') or signal.get('breakdown') or market_context.get('breakdown'),
                    # v29.1: Missing fields for analysis
                    'm5_state': market_context.get('m5_state'),
                    'regime': market_context.get('regime'),
                    'h1_bias_level': market_context.get('h1_bias_level'),
                    # v30.7: DER stability + M5 context for analyst
                    'der_direction': market_context.get('der_direction'),
                    'der_persistence': market_context.get('der_persistence'),
                    'der_sustainability': market_context.get('der_sustainability'),
                    'm5_efficiency': market_context.get('m5_efficiency'),
                    'm5_ema_position': market_context.get('m5_ema_position'),
                    'atr_m5': market_context.get('atr_m5'),
                    
                })
            
            # v36.0: Use DB instead of JSONL
            self._db.insert_trade(entry)
            
            logger.info(
                f"[AI] Signal Sent | {signal_id} | {signal.get('direction')} "
                f"| AI:{ai_analysis.get('bias') if ai_analysis else 'N/A'} "
                f"{'✓ ALIGNED' if aligned else '✗ CONFLICT' if aligned is False else '—'}"
            )
            
        except Exception as e:
            logger.warning(f"[AI] Trade entry log error: {e}")
    
    def log_skipped_signal(self, mode: str, direction: str, gate_blocked: str, 
                          ai_analysis: Optional[Dict], score: int):
        """
        บันทึก signal ที่ถูก block (Phase 1.5)
        
        Args:
            mode: IPA/IOF/IPAF/IOFF
            direction: LONG/SHORT
            gate_blocked: ชื่อ gate ที่ block (เช่น Gate 3, Gate 4)
            ai_analysis: AI analysis result
            score: Score ตอนถูก block
        """
        try:
            aligned = self._check_aligned(direction, ai_analysis)
            
            entry = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'signal_id': f"{mode}_{direction}_SKIPPED_{gate_blocked}",
                'mode': mode,
                'direction': direction,
                'score': score,
                'gate_blocked': gate_blocked,
                
                # AI data ตอนถูก block
                'ai_bias': ai_analysis.get('bias') if ai_analysis else None,
                'ai_confidence': ai_analysis.get('confidence') if ai_analysis else None,
                'ai_action': ai_analysis.get('action') if ai_analysis else None,
                'ai_reason': ai_analysis.get('reason', '')[:100] if ai_analysis else None,
                'ai_aligned': 1 if aligned is True else 0 if aligned is False else None,
                
                # Result
                'result': 'SKIPPED',
            }
            
            # v36.0: Use DB instead of JSONL
            self._db.insert_ai_skipped(entry)
            
            logger.info(
                f"[AI] Skipped | {mode} {direction} | Blocked:{gate_blocked} | "
                f"AI:{ai_analysis.get('bias') if ai_analysis else 'N/A'} {ai_analysis.get('action') if ai_analysis else ''}"
            )
            
        except Exception as e:
            logger.debug(f"[AI] Skipped log error: {e}")
    
    def log_trade_opened(self, signal_id: str, actual_entry_price: float = 0):
        """v36.2: Update trade status to OPENED in database"""
        try:
            self._db.update_trade(signal_id, {
                'status': 'OPENED',
                'ea_opened': True,
                'opened_at': datetime.now(timezone.utc).isoformat(),
                'actual_entry_price': actual_entry_price if actual_entry_price > 0 else None
            })
            logger.info(f"[AI] Trade Opened | {signal_id}")
        except Exception as e:
            logger.debug(f"[AI] Trade opened log error: {e}")
    
    def log_trade_exit(self, signal_id: str, pnl: float, exit_reason: str, mfe: float = 0, mae: float = 0):
        """
        EA ปิด trade — update status to WIN/LOSS/BE (v19.0)
        v26.0: Added MFE/MAE (max favorable/adverse excursion from entry)
        v36.0: Use SQLite DB instead of JSONL
        """
        try:
            # Prepare updates
            updates = {
                'status': 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'BE',
                'pnl': round(pnl, 2),
                'exit_reason': exit_reason,
                'closed_at': datetime.now(timezone.utc).isoformat(),
            }
            if mfe > 0:
                updates['mfe'] = round(mfe, 2)
            if mae > 0:
                updates['mae'] = round(mae, 2)
            
            # v35.2: Add close context from cached binance_data
            bd = self._last_binance_data
            if bd:
                updates['price_at_close'] = bd.get('current_price', 0)
                updates['regime_at_close'] = bd.get('regime', '')
                updates['m5_state_at_close'] = bd.get('m5_state', '')
                # Get wall info
                ws = bd.get('wall_scan', {})
                wall_dom = ws.get('raw_dominant', 'N') if ws else 'N'
                wall_ratio = ws.get('raw_ratio', 1) if ws else 1
                updates['wall_at_close'] = f"{wall_dom} {wall_ratio:.1f}x"
                updates['delta_at_close'] = round(bd.get('delta', 0), 1)
                updates['der_at_close'] = round(bd.get('der', 0), 3)
                updates['h1_dist_at_close'] = round(bd.get('h1_ema_dist_pct', 0), 2)
                updates['h1_bias_at_close'] = bd.get('h1_bias', '')
            
            # Use DB update
            self._db.update_trade(signal_id, updates)
            
            # Get trade for logging
            trade = self._db.get_trade(signal_id)
            if trade:
                aligned = trade.get('ai_aligned')
                status = updates['status']
                logger.info(
                    f"[AI] Trade Result | {signal_id} → {status} | PnL:{pnl:+.2f} | "
                    f"AI {'✓ aligned' if aligned else '✗ conflict'} "
                    f"({trade.get('ai_bias')} {trade.get('ai_confidence')}%)"
                )

        except Exception as e:
            logger.warning(f"[AI] Trade exit log error: {e}")
    
    def cleanup_stale_signals(self, timeout_seconds: int = 30, timeout_minutes: int = None):
        """
        v50.6: Clean up stale SIGNAL_SENT (30s) and OPENED (2h) trades
        """
        try:
            now = datetime.now(timezone.utc)
            ts = timeout_seconds

            # Clean SIGNAL_SENT that are too old -> EA_SKIPPED
            with self._db._conn() as conn:
                skip_reason = f'No EA confirmation within {ts}s'
                result = conn.execute("""
                    UPDATE trades
                    SET status = 'EA_SKIPPED',
                        skip_reason = ?
                    WHERE status = 'SIGNAL_SENT'
                    AND timestamp < datetime('now', '-' || ? || ' seconds')
                """, (skip_reason, str(ts)))
                
                signal_sent_cleaned = result.rowcount
            
            # v50.8: OPENED trades are NOT auto-cleaned — wait for EA confirm
            # EA will send TP/SL/CLOSED confirmation when trade closes
            opened_cleaned = 0
            
            total_cleaned = signal_sent_cleaned + opened_cleaned
            if total_cleaned > 0:
                logger.warning(f"[AI] Cleaned {total_cleaned} stale trades (SIGNAL_SENT:{signal_sent_cleaned}, OPENED:{opened_cleaned})")
                
        except Exception as e:
            logger.warning(f"[AI] Cleanup error: {e}")
    
    def get_trade_summary(self) -> Dict:
        """
        v36.2: Get trade summary from database
        
        Returns:
            {'total': N, 'wins': W, 'losses': L, 'win_rate': X%,
             'aligned_wins': AW, 'aligned_losses': AL,
             'conflict_wins': CW, 'conflict_losses': CL,
             'signal_sent': S, 'opened': O, 'skipped': K, 'pending': P}
        """
        try:
            trades = self._db.get_trades(limit=1000)
            
            total = wins = losses = 0
            aligned_wins = aligned_losses = 0
            conflict_wins = conflict_losses = 0
            signal_sent = opened = skipped = 0
            
            for record in trades:
                status = record.get('status')
                
                # Count by status
                if status == 'SIGNAL_SENT':
                    signal_sent += 1
                elif status == 'OPENED':
                    opened += 1
                elif status == 'EA_SKIPPED':
                    skipped += 1
                # Count closed trades
                elif status in ('WIN', 'LOSS', 'BE'):
                    total += 1
                    aligned = record.get('ai_aligned')
                    
                    if status == 'WIN':
                        wins += 1
                        if aligned is True:
                            aligned_wins += 1
                        elif aligned is False:
                            conflict_wins += 1
                    elif status == 'LOSS':
                        losses += 1
                        if aligned is True:
                            aligned_losses += 1
                        elif aligned is False:
                            conflict_losses += 1
            
            win_rate = round((wins / total * 100), 1) if total > 0 else 0
            
            return {
                'total': total,
                'wins': wins,
                'losses': losses,
                'win_rate': win_rate,
                'aligned_wins': aligned_wins,
                'aligned_losses': aligned_losses,
                'conflict_wins': conflict_wins,
                'conflict_losses': conflict_losses,
                'signal_sent': signal_sent,
                'opened': opened,
                'skipped': skipped,
                'pending': signal_sent,
            }
        except Exception as e:
            logger.warning(f"[AI] Trade summary error: {e}")
            return {'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'signal_sent': 0, 'opened': 0, 'skipped': 0}
