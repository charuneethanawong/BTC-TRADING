"""
Smart Flow Manager - Institutional Grade v4.0
Patterns: LP (Liquidity Purge), DB (Defensive Block), DA (Delta Absorption)
Uses Binance Data + gives signals for all patterns passing Threshold
"""
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import asyncio
import json
import os
from pathlib import Path

from ..analysis.institutional_flow import InstitutionalFlowAnalyzer, WallClusterManager
from ..analysis.news_filter import NewsFilter
from ..utils.logger import get_logger
from ..data.binance_fetcher import BinanceDataFetcher
from ..analysis.ict import ICTAnalyzer
from ..analysis.order_flow import OrderFlowAnalyzer
from ..risk.position_flip_intelligence import PositionFlipIntelligence

logger = get_logger(__name__)


class SmartFlowManager:
    """
    Smart Flow Mode - Institutional Grade v4.0
    3 Patterns: LP (Liquidity Purge), DB (Defensive Block), DA (Delta Absorption)
    """
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # === Institutional Flow Analyzer ===
        self.institutional_analyzer = InstitutionalFlowAnalyzer(self.config.get('institutional_flow', {}))
        
        # === Section 2: DB IDI Engine - Wall Cluster Manager ===
        self.wall_cluster_manager = WallClusterManager(bucket_size=20.0)  # $20 buckets
        
        # === News Filter Gate (TASK 3) ===
        self.news_filter = NewsFilter(self.config)
        self.news_filter_enabled = self.config.get('smart_flow', {}).get('news_filter_enabled', True)
        self.news_filter_patterns = self.config.get('smart_flow', {}).get('news_filter_patterns', ['DB', 'DA'])
        
        # Thresholds สำหรับแต่ละ Pattern (Institutional naming - v6.0)
        # v6.0: Reduced DA threshold from 10 to 7 per architecture plan
        sf_config = self.config.get('smart_flow', {})
        self.thresholds = {
            'LP': sf_config.get('threshold_lp', 8),      # Liquidity Purge
            'DB': sf_config.get('threshold_db', 8),       # Defensive Block
            'DA': sf_config.get('threshold_da', 7)       # Delta Absorption (was 10 in v5.0)
        }
        
        # ATR Filter - Dynamic Threshold Scaling (v6.0 - reduced range)
        # v6.0: Reduced scale range from 0.7-1.3 to 0.8-1.1 for more stable thresholds
        self.atr_filter_enabled = sf_config.get('atr_filter_enabled', True)
        self.atr_scale_min = sf_config.get('atr_volatility_scale_min', 0.8)  # was 0.7
        self.atr_scale_max = sf_config.get('atr_volatility_scale_max', 1.1)  # was 1.3
        
        # Institutional Confluence Score (TASK 2)
        self.institutional_confluence_bonus = sf_config.get('institutional_confluence_bonus', 5)
        self.institutional_confluence_enabled = sf_config.get('institutional_confluence_enabled', True)
        
        # Price-distance based cooldown (v6.0 - reduced from $100 to $50)
        # v6.0: Allows faster signal generation in low volatility
        self.cooldown_price_distance = sf_config.get('cooldown_price_distance', 50)  # was 100
        
        # Track last trade prices per pattern (replaces time-based cooldown)
        # Using Institutional naming: LP, DB, DA
        self.last_trade_prices = {
            'LP': None,   # Liquidity Purge (formerly OI_MOM/SWEEP)
            'DB': None,   # Defensive Block (formerly WALL)
            'DA': None    # Delta Absorption (formerly CVD_REV/ZONE)
        }
        
        # Legacy time-based cooldowns (kept for state file compatibility, but not used)
        self.cooldowns = {
            'LP': 0,
            'DB': 0,
            'DA': 0
        }
        
        # === Session-Aware Filters ===
        # ปรับตามสภาพตลาดแต่ละ session
        # H-1: Reduced OI thresholds by 50% to allow more OI_MOM signals
        self.session_config = {
            'ASIA': {
                'pullback_pct': 0.20,      # 20% - ตลาด ranging, รอน้อย
                'volume_threshold': 1.2,    # Volume ต่ำ
                'oi_threshold': 0.02,      # v6.0: OI ต่ำ (จาก 0.03)
            },
            'LONDON': {
                'pullback_pct': 0.25,      # 25% - เริ่มคึกคาม
                'volume_threshold': 1.3,    # Volume ปานกลาง
                'oi_threshold': 0.03,      # v6.0: OI ปานกลาง (จาก 0.04)
            },
            'LONDON-NY': {
                'pullback_pct': 0.30,      # 30% - High volatility, รอมาก
                'volume_threshold': 1.3,    # v6.0: Volume ปานกลาง (จาก 1.5)
                'oi_threshold': 0.03,       # v6.0: OI ปานกลาง (จาก 0.05)
            },
            'NY': {
                'pullback_pct': 0.30,      # 30% - High volatility
                'volume_threshold': 1.3,    # v6.0: Volume ปานกลาง (จาก 1.5)
                'oi_threshold': 0.03,       # v6.0: OI ปานกลาง (จาก 0.05)
            },
            'ASIA-LATE': {
                'pullback_pct': 0.20,      # 20% - ตลาด ranging
                'volume_threshold': 1.2,    # Volume ต่ำ
                'oi_threshold': 0.02,       # v6.0: OI ต่ำ (จาก 0.03)
            }
        }
        
        # Binance Data Fetcher
        self.binance_fetcher = BinanceDataFetcher(
            symbol=config.get('trading_symbol', 'BTCUSDT'),
            testnet=config.get('testnet', False)
        )
        
        # ICT Analyzer
        self.ict_analyzer = ICTAnalyzer(config)
        
        # Order Flow Analyzer (for CVD Slope analysis)
        self.order_flow = OrderFlowAnalyzer(config)
        
        # Section 27.1: Position Flip Intelligence
        self.flip_intelligence = PositionFlipIntelligence(config)
        
        self.last_trade_time = {
            'LP': None,   # Liquidity Purge
            'DB': None,   # Defensive Block
            'DA': None    # Delta Absorption
        }
        
        # === Wall Stability Tracking ===
        # Store wall history: {price: {'first_seen': timestamp, 'size': volume}}
        self.wall_history = {
            'bid': {},   
            'ask': {}
        }
        # C-4: Reduced from 30-60s to 10s for more wall signals
        self.wall_stability_seconds = 10  # Default: Wall must be present for 10s (was 30s)
        self.wall_stability_large_seconds = 10  # Large walls ($1M+) now also 10s (was 60s)
        self.large_wall_threshold_usd = 1_000_000  # $1M threshold for extended stability
        
        # Section 56 Bug #8 FIX: Proximity threshold for DB entry
        # Wall must be within this % of current price for entry
        self.proximity_threshold_pct = 0.002  # 0.2% (was 0.02%)
        
        # --- Log Throttling ---
        self.last_status_log_time = datetime.min.replace(tzinfo=timezone.utc)
        self.last_active_patterns = []
        self.throttled_logs = {}
        
        
         # key -> datetime
        self._last_no_signal_log = 0
        self._no_signal_log_interval = 60  # seconds
        
        # === Section 24.3: Active Walls Tracking with Timestamps ===
        # Store wall start time for stability check: {price: {'first_seen': timestamp, 'size': volume, 'initial_size': volume}}
        self._active_walls = {
            'bid': {},
            'ask': {}
        }
        
        # === Section 24.4: Adaptive Regime Strategy Configuration ===
        # Dynamic thresholds per regime (Institutional naming)
        self.regime_config = {
            'RANGING': {
                'da_threshold': 8,
                # Section 56 Bug #4 FIX: LP threshold from 12 to 8
                # Ranging markets are the norm for BTC - 12 was too strict
                'lp_threshold': 8,  # was 12
                'db_threshold': 8,  # Bug #4 FIX: also reduced from 9 to 8
                'tp_multiplier': 1.3,  # ATR multiplier for TP
                'sl_multiplier': 1.2   # ATR multiplier for SL
            },
            'EXPANDING': {
                'da_threshold': 12,
                'lp_threshold': 8,
                'db_threshold': 10,
                'tp_multiplier': 2.8,
                'sl_multiplier': 1.5
            },
            'VOLATILE': {
                'da_threshold': 10,
                # Section 56 Bug #4 FIX: LP threshold from 11 to 9 (consistent with other regimes)
                'lp_threshold': 9,  # was 11
                'db_threshold': 10,  # was 11
                'tp_multiplier': 2.0,
                'sl_multiplier': 1.5
            },
            'NORMAL': {
                'da_threshold': self.thresholds.get('DA', 10),
                'lp_threshold': self.thresholds.get('LP', 8),
                'db_threshold': self.thresholds.get('DB', 8),
                'tp_multiplier': 2.0,
                'sl_multiplier': 1.3
            }
        }
        
        # DA (Delta Absorption) threshold
        self.da_cvd_threshold = sf_config.get('da_cvd_threshold', 0.15)
        
        # Section 24.2: OI Velocity threshold (v6.0: reduced from 0.03 to 0.02)
        self.oi_velocity_threshold = sf_config.get('oi_velocity_threshold', 0.02)
        
        # Section 24.3: Wall significance threshold
        # Section 56 Bug #6 FIX: Changed from 3.0 to 0.005 (wall must be >= 0.5% of hourly volume)
        # Previous threshold 3.0 was impossible due to formula unit error
        self.wall_significance_threshold = sf_config.get('wall_significance_threshold', 0.005)
        
        # Wall stability time requirement
        # Section 56 Bug #7 FIX: Reduced from 15s to 5s for M5 scalping
        # Wall must be present for 5 seconds before being considered stable
        self.wall_stability_time_seconds = 5  # was 15
        
        # Whale confluence threshold (500K USD)
        self.whale_confluence_threshold = 500_000
        
        # Retest watcher state
        self._retest_waiting = {}  # {pattern_type: {'entry_price': price, 'signal_bar_high': high, 'signal_bar_low': low}}
        
        # Load state on init
        self.load_state()
        
        logger.info(f"SmartFlowManager initialized with thresholds: {self.thresholds}")

    def save_state(self, filepath: str = "data/sf_state.json") -> bool:
        """
        Save smart flow state to JSON file.
        
        Architecture Plan Section 36.2: Last Price Recovery
        Save last_price_cache for DER continuity after restart.
        """
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            state = {
                'last_trade_prices': {k: v for k, v in self.last_trade_prices.items()},
                'last_trade_time': {k: v.isoformat() if isinstance(v, datetime) else None for k, v in self.last_trade_time.items()},
                # Section 36.2: Save last_price_cache for DER calculation continuity
                'last_price_cache': {
                    'last_price': getattr(self, 'last_price', 0),
                    'last_cvd': getattr(self, 'last_cvd', 0),
                    'last_price_movement': getattr(self, 'last_price_movement', 0),
                    'saved_at': datetime.now(timezone.utc).isoformat()
                }
            }
            
            with open(filepath, 'w') as f:
                json.dump(state, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving SmartFlowManager state: {e}")
            return False

    def load_state(self, filepath: str = "data/sf_state.json") -> bool:
        """
        Load smart flow state from JSON file.
        
        Architecture Plan Section 36.2: Last Price Recovery
        Load last_price_cache for DER continuity after restart.
        """
        if not os.path.exists(filepath):
            return False
            
        try:
            with open(filepath, 'r') as f:
                state = json.load(f)
            
            valid_keys = {'LP', 'DB', 'DA'}
            
            # Load last_trade_prices (new price-distance based)
            loaded_prices = state.get('last_trade_prices', {})
            for k, v in loaded_prices.items():
                if k in valid_keys and v is not None:
                    self.last_trade_prices[k] = float(v)
            
            # Legacy: Load last_trade_time (for backward compatibility)
            loaded_times = state.get('last_trade_time', {})
            now = datetime.now(timezone.utc)
            
            for k, v in loaded_times.items():
                if k in valid_keys and isinstance(v, str):
                    loaded_time = datetime.fromisoformat(v)
                    # Only load if within reasonable time, otherwise clear
                    if (now - loaded_time).total_seconds() < 3600:  # 1 hour
                        self.last_trade_time[k] = loaded_time
            
            # Section 36.2: Load last_price_cache for DER calculation continuity
            price_cache = state.get('last_price_cache', {})
            if price_cache:
                # Only restore if cache is recent (within 5 minutes)
                saved_at = price_cache.get('saved_at')
                if saved_at:
                    cache_time = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                    if (now - cache_time).total_seconds() < 300:  # 5 minutes
                        self.last_price = price_cache.get('last_price', 0)
                        self.last_cvd = price_cache.get('last_cvd', 0)
                        self.last_price_movement = price_cache.get('last_price_movement', 0)
                        logger.info(f"📊 DER: Loaded price cache - last_price={self.last_price}, last_cvd={self.last_cvd}")
                    else:
                        logger.debug("📊 DER: Price cache too old, starting fresh")
            
            logger.info(f"SmartFlowManager state loaded: prices={self.last_trade_prices}")
            return True
        except Exception as e:
            logger.error(f"Error loading SmartFlowManager state: {e}")
            return False

    def _get_institutional_bias(self, candles, binance_data) -> str:
        """Analyze 30-min CVD slope and Structure to determine the 'Side' institutions are on."""
        if len(candles) < 30: return 'NEUTRAL'
        
        # 1. ตรวจสอบความชันของ CVD (ย้อนหลัง 15-30 แท่ง)
        recent_cvd = binance_data.get('cvd', {}).get('cvd_series', [])
        if len(recent_cvd) >= 15:
            cvd_diff = recent_cvd[-1] - recent_cvd[-15]
            price_diff = candles.iloc[-1]['close'] - candles.iloc[-15]['close']
            
            # กฎทอง: ถ้า CVD ไหลลงรุนแรง แต่ราคาขยับลงนิดเดียว = สถาบันเก็บของ (LONG Bias)
            if cvd_diff < -100 and price_diff > -50: return 'BUY'
            # ถ้า CVD พุ่งแรง แต่ราคาขยับขึ้นนิดเดียว = สถาบันปล่อยของ (SELL Bias)
            if cvd_diff > 100 and price_diff < 50: return 'SELL'
            
        # 2. ตรวจสอบตำแหน่งเทียบกับสวิง 30 นาที
        recent_high = candles['high'].tail(30).max()
        recent_low = candles['low'].tail(30).min()
        mid_point = (recent_high + recent_low) / 2
        current_price = candles.iloc[-1]['close']
        
        if current_price < mid_point: return 'BUY'
        if current_price > mid_point: return 'SELL'
        
        return 'NEUTRAL'

    async def scan_patterns(
        self,
        candles: pd.DataFrame,
        current_price: float,
        p1_data: Dict,
        htf_data: Dict = None,
        phase1_score: int = None,
        liquidity_wall_data: Dict = None
    ) -> List[Dict]:
        """
        Scan ทั้ง 3 Patterns พร้อมกัน (Async)
        v4.0: Institutional Grade with LP/DB/DA patterns and News Filter Gate
        
        Patterns:
        - LP: Liquidity Purge (formerly SWEEP/OI_MOM)
        - DB: Defensive Block (formerly WALL)
        - DA: Delta Absorption (formerly CVD_REV/ZONE)
        
        return: List ของสัญญาณที่ผ่าน Threshold (อาจมี 0-3 ตัว)
        """
        signals = []
        
        # === Section 6: News Management Policy ===
        # Strategist (Python) sends signals continuously without blocking on news
        # Executioner (MT5 EA) is the final gate using News Filter
        # News filter check is kept for informational logging only
        news_paused, news_event = self._check_news_filter()
        if news_paused:
            # Log for information only - DO NOT block signals
            # EA will handle news filtering at execution time
            self._log_throttled(
                'news_filter_info',
                f"📰 NEWS INFO: {news_event.get('title', 'High Impact News')} - Signals will be sent (EA filters at execution)",
                level='debug',
                interval_minutes=5
            )
        
        # 1. ดึงข้อมูล Binance (Async - non-blocking)
        binance_data = await self._fetch_binance_data_async()
        
        # 2. วิเคราะห์ ICT
        ict_summary = self.ict_analyzer.get_ict_summary(candles, current_price, p1_data)
        
        # 2.1 Add liquidity_wall_data to ict_summary for pattern evaluation
        if liquidity_wall_data:
            ict_summary['liquidity_wall'] = liquidity_wall_data
        
        # === Section 2.1: M5 Settlement Guard ===
        # Section 2.1: Wait for candle to progress at least 60% (3 minutes) before sending signal
        candle_ready, candle_progress = self._check_candle_progress(candles)
        if not candle_ready:
            self._log_throttled(
                'candle_progress_wait',
                f"⏳ CANDLE SETTLING: Waiting for M5 candle to progress ({candle_progress:.0f}% complete)",
                level='debug',
                interval_minutes=1
            )
            return []  # Return empty if candle not ready

        # === Section 2: Target Focus Engine ===
        # Get active liquidity magnets (PDH/PDL, Asian Range, M15 Fractals)
        magnets = self.ict_analyzer.get_active_magnets(candles, current_price)
        ict_summary['magnets'] = magnets
        
        # 2.2 Calculate Real-time P1 Score if not provided (Fallback)
        if phase1_score is None or phase1_score == 0:
            phase1_score = self._calculate_real_time_p1_score(p1_data, binance_data)
        
        # === Section 24.4: Detect Regime for Dynamic Thresholds ===
        regime = self._detect_regime_v3(candles)
        regime_thresholds = self._get_regime_thresholds(regime)
        
        # === TASK 3: ATR Filter - Scale thresholds based on volatility ===
        if self.atr_filter_enabled:
            atr_scale = self._calculate_atr_filter_scale(candles)
        else:
            atr_scale = 1.0
        
        # Get thresholds from regime config (with ATR scaling)
        lp_threshold = int(regime_thresholds.get('lp_threshold', self.thresholds['LP']) * atr_scale)
        db_threshold = int(regime_thresholds.get('db_threshold', self.thresholds['DB']) * atr_scale)
        da_threshold = int(regime_thresholds.get('da_threshold', self.thresholds['DA']) * atr_scale)
        
        # 0. Check pending retests from watchdogs
        retest_signals = self._check_pending_retests(current_price, binance_data)
        for rs in retest_signals:
            signals.append(rs)

        # 3. ประเมินแต่ละ Pattern (เก็บข้อมูลคะแนนไว้ Log แม้ไม่ผ่าน Threshold)
        # Pattern 1: LP (Liquidity Purge)
        lp_result = self._evaluate_liquidity_purge_pattern(
            candles, current_price, p1_data, ict_summary, htf_data, binance_data
        )
        lp_score = lp_result['score'] if lp_result else self._get_pattern_score('LP', candles, current_price, p1_data, ict_summary, htf_data, binance_data)
        lp_filtered = lp_result is None
        # Use regime-based threshold for LP
        if lp_result and lp_result.get('score', 0) >= lp_threshold:
            if not lp_result.get('wait_for_retest', False):
                if self._can_trade_pattern('LP', current_price):
                    # === v6.0: Directional Alignment Filter (SOFT) ===
                    direction = lp_result.get('direction', 'LONG')
                    is_aligned, alignment_reason = self.ict_analyzer.check_directional_alignment(direction, magnets, current_price)
                    if is_aligned:
                        # v6.0: Reduced bonus from +2 to +1
                        nearest_target = magnets.get('nearest_buy') if direction == 'SELL' else magnets.get('nearest_sell')
                        if nearest_target:
                            target_bonus = 1  # v6.0: was 2
                            lp_result['score'] = lp_result.get('score', 0) + target_bonus
                            lp_result['target_bonus'] = target_bonus
                            lp_result['target_type'] = nearest_target.get('type', 'UNKNOWN')
                            lp_result['target_distance'] = nearest_target.get('distance_pct', 0)
                            if 'reasons' not in lp_result:
                                lp_result['reasons'] = []
                            lp_result['reasons'].append(f"Target_Bonus:+{target_bonus}({nearest_target.get('type', 'UNKNOWN')})")
                            logger.debug(f"🎯 LP Target Bonus: +{target_bonus} aligned with {nearest_target.get('type', 'UNKNOWN')} at {nearest_target.get('distance_pct', 0):.2f}%")
                        signals.append(lp_result)
                    else:
                        # v6.0: Soft penalty instead of hard block
                        lp_result['score'] = lp_result.get('score', 0) - 1
                        lp_result['alignment_penalty'] = -1
                        if lp_result['score'] >= lp_threshold:
                            logger.debug(f"⚠️ LP: Alignment penalty applied, score {lp_result['score']} still above threshold")
                            signals.append(lp_result)
                        else:
                            logger.debug(f"🚫 LP BLOCKED: {alignment_reason} (score {lp_result['score']} < {lp_threshold})")
                            lp_filtered = True
            else:
                logger.info(f"⏳ LP: Entry criteria met, but waiting for retest @ {lp_result.get('entry_price', 0):.2f}")
        
        # Pattern 2: DB (Defensive Block)
        # Section 6: News filter removed - EA handles filtering at execution
        db_result = self._evaluate_defensive_block_pattern(
            candles, current_price, p1_data, ict_summary, htf_data, binance_data
        )
        db_score = db_result['score'] if db_result else self._get_pattern_score('DB', candles, current_price, p1_data, ict_summary, htf_data, binance_data)
        db_filtered = db_result is None
        # Use regime-based threshold for DB
        if db_result and db_result.get('score', 0) >= db_threshold:
            if self._can_trade_pattern('DB', current_price):
                # === v6.0: Directional Alignment Filter (SOFT) ===
                direction = db_result.get('direction', 'LONG')
                is_aligned, alignment_reason = self.ict_analyzer.check_directional_alignment(direction, magnets, current_price)
                if is_aligned:
                    # v6.0: Reduced bonus from +2 to +1
                    nearest_target = magnets.get('nearest_buy') if direction == 'SELL' else magnets.get('nearest_sell')
                    if nearest_target:
                        target_bonus = 1  # v6.0: was 2
                        db_result['score'] = db_result.get('score', 0) + target_bonus
                        db_result['target_bonus'] = target_bonus
                        db_result['target_type'] = nearest_target.get('type', 'UNKNOWN')
                        db_result['target_distance'] = nearest_target.get('distance_pct', 0)
                        if 'reasons' not in db_result:
                            db_result['reasons'] = []
                        db_result['reasons'].append(f"Target_Bonus:+{target_bonus}({nearest_target.get('type', 'UNKNOWN')})")
                        logger.debug(f"🎯 DB Target Bonus: +{target_bonus} aligned with {nearest_target.get('type', 'UNKNOWN')} at {nearest_target.get('distance_pct', 0):.2f}%")
                    signals.append(db_result)
                else:
                    # v6.0: Soft penalty instead of hard block
                    db_result['score'] = db_result.get('score', 0) - 1
                    db_result['alignment_penalty'] = -1
                    if db_result['score'] >= db_threshold:
                        logger.debug(f"⚠️ DB: Alignment penalty applied, score {db_result['score']} still above threshold")
                        signals.append(db_result)
                    else:
                        logger.debug(f"🚫 DB BLOCKED: {alignment_reason} (score {db_result['score']} < {db_threshold})")
                        db_filtered = True
        
        # Pattern 3: DA (Delta Absorption)
        # Section 6: News filter removed - EA handles filtering at execution
        da_result = self._evaluate_delta_absorption_pattern(
            candles, current_price, p1_data, ict_summary, htf_data, binance_data
        )
        da_score = da_result['score'] if da_result else self._get_pattern_score('DA', candles, current_price, p1_data, ict_summary, htf_data, binance_data)
        da_filtered = da_result is None
        # Use regime-based threshold for DA
        if da_result and da_result.get('score', 0) >= da_threshold:
            can_trade = self._can_trade_pattern('DA', current_price)
            if can_trade:
                # === v6.0: Directional Alignment Filter (SOFT) ===
                direction = da_result.get('direction', 'LONG')
                is_aligned, alignment_reason = self.ict_analyzer.check_directional_alignment(direction, magnets, current_price)
                if is_aligned:
                    # v6.0: Reduced bonus from +2 to +1
                    nearest_target = magnets.get('nearest_buy') if direction == 'SELL' else magnets.get('nearest_sell')
                    if nearest_target:
                        target_bonus = 1  # v6.0: was 2
                        da_result['score'] = da_result.get('score', 0) + target_bonus
                        da_result['target_bonus'] = target_bonus
                        da_result['target_type'] = nearest_target.get('type', 'UNKNOWN')
                        da_result['target_distance'] = nearest_target.get('distance_pct', 0)
                        if 'reasons' not in da_result:
                            da_result['reasons'] = []
                        da_result['reasons'].append(f"Target_Bonus:+{target_bonus}({nearest_target.get('type', 'UNKNOWN')})")
                        logger.debug(f"🎯 DA Target Bonus: +{target_bonus} aligned with {nearest_target.get('type', 'UNKNOWN')} at {nearest_target.get('distance_pct', 0):.2f}%")
                    signals.append(da_result)
                else:
                    # v6.0: Soft penalty instead of hard block
                    da_result['score'] = da_result.get('score', 0) - 1
                    da_result['alignment_penalty'] = -1
                    if da_result['score'] >= da_threshold:
                        logger.debug(f"⚠️ DA: Alignment penalty applied, score {da_result['score']} still above threshold")
                        signals.append(da_result)
                    else:
                        logger.debug(f"🚫 DA BLOCKED: {alignment_reason} (score {da_result['score']} < {da_threshold})")
                        da_filtered = True
        
        # 4. Log สรุปแสดง Score ทุก Pattern (ไม่ว่าจะผ่านหรือไม่)
        # Show "(filtered)" when pattern was rejected by gates
        def format_score(name, score, threshold, filtered):
            suffix = "(filtered)" if filtered else ""
            return f"{name}:{score}/{threshold}{suffix}"
        
        # Use regime info in logging
        regime_str = f"[{regime}]"
        atr_str = f"(ATR:{atr_scale:.2f})" if self.atr_filter_enabled else ""
        status_str = f"{format_score('LP', lp_score, lp_threshold, lp_filtered)} {format_score('DB', db_score, db_threshold, db_filtered)} {format_score('DA', da_score, da_threshold, da_filtered)}"
        phase1_str = f" | P1_Flow:{phase1_score}/8"
        
        # === Section 37.3: Log Hygiene & Focus ===
        # Reduce status log frequency: only log every 5 minutes OR when state changes
        # Add Alert Highlighting for institutional grade signals
        now = datetime.now(timezone.utc)
        pattern_names = sorted([s['pattern_type'] for s in signals]) if signals else []
        seconds_since_log = (now - self.last_status_log_time).total_seconds()
        
        state_changed = pattern_names != self.last_active_patterns
        # Changed from 60 seconds to 300 seconds (5 minutes)
        heartbeat_needed = (not signals and seconds_since_log >= 300)
        active_update_needed = (signals and seconds_since_log >= 300)
        
        # Check for institutional grade signals (Alert Highlighting)
        institutional_signals = [s for s in signals if s.get('institutional_grade', False)] if signals else []
        
        if state_changed or heartbeat_needed or active_update_needed:
            icon = "✅" if signals else "🔍"
            logger.info(f"{icon} Smart Flow {regime_str}{atr_str}: {status_str}{phase1_str} | Active: {pattern_names}")
            
            self.last_status_log_time = now
            self.last_active_patterns = pattern_names
        
        # === Section 37.3: Alert Highlighting for Institutional Grade ===
        # Highlight when institutional grade signals are found
        if institutional_signals:
            for sig in institutional_signals:
                score = sig.get('score', 0)
                pattern = sig.get('pattern_type', 'UNKNOWN')
                logger.warning(f"🏛️ INSTITUTIONAL GRADE: {pattern} detected with Score={score} | {regime_str}")
        
        # === MINIMUM SL GUARD (SAFETY) ===
        for sig in signals:
            entry = sig.get('entry_price', current_price)
            sl = sig.get('sl_boundary')
            
            # Ensure at least $100 SL buffer for BTC
            if sl is not None:
                min_dist = 100.0
                if sig.get('direction') == 'LONG':
                    if entry - sl < min_dist:
                        sig['sl_boundary'] = entry - min_dist
                        logger.debug(f"⚖️ SL GUARD: Tightened LONG SL to ${sig['sl_boundary']:.2f}")
                else: # SHORT
                    if sl - entry < min_dist:
                        sig['sl_boundary'] = entry + min_dist
                        logger.debug(f"⚖️ SL GUARD: Tightened SHORT SL to ${sig['sl_boundary']:.2f}")
        
        # === TASK 2: Institutional Confluence Score ===
        if self.institutional_confluence_enabled and signals:
            for sig in signals:
                if self._check_institutional_confluence(sig, binance_data, p1_data):
                    sig['score'] += self.institutional_confluence_bonus
                    sig['institutional_confluence'] = True
                    logger.info(f"🏛️ INSTITUTIONAL CONFLUENCE: +{self.institutional_confluence_bonus} bonus for {sig['pattern_type']}")
        
        # === Section 4: Multi-Signal Trigger Policy ===
        # NO ARBITRATOR: Send ALL signals that pass the score threshold
        # Each signal will have pattern_type, institutional_grade, and required_rr metadata
        # The EA will handle multiple positions according to MaxPositions setting
        
        # === Section 2.3: Signal Spacing Guard ===
        # Prevent signal clustering: minimum $50 price distance between signals
        min_spacing_usd = 50.0
        filtered_signals = []
        sent_prices = []
        
        # Get recent traded prices (last 5 minutes)
        recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        for pattern in ['LP', 'DB', 'DA']:
            last_time = self.last_trade_time.get(pattern)
            if last_time and last_time > recent_cutoff:
                sent_prices.append(self.last_trade_prices.get(pattern, 0))
        
        for signal in signals:
            entry_price = signal.get('entry_price', 0)
            if entry_price <= 0:
                filtered_signals.append(signal)
                continue
            
            # Check distance from recent signals
            too_close = False
            for recent_price in sent_prices:
                if recent_price > 0:
                    price_diff = abs(entry_price - recent_price)
                    if price_diff < min_spacing_usd:
                        logger.debug(f"🚫 SIGNAL FILTERED: {signal['pattern_type']} @ {entry_price:.2f} too close to recent @ {recent_price:.2f} (diff: ${price_diff:.2f} < ${min_spacing_usd})")
                        too_close = True
                        break
            
            if not too_close:
                filtered_signals.append(signal)
                sent_prices.append(entry_price)
        
        signals = filtered_signals
        
        if len(signals) > 1:
            signal_types = [s['pattern_type'] for s in signals]
            scores = [s.get('score', 0) for s in signals]
            logger.info(f"📊 MULTI-SIGNAL: {len(signals)} patterns triggered - {signal_types} (scores: {scores})")
        
        # === SECTION 7 [TASK 2]: Counter-Trend Quality Filter ===
        # Apply counter-trend quality validation to all signals
        ct_filtered_signals = []
        ct_blocked_count = {'LP': 0, 'DB': 0, 'DA': 0}
        
        for signal in signals:
            pattern_type = signal.get('pattern_type', 'DA')
            
            # Check counter-trend quality
            pass_quality, quality_reason, ct_details = self._check_counter_trend_quality(
                signal=signal,
                htf_data=htf_data,
                binance_data=binance_data,
                p1_data=p1_data
            )
            
            # Add counter-trend details to signal metadata
            signal['counter_trend'] = ct_details
            
            if pass_quality:
                ct_filtered_signals.append(signal)
            else:
                # Log blocked counter-trend signal
                direction = signal.get('direction', 'UNKNOWN')
                score = signal.get('score', 0)
                htf_trend = ct_details.get('htf_trend', 'NEUTRAL')
                quality_level = ct_details.get('quality_level', 'UNKNOWN')
                
                self._log_throttled(
                    f'ct_blocked_{pattern_type}',
                    f"🚫 COUNTER-TREND BLOCKED: {pattern_type} {direction} @ {signal.get('entry_price', 0):.2f} "
                    f"| HTF:{htf_trend} | Quality:{quality_level} | Score:{score} "
                    f"| Reason:{quality_reason}",
                    level='debug',
                    interval_minutes=5
                )
                ct_blocked_count[pattern_type] += 1
        
        signals = ct_filtered_signals
        
        # Log summary of blocked counter-trend signals
        if sum(ct_blocked_count.values()) > 0:
            blocked_summary = ", ".join([f"{k}:{v}" for k, v in ct_blocked_count.items() if v > 0])
            logger.info(f"🔒 COUNTER-TREND FILTER: Blocked {sum(ct_blocked_count.values())} signals ({blocked_summary})")
        
        return signals

    def _get_volume_ratio(self, binance_data: Dict, p1_data: Dict) -> float:
        """Get volume ratio - prefer Binance real volume over MT5 tick volume.
        
        Binance: Real trading volume (quote asset volume in USDT)
        MT5: Tick volume (number of price changes) - NOT ACCURATE
        """
        # Prefer Binance real volume
        binance_volume = binance_data.get('volume', {})
        binance_ratio = binance_volume.get('volume_ratio', 1)
        
        if binance_ratio and binance_ratio > 0:
            return binance_ratio
        
        # Fallback to P1 (MT5 tick volume) if Binance unavailable
        return p1_data.get('volume_ratio', 1)
    
    def _calculate_real_time_p1_score(self, p1_data: Dict, binance_data: Dict) -> int:
        """คำนวณคะแนน Flow รายนาทีจาก OI, CVD และ Volume (Real-time Fallback)"""
        score = 0
        
        # 1. OI Surge (0-3)
        oi_change = binance_data.get('oi', {}).get('openInterestChange', 0)
        if oi_change > 0.3: score += 3
        elif oi_change > 0.1: score += 2
        elif oi_change > 0: score += 1
        
        # 2. CVD Intensity (0-3) - check both p1_data and binance_data
        cvd_delta = abs(p1_data.get('cvd_delta', 0))
        if cvd_delta > 0.8: score += 3
        elif cvd_delta > 0.4: score += 2
        elif cvd_delta > 0.1: score += 1
        
        # 3. Volume Energy (0-2) - Use Binance real volume
        vol_ratio = self._get_volume_ratio(binance_data, p1_data)
        if vol_ratio > 2.0: score += 2
        elif vol_ratio > 1.2: score += 1
        
        return score
    
    def _can_trade_pattern(self, pattern_type: str, current_price: float = None) -> bool:
        """Check if pattern can trade based on price-distance (not time).
        
        Price-Distance Based Cooldown:
        - Pattern can trade when price has moved $X away from last trade
        - This is market-aware: respects volatility, not arbitrary time
        - High volatility = faster cooldown (price moves quickly)
        - Low volatility = slower cooldown (price moves slowly)
        """
        if current_price is None:
            current_price = self.binance_fetcher.get_current_price() if hasattr(self.binance_fetcher, 'get_current_price') else 0
        
        last_price = self.last_trade_prices.get(pattern_type)
        if last_price is None:
            logger.debug(f"🔍DEBUG COOLDOWN: {pattern_type} - No previous trade, can trade")
            return True
        
        price_distance = abs(current_price - last_price)
        can_trade = price_distance >= self.cooldown_price_distance
        
        if can_trade:
            logger.debug(f"✓ COOLDOWN RESET: {pattern_type} - Price moved ${price_distance:.0f} >= ${self.cooldown_price_distance}")
        else:
            logger.debug(f"⏳ COOLDOWN WAIT: {pattern_type} - Price moved ${price_distance:.0f} < ${self.cooldown_price_distance}")
        
        return can_trade
    
    def record_trade(self, pattern_type: str, entry_price: float, direction: str = 'LONG'):
        """Record trade for price-distance based cooldown."""
        self.last_trade_prices[pattern_type] = entry_price
        self.save_state()
        logger.info(f"📝 TRADE RECORDED: {pattern_type} @ {entry_price:.2f} - Price-distance cooldown reset")

    def _log_throttled(self, key: str, message: str, level: str = 'debug', interval_minutes: int = 5):
        """Helper to log messages with throttling."""
        now = datetime.now(timezone.utc)
        last_log = self.throttled_logs.get(key, datetime.min.replace(tzinfo=timezone.utc))
        
        if (now - last_log).total_seconds() / 60 >= interval_minutes:
            if level == 'info':
                logger.info(message)
            elif level == 'debug':
                logger.debug(message)
            elif level == 'warning':
                logger.warning(message)
            self.throttled_logs[key] = now

    def _check_candle_progress(self, candles: pd.DataFrame) -> Tuple[bool, float]:
        """
        Section 2.1: M5 Settlement Guard.
        
        Checks if the current M5 candle has progressed enough (60% minimum).
        This ensures institutional confirmation before sending signals.
        
        Args:
            candles: Price candles DataFrame
            
        Returns:
            Tuple[bool, float]: (is_ready, progress_percentage)
        """
        if candles is None or len(candles) < 1:
            return True, 100.0  # Default to ready if no data
        
        try:
            last_candle = candles.iloc[-1]
            
            # Get candle open time from data
            # Assume candles are M5 (5-minute candles)
            candle_open = last_candle.get('open_time', 0)
            candle_close = last_candle.get('close_time', 0)
            
            if candle_close <= candle_open:
                # Fallback: estimate progress from price movement
                candle_range = abs(last_candle.get('high', 0) - last_candle.get('low', 0))
                price_so_far = abs(last_candle.get('close', 0) - last_candle.get('open', 0))
                
                if candle_range > 0:
                    progress = (price_so_far / candle_range) * 100
                else:
                    progress = 50.0  # Default to 50% if no range
            else:
                # Calculate actual progress based on time
                now = datetime.now(timezone.utc)
                candle_duration = (candle_close - candle_open).total_seconds()
                elapsed = (now.timestamp() - candle_open.timestamp()) if candle_open else 0
                
                if candle_duration > 0:
                    progress = min(100.0, (elapsed / candle_duration) * 100)
                else:
                    progress = 50.0
            
            # v6.0: Reduced from 60% to 40% (2 minutes instead of 3)
            # Allows faster entry while still waiting for candle confirmation
            min_progress = 40.0
            is_ready = progress >= min_progress
            
            return is_ready, progress
            
        except Exception as e:
            logger.debug(f"Candle progress check error: {e}")
            return True, 100.0  # Default to ready on error
    
    def _get_pattern_score(self, pattern_type: str, candles, price, p1, ict, htf, binance_data) -> int:
        """Calculate pattern score without full evaluation (for logging)
        
        Institutional naming:
        - LP: Liquidity Purge (formerly OI_MOM/SWEEP)
        - DB: Defensive Block (formerly WALL)
        - DA: Delta Absorption (formerly CVD_REV/ZONE)
        """
        score = 0
        if pattern_type == 'LP':
            signal = self._evaluate_liquidity_purge_pattern(candles, price, p1, ict, htf, binance_data)
            score = signal['score'] if signal else 0
        elif pattern_type == 'DB':
            signal = self._evaluate_defensive_block_pattern(candles, price, p1, ict, htf, binance_data)
            score = signal['score'] if signal else 0
        elif pattern_type == 'DA':
            signal = self._evaluate_delta_absorption_pattern(candles, price, p1, ict, htf, binance_data)
            score = signal['score'] if signal else 0
        
        return score
    
    def _check_news_filter(self) -> Tuple[bool, Optional[Dict]]:
        """
        TASK 3: Check if trading should be paused due to high-impact news.
        
        Returns:
            Tuple[bool, Optional[Dict]]: (is_paused, news_event)
        """
        if not self.news_filter_enabled:
            return False, None
        return self.news_filter.is_news_paused()
    
    def _calculate_atr_filter_scale(self, candles: pd.DataFrame) -> float:
        """
        TASK 3: Calculate ATR-based threshold scaling factor.
        
        High volatility = higher thresholds (less signals)
        Low volatility = lower thresholds (more signals)
        
        Returns:
            float: Scale factor between atr_scale_min and atr_scale_max
        """
        if len(candles) < 14:
            return 1.0
        
        try:
            high = candles['high'].values
            low = candles['low'].values
            close = candles['close'].values
            
            # Calculate True Range
            tr = np.maximum(high[1:] - low[1:], 
                           np.maximum(np.abs(high[1:] - close[:-1]), 
                                      np.abs(low[1:] - close[:-1])))
            
            # ATR(14)
            atr_14 = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
            
            # ATR(200) for comparison
            atr_200 = np.mean(tr[-200:]) if len(tr) >= 200 else np.mean(tr)
            
            if atr_200 <= 0:
                return 1.0
            
            # ATR ratio indicates volatility
            atr_ratio = atr_14 / atr_200
            
            # Scale: High ATR ratio = higher scale (more conservative)
            # Low ATR ratio = lower scale (more aggressive)
            # Normal range: atr_ratio 0.75-1.4 maps to scale 0.7-1.3
            if atr_ratio < 0.75:
                scale = self.atr_scale_min
            elif atr_ratio > 1.4:
                scale = self.atr_scale_max
            else:
                # Linear interpolation
                normalized = (atr_ratio - 0.75) / (1.4 - 0.75)
                scale = self.atr_scale_min + (self.atr_scale_max - self.atr_scale_min) * normalized
            
            return max(self.atr_scale_min, min(self.atr_scale_max, scale))
            
        except Exception as e:
            logger.debug(f"ATR filter scale calculation error: {e}")
            return 1.0
    
    def _check_institutional_confluence(self, signal: Dict, binance_data: Dict, p1_data: Dict) -> bool:
        """
        TASK 2: Check for Institutional Confluence (+5 bonus).
        
        Returns True if micro-flow signals are 100% aligned:
        - OI increasing in direction of trade
        - CVD supporting the direction
        - Volume confirming the move
        - Funding rate favorable
        """
        direction = signal.get('direction', 'NEUTRAL')
        if direction == 'NEUTRAL':
            return False
        
        flow_indicators = 0
        total_indicators = 4
        
        # 1. OI Direction
        oi_change = binance_data.get('oi', {}).get('openInterestChange', 0)
        price_change = p1_data.get('price_change', 0)
        
        if direction == 'LONG':
            if (oi_change > 0 and price_change > 0) or (oi_change < 0 and price_change < 0):
                flow_indicators += 1
        else:  # SHORT
            if (oi_change < 0 and price_change > 0) or (oi_change > 0 and price_change < 0):
                flow_indicators += 1
        
        # 2. CVD Support
        cvd_delta = p1_data.get('cvd_delta', 0)
        if direction == 'LONG' and cvd_delta > 0:
            flow_indicators += 1
        elif direction == 'SHORT' and cvd_delta < 0:
            flow_indicators += 1
        
        # 3. Volume Confirmation
        vol_ratio = self._get_volume_ratio(binance_data, p1_data)
        if vol_ratio > 1.3:
            flow_indicators += 1
        
        # 4. Funding Rate
        funding_rate = binance_data.get('funding_rate', 0)
        if direction == 'LONG' and funding_rate < 0.005:
            flow_indicators += 1
        elif direction == 'SHORT' and funding_rate > -0.005:
            flow_indicators += 1
        
        # 100% confluence = all 4 indicators aligned
        return flow_indicators == total_indicators
    
    # Section 25.1: Data Layer Decoupling
    # Sync fetchers removed. All data now comes through BinanceDataFetcher
    # with tiered caching: Liquidations (10s), Whales (5s), L/S Ratio (10s), Volume (2s)
    
    async def _fetch_binance_data_async(self) -> Dict:
        """ดึงข้อมูลจาก Binance - ใช้ Async
        
        Section 22.4 Task 1: Data Layer Decoupling
        Uses BinanceDataFetcher for all data fetching with tiered caching:
        - Liquidations (10s cache)
        - Whales/AggTrades (5s cache) 
        - Long/Short Ratio (10s cache)
        - Volume (2s cache)
        - Funding Rate (2s cache)
        
        Keeps custom wall detection in manager for session-based thresholds.
        """
        return await self._fetch_binance_data_with_fetcher()
    
    async def _fetch_binance_data_with_fetcher(self) -> Dict:
        """
        Alternative method using BinanceDataFetcher (async).
        Section 22.4 Task 1: Data Layer Decoupling
        
        Uses fetcher for:
        - Liquidations (10s cache)
        - Whales/AggTrades (5s cache) 
        - Long/Short Ratio (10s cache)
        - Volume (2s cache)
        - Funding Rate (2s cache)
        
        Keeps custom wall detection in manager for session-based thresholds.
        """
        import time
        
        current_time = time.time()
        
        # Initialize caches if not exists
        if not hasattr(self, '_oi_cache'):
            self._oi_cache = {'data': None, 'time': 0}
        if not hasattr(self, '_walls_cache'):
            self._walls_cache = {'data': None, 'time': 0}
        
        OI_CACHE_TTL = 2
        WALLS_CACHE_TTL = 2
        
        binance_data = {}
        
        try:
            # Use fetcher's async methods for decoupled data
            # These methods have built-in tiered caching
            
            # Fetch data in parallel where possible
            liq_task = self.binance_fetcher.fetch_liquidations()
            whale_task = self.binance_fetcher.fetch_aggtrades()
            ls_task = self.binance_fetcher.fetch_long_short_ratio()
            vol_task = self.binance_fetcher.fetch_volume()
            funding_task = self.binance_fetcher.fetch_funding_rate()
            
            # Fetch OI and Orderbook separately (custom logic needed)
            # Check OI cache
            if self._oi_cache['data'] and (current_time - self._oi_cache['time']) < OI_CACHE_TTL:
                oi_data = self._oi_cache['data']
            else:
                oi_data = await self.binance_fetcher.fetch_open_interest()
                self._oi_cache = {'data': oi_data, 'time': current_time}
            
            # Check Walls cache (still need custom wall detection)
            if self._walls_cache['data'] and (current_time - self._walls_cache['time']) < WALLS_CACHE_TTL:
                walls_data = self._walls_cache['data']
            else:
                # Fetch orderbook for custom wall detection
                ob_data = await self.binance_fetcher.fetch_order_book()
                walls_data = self._process_orderbook_walls(ob_data)
                self._walls_cache = {'data': walls_data, 'time': current_time}
            
            # Wait for fetcher tasks
            liq_data, whale_data, ls_data, vol_data, funding_data = await asyncio.gather(
                liq_task, whale_task, ls_task, vol_task, funding_task,
                return_exceptions=True
            )
            
            # Handle exceptions
            if isinstance(liq_data, Exception):
                logger.debug(f"Liquidation fetch error: {liq_data}")
                liq_data = {'buy_liquidation': [], 'sell_liquidation': []}
            if isinstance(whale_data, Exception):
                logger.debug(f"Whale fetch error: {whale_data}")
                whale_data = {'buy_whales': [], 'sell_whales': [], 'total_buy': 0.0, 'total_sell': 0.0}
            if isinstance(ls_data, Exception):
                logger.debug(f"Long/Short fetch error: {ls_data}")
                ls_data = {'long_ratio': 0.5, 'short_ratio': 0.5}
            if isinstance(vol_data, Exception):
                logger.debug(f"Volume fetch error: {vol_data}")
                vol_data = {'current_volume': 0, 'avg_volume': 0, 'volume_ratio': 1}
            if isinstance(funding_data, Exception):
                logger.debug(f"Funding fetch error: {funding_data}")
                funding_data = 0
            
            # Compile binance_data
            binance_data = {
                'oi': oi_data,
                'walls': walls_data,
                'cvd': {},
                'funding_rate': funding_data,
                'volume': vol_data,
                'liquidations': liq_data,
                'whales': whale_data,
                'long_short_ratio': ls_data,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            return binance_data
            
        except Exception as e:
            logger.debug(f"Binance fetcher async error: {e}")
            return {}
    
    def _process_orderbook_walls(self, ob_data: Dict) -> Dict:
        """
        Process orderbook data for wall detection with custom logic.
        This keeps the session-based thresholding and stability tracking.
        """
        walls_data = {'bid_walls': [], 'ask_walls': []}
        
        try:
            bids = ob_data.get('bids', [])
            asks = ob_data.get('asks', [])
            
            if not bids or not asks:
                return walls_data
            
            # Calculate dynamic threshold (same as before)
            all_volumes = []
            if bids:
                all_volumes.extend([float(b[1]) for b in bids[:20]])
            if asks:
                all_volumes.extend([float(a[1]) for a in asks[:20]])
            
            avg_volume = sum(all_volumes) / len(all_volumes) if all_volumes else 0
            current_price = ob_data.get('best_bid', asks[0][0] if asks else 0)
            
            # Session-based threshold
            utc_hour = datetime.now(timezone.utc).hour
            if 13 <= utc_hour < 21:
                session_multiplier = 1.2  # Reduced from 1.5
            elif 0 <= utc_hour < 8:
                session_multiplier = 0.6  # Reduced from 0.7
            else:
                session_multiplier = 0.8  # Reduced from 1.0
            
            # Section 24.3 & Section 56 Bug #5 FIX: Smart Wall Detection
            # Changed threshold floor from $500K to $200K (more realistic for BTC M5)
            # $200K ≈ 2.1 BTC at $95K = reasonable institutional wall size
            # Also reduced multiplier from 3 to 2 to be more realistic
            dynamic_threshold = avg_volume * current_price * 2 * session_multiplier
            WALL_THRESHOLD_USD = max(200000, dynamic_threshold)  # Floor: $200K (was $500K)
            VOLUME_MULTIPLIER = 1.3  # Reduced from 1.5
            MAX_WALL_DISTANCE_PCT = 0.05
            
            # Squeeze detection
            total_bid_vol = sum([float(b[1]) for b in bids[:10]])
            total_ask_vol = sum([float(a[1]) for a in asks[:10]])
            bid_ask_ratio = total_bid_vol / total_ask_vol if total_ask_vol > 0 else 1.0
            is_squeeze = 0.9 <= bid_ask_ratio <= 1.1
            
            if not is_squeeze:
                # Bid walls
                for bid in bids[:20]:
                    price, volume = float(bid[0]), float(bid[1])
                    wall_value_usd = volume * price
                    distance_pct = abs(price - current_price) / current_price if current_price > 0 else 1.0
                    
                    if (volume > avg_volume * VOLUME_MULTIPLIER and 
                        wall_value_usd >= WALL_THRESHOLD_USD and 
                        distance_pct <= MAX_WALL_DISTANCE_PCT):
                        walls_data['bid_walls'].append({
                            'price': price,
                            'size': volume,
                            'value_usd': wall_value_usd,
                            'distance_pct': distance_pct * 100
                        })
                
                # Ask walls
                for ask in asks[:20]:
                    price, volume = float(ask[0]), float(ask[1])
                    wall_value_usd = volume * price
                    distance_pct = abs(price - current_price) / current_price if current_price > 0 else 1.0
                    
                    if (volume > avg_volume * VOLUME_MULTIPLIER and 
                        wall_value_usd >= WALL_THRESHOLD_USD and 
                        distance_pct <= MAX_WALL_DISTANCE_PCT):
                        walls_data['ask_walls'].append({
                            'price': price,
                            'size': volume,
                            'value_usd': wall_value_usd,
                            'distance_pct': distance_pct * 100
                        })
            
            # Add metadata
            walls_data['squeeze_detected'] = is_squeeze
            walls_data['bid_ask_ratio'] = bid_ask_ratio
            walls_data['dynamic_threshold'] = WALL_THRESHOLD_USD
            walls_data['session_multiplier'] = session_multiplier
            
        except Exception as e:
            logger.debug(f"Wall processing error: {e}")
        
        return walls_data
    
    # ============================================================================
    # SECTION 7: COUNTER-TREND TRADING QUALITY
    # ============================================================================
    
    def _get_htf_trend_from_data(self, htf_data: Dict) -> Tuple[str, int]:
        """
        Extract HTF trend from htf_data dict.
        
        Args:
            htf_data: HTF analysis data from HTFMSSAnalyzer
            
        Returns:
            Tuple[str, int]: (htf_trend, htf_strength)
        """
        if htf_data is None:
            return 'NEUTRAL', 3
        
        # Extract from HTFMSSAnalyzer output format
        htf_trend = htf_data.get('trend', 'NEUTRAL')
        htf_strength = htf_data.get('strength', 3)
        
        # Map strength values to standard scale (1-5)
        if isinstance(htf_strength, int):
            if htf_strength < 1:
                htf_strength = 1
            elif htf_strength > 5:
                htf_strength = 5
        
        # Normalize trend names
        trend_map = {
            'BULLISH': 'BULL',
            'STRONG_BULL': 'STRONG_BULL',
            'BEARISH': 'BEAR',
            'STRONG_BEAR': 'STRONG_BEAR',
            'STRONG_LONG': 'STRONG_BULL',
            'STRONG_SHORT': 'STRONG_BEAR'
        }
        htf_trend = trend_map.get(htf_trend, htf_trend)
        
        return htf_trend, htf_strength
    
    def _is_counter_trend(self, trade_direction: str, htf_trend: str) -> Tuple[bool, str]:
        """
        Determine if a trade is counter-trend based on HTF trend.
        
        Args:
            trade_direction: 'LONG' or 'SHORT'
            htf_trend: 'STRONG_BULL', 'BULL', 'NEUTRAL', 'BEAR', 'STRONG_BEAR'
            
        Returns:
            Tuple[bool, str]: (is_counter_trend, quality_level)
            quality_level: 'VERY_HIGH', 'HIGH', 'NORMAL'
        """
        # With-Trend: BULL → LONG, BEAR → SHORT
        # Counter-Trend: BULL → SHORT, BEAR → LONG
        
        with_trend_bull = trade_direction == 'LONG' and htf_trend in ['BULL', 'STRONG_BULL']
        with_trend_bear = trade_direction == 'SHORT' and htf_trend in ['BEAR', 'STRONG_BEAR']
        
        if with_trend_bull or with_trend_bear:
            return False, 'WITH_TREND'
        
        counter_trend_bull = trade_direction == 'SHORT' and htf_trend in ['BULL', 'STRONG_BULL']
        counter_trend_bear = trade_direction == 'LONG' and htf_trend in ['BEAR', 'STRONG_BEAR']
        
        if counter_trend_bull or counter_trend_bear:
            # Determine quality level based on HTF strength
            if htf_trend == 'STRONG_BULL' or htf_trend == 'STRONG_BEAR':
                return True, 'VERY_HIGH'
            elif htf_trend == 'BULL' or htf_trend == 'BEAR':
                return True, 'HIGH'
            else:
                return True, 'NORMAL'
        
        # Neutral HTF - not counter-trend
        return False, 'NEUTRAL'
    
    def _get_counter_trend_thresholds(self, pattern_type: str, quality_level: str) -> Tuple[int, float]:
        """
        Get quality thresholds for counter-trend trades.
        v6.0: Reduced thresholds per architecture plan Section 4.6
        
        Args:
            pattern_type: 'LP', 'DB', or 'DA'
            quality_level: 'VERY_HIGH', 'HIGH', 'NORMAL'
            
        Returns:
            Tuple[int, float]: (min_score, max_rr_ratio)
        """
        # v6.0: Reduced thresholds for more counter-trend signals
        thresholds = {
            'LP': {
                'VERY_HIGH': (13, 1.2),   # v6.0: was 15
                'HIGH':      (11, 1.5),   # v6.0: was 13
                'NORMAL':    (9,  2.0),   # v6.0: was 10
                'NEUTRAL':   (7,  2.5)    # v6.0: was 8
            },
            'DB': {
                'VERY_HIGH': (12, 1.2),   # v6.0: was 14
                'HIGH':      (10, 1.5),   # v6.0: was 12
                'NORMAL':    (8,  2.0),   # v6.0: was 10
                'NEUTRAL':   (6,  2.5)    # v6.0: was 8
            },
            'DA': {
                'VERY_HIGH': (14, 1.2),   # v6.0: was 16
                'HIGH':      (12, 1.5),   # v6.0: was 14
                'NORMAL':    (10, 2.0),   # v6.0: was 12
                'NEUTRAL':   (8,  2.5)    # v6.0: was 10
            }
        }
        
        pattern_thresholds = thresholds.get(pattern_type, thresholds['DA'])
        return pattern_thresholds.get(quality_level, pattern_thresholds['NORMAL'])
    
    def _check_counter_trend_quality(
        self,
        signal: Dict,
        htf_data: Dict,
        binance_data: Dict,
        p1_data: Dict
    ) -> Tuple[bool, str, Dict]:
        """
        Section 7 [TASK 2]: Check counter-trend signal quality.
        
        Validates counter-trend signals against quality thresholds:
        1. Detects if trade is counter-trend
        2. Applies stricter quality thresholds
        3. Checks pattern-specific requirements
        
        Args:
            signal: Signal dict with pattern_type, direction, score
            htf_data: HTF analysis data
            binance_data: Binance market data
            p1_data: Phase 1 flow data
            
        Returns:
            Tuple[bool, str, Dict]: (pass_quality, reason, details)
        """
        pattern_type = signal.get('pattern_type', 'DA')
        trade_direction = signal.get('direction', 'NEUTRAL')
        
        # Get HTF trend
        htf_trend, htf_strength = self._get_htf_trend_from_data(htf_data)
        
        # Check if counter-trend
        is_counter, quality_level = self._is_counter_trend(trade_direction, htf_trend)
        
        details = {
            'htf_trend': htf_trend,
            'htf_strength': htf_strength,
            'is_counter_trend': is_counter,
            'quality_level': quality_level,
            'trade_direction': trade_direction
        }
        
        # Not counter-trend - pass automatically with normal multipliers
        if not is_counter:
            details['result'] = 'PASS'
            details['reason'] = 'With-trend trade - normal quality'
            details['sl_multiplier'] = 1.0
            details['tp_multiplier'] = 1.0
            details['position_size_multiplier'] = 1.0
            return True, 'WITH_TREND', details

        # =====================================================================
        # Section 10 CT-1 FIX: COUNTER-TREND VALIDATION (was dead code before)
        # All checks must run BEFORE returning True
        # =====================================================================

        # 1. Score threshold check
        min_score, max_rr = self._get_counter_trend_thresholds(pattern_type, quality_level)
        current_score = signal.get('score', 0)
        details['min_required_score'] = min_score
        details['max_allowed_rr'] = max_rr
        details['current_score'] = current_score

        if current_score < min_score:
            details['result'] = 'FAIL'
            details['reason'] = f'Score {current_score} < {min_score} required for {quality_level} counter-trend'
            return False, 'SCORE_TOO_LOW', details

        # 2. RR ratio check
        entry_price = signal.get('entry_price', 0)
        sl_boundary = signal.get('sl_boundary', 0)
        if entry_price > 0 and sl_boundary > 0:
            sl_dist = abs(entry_price - sl_boundary)
            tp_price = signal.get('tp_boundary',
                entry_price * 1.02 if trade_direction == 'LONG' else entry_price * 0.98)
            tp_dist = abs(entry_price - tp_price)
            if sl_dist > 0:
                rr_ratio = tp_dist / sl_dist
                details['rr_ratio'] = round(rr_ratio, 2)
                if rr_ratio > max_rr:
                    details['result'] = 'FAIL'
                    details['reason'] = f'RR {rr_ratio:.2f} > {max_rr:.2f} max for counter-trend'
                    return False, 'RR_TOO_HIGH', details

        # 3. Pattern-specific requirements
        pattern_pass, pattern_reason = self._check_pattern_specific_requirements(
            pattern_type, signal, binance_data, p1_data, quality_level
        )
        if not pattern_pass:
            details['result'] = 'FAIL'
            details['reason'] = f'{pattern_type} requirements failed: {pattern_reason}'
            details['pattern_failure'] = pattern_reason
            return False, 'PATTERN_REQUIREMENTS', details

        # 4. Section 10 CT-2 FIX: HTF Structure Confirmation
        htf_struct_pass, htf_struct_reason = self._check_htf_structure_confirmation(
            htf_data, trade_direction
        )
        details['htf_structure_reason'] = htf_struct_reason
        if not htf_struct_pass:
            details['result'] = 'FAIL'
            details['reason'] = f'HTF still strong: {htf_struct_reason}'
            return False, 'NO_HTF_STRUCTURE', details

        # 5. Section 10 CT-5: Funding rate bonus
        funding_bonus, funding_reason = self._check_funding_rate_alignment(
            binance_data, trade_direction
        )
        details['funding_bonus'] = funding_bonus
        details['funding_reason'] = funding_reason

        # 6. Section 10 CT-4: Position size reduction
        position_size_mult = {
            'VERY_HIGH': 0.5,
            'HIGH':      0.75,
            'NORMAL':    1.0,
            'NEUTRAL':   1.0,
        }.get(quality_level, 1.0)
        details['position_size_multiplier'] = position_size_mult

        # All checks passed — now apply SL/TP multipliers
        # Section 10 CT-3 FIX: multipliers applied AFTER validation
        ct_sl_mult, ct_tp_mult = self._get_counter_trend_sl_tp_multipliers(htf_trend, trade_direction)
        details['sl_multiplier'] = ct_sl_mult
        details['tp_multiplier'] = ct_tp_mult
        details['result'] = 'PASS'
        details['reason'] = (
            f'Counter-trend qualified: {quality_level} | '
            f'Score {current_score}/{min_score} | '
            f'SL×{ct_sl_mult} TP×{ct_tp_mult} | '
            f'Pos×{position_size_mult}'
        )
        return True, 'COUNTER_TREND_QUALIFIED', details
    
    def _check_htf_structure_confirmation(
        self,
        htf_data: Dict,
        trade_direction: str
    ) -> Tuple[bool, str]:
        """
        Section 10 CT-2 FIX: HTF Structure Weakness Confirmation.

        Before trading counter-trend, HTF must show at least ONE sign of weakness:
          1. HTF CHoCH (Change of Character) — trend starting to flip
          2. HTF Weak/Failed BOS — momentum fading
          3. HTF Unfilled FVG in trade direction — price gap to fill

        Returns:
            Tuple[bool, str]: (confirmed, reason)
        """
        if htf_data is None:
            return True, 'NO_HTF_DATA_SKIP'

        # 1. CHoCH check
        choch_status = htf_data.get('choch_status', '')
        has_choch = 'CHoCH' in str(choch_status)

        # 2. Weak BOS check
        bos_data = htf_data.get('break_of_structure', {})
        bos_strength = bos_data.get('strength', 'STRONG') if isinstance(bos_data, dict) else 'STRONG'
        has_weak_bos = bos_strength in ('WEAK', 'MODERATE')

        # 3. Unfilled FVG in trade direction
        fvgs = htf_data.get('fvgs', {})
        direction_key = 'bearish' if trade_direction == 'SHORT' else 'bullish'
        fvg_list = fvgs.get(direction_key, [])
        has_fvg = bool(fvg_list)

        if has_choch or has_weak_bos or has_fvg:
            reason = (
                f'HTF_STRUCT_OK | CHoCH={has_choch} | '
                f'WeakBOS={has_weak_bos} | FVG={has_fvg}'
            )
            return True, reason

        return False, 'HTF_STILL_STRONG: no CHoCH, no weak BOS, no FVG'

    def _check_funding_rate_alignment(
        self,
        binance_data: Dict,
        trade_direction: str
    ) -> Tuple[int, str]:
        """
        Section 10 CT-5: Funding Rate as Counter-Trend Booster.

        Over-leveraged market = higher probability of counter-trend reversal:
          Funding > +0.05% → market over-long  → SHORT counter-trend gets +3
          Funding < -0.05% → market over-short → LONG  counter-trend gets +3

        Returns:
            Tuple[int, str]: (bonus_score, description)
        """
        funding = binance_data.get('funding_rate', 0)
        if not funding:
            return 0, 'NO_FUNDING_DATA'

        bonus = 0
        if trade_direction == 'SHORT':
            if funding > 0.05:
                bonus = 3
            elif funding > 0.02:
                bonus = 1
        elif trade_direction == 'LONG':
            if funding < -0.05:
                bonus = 3
            elif funding < -0.02:
                bonus = 1

        return bonus, f'Funding={funding:.4f} bonus={bonus}'

    def _check_pattern_specific_requirements(
        self,
        pattern_type: str,
        signal: Dict,
        binance_data: Dict,
        p1_data: Dict,
        quality_level: str
    ) -> Tuple[bool, str]:
        """
        Section 7.4: Pattern-specific requirements for counter-trend trades.
        
        Args:
            pattern_type: 'LP', 'DB', or 'DA'
            signal: Signal dict
            binance_data: Binance market data
            p1_data: Phase 1 flow data
            quality_level: 'VERY_HIGH', 'HIGH', 'NORMAL'
            
        Returns:
            Tuple[bool, str]: (passes, failure_reason)
        """
        trade_direction = signal.get('direction', 'NEUTRAL')
        requirements_met = True
        failures = []
        
        if pattern_type == 'LP':
            # === LP Counter-Trend Requirements (Section 7.4) ===
            
            # OI Spike check (required for all counter-trend levels)
            oi_change = abs(binance_data.get('oi', {}).get('openInterestChange', 0))
            min_oi_spike = {
                'VERY_HIGH': 0.005,  # 0.5%
                'HIGH': 0.003,       # 0.3%
                'NORMAL': 0.0015     # 0.15%
            }.get(quality_level, 0.0015)
            
            if oi_change < min_oi_spike:
                failures.append(f'OI spike {oi_change*100:.2f}% < {min_oi_spike*100:.2f}%')
                requirements_met = False
            
            # Volume check for Very High quality
            if quality_level == 'VERY_HIGH':
                vol_ratio = self._get_volume_ratio(binance_data, p1_data)
                if vol_ratio < 1.5:
                    failures.append(f'Volume {vol_ratio:.2f}x < 1.5x for Very High')
                    requirements_met = False
            
            # CVD Divergence check for High/Very High
            if quality_level in ['HIGH', 'VERY_HIGH']:
                cvd_delta = p1_data.get('cvd_delta', 0)
                # For SHORT counter-trend, expect CVD divergence (price up, CVD down)
                if trade_direction == 'SHORT' and cvd_delta >= 0:
                    failures.append('CVD not divergent for counter-trend SHORT')
                    requirements_met = False
                elif trade_direction == 'LONG' and cvd_delta <= 0:
                    failures.append('CVD not divergent for counter-trend LONG')
                    requirements_met = False
        
        elif pattern_type == 'DB':
            # === DB Counter-Trend Requirements (Section 7.4) ===
            
            # Wall Z-Score check (Very High requires 3.0+)
            if quality_level in ['VERY_HIGH', 'HIGH']:
                wall_zscore = binance_data.get('walls', {}).get('wall_zscore', 0)
                min_zscore = 3.0 if quality_level == 'VERY_HIGH' else 2.5
                
                if wall_zscore < min_zscore:
                    failures.append(f'Wall Z-Score {wall_zscore:.2f} < {min_zscore:.2f}')
                    requirements_met = False
            
            # Volume check for Very High
            if quality_level == 'VERY_HIGH':
                vol_ratio = self._get_volume_ratio(binance_data, p1_data)
                if vol_ratio < 1.2:
                    failures.append(f'Volume {vol_ratio:.2f}x < 1.2x for Very High')
                    requirements_met = False
        
        elif pattern_type == 'DA':
            # === DA Counter-Trend Requirements (Section 7.4) ===
            
            # V-DER check (Very High requires 2.5+)
            if quality_level in ['VERY_HIGH', 'HIGH']:
                v_der = binance_data.get('cvd', {}).get('volume_der', 0)
                min_der = 2.5 if quality_level == 'VERY_HIGH' else 2.0
                
                if v_der < min_der:
                    failures.append(f'V-DER {v_der:.2f} < {min_der:.2f}')
                    requirements_met = False
            
            # Volume check for Very High
            if quality_level == 'VERY_HIGH':
                vol_ratio = self._get_volume_ratio(binance_data, p1_data)
                if vol_ratio < 1.3:
                    failures.append(f'Volume {vol_ratio:.2f}x < 1.3x for Very High')
                    requirements_met = False
        
        failure_reason = '; '.join(failures) if failures else ''
        return requirements_met, failure_reason
    
    def _get_counter_trend_sl_tp_multipliers(self, htf_trend: str, trade_direction: str) -> Tuple[float, float]:
        """
        Section 7.5: Get SL/TP multipliers for counter-trend trades.
        
        Args:
            htf_trend: 'STRONG_BULL', 'BULL', 'NEUTRAL', 'BEAR', 'STRONG_BEAR'
            trade_direction: 'LONG' or 'SHORT'
            
        Returns:
            Tuple[float, float]: (sl_multiplier, tp_multiplier)
        """
        # SL multipliers from Section 7.5
        sl_mult_map = {
            ('STRONG_BULL', 'SHORT'): 1.5,
            ('BULL', 'SHORT'): 1.25,
            ('NEUTRAL', 'LONG'): 1.0,
            ('NEUTRAL', 'SHORT'): 1.0,
            ('BEAR', 'LONG'): 1.25,
            ('STRONG_BEAR', 'LONG'): 1.5
        }
        
        # TP multipliers from Section 7.5
        tp_mult_map = {
            ('STRONG_BULL', 'SHORT'): 0.7,
            ('BULL', 'SHORT'): 0.85,
            ('NEUTRAL', 'LONG'): 1.0,
            ('NEUTRAL', 'SHORT'): 1.0,
            ('BEAR', 'LONG'): 0.85,
            ('STRONG_BEAR', 'LONG'): 0.7
        }
        
        key = (htf_trend, trade_direction)
        
        # Check if it's a counter-trend situation
        is_counter = False
        if htf_trend in ['STRONG_BULL', 'BULL'] and trade_direction == 'SHORT':
            is_counter = True
        elif htf_trend in ['STRONG_BEAR', 'BEAR'] and trade_direction == 'LONG':
            is_counter = True
        
        if is_counter:
            sl_mult = sl_mult_map.get(key, 1.0)
            tp_mult = tp_mult_map.get(key, 1.0)
        else:
            # With-trend or neutral
            sl_mult = 1.0
            tp_mult = 1.0
        
        return sl_mult, tp_mult
    
    # ============================================================================
    # END SECTION 7: COUNTER-TREND TRADING QUALITY
    # ============================================================================
    
    def _check_pending_retests(self, current_price: float, binance_data: Dict) -> List[Dict]:
        """Check if any pending retests have been triggered."""
        triggered_signals = []
        expired_patterns = []
        
        for pattern_type, retest_data in list(self._retest_waiting.items()):
            # Check for expiry — make both timezone-aware for safe comparison
            expiry = retest_data.get('expiry', datetime.now(timezone.utc))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expiry:
                expired_patterns.append(pattern_type)
                continue
                
            direction = retest_data['direction']
            target = retest_data['target_price']
            
            # Trigger logic: Price touches or crosses retest target
            triggered = False
            if direction == 'BUY' and current_price <= target:
                triggered = True
            elif direction == 'SELL' and current_price >= target:
                triggered = True
                
            if triggered:
                logger.info(f"🎯 RETEST TRIGGERED: {pattern_type} {direction} @ {current_price:.2f}")
                signal = {
                    'found': True,
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                    'entry_price': current_price,  # Enter at market now
                    'stop_loss': retest_data.get('stop_loss'),
                    'take_profit': retest_data.get('take_profit'),
                    'score': retest_data['score'] + 2,  # Bonus for retest confirmation
                    'reasons': retest_data['reasons'] + ["RETEST_CONFIRMED"],
                    'pattern_type': pattern_type,
                    'is_retest': True,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                triggered_signals.append(signal)
                expired_patterns.append(pattern_type)
        
        # Clean up expired patterns
        for p in expired_patterns:
            if p in self._retest_waiting:
                del self._retest_waiting[p]
        
        return triggered_signals

    def _calculate_smart_sl(self, direction: str, entry_price: float, candles, pattern_type: str) -> float:
        """
        Section 52: Institutional Structural SL - Heavy-Duty Standard.
        
        Key Changes for Institutional Grade:
        - ATR Period: 20 (was 14) - more stable volatility measure
        - Pattern-Specific Buffers:
          - LP (Liquidity Purge): 2.0x ATR - High risk, need room for sweep rejection
          - DA (Delta Absorption): 2.5x ATR - Highest risk, precision entry
          - DB (Defensive Block): 1.5x ATR - Moderate risk, wall-backed
        - Hard Floor: 0.8% minimum - prevents SL being too tight
        - Lookback: 50 candles (~4 hours) - True swing identification
        """
        # === Institutional Standards ===
        lookback = 50  # Standard for M5 (~4 hours of price action)
        atr_period = 20  # ATR period (increased from 14 for stability)
        
        # Pattern-Specific Buffer Multipliers (Institutional Standard)
        if pattern_type == 'LP':
            buffer_mult = 2.0  # Liquidity Purge = High risk
        elif pattern_type == 'DA':
            buffer_mult = 2.5  # Delta Absorption = Highest risk
        elif pattern_type == 'DB':
            buffer_mult = 1.5  # Defensive Block = Moderate risk
        else:
            buffer_mult = 1.5  # Default
        
        # Hard Floor: 0.8% minimum (prevents SL being too tight)
        floor_pct = 0.008  # 0.8%
        
        if candles is None or len(candles) < lookback:
            # Fallback to floor percentage if not enough data
            fallback_sl = entry_price * (1 + floor_pct) if direction in ['SHORT', 'SELL', 'BEARISH'] else entry_price * (1 - floor_pct)
            return float(fallback_sl)

        import numpy as np
        
        # 1. Calculate ATR (Institutional Period)
        highs = candles['high'].values
        lows = candles['low'].values
        closes = candles['close'].values
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(abs(highs[1:] - closes[:-1]), abs(lows[1:] - closes[:-1])))
        atr = np.mean(tr[-atr_period:])  # Use period 20 instead of 14
        
        # 2. Calculate Hard Floor Distance
        min_distance = entry_price * floor_pct  # 0.8%
        
        # 3. Calculate Structural Level (True Swing High/Low)
        recent_high = float(candles['high'].tail(lookback).max())
        recent_low = float(candles['low'].tail(lookback).min())

        # 4. Calculate Pattern-Specific Buffer
        buffer = atr * buffer_mult
        
        # Ensure buffer is at least $100 (minimum practical buffer)
        buffer = max(buffer, 100.0)
        
        # 5. Calculate Final SL (Institutional Standard)
        # SL = max(structural_level + buffer, entry + floor)
        if direction in ['SHORT', 'SELL', 'BEARISH']:
            sl = max(recent_high + buffer, entry_price + min_distance)
        else:
            sl = min(recent_low - buffer, entry_price - min_distance)
        
        # Log for debugging
        logger.debug(f"📐 SL Calc: {pattern_type} | Entry={entry_price:.2f} | ATR={atr:.2f} | Buffer={buffer_mult}xATR | Floor=0.8% | SL={sl:.2f}")
        
        return float(sl)

    def _evaluate_liquidity_purge_pattern(self, candles, price, p1, ict, htf, binance_data) -> Optional[Dict]:
        """
        Pattern 1: LP (Liquidity Purge) - formerly OI_MOM/SWEEP
        v4.0: Institutional Grade with micro-flow analysis
        
        Requirements:
        1. Price Action: Price sweeps old High/Low (M15/H1) and gets rejected back into zone
        2. Absorption Check: During sweep, CVD (Delta) spikes against price direction
        3. OI Spike: Open Interest increases at least 0.5% within 1 minute after sweep
        
        Gate Implementation:
        1. OI_Velocity = (OI[current] - OI[1 minute ago]) / OI[1 minute ago] * 100
        2. Gate Passed IF OI_Velocity > 0.03%
        
        Retest Watcher:
        - If Gate Passed but Price > High[1] + 10 pips: Set flag WAIT_RETEST
        - Wait for price to touch Fib 0.5 of the signal bar before executing
        """
        # === Section 24.4: Get Regime-Based Thresholds ===
        regime = self._detect_regime_v3(candles)
        regime_thresholds = self._get_regime_thresholds(regime)
        
        # === Session-Aware Thresholds ===
        session_thresh = self._get_session_thresholds()
        
        # === Section 24.2: FLOW GATE - OI Velocity ===
        oi_change = binance_data.get('oi', {}).get('openInterestChange', 0)
        
        # Calculate OI Velocity (Section 24.2)
        oi_velocity, oi_velocity_details = self._calculate_oi_velocity(binance_data)
        
        cvd_momentum = self._calculate_cvd_momentum(candles, lookback=5)
        vol_ratio = self._get_volume_ratio(binance_data, p1)
        cvd_delta = p1.get('cvd_delta', 0)
        
        # Gate: OI Velocity must pass threshold
        oi_velocity_passed = oi_velocity_details.get('gate_passed', False)
        
        # Section 24.2: Retest Watcher Logic
        # If price moved too far from signal bar, wait for retest
        wait_for_retest = False
        signal_bar_high = 0
        signal_bar_low = 0
        
        if len(candles) >= 2:
            signal_bar_high = candles.iloc[-1]['high']
            signal_bar_low = candles.iloc[-1]['low']
            
            # For BUY: check if price is too far above recent high
            if cvd_delta > 0:
                pip_distance = (price - signal_bar_high) / price * 10000  # Approximate pips
                if pip_distance > 10:  # More than 10 pips above
                    wait_for_retest = True
            # For SELL: check if price is too far below recent low
            else:
                pip_distance = (signal_bar_low - price) / price * 10000
                if pip_distance > 10:
                    wait_for_retest = True
        
        # Flow Gate: OI velocity + CVD trending + Volume active
        # Section 56 Bug #3 FIX: Reduced CVD threshold from >= 2 to >= 1
        # v6.0: Further reduced to >= 0.5 for more signals
        has_cvd_momentum = cvd_momentum['strength'] >= 0.5  # v6.0: was 1
        has_volume = vol_ratio > session_thresh['volume_threshold'] * 0.9  # Bug #3: 10% relaxation
        
        # Bug #3 FIX: Add OI activity as additional fallback
        has_oi_activity = abs(binance_data.get('oi', {}).get('openInterestChange', 0)) > 0.05
        
        # v3.6: Add Momentum Breakout exception (Fast move > 1.5x ATR)
        # Allows signal even if OI velocity slightly misses or CVD lag
        is_breakout = False
        if len(candles) >= 14:
            atr = p1.get('atr', 100)
            price_move = abs(candles.iloc[-1]['close'] - candles.iloc[-1]['open'])
            if price_move > atr * 1.5:
                is_breakout = True
        
        # Need OI velocity gate + (CVD momentum OR Volume OR OI Activity OR Breakout)
        # Bug #3 FIX: Added has_oi_activity to the OR condition
        if not oi_velocity_passed and not is_breakout:
            logger.debug(f"❌ LP: Gate failed (OI Vel: {oi_velocity:.4f}, Breakout: {is_breakout})")
            return None
        if not (has_cvd_momentum or has_volume or has_oi_activity or is_breakout):
            logger.debug(f"❌ LP: Momentum failed (CVD: {cvd_momentum['strength']}, Vol: {vol_ratio:.2f}, OI: {has_oi_activity})")
            return None
        
        score = 0
        details = {
            'flow_gates': 'oi_velocity_passed',
            'oi_velocity': oi_velocity,
            'regime': regime
        }
        details.update(oi_velocity_details)
        
        # === Retest Wait Logic ===
        if wait_for_retest:
            details['wait_for_retest'] = True
            details['signal_bar_high'] = signal_bar_high
            details['signal_bar_low'] = signal_bar_low
            
            retest_dir = 'BUY' if cvd_delta > 0 else 'SELL'
            # Target is Fib 0.5 of signal bar (Section 24.2)
            retest_target = (signal_bar_high + signal_bar_low) / 2
            
            # Store for potential retest entry
            self._retest_waiting['LP'] = {
                'entry_price': price,
                'target_price': retest_target,
                'stop_loss': price - (price * 0.01) if retest_dir == 'BUY' else price + (price * 0.01), # Placeholder SL
                'take_profit': price + (price * 0.02) if retest_dir == 'BUY' else price - (price * 0.02), # Placeholder TP
                'score': int(score),
                'reasons': [f"LP_{retest_dir}", "WAITING_RETEST"],
                'direction': retest_dir,
                'expiry': datetime.now(timezone.utc) + timedelta(minutes=10)
            }
            
            return {
                'pattern_type': 'LP',
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                'direction': 'LONG' if retest_dir == 'BUY' else 'SHORT',
                'score': int(score),
                'entry_price': retest_target,
                'wait_for_retest': True
            }
        
        # === DIRECTION (from Flow Data) ===
        direction = cvd_momentum['direction']
        if direction == 'NEUTRAL':
            if abs(cvd_delta) < 0.05:
                return None
            direction = 'BUY' if cvd_delta > 0 else 'SELL'
        details['direction_source'] = 'cvd_momentum'
        
        # === Section 24.2: FLOW SCORING (PRIMARY — max ~20) ===
        
        # 1. OI Velocity magnitude (0-4)
        if abs(oi_velocity) > 0.1:
            score += 4
            details['oi_velocity'] = 'strong'
        elif abs(oi_velocity) > 0.05:
            score += 3
            details['oi_velocity'] = 'good'
        elif abs(oi_velocity) > 0.03:
            score += 2
            details['oi_velocity'] = 'moderate'
        
        # 2. CVD Momentum (0-4)
        if cvd_momentum['strength'] >= 3:
            score += 4
            details['cvd_momentum'] = 'strong'
        elif cvd_momentum['strength'] >= 2:
            score += 2
            details['cvd_momentum'] = 'moderate'
        
        # 3. Volume Surge (0-3)
        if vol_ratio > 3.0:
            score += 3
            details['volume'] = 'high'
        elif vol_ratio > 2.0:
            score += 2
            details['volume'] = 'moderate'
        elif vol_ratio > 1.3:
            score += 1
        
        # 4. OI Direction classification (0-2)
        price_change = 0
        if len(candles) >= 2:
            price_change = candles.iloc[-1]['close'] - candles.iloc[-2]['close']
        oi_direction = self._classify_oi_direction(oi_change, price_change)
        details['oi_direction'] = oi_direction
        if (direction == 'BUY' and oi_direction in ('NEW_LONGS', 'SHORT_COVERING')):
            score += 2
        elif (direction == 'SELL' and oi_direction in ('NEW_SHORTS', 'LONG_LIQUIDATION')):
            score += 2
        elif oi_direction and oi_direction != 'NEUTRAL':
            score -= 1
            details['oi_opposes'] = True
        
        # 5. Funding Rate (-1 to +1)
        funding_rate = binance_data.get('funding_rate', 0)
        if funding_rate:
            if direction == 'BUY' and funding_rate > 0.01:
                score -= 1
                details['funding_crowded'] = 'long'
            elif direction == 'SELL' and funding_rate < -0.01:
                score -= 1
                details['funding_crowded'] = 'short'
            elif direction == 'BUY' and funding_rate < -0.005:
                score += 1
                details['funding_support'] = True
            elif direction == 'SELL' and funding_rate > 0.005:
                score += 1
                details['funding_support'] = True
        
        # === Section 24.2: Liquidation Fuel Scoring (+2) ===
        liq_fuel_score, liq_fuel_details = self._calculate_liquidation_fuel(direction, binance_data)
        score += liq_fuel_score
        if liq_fuel_details:
            details['liquidation_fuel'] = liq_fuel_details
        
        # === FILTERS ===
        
        # HTF alignment (-4 to +3)
        if htf and htf.get('trend'):
            htf_trend = htf.get('trend')
            if (direction == 'BUY' and htf_trend == 'BULLISH') or \
               (direction == 'SELL' and htf_trend == 'BEARISH'):
                score += 3
                details['htf_sync'] = True
            elif (direction == 'BUY' and htf_trend == 'BEARISH') or \
                 (direction == 'SELL' and htf_trend == 'BULLISH'):
                score -= 4
                details['htf_counter'] = True
        
        # Delta Divergence (-2 to 0)
        delta_div = p1.get('cvd_divergence', 'NONE')
        if delta_div == 'BEARISH' and direction == 'BUY':
            score -= 2
            details['delta_divergence'] = 'bearish_warning'
        elif delta_div == 'BULLISH' and direction == 'SELL':
            score -= 2
            details['delta_divergence'] = 'bullish_warning'
        
        # === ICT BONUS (CONFIRMING — not gate) ===
        sweep_info = ict.get('liquidity_sweep', {})
        sweep_quality = 0
        if sweep_info.get('type'):
            sweep_quality = sweep_info.get('quality', 0)
            if sweep_quality >= 3:
                score += 2
                details['ict_sweep_bonus'] = True
            elif sweep_quality >= 2:
                score += 1
                details['ict_sweep_bonus'] = 'weak'
        
        structure = ict.get('structure', {})
        choch_status = structure.get('choch_status', '')
        if 'CHoCH_CONFIRMED' in str(choch_status):
            score += 1
            details['ict_choch_bonus'] = True
        
        # === Section 24.4: REGIME BONUS (Adaptive) ===
        # FIX: Issue 4 - Changed 'SWEEP' to 'LP' for institutional naming
        regime_bonus = self._get_regime_bonus('LP', regime)
        score += regime_bonus
        details['regime_bonus'] = regime_bonus
        
        # === PULLBACK FILTER (Session-Aware) ===
        details['peak_price'] = price
        details['pullback_status'] = 'none'
        pullback_threshold = session_thresh['pullback_pct']
        
        if len(candles) >= 5:
            recent_high = candles['high'].tail(5).max()
            recent_low = candles['low'].tail(5).min()
            
            if direction == 'BUY':
                move_distance = recent_high - recent_low
                pullback_pct = (recent_high - price) / move_distance if move_distance > 0 else 0
                if pullback_pct < pullback_threshold:
                    details['pullback_status'] = 'waiting'
                    details['pullback_pct'] = pullback_pct
                    details['pullback_warning'] = True
            else:
                move_distance = recent_high - recent_low
                pullback_pct = (price - recent_low) / move_distance if move_distance > 0 else 0
                if pullback_pct < pullback_threshold:
                    details['pullback_status'] = 'waiting'
                    details['pullback_pct'] = pullback_pct
                    details['pullback_warning'] = True
        
        # Get threshold from regime config
        lp_threshold = regime_thresholds.get('lp_threshold', self.thresholds['LP'])
        
        if score < lp_threshold:
            return {
                'pattern_type': 'LP',
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                'score': int(score),
                'max_score': 20,
                'reason': f"LP_BelowThreshold_{score}/{lp_threshold}",
                'details': details,
                'is_partial': True
            }
        
        # === Architecture Plan 2.2: SFP Close Price Confirmation ===
        # Section 2.2: Enforce Close Price Confirmation for SFP
        # Reject touch-only sweeps - must have candle close confirmation
        sweep_level = sweep_info.get('sweep_level', price)
        if sweep_info.get('type') and sweep_level > 0:
            # Use ICT analyzer's close confirmation check
            sfp_confirmed, sfp_details = self.ict_analyzer.check_sfp_close_confirmation(
                candles, sweep_level, direction
            )
            
            if not sfp_confirmed:
                status = sfp_details.get('status', 'unknown')
                logger.debug(f"❌ LP: SFP Close Confirmation failed - {status}")
                details['sfp_close_status'] = status
                details['sfp_close_details'] = sfp_details
                # Return partial signal with reduced score
                return {
                    'pattern_type': 'LP',
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                    'score': max(0, score - 3),
                    'max_score': 20,
                    'reason': f"LP_NoCloseConfirm_{score-3}/{lp_threshold}",
                    'entry_price': price,
                    'sl_boundary': sl_boundary if 'sl_boundary' in dir() else None,
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                    'details': details,
                    'is_partial': True
                }
            
            details['sfp_close_status'] = 'confirmed'
            details['sfp_close_details'] = sfp_details
        
        # === BODY RECLAIM FILTER ===
        sweep_level = sweep_info.get('sweep_level', price)
        if sweep_info.get('type') and sweep_level > 0:
            last_candle = candles.iloc[-1]
            body_close = last_candle['close']
            
            if direction == 'BUY':
                if sweep_level < price:
                    if body_close <= sweep_level:
                        if len(candles) >= 2:
                            prev_candle = candles.iloc[-2]
                            prev_close = prev_candle['close']
                            if prev_close <= sweep_level:
                                details['body_reclaim'] = 'waiting'
                                return {
                                    'pattern_type': 'LP',
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                                    'score': int(score) - 2,
                                    'max_score': 20,
                                    'reason': f"LP_WaitingReclaim_{score-2}/{lp_threshold}",
                                    'details': details,
                                    'is_partial': True
                                }
                        details['body_reclaim'] = 'partial'
            else:
                if sweep_level > price:
                    if body_close >= sweep_level:
                        if len(candles) >= 2:
                            prev_candle = candles.iloc[-2]
                            prev_close = prev_candle['close']
                            if prev_close >= sweep_level:
                                details['body_reclaim'] = 'waiting'
                                return {
                                    'pattern_type': 'LP',
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                                    'score': int(score) - 2,
                                    'max_score': 20,
                                    'reason': f"LP_WaitingReclaim_{score-2}/{lp_threshold}",
                                    'details': details,
                                    'is_partial': True
                                }
                        details['body_reclaim'] = 'partial'
            details['body_reclaim'] = 'confirmed'
        
        # === Entry and SL Calculation ===
        sweep_level = sweep_info.get('sweep_level', price)
        entry_price = sweep_level + (price - sweep_level) * 0.3
        
        if sweep_info.get('type') and sweep_level > 0 and abs(sweep_level - price) > 50:
            sl_boundary = sweep_level
        else:
            sl_boundary = None
        
        # Structure confirmation
        structure = ict.get('structure', {})
        choch_status = structure.get('choch_status', '')
        has_choch = 'CHoCH_CONFIRMED' in str(choch_status) if choch_status else False
        
        m5_structure = 'NONE'
        m5_structure_dir = 'NEUTRAL'
        if ict and 'break_of_structure' in ict:
            bos_data = ict['break_of_structure']
            m5_structure = bos_data.get('type', 'NONE').upper()
            m5_structure_dir = bos_data.get('direction', 'NEUTRAL').upper()
        
        # === Section 24.4: TP/SL Multipliers ===
        atr_multiplier = regime_thresholds.get('tp_multiplier', 2.0)
        sl_multiplier = regime_thresholds.get('sl_multiplier', 1.3)
        
        # === Architecture Plan 2.2: Institutional Grade Signal ===
        # Institutional Grade = True when Score >= 15 OR ICS >= 13
        ics = self._calculate_institutional_confluence_score(details, binance_data, p1)
        institutional_grade = score >= 15 or ics >= 13
        
        return {
            'pattern_type': 'LP',
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
            'institutional_grade': institutional_grade,
            'confluence_score': ics,
              # Architecture Plan TASK 2: LP requires higher RR for trend running
            'direction': 'LONG' if direction == 'BUY' else 'SHORT',
            'score': int(score),
            'max_score': 20,
            'entry_price': entry_price,
            'sl_boundary': sl_boundary,
            'has_choch': has_choch,
            'm5_structure': m5_structure,
            'm5_structure_dir': m5_structure_dir,
            'regime': regime,
            'tp_multiplier': atr_multiplier,
            'sl_multiplier': sl_multiplier,
            'reason': f"LP_Q{sweep_info.get('quality', 0)}_OI{details.get('oi_surge', 'none')}",
            'details': details,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def _evaluate_defensive_block_pattern(self, candles, price, p1, ict, htf, binance_data) -> Optional[Dict]:
        """
        Pattern 2: DB (Defensive Block) - formerly WALL
        v4.0: Institutional Grade with Iceberg detection and Refill rate analysis
        
        Requirements:
        1. Refill Logic: When price hits the wall and matching occurs, wall size must
           NOT decrease proportionally to traded volume (hidden order replenishment)
        2. OBI (Order Book Imbalance): OBI must be > 0.7 (wall side is denser)
        3. Volatility Scaling: Minimum wall size adjusts to ATR (High Vol = Large Wall)
        
        Dynamic Threshold:
        - AVG_VOL_1H = Mean(Volume[1]...Volume[60])
        - Wall_Significance = Wall_Size_USD / (AVG_VOL_1H * Price)
        - Gate Passed IF Wall_Significance > 3.0
        
        Stability & Spoofing Filter:
        - Store wall_start_time in self._active_walls
        - Confirm Gate IF (Time.now - wall_start_time) > 15 seconds
        
        Scoring Bonus:
        - Whale Confluence (+3): If a Whale Trade hits the wall and 
          the wall size decreases by less than 20 percent (Absorption).
        """
        # === Section 24.4: Get Regime-Based Thresholds ===
        regime = self._detect_regime_v3(candles)
        regime_thresholds = self._get_regime_thresholds(regime)
        
        walls = binance_data.get('walls', {})
        
        # === Update active walls tracking (Section 24.3) ===
        self._update_active_walls(walls, price)
        
        # === Section 24.3: FLOW GATE (PRIMARY) ===
        oi_change = binance_data.get('oi', {}).get('openInterestChange', 0)
        vol_ratio = self._get_volume_ratio(binance_data, p1)
        
        # Flow Gate: OI moving or Volume active (v3.6: Relaxed)
        has_oi_signal = abs(oi_change) > 0.05
        has_volume = vol_ratio > 1.2
        
        # v3.6: Relaxed check for significant walls 
        # If wall exists in binance_data, we proceed to check significance
        has_binance_wall = bool(walls.get('bid_walls') or walls.get('ask_walls'))
        if not has_binance_wall:
            return None
            
        # Check significance BEFORE gate to allow high-conviction walls to pass
        strongest_bid = walls.get('strongest_bid', {})
        strongest_ask = walls.get('strongest_ask', {})
        
        # Section 26.3: Wall Significance Bypass
        # Pre-calculate significance to check for bypass
        bid_val_pre = strongest_bid.get('value_usd', 0) if strongest_bid else 0
        ask_val_pre = strongest_ask.get('value_usd', 0) if strongest_ask else 0
        dominant_wall_val = max(bid_val_pre, ask_val_pre)
        
        if dominant_wall_val > 0:
            pre_significance, _ = self._calculate_wall_significance(dominant_wall_val, candles, price)
            
            # Bypass Gate if wall_significance > 5.0 (very large wall)
            if pre_significance > 5.0:
                logger.debug(f"✓ WALL BYPASS: Wall significance {pre_significance:.2f} > 5.0, skipping OI/Volume gate")
                # Skip the gate check and proceed directly to scoring
            elif not (has_oi_signal or has_volume):
                logger.debug(f"❌ WALL: Gate failed (OI: {oi_change:.4f}, Vol: {vol_ratio:.2f})")
                return None
        elif not (has_oi_signal or has_volume or (strongest_bid or strongest_ask)):
            logger.debug(f"❌ WALL: Gate failed (OI: {oi_change:.4f}, Vol: {vol_ratio:.2f})")
            return None
        
        # === Get wall prices ===
        bid_walls = walls.get('bid_walls', [])
        ask_walls = walls.get('ask_walls', [])
        
        strongest_bid = walls.get('strongest_bid', {})
        strongest_ask = walls.get('strongest_ask', {})
        
        # === WALL DEPTH CHECK: Minimum orders at wall level (v3.6: Reduced to 1) ===
        min_wall_orders = 1
        
        if strongest_bid and len(bid_walls) < min_wall_orders:
            logger.debug(f"❌ WALL: Insufficient bid wall depth ({len(bid_walls)} orders)")
            return None
        if strongest_ask and len(ask_walls) < min_wall_orders:
            logger.debug(f"❌ WALL: Insufficient ask wall depth ({len(ask_walls)} orders)")
            return None
        
        bid_price = strongest_bid.get('price', 0) if strongest_bid else 0
        ask_price = strongest_ask.get('price', 0) if strongest_ask else 0
        bid_val = strongest_bid.get('value_usd', 0) if strongest_bid else 0
        ask_val = strongest_ask.get('value_usd', 0) if strongest_ask else 0
        
        # === Section 24.3: Dynamic Wall Significance Check ===
        # Calculate significance for dominant wall
        if bid_val > ask_val and bid_price > 0:
            significance, is_significant = self._calculate_wall_significance(bid_val, candles, price)
            if not is_significant:
                logger.debug(f"❌ WALL: Wall significance too low ({significance:.2f} < {self.wall_significance_threshold})")
                return None
            details = {'wall_significance': significance}
        elif ask_val > 0:
            significance, is_significant = self._calculate_wall_significance(ask_val, candles, price)
            if not is_significant:
                logger.debug(f"❌ WALL: Wall significance too low ({significance:.2f} < {self.wall_significance_threshold})")
                return None
            details = {'wall_significance': significance}
        else:
            return None
        
        # === Section 24.3: Wall Stability Check (15 seconds) ===
        wall_to_check = 'bid' if bid_val > ask_val else 'ask'
        wall_price_to_check = bid_price if wall_to_check == 'bid' else ask_price
        
        is_stable, stability_details = self._check_wall_stability(wall_price_to_check, wall_to_check)
        details.update(stability_details)
        
        if not is_stable:
            logger.debug(f"⚠️ WALL: Wall not stable yet (age={stability_details.get('wall_age_seconds', 0):.1f}s)")
            return None
        
        
        # === NEW: Pre-emptive Wall Pulling Detection (Spoofing Guard) ===
        cancellation_rate = self.institutional_analyzer.calculate_cancellation_rate(
            wall_price_to_check, wall_to_check, lookback_seconds=5
        )
        details['cancellation_rate'] = cancellation_rate
        if cancellation_rate > 0.40:
            logger.debug(f"❌ DB: Spoofing Guard triggered - Cancellation rate {cancellation_rate*100:.0f}% > 40%")
            return None
        
        # === Section 41.1: Wall Longevity Bonus ===
        # Check if wall has been stable for > 15 minutes for Grade A bonus
        longevity_bonus, longevity_grade, longevity_details = self.institutional_analyzer.calculate_wall_longevity(
            wall_price_to_check, wall_to_check, min_stability_minutes=15.0
        )
        details['wall_longevity'] = longevity_details
        if longevity_grade == 'A':
            details['institutional_defense_grade'] = 'A'
            logger.debug(f"✓ DB: Institutional Defense Grade A - {longevity_details.get('reason', '')}")
        else:
            # === NEW: Multi-Tier DB Signals (Grade B Momentum Block) ===
            longevity_mins = longevity_details.get('longevity_minutes', 0)
            refill_rate_val = stability_details.get('refill_rate', 0)
            delta_surge = abs(p1.get('cvd_delta', 0)) > abs(p1.get('avg_delta', 1)) * 1.5
            
            if longevity_mins > 3 and refill_rate_val > 1.5 and delta_surge:
                details['institutional_defense_grade'] = 'B'
                db_idi_score += 2
                logger.debug(f"✓ DB: Momentum Block Grade B - Longevity: {longevity_mins:.1f}m, Refill: {refill_rate_val:.1f}x, Delta Surge: True")
        
        # === Section 41.3: Erosion Guard ===
        # Check if wall is being eroded by aggressive market orders
        aggressor_vol = binance_data.get('aggressor_volume', 0)
        refill_rate = stability_details.get('refill_rate', 0)
        
        is_safe, erosion_msg = self.institutional_analyzer.check_erosion_guard(
            wall_price_to_check, wall_to_check, aggressor_vol, refill_rate
        )
        if not is_safe:
            logger.debug(f"❌ DB: Erosion Guard triggered - {erosion_msg}")
            return None
        details['erosion_guard'] = erosion_msg
        
        # === Section 2: DB IDI Engine - Flow-Enhanced Intelligence ===
        # Initialize score for DB pattern
        db_idi_score = 0
        
        # 2.1: Z-Score Significance Check
        # Wall must be > 2.5 SD above average to be institutional
        wall_size_usd = bid_val if wall_to_check == 'bid' else ask_val
        avg_wall_size = binance_data.get('avg_wall_size', wall_size_usd * 0.5)  # Default to 50% of current
        std_wall_size = binance_data.get('std_wall_size', wall_size_usd * 0.2)  # Default to 20% std
        
        z_score, z_significance = self.institutional_analyzer.calculate_wall_zscore(
            wall_size_usd, avg_wall_size, std_wall_size
        )
        details['z_score'] = z_score
        details['z_significance'] = z_significance
        
        if z_score >= 2.5:
            db_idi_score += 3  # Strong institutional wall
            details['z_score_bonus'] = 3
            logger.debug(f"✓ DB: Z-Score {z_score:.2f} ({z_significance}) - Institutional wall confirmed")
        elif z_score >= 2.0:
            db_idi_score += 1  # Moderate wall
            details['z_score_bonus'] = 1
        
        # 2.2: Wall-Specific DER Check (Iceberg Detection)
        # Calculate DER specifically at wall level
        delta_at_wall = p1.get('cvd_delta', 0)
        price_move_at_wall = abs(candles.iloc[-1]['close'] - candles.iloc[-2]['close']) if len(candles) >= 2 else 0
        volume_at_wall = binance_data.get('volume', {}).get('volume', 1)
        
        der_score, der_description = self.institutional_analyzer.calculate_wall_specific_der(
            wall_price_to_check, wall_to_check, delta_at_wall, price_move_at_wall, volume_at_wall
        )
        details['wall_der_score'] = der_score
        details['wall_der_description'] = der_description
        
        if der_score >= 3.0:
            db_idi_score += 5  # Strong iceberg detection
            details['iceberg_bonus'] = 5
            logger.debug(f"✓ DB: {der_description}")
        elif der_score >= 2.0:
            db_idi_score += 2
            details['iceberg_bonus'] = 2
        
        # 2.3: Stacking vs Pulling Detection
        # Analyze order book movements to detect spoofing
        order_book_history = list(self.institutional_analyzer.wall_history_cache)
        behavior, confidence, stack_details = self.institutional_analyzer.detect_stacking_vs_pulling(
            wall_price_to_check, wall_to_check, order_book_history
        )
        details['stacking_behavior'] = behavior
        details['stacking_confidence'] = confidence
        
        if behavior == "STACKING" and confidence > 0.5:
            db_idi_score += 2  # Real institutional interest
            details['stacking_bonus'] = 2
            logger.debug(f"✓ DB: Stacking detected (confidence: {confidence:.2f})")
        elif behavior == "PULLING" and confidence > 0.5:
            # Potential spoofing - reduce score
            db_idi_score -= 2
            details['spoofing_penalty'] = -2
            logger.debug(f"⚠️ DB: Pulling detected (potential spoofing) - confidence: {confidence:.2f}")
        
        # 2.4: Aggressor Exhaustion Check
        # Check if CVD slope is flat before wall contact
        cvd_values = p1.get('cvd_series', [])
        if cvd_values and len(cvd_values) >= 10:
            is_exhausted, cvd_slope, exhaustion_desc = self.institutional_analyzer.calculate_aggressor_exhaustion(cvd_values)
            details['aggressor_exhausted'] = is_exhausted
            details['cvd_slope'] = cvd_slope
            
            if is_exhausted:
                db_idi_score += 2  # Aggressor exhaustion = Wall likely to hold
                details['exhaustion_bonus'] = 2
                logger.debug(f"✓ DB: {exhaustion_desc}")
        
        # Update wall cluster manager for fuzzy memory tracking
        self.wall_cluster_manager.update_wall(
            wall_price_to_check, wall_size_usd, wall_to_check, datetime.now(timezone.utc)
        )
        
        # Get bucket stats for additional scoring
        bucket_stats = self.wall_cluster_manager.get_bucket_stats(wall_price_to_check)
        details['wall_bucket_stats'] = bucket_stats
        
        if bucket_stats.get('total_updates', 0) >= 5:
            # Wall has been updated multiple times in this bucket = Strong presence
            db_idi_score += 1
            details['bucket_persistence_bonus'] = 1
        
        # === Wall Distance Ratio Filter ===
        if bid_price > 0 and ask_price > 0:
            wall_range = abs(ask_price - bid_price)
            if wall_range > 0:
                dist_to_bid = abs(price - bid_price) / wall_range
                
                if 0.3 < dist_to_bid < 0.7:
                    logger.debug(f"❌ WALL: Price in middle zone (dist_to_bid={dist_to_bid:.2f})")
                    return None
                
                details['wall_distance_ratio'] = dist_to_bid
        
        # === Triple Confluence Validation ===
        confluence_sources = []
        
        # Source 1: Wall Direction
        wall_direction = None
        if bid_val > ask_val * 1.5 and bid_val > 0:
            wall_direction = 'BUY'
            confluence_sources.append('wall')
        elif ask_val > bid_val * 1.5 and ask_val > 0:
            wall_direction = 'SELL'
            confluence_sources.append('wall')
        
        if not wall_direction:
            logger.debug(f"❌ WALL: No dominant wall (bid={bid_val}, ask={ask_val})")
            return None
        
        # Source 2: CVD Direction
        cvd_delta = p1.get('cvd_delta', 0)
        cvd_direction = None
        cvd_min_threshold = 0.05
        if cvd_delta > cvd_min_threshold:
            cvd_direction = 'BUY'
            confluence_sources.append('cvd')
        elif cvd_delta < -cvd_min_threshold:
            cvd_direction = 'SELL'
            confluence_sources.append('cvd')
        
        # Source 3: Zone Context
        zone_context = ict.get('zone_context', 'RANGE')
        zone_direction = 'BUY' if zone_context == 'DISCOUNT' else ('SELL' if zone_context == 'PREMIUM' else None)
        if zone_direction:
            confluence_sources.append('zone')
        
        # Determine final direction (majority wins)
        direction_votes = {'BUY': 0, 'SELL': 0}
        for src in confluence_sources:
            if src == 'wall' and wall_direction:
                direction_votes[wall_direction] += 1
            elif src == 'cvd' and cvd_direction:
                direction_votes[cvd_direction] += 1
            elif src == 'zone' and zone_direction:
                direction_votes[zone_direction] += 1
        
        direction = 'BUY' if direction_votes['BUY'] >= direction_votes['SELL'] else 'SELL'
        
        # === CVD Exhaustion Detection ===
        cvd_exhaustion = False
        cvd_tilt = p1.get('cvd_tilt', None)
        
        if direction == 'BUY':
            if abs(cvd_delta) < 1.0 and (cvd_tilt is None or cvd_tilt > 0.05):
                cvd_exhaustion = True
        else:
            if abs(cvd_delta) < 1.0 and (cvd_tilt is None or cvd_tilt < -0.05):
                cvd_exhaustion = True
        
        # === Velocity Check (Avoid Wall Absorption) ===
        if len(candles) >= 3:
            price_changes = candles['close'].diff().tail(3)
            avg_velocity = abs(price_changes.mean())
            price_range = max(ask_price - bid_price, price * 0.001)
            
            # ATR Spike Check
            high = candles['high'].values
            low = candles['low'].values
            close = candles['close'].values
            tr = np.maximum(high[1:] - low[1:], 
                           np.maximum(np.abs(high[1:] - close[:-1]), 
                                      np.abs(low[1:] - close[:-1])))
            atr_current = np.mean(tr[-3:]) if len(tr) >= 3 else np.mean(tr)
            atr_avg = np.mean(tr[-20:]) if len(tr) >= 20 else np.mean(tr)
            atr_spike_ratio = atr_current / atr_avg if atr_avg > 0 else 1.0
            
            if atr_spike_ratio > 2.0:
                logger.debug(f"⚠️ WALL: ATR Spike too high (ratio={atr_spike_ratio:.2f})")
                return None
            
            if direction == 'BUY' and bid_price > 0:
                distance_to_wall = price - bid_price
                approach_speed = avg_velocity / price_range if price_range > 0 else 0
                if distance_to_wall < price_range * 0.3 and approach_speed > 0.5:
                    logger.debug(f"⚠️ WALL: High velocity toward bid wall (speed={approach_speed:.2f})")
                    return None
            elif direction == 'SELL' and ask_price > 0:
                distance_to_wall = ask_price - price
                approach_speed = avg_velocity / price_range if price_range > 0 else 0
                if distance_to_wall < price_range * 0.3 and approach_speed > 0.5:
                    logger.debug(f"⚠️ WALL: High velocity toward ask wall (speed={approach_speed:.2f})")
                    return None
        
        # === Value Area Alignment ===
        volume_profile_data = ict.get('volume_profile', {})
        if volume_profile_data:
            vah = volume_profile_data.get('value_area_high')
            val = volume_profile_data.get('value_area_low')
            
            if direction == 'BUY' and val and bid_price > 0:
                val_distance_pct = abs(bid_price - val) / val if val > 0 else 1.0
                if val_distance_pct < 0.02:
                    details['value_area_alignment'] = 'val_bounce'
            elif direction == 'SELL' and vah and ask_price > 0:
                vah_distance_pct = abs(ask_price - vah) / vah if vah > 0 else 1.0
                if vah_distance_pct < 0.02:
                    details['value_area_alignment'] = 'vah_bounce'
        
        # === Section 24.3: Whale Confluence Check (+3) ===
        wall_price_for_whale = bid_price if direction == 'BUY' else ask_price
        whale_confluence_score, whale_details = self._check_whale_confluence(wall_price_for_whale, direction, binance_data)
        score = whale_confluence_score + db_idi_score  # Add DB IDI Engine score
        if whale_details:
            details['whale_confluence'] = whale_details
        
        # Add DB IDI score to details
        if db_idi_score > 0:
            details['db_idi_score'] = db_idi_score
            logger.debug(f"✓ DB: IDI Engine score +{db_idi_score} (Z-Score: {details.get('z_score', 0):.2f}, DER: {details.get('wall_der_score', 0)})")
        
        # === Section 41.2: Proximity Entry Logic ===
        # Allow entry when price is within 0.02% of wall with CVD Spike + Price Rejection
        proximity_threshold_pct = 0.0002  # 0.02%
        proximity_entry_allowed = False
        
        if direction == 'BUY' and bid_price > 0:
            distance_to_wall = abs(price - bid_price) / price
            within_proximity = distance_to_wall <= proximity_threshold_pct
            
            # Check for CVD Spike (strong buying pressure)
            cvd_spike = abs(cvd_delta) > 0.3  # Higher threshold for proximity entry
            
            # Check for price rejection (candle wick rejection)
            if len(candles) >= 1:
                last_candle = candles.iloc[-1]
                candle_body = abs(last_candle['close'] - last_candle['open'])
                candle_wick = last_candle['low'] - min(last_candle['open'], last_candle['close']) if direction == 'BUY' else 0
                price_rejection = candle_wick > candle_body * 1.5  # Wick > 1.5x body
            else:
                price_rejection = False
            
            if within_proximity and cvd_spike and price_rejection:
                proximity_entry_allowed = True
                details['proximity_entry'] = f"Within {distance_to_wall*100:.3f}% with CVD Spike + Rejection"
                logger.debug(f"✓ DB: Proximity entry allowed - {distance_to_wall*100:.3f}% from wall")
        
        elif direction == 'SELL' and ask_price > 0:
            distance_to_wall = abs(price - ask_price) / price
            within_proximity = distance_to_wall <= proximity_threshold_pct
            
            # Check for CVD Spike (strong selling pressure)
            cvd_spike = abs(cvd_delta) > 0.3
            
            # Check for price rejection
            if len(candles) >= 1:
                last_candle = candles.iloc[-1]
                candle_body = abs(last_candle['close'] - last_candle['open'])
                candle_wick = max(last_candle['open'], last_candle['close']) - last_candle['high'] if direction == 'SELL' else 0
                price_rejection = candle_wick > candle_body * 1.5
            else:
                price_rejection = False
            
            if within_proximity and cvd_spike and price_rejection:
                proximity_entry_allowed = True
                details['proximity_entry'] = f"Within {distance_to_wall*100:.3f}% with CVD Spike + Rejection"
                logger.debug(f"✓ DB: Proximity entry allowed - {distance_to_wall*100:.3f}% from wall")
        
        # === Sniper Entry ===
        # Section 56 Bug #8 FIX: Expanded from 0.05% (~$47) to 0.2% (~$190)
        # BTC moves $100-$500/minute, $47 range was too tight to catch
        bounce_threshold = price * 0.002  # was 0.0005 (0.05%)
        
        # Get effective proximity threshold (0.2% default)
        effective_proximity_threshold = getattr(self, 'proximity_threshold_pct', 0.0002)
        
        if direction == 'BUY' and bid_price > 0:
            wall_to_use = bid_price
            distance_to_wall = abs(price - bid_price) / price
            
            if price >= bid_price - bounce_threshold:
                # Price is within bounce zone
                entry_price = max(price, bid_price + (price - bid_price) * 0.2)
                details['entry_type'] = 'bounce_entry'
            elif proximity_entry_allowed or distance_to_wall <= effective_proximity_threshold:
                # Section 41.2: Allow proximity entry
                entry_price = price
                details['entry_type'] = 'proximity'
            elif score >= db_threshold + 3:
                # Bug #8 FIX: High-score signal can enter as limit order before wall
                # Extra high score = allow pre-wall limit entry
                entry_price = bid_price - bounce_threshold
                details['entry_type'] = 'pre_wall_limit'
                logger.debug(f"✓ DB: Pre-wall limit entry @ {entry_price:.2f} (score: {score}, threshold: {db_threshold})")
            else:
                return None
        elif direction == 'SELL' and ask_price > 0:
            wall_to_use = ask_price
            distance_to_wall = abs(price - ask_price) / price
            
            if price <= ask_price + bounce_threshold:
                # Price is within bounce zone
                entry_price = min(price, ask_price - (ask_price - price) * 0.2)
                details['entry_type'] = 'bounce_entry'
            elif proximity_entry_allowed or distance_to_wall <= effective_proximity_threshold:
                # Section 41.2: Allow proximity entry
                entry_price = price
                details['entry_type'] = 'proximity'
            elif score >= db_threshold + 3:
                # Bug #8 FIX: High-score signal can enter as limit order before wall
                entry_price = ask_price + bounce_threshold
                details['entry_type'] = 'pre_wall_limit'
                logger.debug(f"✓ DB: Pre-wall limit entry @ {entry_price:.2f} (score: {score}, threshold: {db_threshold})")
            else:
                return None
        else:
            wall_to_use = price
            entry_price = price
        
        # === Calculate Score ===
        # Confluence score (0-6)
        details['confluence_count'] = len(confluence_sources)
        details['confluence_sources'] = confluence_sources
        score += min(len(confluence_sources) * 2, 6)
        
        # Wall strength score (0-4)
        if direction == 'BUY':
            wall_usd = bid_val
        else:
            wall_usd = ask_val
        
        if wall_usd >= 500000:
            score += 4
            details['real_wall'] = 'very_strong'
        elif wall_usd >= 200000:
            score += 3
            details['real_wall'] = 'strong'
        elif wall_usd >= 100000:
            score += 2
            details['real_wall'] = 'moderate'
        else:
            score += 1
            details['real_wall'] = 'weak'
        
        details['wall_value'] = f"${wall_usd/1000:.0f}K"
        
        # === Section 41.1: Wall Longevity Bonus (+5 for Grade A) ===
        if longevity_bonus > 0:
            score += longevity_bonus
            details['longevity_bonus'] = longevity_bonus
            if longevity_grade == 'A':
                details['institutional_defense_grade'] = 'A'
        
        # CVD Exhaustion bonus
        if cvd_exhaustion:
            score += 3
            details['cvd_exhaustion'] = True
        
        # Value Area Alignment Score
        if details.get('value_area_alignment') == 'val_bounce' or details.get('value_area_alignment') == 'vah_bounce':
            score += 2
            details['va_alignment_bonus'] = True
        
        # Zone depth bonus
        zone = ict.get('zone_info', {})
        retracement = zone.get('retracement_pct', 0)
        if retracement >= 70:
            score += 2
            details['zone_depth'] = 'deep'
        elif retracement >= 50:
            score += 1
            details['zone_depth'] = 'moderate'
        
        # OI stability + OI Direction
        if -0.3 < oi_change < 0.5:
            score += 1
            details['oi_stable'] = True
        
        price_change = 0
        if len(candles) >= 2:
            price_change = candles.iloc[-1]['close'] - candles.iloc[-2]['close']
        oi_direction = self._classify_oi_direction(oi_change, price_change)
        details['oi_direction'] = oi_direction
        if (direction == 'BUY' and oi_direction in ('NEW_LONGS', 'SHORT_COVERING')):
            score += 1
        elif (direction == 'SELL' and oi_direction in ('NEW_SHORTS', 'LONG_LIQUIDATION')):
            score += 1
        elif oi_direction and oi_direction != 'NEUTRAL':
            score -= 1
            details['oi_opposes'] = True
        
        # HTF alignment
        if htf and htf.get('trend'):
            htf_trend_val = htf.get('trend')
            if (direction == 'BUY' and htf_trend_val == 'BULLISH') or \
               (direction == 'SELL' and htf_trend_val == 'BEARISH'):
                score += 2
                details['htf_sync'] = True
            elif (direction == 'BUY' and htf_trend_val == 'BEARISH') or \
                 (direction == 'SELL' and htf_trend_val == 'BULLISH'):
                score -= 4
                details['htf_counter'] = True
        
        # Delta Divergence
        delta_div = p1.get('cvd_divergence', 'NONE')
        if delta_div == 'BEARISH' and direction == 'BUY':
            score -= 2
            details['delta_divergence'] = 'bearish_warning'
        elif delta_div == 'BULLISH' and direction == 'SELL':
            score -= 2
            details['delta_divergence'] = 'bullish_warning'
        
        # Funding Rate
        funding_rate = binance_data.get('funding_rate', 0)
        if funding_rate:
            if direction == 'BUY' and funding_rate > 0.01:
                score -= 1
                details['funding_crowded'] = 'long'
            elif direction == 'SELL' and funding_rate < -0.01:
                score -= 1
                details['funding_crowded'] = 'short'
            elif direction == 'BUY' and funding_rate < -0.005:
                score += 1
                details['funding_support'] = True
            elif direction == 'SELL' and funding_rate > 0.005:
                score += 1
                details['funding_support'] = True
        
        # Volume Exhaustion
        if self._detect_volume_exhaustion(candles, lookback=3):
            score += 2
            details['volume_exhaustion'] = True
        
        # === Section 24.4: Regime Bonus (Adaptive) ===
        regime_bonus = self._get_regime_bonus('DB', regime)
        score += regime_bonus
        details['regime'] = regime
        details['regime_bonus'] = regime_bonus
        
        # ICT bonus
        zone_quality = self._evaluate_zone_quality(ict, price)
        if zone_quality.get('type', 'NONE') != 'NONE':
            zq = zone_quality.get('quality', 0)
            if zq >= 2:
                score += 2
                details['ict_zone_bonus'] = True
            elif zq >= 1:
                score += 1
                details['ict_zone_bonus'] = 'weak'
        
        # Get threshold from regime config
        db_threshold = regime_thresholds.get('db_threshold', self.thresholds['DB'])
        
        if score < db_threshold:
            return {
                'pattern_type': 'DB',
                'score': int(score),
                'max_score': 22,
                'reason': f"DB_BelowThreshold_{score}/{db_threshold}",
                'details': details,
                'is_partial': True
            }
        
        # === SL Boundary ===
        sl_boundary = wall_to_use
        
        # Structure confirmation
        structure = ict.get('structure', {})
        choch_status = structure.get('choch_status', '')
        has_choch = 'CHoCH_CONFIRMED' in str(choch_status) if choch_status else False
        
        m5_structure = 'NONE'
        m5_structure_dir = 'NEUTRAL'
        if ict and 'break_of_structure' in ict:
            bos_data = ict['break_of_structure']
            m5_structure = bos_data.get('type', 'NONE').upper()
            m5_structure_dir = bos_data.get('direction', 'NEUTRAL').upper()
        
        # === Section 24.4: TP/SL Multipliers ===
        atr_multiplier = regime_thresholds.get('tp_multiplier', 2.0)
        sl_multiplier = regime_thresholds.get('sl_multiplier', 1.3)
        
        return {
            'pattern_type': 'DB',
            'institutional_grade': score >= 15,
            'confluence_score': len(confluence_sources) * 2 + (3 if cvd_exhaustion else 0),
              # Architecture Plan TASK 2: DB requires lower RR for quick profit
            'direction': 'LONG' if direction == 'BUY' else 'SHORT',
            'score': int(score),
            'max_score': 22,
            'entry_price': entry_price,
            'sl_boundary': sl_boundary,
            'wall_price': wall_to_use,
            'has_choch': has_choch,
            'm5_structure': m5_structure,
            'm5_structure_dir': m5_structure_dir,
            'regime': regime,
            'tp_multiplier': atr_multiplier,
            'sl_multiplier': sl_multiplier,
            'reason': f"DB_{details.get('real_wall', 'none')}_C{len(confluence_sources)}",
            'details': details,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def _evaluate_delta_absorption_pattern(self, candles, price, p1, ict, htf, binance_data) -> Optional[Dict]:
        """
        Pattern 3: DA (Delta Absorption) - formerly CVD_REV/ZONE
        v4.0: Institutional Grade with DER (Delta Efficiency Ratio) analysis
        
        Requirements:
        1. DER (Delta Efficiency Ratio): Calculate (Absolute Delta / Price Movement)
           If DER > 3.0, indicates heavy effort without result = institutional absorption
        2. MSS Confirmation: Must have Market Structure Shift (M1 CHoCH) after DER Spike
           before sending trade signal (prevents early entries)
        
        Gate Implementation:
        1. abs(cvd_delta) >= current_cvd_threshold (Default: 0.15)
        2. CHECK price IN ict_zone (OB or FVG from ICTAnalyzer)
        3. CHECK divergence: Compare Close with CVD over last 3 candles
        
        Detailed Scoring:
        1. Wick Cluster (+3): Segment candle into 4 quads. If 60% of total Delta 
           is in the upper/lower 25% quad (the wick), add points.
        2. Exhaustion (+2): Volume[0] < EMA(Volume, 5) * 0.8 AND hits_new_structural_extreme
        3. Volume Spike (+2): Volume[0] > EMA(Volume, 5) * 2.0
        """
        # === Section 24.4: Get Regime-Based Thresholds ===
        regime = self._detect_regime_v3(candles)
        regime_thresholds = self._get_regime_thresholds(regime)
        
        # === Session-Aware Thresholds ===
        session_thresh = self._get_session_thresholds()
        
        # === Section 24.1: FLOW GATE (PRIMARY) ===
        cvd_delta = p1.get('cvd_delta', 0)
        cvd_momentum = self._calculate_cvd_momentum(candles, lookback=5)
        vol_ratio = self._get_volume_ratio(binance_data, p1)
        
        # Gate 1: CVD threshold (default 0.15 from Section 24.1)
        has_cvd_threshold = abs(cvd_delta) >= self.da_cvd_threshold
        
        # Gate 2: ICT Zone check
        zone_quality = self._evaluate_zone_quality(ict, price)
        in_zone = zone_quality.get('type', 'NONE') != 'NONE'
        
        # Gate 3: Divergence check
        divergence_type, divergence_details = self._check_cvd_divergence(candles, p1)
        
        # Section 24.1: Need CVD threshold + at least zone OR divergence
        if not has_cvd_threshold:
            return None
        if not (in_zone or divergence_type != 'NONE'):
            return None
        
        score = 0
        details = {
            'flow_gate': True,
            'regime': regime,
            'divergence': divergence_type
        }
        
        # === SECTION 11 DA-1: DER-Based Reversal Direction Logic ===
        # DA = Absorption: ราคาไม่ขยับแม้ CVD แรง → เทรดตรงข้าม CVD
        # คำนวณ DER (Delta Efficiency Ratio) = |Delta| / |Price Movement|
        der = 1.0
        absorption_confirmed = False
        if len(candles) >= 2:
            prev_close = candles.iloc[-2]['close']
            curr_close = candles.iloc[-1]['close']
            price_move_pct = abs(curr_close - prev_close) / max(prev_close, 1)
            cvd_intensity = abs(cvd_delta)
            der = cvd_intensity / max(price_move_pct, 0.0001)

            # v6.0: Reduced DER threshold from 3.0 to 2.0 per architecture plan Section 4.4
            if der >= 2.0:  # v6.0: was 3.0
                absorption_confirmed = True
                # Reverse direction: CVD BUY แต่ราคาไม่ขึ้น = SELL
                if cvd_delta > 0:
                    direction = 'SELL'  # Sellers absorbing buying pressure
                else:
                    direction = 'BUY'   # Buyers absorbing selling pressure
                details['direction_source'] = 'der_reversal'
            else:
                # ไม่มี absorption ชัดเจน → ใช้ CVD direction ปกติ
                direction = cvd_momentum['direction']
                if direction == 'NEUTRAL':
                    direction = 'BUY' if cvd_delta > 0 else 'SELL'
                details['direction_source'] = 'cvd_momentum'

        details['absorption_confirmed'] = absorption_confirmed
        details['der'] = round(der, 2)
        
        # Check divergence conflict
        if divergence_type == 'BULLISH' and direction == 'SELL':
            score -= 2
            details['divergence_conflict'] = True
        elif divergence_type == 'BEARISH' and direction == 'BUY':
            score -= 2
            details['divergence_conflict'] = True
        
        # === Section 24.1: FLOW SCORING (PRIMARY — max ~22) ===
        
        # 1. CVD Strength (0-4)
        if abs(cvd_delta) > 0.4:
            score += 4
            details['cvd_strength'] = 'strong'
        elif abs(cvd_delta) > 0.2:
            score += 3
            details['cvd_strength'] = 'moderate'
        elif abs(cvd_delta) > 0.15:
            score += 2
            details['cvd_strength'] = 'weak'
        else:
            score += 1
        
        # 2. CVD Momentum strength (0-3)
        if cvd_momentum['strength'] >= 3:
            score += 3
        elif cvd_momentum['strength'] >= 2:
            score += 2
        elif cvd_momentum['is_shifting']:
            score += 1
            details['cvd_shifting'] = True
        
        # 3. Volume activity (0-2)
        if vol_ratio > 2.0:
            score += 2
            details['volume'] = 'high'
        elif vol_ratio > 1.2:
            score += 1
        
        # 4. OI Direction (0-2)
        oi_change = binance_data.get('oi', {}).get('openInterestChange', 0)
        price_change = 0
        if len(candles) >= 2:
            price_change = candles.iloc[-1]['close'] - candles.iloc[-2]['close']
        oi_direction = self._classify_oi_direction(oi_change, price_change)
        details['oi_direction'] = oi_direction
        if (direction == 'BUY' and oi_direction in ('NEW_LONGS', 'SHORT_COVERING')):
            score += 2
        elif (direction == 'SELL' and oi_direction in ('NEW_SHORTS', 'LONG_LIQUIDATION')):
            score += 2
        elif oi_direction and oi_direction != 'NEUTRAL':
            score -= 1
            details['oi_opposes'] = True
        
        # 5. Funding Rate (-1 to +1)
        funding_rate = binance_data.get('funding_rate', 0)
        if funding_rate:
            if direction == 'BUY' and funding_rate > 0.01:
                score -= 1
                details['funding_crowded'] = 'long'
            elif direction == 'SELL' and funding_rate < -0.01:
                score -= 1
                details['funding_crowded'] = 'short'
            elif direction == 'BUY' and funding_rate < -0.005:
                score += 1
                details['funding_support'] = True
            elif direction == 'SELL' and funding_rate > 0.005:
                score += 1
                details['funding_support'] = True
        
        # === Section 24.1: Wick Cluster Scoring (+3) ===
        wick_cluster_score, wick_details = self._calculate_wick_cluster(candles, direction)
        score += wick_cluster_score
        if wick_details:
            details.update(wick_details)
        
        # === Section 24.1: Exhaustion Scoring (+2) ===
        is_exhausted, exhaustion_details = self._check_volume_exhaustion_v3(candles)
        if is_exhausted:
            score += 2
            details['volume_exhaustion'] = True
            details.update(exhaustion_details)
        
        # === Section 24.1: Volume Spike Scoring (+2) ===
        is_spike, spike_details = self._check_volume_spike(candles)
        if is_spike:
            score += 2
            details['volume_spike'] = True
            details.update(spike_details)
        
        # === Section 40.2: CVD Slope Divergence Analysis (+3 for Institutional Exhaustion) ===
        # Check for price/CVD divergence to detect institutional exhaustion
        if len(candles) >= 5:
            # Get CVD values from candles (approximate from delta)
            cvd_values = []
            price_highs = []
            price_lows = []
            
            for i in range(-5, 0):
                if 'cvd_delta' in candles.iloc[i]:
                    cvd_values.append(candles.iloc[i]['cvd_delta'])
                else:
                    # === SECTION 11 DA-3: Use candle-based CVD approx (body ratio x volume)
                    cvd_values.append(self._get_candle_cvd_approx(candles.iloc[i]))
                price_highs.append(candles.iloc[i]['high'])
                price_lows.append(candles.iloc[i]['low'])
            
            if len(cvd_values) >= 5:
                cvd_result = self.order_flow.calculate_cvd_slope(cvd_values, lookback=5)
                cvd_slope = cvd_result.get('slope', 0)
                
                # Check for divergence
                divergence_result = self.order_flow.check_cvd_price_divergence(
                    cvd_values, price_highs, price_lows, lookback=5
                )
                
                if divergence_result.get('divergence'):
                    bonus = divergence_result.get('bonus_score', 0)
                    score += bonus
                    details['cvd_slope_divergence'] = divergence_result.get('type', 'UNKNOWN')
                    details['cvd_slope'] = cvd_slope
                    details['cvd_divergence_bonus'] = bonus
                    logger.debug(f"📊 CVD Slope Divergence: {divergence_result.get('description')}")
        
        # === FILTERS ===
        
        # HTF alignment (-4 to +3)
        if htf and htf.get('trend'):
            htf_trend_val = htf.get('trend')
            if (direction == 'BUY' and htf_trend_val == 'BULLISH') or \
               (direction == 'SELL' and htf_trend_val == 'BEARISH'):
                score += 3
                details['htf_sync'] = True
            elif (direction == 'BUY' and htf_trend_val == 'BEARISH') or \
                 (direction == 'SELL' and htf_trend_val == 'BULLISH'):
                score -= 4
                details['htf_counter'] = True
        
        # Delta Divergence (-2 to 0) - from existing logic
        delta_div = p1.get('cvd_divergence', 'NONE')
        if delta_div == 'BEARISH' and direction == 'BUY':
            score -= 2
            details['delta_divergence'] = 'bearish_warning'
        elif delta_div == 'BULLISH' and direction == 'SELL':
            score -= 2
            details['delta_divergence'] = 'bullish_warning'
        
        # === ICT BONUS (CONFIRMING — not gate) ===
        if in_zone:
            # Zone quality bonus (0-2)
            zq = zone_quality.get('quality', 0)
            if zq >= 3:
                score += 2
                details['ict_zone_bonus'] = 'strong'
            elif zq >= 1:
                score += 1
                details['ict_zone_bonus'] = 'weak'
            
            # Entry position bonus (0-1)
            position_score = self._evaluate_entry_position(ict, price, zone_quality['type'])
            if position_score >= 3:
                score += 1
                details['ict_position_bonus'] = True

            # === SECTION 11 DA-5: HTF Zone Alignment Check (+3 max) ===
            # Zone ที่ align กับ H1 OB/FVG มีความน่าเชื่อถือสูงกว่า
            htf_zone_bonus, htf_zone_msg = self._check_htf_zone_alignment(
                zone_quality, htf, direction
            )
            if htf_zone_bonus > 0:
                score += htf_zone_bonus
                details['htf_zone_alignment'] = htf_zone_msg
                details['htf_zone_bonus'] = htf_zone_bonus
        
        # Zone context bonus (0-1)
        zone_context = ict.get('zone_context', 'RANGE')
        if (direction == 'BUY' and zone_context == 'DISCOUNT') or \
           (direction == 'SELL' and zone_context == 'PREMIUM'):
            score += 1
            details['context_aligned'] = True
        
        # === PRICE ACTION CONFIRMATION ===
        pa_bonus = self._check_price_action_rejection(candles, price, direction)
        if pa_bonus > 0:
            score += pa_bonus
            details['price_action_bonus'] = pa_bonus
        
        # === Section 24.4: REGIME BONUS (Adaptive) ===
        regime_bonus = self._get_regime_bonus('DA', regime)
        score += regime_bonus
        details['regime_bonus'] = regime_bonus
        
        # === PULLBACK FILTER (Session-Aware) ===
        pullback_threshold = session_thresh['pullback_pct']
        
        details['peak_price'] = price
        details['pullback_status'] = 'none'
        
        if len(candles) >= 5:
            recent_high = candles['high'].tail(5).max()
            recent_low = candles['low'].tail(5).min()
            move_distance = recent_high - recent_low
            
            if direction == 'BUY':
                price_position = (price - recent_low) / move_distance if move_distance > 0 else 0.5
                entry_threshold = 1.0 - pullback_threshold
                if price_position > entry_threshold:
                    details['pullback_status'] = 'premium_zone'
                    details['pullback_warning'] = True
                    details['price_position'] = price_position
            else:
                price_position = (price - recent_low) / move_distance if move_distance > 0 else 0.5
                if price_position < pullback_threshold:
                    details['pullback_status'] = 'discount_zone'
                    details['pullback_warning'] = True
                    details['price_position'] = price_position
        
        # === SECTION 11 DA-2: Zone Edge Entry (institutional grade) ===
        # Entry ที่ edge ของ zone (ไม่ใช่ mid-point) เพื่อ RR ที่ดีกว่า
        if in_zone:
            zone_high = zone_quality.get('high', price)
            zone_low = zone_quality.get('low', price)
            zone_type = zone_quality.get('type', 'OB')

            if direction == 'BUY':
                # BUY: เข้าที่ขอบล่างของ zone (discount edge) + 15% buffer
                entry_price = zone_low + (zone_high - zone_low) * 0.15
            else:
                # SELL: เข้าที่ขอบบนของ zone (premium edge) - 15% buffer
                entry_price = zone_high - (zone_high - zone_low) * 0.15

            details['entry_type'] = 'zone_edge'
            details['zone_range'] = round(zone_high - zone_low, 2)
            details['zone_type'] = zone_type
        else:
            entry_price = price
            details['entry_type'] = 'market'
        
        # Get threshold from regime config
        da_threshold = regime_thresholds.get('da_threshold', self.thresholds['DA'])
        
        if score < da_threshold:
            return {
                'pattern_type': 'DA',
                'score': int(score),
                'max_score': 22,
                'reason': f"DA_BelowThreshold_{score}/{da_threshold}",
                'details': details,
                'is_partial': True
            }
        
        # === SL/TP with Regime-Based Multipliers ===
        # Get ATR for SL/TP calculation
        atr_multiplier = regime_thresholds.get('tp_multiplier', 2.0)
        sl_multiplier = regime_thresholds.get('sl_multiplier', 1.3)
        
        # === SECTION 11 DA-6: SL Boundary with ATR Buffer ===
        # SL วางที่ขอบ zone + ATR buffer เพื่อหลีกเลี่ยง Stop Hunt
        if in_zone and zone_quality.get('type') != 'NONE':
            zone_high = zone_quality.get('high')
            zone_low = zone_quality.get('low')
            # Get ATR from p1 data or fallback to 0.5% of price
            atr = p1.get('atr', price * 0.005)
            atr_buffer = atr * 0.3  # 30% of ATR as buffer

            if direction == 'SELL':
                sl_boundary = zone_high + atr_buffer  # เหนือ zone + buffer
            else:
                sl_boundary = zone_low - atr_buffer   # ใต้ zone - buffer

            details['sl_atr_buffer'] = round(atr_buffer, 2)
            details['sl_zone_edge'] = zone_high if direction == 'SELL' else zone_low
        else:
            sl_boundary = None
        
        # Structure confirmation
        structure = ict.get('structure', {})
        choch_status = structure.get('choch_status', '')
        has_choch = 'CHoCH_CONFIRMED' in str(choch_status) if choch_status else False
        
        m5_structure = 'NONE'
        m5_structure_dir = 'NEUTRAL'
        if ict and 'break_of_structure' in ict:
            bos_data = ict['break_of_structure']
            m5_structure = bos_data.get('type', 'NONE').upper()
            m5_structure_dir = bos_data.get('direction', 'NEUTRAL').upper()
        
        # === Section 2.2: Micro-Expansion Confirmation ===
        # === SECTION 11 DA-4: Adaptive Micro-Expansion Threshold ===
        # ปรับ threshold ตาม score (high score = ผ่อนปรน)
        # 0.08% = $76 สำหรับ BTC $95K แต่ M5 มักมี range แค่ $50-$200
        # ถ้า score สูง = สัญญาณดี = ผ่อนปรนเรื่อง expansion
        if score >= 15:
            min_expansion = 0.03   # High score: ผ่อนปรน
        elif score >= 12:
            min_expansion = 0.05   # Medium score: ปกติ
        else:
            min_expansion = 0.08   # Low score: เข้มงวด

        if len(candles) >= 1:
            candle_open = candles.iloc[-1]['open']
            current_price = candles.iloc[-1]['close']
            
            # Check micro-expansion using institutional flow analyzer
            expansion_confirmed, expansion_msg = self.institutional_analyzer.check_micro_expansion(
                current_price, candle_open, direction, min_expansion_pct=min_expansion
            )
            
            if not expansion_confirmed:
                details['micro_expansion_status'] = 'waiting'
                details['micro_expansion_msg'] = expansion_msg
                # Return partial signal with reduced score
                return {
                    'pattern_type': 'DA',
                    'score': max(0, score - 3),
                    'max_score': 22,
                    'reason': f"DA_WaitingExpansion_{score-3}/{da_threshold}",
                    'entry_price': entry_price,
                    'sl_boundary': sl_boundary,
                    'direction': 'LONG' if direction == 'BUY' else 'SHORT',
                    'details': details,
                    'is_partial': True
                }
            
            details['micro_expansion_status'] = 'confirmed'
            details['micro_expansion_msg'] = expansion_msg
        
        # === Architecture Plan 2.2: Institutional Grade Signal ===
        # Institutional Grade = True when Score >= 15 OR ICS >= 13
        da_ics = zone_quality.get('quality', 0) + score
        institutional_grade = score >= 15 or da_ics >= 13
        
        return {
            'pattern_type': 'DA',
            'institutional_grade': institutional_grade,
            'confluence_score': da_ics,
              # Architecture Plan TASK 2: DA requires balanced RR for precision
            'direction': 'LONG' if direction == 'BUY' else 'SHORT',
            'score': int(score),
            'max_score': 22,
            'entry_price': entry_price,
            'sl_boundary': sl_boundary,
            'has_choch': has_choch,
            'm5_structure': m5_structure,
            'm5_structure_dir': m5_structure_dir,
            'regime': regime,
            'tp_multiplier': atr_multiplier,
            'sl_multiplier': sl_multiplier,
            'reason': f"DA_{zone_quality['type']}_Q{zone_quality['quality']}",
            'details': details,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def _analyze_market_condition(self, candles, p1, binance_data=None) -> Dict:
        """
        วิเคราะห์สภาพตลาด
        
        Args:
            candles: Price candles DataFrame
            p1: Phase 1 data
            binance_data: Optional Binance data dict
        """
        if binance_data is None:
            binance_data = {}
        vol_ratio = self._get_volume_ratio(binance_data, p1)
        return {
            'is_trending': vol_ratio > 2,
            'is_ranging': vol_ratio < 1.5,
            'volume_score': min(int(vol_ratio), 5)
        }
    
    def _evaluate_zone_quality(self, ict, price) -> Dict:
        """ประเมินคุณภาพ Zone - เลือก quality สูงสุด"""
        obs = ict.get('order_blocks', {})
        fvgs = ict.get('fvgs', {})
        
        all_zones = []
        
        bullish_obs = obs.get('bullish')
        if bullish_obs:
            if isinstance(bullish_obs, list):
                for ob in bullish_obs:
                    all_zones.append({'zone': ob, 'type': 'OB_BULLISH'})
            elif isinstance(bullish_obs, dict):
                all_zones.append({'zone': bullish_obs, 'type': 'OB_BULLISH'})
        
        bullish_fvgs = fvgs.get('bullish')
        if bullish_fvgs:
            if isinstance(bullish_fvgs, list):
                for fvg in bullish_fvgs:
                    all_zones.append({'zone': fvg, 'type': 'FVG_BULLISH'})
            elif isinstance(bullish_fvgs, dict):
                all_zones.append({'zone': bullish_fvgs, 'type': 'FVG_BULLISH'})
        
        bearish_obs = obs.get('bearish')
        if bearish_obs:
            if isinstance(bearish_obs, list):
                for ob in bearish_obs:
                    all_zones.append({'zone': ob, 'type': 'OB_BEARISH'})
            elif isinstance(bearish_obs, dict):
                all_zones.append({'zone': bearish_obs, 'type': 'OB_BEARISH'})
        
        bearish_fvgs = fvgs.get('bearish')
        if bearish_fvgs:
            if isinstance(bearish_fvgs, list):
                for fvg in bearish_fvgs:
                    all_zones.append({'zone': fvg, 'type': 'FVG_BEARISH'})
            elif isinstance(bearish_fvgs, dict):
                all_zones.append({'zone': bearish_fvgs, 'type': 'FVG_BEARISH'})
        
        if not all_zones:
            return {'score': 0, 'type': 'NONE', 'quality': 0}
        
        best_zone_data = max(all_zones, key=lambda x: x['zone'].get('quality', 0) if isinstance(x['zone'], dict) else 0)
        best_zone = best_zone_data['zone']
        zone_type = best_zone_data['type']
        
        quality = best_zone.get('quality', 1) if isinstance(best_zone, dict) else 1
        score = min(quality, 5)
        
        # T-03 FIX: Calculate zone entry from high/low instead of non-existent 'price' key
        # OB has 'high'/'low', FVG has 'top'/'bottom'
        zone_low = best_zone.get('low') or best_zone.get('bottom') or price
        zone_high = best_zone.get('high') or best_zone.get('top') or price
        zone_price = (zone_low + zone_high) / 2  # Sniper entry at mid-point
        
        return {
            'score': int(score), 
            'type': zone_type, 
            'quality': quality,
            'price': zone_price,
            'low': zone_low,
            'high': zone_high
        }
    
    def _evaluate_entry_position(self, ict, price, zone_type) -> int:
        """ประเมินตำแหน่งเข้าใน Zone - FIXED: edge = best entry"""
        zone_data = self._evaluate_zone_quality(ict, price)
        if zone_data.get('type') == 'NONE':
            return 1
        
        zone_low = zone_data.get('low', price)
        zone_high = zone_data.get('high', price)
        zone_range = zone_high - zone_low
        
        if zone_range <= 0:
            return 2
        
        distance_from_low = abs(price - zone_low)
        distance_from_high = abs(zone_high - price)
        position_ratio = distance_from_low / zone_range
        
        # FIXED: Edge of zone = optimal entry (not worst!)
        # BULLISH zone: entry near LOW = best (optimal discount)
        # BEARISH zone: entry near HIGH = best (optimal premium)
        if 'BEARISH' in zone_type:
            # Sell zone: want to sell near HIGH (premium)
            if position_ratio >= 0.7:  # Near high = best
                return 3
            elif position_ratio >= 0.4:  # Middle
                return 2
            else:  # Near low = worst for selling
                return 0
        else:
            # Buy zone: want to buy near LOW (discount)
            if position_ratio <= 0.3:  # Near low = best
                return 3
            elif position_ratio <= 0.6:  # Middle
                return 2
            else:  # Near high = worst for buying
                return 0
    
    def check_htf_m5_coherence(
        self,
        htf_analyzer,
        m5_trend: str,
        m5_structure_type: str
    ) -> Dict:
        """
        Check H1-M5 trend coherence using HTFMSSAnalyzer.
        
        Args:
            htf_analyzer: HTFMSSAnalyzer instance
            m5_trend: M5 trend from ICTAnalyzer
            m5_structure_type: M5 structure type
        
        Returns:
            {
                'is_coherent': bool,
                'coherence_type': str,
                'warning': str or None,
                'should_proceed': bool
            }
        """
        if htf_analyzer is None:
            return {
                'is_coherent': True,
                'coherence_type': 'NO_HTF',
                'warning': None,
                'should_proceed': True
            }
        
        coherence = htf_analyzer.check_m5_h1_coherence(m5_trend, m5_structure_type)
        
        if not coherence['is_coherent']:
            logger.warning(f"[SmartFlow] H1-M5 Conflict: {coherence['warning']}")
        
        return {
            'is_coherent': coherence['is_coherent'],
            'coherence_type': coherence['coherence_type'],
            'warning': coherence['warning'],
            'should_proceed': coherence['should_flip_m5']
        }
    
    def _classify_oi_direction(self, oi_change: float, price_change: float) -> str:
        """
        Classify OI change direction based on price movement.
        
        OI Up + Price Up = NEW_LONGS (bullish)
        OI Up + Price Down = NEW_SHORTS (bearish)
        OI Down + Price Up = SHORT_COVERING (bullish)
        OI Down + Price Down = LONG_LIQUIDATION (bearish)
        """
        if abs(oi_change) < 0.02:
            return 'NEUTRAL'
        
        if oi_change > 0.02:
            if price_change > 0:
                return 'NEW_LONGS'
            else:
                return 'NEW_SHORTS'
        else:  # oi_change < -0.02
            if price_change > 0:
                return 'SHORT_COVERING'
            else:
                return 'LONG_LIQUIDATION'
    
    def _get_current_session(self) -> str:
        """
        Get current trading session based on UTC time.
        Session-Aware filtering uses this to adjust thresholds.
        """
        hour = datetime.now(timezone.utc).hour
        
        # Trading sessions (UTC)
        # Asia: 00:00 - 08:00 (Tokyo)
        # London: 08:00 - 13:00
        # London-NY: 13:00 - 16:00 (High volatility overlap)
        # NY: 16:00 - 21:00
        # Asia-Late: 21:00 - 00:00
        
        if 0 <= hour < 8:
            return "ASIA"
        elif 8 <= hour < 13:
            return "LONDON"
        elif 13 <= hour < 16:
            return "LONDON-NY"
        elif 16 <= hour < 21:
            return "NY"
        else:
            return "ASIA-LATE"
    
    def _get_session_thresholds(self) -> Dict:
        """
        Get session-aware thresholds based on current trading session.
        """
        session = self._get_current_session()
        return self.session_config.get(session, self.session_config['ASIA'])
    
    def _detect_market_regime(self, candles: pd.DataFrame, p1_data: Dict, binance_data: Dict) -> str:
        """
        Detect current market regime to select optimal pattern.
        
        Returns: 'TRENDING' | 'RANGING' | 'VOLATILE' | 'NORMAL'
        """
        if len(candles) < 20:
            return 'NORMAL'
        
        # ATR current vs ATR average (volatility expansion/contraction)
        high = candles['high'].values
        low = candles['low'].values
        close = candles['close'].values
        
        tr = np.maximum(high[1:] - low[1:], 
                        np.maximum(np.abs(high[1:] - close[:-1]), 
                                   np.abs(low[1:] - close[:-1])))
        
        atr_14 = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
        atr_50 = np.mean(tr[-50:]) if len(tr) >= 50 else np.mean(tr)
        
        vol_ratio = self._get_volume_ratio(binance_data, p1_data)
        oi_change = abs(binance_data.get('oi', {}).get('openInterestChange', 0))
        
        # Trending: ATR expanding + high volume
        if atr_14 > atr_50 * 1.5 and vol_ratio > 2.0:
            return 'TRENDING'
        
        # Ranging: ATR contracting + low volume
        if atr_14 < atr_50 * 0.7 and vol_ratio < 1.5:
            return 'RANGING'
        
        # Volatile: high OI change = big position shifts
        if oi_change > 0.3:
            return 'VOLATILE'
        
        return 'NORMAL'
    
    def _check_price_action_rejection(self, candles: pd.DataFrame, price: float, direction: str) -> int:
        """
        Check for rejection candle patterns (pin bar, engulfing) for CVD_REV confirmation.
        Returns 0-2 bonus points.
        """
        if len(candles) < 3:
            return 0
        
        try:
            last = candles.iloc[-1]
            prev = candles.iloc[-2]
            
            last_open = last['open']
            last_close = last['close']
            last_high = last['high']
            last_low = last['low']
            last_body = abs(last_close - last_open)
            last_range = last_high - last_low
            
            prev_open = prev['open']
            prev_close = prev['close']
            prev_body = abs(prev_close - prev_open)
            
            bonus = 0
            
            # Pin Bar: small body, long wick in rejection direction
            if last_range > 0 and last_body / last_range < 0.3:
                # Bullish pin bar: long lower wick, small body near top
                lower_wick = min(last_open, last_close) - last_low
                upper_wick = last_high - max(last_open, last_close)
                
                if direction == 'BUY' and lower_wick > upper_wick * 2 and lower_wick > last_body * 2:
                    bonus = 2  # Strong bullish rejection
                elif direction == 'SELL' and upper_wick > lower_wick * 2 and upper_wick > last_body * 2:
                    bonus = 2  # Strong bearish rejection
            
            # Engulfing: current candle engulfs previous
            if bonus == 0:
                if direction == 'BUY':
                    # Bullish engulfing: last candle engulfs prev red candle
                    if prev_close < prev_open and last_close > last_open:
                        if last_close > prev_open and last_open < prev_close:
                            bonus = 1
                elif direction == 'SELL':
                    # Bearish engulfing: last candle engulfs prev green candle
                    if prev_close > prev_open and last_close < last_open:
                        if last_open > prev_close and last_close < prev_open:
                            bonus = 1
            
            return bonus
            
        except Exception:
            return 0
    
    def _calculate_institutional_confluence_score(self, details: Dict, binance_data: Dict, p1_data: Dict) -> int:
        """
        Architecture Plan 2.2: Calculate Institutional Confluence Score (ICS).
        
        ICS = Sum of confluence indicators (max ~13+)
        
        Components:
        - OI Direction aligned with trade (0-3)
        - CVD Momentum (0-3)
        - Volume confirmation (0-2)
        - HTF alignment (0-3)
        - Whale activity (0-2)
        
        Returns:
            int: ICS score (higher = more institutional confidence)
        """
        ics = 0
        
        # 1. OI Direction aligned with trade
        oi_direction = details.get('oi_direction', 'NEUTRAL')
        direction = details.get('direction', 'BUY')
        if (direction == 'BUY' and oi_direction in ('NEW_LONGS', 'SHORT_COVERING')):
            ics += 3
        elif (direction == 'SELL' and oi_direction in ('NEW_SHORTS', 'LONG_LIQUIDATION')):
            ics += 3
        elif oi_direction and oi_direction != 'NEUTRAL':
            ics += 1
        
        # 2. CVD Momentum
        cvd_momentum = details.get('cvd_momentum', 'none')
        if cvd_momentum == 'strong':
            ics += 3
        elif cvd_momentum == 'moderate':
            ics += 2
        
        # 3. Volume confirmation
        vol_ratio = self._get_volume_ratio(binance_data, p1_data)
        if vol_ratio > 2.0:
            ics += 2
        elif vol_ratio > 1.3:
            ics += 1
        
        # 4. HTF alignment
        if details.get('htf_sync'):
            ics += 3
        elif details.get('htf_counter'):
            ics -= 2
        
        # 5. Whale activity
        if details.get('whale_confluence', {}).get('has_big_whale'):
            ics += 2
        
        return max(0, ics)
    
    def _get_regime_bonus(self, pattern_type: str, regime: str) -> int:
        """
        Get score bonus/penalty based on pattern-regime match.
        """
        # Optimal pattern for each regime
        regime_map = {
            'TRENDING':  {'LP': 3, 'DB': -2, 'DA': 0},
            'RANGING':   {'LP': -2, 'DB': 3, 'DA': 1},
            'VOLATILE':  {'LP': 1, 'DB': 0, 'DA': 3},
            'NORMAL':    {'LP': 0, 'DB': 0, 'DA': 0},
        }
        return regime_map.get(regime, {}).get(pattern_type, 0)

    def _check_htf_zone_alignment(
        self, zone_quality: Dict, htf_data: Dict, direction: str
    ) -> Tuple[int, str]:
        """
        Section 11 DA-5: Multi-Timeframe Zone Alignment Check.
        
        ตรวจสอบว่า M5 zone align กับ H1 zone หรือไม่
        Zone ที่ align กับ HTF มีความน่าเชื่อถือสูงกว่ามาก
        
        Returns:
            Tuple[int, str]: (bonus_score, description)
              +3 = align กับ H1 OB
              +2 = align กับ H1 FVG
               0 = ไม่ align
        """
        if not htf_data or zone_quality.get('type') == 'NONE':
            return 0, 'NO_HTF_ZONE'

        zone_price = zone_quality.get('price', 0)
        direction_key = 'bullish' if direction == 'BUY' else 'bearish'

        # Check H1 OB alignment
        htf_ob_list = htf_data.get('order_blocks', {}).get(direction_key, [])
        if isinstance(htf_ob_list, dict):
            htf_ob_list = [htf_ob_list]
        for ob in htf_ob_list:
            if ob and ob.get('low', 0) <= zone_price <= ob.get('high', 0):
                return 3, f"H1_OB_ALIGNED: {ob.get('low',0):.0f}-{ob.get('high',0):.0f}"

        # Check H1 FVG alignment
        htf_fvg_list = htf_data.get('fvgs', {}).get(direction_key, [])
        if isinstance(htf_fvg_list, dict):
            htf_fvg_list = [htf_fvg_list]
        for fvg in htf_fvg_list:
            if fvg and fvg.get('bottom', 0) <= zone_price <= fvg.get('top', 0):
                return 2, f"H1_FVG_ALIGNED: {fvg.get('bottom',0):.0f}-{fvg.get('top',0):.0f}"

        return 0, 'NO_HTF_ALIGNMENT'

    def _calculate_cvd_momentum(self, candles: pd.DataFrame, lookback: int = 3) -> Dict:
        """
        Calculate CVD momentum over last N candles.
        
        Returns:
            {
                'direction': 'BUY' | 'SELL' | 'NEUTRAL',
                'strength': 0-3 (consecutive candles in same direction),
                'total_delta': float,
                'is_shifting': bool (direction changed recently)
            }
        """
        if len(candles) < lookback + 1:
            return {'direction': 'NEUTRAL', 'strength': 0, 'total_delta': 0, 'is_shifting': False}
        
        # Calculate per-candle delta (close > open = buy, else sell)
        recent = candles.tail(lookback + 1)
        deltas = []
        for _, row in recent.iterrows():
            d = row['volume'] if row['close'] >= row['open'] else -row['volume']
            deltas.append(d)
        
        # Check consecutive direction
        last_n = deltas[-lookback:]
        positive_count = sum(1 for d in last_n if d > 0)
        negative_count = sum(1 for d in last_n if d < 0)
        total_delta = sum(last_n)
        
        # Direction: majority of last N candles
        if positive_count >= lookback:
            direction = 'BUY'
            strength = positive_count
        elif negative_count >= lookback:
            direction = 'SELL'
            strength = negative_count
        elif positive_count > negative_count:
            direction = 'BUY'
            strength = positive_count
        elif negative_count > positive_count:
            direction = 'SELL'
            strength = negative_count
        else:
            direction = 'NEUTRAL'
            strength = 0
        
        # Detect shift: previous candle vs current are different direction
        is_shifting = len(deltas) >= 2 and (deltas[-1] * deltas[-2] < 0)
        
        return {
            'direction': direction,
            'strength': strength,
            'total_delta': total_delta,
            'is_shifting': is_shifting
        }
    
    def _detect_volume_exhaustion(self, candles: pd.DataFrame, lookback: int = 3) -> bool:
        """
        Detect if volume is declining (exhaustion approaching a level).
        True = volume decreasing for N consecutive candles.
        """
        if len(candles) < lookback + 1:
            return False
        
        volumes = candles['volume'].tail(lookback).values
        # Check if each volume is less than the previous
        for i in range(1, len(volumes)):
            if volumes[i] >= volumes[i - 1]:
                return False
        return True

    # ==================== Section 24: Smart Flow v3.5 Helper Methods ====================

    def _calculate_atr_ratio(self, candles: pd.DataFrame) -> float:
        """
        Section 24.4: Calculate ATR ratio for regime detection.
        
        Volatility_Index = ATR(14, M5) / ATR(200, M5)
        
        Returns:
            float: ATR ratio (volatility index)
            - < 0.75 = RANGING
            - > 1.4 = EXPANDING
            - between = NORMAL/VOLATILE
        """
        if len(candles) < 200:
            # Not enough data - return normal regime
            return 1.0
        
        try:
            high = candles['high'].values
            low = candles['low'].values
            close = candles['close'].values
            
            # Calculate True Range
            tr = np.maximum(high[1:] - low[1:], 
                           np.maximum(np.abs(high[1:] - close[:-1]), 
                                      np.abs(low[1:] - close[:-1])))
            
            # ATR(14)
            atr_14 = np.mean(tr[-14:]) if len(tr) >= 14 else np.mean(tr)
            
            # ATR(200)
            atr_200 = np.mean(tr[-200:]) if len(tr) >= 200 else np.mean(tr)
            
            if atr_200 > 0:
                return atr_14 / atr_200
            return 1.0
            
        except Exception as e:
            logger.debug(f"ATR ratio calculation error: {e}")
            return 1.0

    def _detect_regime_v3(self, candles: pd.DataFrame) -> str:
        """
        Section 24.4: Adaptive Regime Strategy using ATR ratio.
        
        Returns:
            str: 'RANGING' | 'EXPANDING' | 'VOLATILE' | 'NORMAL'
        """
        atr_ratio = self._calculate_atr_ratio(candles)
        
        THRESHOLD_RANGING = 0.75
        THRESHOLD_EXPANDING = 1.4
        
        if atr_ratio < THRESHOLD_RANGING:
            return 'RANGING'
        elif atr_ratio > THRESHOLD_EXPANDING:
            return 'EXPANDING'
        else:
            # Check if volatile (high OI change but not expanding)
            # This is handled by _detect_market_regime for OI-based volatility
            return 'VOLATILE' if atr_ratio > 1.0 else 'NORMAL'

    def _calculate_wick_cluster(self, candles: pd.DataFrame, direction: str) -> Tuple[int, Dict]:
        """
        Section 24.1: Calculate Wick Cluster score for CVD_REV.
        
        Segment candle into 4 quads. If 60% of total Delta is in the 
        upper/lower 25% quad (the wick), add points.
        
        Returns:
            Tuple[int, Dict]: (score, details)
            - +3 points if 60%+ delta concentration in wick quad
        """
        if len(candles) < 1:
            return 0, {}
        
        try:
            last = candles.iloc[-1]
            candle_high = last['high']
            candle_low = last['low']
            candle_open = last['open']
            candle_close = last['close']
            
            total_range = candle_high - candle_low
            if total_range <= 0:
                return 0, {}
            
            # Define quads (4 segments)
            quad_size = total_range / 4
            
            # Upper quad (top 25% - upper wick area)
            upper_quad_start = candle_high - quad_size
            # Lower quad (bottom 25% - lower wick area)
            lower_quad_end = candle_low + quad_size
            
            # Get candle body
            body_top = max(candle_open, candle_close)
            body_bottom = min(candle_open, candle_close)
            
            # Upper wick: from body top to high
            upper_wick = candle_high - body_top
            # Lower wick: from low to body bottom
            lower_wick = body_bottom - candle_low
            
            # Calculate delta distribution (simplified - use volume as proxy)
            volume = last.get('volume', 1)
            if volume <= 0:
                volume = 1
            
            # Determine which quad has concentration
            # For BUY direction: check lower wick concentration (selling pressure being absorbed)
            # For SELL direction: check upper wick concentration (buying pressure being absorbed)
            
            score = 0
            details = {}
            
            if direction == 'BUY':
                # Check if lower wick is significant (selling absorption)
                lower_wick_ratio = lower_wick / total_range
                if lower_wick_ratio >= 0.25:  # At least 25% of range in lower wick
                    # Check if this is 60%+ of the body-excluded delta
                    # (wick dominates the non-body portion)
                    non_body_range = upper_wick + lower_wick
                    if non_body_range > 0:
                        wick_concentration = lower_wick / non_body_range
                        if wick_concentration >= 0.6:
                            score = 3
                            details['wick_cluster'] = 'lower_wick_absorption'
                            details['wick_ratio'] = lower_wick_ratio
                            details['concentration'] = wick_concentration
            else:  # SELL
                # Check if upper wick is significant (buying absorption)
                upper_wick_ratio = upper_wick / total_range
                if upper_wick_ratio >= 0.25:
                    non_body_range = upper_wick + lower_wick
                    if non_body_range > 0:
                        wick_concentration = upper_wick / non_body_range
                        if wick_concentration >= 0.6:
                            score = 3
                            details['wick_cluster'] = 'upper_wick_absorption'
                            details['wick_ratio'] = upper_wick_ratio
                            details['concentration'] = wick_concentration
            
            return score, details
            
        except Exception as e:
            logger.debug(f"Wick cluster calculation error: {e}")
            return 0, {}

    def _calculate_oi_velocity(self, binance_data: Dict) -> Tuple[float, Dict]:
        """
        Section 24.2 & 37.1: Calculate OI Velocity for LP pattern.
        
        Section 56 Bug #1 FIX: Added OI History Fallback
        - When oi_history < 10, use fixed threshold instead of failing
        
        Section 56 Bug #2 FIX: Dynamic OI Threshold adjustment
        - Changed from 2.0 sigma to 1.5 sigma (less strict)
        - Added hard cap 0.15% to prevent excessive threshold in volatile markets
        
        OI_Velocity = (OI[current] - OI[1 minute ago]) / OI[1 minute ago] * 100
        
        Returns:
            Tuple[float, Dict]: (velocity_percentage, details)
        """
        oi_data = binance_data.get('oi', {})
        
        # Get current OI
        current_oi = oi_data.get('openInterest', 0)
        
        # Get OI history (maintained by BinanceDataFetcher)
        oi_history = oi_data.get('oi_history', [])
        
        # Section 56 Bug #1 FIX: OI History Fallback
        # If current_oi is missing, can't calculate velocity at all
        if current_oi <= 0:
            return 0.0, {'status': 'no_current_oi', 'gate_passed': False}
        
        # If oi_history < 10, use fixed threshold instead of failing
        if len(oi_history) < 10:
            # Bug #1 FIX: Use fixed threshold as fallback instead of returning False
            velocity = binance_data.get('oi', {}).get('openInterestChange', 0)
            dynamic_threshold = self.oi_velocity_threshold
            gate_passed = abs(velocity) > dynamic_threshold
            return velocity, {
                'status': 'fallback_fixed_threshold',
                'velocity': velocity,
                'threshold': dynamic_threshold,
                'threshold_type': 'fixed_fallback',
                'gate_passed': gate_passed
            }
        
        # Get OI from approx 1 minute ago
        lookback = min(len(oi_history), 20)
        oi_1min_ago = oi_history[-lookback] if len(oi_history) >= lookback else oi_history[0]
        
        if oi_1min_ago <= 0:
            # Bug #1 FIX: Use fixed threshold as fallback
            velocity = ((current_oi - oi_history[0]) / oi_history[0]) * 100 if oi_history[0] > 0 else 0
            dynamic_threshold = self.oi_velocity_threshold
            gate_passed = abs(velocity) > dynamic_threshold
            return velocity, {
                'status': 'fallback_from_first_oi',
                'velocity': velocity,
                'threshold': dynamic_threshold,
                'threshold_type': 'fixed_fallback',
                'gate_passed': gate_passed
            }
        
        # Calculate velocity as percentage
        velocity = ((current_oi - oi_1min_ago) / oi_1min_ago) * 100
        
        # === Section 37.1: Dynamic OI Sensitivity ===
        # Calculate relative threshold using 1.5 Standard Deviation (Bug #2 FIX: was 2.0)
        import numpy as np
        
        oi_changes = []
        for i in range(1, min(len(oi_history), 60)):  # Look at last 60 samples
            if oi_history[-i] > 0 and oi_history[-i-1] > 0:
                change = ((oi_history[-i] - oi_history[-i-1]) / oi_history[-i-1]) * 100
                oi_changes.append(change)
        
        if len(oi_changes) >= 10:
            # Calculate mean and standard deviation
            mean_change = np.mean(oi_changes)
            std_change = np.std(oi_changes)
            
            # Section 56 Bug #2 FIX: Changed from 2.0 to 1.5 sigma
            dynamic_threshold = mean_change + 1.5 * std_change  # was 2.0 * std_change
            
            # Section 56 Bug #2 FIX: Added hard cap 0.15%
            # Prevents threshold from becoming too high during volatile periods
            MAX_THRESHOLD = 0.15  # 0.15% hard cap
            dynamic_threshold = min(dynamic_threshold, MAX_THRESHOLD)
            
            # Floor: minimum threshold to avoid false positives in quiet markets
            min_threshold = 0.02  # 0.02% minimum
            dynamic_threshold = max(dynamic_threshold, min_threshold)
        else:
            # Fallback to fixed threshold if insufficient history
            dynamic_threshold = self.oi_velocity_threshold
        
        # Gate passed if velocity exceeds dynamic threshold
        gate_passed = abs(velocity) > dynamic_threshold
        
        details = {
            'current_oi': current_oi,
            'oi_1min_ago': oi_1min_ago,
            'velocity': velocity,
            'threshold': dynamic_threshold,
            'threshold_type': 'dynamic_1.5sigma_capped' if len(oi_changes) >= 10 else 'fixed_fallback',
            'gate_passed': gate_passed
        }
        
        return velocity, details

    # NOTE: _check_cvd_divergence has been moved and replaced.
    # See the improved version below (after _get_candle_cvd_approx) with
    # candle-based CVD approximation (DA-3 fix).

    def _get_candle_cvd_approx(self, candle_row: pd.Series) -> float:
        """
        Section 11 DA-3: Approximate CVD from candle body ratio x volume.
        
        CVD ≈ Volume * |Body/Range| * direction
        
        Args:
            candle_row: Single candle row with open, close, high, low, volume
            
        Returns:
            float: Approximated CVD delta for this candle
        """
        try:
            body = candle_row['close'] - candle_row['open']
            volume = candle_row['volume']
            candle_range = candle_row['high'] - candle_row['low']
            if candle_range > 0:
                body_ratio = abs(body) / candle_range
                return volume * body_ratio * (1 if body > 0 else -1)
            return volume * (1 if body > 0 else -1)
        except Exception:
            return 0.0

    def _check_cvd_divergence(self, candles: pd.DataFrame, p1_data: Dict) -> Tuple[str, Dict]:
        """
        Section 11 DA-3 + Section 24.1: Check for Price vs CVD divergence over last 3 candles.
        
        Uses candle-based CVD approximation (body ratio x volume) instead of simple
        delta scaling. This provides more accurate divergence detection.
        
        Bullish Div: Price Lower Low but CVD Higher Low (buying pressure hidden)
        Bearish Div: Price Higher High but CVD Lower High (selling pressure hidden)
        
        Returns:
            Tuple[str, Dict]: (divergence_type, details)
        """
        if len(candles) < 3:
            return 'NONE', {}

        try:
            recent_candles = candles.tail(3)

            current_low = recent_candles.iloc[-1]['low']
            prev_low = recent_candles.iloc[-2]['low']
            current_high = recent_candles.iloc[-1]['high']
            prev_high = recent_candles.iloc[-2]['high']

            # === SECTION 11 DA-3: Candle-based CVD approximation ===
            # Approximate CVD from candle body ratio × volume (more accurate than delta scaling)
            current_cvd = self._get_candle_cvd_approx(recent_candles.iloc[-1])
            prev_cvd = self._get_candle_cvd_approx(recent_candles.iloc[-2])

            details = {
                'current_low': current_low,
                'prev_low': prev_low,
                'current_high': current_high,
                'prev_high': prev_high,
                'current_cvd_approx': round(current_cvd, 2),
                'prev_cvd_approx': round(prev_cvd, 2)
            }

            # Bullish Divergence: Price making lower low but CVD higher low
            if current_low < prev_low and current_cvd > prev_cvd:
                return 'BULLISH', details

            # Bearish Divergence: Price making higher high but CVD lower high
            if current_high > prev_high and current_cvd < prev_cvd:
                return 'BEARISH', details

            return 'NONE', details

        except Exception as e:
            logger.debug(f"CVD divergence check error: {e}")
            return 'NONE', {}

    def _check_volume_exhaustion_v3(self, candles: pd.DataFrame) -> Tuple[bool, Dict]:
        """
        Section 24.1: Check for Volume Exhaustion.

        Volume[0] < EMA(Volume, 5) * 0.8 AND hits_new_structural_extreme

        Returns:
            Tuple[bool, Dict]: (is_exhausted, details)
        """
        if len(candles) < 6:
            return False, {}

        try:
            volumes = candles['volume'].tail(6).values

            # Calculate EMA(Volume, 5)
            ema_5 = self._calculate_ema(volumes[:-1], 5)
            current_volume = volumes[-1]

            is_below_ema = current_volume < ema_5 * 0.8

            # Check if hitting new structural extreme (new high/low)
            recent = candles.tail(3)
            current_price = candles.iloc[-1]['close']

            # Check if at extreme
            recent_high = recent['high'].max()
            recent_low = recent['low'].min()

            at_extreme = current_price >= recent_high or current_price <= recent_low

            details = {
                'current_volume': current_volume,
                'ema_5': ema_5,
                'is_below_ema_80pct': is_below_ema,
                'at_structural_extreme': at_extreme
            }

            return is_below_ema and at_extreme, details

        except Exception as e:
            logger.debug(f"Volume exhaustion v3 error: {e}")
            return False, {}

    def _check_volume_spike(self, candles: pd.DataFrame) -> Tuple[bool, Dict]:
        """
        Section 24.1: Check for Volume Spike.
        
        Volume[0] > EMA(Volume, 5) * 2.0
        
        Returns:
            Tuple[bool, Dict]: (is_spike, details)
        """
        if len(candles) < 6:
            return False, {}
        
        try:
            volumes = candles['volume'].tail(6).values
            
            # Calculate EMA(Volume, 5)
            ema_5 = self._calculate_ema(volumes[:-1], 5)
            current_volume = volumes[-1]
            
            is_spike = current_volume > ema_5 * 2.0
            
            details = {
                'current_volume': current_volume,
                'ema_5': ema_5,
                'is_spike': is_spike,
                'spike_ratio': current_volume / ema_5 if ema_5 > 0 else 0
            }
            
            return is_spike, details
            
        except Exception as e:
            logger.debug(f"Volume spike check error: {e}")
            return False, {}

    def _calculate_ema(self, values: np.ndarray, period: int) -> float:
        """Calculate EMA for a given period."""
        if len(values) < period:
            return np.mean(values) if len(values) > 0 else 0
        
        ema = np.mean(values[:period])
        multiplier = 2 / (period + 1)
        
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        
        return ema

    def _update_active_walls(self, walls: Dict, current_price: float):
        """
        Section 24.3: Update active walls with timestamps for stability tracking.
        
        Args:
            walls: Wall data from Binance
            current_price: Current BTC price
        """
        now = datetime.now(timezone.utc)
        
        # Process bid walls
        bid_walls = walls.get('bid_walls', [])
        for wall in bid_walls:
            wall_price = wall.get('price', 0)
            wall_size = wall.get('size', 0)
            
            if wall_price > 0:
                if wall_price not in self._active_walls['bid']:
                    # New wall - add with timestamp
                    self._active_walls['bid'][wall_price] = {
                        'first_seen': now,
                        'size': wall_size,
                        'initial_size': wall_size,
                        'last_update': now
                    }
                else:
                    # Update existing wall
                    self._active_walls['bid'][wall_price]['last_update'] = now
                    self._active_walls['bid'][wall_price]['size'] = wall_size
        
        # Process ask walls
        ask_walls = walls.get('ask_walls', [])
        for wall in ask_walls:
            wall_price = wall.get('price', 0)
            wall_size = wall.get('size', 0)
            
            if wall_price > 0:
                if wall_price not in self._active_walls['ask']:
                    self._active_walls['ask'][wall_price] = {
                        'first_seen': now,
                        'size': wall_size,
                        'initial_size': wall_size,
                        'last_update': now
                    }
                else:
                    self._active_walls['ask'][wall_price]['last_update'] = now
                    self._active_walls['ask'][wall_price]['size'] = wall_size
        
        # Clean up old walls (not seen in last 30 seconds)
        cleanup_threshold = timedelta(seconds=30)
        for wall_type in ['bid', 'ask']:
            to_remove = []
            for price, data in self._active_walls[wall_type].items():
                if now - data['last_update'] > cleanup_threshold:
                    to_remove.append(price)
            for price in to_remove:
                del self._active_walls[wall_type][price]

    def _check_wall_stability(self, wall_price: float, wall_type: str, wall_size: float = None) -> Tuple[bool, Dict]:
        """
        Section 24.3: Check if wall has been stable for required time.
        
        Section 56 Bug #7 FIX: 
        - Now registers new walls instead of failing immediately
        - Reduced stability requirement from 15s to 5s (self.wall_stability_time_seconds)
        
        Returns:
            Tuple[bool, Dict]: (is_stable, details)
        """
        now = datetime.now(timezone.utc)
        
        if wall_price not in self._active_walls[wall_type]:
            # Bug #7 FIX: Register new wall and track it
            # Don't fail immediately - let it build stability over time
            self._active_walls[wall_type][wall_price] = {
                'first_seen': now,
                'size': wall_size if wall_size else 0,
                'initial_size': wall_size if wall_size else 0,
                'last_update': now
            }
            return False, {
                'status': 'just_registered',
                'wall_age_seconds': 0,
                'required_seconds': self.wall_stability_time_seconds,
                'is_stable': False
            }
        
        wall_data = self._active_walls[wall_type][wall_price]
        wall_age = (now - wall_data['first_seen']).total_seconds()
        
        # Bug #7 FIX: Use instance variable (now 5s instead of hardcoded 15s)
        is_stable = wall_age > self.wall_stability_time_seconds
        
        details = {
            'wall_age_seconds': wall_age,
            'required_seconds': self.wall_stability_time_seconds,
            'is_stable': is_stable,
            'initial_size': wall_data.get('initial_size', 0),
            'current_size': wall_data.get('size', 0)
        }
        
        return is_stable, details

    def _calculate_wall_significance(self, wall_size_usd: float, candles: pd.DataFrame, current_price: float) -> Tuple[float, bool]:
        """
        Section 24.3: Calculate Wall Significance.
        Section 56 Bug #6 FIX: Fixed formula unit error.
        
        Previous (WRONG) formula:
            Wall_Significance = Wall_Size_USD / (AVG_VOL_1H * Price)
        - avg_vol_1h is volume per CANDLE (M5), not per hour
        - Multiplying by price again makes denominator huge
        
        Corrected formula:
            Wall_Significance = Wall_Size_USD / (AVG_VOL_PER_CANDLE * Price * 12)
        - Convert to USD per hour: avg_vol_per_candle * price * 12 candles/hour
        
        Gate Passed IF Wall_Significance > 0.005 (wall >= 0.5% of hourly volume)
        
        Returns:
            Tuple[float, bool]: (significance, is_significant)
        """
        if len(candles) < 5 or current_price <= 0:
            # Not enough data - use simplified calculation
            avg_vol_per_candle = candles['volume'].tail(20).mean() if len(candles) >= 5 else candles['volume'].mean()
            if avg_vol_per_candle is None or avg_vol_per_candle <= 0:
                return 0.0, False
        else:
            # Use last 20 candles for average volume per M5 candle
            avg_vol_per_candle = candles['volume'].tail(20).mean()
        
        if avg_vol_per_candle <= 0:
            return 0.0, False
        
        # Section 56 Bug #6 FIX: Convert to USD per hour correctly
        # avg_vol_per_candle is in BTC/candle (M5)
        # 12 candles per hour (M5)
        # Multiply by price to get USD, then by 12 for hourly volume
        avg_vol_usd_per_hour = avg_vol_per_candle * current_price * 12
        
        if avg_vol_usd_per_hour <= 0:
            return 0.0, False
        
        # Section 56 Bug #6 FIX: New threshold 0.005 (was 3.0 which was impossible to pass)
        # This means wall must be >= 0.5% of hourly trading volume
        # Example: $100M hourly volume → wall must be >= $500K
        significance = wall_size_usd / avg_vol_usd_per_hour
        is_significant = significance > self.wall_significance_threshold
        
        # Debug log for tuning
        logger.debug(f"📐 Wall Sig: ${wall_size_usd/1000:.0f}K / ${avg_vol_usd_per_hour/1e6:.1f}M = {significance:.4f} (threshold: {self.wall_significance_threshold})")
        
        return significance, is_significant

    def _calculate_executed_volume(self, wall_price: float, wall_type: str, binance_data: Dict) -> float:
        """
        Calculate actual executed volume from trades hitting the wall.
        
        FIX: Issue 1 - Replace placeholder data with real data from binance_data['trades']
        
        Args:
            wall_price: Price level of the wall
            wall_type: 'bid' or 'ask'
            binance_data: Binance data containing trade information
            
        Returns:
            float: Total executed volume at the wall level
        """
        executed_volume = 0.0
        
        # Try to get trades from binance_data
        whales = binance_data.get('whales', {})
        agg_trades = whales.get('buy_whales', []) + whales.get('sell_whales', [])
        
        # Also check for trades in binance_data directly
        if 'trades' in binance_data:
            trades = binance_data.get('trades', [])
            for trade in trades:
                trade_price = trade.get('price', 0)
                trade_qty = trade.get('qty', 0)
                
                # Check if trade is near the wall price (within 0.5%)
                if wall_price > 0 and abs(trade_price - wall_price) / wall_price < 0.005:
                    executed_volume += trade_qty
        
        # If no trades found, estimate from whale activity
        if executed_volume == 0 and agg_trades:
            for trade in agg_trades:
                trade_price = trade.get('price', 0)
                trade_qty = trade.get('qty', 0)
                
                # Check if trade is near the wall price
                if wall_price > 0 and abs(trade_price - wall_price) / wall_price < 0.005:
                    executed_volume += trade_qty
        
        # Get current wall size from active walls for comparison
        if wall_price in self._active_walls.get(wall_type, {}):
            wall_data = self._active_walls[wall_type][wall_price]
            initial_size = wall_data.get('initial_size', 0)
            current_size = wall_data.get('size', 0)
            
            # Calculate executed volume as the difference
            if initial_size > 0:
                # Wall size decrease represents executed volume
                size_diff = initial_size - current_size
                if size_diff > 0:
                    # Use the larger of the two estimates
                    executed_volume = max(executed_volume, size_diff)
        
        return executed_volume

    def _check_whale_confluence(self, wall_price: float, direction: str, binance_data: Dict) -> Tuple[int, Dict]:
        """
        Section 24.3: Check for Whale Confluence.
        
        If a Whale Trade hits the wall and the wall size 
        decreases by less than 20 percent (Absorption).
        
        FIX: Issue 1 - Use real executed volume from trades instead of placeholder values.
        
        Returns:
            Tuple[int, Dict]: (score, details)
            - +3 points if whale confluence detected
        """
        whales = binance_data.get('whales', {})
        
        # Get initial wall size from _active_walls
        wall_type = 'bid' if direction == 'BUY' else 'ask'
        
        if wall_price not in self._active_walls[wall_type]:
            return 0, {'status': 'wall_not_tracked'}
        
        wall_data = self._active_walls[wall_type][wall_price]
        initial_size = wall_data.get('initial_size', 0)
        current_size = wall_data.get('size', 0)
        
        if initial_size <= 0:
            return 0, {'status': 'no_initial_size'}
        
        # FIX: Calculate real executed volume from trades
        # Previously used placeholder value 'executed_volume': binance_data.get('trades_1m_vol', 10)
        executed_volume = self._calculate_executed_volume(wall_price, wall_type, binance_data)
        
        # Check size decrease using real data
        size_decrease_pct = (initial_size - current_size) / initial_size if initial_size > 0 else 0
        size_decreased_less_than_20pct = size_decrease_pct < 0.20
        
        # Check for whale trades hitting the wall
        if direction == 'BUY':
            whale_trades = whales.get('buy_whales', [])
            whales_hitting = [w for w in whale_trades if abs(w.get('price', 0) - wall_price) / wall_price < 0.005]
        else:
            whale_trades = whales.get('sell_whales', [])
            whales_hitting = [w for w in whale_trades if abs(w.get('price', 0) - wall_price) / wall_price < 0.005]
        
        total_whale_value = sum(w.get('value_usd', 0) for w in whales_hitting)
        
        has_big_whale = total_whale_value > self.whale_confluence_threshold
        
        details = {
            'initial_size': initial_size,
            'current_size': current_size,
            'executed_volume': executed_volume,  # FIX: Real executed volume
            'size_decrease_pct': size_decrease_pct,
            'size_decreased_less_than_20pct': size_decreased_less_than_20pct,
            'whale_trades_count': len(whales_hitting),
            'total_whale_value': total_whale_value,
            'has_big_whale': has_big_whale
        }
        
        if has_big_whale and size_decreased_less_than_20pct:
            return 3, details
        
        return 0, details

    def _get_regime_thresholds(self, regime: str) -> Dict:
        """
        Section 24.4: Get dynamic thresholds based on detected regime.
        
        Returns:
            Dict: {{pattern: threshold_value}}
        """
        return self.regime_config.get(regime, self.regime_config['NORMAL'])

    def _calculate_liquidation_fuel(self, direction: str, binance_data: Dict) -> Tuple[int, Dict]:
        """
        Section 24.2: Check for Liquidation Fuel.
        
        Check for large liquidations (over 100 thousand USD) in the last 60 seconds 
        against the direction.
        
        Returns:
            Tuple[int, Dict]: (score, details)
            - +2 points if significant liquidation fuel detected
        """
        liquidations = binance_data.get('liquidations', {})
        
        if direction == 'BUY':
            # Check for SELL liquidations (short squeeze fuel)
            sell_liq = liquidations.get('sell_liquidation', [])
            total_sell_liq = sum(l.get('value_usd', 0) for l in sell_liq if l.get('value_usd', 0) > 0)
            
            details = {
                'direction': direction,
                'liquidation_type': 'sell',
                'total_value': total_sell_liq,
                'threshold': 100_000
            }
            
            if total_sell_liq > 100_000:
                return 2, details
        else:  # SELL
            # Check for BUY liquidations (long squeeze fuel)
            buy_liq = liquidations.get('buy_liquidation', [])
            total_buy_liq = sum(l.get('value_usd', 0) for l in buy_liq if l.get('value_usd', 0) > 0)
            
            details = {
                'direction': direction,
                'liquidation_type': 'buy',
                'total_value': total_buy_liq,
                'threshold': 100_000
            }
            
            if total_buy_liq > 100_000:
                return 2, details
        return 0, details
    
    def check_position_flip(self, current_direction: str, binance_data: Dict) -> Dict:
        """
        Section 27.1: Check if position should close early due to flip signals.
        
        Args:
            current_direction: Current position direction ('BUY' or 'SELL')
            binance_data: Latest Binance market data
            
        Returns:
            Dict: {{
                'should_close': bool,
                'flip_score': float,
                'reason': str,
                'details': Dict
            }}
        """
        try:
            should_close, flip_score, reason = self.flip_intelligence.should_close_early(
                current_direction, binance_data
            )
            
            return {
                'should_close': should_close,
                'flip_score': flip_score,
                'reason': reason,
                'details': self.flip_intelligence.get_state()
            }
            
        except Exception as e:
            logger.error(f"Error checking position flip: {e}")
            return {
                'should_close': False,
                'flip_score': 0.0,
                'reason':f'Error: {str(e)}',
                'details': {}
            }




















