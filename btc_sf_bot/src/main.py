import os
import sys
import json
import asyncio
import logging
import signal
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.data.connector import BinanceConnector
from src.data.cache import MarketCache
from src.data.db_manager import get_db
from src.data.binance_fetcher import BinanceDataFetcher as BinanceFetcher
from src.analysis.ict import ICTAnalyzer
from src.analysis.order_flow import OrderFlowAnalyzer
from src.analysis.volume_profile import VolumeProfileAnalyzer
from src.analysis.market_regime import MarketRegimeDetector as RegimeDetector
from src.analysis.h1_bias_engine import H1BiasEngine
from src.analysis.market_snapshot import MarketSnapshotBuilder
from src.signals.session_detector import SessionDetector
from src.analysis.pullback_detector import PullbackDetector
from src.analysis.frvp import MultiLayerVolumeProfile
from src.analysis.ipa_analyzer import IPAAnalyzer
from src.analysis.iof_analyzer import IOFAnalyzer
# v40.0: IPAF/IOFF replaced by detector architecture (ipa_fvg, ipa_ema, etc.)
# from src.analysis.ipa_frvp_analyzer import IPAFRVPAnalyzer as IPAFAnalyzer
# from src.analysis.iof_frvp_analyzer import IOFFRVPAnalyzer as IOFFAnalyzer

# v40.3: New detector architecture (9 types including REVERSAL)
from src.detectors import (
    MomentumDetector, MeanRevertDetector, AbsorptionDetector,
    ReversalOBDetector, ReversalOSDetector,
    IPADetector,  # v51.0: unified IPA (was IPAOBDetector, IPAFVGDetector, IPAEMADetector)
    VPBounceDetector, VPBreakoutDetector, VPAbsorbDetector, VPRevertDetector, VPPOCDetector,
    DetectionContext, ALL_DETECTORS,
)
from src.signals.sl_tp_calculator import InstitutionalSLTPCalculator as SLTPCalculator, SLTPRESult
from src.signals.signal_builder import SignalBuilder
from src.signals.signal_gate import SignalGate
from src.execution.telegram_alert import TelegramAlert as TelegramNotifier
from src.risk.position_sizer import RiskManager
from src.risk.trailing_stop_manager import TrailingStopManager
from src.signals.signal_gate import PositionInfo, AccountState
from src.utils.logger import setup_logger
from src.utils.terminal_display import get_display
from src.execution.webhook_server import set_confirmation_callback, start_server_background
from src.utils.decorators import retry, circuit_breaker, log_errors
from src.utils.metrics import timed_metric

logger = setup_logger("BTCSFBot")

class BTCSFBot:
    """
    BTC Smart Flow Bot (v13.5 - Mode 1/2/3/4 Independent Architecture)
    
    This bot combines Price Action (IPA) and Order Flow (IOF) analysis
    to generate high-probability trading signals for BTCUSDT.
    
    Architecture (v13.0+):
    1. Mode 1 (IPA): Institutional Price Action (OB, FVG, Structure)
    2. Mode 2 (IOF): Institutional Order Flow (Delta, OI, Liquidity Walls)
    3. Mode 3 (IPAF): Price Action + FRVP (Fixed Range Volume Profile)
    4. Mode 4 (IOFF): Order Flow + FRVP
    """
    
    def __init__(self, config_path: str = None):
        """Initialize the bot."""
        # Load config using ConfigManager class (with validation and hot-reload)
        from src.utils.config_v2 import ConfigManager
        self.config_manager = ConfigManager(config_path)
        # For backward compatibility, keep self.config as dict
        config_obj = self.config_manager.config or {}
        if hasattr(config_obj, 'dict'):
            self.config = config_obj.dict()
        else:
            self.config = config_obj
        
        # v36.0: Database manager
        from src.data.db_manager import get_db
        self.db = get_db()
        self._reconcile_with_mt5()  # v50.7: Sync with MT5 on startup
        self._last_snapshot_time = None
        self._last_regime = None  # v52.0: For regime change detection
        
        # Initialize components
        self.connector = BinanceConnector(self.config)
        self.cache = MarketCache()
        self.ict = ICTAnalyzer()
        self.order_flow = OrderFlowAnalyzer()
        self.volume_profile = VolumeProfileAnalyzer()
        self.regime_detector = RegimeDetector(self.config.get('market_regime', {}))
        
        # v27.0: Unified Data Layer
        self.h1_bias_engine = H1BiasEngine(self.config)
        self.snapshot_builder = MarketSnapshotBuilder(self.order_flow, self.ict)
        
        self.session_detector = SessionDetector()
        self.pullback_detector = PullbackDetector()
        self.frvp_engine = MultiLayerVolumeProfile(bins=50, value_area_pct=0.70)
        
        # v11.0: Independent Analyzers - pass the correct nested config section to each
        # H1 FIX: Analyzers read flat keys (e.g. 'score_threshold'); pass the matching
        # YAML sub-section so config.get('score_threshold') resolves correctly.
        self.ipa_analyzer = IPAAnalyzer(self.config.get('ipa', {}))
        self.iof_analyzer = IOFAnalyzer(self.config.get('iof', {}))
        # v40.0: IPAF/IOFF replaced by detector architecture
        # self.ipaf_analyzer = IPAFAnalyzer(self.config)
        # self.ioff_analyzer = IOFFAnalyzer(self.config)
        self.ipaf_analyzer = None
        self.ioff_analyzer = None
        
        # v40.3: New detector architecture (9 types)
        self.use_detectors = self.config.get('smart_flow', {}).get('use_detectors', False)
        self.detectors = []
        if self.use_detectors:
            detector_config = self.config.get('detectors', {})
            self.detectors = [
                # Order Flow (every 60s)
                MomentumDetector(detector_config.get('momentum', {})),
                MeanRevertDetector(detector_config.get('mean_revert', {})),
                AbsorptionDetector(detector_config.get('absorption', {})),
                ReversalOBDetector(detector_config.get('reversal', {})),
                ReversalOSDetector(detector_config.get('reversal', {})),
                # Price Action (candle close)
                # v51.0: MOD-38 - unified IPA (was IPAOBDetector, IPAFVGDetector, IPAEMADetector)
                IPADetector(self.config.get('ipa', {})),
                # VP (candle close) — v43.7
                VPBounceDetector(detector_config.get('vp', {})),
                VPBreakoutDetector(detector_config.get('vp', {})),
                VPAbsorbDetector(detector_config.get('vp', {})),
                VPRevertDetector(detector_config.get('vp', {})),
                VPPOCDetector(detector_config.get('vp', {})),
            ]
            logger.info(f"[v43.7] Detector architecture enabled ({len(self.detectors)} detectors)")
        else:
            logger.info("[v40.3] Using legacy 4-mode architecture (set use_detectors: true to switch)")
        
        # v18.7: AI Pre-Trade Analyzer (Level 1) - OpenRouter
        from src.analysis.ai_analyzer import AIMarketAnalyzer
        ai_config = {
            'openrouter_api_key': os.getenv('OPENROUTER_API_KEY', ''),
            'ai_model': 'deepseek/deepseek-v3.2',  # DeepSeek V3.2 — ใหม่สุด $0.26/$0.38 per 1M
            'call_interval': 300,  # 5 minutes
            'ai_enabled': False,
        }
        self.ai_analyzer = AIMarketAnalyzer(ai_config)
        self.ai_analyzer._ipa_ref = self.ipa_analyzer  # v25.0: AI reads M5 structure from IPA

        # v27.3: News filter for AI context
        from src.analysis.news_filter import NewsFilter
        self.news_filter = NewsFilter(self.config.get('smart_flow', {}))

        self.sl_tp_calc = SLTPCalculator(self.config)
        self.signal_builder = SignalBuilder()
        self.signal_gate = SignalGate(self.config)
        # RiskManager disabled - only EA handles risk
        # self.risk_manager = RiskManager(self.config)
        self.risk_manager = None
        self.trailing_manager = TrailingStopManager(self.config)
        self.binance_fetcher = BinanceFetcher(
            symbol=self.config.get('exchange.symbol', 'BTCUSDT'),
            testnet=self.config.get('exchange.testnet', False)
        )
        
        # Notifications
        self.telegram = None
        if self.config.get('telegram.enabled', False):
            self.telegram = TelegramNotifier(self.config)
            
        # State
        self.is_running = False
        self._is_shutting_down = False
        self.ws_handler = None
        self.ws_task = None
        self.last_candle_time = 0
        self.last_summary_date = datetime.now(timezone.utc).date()
        self.current_price = 0
        self.prev_oi = 0
        self._price_1min_ago = 0  # v26.0: track price 1 min ago for IOF

        # v27.2: Smart Interval — แยก timing ตาม data dependency
        self._last_iof_run = 0
        self._iof_interval = 15        # IOF/IOFF: ทุก 15 วินาที (order flow real-time)
        self._last_candle_time = None  # IPA/IPAF+AI: trigger by M5 candle close
        
        # Telemetry / Dashboard Cache
        self._last_ipa_result = None
        self._last_iof_result = None
        self._last_ipaf_result = None  # v24.1: For dashboard
        self._last_ioff_result = None  # v24.1: For dashboard
        self._last_sent_signal = None  # v24.1: For dashboard
        self._last_h1_bias = None
        self._last_terminal_update = 0
        self._cached_regime = None  # v27.0: Cached regime for single source of truth
        self._wall_tracking = {}
        self.wall_history_cache = []
        self._last_heartbeat_log = datetime.now(timezone.utc)
        
        # v24.0: Dashboard tracking
        self.cycle_count = 0
        self.start_time = datetime.now(timezone.utc)
        
        # v48.0: MOD-21 BUG-6 — Post-close Delta Alignment tracking
        self.pending_delta_signals = []
        
        # v18.5: Terminal Display
        self.terminal = get_display()
        
    def initialize(self) -> bool:
        """Initialize the bot components."""
        logger.info("Initializing BTC SF Bot...")
        
        # Connect to Binance
        if not self.connector.connect():
            logger.error("Failed to connect to Binance")
            return False
            
        # Initialize WebSocket
        from src.data.websocket import WebSocketHandler
        symbol = self.config.get('symbol', 'btcusdt')
        self.ws_handler = WebSocketHandler(symbol)
        self.ws_handler.register_callback('trade', self.on_trade)
        self.ws_handler.register_callback('order_book', self.on_order_book)
        
        # Initialize Risk Manager (disabled - only EA handles risk)
        # try:
        #     if hasattr(self.risk_manager, 'initialize') and callable(getattr(self.risk_manager, 'initialize')):
        #         if not self.risk_manager.initialize():
        #             logger.warning("Risk Manager initialization returned False, continuing anyway")
        # except Exception as e:
        #     logger.warning(f"Risk Manager initialization failed: {e}, continuing anyway")
             
        logger.info("Initialization complete")
        return True

    def _send_heartbeat(self):
        """v31.1: Send ZMQ heartbeat mid-analysis to prevent EA timeout."""
        try:
            from src.execution.signal_publisher import get_signal_sender
            sender = get_signal_sender()
            if sender.zeromq.is_connected():
                sender.zeromq.publish('heartbeat', {
                    'status': 'OK',
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            pass

    @log_errors
    @timed_metric("BTCSFBot._run_ipa_iof_analysis")
    async def _run_ipa_iof_analysis(self,
                                      candles_m5: pd.DataFrame,
                                      candles_h1: pd.DataFrame,
                                      current_price: float,
                                      binance_data: dict,
                                      cycle_start_time: float = None,
                                      new_candle: bool = False) -> bool:
        """
        v27.2: Smart Interval
        - Regime + H1 Bias + Snapshot + Terminal + IOF/IOFF: ทุก 15 วินาที
        - IPA/IPAF: เมื่อ M5 candle ปิดใหม่ (new_candle=True)
        v43.8: Smart Silent Display - 60s interval unless signal

        Returns:
            True if a signal was generated, False otherwise.
        """
        session = self.session_detector.get_current_session()
        magnets = self.ict.get_active_magnets(candles_m5, current_price)

        # v28.1: Cache for AI signal evaluation
        self._last_binance_data = binance_data
        self._last_candles_h1 = candles_h1
        self._last_candles_m5 = candles_m5
        self._last_data_timestamp = time.time()

        regime = self.regime_detector.detect(candles_m5, candles_h1)
        self._cached_regime = regime
        binance_data['adx_h1'] = regime.adx_h1 if regime else 25.0
        binance_data['regime_confidence'] = 'HIGH'

        h1_bias_pb = getattr(self, '_last_h1_bias', None) or 'NEUTRAL'
        pullback_info = self.pullback_detector.analyze(
            candles_m5=candles_m5, candles_h1=candles_h1,
            h1_bias=h1_bias_pb, current_price=current_price
        )
        binance_data['pullback'] = pullback_info

        # === v43.7: FRVP Engine FIRST ===
        # v43.8: Send all H1 swings + ATR for major swing filter
        h1_swings = {'last_swing_high_time': None, 'last_swing_low_time': None}
        if candles_h1 is not None and len(candles_h1) >= 10:
            h1_fractals = self.ict._get_fractals(candles_h1, n=5)
            if h1_fractals.get('highs'):
                h1_swings['last_swing_high_time'] = h1_fractals['highs'][-1].get('time')
            if h1_fractals.get('lows'):
                h1_swings['last_swing_low_time'] = h1_fractals['lows'][-1].get('time')
            # v43.8: All swings + ATR for major swing detection
            h1_swings['all_highs'] = h1_fractals.get('highs', [])
            h1_swings['all_lows'] = h1_fractals.get('lows', [])
            h1_swings['atr_h1'] = self.snapshot_builder._calc_atr(candles_h1, 14)
        
        ict_sweep = self.ict.get_last_sweep(candles_m5, current_price)
        frvp_data = self.frvp_engine.calculate(candles_m5, h1_swings, ict_data={'last_sweep': ict_sweep}, h1_candles=candles_h1)
        self.frvp_engine.commit_poc_state(frvp_data.get('layers', {}))
        self._last_frvp_data = frvp_data
        binance_data['frvp_data'] = frvp_data

        # === MarketSnapshot ===
        snapshot = self.snapshot_builder.build(
            candles_m5=candles_m5, candles_h1=candles_h1,
            binance_data=binance_data, regime_result=regime,
            current_price=current_price
        )

        # Propagate data
        binance_data['m5_state'] = snapshot.m5_state
        binance_data['m5_ema_position'] = snapshot.m5_ema_position
        binance_data['m5_efficiency'] = snapshot.m5_efficiency
        binance_data['m5_dist_pct'] = snapshot.m5_dist_pct
        binance_data['atr_m5'] = snapshot.atr_m5
        binance_data['der'] = snapshot.der
        binance_data['der_direction'] = snapshot.der_direction
        binance_data['der_persistence'] = snapshot.der_persistence
        binance_data['der_sustainability'] = snapshot.der_sustainability
        binance_data['delta'] = snapshot.delta
        binance_data['m5_bias'] = snapshot.m5_bias
        binance_data['m5_bias_level'] = snapshot.m5_bias_level
        binance_data['m5_swing_structure'] = snapshot.m5_swing_structure
        binance_data['m5_swing_ema_overextended'] = snapshot.m5_swing_ema_overextended
        binance_data['m5_swing_reversal_hint'] = snapshot.m5_swing_reversal_hint
        binance_data['h1_swing_structure'] = snapshot.h1_swing_structure
        binance_data['h1_swing_ema_overextended'] = snapshot.h1_swing_ema_overextended
        binance_data['h1_swing_reversal_hint'] = snapshot.h1_swing_reversal_hint

        # H1 Bias
        h1_bias_result = self.h1_bias_engine.analyze(
            candles_h1=candles_h1, candles_m5=candles_m5,
            binance_data=binance_data, regime=regime.regime,
        )

        # Refine M5 State
        snapshot = self.snapshot_builder.refine_m5_state(snapshot, candles_m5, h1_bias_result.bias)
        binance_data['m5_state'] = snapshot.m5_state
        binance_data['m5_bias'] = snapshot.m5_bias
        binance_data['m5_bias_level'] = snapshot.m5_bias_level
        binance_data['m5_swing_structure'] = snapshot.m5_swing_structure
        binance_data['m5_swing_ema_overextended'] = snapshot.m5_swing_ema_overextended
        binance_data['m5_swing_reversal_hint'] = snapshot.m5_swing_reversal_hint
        binance_data['h1_swing_structure'] = snapshot.h1_swing_structure
        binance_data['h1_swing_ema_overextended'] = snapshot.h1_swing_ema_overextended
        binance_data['h1_swing_reversal_hint'] = snapshot.h1_swing_reversal_hint

        # Update confidence
        confidence = self.regime_detector._calc_regime_confidence(regime.regime, snapshot.m5_state)
        regime.regime_confidence = confidence
        binance_data['regime_confidence'] = confidence
        self._cached_regime = regime

        # v51.2: Create ctx earlier so pending delta signals can use it
        ctx = DetectionContext(
            candles_m5=candles_m5, candles_h1=candles_h1,
            current_price=current_price, snapshot=snapshot,
            regime=regime, h1_bias=h1_bias_result,
            session=session, magnets=magnets,
            frvp_data=frvp_data, new_candle=new_candle,
            binance_data=binance_data,
        )

        # v48.0: MOD-21 BUG-6 — Handle Pending Delta Signals in REAL-TIME
        if self.pending_delta_signals:
            await self._handle_pending_delta_signals(ctx)

        # Propagate results
        binance_data['h1_bias'] = h1_bias_result.bias
        binance_data['h1_candle_bias'] = h1_bias_result.lc
        binance_data['lr_bias'] = h1_bias_result.lr
        binance_data['lr_count'] = h1_bias_result.lr_count
        binance_data['h1_ema9_direction'] = h1_bias_result.l2
        binance_data['h1_bias_result'] = h1_bias_result
        binance_data['regime_result'] = regime
        self._last_h1_bias = h1_bias_result.bias
        self._last_h1_bias_result = h1_bias_result

        binance_data['snapshot'] = snapshot
        h1_ema_dist_pct = binance_data.get('h1_ema_dist_pct', 0.0)
        wall_scan = binance_data.get('wall_scan', {})
        wall_info = f"{wall_scan.get('raw_dominant', 'NONE')} {wall_scan.get('raw_ratio', 1.0):.1f}x"

        # === v43.8: Smart Silent Display Logic ===
        show_terminal = (time.time() - self._last_terminal_update >= 60)
        
        # v43.8: Extract actual anchor used by FRVP (major swing, not raw fractals)
        # Always define anchor_info so it's available for signal display
        anchor_info = {}
        if frvp_data:
            swing_layer = frvp_data.get('layers', {}).get('swing_anchored', {})
            if swing_layer:
                anchor_info = {
                    'type': swing_layer.get('anchor_type', 'unknown'),
                    'price': swing_layer.get('anchor_price', 0),
                    'move': swing_layer.get('anchor_move', 0),
                    'age_candles': swing_layer.get('anchor_age_candles', 0),
                }
                atr_h1 = h1_swings.get('atr_h1', 0)
                if atr_h1:
                    anchor_info['atr_h1'] = atr_h1
        
        if show_terminal:
            self._last_terminal_update = time.time()
            # 1. Header
            self.terminal.header(current_price, session, regime.regime, datetime.now().strftime('%Y-%m-%d %H:%M'))
            # 2. Market Context
            self.terminal.market_context(
                regime=regime, h1_bias_result=h1_bias_result, snapshot=snapshot,
                h1_dist=h1_ema_dist_pct, wall_info=wall_info, anchor_info=anchor_info
            )

        # v52.0: Save regime snapshot every 1 minute (60s)
        now = time.time()
        current_regime = regime.regime if regime else 'UNKNOWN'
        # v52.0: Initialize on first run
        if self._last_snapshot_time is None:
            self._last_snapshot_time = now - 60  # Allow immediate save
        if self._last_regime is None:
            self._last_regime = 'INIT'  # Force first save
        
        if (now - self._last_snapshot_time) >= 60 or self._last_regime != current_regime:
            if self.db:
                # Get M5 debug data from snapshot
                m5_debug = getattr(snapshot, '_m5_debug', {}) or {}
                snap_data = {
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'price': current_price,
                    'regime': current_regime,
                    'regime_confidence': getattr(regime, 'regime_confidence', 'MEDIUM'),
                    'adx': getattr(regime, 'adx_h1', 0),
                    'bb_width': getattr(regime, 'bb_width', 0),
                    'm5_state': snapshot.m5_state,
                    'm5_bias': snapshot.m5_bias,
                    'h1_bias': h1_bias_result.bias,
                    'h1_dist_pct': h1_ema_dist_pct,
                    'wall_info': wall_info,
                    'delta': snapshot.delta,
                    'der': snapshot.der,
                    'signals_sent': 0,
                    # v52.0: Add M5 debug fields
                    'er_long': m5_debug.get('er_long', 0),
                    'er_short': m5_debug.get('er_short', 0),
                    'vol_rising': 1 if m5_debug.get('vol_rising', False) else 0,
                    'ema_slope': m5_debug.get('ema_slope', 0),
                    'net_long': m5_debug.get('net_long', 0),
                    'net_short': m5_debug.get('net_short', 0),
                    'atr_est': m5_debug.get('atr_est', 0),
                }
                self.db.insert_snapshot(snap_data)
            self._last_snapshot_time = now
            self._last_regime = current_regime

        # Detectors context
        ctx = DetectionContext(
            candles_m5=candles_m5, candles_h1=candles_h1,
            current_price=current_price, snapshot=snapshot,
            regime=regime, h1_bias=h1_bias_result,
            session=session, magnets=magnets,
            frvp_data=frvp_data, new_candle=new_candle,
            binance_data=binance_data,
        )

        account = self._get_account_state()
        positions = self._get_active_positions()
        detector_signals = []

        for detector in self.detectors:
            # Skip according to timing
            if detector.timing == 'CANDLE_CLOSE':
                if not new_candle: continue
            elif detector.timing == 'EVERY_60S':
                now = time.time()
                last_run = getattr(detector, '_last_detect_time', 0)
                if now - last_run < 60: continue
                detector._last_detect_time = now

            # Detect
            signals = detector.detect(ctx)

            if not signals:
                if show_terminal:
                    # Show skip reason only on 60s update
                    timing_str = '60s' if detector.timing == 'EVERY_60S' else 'candle'
                    self.terminal.detector_header(detector.signal_type, timing=timing_str)
                    reason = getattr(detector, 'last_reject_reason', 'No signal detected')
                    self.terminal.detector_no_signal(detector.signal_type, reason)
                continue

            # Signal found!
            if not show_terminal:
                # Force print header if signal found but 60s not reached
                self.terminal.header(current_price, session, regime.regime, datetime.now().strftime('%Y-%m-%d %H:%M'))
                self.terminal.market_context(regime=regime, h1_bias_result=h1_bias_result, snapshot=snapshot, h1_dist=h1_ema_dist_pct, wall_info=wall_info, anchor_info=anchor_info)
            
            timing_str = '60s' if detector.timing == 'EVERY_60S' else 'candle'
            self.terminal.detector_header(detector.signal_type, timing=timing_str)

            for sig in signals:
                # v48.0: MOD-21 BUG-6 — Delta Alignment Window
                # If signal has 'pending_delta', add to watch list instead of firing
                if sig.score_breakdown and sig.score_breakdown.get('pending_delta'):
                    self.pending_delta_signals.append({
                        'signal': sig,
                        'timestamp': time.time(),
                        'entry_zone_min': sig.score_breakdown.get('entry_zone_min', sig.entry_price * 0.999),
                        'entry_zone_max': sig.score_breakdown.get('entry_zone_max', sig.entry_price * 1.001),
                        'direction': sig.direction,
                        'ctx': ctx # Store context snapshot
                    })
                    self.terminal.gate(f'DELTA_WATCH_{sig.signal_type}', True, 'Pending 30s Alignment...')
                    continue

                if sig.signal_type.startswith('VP_'):
                    verified, verify_reason = self._quick_verify(sig, ctx)
                    if not verified:
                        self.terminal.gate('QuickVerify', False, verify_reason)
                        continue

                sl_tp = self.sl_tp_calc.calculate(sig)
                if not sl_tp:
                    self.terminal.gate('SL/TP', False, 'Calculation failed')
                    continue

                payload = self.signal_builder.build_from_result(sig, sl_tp)
                # Populate required gate fields
                # v44.4: Add FRVP anchor_type and DER fields for new gates
                frvp_anchor_type = ''
                if frvp_data:
                    swing_layer = frvp_data.get('layers', {}).get('swing_anchored', {})
                    frvp_anchor_type = swing_layer.get('anchor_type', '')
                
                payload.update({
                    'wall_info': wall_info,
                    'h1_dist_pct': h1_ema_dist_pct,
                    'der': snapshot.der,
                    'der_direction': snapshot.der_direction,
                    'der_persistence': snapshot.der_persistence,
                    'm5_state': snapshot.m5_state,
                    'delta': snapshot.delta,
                    'm5_ema_position': snapshot.m5_ema_position,
                    'current_price': current_price,
                    'regime_confidence': regime.regime_confidence,
                    'anchor_type': frvp_anchor_type,  # MOD-2: for FRVP_DIRECTION_BLOCK
                    'm5_bias': getattr(snapshot, 'm5_bias', 'NEUTRAL'),
                    'm5_bias_level': getattr(snapshot, 'm5_bias_level', 'NEUTRAL'),
                    'm5_swing_structure': getattr(snapshot, 'm5_swing_structure', 'NEUTRAL'),
                    'm5_swing_ema_overextended': getattr(snapshot, 'm5_swing_ema_overextended', False),
                    'm5_swing_reversal_hint': getattr(snapshot, 'm5_swing_reversal_hint', False),
                    'h1_swing_structure': getattr(snapshot, 'h1_swing_structure', 'NEUTRAL'),
                    'h1_swing_ema_overextended': getattr(snapshot, 'h1_swing_ema_overextended', False),
                    'h1_swing_reversal_hint': getattr(snapshot, 'h1_swing_reversal_hint', False),
                    # v51.3: H1 overextension — use CURRENT H1 (iloc[-2] caused 145% false positive on breakout)
                    'h1_last_high': float(candles_h1.iloc[-1]['high']) if candles_h1 is not None and len(candles_h1) >= 1 else 0,
                    'h1_last_low': float(candles_h1.iloc[-1]['low']) if candles_h1 is not None and len(candles_h1) >= 1 else 0,
                    'wall_stability_seconds': getattr(snapshot, 'wall_stability_sec', 0),
                })
                if h1_bias_result:
                    payload.update({
                        'l0': h1_bias_result.l0,
                        'l1': h1_bias_result.l1,
                        'l2': h1_bias_result.l2,
                        'l3': h1_bias_result.l3,
                        'h1_bias': h1_bias_result.bias,              # v50.4: direction (BULLISH/BEARISH/NEUTRAL)
                        'h1_bias_level': h1_bias_result.bias_level,  # MOD-4: for IPA_H1_BIAS_CLIMAX_BLOCK
                    })

                gate_result = self.signal_gate.check(payload, account, positions)
                if gate_result.passed:
                    self.signal_gate.mark_sent(payload)
                    await self._send_signal(payload)
                    
                    # v51.2: Record SENT signal to DB
                    if hasattr(self, 'db') and self.db:
                        breakdown = payload.get('extra_data', {}).get('score_breakdown') or payload.get('score_breakdown')
                        # Trades table: slim execution data only
                        trade_data = {
                            'signal_id': payload.get('signal_id'),
                            'mode': payload.get('mode'),
                            'direction': payload.get('direction'),
                            'signal_type': sig.signal_type,
                            'entry_price': payload.get('entry_price'),
                            'stop_loss': payload.get('stop_loss'),
                            'take_profit': payload.get('take_profit'),
                            'score': sig.score,
                            'status': 'SIGNAL_SENT',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        self.db.insert_trade(trade_data)
                        # Telemetry: FULL context — payload + snapshot + regime + VP
                        self.db.insert_signal_telemetry(payload.get('signal_id'), {
                            **payload,
                            'signal_type': sig.signal_type,
                            'score': sig.score,
                            'gate_status': 'PASSED',
                            'breakdown': breakdown,
                            # v51.4: Fields missing from payload — from snapshot/regime/frvp
                            'regime': regime.regime if regime else None,
                            'der_sustainability': getattr(snapshot, 'der_sustainability', None),
                            'atr_m5': getattr(snapshot, 'atr_m5', None),
                            'h1_ema9': getattr(snapshot, 'h1_ema9', None) if hasattr(snapshot, 'h1_ema9') else (h1_bias_result.ema9 if h1_bias_result and hasattr(h1_bias_result, 'ema9') else None),
                            'h1_ema20': getattr(snapshot, 'h1_ema20', None) if hasattr(snapshot, 'h1_ema20') else (h1_bias_result.ema20 if h1_bias_result and hasattr(h1_bias_result, 'ema20') else None),
                            'h1_ema50': getattr(snapshot, 'h1_ema50', None) if hasattr(snapshot, 'h1_ema50') else (h1_bias_result.ema50 if h1_bias_result and hasattr(h1_bias_result, 'ema50') else None),
                            'vp_poc': getattr(snapshot, 'vp_poc', None),
                            'vp_vah': getattr(snapshot, 'vp_vah', None),
                            'vp_val': getattr(snapshot, 'vp_val', None),
                            'vp_price_vs_va': getattr(snapshot, 'vp_price_vs_va', None),
                            'oi': getattr(snapshot, 'oi_change_pct', None),
                            'funding': binance_data.get('funding_rate'),
                            # Group B: snapshot raw metrics
                            'm5_efficiency': getattr(snapshot, 'm5_efficiency', None),
                            'm5_dist_pct': getattr(snapshot, 'm5_dist_pct', None),
                            'ema_trend': getattr(snapshot, 'ema_trend', None),
                            'pullback': binance_data.get('pullback'),
                            'wall_stability_seconds': getattr(snapshot, 'wall_stability_sec', None),
                            'raw_wall_ratio': binance_data.get('wall_scan', {}).get('raw_ratio') if binance_data.get('wall_scan') else None,
                            'raw_volume_ratio': getattr(snapshot, 'volume_ratio_m5', None),
                            'raw_m5_efficiency': getattr(snapshot, 'm5_efficiency', None),
                            'raw_oi_change_pct': getattr(snapshot, 'oi_change_pct', None),
                            'raw_atr_ratio': getattr(snapshot, 'atr_ratio', None),
                        })

                    detector_signals.append(sig.signal_type)
                    self.terminal.detector_signal(
                        signal_type=sig.signal_type, direction=sig.direction,
                        score=sig.score, threshold=sig.threshold, entry_price=sig.entry_price,
                        sl=sl_tp.stop_loss, tp=sl_tp.take_profit, rr=sl_tp.actual_rr,
                        der=payload.get('der', 0), der_dir='S' if payload.get('der', 0) < 0 else 'B',
                        wall=payload.get('wall_info', ''), m5_state=payload.get('m5_state', ''),
                    )
                else:
                    self.terminal.detector_blocked(sig.signal_type, sig.direction, sig.score, gate_result.reason)
                    # v51.2: Log gate block + telemetry with FULL payload
                    if hasattr(self, 'db') and self.db:
                        breakdown = payload.get('extra_data', {}).get('score_breakdown') or payload.get('score_breakdown')
                        # Gate blocks table: block reason
                        block_data = {
                            'signal_id': payload.get('signal_id'),
                            'mode': sig.signal_type,
                            'direction': sig.direction,
                            'signal_type': sig.signal_type,
                            'score': sig.score,
                            'gate_reason': gate_result.reason,
                            'price': payload.get('current_price'),
                            'breakdown': breakdown,
                        }
                        self.db.insert_gate_block(block_data)
                        # Telemetry: FULL context even for blocked signals
                        self.db.insert_signal_telemetry(payload.get('signal_id'), {
                            **payload,
                            'signal_type': sig.signal_type,
                            'score': sig.score,
                            'gate_status': 'BLOCKED',
                            'block_reason': gate_result.reason,
                            'breakdown': breakdown,
                            # v51.4: Fields missing from payload
                            'regime': regime.regime if regime else None,
                            'der_sustainability': getattr(snapshot, 'der_sustainability', None),
                            'atr_m5': getattr(snapshot, 'atr_m5', None),
                            'h1_ema9': getattr(snapshot, 'h1_ema9', None) if hasattr(snapshot, 'h1_ema9') else (h1_bias_result.ema9 if h1_bias_result and hasattr(h1_bias_result, 'ema9') else None),
                            'h1_ema20': getattr(snapshot, 'h1_ema20', None) if hasattr(snapshot, 'h1_ema20') else (h1_bias_result.ema20 if h1_bias_result and hasattr(h1_bias_result, 'ema20') else None),
                            'h1_ema50': getattr(snapshot, 'h1_ema50', None) if hasattr(snapshot, 'h1_ema50') else (h1_bias_result.ema50 if h1_bias_result and hasattr(h1_bias_result, 'ema50') else None),
                            'vp_poc': getattr(snapshot, 'vp_poc', None),
                            'vp_vah': getattr(snapshot, 'vp_vah', None),
                            'vp_val': getattr(snapshot, 'vp_val', None),
                            'vp_price_vs_va': getattr(snapshot, 'vp_price_vs_va', None),
                            'oi': getattr(snapshot, 'oi_change_pct', None),
                            'funding': binance_data.get('funding_rate'),
                            # Group B: snapshot raw metrics
                            'm5_efficiency': getattr(snapshot, 'm5_efficiency', None),
                            'm5_dist_pct': getattr(snapshot, 'm5_dist_pct', None),
                            'ema_trend': getattr(snapshot, 'ema_trend', None),
                            'pullback': binance_data.get('pullback'),
                            'wall_stability_seconds': getattr(snapshot, 'wall_stability_sec', None),
                            'raw_wall_ratio': binance_data.get('wall_scan', {}).get('raw_ratio') if binance_data.get('wall_scan') else None,
                            'raw_volume_ratio': getattr(snapshot, 'volume_ratio_m5', None),
                            'raw_m5_efficiency': getattr(snapshot, 'm5_efficiency', None),
                            'raw_oi_change_pct': getattr(snapshot, 'oi_change_pct', None),
                            'raw_atr_ratio': getattr(snapshot, 'atr_ratio', None),
                        })

        # v43.7: Commit POC state
        if hasattr(self, 'frvp_engine'):
            pass  # Already handled in main loop

        return len(detector_signals) > 0

    def _quick_verify(self, sig, ctx) -> tuple:
        """v43.7: Quick Verify — check conditions still valid before send."""
        # 1. Wall still there? (for VP_ABSORB, VP_BOUNCE)
        if sig.signal_type in ('VP_ABSORB', 'VP_BOUNCE'):
            wall = ctx.binance_data.get('wall_scan', {})
            if wall.get('raw_ratio', 0) < 2.0:
                return False, 'Wall disappeared'

        # 2. Price slip check
        slip = abs(ctx.current_price - sig.entry_price) / sig.entry_price * 100 if sig.entry_price > 0 else 0
        if slip > 0.1:
            return False, f'Price slipped {slip:.2f}%'

        # 3. Delta still same direction — skip VP_REVERT (mean reversion = counter-delta by design)
        if sig.signal_type != 'VP_REVERT':
            delta = ctx.binance_data.get('delta', 0)
            if sig.direction == 'LONG' and delta < -abs(sig.delta) * 0.5:
                return False, 'Delta reversed'
            if sig.direction == 'SHORT' and delta > abs(sig.delta) * 0.5:
                return False, 'Delta reversed'

        return True, 'OK'

    def _get_account_state(self) -> AccountState:
        """Build AccountState from current risk manager state."""
        try:
            daily_loss_pct = self.risk_manager.position_sizer.daily_loss if self.risk_manager else 0
            equity = self.risk_manager.equity if self.risk_manager else 0
            balance = self.risk_manager.balance if self.risk_manager else 0
            positions = self._get_active_positions()

            return AccountState(
                daily_pnl=0,
                daily_loss_pct=daily_loss_pct,
                equity=equity,
                balance=balance,
                open_positions=positions,
            )
        except Exception:
            return AccountState.empty()

    async def _handle_pending_delta_signals(self, ctx: DetectionContext):
        """
        v48.0: MOD-21 BUG-6 — Institutional Delta Alignment Window (30s)
        Re-checks signals that were valid at candle-close but had opposite Delta.
        Fires if new candle Delta aligns while price is still in zone.
        """
        now = time.time()
        current_delta = ctx.binance_data.get('delta', 0.0)
        current_price = ctx.current_price
        remaining_signals = []

        for item in self.pending_delta_signals:
            sig = item['signal']
            age = now - item['timestamp']
            
            # 1. Timeout check (30 seconds)
            if age > 30:
                self.terminal.gate(f'DELTA_TIMEOUT_{sig.signal_type}', False, 'Window closed')
                continue
            
            # 2. Zone check (Price must still be in the entry zone)
            if not (item['entry_zone_min'] <= current_price <= item['entry_zone_max']):
                # If price left zone, signal is invalid
                self.terminal.gate(f'DELTA_ZONE_FAIL_{sig.signal_type}', False, f'Price {current_price:.0f} left zone')
                continue

            # 3. Delta Alignment Check
            # We check if NEW candle delta matches direction
            confirmed = False
            if sig.direction == 'LONG' and current_delta > 5.0:  # Min 5 units to avoid noise
                confirmed = True
            elif sig.direction == 'SHORT' and current_delta < -5.0:
                confirmed = True
            
            if confirmed:
                self.terminal.gate(f'DELTA_CONFIRM_{sig.signal_type}', True, f'Aligned at +{age:.1f}s (D:{current_delta:.1f})')
                # 4. Proceed to fire (Gate + Send)
                # Note: We use original sl_tp and ctx from signal generation
                sl_tp = self.sl_tp_calc.calculate(sig)
                if sl_tp:
                    await self._process_verified_signal(sig, sl_tp, ctx)
                # Once fired, don't keep in pending
            else:
                # Still waiting
                remaining_signals.append(item)
        
        self.pending_delta_signals = remaining_signals

    async def _process_verified_signal(self, sig, sl_tp, ctx):
        """Helper to pass through gate and send to MT5."""
        # v52.0: Convert SignalResult to dict for gate check
        signal_dict = {
            'signal_id': getattr(sig, 'signal_id', ''),
            'signal_type': sig.signal_type,
            'direction': sig.direction,
            'entry_price': sig.entry_price,
            'score': sig.score,
            'required_rr': getattr(sig, 'required_rr', 0),
            'm5_state': sig.m5_state,
            'h1_bias_level': sig.h1_bias_level,
            'regime': sig.regime,
            'der': sig.der,
            'delta': sig.delta,
            'wall_info': sig.wall_info,
            'session': sig.session,
            'atr_m5': sig.atr_m5,
            'h1_swing_structure': ctx.binance_data.get('h1_swing_structure', 'NEUTRAL'),
            'h1_swing_ema_overextended': ctx.binance_data.get('h1_swing_ema_overextended', False),
            'h1_swing_reversal_hint': ctx.binance_data.get('h1_swing_reversal_hint', False),
            'm5_swing_structure': ctx.binance_data.get('m5_swing_structure', 'NEUTRAL'),
            'm5_swing_ema_overextended': ctx.binance_data.get('m5_swing_ema_overextended', False),
            'm5_swing_reversal_hint': ctx.binance_data.get('m5_swing_reversal_hint', False),
            'h1_dist_pct': sig.h1_dist_pct,
            'm5_bias': ctx.binance_data.get('m5_bias', 'NEUTRAL'),
            'm5_bias_level': ctx.binance_data.get('m5_bias_level', 'NEUTRAL'),
            'h1_last_high': ctx.binance_data.get('h1_last_high', 0),
            'h1_last_low': ctx.binance_data.get('h1_last_low', 0),
            'anchor_type': ctx.binance_data.get('anchor_type', ''),
        }
        
        # v51.2: Get account and positions for gate check
        account = self._get_account_state()
        positions = self._get_active_positions()
        
        # Gate
        gate_res = self.signal_gate.check(signal_dict, account, positions)
        if not gate_res.passed:
            self.terminal.gate(sig.signal_type, False, gate_res.reason)
            return

        # Prepare and Send
        # v54.0: Fix - build() needs mode, direction, entry_price, sl_tp, session, score
        mode = 'IPA'  # v54.0: All signal types use IPA timing
        signal_msg = self.signal_builder.build(
            mode=mode,
            direction=sig.direction,
            entry_price=sig.entry_price,
            sl_tp=sl_tp,
            session=sig.session,
            score=sig.score,
            regime=sig.regime,
            short_reason=sig.signal_type
        )
        from src.execution.signal_publisher import get_signal_sender
        sender = get_signal_sender()
        if sender.send(signal_msg):
            self.terminal.gate(sig.signal_type, True, 'SENT TO MT5 (Delta Aligned)')
            self._last_sent_signal = signal_msg
            if self.telegram: self.telegram.send_signal(signal_msg)

    def _get_active_positions(self) -> List[PositionInfo]:
        """
        Build list of PositionInfo from risk manager.
        
        v11.x BUG FIX: Extract mode from MT5 position comment.
        The comment contains the signal_id which has mode prefix (IPA_, IOF_, IPAF_, IOFF_).
        This ensures directional lock correctly identifies which mode opened the position.
        """
        positions = []
        try:
            if self.risk_manager and hasattr(self.risk_manager, 'positions_state'):
                for pos in self.risk_manager.positions_state:
                    comment = pos.get('comment', '')
                    
                    # Try to extract mode from comment (signal_id format: IPA_SHORT_... or IOFF_LONG_...)
                    # BUG FIX: Check longer mode names FIRST (IPAF before IPA, IOFF before IOF)
                    # Otherwise "IPA_SHORT" would match "IPA" first and never reach "IPAF"
                    mode = 'IPA'  # Default legacy
                    # Check FRVP-style comments first
                    if 'IPA_FRVP' in comment:
                        mode = 'IPA'
                    elif 'IOF_FRVP' in comment:
                        mode = 'IOFF'
                    # Check standard prefix (check longer modes first)
                    elif comment.startswith('IOFF_'):
                        mode = 'IOFF'
                    elif comment.startswith('IPAF_'):
                        mode = 'IPAF'
                    elif comment.startswith('IOF_'):
                        mode = 'IOF'
                    elif comment.startswith('IPA_'):
                        mode = 'IPA'
                    # Check for underscore-contained modes (e.g., middle of signal_id)
                    elif '_IOFF' in comment or comment.endswith('_IOFF'):
                        mode = 'IOFF'
                    elif '_IPAF' in comment or comment.endswith('_IPAF'):
                        mode = 'IPAF'
                    elif '_IOF' in comment or comment.endswith('_IOF'):
                        mode = 'IOF'
                    elif '_IPA' in comment or comment.endswith('_IPA'):
                        mode = 'IPA'
                    
                    positions.append(PositionInfo(
                        ticket=pos.get('ticket', 0),
                        symbol=pos.get('symbol', ''),
                        direction='LONG' if pos.get('type', 0) == 0 else 'SHORT',
                        mode=mode,
                        open_time=datetime.now(timezone.utc),
                        entry_price=pos.get('entry', 0),
                        current_pnl=pos.get('profit', 0),
                    ))
        except Exception:
            pass
        return positions

    async def _send_signal(self, signal: dict):
        """Send signal via publisher and Telegram."""
        from src.execution.signal_publisher import get_signal_sender

        # v28.1: AI call removed from here — moved to on_ea_confirmation(OPENED)
        # AI วิเคราะห์เฉพาะเมื่อ EA เปิดจริง ไม่เปลือง API กับ signal ที่ EA skip
        ai_result = getattr(self, '_last_ai_result', None)
        if ai_result and hasattr(self, 'ai_analyzer'):
            signal_dir = signal.get('direction', '')
            ai_bias = ai_result.get('bias', 'NEUTRAL')
            ai_action = ai_result.get('action', 'WAIT')
            aligned = (
                (signal_dir == 'LONG' and ai_bias == 'BULLISH') or
                (signal_dir == 'SHORT' and ai_bias == 'BEARISH')
            )

            # v27.3: Calculate AI age — how stale is this AI result?
            ai_age_sec = 0
            ai_fresh = False
            if ai_result.get('timestamp'):
                try:
                    ai_time = datetime.fromisoformat(ai_result['timestamp'])
                    ai_age_sec = int((datetime.now(timezone.utc) - ai_time).total_seconds())
                    ai_fresh = ai_age_sec < 30  # < 30s = same candle cycle as signal
                except (ValueError, TypeError):
                    pass

            # v27.3: Signal mode determines if AI comparison is valid
            mode = signal.get('mode', '')
            is_ipa_mode = mode == 'IPA'
            # IPA/IPAF วิ่งพร้อม AI (candle close) → เทียบได้ตรง
            # IOF/IOFF วิ่งทุก 15 วิ → AI อาจ stale → บอกใน log
            ai_tag = "FRESH" if ai_fresh else f"STALE_{ai_age_sec}s"

            # v29.1: AI action gate logging — track for future analysis
            if ai_action == 'WAIT':
                logger.warning(
                    f"⚠️ AI WAIT [{ai_tag}]: {mode} {signal_dir} "
                    f"AI says {ai_bias} WAIT conf:{ai_result.get('confidence')}% — would block if gate enabled"
                )
            elif ai_action == 'CAUTION':
                logger.warning(
                    f"⚠️ AI CAUTION [{ai_tag}]: {mode} {signal_dir} "
                    f"AI says {ai_bias} CAUTION conf:{ai_result.get('confidence')}% — would penalty -2 if gate enabled"
                )
            elif ai_bias != 'NEUTRAL' and not aligned:
                ai_conf = ai_result.get('confidence', 0)
                if ai_fresh and ai_conf > 60:
                    logger.warning(
                        f"AI_CONFLICT_BLOCK [{ai_tag}]: {mode} {signal_dir} "
                        f"blocked — AI says {ai_bias} conf:{ai_conf}% (fresh, directional conflict)"
                    )
                    return
                else:
                    logger.warning(
                        f"⚠️ AI CONFLICT [{ai_tag}]: {mode} {signal_dir} "
                        f"but AI says {ai_bias} conf:{ai_conf}% — not blocked (stale or low conf)"
                    )
            elif ai_action == 'TRADE':
                logger.info(
                    f"✅ AI TRADE [{ai_tag}]: {mode} {signal_dir} "
                    f"AI confirms {ai_bias} conf:{ai_result.get('confidence')}%"
                )
            
            # v26.0: Log trade entry with full market context
            # v31.0: Read from live sources directly to avoid stale dashboard_state
            # (dashboard_state is updated AFTER _send_signal completes in the main loop)
            _bd = getattr(self, '_last_binance_data', {}) or {}
            _h1r = getattr(self.ipa_analyzer, '_last_h1_result', None) or {}
            _h1_bias = getattr(self, '_last_h1_bias_result', None)
            _wall_scan = _bd.get('wall_scan', {}) or {}
            _wall_dom = _wall_scan.get('raw_dominant', 'NONE')
            _wall_ratio = _wall_scan.get('raw_ratio', 1.0)
            _of_summary = _bd.get('order_flow_summary', {}) or {}
            market_ctx = {
                # H1 Bias Layers
                'l0': getattr(_h1_bias, 'l0', 'NEUTRAL') if _h1_bias else 'NEUTRAL',
                'l1': getattr(_h1_bias, 'l1', 'NEUTRAL') if _h1_bias else 'NEUTRAL',
                'l2': getattr(_h1_bias, 'l2', 'NEUTRAL') if _h1_bias else 'NEUTRAL',
                'l3': getattr(_h1_bias, 'l3', 'NEUTRAL') if _h1_bias else 'NEUTRAL',
                'lc': getattr(_h1_bias, 'lc', 'NEUTRAL') if _h1_bias else 'NEUTRAL',
                'lr': getattr(_h1_bias, 'lr', 'NEUTRAL') if _h1_bias else 'NEUTRAL',
                # H1 EMA
                'h1_ema9': _bd.get('ema9'),
                'h1_ema20': _bd.get('ema20'),
                'h1_ema50': _bd.get('ema50'),
                'h1_dist_pct': _bd.get('h1_ema_dist_pct', 0),
                'ema_trend': _h1_bias.bias if _h1_bias else None,
                # Pullback
                'pullback': (_bd.get('pullback', {}) or {}).get('status', 'NONE'),
                # Wall
                'wall_info': f"{_wall_dom} {_wall_ratio:.1f}x",
                # Order Flow
                'delta': _of_summary.get('delta'),
                'der': _of_summary.get('der'),
                'oi': getattr(self, '_last_oi', None),
                'funding': _bd.get('funding_rate'),
                # v50.8: MLVP — use swing_anchored (matches TradingView)
                'poc': round((getattr(self, '_last_frvp_data', None) or {}).get('layers', {}).get('swing_anchored', {}).get('poc') or 0, 1) or None,
                'vah': round((getattr(self, '_last_frvp_data', None) or {}).get('layers', {}).get('swing_anchored', {}).get('vah') or 0, 1) or None,
                'val': round((getattr(self, '_last_frvp_data', None) or {}).get('layers', {}).get('swing_anchored', {}).get('val') or 0, 1) or None,
                # Score breakdown
                'breakdown': signal.get('score_breakdown') or signal.get('breakdown'),
                # v29.1: Missing fields for analysis
                'm5_state': _bd.get('m5_state'),
                'regime': self._cached_regime.regime if self._cached_regime else None,
                'h1_bias_level': _h1_bias.bias_level if _h1_bias else None,
                # v30.7: DER stability + M5 context for analyst
                'der_direction': _bd.get('der_direction'),
                'der_persistence': _bd.get('der_persistence'),
                'der_sustainability': _bd.get('der_sustainability'),
                'm5_efficiency': _bd.get('m5_efficiency'),
                'm5_ema_position': _bd.get('m5_ema_position'),
                'atr_m5': _bd.get('atr_m5'),
                # v29.1: Chart patterns (data only — not used as gate)
                # v27.3: AI freshness — for accurate backtest comparison
                'ai_fresh': ai_fresh,
                'ai_age_sec': ai_age_sec,
            }
            self.ai_analyzer.log_trade_entry(
                signal_id=signal.get('signal_id', ''),  # v42.2: signal_id is required
                signal=signal,
                ai_analysis=ai_result,
                market_context=market_ctx
            )
        
        sender = get_signal_sender()
        result = sender.send_signal(signal)
        if result.get('sent'):
            logger.info(f"Signal {signal['signal_id']} sent via {result.get('primary_method', 'unknown')}")
            # v24.1: Store for dashboard
            self._last_sent_signal = signal
        else:
            logger.warning(f"Signal {signal['signal_id']} not sent: {result.get('reason', 'unknown')}")

        if self.telegram:
            await self.telegram.send_signal_alert(signal)

    def _update_trade_ai(self, signal_id: str, ai_result: dict):
        """v36.2: Update trade with AI analysis after EA OPENED using database."""
        try:
            trade = self.ai_analyzer._db.get_trade(signal_id)
            if not trade or trade.get('status') != 'OPENED':
                return
            
            # Update trade with AI info
            self.ai_analyzer._db.update_trade(signal_id, {
#                 'ai_bias': ai_result.get('bias'),
                'ai_confidence': ai_result.get('confidence'),
                'ai_action': ai_result.get('action'),
                'ai_reason': ai_result.get('reason', '')[:100],
                'ai_aligned': 1 if self.ai_analyzer._check_aligned(
                    trade.get('direction'), ai_result
                ) else 0
            })
            logger.info(
                f"[AI] Trade {signal_id} | {ai_result.get('bias')} {ai_result.get('confidence')}% "
                f"{'ALIGNED' if self.ai_analyzer._check_aligned(trade.get('direction'), ai_result) else 'CONFLICT'}"
            )
        except Exception as e:
            logger.warning(f"[AI] Update trade AI error: {e}")

    async def on_ea_confirmation(self, data: dict):
        """
        Handle EA confirmation from webhook (v19.0)
        
        Args:
            data: {'signal_id': str, 'status': 'OPENED'|'TP'|'SL'|'CLOSED', 
                   'entry_price': float, 'profit': float, ...}
        """
        try:
            signal_id = data.get('signal_id', '')
            status = data.get('status', '').upper()
            
            logger.info(f"[EA] Confirmation received | {signal_id} | Status: {status}")
            
            if not hasattr(self, 'ai_analyzer'):
                return
            
            if status == 'OPENED':
                # EA เปิด trade จริง
                entry_price = data.get('price', data.get('entry_price', 0))
                self.ai_analyzer.log_trade_opened(signal_id, entry_price)

                # v28.1: AI วิเคราะห์เฉพาะเมื่อ EA เปิดจริง (ไม่เปลือง API กับ EA_SKIPPED)
                if self.ai_analyzer.enabled:
                    binance_data = getattr(self, '_last_binance_data', {})
                    candles_h1 = getattr(self, '_last_candles_h1', None)
                    candles_m5 = getattr(self, '_last_candles_m5', None)
                    ai_result = await self.ai_analyzer.analyze(
                        candles_h1=candles_h1,
                        candles_m5=candles_m5,
                        binance_data=binance_data,
                        current_price=self.current_price,
                    )
                    if ai_result:
                        self._last_ai_result = ai_result
                        # Update trade log with AI analysis
                        self._update_trade_ai(signal_id, ai_result)
                
            elif status in ('TP', 'SL', 'CLOSED', 'EXIT', 'CLOSE'):
                # EA ปิด trade
                pnl = data.get('profit', 0)
                mfe = data.get('mfe', 0)  # v26.0: max favorable excursion
                mae = data.get('mae', 0)  # v26.0: max adverse excursion
                self.ai_analyzer.log_trade_exit(signal_id, pnl, status, mfe=mfe, mae=mae)

                # v51.3 MOD-42: Update loss streak counter
                trade_result = 'WIN' if status == 'TP' else 'LOSS'
                if hasattr(self, 'signal_gate'):
                    self.signal_gate.on_trade_result(trade_result)

        except Exception as e:
            logger.warning(f"[EA] Confirmation handling error: {e}")

    async def on_trade(self, trade: dict):
        """Handle incoming trade."""
        self.cache.add_trade(trade)
        self.current_price = trade.get('price', self.current_price)
        
        # Calculate delta
        delta = 0
        if self.ws_handler is not None:
            delta = self.ws_handler.calculate_delta(50)
        self.cache.add_delta(delta)
        # Note: Real-time trade stream handled by IPA/IOF analyzers on 30s cycle.
        # No per-trade processing needed in v4.9 M5.
    
    def _get_current_session(self) -> str:
        """Get current trading session based on UTC time."""
        now = datetime.now(timezone.utc)
        hour = now.hour
        
        # Trading sessions (UTC)
        # Asia: 00:00 - 08:00 (Tokyo)
        # London: 08:00 - 16:00 (London)  
        # NY: 13:00 - 21:00 (New York)
        # Overlaps:
        # London-NY: 13:00 - 16:00
        # Asia-London: 08:00 - 09:00
        
        if 0 <= hour < 8:
            return "ASIA"
        elif 8 <= hour < 13:
            return "LONDON"
        elif 13 <= hour < 16:
            return "LONDON-NY"  # High volatility overlap
        elif 16 <= hour < 21:
            return "NY"
        else:  # 21:00 - 00:00
            return "ASIA-LATE"
    
    def _get_indicators_data(self, candles, order_book, trades, current_price, avg_volume) -> dict:
        """
        Get current market indicators for dashboard.
        
        v4.9 M5: Uses IPA/IOF analyzers and legacy analyzers (order_flow, volume_profile, ict).
        No longer depends on SignalManagerV3.
        """
        # === Order Flow ===
        bids = order_book.get('bids', {})
        asks = order_book.get('asks', {})
        oi = order_book.get('open_interest', 0)
        prev_oi = order_book.get('prev_oi', 0)
        order_flow_data = self.order_flow.get_order_flow_summary(
            bids=bids, asks=asks, trades=trades,
            price=current_price, open_interest=oi, prev_oi=prev_oi
        )
        delta = order_flow_data.get('delta', 0)
        imbalance = order_flow_data.get('imbalance_ratio', 0)
        cvd_div = order_flow_data.get('cvd_divergence', 'NONE')
        
        # === Volume Profile ===
        vp_data = self.volume_profile.get_volume_profile_summary(candles, current_price)
        poc = vp_data.get('poc', 0)
        
        # === ICT ===
        ict_data = self.ict.get_ict_summary(candles, current_price)
        struct_dict = ict_data.get('structure', {})
        if isinstance(struct_dict, dict):
            raw_trend = struct_dict.get('trend', 'RANGE')
            m5_structure = raw_trend if raw_trend in ['BULLISH', 'BEARISH'] else 'RANGE'
        else:
            m5_structure = 'RANGE'
        
        ob_present = bool(ict_data.get('bullish_obs', []) or ict_data.get('bearish_obs', []))
        fvg_present = bool(ict_data.get('fvgs', {}).get('bullish') or ict_data.get('fvgs', {}).get('bearish'))
        liq_sweep = ict_data.get('liquidity_sweep', {})
        bid_wall = liq_sweep.get('level') if liq_sweep.get('type') == 'SWEEP_LOW' else None
        ask_wall = liq_sweep.get('level') if liq_sweep.get('type') == 'SWEEP_HIGH' else None
        
        # === Fractals for BOS targets ===
        major_fractals = self.ict._get_fractals(candles, n=5)
        swing_highs = major_fractals.get('highs', [])
        swing_lows = major_fractals.get('lows', [])
        bos_high = swing_highs[-1].get('level') if swing_highs else current_price
        bos_low = swing_lows[-1].get('level') if swing_lows else current_price
        
        # === Regime & Session (PHASE 1) ===
        candles_h1 = getattr(self, 'htf_cache', {}).get('h1', {}).get('data') if hasattr(self, 'htf_cache') else None
        if candles_h1 is None or candles_h1.empty:
            try:
                symbol = self.config.get('exchange.symbol', 'BTC/USDT:USDT')
                candles_h1 = self.connector.get_ohlcv(symbol, '1h', limit=200)
            except Exception:
                candles_h1 = None
        
        # v27.0: Use cached regime (single source of truth)
        regime_str = 'RANGE'
        if self._cached_regime is not None:
            regime_str = self._cached_regime.regime
        
        current_session = self.session_detector.get_current_session()
        
        # === IPA/IOF scores from last analysis cycle ===
        ipa_score = self._last_ipa_result.score if self._last_ipa_result else 0
        iof_score = self._last_iof_result.score if self._last_iof_result else 0
        
        # === Win/loss from position sizer ===
        trades_today = getattr(self.risk_manager.position_sizer, 'trades_today', []) if self.risk_manager else []
        total_trades = len(trades_today)
        wins = sum(1 for t in trades_today if t.get('pnl', 0) > 0)
        losses = total_trades - wins
        win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0.0
        
        return {
            'timestamp': datetime.now().isoformat(),
            'current_price': current_price,
            'avg_volume': avg_volume,
            'drawdown': getattr(self.risk_manager, 'drawdown_pct', 0),
            'indicators': {
                'session': current_session,
                'delta': delta,
                'imbalance_ratio': imbalance,
                'volume_spike': 0,
                'poc': poc,
                'ob_present': ob_present,
                'fvg_present': fvg_present,
                'regime': regime_str,
                'structure': m5_structure,
                'htf_structure': "NONE",
                'bos_high': round(bos_high, 2),
                'bos_low': round(bos_low, 2),
                'zone_context': 'RANGE',
                'cvd_divergence': cvd_div,
                'bid_wall': bid_wall,
                'ask_wall': ask_wall,
                # v4.9 M5: IPA/IOF scores
                'phase1_score': ipa_score,
                'phase2_score': iof_score,
                # Legacy fields zeroed (no longer produced)
                'p1_flow': 0,
                'sweep_score': 0,
                'wall_score': 0,
                'zone_score': 0,
                'risk_tier': getattr(self.risk_manager, 'tier', 0),
                'drawdown': getattr(self.risk_manager, 'drawdown_pct', 0),
                'smart_flow_pattern': 'NONE',
                'smart_flow_score': 0,
                'total_trades': total_trades,
                'wins': wins,
                'losses': losses,
                'win_rate': win_rate,
                'next_news': None,
            }
        }
    
    async def on_order_book(self, order_book: dict):
        """Handle incoming order book."""
        self.cache.update_order_book(order_book)
    
    async def on_signal_from_webhook(self, signal_data: dict):
        """
        Handle signal from webhook.
        
        Args:
            signal_data: Signal dictionary
        """
        logger.info(f"Received webhook signal: {signal_data}")
        
        # Forward to Telegram if enabled
        if self.telegram:
            await self.telegram.send_signal_alert(signal_data)
            
    async def on_confirmation_from_webhook(self, confirmation_data: dict):
        """
        Handle trade confirmation/result from MT5.
        
        Args:
            confirmation_data: Confirmation dictionary with results
        """
        logger.info(f"Received trade confirmation: {confirmation_data}")
        
        status = confirmation_data.get('status', '').upper()
        
        # If trade is closed (TP/SL), record the result
        if status in ['TP', 'SL', 'CLOSED', 'EXIT']:
            if self.risk_manager and self.risk_manager.position_sizer:
                self.risk_manager.position_sizer.record_external_trade(
                    symbol=confirmation_data.get('symbol', 'BTCUSDT'),
                    direction=confirmation_data.get('action', 'BUY'),
                    entry=confirmation_data.get('entry', 0) or confirmation_data.get('entry_price', 0),
                    exit=confirmation_data.get('exit', 0) or confirmation_data.get('exit_price', 0),
                    lot_size=confirmation_data.get('lot', 0) or confirmation_data.get('filled_lot', 0),
                    profit=confirmation_data.get('profit', 0),
                    commission=confirmation_data.get('commission', 0)
                )
                
            # Note: Individual TP/SL Telegram alerts removed as per user request. 
            # Only Daily Summary will be sent.
    
    @log_errors
    @timed_metric("BTCSFBot.analyze_and_trade")
    async def analyze_and_trade(self):
        """Main analysis and trading loop."""
        # logger.info("💓 Analysis loop heartbeat") # Silenced per user request
        try:
            
            # Get symbol
            symbol = self.config.get('exchange.symbol', 'BTC/USDT:USDT')
            timeframe = self.config.get('exchange.timeframe', '5m')  # ← FIXED: default now 5m (was 1m)
            
            # Initialize HTF cache if not exists
            if not hasattr(self, 'htf_cache'):
                self.htf_cache = {'h1': {'data': None, 'timestamp': 0}}
                self.htf_update_interval = 60  # Update every 60 seconds
            
            # Get current time
            now = time.time()
            
            # Update H1 candles cache (every 60 seconds)
            if now - self.htf_cache['h1']['timestamp'] > self.htf_update_interval:
                candles_h1 = self.connector.get_ohlcv(symbol, '1h', limit=200)
                if not candles_h1.empty:
                    self.htf_cache['h1']['data'] = candles_h1
                    self.htf_cache['h1']['timestamp'] = now
            
            candles_h1 = self.htf_cache['h1'].get('data')

            if candles_h1 is None or len(candles_h1) < 4:
                logger.warning("H1 candles not available yet, skipping analysis")
                return

            # Get current price
            self.current_price = self.connector.get_price(symbol)
            
            if self.current_price == 0:
                logger.warning("Failed to get price")
                return
            
            # Get order book and OI (SMC Enhancement)
            order_book = self.cache.get_order_book()
            oi = self.connector.get_open_interest(symbol)
            
            order_book['open_interest'] = oi
            # v9.1: FIX — store previous BEFORE overwriting
            oi_previous = self.prev_oi
            self.prev_oi = oi
            # v25.0: Store OI for dashboard
            self._last_oi = oi
            
            # v6.0: Get order book from Binance API (MT5 may not have OB data)
            try:
                binance_ob = await self.binance_fetcher.fetch_order_book(limit=100)
                if binance_ob and binance_ob.get('bids') and binance_ob.get('asks'):
                    bids = binance_ob.get('bids', [])
                    asks = binance_ob.get('asks', [])
                    logger.debug(f"[BINANCE] Order Book: bids={len(bids)}, asks={len(asks)}")
                else:
                    bids = order_book.get('bids', [])
                    asks = order_book.get('asks', [])
            except Exception as e:
                logger.debug(f"[BINANCE] Order book fetch failed: {e}")
                bids = order_book.get('bids', [])
                asks = order_book.get('asks', [])
            
            # v42.2: Get fresh recent trades from exchange for accurate delta calculation
            # Cache may be empty or stale - use connector for real-time data
            try:
                trades = self.connector.get_recent_trades(symbol, limit=100)
            except Exception as e:
                logger.debug(f"[Trades] Fetch failed: {e}, using cache")
                trades = self.cache.get_trades(100)
            
            # Get candles
            candles = self.connector.get_ohlcv(symbol, timeframe, limit=300)

            # v25.0: Store last 50 close prices for frontend chart
            if not candles.empty:
                self._price_history = [round(float(p), 1) for p in candles['close'].iloc[-50:].values]

            if candles.empty:
                logger.warning("Failed to get candles")
                return
            
            # Update volume history
            if not candles.empty:
                self.cache.add_volume(candles['volume'].iloc[-1])
                
                # v4.9 M5: Track candle changes (no signal_manager needed)
                candle_time = candles.index[-1].timestamp()
                if candle_time > self.last_candle_time:
                    self.last_candle_time = candle_time
            
            # Get average volume
            avg_volume = self.cache.get_average_volume(20)

            # v9.1: Track wall history for IOF anti-spoofing
            # Track how long each large order book level persists
            now = datetime.now(timezone.utc)
            # Normalize bids/asks to list format
            all_levels = []
            if bids and asks:
                for p, s in bids[:10] + asks[:10]:
                    all_levels.append((float(p), float(s)))
            # For each large level (top 5 by size), track persistence
            large_levels = sorted(all_levels, key=lambda x: x[1], reverse=True)[:5]
            updated_prices = set()
            for level_price, level_size in large_levels:
                # Only track significant levels (>$50K)
                if level_price * level_size < 50000:
                    continue
                updated_prices.add(level_price)
                if level_price in self._wall_tracking:
                    self._wall_tracking[level_price]['last_seen'] = now
                else:
                    self._wall_tracking[level_price] = {
                        'first_seen': now,
                        'last_seen': now,
                        'size': level_size,
                        'refilled': False,
                    }
            # Build wall_history_cache with stability_seconds
            self.wall_history_cache = []
            for price, data in self._wall_tracking.items():
                stability = (data['last_seen'] - data['first_seen']).total_seconds()
                self.wall_history_cache.append({
                    'price': price,
                    'size': data['size'],
                    'stability_seconds': stability,
                    'refilled': data['refilled'],
                })
            # Clean up stale walls (not seen in 5 minutes)
            stale = [p for p, d in self._wall_tracking.items()
                     if (now - d['last_seen']).total_seconds() > 300]
            for p in stale:
                del self._wall_tracking[p]

            # === v27.2: Smart Interval — IOF ทุก 15 วิ, IPA+AI ตอน candle close ===
            cycle_start_time = time.time()
            throttle_now = time.time()

            # Check IOF interval (15 seconds)
            run_iof = (throttle_now - self._last_iof_run >= self._iof_interval)
            if not run_iof:
                return  # ยังไม่ถึงเวลา IOF → ข้ามทั้ง cycle
            
            # v18.6: Get session for terminal display
            # v27.0: Regime is calculated ONCE in _run_ipa_iof_analysis (single source of truth)
            session = self.session_detector.get_current_session()

            # Build binance_data dict for IOF analyzer
            # v6.0: Get funding rate from Binance
            funding_rate = 0.0
            try:
                funding_rate = await self.binance_fetcher.fetch_funding_rate()
            except Exception:
                pass
            
            binance_data = {
                'oi': oi,
                'oi_1min_ago': oi_previous,
                'current_price': self.current_price,
                'order_book': {'bids': bids, 'asks': asks},
                'funding_rate': funding_rate,
                'wall_history': list(getattr(self, 'wall_history_cache', [])),
                'h1_bias': getattr(self, '_last_h1_bias', None),
                'h1_candles': candles_h1,
                # v27.0: ADX now set in _run_ipa_iof_analysis (single source of truth)
                # v26.0: Fix missing keys (CRITICAL audit findings)
                'trades': trades,                      # was never set → trades count always 0
                'price_1min_ago': getattr(self, '_price_1min_ago', self.current_price),
                'liquidation_cascade': False,          # placeholder — no liquidation data source yet
            }

            # v14.3: Calculate H1 EMA20 distance for DER Bypass
            if candles_h1 is not None and len(candles_h1) >= 50:
                ema9_h1 = candles_h1['close'].ewm(span=9).mean().iloc[-1]
                ema20_h1 = candles_h1['close'].ewm(span=20).mean().iloc[-1]
                ema50_h1 = candles_h1['close'].ewm(span=50).mean().iloc[-1]
                h1_ema_dist_pct = abs(self.current_price - ema20_h1) / ema20_h1 * 100
                
                # v20.1: Set both ema9/ema20/ema50 (for AI) and ema20_h1 (for other uses)
                binance_data['ema9'] = float(ema9_h1)
                binance_data['ema20'] = float(ema20_h1)
                binance_data['ema50'] = float(ema50_h1)
                binance_data['ema20_h1'] = float(ema20_h1)
                binance_data['h1_ema_dist_pct'] = h1_ema_dist_pct

            else:
                binance_data['ema20_h1'] = self.current_price
                binance_data['h1_ema_dist_pct'] = 0

            # v18.3: Build wall_scan for LC + LR detection
            # Calculate raw wall ratio from order_book
            # Handle both list and dict formats
            try:
                if bids and isinstance(bids[0], dict):
                    bid_total = sum(b.get('quantity', 0) for b in bids[:10] if isinstance(b, dict))
                    ask_total = sum(a.get('quantity', 0) for a in asks[:10] if isinstance(a, dict))
                else:
                    # List format: [price, quantity]
                    bid_total = sum(b[1] for b in bids[:10] if isinstance(b, (list, tuple)) and len(b) > 1)
                    ask_total = sum(a[1] for a in asks[:10] if isinstance(a, (list, tuple)) and len(a) > 1)
            except (TypeError, IndexError):
                bid_total = 0
                ask_total = 0
            
            if bid_total > 0 and ask_total > 0:
                raw_ratio = max(bid_total / ask_total, ask_total / bid_total)
                raw_dominant = 'BID' if bid_total > ask_total else 'ASK'
            else:
                raw_ratio = 1.0
                raw_dominant = 'NONE'
            binance_data['wall_scan'] = {
                'raw_ratio': raw_ratio,
                'raw_dominant': raw_dominant,
            }
            
            # v18.3: Get DER from order_flow_data for LR detection
            # Need to call order_flow again or get from cached data
            # Use oi_previous from line 918
            if 'order_flow_summary' in binance_data:
                of_summary = binance_data['order_flow_summary']
                der = of_summary.get('der', 0)
            else:
                # v18.4: Convert bids/asks to dict format for order_flow
                # Order flow expects {price: volume} format
                def convert_to_dict(levels):
                    if not levels:
                        return {}
                    if isinstance(levels[0], dict):
                        return {float(b['price']): float(b.get('quantity', 0)) for b in levels}
                    else:
                        # List format: [[price, quantity], ...]
                        return {float(b[0]): float(b[1]) for b in levels if len(b) >= 2}
                
                bids_dict = convert_to_dict(bids)
                asks_dict = convert_to_dict(asks)
                
                # v26.1: Guard empty trades for delta/der (Fix 4B)
                if trades and len(trades) > 0:
                    of_data = self.order_flow.get_order_flow_summary(
                        bids=bids_dict, asks=asks_dict, trades=trades,
                        price=self.current_price, open_interest=oi, prev_oi=oi_previous
                    )
                    der = of_data.get('der', 0)
                    binance_data['order_flow_summary'] = of_data
                else:
                    logger.warning("[OrderFlow] No trades data — delta/der will be 0")
                    der = 0
                    binance_data['order_flow_summary'] = {
                        'delta': 0, 'der': 0, 'imbalance': 0, 'imbalance_direction': 'NEUTRAL'
                    }
            
            # v18.3: DER direction for LR detection
            if der > 0.3:
                binance_data['der_direction'] = 'LONG'
            elif der < -0.3:
                binance_data['der_direction'] = 'SHORT'
            else:
                binance_data['der_direction'] = 'NEUTRAL'

                # v27.2: Detect new M5 candle close (store for analyze_and_trade)
            latest_candle_time = candles.index[-1] if len(candles) > 0 else None
            self._new_candle = (latest_candle_time != self._last_candle_time)
            if self._new_candle and latest_candle_time is not None:
                self._last_candle_time = latest_candle_time

            # v27.3: News context for AI
            news_paused, news_event = self.news_filter.is_news_paused()
            if news_paused and news_event:
                event_time = news_event['time']
                minutes_to = int((event_time - datetime.now(timezone.utc)).total_seconds() / 60)
                if minutes_to > 0:
                    binance_data['news_context'] = f"{news_event['title'][:25]} in {minutes_to}min"
                else:
                    binance_data['news_context'] = f"{news_event['title'][:25]} NOW"
            else:
                binance_data['news_context'] = 'none'
            
            # v36.3: Store new_candle flag for analyze_and_trade
            binance_data['new_candle'] = self._new_candle

            # v28.1: AI periodic analysis REMOVED — AI now evaluates per-signal in _send_signal()
            # Keep cached result for dashboard display
            ai_result = getattr(self, '_last_ai_result', None)
            if ai_result:
                binance_data['ai_analysis'] = ai_result
            
            # v18.5: AI Accuracy Evaluation (every 1 hour)
            # ทุก 60 นาที ประเมินความแม่นยำ
            current_time = time.time()
            last_ai_eval = getattr(self, '_last_ai_eval_time', 0)
            
            if current_time - last_ai_eval > 3600:  # 1 hour
                # v18.5: Auto evaluate AI accuracy
                accuracy = await self.ai_analyzer.evaluate_and_log_accuracy(candles, self.current_price)
                if accuracy:
                    logger.info(
                        f"[AI] Accuracy: {accuracy['correct']}/{accuracy['total']} = {accuracy['accuracy']}%"
                    )
                self._last_ai_eval_time = current_time

            # v27.2: Smart Interval — pass new_candle flag
            ipa_iof_sent = await self._run_ipa_iof_analysis(
                candles_m5=candles,
                candles_h1=candles_h1,
                current_price=self.current_price,
                binance_data=binance_data,
                cycle_start_time=cycle_start_time,
                new_candle=self._new_candle,  # v27.2: IPA runs only on candle close
            )

            # Update IOF throttle time
            self._last_iof_run = time.time()
            # v26.0: Track price for next cycle's price_1min_ago
            self._price_1min_ago = self.current_price

            # Log Telemetry - Silenced per user request
            # logger.debug(f"📊 Telemetry: Candles={len(candles)}, Trades={len(trades)}, OI={oi}, Price={self.current_price}")

            # v4.9 M5: Legacy SignalManagerV3 signal generation REMOVED.
            # All signals now come from _run_ipa_iof_analysis() (every 30s).
            
            # === Risk Tier Check REMOVED ===
            # User requested: Only EA handles risk blocking, Python sends all signals
            # allowed, reason = self.risk_manager.check_trading_allowed() if self.risk_manager else (True, "")
            # if not allowed:
            #     logger.warning(f"Trading not allowed (Risk Tier): {reason}")
            #     return
            
            # === v4.9 M5: Trailing (EA handles independently — no Python-side trailing) ===
            # active_signals tracked from IPA/IOF results are forwarded to EA via _send_signal
            # EA manages its own trailing based on mode (IPA=let run, IOF=fast lock)
            
            # === v4.9 M5: News Policy ===
            # EA handles all news filtering and CLOSE_ALL. Python no longer broadcasts news.
            
            # Save and publish indicators for real-time dashboard (every loop)
            indicators_data = self._get_indicators_data(candles, order_book, trades, self.current_price, avg_volume)
            
            from src.execution import webhook_server
            webhook_server._save_indicators_to_file(indicators_data)
            
            from src.execution.signal_publisher import get_signal_sender
            sender = get_signal_sender()
            if sender.zeromq.is_connected():
                sender.zeromq.publish('indicator', indicators_data)
                # logger.info(f"Published real-time indicators: {indicators_data['indicators']['htf_trend']}")
                if self.config.get('debug', False):
                    logger.debug(f"Indicators payload: {indicators_data}")
            
            # v27.1: Heartbeat moved to run() loop — sent BEFORE analyze_and_trade()
            # This prevents EA timeout when analysis takes >30s
            
            # === v4.9 M5: Heartbeat log every 60s ===
            now = datetime.now(timezone.utc)
            if (now - self._last_heartbeat_log).total_seconds() >= 60:
                # v27.0: Use cached regime (single source of truth)
                regime = self._cached_regime
                session = self.session_detector.get_current_session()
                ipa_s = self._last_ipa_result.score if self._last_ipa_result else 0
                iof_s = self._last_iof_result.score if self._last_iof_result else 0
                # logger.info(
#                     f"💓 BTC ${self.current_price:,.0f} | "
#                     f"Session:{session} | "
#                     f"Regime:{regime.regime if regime else 'N/A'} | "
#                     f"IPA:{ipa_s}/12 | IOF:{iof_s}/11"
#                 )
                self._last_heartbeat_log = now
            
            # === v24.0: Push Data to Dashboard State ===
            regime = self._cached_regime  # v27.1: always read from cache (set in _run_ipa_iof_analysis)
            h1_bias_result = getattr(self, '_last_h1_bias_result', None)  # v29.1: for dashboard
            self._update_dashboard_state(
                current_price=self.current_price,
                session=session,
                regime=regime,
                h1_bias_result=h1_bias_result,  # v29.1: for dashboard
                binance_data=binance_data,
                cycle_start_time=cycle_start_time,
                frvp_data=getattr(self, '_last_frvp_data', None),
                ipa_result=self._last_ipa_result,
                iof_result=self._last_iof_result,
                ipaf_result=getattr(self, '_last_ipaf_result', None),
                ioff_result=getattr(self, '_last_ioff_result', None),
                last_signal=getattr(self, '_last_sent_signal', None),
                candles_m5=candles,  # v27.0: pass for pullback computation
                candles_h1=candles_h1
            )

            # Update cache with candles
            for _, row in candles.iterrows():
                self.cache.add_candle(timeframe, row.to_dict())
            
            # Update volume history
            
            # Check for Daily Summary (at 00:00 UTC or day change)
            await self.check_and_send_daily_summary()
            
        except Exception as e:
            logger.error(f"Error in analyze_and_trade: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Disabled Telegram error alerts as per user request
            # if self.telegram:
            #     await self.telegram.send_error_alert(str(e))

    def _update_trailing_stops(self, candles: pd.DataFrame):
        """
        v4.9 M5: Trailing is handled entirely by the EA.
        The EA applies mode-specific trailing (IPA=let run, IOF=fast lock).
        Python no longer manages trailing stops.
        """
        pass  # No-op — EA handles trailing independently
    
    def _update_dashboard_state(self, current_price, session, regime, h1_bias_result, binance_data, cycle_start_time, 
                                frvp_data=None, ipa_result=None, iof_result=None, 
                                ipaf_result=None, ioff_result=None, last_signal=None,
                                candles_m5=None, candles_h1=None):
        """v24.0: Push all bot data to shared dashboard_state in webhook_server."""
        from src.execution.webhook_server import dashboard_state
        
        self.cycle_count += 1
        now = datetime.now(timezone.utc)
        uptime = now - self.start_time
        cycle_time = time.time() - cycle_start_time
        
        dashboard_state.update({
            'price': current_price,
            'session': session,
            'regime': regime.regime if regime else 'N/A',
            'timestamp': now.isoformat(),
            'cycle_time': round(cycle_time, 2),
            'cycle_count': self.cycle_count,
            'bot_uptime': str(uptime).split('.')[0], # 00:00:00 format
            'price_history': getattr(self, '_price_history', []),
        })
        
        # AI Section
        ai_res = binance_data.get('ai_analysis')
        if ai_res:
            dashboard_state['ai'].update({
                'bias': ai_res.get('bias', 'NEUTRAL'),
                'confidence': ai_res.get('confidence', 0),
                'action': ai_res.get('action', 'WAIT'),
                'reason': ai_res.get('reason', ''),
                'key_level': ai_res.get('key_level', 0),
                'last_update': now.isoformat(),
            })
        # v24.1: DO NOT overwrite 'enabled' here - controlled by API /api/ai/toggle only
        # dashboard_state['ai']['enabled'] should remain as set by API
            
        # Market Context
        h1_dist = binance_data.get('h1_ema_dist_pct', 0)
        
        # v25.0: Use pullback from binance_data (same source as terminal display)
        pullback_info = binance_data.get('pullback', {}) or {}
        pullback_status_value = pullback_info.get('status', 'NONE')
        
        wall_scan = binance_data.get('wall_scan', {})
        wall_dom = wall_scan.get('raw_dominant', 'NONE')
        wall_ratio = wall_scan.get('raw_ratio', 1.0)
        
        dashboard_state['market'].update({
            'ema9': binance_data.get('ema9', 0),
            'ema20': binance_data.get('ema20', 0),
            'ema50': binance_data.get('ema50', 0),
            'h1_dist_pct': round(h1_dist, 2),
            'pullback_status': pullback_status_value,
            'wall_info': f"{wall_dom} {wall_ratio:.1f}x",
            # v29.1: Fields for trade log analysis
            'm5_state': binance_data.get('m5_state', 'RANGING'),
            'regime': regime.regime if regime else None,
            'h1_bias_level': h1_bias_result.bias_level if h1_bias_result else None,
            # v30.7: M5 context fields
            'm5_efficiency': binance_data.get('m5_efficiency'),
            'm5_ema_position': binance_data.get('m5_ema_position'),
            'atr_m5': binance_data.get('atr_m5'),
        })

        

        # v29.1: Add ADX/DI + H1 bias to dashboard_state (for Frontend SidePanel)
        if regime:
            dashboard_state['adx_h1'] = round(regime.adx_h1, 1) if regime.adx_h1 else None
            dashboard_state['plus_di'] = round(regime.plus_di, 1) if regime.plus_di else None
            dashboard_state['minus_di'] = round(regime.minus_di, 1) if regime.minus_di else None
            dashboard_state['di_spread'] = round(regime.di_spread, 1) if regime.di_spread else None
        
        # v29.1: Add H1 bias from h1_bias_result (matching Terminal Display)
        if h1_bias_result:
            dashboard_state['ema_trend'] = h1_bias_result.bias  # BULLISH/BEARISH/NEUTRAL
            dashboard_state['bias_level'] = h1_bias_result.bias_level
        
        wall_scan = binance_data.get('wall_scan', {})
        wall_dom = wall_scan.get('raw_dominant', 'NONE')
        wall_ratio = wall_scan.get('raw_ratio', 1.0)
        
        dashboard_state['market'].update({
            'ema9': binance_data.get('ema9', 0),
            'ema20': binance_data.get('ema20', 0),
            'ema50': binance_data.get('ema50', 0),
            'h1_dist_pct': round(h1_dist, 2),
            'pullback_status': pullback_status_value,
            'wall_info': f"{wall_dom} {wall_ratio:.1f}x",
            'm5_state': binance_data.get('m5_state', 'RANGING'),
            'regime': regime.regime if regime else None,
            'h1_bias_level': h1_bias_result.bias_level if h1_bias_result else None,
        })

        # Bias Layers (Gate 1) — v25.0: include L0-L3 from IPA h1_result
        h1r = getattr(self.ipa_analyzer, '_last_h1_result', None) or {}
        dashboard_state['bias_layers'].update({
            'lc': binance_data.get('h1_candle_bias', 'NEUTRAL'),
            'lr': binance_data.get('lr_bias', 'NEUTRAL'),
            'lr_count': binance_data.get('lr_count', 0),
            'l0': h1r.get('l0_direction', 'NEUTRAL'),
            'l1': h1r.get('l1_direction', 'NEUTRAL'),
            'l2': h1r.get('l2_direction', 'NEUTRAL'),
            'l3': h1r.get('l3_direction', 'NEUTRAL'),
        })
        
        # Mode Results
        # IPA
        if ipa_result:
            dashboard_state['modes']['IPA'].update({
                'active': True,
                'score': ipa_result.score,
                'direction': ipa_result.direction,
            })
        # IOF
        if iof_result:
            dashboard_state['modes']['IOF'].update({
                'active': True,
                'score': iof_result.score,
                'direction': iof_result.direction,
            })
        # v24.1: IPAF
        if ipaf_result:
            dashboard_state['modes']['IPAF'].update({
                'active': True,
                'score': ipaf_result.score,
                'direction': ipaf_result.direction,
            })
        # v24.1: IOFF
        if ioff_result:
            dashboard_state['modes']['IOFF'].update({
                'active': True,
                'score': ioff_result.score,
                'direction': ioff_result.direction,
            })
        
        # v24.1: Last Signal Telemetry
        if last_signal:
            dashboard_state['last_signal'] = {
                'signal_id': last_signal.get('signal_id', ''),
                'mode': last_signal.get('mode', ''),
                'direction': last_signal.get('direction', ''),
                'entry_price': last_signal.get('entry_price', 0),
                'stop_loss': last_signal.get('stop_loss', 0),
                'take_profit': last_signal.get('take_profit', 0),
                'score': last_signal.get('score', 0),
                'rr': last_signal.get('rr', 3.0),
                'timestamp': now.isoformat(),
            }
        
        # MLVP — v25.0: guard against None in composite
        if frvp_data:
            # v50.8: Use swing_anchored for display (matches TradingView)
            swing_vp = frvp_data.get('layers', {}).get('swing_anchored', {}) or {}
            dashboard_state['mlvp'].update({
                'composite_poc': round(swing_vp.get('poc') or 0, 1),
                'composite_vah': round(swing_vp.get('vah') or 0, 1),
                'composite_val': round(swing_vp.get('val') or 0, 1),
                'current_session': session,
                'confluence_zones': frvp_data.get('confluence_zones', [])[:5],
            })
            
        # AI Stats
        if hasattr(self, 'ai_analyzer'):
            stats = self.ai_analyzer.get_trade_summary()
            if stats:
                dashboard_state['ai_stats'].update(stats)
        
        # v25.0: Order Flow Data — ดึงจาก order_flow_summary + instance vars
        # v30.7: Added der_direction, der_persistence, der_sustainability
        of_summary = binance_data.get('order_flow_summary', {})
        dashboard_state['order_flow'].update({
            'delta': of_summary.get('delta', 0),
            'volume_24h': of_summary.get('total_volume', 0),
            'oi': getattr(self, '_last_oi', 0),
            'oi_change': of_summary.get('oi_change_pct', 0),
            'liquidations': 0,
            'der': of_summary.get('der', 0),
            'funding_rate': binance_data.get('funding_rate', 0),
            # v30.7: DER stability fields
            'der_direction': binance_data.get('der_direction', 'NEUTRAL'),
            'der_persistence': binance_data.get('der_persistence', 0),
            'der_sustainability': binance_data.get('der_sustainability', 'NEUTRAL'),
        })
                
        # Account & Positions (updated asynchronously in run loop)
        if self.risk_manager:
            balance = getattr(self.risk_manager, 'balance', 0)
            equity = getattr(self.risk_manager, 'equity', 0)
            dd_pct = ((balance - equity) / balance * 100) if balance > 0 else 0
            dashboard_state['account'].update({
                'balance': balance,
                'equity': equity,
                'profit': getattr(self.risk_manager, 'daily_pnl', 0),
                'leverage': float(os.environ.get('ACCOUNT_LEVERAGE', '10')),
                'drawdown_pct': round(dd_pct, 2),
            })
            # Positions
            positions = []
            if hasattr(self.risk_manager, 'positions_state'):
                for pos in self.risk_manager.positions_state:
                    positions.append({
                        'ticket': pos.get('ticket'),
                        'symbol': pos.get('symbol'),
                        'type': 'BUY' if pos.get('type') == 0 else 'SELL',
                        'volume': pos.get('volume'),
                        'price_open': pos.get('price_open'),
                        'sl': pos.get('sl'),
                        'tp': pos.get('tp'),
                        'profit': pos.get('profit'),
                    })
            dashboard_state['positions'] = positions

    def stop(self):
        """Stop the bot."""
        self.is_running = False
    
    async def run(self):
        """Main run loop."""
        logger.info("Starting BTC SF Bot...")
        
        if not self.initialize():
            logger.error("Failed to initialize bot")
            return
        
        # v50.8: Old reconcile removed — replaced by _reconcile_with_mt5() in __init__
        # OPENED trades are NOT marked STALE on startup — wait for EA confirm
        
        # v19.0: Register webhook callbacks
        if hasattr(self, 'ai_analyzer'):
            # set_confirmation_callback(self.on_ea_confirmation) # DISABLED
            logger.info("[Webhook] EA confirmation callback registered")
        
        # v19.0: Start webhook server in background
        try:
            start_server_background(host="0.0.0.0", port=8000)
            logger.info("[Webhook] Server started on port 8000")
        except Exception as e:
            logger.warning(f"[Webhook] Server start failed: {e}")
        
        self.is_running = True
        self.last_report_date = None
        
        # Start WebSocket for real-time data
        if self.ws_handler:
            logger.info("Starting Binance WebSocket background task...")
            self.ws_task = asyncio.create_task(self.ws_handler.start())
        
        # Get analysis interval
        analysis_interval = 5  # seconds
        
        # Initialize MT5 Data Subscriber
        from src.execution.signal_publisher import get_data_subscriber
        subscriber = get_data_subscriber()
        
        # v19.0: Track last cleanup time
        last_cleanup_time = 0
        
        try:
            while self.is_running:
                # 1. Process ALL pending data from MT5 (Account/Position Info)
                # Draining the queue ensures we have the latest balance before analysis
                # v30.5: Increased timeout from 1ms to 100ms to ensure messages are received
                while True:
                    account_msg = subscriber.receive(timeout_ms=100)
                    if not account_msg:
                        break
                    
                    topic, data = account_msg
                    # v30.5: Debug logging for received messages
                    logger.debug(f"[ZMQ] Received: {topic}")
                    
                    if topic == 'account_info':
                        if self.risk_manager:
                            self.risk_manager.update_account_state(data)
                        # logger.debug(f"Account state updated: Equity={data.get('equity')}") # Silenced per user request
                    elif topic == 'position_info':
                        if self.risk_manager:
                            self.risk_manager.update_positions_state(data)
                        # v4.9 M5: No Python-side signal tracking needed.
                        # EA handles all position/trailing management independently.
                    elif topic == 'trade_confirm':
                        # v23.0: EA sent trade confirmation (OPENED/TP/SL/CLOSED)
                        logger.info(f"[ZMQ] Trade Confirm: {data.get('signal_id')} -> {data.get('status')}")
                        await self.on_ea_confirmation(data)
                
                # v26.1: Check for file-based trade confirm fallback
                confirm_file = Path("C:/MetaTrader 5 - Account 1/MQL5/Files/trade_confirm.json")
                if confirm_file.exists():
                    try:
                        import json as json_module
                        with open(confirm_file, 'r') as f:
                            data = json_module.load(f)
                        # Delete file after reading
                        confirm_file.unlink()
                        # Process confirmation
                        if data:
                            logger.info(f"[File] Trade Confirm received: {data.get('signal_id')} -> {data.get('status')}")
                            await self.on_ea_confirmation(data)
                    except Exception as e:
                        logger.warning(f"[File] Trade Confirm read error: {e}")
                
                # v27.1: Send heartbeat BEFORE analysis to prevent EA timeout
                # analyze_and_trade() can take 10-30s → heartbeat inside it is too late
                from src.execution.signal_publisher import get_signal_sender
                sender = get_signal_sender()
                if sender.zeromq.is_connected():
                    sender.zeromq.publish('heartbeat', {
                        'status': 'OK',
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'interval': 5
                    })

                # 2. Main Analysis Loop
                await self.analyze_and_trade()
                
                # v19.0: Cleanup stale signals every 1 minute
                current_time = time.time()
                if current_time - last_cleanup_time > 30:
                    if hasattr(self, 'ai_analyzer'):
                        self.ai_analyzer.cleanup_stale_signals(timeout_seconds=30)
                    last_cleanup_time = current_time
                
                await self._check_daily_report()
            
                # Wait
                await asyncio.sleep(analysis_interval)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            self.is_running = False
            # The shutdown is now called by the caller of run() or here if we want it synchronous
            # But let's keep it in finally for safety if run() was awaited directly
            await self.shutdown()
    
    # v23.1: on_ea_confirmation ถูกย้ายไป line 870 (v19.0) — ไม่ต้อง duplicate

    def _reconcile_with_mt5(self):
        """v50.8: Sync trades DB with actual MT5 positions on startup.

        Rules:
        - SIGNAL_SENT/SENT + EA ไม่มี → EA_SKIPPED (signal ไม่ถูกเปิด)
        - SIGNAL_SENT/SENT + EA มี → OPENED (sync status)
        - OPENED + EA มี → ไม่แก้ (ถูกต้องแล้ว)
        - OPENED + EA ไม่มี → ไม่แก้ (รอ EA confirm ปิดเอง — ห้าม STALE_CLEANUP ทันที)
        """
        try:
            if not mt5.initialize():
                logger.warning(f"[Startup] MT5 init failed: {mt5.last_error()}")
                return

            # 1. Get actual EA positions
            ea_positions = mt5.positions_get(symbol="BTCUSDm") or []
            ea_signal_ids = set()
            for p in ea_positions:
                if p.comment:
                    ea_signal_ids.add(p.comment)

            # 2. Get DB trades with active status
            active_trades = []
            with self.db._conn() as conn:
                rows = conn.execute(
                    "SELECT signal_id, status FROM trades WHERE status IN ('SIGNAL_SENT','SENT','OPENED')"
                ).fetchall()
                active_trades = [(r[0], r[1]) for r in rows]

            synced = {'skipped': 0, 'opened': 0, 'closed': 0}

            for signal_id, status in active_trades:
                if signal_id in ea_signal_ids:
                    # EA has this position open
                    if status in ('SIGNAL_SENT', 'SENT'):
                        self.db.update_trade(signal_id, {'status': 'OPENED'})
                        synced['opened'] += 1
                    # status == 'OPENED' → already correct
                else:
                    # EA does NOT have this position
                    if status in ('SIGNAL_SENT', 'SENT'):
                        self.db.update_trade(signal_id, {
                            'status': 'EA_SKIPPED',
                            'skip_reason': 'Not found in MT5 on startup'
                        })
                        synced['skipped'] += 1
                    elif status == 'OPENED':
                        # EA ไม่มี position นี้ → หมายความว่าถูกปิดไปแล้ว (EA close ระหว่าง restart)
                        self.db.update_trade(signal_id, {
                            'status': 'EA_CLOSED',
                            'exit_reason': 'Closed during bot restart (not in EA)'
                        })
                        synced['closed'] += 1
                        logger.info(f"[Startup] EA_CLOSED (was OPENED): {signal_id}")

            mt5.shutdown()
            logger.info(
                f"[Startup] MT5 Reconciled: {len(ea_positions)} EA positions, "
                f"{synced['skipped']} SKIPPED, {synced['opened']} synced OPENED, {synced['closed']} EA_CLOSED"
            )
        except Exception as e:
            logger.warning(f"[Startup] MT5 reconcile error: {e}")

    async def _check_daily_report(self):
        """
        Section 51: Daily Report Scheduler.
        
        Checks if it's time to send a daily summary report (00:05 UTC).
        This is a lightweight check - actual report generation is handled by AIReportGenerator.
        """
        
        now = datetime.now(timezone.utc)
        
        # Check if it's 00:05 UTC (5 minutes past midnight)
        if now.hour == 0 and now.minute < 10:
            # Check if we haven't sent today's report yet
            last_report_date = getattr(self, '_last_report_date', None)
            today_date = now.date()
            
            if last_report_date != today_date:
                logger.info("Daily Report Time: Preparing daily summary...")
                
                # Mark today's date as reported
                self._last_report_date = today_date
                
                # TODO: Trigger actual report generation
                # For now, just log that report time has arrived
                # The AIReportGenerator can be triggered here when ready
                self._log_daily_report_trigger()
    
    def _log_daily_report_trigger(self):
        """Placeholder for daily report generation logic."""
        # TODO: Implement actual report generation using AIReportGenerator
        logger.info("Daily report trigger fired - report generation not yet implemented")
    
    async def shutdown(self):
        """Shutdown bot."""
        if self._is_shutting_down:
            return
            
        self._is_shutting_down = True
        logger.info("Shutting down bot gracefully...")
        
        self.is_running = False
        
        if self.ws_task:
            logger.info("Cancelling WebSocket task...")
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
        
        if self.ws_handler:
            await self.ws_handler.disconnect()
        
        # v6.0: Close Binance fetcher
        if self.binance_fetcher:
            await self.binance_fetcher.close()
        
        logger.info("Bot shutdown complete")

    async def check_and_send_daily_summary(self):
        """Check if it's a new day and send the daily summary report."""
        now = datetime.now(timezone.utc)
        today = now.date()
        
        # If day has changed, send summary for the previous day
        if today != self.last_summary_date:
            logger.info(f"Day changed from {self.last_summary_date} to {today}. Sending summary...")
            
            if self.telegram and self.risk_manager and self.risk_manager.position_sizer:
                stats = self.risk_manager.position_sizer.get_stats()
                trades = self.risk_manager.position_sizer.trades_today
                
                if trades:
                    wins = len([t for t in trades if t['pnl'] > 0])
                    losses = len([t for t in trades if t['pnl'] < 0])
                    total_pnl = sum(t['pnl'] for t in trades)
                    # We don't track R units accurately here yet, so we use 0 or estimate
                    # but let's send what we have
                    total_pnl_r = sum(t.get('pnl_r', 0) for t in trades) 

                    await self.telegram.send_daily_summary(
                        total_trades=len(trades),
                        wins=wins,
                        losses=losses,
                        profit=total_pnl,
                        profit_r=total_pnl_r,
                        trades=trades
                    )
                
                # Reset daily tracking for the new day
                if self.risk_manager and self.risk_manager.position_sizer:
                    self.risk_manager.position_sizer.reset_daily()
                
            self.last_summary_date = today


async def main():
    """Main entry point."""
    # Create bot instance
    bot = BTCSFBot()
    
    # Run bot
    try:
        # Wrap in a task to allow better signal handling
        bot_task = asyncio.create_task(bot.run())
        
        # Monitor signals using wait
        def handle_exit():
            bot.stop()
            
        # For Windows compatibility, we use traditional signal
        signal.signal(signal.SIGINT, lambda s, f: handle_exit())
        signal.signal(signal.SIGTERM, lambda s, f: handle_exit())
        
        await bot_task
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Stop signal received")
        bot.stop()
        await bot.shutdown()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    # Run the main function
    asyncio.run(main())
