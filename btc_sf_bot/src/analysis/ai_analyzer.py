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
        
        # v26.1: Absolute path for log files (Fix 1C)
        self._log_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'ai_trade_log.jsonl'
        self._ai_analysis_log_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'ai_analysis_log.jsonl'
        self._ai_market_results_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'ai_market_results.jsonl'
        self._ai_accuracy_log_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'ai_accuracy_log.jsonl'
        self._ai_skipped_log_path = Path(__file__).resolve().parent.parent.parent / 'data' / 'ai_skipped_log.jsonl'
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # ปิด log รกๆ ของ httpx เวลายิง API ออกไป
        import logging
        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        self.api_key = self.config.get('openrouter_api_key', '')
        # v18.7: OpenRouter — ใช้ DeepSeek Chat (free tier)
        self.model = self.config.get('ai_model', 'deepseek/deepseek-chat')
        
        # Enable only if API key provided
        self.enabled = bool(self.api_key) and OPENAI_AVAILABLE
        
        # Rate limiting
        self.call_interval = self.config.get('call_interval', 300)  # 5 นาที
        self._last_call_time = None
        self._cached_result = None
        
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
        ws = binance_data.get('wall_scan', {}) or {}
        wall = f"{ws.get('raw_dominant', 'N')} {ws.get('raw_ratio', 1):.1f}x"

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

        # v28.1: M5 EMA position + range + candle pattern
        m5_ema_pos = ''
        m5_range_str = ''
        

        return f"""price:{current_price:.0f} {ema_summary}{bias_lvl_str}{ema50_str}
regime:{regime} adx:{adx:.0f} +di:{plus_di:.0f} -di:{minus_di:.0f}{m5_sw}{m5_ema_pos}
der:{der_val:.3f}{der_extra} delta:{of.get('delta', 0):+.1f} wall:{wall} {pb_raw}{m5_range_str}{extra_data_str}{h1_struct_str}{m5_struct_str}{h1c_part}{m5c_part}{news_part}"""

    def _build_prompt(self, context: str) -> str:
        """v27.3: Expert role + raw data"""
        return f"""You are a BTC institutional order flow analyst specializing in Smart Money Concepts (SMC) and M5 scalping.

Your expertise:
- Read EMA alignment to determine H1 trend structure (EMA9>EMA20>EMA50=bullish cascade, inverse=bearish)
- Read swing structure: HH+HL=uptrend, LH+LL=downtrend, HH+LL=expansion, LH+HL=compression
- Interpret order flow: DER shows directional conviction, delta shows net buying/selling, wall shows institutional defense
- Assess pullback quality: ema_dist near zero + vol_declining = pullback ending, price returning to mean
- Use ema50_dist to gauge macro positioning: negative=below EMA50(bearish macro), positive=above(bullish macro)
- bias level: STRONG>CONFIRMED+>CONFIRMED>EARLY>NONE — higher=more layers agree on direction
- POC=volume magnet price, funding extreme(>0.01%=crowded long, <-0.01%=crowded short), oi_chg(+price+oi=real move, +price-oi=fake)

Analyze next 5-30 minutes:

{context}

Format: Candles ▲/▼=dir, number=range$, b%=body ratio, ↑=upper wick dominant, ↓=lower wick dominant
DER: 0-1 (>0.6=strong institutional flow, <0.3=retail noise)

Respond with ONLY a single JSON object, no markdown, no explanation:
{{"bias":"BULLISH/BEARISH/NEUTRAL","confidence":0-100,"action":"TRADE/WAIT/CAUTION","reason":"max 80 chars, cite values"}}"""

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
                messages=[
                    {"role": "user", "content": prompt}
                ]
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
            
            # Validate
            result['bias'] = result.get('bias', 'NEUTRAL').upper()
            result['confidence'] = max(0, min(100, int(result.get('confidence', 50))))
            result['action'] = result.get('action', 'WAIT').upper()
            result['reason'] = result.get('reason', '')[:200]
            result['key_level'] = float(result.get('key_level', 0))
            result['timestamp'] = now.isoformat()
            
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
            approve = result.get('approve', True)
            raw_confidence = max(0, min(100, int(result.get('confidence', 50))))
            reason = result.get('reason', '')[:80]
            
            # v33.0: Efficiency-Weighted Confidence (EWC)
            snap = binance_data.get('snapshot')
            m5_efficiency = getattr(snap, 'm5_efficiency', 0.5) if snap else 0.5
            # Target efficiency 0.5, if < 0.2 penalize
            if m5_efficiency < 0.2:
                confidence = int(raw_confidence * 0.5)
                reason = f"[EWC-PENALTY] {reason}"
            else:
                confidence = raw_confidence

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
        """บันทึกผลการวิเคราะห์ลง log file"""
        try:
            log_path = self._ai_analysis_log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a') as f:
                f.write(json.dumps(result, default=str) + '\n')
        except Exception as e:
            logger.debug(f"[AI] Log save error: {e}")
    
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
        """บันทึก market result แยกไฟล์ (v18.6)"""
        try:
            log_path = self._ai_market_results_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a') as f:
                f.write(json.dumps(result, default=str) + '\n')
        except Exception as e:
            logger.debug(f"[AI] Market result save error: {e}")
    
    def _save_accuracy_log(self, result: Dict):
        """บันทึก accuracy log — สะสมทุกครั้ง"""
        try:
            log_path = self._ai_accuracy_log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)

            result['timestamp'] = datetime.now(timezone.utc).isoformat()

            with open(log_path, 'a') as f:
                f.write(json.dumps(result, default=str) + '\n')
        except Exception as e:
            logger.debug(f"[AI] Accuracy log save error: {e}")
    
    def _calculate_cumulative_accuracy(self) -> Dict:
        """คำนวณ cumulative accuracy จาก market results (v18.6)"""
        try:
            log_path = self._ai_market_results_path
            if not log_path.exists():
                return {'total': 0, 'correct': 0, 'accuracy': 0}
            
            correct = 0
            total = 0
            
            with open(log_path, 'r') as f:
                for line in f:
                    r = json.loads(line.strip())
                    ai_bias = r.get('ai_bias', 'NEUTRAL')
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
            
            # Save to market result log
            log_path = self._ai_market_results_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a') as f:
                f.write(json.dumps(result, default=str) + '\n')
            
            logger.info(
                f"[AI] Market Result | Actual:{actual_direction} | "
                f"Price:{price_at_analysis:.0f}→{price_after_1h:.0f} ({price_change_pct:+.2f}%)"
            )
            
        except Exception as e:
            logger.warning(f"[AI] Market result log error: {e}")
    
    async def evaluate_accuracy(self, candles_m5, current_price: float) -> Optional[Dict]:
        """
        ประเมินความแม่นยำของ AI โดยเทียบกับผลตลาดจริง
        
        เรียกหลังจากผ่านไป 1+ ชม. หลัง AI วิเคราะห์
        
        Returns:
            {'total': N, 'correct': M, 'accuracy': P%}
        """
        try:
            import pandas as pd
            
            # Load AI analysis logs
            ai_log_path = self._ai_analysis_log_path
            if not ai_log_path.exists():
                return None
            
            ai_logs = []
            with open(ai_log_path, 'r') as f:
                for line in f:
                    ai_logs.append(json.loads(line.strip()))
            
            if len(ai_logs) < 5:
                return None
            
            # Load market results
            market_path = self._ai_market_results_path
            market_results = {}
            if market_path.exists():
                with open(market_path, 'r') as f:
                    for line in f:
                        r = json.loads(line.strip())
                        market_results[r['ai_timestamp']] = r
            
            if not market_results:
                return None
            
            # Compare AI bias vs actual direction
            correct = 0
            total = 0
            
            for ai in ai_logs:
                ts = ai.get('timestamp')
                if ts not in market_results:
                    continue
                
                market = market_results[ts]
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
                    # Score breakdown
                    'breakdown': market_context.get('breakdown'),
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
            
            with open(self._log_path, 'a') as f:
                f.write(json.dumps(entry, default=str) + '\n')
            
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
                'ai_aligned': aligned,
                
                # Result
                'result': 'SKIPPED',
            }
            
            log_path = self._ai_skipped_log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a') as f:
                f.write(json.dumps(entry, default=str) + '\n')
            
            logger.info(
                f"[AI] Skipped | {mode} {direction} | Blocked:{gate_blocked} | "
                f"AI:{ai_analysis.get('bias') if ai_analysis else 'N/A'} {ai_analysis.get('action') if ai_analysis else ''}"
            )
            
        except Exception as e:
            logger.debug(f"[AI] Skipped log error: {e}")
    
    def log_trade_opened(self, signal_id: str, actual_entry_price: float = 0):
        """
        EA ยืนยันว่าเปิด trade จริง (v19.0)
        
        Args:
            signal_id: Signal ID to match
            actual_entry_price: ราคาที่ EA เปิดจริง
        """
        try:
            if not self._log_path.exists():
                return
            
            lines = []
            updated = False
            
            with open(self._log_path, 'r') as f:
                for line in f:
                    record = json.loads(line.strip())
                    if record.get('signal_id') == signal_id and record.get('status') == 'SIGNAL_SENT':
                        record['status'] = 'OPENED'
                        record['ea_opened'] = True
                        record['opened_at'] = datetime.now(timezone.utc).isoformat()
                        if actual_entry_price > 0:
                            record['actual_entry_price'] = actual_entry_price
                        updated = True
                        logger.info(f"[AI] Trade Opened | {signal_id}")
                    lines.append(json.dumps(record, default=str))

            if updated:
                with open(self._log_path, 'w') as f:
                    f.write('\n'.join(lines) + '\n')
                    
        except Exception as e:
            logger.debug(f"[AI] Trade opened log error: {e}")
    
    def log_trade_exit(self, signal_id: str, pnl: float, exit_reason: str, mfe: float = 0, mae: float = 0):
        """
        EA ปิด trade — update status to WIN/LOSS/BE (v19.0)
        v26.0: Added MFE/MAE (max favorable/adverse excursion from entry)
        """
        try:
            if not self._log_path.exists():
                return
            
            lines = []
            updated = False
            
            with open(self._log_path, 'r') as f:
                for line in f:
                    record = json.loads(line.strip())
                    if record.get('signal_id') == signal_id and record.get('status') == 'OPENED':
                        record['status'] = 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'BE'
                        record['pnl'] = round(pnl, 2)
                        record['exit_reason'] = exit_reason
                        record['closed_at'] = datetime.now(timezone.utc).isoformat()
                        if mfe > 0: record['mfe'] = round(mfe, 2)
                        if mae > 0: record['mae'] = round(mae, 2)
                        updated = True
                        
                        # Log summary
                        aligned = record.get('ai_aligned')
                        status = record['status']
                        logger.info(
                            f"[AI] Trade Result | {signal_id} → {status} | PnL:{pnl:+.2f} | "
                            f"AI {'✓ aligned' if aligned else '✗ conflict'} "
                            f"({record.get('ai_bias')} {record.get('ai_confidence')}%)"
                        )
                    
                    lines.append(json.dumps(record, default=str))
            
            if updated:
                with open(self._log_path, 'w') as f:
                    f.write('\n'.join(lines) + '\n')

        except Exception as e:
            logger.warning(f"[AI] Trade exit log error: {e}")
    
    def cleanup_stale_signals(self, timeout_minutes: int = 5):
        """
        SIGNAL_SENT ที่ไม่มี OPENED หลัง X นาที = EA_SKIPPED (v19.0)
        
        Args:
            timeout_minutes: จำนวนนาทีที่รอ ค่า default = 5
        """
        try:
            if not self._log_path.exists():
                return

            now = datetime.now(timezone.utc)
            lines = []
            cleaned = 0

            with open(self._log_path, 'r') as f:
                for line in f:
                    record = json.loads(line.strip())
                    if record.get('status') == 'SIGNAL_SENT':
                        sent_time = datetime.fromisoformat(record['timestamp'])
                        age_min = (now - sent_time).total_seconds() / 60
                        if age_min > timeout_minutes:
                            record['status'] = 'EA_SKIPPED'
                            record['skip_reason'] = f'No EA confirmation within {timeout_minutes}min'
                            cleaned += 1
                    # v25.0: Cleanup stale OPENED (EA closed but Python missed confirm)
                    elif record.get('status') == 'OPENED':
                        opened_at = record.get('opened_at') or record.get('timestamp', '')
                        if opened_at:
                            try:
                                opened_time = datetime.fromisoformat(opened_at)
                                age_hrs = (now - opened_time).total_seconds() / 3600
                            except (ValueError, TypeError):
                                age_hrs = 999  # v27.1: unparseable timestamp → force cleanup
                            if age_hrs > 2:  # OPENED > 2 hours = stale (trade already closed)
                                record['status'] = 'LOSS'
                                record['pnl'] = 0
                                record['exit_reason'] = 'STALE_CLEANUP'
                                record['closed_at'] = now.isoformat()
                                cleaned += 1
                        else:
                            # v27.1: no timestamp at all → orphan record → force cleanup
                            record['status'] = 'LOSS'
                            record['pnl'] = 0
                            record['exit_reason'] = 'STALE_CLEANUP_NO_TIMESTAMP'
                            record['closed_at'] = now.isoformat()
                            cleaned += 1
                    lines.append(json.dumps(record, default=str))

            if cleaned > 0:
                with open(self._log_path, 'w') as f:
                    f.write('\n'.join(lines) + '\n')
                logger.warning(f"[AI] Cleaned {cleaned} stale SIGNAL_SENT → EA_SKIPPED")
                
        except Exception as e:
            logger.warning(f"[AI] Cleanup error: {e}")
    
    def get_trade_summary(self) -> Dict:
        """
        สรุปผล trade ทั้งหมด (v19.0 - นับเฉพาะ OPENED + closed)
        
        Returns:
            {'total': N, 'wins': W, 'losses': L, 'win_rate': X%,
             'aligned_wins': AW, 'aligned_losses': AL,
             'conflict_wins': CW, 'conflict_losses': CL,
             'signal_sent': S, 'opened': O, 'skipped': K, 'pending': P}
        """
        try:
            if not self._log_path.exists():
                return {'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0}
            
            total = wins = losses = 0
            aligned_wins = aligned_losses = 0
            conflict_wins = conflict_losses = 0
            signal_sent = opened = skipped = pending = 0
            
            with open(self._log_path, 'r') as f:
                for line in f:
                    record = json.loads(line.strip())
                    status = record.get('status')
                    
                    # v19.0: นับเฉพาะ trade ที่เปิดจริง + ปิดแล้ว
                    if status == 'SIGNAL_SENT':
                        signal_sent += 1
                        continue
                    elif status == 'OPENED':
                        opened += 1
                        continue  # ยังไม่ปิด
                    elif status == 'EA_SKIPPED':
                        skipped += 1
                        continue  # ไม่นับใน accuracy
                    
                    # นับเฉพาะ WIN/LOSS/BE (trade ที่เปิดจริง + ปิดแล้ว)
                    if status not in ('WIN', 'LOSS', 'BE', 'OPENED'):
                        continue
                    
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
            
            # v26.4: pending = SIGNAL_SENT (รอ EA confirm)
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
                'pending': signal_sent,  # pending = signal sent but no EA confirmation yet
            }
        except Exception as e:
            logger.warning(f"[AI] Trade summary error: {e}")
            return {'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'signal_sent': 0, 'opened': 0, 'skipped': 0}
