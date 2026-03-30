"""
Signal Manager Module v3.2
2-Phase Signal Generation: Structure Validation + Entry Setup
Includes POC, Liquidity Wall, HTF MSS Sync, and Dynamic Trailing SL
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import hashlib
import json
import os
from pathlib import Path

from ..utils.logger import get_logger
from ..analysis.order_flow import OrderFlowAnalyzer
from ..analysis.volume_profile import VolumeProfileAnalyzer
from ..analysis.ict import ICTAnalyzer
from ..analysis.structure_validator import StructureValidator
from ..analysis.liquidity_wall_analyzer import LiquidityWallAnalyzer
from ..analysis.htf_mss_analyzer import HTFMSSAnalyzer
from ..analysis.news_filter import NewsFilter
from ..signals.bot_state import BotState
from ..signals.entry_scanner import EntrySetupScanner
# v6.0: SmartFlowManager DISABLED - signals now come from IPA/IOF analyzers only
# from ..signals.smart_flow_manager import SmartFlowManager
from ..risk.trailing_stop_manager import TrailingStopManager
from ..analysis.pattern_tracker import PatternPerformanceTracker
from ..data.trade_storage import TradeStorage
from ..enums import TrendState, BOSStatus, EntryType
from .logistic_regression_model import LogisticRegressionModel

logger = get_logger(__name__)


def abbreviate_reason(reason: str) -> str:
    """
    Abbreviate SMC/ICT terms based on Technical Documentation standard.
    """
    if not reason:
        return ""
    
    mapping = {
        "BULLISH": "B",
        "BEARISH": "S",
        "CHANGE_OF_CHARACTER": "CHoCH",
        "MARKET_STRUCTURE_SHIFT": "MSS",
        "BREAK_OF_STRUCTURE": "BOS",
        "ORDERBLOCK": "OB",
        "ORDER_BLOCK": "OB",
        "FAIR_VALUE_GAP": "FVG",
        "FAIRVALUEGAP": "FVG",
        "LIQUIDITY_SWEEP": "LQ_SWP",
        "LIQUIDITY": "LQ",
        "IMBALANCE": "IB",
        "PREMIUM": "PREM",
        "DISCOUNT": "DISC",
        "DISPLACEMENT": "DSPL",
        "CONFLUENCE": "CONF",
        "STRUCTURE": "STR",
        "CONTEXT": "CTX",
        "ORDERFLOW": "OF",
        "ORDER_FLOW": "OF"
    }
    
    result = reason.upper()
    for full, short in mapping.items():
        result = result.replace(full, short)
    
    return result[:31]


class Signal:
    """Trading signal class."""
    
    def __init__(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        lot_size: float = 0,
        tp1: float = 0,
        tp2: float = 0,
        tp3: float = 0,
        confidence: int = 0,
        reason: str = "",
        metadata: Optional[Dict] = None
    ):
        self.direction = direction
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.lot_size = lot_size
        self.tp1 = tp1 if tp1 != 0 else take_profit
        self.tp2 = tp2
        self.tp3 = tp3
        self.confidence = confidence
        self.reason = reason
        self.short_reason = abbreviate_reason(reason) if not metadata or 'short_reason' not in metadata else metadata['short_reason']
        self.metadata = metadata or {}
        self.timestamp = datetime.now(timezone.utc)
        self.status = "PENDING"
        self.signal_id = self._generate_signal_id()
    
    def _generate_signal_id(self) -> str:
        """Generate unique signal ID with null safety."""
        # Null safety: handle None values
        direction = self.direction if self.direction else 'N'
        entry_price = self.entry_price if self.entry_price else 0
        stop_loss = self.stop_loss if self.stop_loss else 0
        take_profit = self.take_profit if self.take_profit else 0
        
        components = [
            direction,
            f"{entry_price:.2f}",
            f"{stop_loss:.2f}",
            f"{take_profit:.2f}",
            self.timestamp.strftime('%Y%m%d%H%M'),
            str(self.metadata.get('score', 0)),
            self.metadata.get('setup_id', '')
        ]
        
        hash_input = "|".join(components)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16].upper()
    
    def to_dict(self) -> Dict:
        """
        Convert signal to dictionary for MT5.
        
        Architecture Plan 2.2 & TASK 2: Includes institutional_grade, confluence_score,
        required_rr, be_trigger_pct, and trailing_config for EA pattern-specific risk management.
        """
        metadata = self.metadata or {}
        
        # === Architecture Plan 2.2 & TASK 2: Dynamic required_rr from actual TP/SL ===
        # required_rr = actual_rr * 0.95 (5% buffer สำหรับ spread/slippage)
        # Floor = 1.0 เสมอ — ไม่ส่ง Signal ที่ RR < 1.0
        pattern_type = metadata.get('pattern_type', 'DA')
        if (self.stop_loss and self.take_profit and self.entry_price
                and abs(self.entry_price - self.stop_loss) > 0):
            sl_dist_actual = abs(self.entry_price - self.stop_loss)
            tp_dist_actual = abs(self.take_profit - self.entry_price)
            actual_rr = tp_dist_actual / sl_dist_actual
            required_rr = round(max(actual_rr * 0.95, 1.0), 2)
        else:
            # Fallback เฉพาะกรณี SL/TP ไม่มีค่า (ไม่ควรเกิดขึ้น)
            required_rr = 1.2
        
        # Architecture Plan Section 3.2: Breakeven trigger
        institutional_grade = metadata.get('institutional_grade', False)
        be_trigger_pct = 0.2 if institutional_grade else 0.4
        
        # Architecture Plan Section 3.2: Trailing config
        trailing_config = metadata.get('trailing_config', {})
        trailing_lock_pct = trailing_config.get('lock_pct', 0.75)
        trailing_trigger_pct = trailing_config.get('trigger_profit_pct', 1.5)
        trailing_mode = trailing_config.get('mode', 'BALANCED')
        
        # Only include essential fields that MT5/JAson can parse
        # Avoid: nested objects, booleans, null values, complex timestamps
        return {
            'signal_id': self.signal_id,
            'direction': self.direction,
            'entry_price': round(self.entry_price, 2),
            'stop_loss': round(self.stop_loss, 2),
            'take_profit': round(self.take_profit, 2),
            # lot_size removed - EA calculates independently
            'tp1': round(self.tp1, 2) if self.tp1 else 0,
            'tp2': round(self.tp2, 2) if self.tp2 else 0,
            'tp3': round(self.tp3, 2) if self.tp3 else 0,
            'confidence': self.confidence,
            'reason': self.reason or '',
            'short_reason': self.short_reason or '',
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'status': self.status,
            'score': metadata.get('score', 0),
            'entry_type': metadata.get('entry_type', 'UNKNOWN'),
            'pattern_type': pattern_type,
            # Architecture Plan 2.2 & TASK 2: Institutional Grade & RR Signal Metadata
            'institutional_grade': institutional_grade,
            'confluence_score': metadata.get('confluence_score', 0),
            'required_rr': required_rr,
            # Architecture Plan Section 3.2: Dynamic Institutional Risk Management
            'be_trigger_pct': be_trigger_pct,                    # 0.2% for institutional, 0.4% for normal
            'trailing_lock_pct': trailing_lock_pct,            # Lock percentage (0.70-0.80)
            'trailing_trigger_pct': trailing_trigger_pct,      # Trigger profit % (1.0-1.8)
            'trailing_mode': trailing_mode,                      # TRAILING_LONG, QUICK_EXIT, BALANCED
            'setup_id': metadata.get('setup_id', ''),
            'tp_multiplier': metadata.get('tp_multiplier', 1.0),
            'htf_trend': metadata.get('htf_trend', 'RANGE'),
            'structure': metadata.get('structure', 'RANGE')
        }
    
    def __repr__(self):
        return f"Signal({self.direction} @ {self.entry_price}, SL: {self.stop_loss}, TP: {self.take_profit}, ID: {self.signal_id[:8]})"


class SignalManager:
    """Signal generation and management with 2-Phase flow."""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
        # Initialize analyzers
        self.order_flow = OrderFlowAnalyzer(self.config.get('order_flow', {}))
        self.volume_profile = VolumeProfileAnalyzer(self.config.get('volume_profile', {}))
        self.ict = ICTAnalyzer(self.config.get('ict', {}))
        self.news_filter = NewsFilter(self.config)
        
        # v3.0: New components
        self.structure_validator = StructureValidator(self.config.get('structure_validation', {}))
        self.bot_state = BotState()
        self.bot_state.load_state() # Load previous trend/structure
        self.entry_scanner = EntrySetupScanner(self.config.get('entry_scanner', {}))
        
        # v3.1: Liquidity Wall Analyzer
        self.liquidity_wall_analyzer = LiquidityWallAnalyzer(self.config.get('liquidity_wall', {}))
        
        # v3.2: HTF MSS Analyzer
        self.htf_mss_analyzer = HTFMSSAnalyzer(self.config.get('htf_mss', {}))
        
        # v3.2: Trailing Stop Manager
        self.trailing_stop_manager = TrailingStopManager(self.config.get('trailing_stop', {}))
        
        # v6.0: SmartFlowManager DISABLED
        # self.smart_flow_manager = SmartFlowManager(self.config)
        
        # New: Pattern Performance Tracker & Trade Storage
        self.pattern_tracker = PatternPerformanceTracker()
        self.trade_storage = TradeStorage()
        
        # v3.3: Logistic Regression Model for Win Probability
        self.lr_model = LogisticRegressionModel(self.config)
        
        # Data tracking
        self.cvd_series: List[float] = []
        self.oi_before_break = 0.0
        self.cvd_at_swing: Dict[str, float] = {}
        self.trades_in_candle: List[Dict] = []
        
        # POC tracking
        self.last_poc_data: Optional[Dict] = None
        self.last_liquidity_wall_data: Optional[Dict] = None
        self.last_htf_analysis: Optional[Dict] = None
        
        # Settings
        self.min_score = self.config.get('min_score', 6)
        self.signal_cooldown = self.config.get('signal_cooldown_seconds', 60)  # Default 60s for M5
        # Initialize to past time so cooldown works on first run
        self.last_signal_time: Optional[datetime] = datetime.now(timezone.utc) - timedelta(seconds=self.signal_cooldown)
        self.active_setups = {}
        self.last_entry_price = 0
        self.last_pattern_prices: Dict[str, float] = {}
        self.min_price_distance = self.config.get('min_price_distance', 0)  # Disabled for M5 scalping
        
        # Zone tracking to prevent duplicate entries in same zone (Structure-based)
        self.active_zones = {}  # Dict of zone_id -> {zone_data, status, entry_count}
        self.last_zone_id = None
        
        # v3.2: Scoring Cache for Dashboard
        self.last_validation_score = 0
        self.last_entry_score = 0
        
        # Feature flags
        self.use_v3_flow = self.config.get('structure_validation', {}).get('enabled', True)
        self.use_poc_check = self.config.get('structure_validation', {}).get('use_poc_check', True)
        self.use_liquidity_walls = self.config.get('structure_validation', {}).get('use_liquidity_walls', True)
        self.use_htf_sync = self.config.get('htf_mss', {}).get('enabled', True)
        self.use_trailing_stop = self.config.get('trailing_stop', {}).get('enabled', True)
        
        # Log throttling
        self.last_block_log_time = {} # Dict of (reason, key) -> datetime
        
        # Load state on init
        self.load_state()
    
    def save_state(self, filepath: str = "data/signal_state.json") -> bool:
        """Save signal management state to JSON file."""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            # Convert datetime objects in active_setups to strings
            serializable_setups = {
                k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in self.active_setups.items()
            }
            
            # Convert datetime objects in last_pattern_prices
            serializable_prices = {}
            for k, v in self.last_pattern_prices.items():
                if isinstance(v, dict):
                    serializable_prices[k] = {
                        'price': v['price'],
                        'time': v['time'].isoformat() if isinstance(v['time'], datetime) else v['time']
                    }
                else:
                    serializable_prices[k] = v
                    
            state = {
                'active_setups': serializable_setups,
                'last_pattern_prices': serializable_prices,
                'last_signal_time': self.last_signal_time.isoformat() if self.last_signal_time else None,
                'last_entry_price': self.last_entry_price
            }
            
            with open(filepath, 'w') as f:
                json.dump(state, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving SignalManager state: {e}")
            return False

    def load_state(self, filepath: str = "data/signal_state.json") -> bool:
        """Load signal management state from JSON file."""
        if not os.path.exists(filepath):
            return False
            
        try:
            with open(filepath, 'r') as f:
                state = json.load(f)
            
            # Convert strings back to datetime objects
            loaded_setups = state.get('active_setups', {})
            for k, v in loaded_setups.items():
                if isinstance(v, str):
                    self.active_setups[k] = datetime.fromisoformat(v)
            
            # Format: {pattern_type: {'price': float, 'time': datetime}}
            loaded_prices = state.get('last_pattern_prices', {})
            self.last_pattern_prices = {}
            for k, v in loaded_prices.items():
                if isinstance(v, dict) and 'price' in v and 'time' in v:
                    self.last_pattern_prices[k] = {
                        'price': v['price'],
                        'time': datetime.fromisoformat(v['time'])
                    }
                elif isinstance(v, (int, float)): # Legacy format
                    self.last_pattern_prices[k] = {
                        'price': float(v),
                        'time': datetime.now(timezone.utc) - timedelta(minutes=60) # Assume old
                    }
            
            last_signal_time_str = state.get('last_signal_time')
            if last_signal_time_str:
                self.last_signal_time = datetime.fromisoformat(last_signal_time_str)
            
            self.last_entry_price = state.get('last_entry_price', 0)
            

            return True
        except Exception as e:
            logger.error(f"Error loading SignalManager state: {e}")
            return False

    def on_new_candle(self):
        """Called when new candle starts - reset candle-level data."""
        self.trades_in_candle = []
    
    def on_trade(self, trade: Dict):
        """Called on each trade - accumulate for displacement calculation."""
        self.trades_in_candle.append(trade)
        
        # Update CVD series
        if len(self.trades_in_candle) >= 50:
            delta = sum(
                t['volume'] if not t.get('is_buyer_maker', True) else -t['volume']
                for t in self.trades_in_candle[-50:]
            )
            if self.cvd_series:
                self.cvd_series.append(self.cvd_series[-1] + delta)
            else:
                self.cvd_series.append(delta)
            
            # Keep only last 200 values
            if len(self.cvd_series) > 200:
                self.cvd_series = self.cvd_series[-200:]
    
    def check_structure_break(
        self,
        candles: pd.DataFrame,
        current_price: float,
        oi: float,
        order_book: Dict = None,
        htf_trend: str = 'RANGE'
    ) -> Optional[Dict]:
        """
        Check for structure break and validate with POC and Liquidity Wall data.
        Called on every tick.
        
        Args:
            candles: OHLCV DataFrame
            current_price: Current price
            oi: Open Interest
            order_book: Order book data (for liquidity wall analysis)
        
        Returns validation result if break detected, None otherwise.
        """
        if candles.empty or len(candles) < 10:
            return None
        
        ict_summary = self.ict.get_ict_summary(candles, current_price)
        structure = ict_summary.get('structure', {})
        
        last_high = structure.get('last_high', 0)
        last_low = structure.get('last_low', float('inf'))
        
        # logger.debug(f"🔍 Structure: High={last_high:.2f}, Low={last_low:.2f}, Price={current_price:.2f}") # Silenced per user request
        
        # Get POC data for validation
        poc_data = None
        if self.use_poc_check:
            vp_summary = self.volume_profile.get_volume_profile_summary(candles, current_price)
            if vp_summary:
                poc_data = {
                    'poc': vp_summary.get('poc', 0),
                    'vah': vp_summary.get('vah', 0),
                    'val': vp_summary.get('val', 0)
                }
                self.last_poc_data = poc_data
        
        # Get Liquidity Wall data for validation
        liquidity_wall_data = None
        if self.use_liquidity_walls and order_book:
            liquidity_wall_data = self.liquidity_wall_analyzer.analyze(order_book, current_price)
            self.last_liquidity_wall_data = liquidity_wall_data
        
        validation_result = None
        
        # Check Bullish Break
        if (current_price > last_high and 
            last_high > 0 and
            self.bot_state.last_confirmed_high < last_high):
            
            # Store OI before validation
            if self.oi_before_break == 0:
                self.oi_before_break = oi
            
            validation_result = self.structure_validator.validate_bos(
                direction='BULLISH',
                swing_level=last_high,
                candles=candles,
                cvd_series=self.cvd_series,
                oi_current=oi,
                oi_before=self.oi_before_break,
                trades=self.trades_in_candle,
                cvd_at_swing=self.cvd_at_swing.get('high'),
                poc_data=poc_data,
                liquidity_wall_data=liquidity_wall_data,
                htf_trend=htf_trend,
                analysis_data=ict_summary  # MED-1 FIX: Pass ICT analysis for IDM_CONFIRMED
            )
            
            self._process_validation_result(validation_result, last_high, 'BULLISH')
        
        # Check Bearish Break
        elif (current_price < last_low and
              last_low < float('inf') and
              self.bot_state.last_confirmed_low > last_low):
            
            if self.oi_before_break == 0:
                self.oi_before_break = oi
            
            validation_result = self.structure_validator.validate_bos(
                direction='BEARISH',
                swing_level=last_low,
                candles=candles,
                cvd_series=self.cvd_series,
                oi_current=oi,
                oi_before=self.oi_before_break,
                trades=self.trades_in_candle,
                cvd_at_swing=self.cvd_at_swing.get('low'),
                poc_data=poc_data,
                liquidity_wall_data=liquidity_wall_data,
                htf_trend=htf_trend,
                analysis_data=ict_summary  # MED-1 FIX: Pass ICT analysis for IDM_CONFIRMED
            )
            
            self._process_validation_result(validation_result, last_low, 'BEARISH')
        
        # Update CVD at swing points for future reference
        if structure.get('is_new_swing_high') and self.cvd_series:
            self.cvd_at_swing['high'] = self.cvd_series[-1]
        if structure.get('is_new_swing_low') and self.cvd_series:
            self.cvd_at_swing['low'] = self.cvd_series[-1]
        
        # Reset OI tracker only after successful validation
        if validation_result:
            status = validation_result.get('status')
            if status == BOSStatus.CONFIRMED:
                self.oi_before_break = 0
        
        return validation_result
    
    def _process_validation_result(
        self,
        validation: Dict,
        level: float,
        direction: str
    ):
        """Process structure validation result."""
        status = validation.get('status')
        score = validation.get('score', 0)
        
        # v3.2: Record for dashboard
        self.last_validation_score = score
        
        if score > 0:
            logger.debug(f"🔍 Structure Validation: {direction} {status.name if hasattr(status, 'name') else status} | Score: {score}")
            new_trend = TrendState.BULLISH if direction == 'BULLISH' else TrendState.BEARISH
            self.bot_state.update_trend(new_trend, score, level, direction, status)
        
        elif status == BOSStatus.PENDING:
            self.bot_state.set_pending_bos(validation, level, direction)
        
        elif status == BOSStatus.SWEEP:
            logger.debug(f"Structure break rejected as SWEEP | Score: {score}")
    
    def analyze_market(
        self,
        candles: pd.DataFrame,
        order_book: Dict,
        trades: List[Dict],
        current_price: float
    ) -> Dict:
        """Perform complete market analysis."""
        
        bids = order_book.get('bids', {})
        asks = order_book.get('asks', {})
        oi = order_book.get('open_interest')
        prev_oi = order_book.get('prev_oi')
        
        order_flow_summary = self.order_flow.get_order_flow_summary(
            bids, asks, trades, current_price,
            open_interest=oi,
            prev_oi=prev_oi
        )
        
        if len(candles) >= 20:
            prices = candles['close'].tolist()
            
            if 'delta' not in candles.columns:
                deltas = []
                for _, row in candles.iterrows():
                    d = row['volume'] if row['close'] >= row['open'] else -row['volume']
                    deltas.append(d)
                cvd = np.cumsum(deltas).tolist()
            else:
                cvd = candles['delta'].cumsum().tolist()
            
            div_type, div_strength = self.order_flow.detect_cvd_divergence(prices, cvd)
            order_flow_summary['cvd_divergence'] = div_type
            order_flow_summary['cvd_div_strength'] = div_strength
            order_flow_summary['cvd_trend'] = self.order_flow.analyze_cvd_trend(cvd)
        
        # S-01/S-02 FIX: Calculate proper volume_ratio from candles
        if len(candles) >= 20:
            current_vol = candles.iloc[-1]['volume']
            avg_volume = candles['volume'].tail(20).mean()
            volume_ratio = current_vol / avg_volume if avg_volume > 0 else 1.0
            order_flow_summary['volume_ratio'] = volume_ratio
        
        vp_summary = self.volume_profile.get_volume_profile_summary(candles, current_price)
        ict_summary = self.ict.get_ict_summary(candles, current_price, order_flow_summary)
        zone_context = ict_summary.get('zone_context', 'RANGE')
        
        liquidity_walls = None
        if self.use_liquidity_walls and order_book:
            liquidity_walls = self.liquidity_wall_analyzer.analyze(order_book, current_price)
        
        return {
            'order_flow': order_flow_summary,
            'volume_profile': vp_summary,
            'ict': ict_summary,
            'structure': ict_summary.get('structure', {}),
            'zone_context': zone_context,
            'price': current_price,
            'order_book': order_book,
            'liquidity_walls': liquidity_walls,
            'timestamp': datetime.now(timezone.utc)
        }
    
    async def generate_signal(
        self,
        candles: pd.DataFrame,
        order_book: Dict,
        trades: List[Dict],
        current_price: float,
        avg_volume: float = 0,
        risk_reward_ratio: float = 2.0,
        candles_h1: pd.DataFrame = None,
        htf_trend: str = 'RANGE'
    ) -> List[Signal]:
        """
        Generate trading signal using 2-Phase flow (Async).
        
        Phase 1: Structure Validation (check_structure_break)
        Phase 2: Entry Setup Scan
        Phase 3: ISF Mode Scan (Integrated Smart Flow)
        Phase 4: HTF MSS Sync (optional)
        
        Section 42: Multi-Signal Acceptance
        - Returns List[Signal] instead of Optional[Signal]
        - ALL patterns (LP, DB, DA) that pass threshold are returned
        - No arbitrator logic - let EA decide which to execute
        """
        
        # Section 28 & 35.1: High Volatility Filter - Skip entry during extreme volatility
        # Section 35.2: Momentum Exception Rule - Allow institutional grade signals
        # Note: signal_data not available yet at this point, so we check volatility first
        # and apply institutional_grade exception later in the signal generation process
        if candles is not None and len(candles) >= 300:
            # At this point, we don't have signal_data yet, so inst_grade defaults to False
            # The institutional_grade exception will be applied when evaluating specific patterns
            regime_result = self._detect_volatility_regime(candles, institutional_grade=False)
            volatility_regime = regime_result[0]
            atr_percentile = regime_result[2]
            is_blocked = regime_result[3] if len(regime_result) > 3 else False
            
            # Skip entry during EXTREME volatility (top 5% of ATR)
            if is_blocked:
                # Only log every 5 minutes to avoid spam
                cache_key = 'volatility_filter'
                if cache_key not in self.last_block_log_time:
                    self.last_block_log_time[cache_key] = datetime.min.replace(tzinfo=timezone.utc)
                
                last_log = self.last_block_log_time[cache_key]
                if (datetime.now(timezone.utc) - last_log).total_seconds() >= 300:
                    logger.warning(f"⚠️ VOLATILITY FILTER: EXTREME volatility detected (ATR%: {atr_percentile:.0f}%). Skipping entry.")
                    self.last_block_log_time[cache_key] = datetime.now(timezone.utc)
                return []  # Section 42: Return empty list instead of None
        
        # Killzone Check (Optimization: Check time first)
        in_killzone, kz_name = self._check_killzone()
        
        # Cooldown check
        if self.last_signal_time:
            time_since_last = (datetime.now(timezone.utc) - self.last_signal_time).seconds
            if time_since_last < self.signal_cooldown:
                return []  # Section 42: Return empty list instead of None
        
        self._cleanup_old_setups()
        
        # Phase 1: Check Structure Break
        oi = order_book.get('open_interest', 0)
        self.check_structure_break(candles, current_price, oi, order_book, htf_trend)
        
        # Check pending BOS
        if self.bot_state.is_pending_expired():
            self.bot_state.clear_pending_bos()
        
        # Phase 2: Entry Signal setup
        entry_setup = None
        
        # Analyze H1 structure for HTF Sync (Needed for both modes)
        htf_analysis = None
        if self.use_htf_sync and candles_h1 is not None and not candles_h1.empty:
            htf_analysis = self.htf_mss_analyzer.analyze_h1_structure(candles_h1)
            self.last_htf_analysis = htf_analysis

        # V3.2 Performance Fix: Analyze market once and reuse data
        analysis = self.analyze_market(candles, order_book, trades, current_price)
        p1_data = analysis.get('order_flow', {})
        if in_killzone:
            p1_data['in_killzone'] = True

        # Scenario A: Normal Flow (Wait for confirmed Phase 1 BOS)
        normal_flow_score = 0
        if self.bot_state.can_look_for_entry():
            # logger.debug("Generating signal: Normal Flow (Phase 1 Confirmed)") # Silenced per user request
            
            # Scan for normal entry setup
            entry_setup = self.entry_scanner.scan(
                bot_state=self.bot_state,
                candles=candles,
                current_price=current_price,
                analysis=analysis,
                htf_trend=htf_trend
            )
            if entry_setup and entry_setup.get('found'):
                normal_flow_score = entry_setup.get('score', 0)
                
                # H-3: OI=0 Fallback Scoring for P1
                # When OI data is unavailable (0), reduce P1 threshold or boost alternative signals
                # Check if OI is missing/unavailable
                oi_value = p1_data.get('open_interest', 0)
                if oi_value == 0 or oi_value is None:
                    # Boost P1 score when OI data is unavailable to allow Normal Flow to compete with Smart Flow
                    normal_flow_score += 3
                    logger.debug(f"🔍DEBUG P1: OI unavailable, boosting normal_flow_score by 3 (now {normal_flow_score})")
        

        # Scenario C: Smart Flow Mode (รวม Aggressive + ISF) - NEW BRANCH
        # This branch runs in parallel to Scenario A/B
        smart_flow_signals = []
        if self.config.get('smart_flow', {}).get('enabled', True):
            smart_flow_signals = await self.smart_flow_manager.scan_patterns(
                candles=candles,
                current_price=current_price,
                p1_data=p1_data,
                htf_data=self.last_htf_analysis,
                phase1_score=self.last_validation_score,
                liquidity_wall_data=self.last_liquidity_wall_data
            )
            
        # === Section 42: Multi-Signal Acceptance ===
        # Return ALL signals that pass threshold, not just the best one
        # No arbitrator logic - let EA decide which to execute
        all_entry_setups = []
        
        # Add Normal Flow signal if it passed
        if entry_setup and entry_setup.get('found'):
            all_entry_setups.append(entry_setup)
        
        # Add ALL Smart Flow signals that passed threshold
        # Each pattern (LP, DB, DA) that passes gets its own signal
        if smart_flow_signals:
            for sf_signal in smart_flow_signals:
                sf_setup = sf_signal.copy()
                sf_setup['found'] = True
                sf_setup['entry_type'] = 'SMART_FLOW'
                all_entry_setups.append(sf_setup)
        
        # If no signals passed threshold, return empty list
        if not all_entry_setups:
            return []
        
        # Create signals for ALL entry setups that passed
        all_signals = []
        for setup in all_entry_setups:
            # Apply HTF MSS Sync check (with null safety)
            if htf_analysis and self.use_htf_sync:
                direction = setup.get('direction')
                entry_type = setup.get('entry_type', 'ZONE_ENTRY')
                
                # Null safety for direction
                if not direction:
                    logger.warning("⚠️ entry_setup has no direction, skipping HTF sync")
                    direction = 'NEUTRAL'
                
                # Smart Flow modes: Only adjust score, never filter (as per design)
                if entry_type == 'SMART_FLOW':
                    htf_score_adj, htf_reason, htf_conf_mult = self.htf_mss_analyzer.get_entry_adjustment(
                        direction, htf_analysis
                    )
                    
                    # Null safety: ensure reasons is a list
                    current_reasons = setup.get('reasons')
                    if not isinstance(current_reasons, list):
                        current_reasons = []
                    
                    setup['score'] = setup.get('score', 0) + htf_score_adj
                    setup['reasons'] = current_reasons + [htf_reason]
                    setup['htf_sync'] = {
                        'score_adjustment': htf_score_adj,
                        'reason': htf_reason,
                        'confidence_mult': htf_conf_mult
                    }
                else:
                    # Other entry types: Filter if HTF conflict
                    should_filter, filter_reason = self.htf_mss_analyzer.should_filter_entry(
                        direction, htf_analysis, min_sync_score=-1
                    )
                    
                    if should_filter:
                        logger.debug(f"Entry filtered by HTF sync: {filter_reason}")
                        continue  # Skip this signal
                    
                    htf_score_adj, htf_reason, htf_conf_mult = self.htf_mss_analyzer.get_entry_adjustment(
                        direction, htf_analysis
                    )
                    
                    setup['score'] = setup.get('score', 0) + htf_score_adj
                    setup['reasons'] = setup.get('reasons', []) + [htf_reason]
                    setup['htf_sync'] = {
                        'score_adjustment': htf_score_adj,
                        'reason': htf_reason,
                        'confidence_mult': htf_conf_mult
                    }
            
            # Create signal from entry setup
            signal = self._create_signal_from_setup(
                entry_setup=setup,
                analysis=analysis,
                candles=candles,
                current_price=current_price,
                risk_reward_ratio=risk_reward_ratio,
                htf_analysis=htf_analysis
            )
            
            if signal:
                all_signals.append(signal)
        
        return all_signals
    
    def _create_signal_from_setup(
        self,
        entry_setup: Dict,
        analysis: Dict,
        candles: pd.DataFrame,
        current_price: float,
        risk_reward_ratio: float,
        htf_analysis: Dict = None
    ) -> Optional[Signal]:
        """Create Signal from EntrySetupScanner result."""
        
        direction = entry_setup.get('direction')
        
        # Null safety: handle None direction
        if direction is None:
            logger.warning("⚠️ _create_signal_from_setup: direction is None, skipping signal creation")
            return None
        
        # Normalize direction: BUY->LONG, SELL->SHORT
        if direction == 'BUY':
            direction = 'LONG'
        elif direction == 'SELL':
            direction = 'SHORT'
        entry_price = entry_setup.get('entry_price', current_price)
        entry_score = entry_setup.get('score', 0)
        entry_type = entry_setup.get('entry_type', 'ZONE_ENTRY')
        tp_multiplier = entry_setup.get('tp_multiplier', 1.0)
        is_trend_aligned = entry_setup.get('is_trend_aligned', True)
        has_choch = entry_setup.get('has_choch', False)
        reasons = entry_setup.get('reasons', [])
        htf_sync = entry_setup.get('htf_sync', {})
        
        # O-01: Evaluate opposite action for EA
        # Get current position info if available
        current_pnl = 0
        current_duration = 0
        if self.active_setups:
            # Get the oldest active setup to estimate position age
            oldest_time = min(self.active_setups.values())
            current_duration = (datetime.now(timezone.utc) - oldest_time).total_seconds() / 60
        
        # Get position state from trailing manager for P&L proxy
        signal_id = f"{direction}_{setup_id[:8]}" if 'setup_id' in locals() else f"{direction}_{int(datetime.now(timezone.utc).timestamp())}"
        position_state = self._estimate_position_state(signal_id, entry_price, direction)
        trailing_active = False
        if hasattr(self, 'trailing_stop_manager') and signal_id in self.trailing_stop_manager.positions:
            trailing_active = self.trailing_stop_manager.positions[signal_id].activated
        
        # Get M5 structure info from ICT analysis
        m5_structure = 'NONE'
        m5_structure_dir = 'NEUTRAL'
        if 'ict_analysis' in analysis and analysis['ict_analysis']:
            ict_data = analysis['ict_analysis']
            if 'break_of_structure' in ict_data:
                bos_data = ict_data['break_of_structure']
                m5_structure = bos_data.get('type', 'NONE').upper()
                m5_structure_dir = bos_data.get('direction', 'NEUTRAL').upper()
        
        # Get supporting indicators (with null safety)
        cvd_divergence = False
        oi_divergence = False
        volume_exhaustion = False
        liquidity_ahead = False
        order_flow = None
        if analysis and 'order_flow' in analysis:
            order_flow = analysis['order_flow']
            if order_flow:
                cvd_divergence = order_flow.get('cvd_divergence', False)
                oi_divergence = order_flow.get('oi_divergence', False)
                volume_exhaustion = order_flow.get('volume_exhaustion', False)
        
        # v3.5: Section 23 - Opposite action handling moved to EA (always close and open new)
        
        pattern_key = entry_setup.get('pattern_type', entry_type)
        
        setup_id = self._generate_setup_id(analysis, direction, entry_setup)
        
        zone_type = self._determine_zone_type(entry_setup, entry_type)
        
        if self._is_setup_duplicate(setup_id):
            self._log_blocked_throttled(f"Duplicate setup (id: {setup_id[:20]}...)", setup_id)
            self._log_blocked_signal(entry_setup, direction, current_price, "DUPLICATE_SETUP")
            return None
        
        if self._is_price_too_close(current_price, pattern_key):
            self._log_blocked_throttled(f"{entry_type} too close to last same-pattern entry", pattern_key)
            self._log_blocked_signal(entry_setup, direction, current_price, "PRICE_TOO_CLOSE")
            return None
        
        if self._is_in_recent_zone(entry_setup, current_price, direction, zone_type):
            self._log_blocked_throttled("In same active zone", f"{direction}_{zone_type}")
            self._log_blocked_signal(entry_setup, direction, current_price, "IN_RECENT_ZONE")
            return None
        
        last_same_dir_signal = self._get_last_same_direction_signal(direction)
        if last_same_dir_signal:
            last_score = last_same_dir_signal.get('score', 0)
            if entry_score < last_score + 2:
                self._log_blocked_throttled(
                    f"Same-direction signal score too low ({entry_score} vs {last_score})", 
                    f"{direction}_UPGRADE"
                )
                self._log_blocked_signal(entry_setup, direction, current_price, "SCORE_NOT_HIGHER")
                return None
        
        # Get mode-specific SL/TP config
        sf_config = self.config.get('smart_flow', {})
        
        # Section 28: Dynamic ATR-based SL/TP with Volatility Regime Adjustment
        atr_result = self._detect_volatility_regime(candles) if candles is not None and len(candles) >= 300 else ('NORMAL', None, 50, False)
        volatility_regime = atr_result[0]
        atr_distance = atr_result[1]
        atr_percentile = atr_result[2]
        
        pattern_type = entry_setup.get('pattern_type', 'DA') if entry_type == 'SMART_FLOW' else 'DA'
        
        # === SECTION 10 CT-3: Get HTF Trend for Counter-Trend Adjustment ===
        # Extract HTF trend with CORRECT priority:
        # 1. counter_trend.htf_trend (new correct field from Section 7 logic)
        # 2. htf_sync (legacy field)
        # 3. htf_trend (top-level fallback)
        htf_trend = 'NEUTRAL'
        counter_trend_data = entry_setup.get('counter_trend', {})
        if counter_trend_data and counter_trend_data.get('htf_trend'):
            # Priority 1: counter_trend.htf_trend (most specific, correct field)
            htf_trend = counter_trend_data.get('htf_trend', 'NEUTRAL')
        elif 'htf_sync' in entry_setup:
            # Priority 2: htf_sync (legacy but still used)
            htf_sync = entry_setup.get('htf_sync', {})
            htf_trend = htf_sync.get('trend', 'NEUTRAL')
        elif 'htf_trend' in entry_setup:
            # Priority 3: htf_trend top-level (final fallback)
            htf_trend = entry_setup.get('htf_trend', 'NEUTRAL')
        
        # Get trade direction
        trade_direction = 'LONG' if direction == 'LONG' else 'SHORT'
        
        # Get dynamic multipliers based on volatility regime + HTF trend
        dynamic_sl_mult, dynamic_tp_mult, min_sl_distance = self._get_dynamic_atr_multiplier(
            volatility_regime, pattern_type, htf_trend, trade_direction
        )
        
        # Log volatility regime for debugging
        logger.debug(f"📊 Volatility: {volatility_regime} (ATR%: {atr_percentile:.0f}%), HTF:{htf_trend}, SL_mult={dynamic_sl_mult:.2f}, TP_mult={dynamic_tp_mult:.2f}, MinSL=${min_sl_distance}")
        
        # Calculate SL/TP
        if entry_type == 'SMART_FLOW':
            # Use ATR if available, otherwise fallback to fixed $ amounts
            if atr_distance is not None and atr_distance > 0:
                sl_distance = atr_distance * dynamic_sl_mult
                tp_distance = atr_distance * dynamic_tp_mult
                mode_rr = dynamic_tp_mult / dynamic_sl_mult if dynamic_sl_mult > 0 else 2.0
                
                # Enforce minimum SL distance
                if sl_distance < min_sl_distance:
                    logger.debug(f"⚠️ SL${sl_distance:.0f} < Min${min_sl_distance}, adjusting...")
                    sl_distance = min_sl_distance
                    tp_distance = sl_distance * mode_rr
                
                logger.debug(f"🎯 Dynamic SL/TP: ATR=${atr_distance:.0f} × {dynamic_sl_mult:.2f} = SL${sl_distance:.0f}, TP${tp_distance:.0f}, RR={mode_rr:.1f}")
            else:
                # Fallback based on pattern type (FIX: Issue 4 - Metadata Naming)
                # Updated: SWEEP -> LP, WALL -> DB, ZONE -> DA, OI_MOM -> LP, CVD_REV -> DA
                fallback_sl = {
                    'LP': 150,      # Liquidity Purge (formerly SWEEP/OI_MOM)
                    'DB': 120,      # Defensive Block (formerly WALL)
                    'DA': 80,       # Delta Absorption (formerly ZONE/CVD_REV)
                    'OI_MOM': 150,  # Legacy
                    'SWEEP': 150,   # Legacy
                    'WALL': 120,    # Legacy
                    'CVD_REV': 100, # Legacy
                    'ZONE': 80      # Legacy
                }
                fallback_tp = {
                    'LP': 300,      # Liquidity Purge
                    'DB': 240,      # Defensive Block
                    'DA': 160,      # Delta Absorption
                    'OI_MOM': 300,  # Legacy
                    'SWEEP': 300,   # Legacy
                    'WALL': 240,   # Legacy
                    'CVD_REV': 200, # Legacy
                    'ZONE': 160     # Legacy
                }
                sl_distance = fallback_sl.get(pattern_type, 100)
                tp_distance = fallback_tp.get(pattern_type, 200)
                mode_rr = 2.0
                logger.debug(f"🎯 Fallback SL/TP: ${sl_distance} / ${tp_distance} (no ATR)")
            mode_tp_mult = 1.0
        else:
            # Fallback for other entry types
            if atr_distance is not None:
                sl_distance = atr_distance * 1.5
                tp_distance = atr_distance * 3.0
                mode_rr = 2.0
            else:
                sl_distance = 1500
                mode_rr = risk_reward_ratio
                tp_distance = sl_distance * mode_rr
            mode_tp_mult = 1.0
        
        # 1. First, calculate Structural SL (needed for RR-refined TP selection)
        if not entry_setup.get('sl_boundary'):
            details = entry_setup.get('details', {})
            invalidation = details.get('invalidation_level') or details.get('sweep_level')
            if invalidation:
                entry_setup['sl_boundary'] = invalidation

        sl_boundary = entry_setup.get('sl_boundary')
        
        # FIX: Ensure sl_boundary is valid (different from entry price)
        if sl_boundary and abs(sl_boundary - entry_price) < 50:
            logger.debug(f"🔍DEBUG SL: sl_boundary too close to entry ({abs(sl_boundary - entry_price):.0f}), using ATR fallback")
            sl_boundary = None
        
        logger.debug(f"🔍DEBUG SL: entry_price={entry_price:.2f}, sl_boundary={sl_boundary}, pattern_type={entry_setup.get('pattern_type')}")
        
        # Get pattern type for config lookup
        pattern_type = entry_setup.get('pattern_type', 'DA')
        
        if sl_boundary:
            stop_loss = self._calculate_structural_sl(direction, entry_price, sl_boundary, candles, pattern_type)
            sl_distance = abs(entry_price - stop_loss)
        else:
            if direction == 'LONG':
                stop_loss = entry_price - sl_distance
            else:
                stop_loss = entry_price + sl_distance
            sl_distance = abs(entry_price - stop_loss)
            logger.debug(f"🔍DEBUG SL: No sl_boundary, using ATR/fixed. sl_distance={sl_distance:.0f}")

        # 2. Determine TP method: Structural (Institutional) with RR refinement
        structural_tp = self._calculate_structural_tp(
            direction=direction,
            entry_price=entry_price,
            entry_type=entry_type,
            pattern_type=entry_setup.get('pattern_type', 'DA'),
            candles=candles,
            poc_data=self.last_poc_data,
            sl_distance=sl_distance
        )

        # 3. Finalize Take Profit
        if structural_tp:
            take_profit = structural_tp
            tp_distance = abs(take_profit - entry_price)

        else:
            # Capital Preservation Floor: RR ต้อง >= 1.5 เสมอ ไม่ว่า mode_rr จะเป็นเท่าไร
            MIN_FALLBACK_RR = 1.5
            effective_fallback_rr = max(mode_rr, MIN_FALLBACK_RR)
            tp_distance = sl_distance * effective_fallback_rr
            if direction == 'LONG':
                take_profit = entry_price + tp_distance
            else:
                take_profit = entry_price - tp_distance
            logger.warning(
                f"⚠️ structural_tp NOT FOUND [{pattern_type} {direction}] "
                f"@ {entry_price:.2f} — fallback RR={effective_fallback_rr:.1f} "
                f"(mode_rr={mode_rr:.1f}, SL=${sl_distance:.0f} -> TP=${tp_distance:.0f})"
            )
        
        # Verify RR with mode-specific values
        actual_rr = tp_distance / sl_distance if sl_distance > 0 else 0

        # === CAPITAL PRESERVATION ASSERTION ===
        # ห้ามส่ง Signal ที่ RR < 1.2 ออกไปเด็ดขาด (ป้องกัน EA ปฏิเสธ)
        MIN_SEND_RR = 1.2
        if actual_rr < MIN_SEND_RR:
            logger.error(
                f"SIGNAL BLOCKED [RR TOO LOW]: {pattern_type} {direction} "
                f"RR={actual_rr:.2f} < {MIN_SEND_RR} — Signal NOT sent. "
                f"SL=${sl_distance:.0f}, TP=${tp_distance:.0f}, mode_rr={mode_rr:.1f}"
            )
            return None
        
        # Log mode-specific settings
        if entry_type == 'ISF_ENTRY':
            details = entry_setup.get('details', {})
            invalidation = details.get('invalidation_level')
            sl_source = "Structural" if sl_boundary else "Dynamic"
            logger.debug(f"📊 ISF SL/TP: SL=${sl_distance:.0f} ({sl_source} lvl:{invalidation:.0f if invalidation else 0}) RR={actual_rr:.1f}")
        
        # Custom formatting for Smart Flow Mode
        if entry_type == "SMART_FLOW":
            # Smart Flow Mode (3 Patterns: LP, DB, DA)
            # FIX: Issue 4 - Metadata Naming (SWEEP -> LP, WALL -> DB, ZONE -> DA)
            pattern_type = entry_setup.get('pattern_type', 'DA')
            
            # Map pattern_type to short names (using institutional naming)
            pattern_map = {
                # Institutional naming (primary)
                'LP': 'LP',      # Liquidity Purge
                'DB': 'DB',      # Defensive Block
                'DA': 'DA',      # Delta Absorption
                # Legacy naming (for backward compatibility)
                'OI_MOM': 'LP',  # Legacy -> LP
                'SWEEP': 'LP',   # Legacy -> LP
                'WALL': 'DB',    # Legacy -> DB
                'CVD_REV': 'DA', # Legacy -> DA
                'ZONE': 'DA'     # Legacy -> DA
            }
            short_pattern = pattern_map.get(pattern_type, pattern_type)
            
            raw_reason = entry_setup.get('reason', 'FLOW')
            reasons_list = raw_reason.split('_')
            
            # Format: <MODE>.<SCORE>.<DETAILS>
            # e.g. OI.18.OB_BU
            details_str = ""
            for detail in reasons_list[:2]:
                if 'OB' in detail.upper():
                    # Extract OB_BU or OB_BE
                    if 'BU' in detail.upper() or 'BULL' in detail.upper():
                        details_str = "OB_BU"
                    elif 'BE' in detail.upper() or 'BEAR' in detail.upper():
                        details_str = "OB_BE"
                    break
            
            if details_str:
                mt5_reason = f"{short_pattern}.{entry_score}.{details_str}"
            else:
                mt5_reason = f"{short_pattern}.{entry_score}"
            
        elif entry_type == "ISF_ENTRY":
            # 4th Mode: Institutional Flow (ISF)
            # e.g. ISF.18.LQ.CVD
            raw_reason = entry_setup.get('reason', 'FLOW')
            reasons_list = raw_reason.split('_')
            mt5_reason = f"ISF.{entry_score}." + ".".join(reasons_list[:2])
            
        else:
            # Normal / Legacy Flow (Fallback) - Keep short for MT5 31 char limit
            reason_abbr = abbreviate_reason("_".join(reasons[:2]))[:8]
            mt5_reason = f"S{entry_score}_{entry_type[:3]}_{reason_abbr}"[:31]
        
        entry_score = entry_setup.get('score', 0)
        
        # Normalize score to 20-point scale for MT5 if coming from 10-point system
        if entry_type not in ['ISF_ENTRY', 'SMART_FLOW']:
            entry_score = entry_score * 2
            
        self.last_entry_score = int(entry_score)
        
        # Apply Dynamic Lot Sizing
        lot_size = 0.0
        # If score is 7-8 -> 50% Lot, if 9-10 -> 100% Lot
        # The actual lot calculation is often done by the EA, but we send the multiplier/confidence
        
        # v3.3: Get LR Win Probability Prediction
        lr_prediction = None
        if hasattr(self, 'lr_model') and self.lr_model:
            try:
                # Prepare signal data for LR
                ict_reasons = entry_setup.get('reasons', [])
                liquidity = analysis.get('ict', {}).get('liquidity', {})
                liq_highs = [h['level'] for h in liquidity.get('highs', [])]
                liq_lows = [l['level'] for l in liquidity.get('lows', [])]
                all_liq = liq_highs + liq_lows
                min_dist = min([abs(current_price - l) / current_price for l in all_liq]) if all_liq else 0.01

                signal_data = {
                    'pattern_type': entry_setup.get('pattern_type', 'DA'),
                    'score': entry_score,
                    'is_trend_aligned': is_trend_aligned,
                    'entry_position_score': entry_setup.get('details', {}).get('entry_position', 1),
                    'zone_quality': entry_setup.get('details', {}).get('zone_quality', {}).get('score', 1),
                    # New Flow-First features
                    'delta_divergence_signal': self._map_divergence_to_number(analysis.get('order_flow', {}).get('cvd_divergence', 'NONE')),
                    'ict_confluence_score': len(ict_reasons),
                    'structure_state': self._map_structure_to_number(analysis.get('structure', {}).get('structure', 'RANGE')),
                    'regime_type': self._map_regime_to_number(entry_setup.get('details', {}).get('regime', 'NORMAL')),
                    'proximity_to_liquidity': min_dist
                }
                
                # Prepare market data for LR (Flow-First features)
                liquidity_walls = self.last_liquidity_wall_data or {}
                bid_walls_value = liquidity_walls.get('total_bid_volume', 0)
                ask_walls_value = liquidity_walls.get('total_ask_volume', 0)
                
                market_data = {
                    # Primary Flow Features (The Engine)
                    'oi_shock': binance_data.get('oi', {}).get('openInterestChange', 0) if 'binance_data' in locals() else 0,
                    'cvd_aggression': analysis.get('order_flow', {}).get('cvd_delta', 0),
                    'volume_surge_ratio': analysis.get('order_flow', {}).get('volume_ratio', 1),
                    'wall_imbalance': (bid_walls_value - ask_walls_value) / (bid_walls_value + ask_walls_value + 1),
                    'funding_bias': binance_data.get('funding_rate', 0) if 'binance_data' in locals() else 0,
                    
                    # Basic Features
                    'atr': atr_distance if 'atr_distance' in locals() else 100
                }
                
                lr_prediction = self.lr_model.predict(signal_data, market_data)
                logger.debug(f"📊 LR Win Prob: {lr_prediction.get('win_probability', 0):.1%} | {lr_prediction.get('recommendation', 'N/A')}")
                
            except Exception as e:
                logger.debug(f"LR prediction error: {e}")
        
        # Calculate confidence (blend score-based with LR probability)
        base_confidence = min(entry_score * 10, 100)
        
        if lr_prediction and lr_prediction.get('win_probability'):
            # Blend: 50% score-based + 50% LR probability
            lr_prob = lr_prediction['win_probability'] * 100
            confidence = int(base_confidence * 0.5 + lr_prob * 0.5)
        else:
            confidence = int(base_confidence)
        
        # Prepare full reason string for Telegram (match MT5 format but use full names, no score)
        if entry_type == "SMART_FLOW":
            pattern_type = entry_setup.get('pattern_type', 'DA')
            raw_reason = entry_setup.get('reason', 'FLOW')
            # Format with full names
            reason_parts = raw_reason.split('_')
            formatted_parts = ' '.join(reason_parts[:3]) if len(reason_parts) >= 3 else raw_reason
            full_reason = f"Mode: {pattern_type} | {formatted_parts}"
        else:
            # Normal Flow (P1 + P2)
            full_reason = f"Reasons: {'; '.join(reasons[:5])}"
        
        # All modes: Force single TP (TP1 = take_profit, TP2 = 0, TP3 = 0)
        # This prevents MT5 EA from splitting into 3 orders
        tp1 = take_profit
        tp2 = 0
        tp3 = 0
        
        # ===== Section 20: Smart Position Management =====
        # Define these BEFORE creating Signal
        # Determine market condition
        order_flow = analysis.get('order_flow', {})
        volume_ratio = order_flow.get('volume_ratio', 1)
        if volume_ratio > 2:
            market_condition = 'TRENDING'
        elif volume_ratio < 1.5:
            market_condition = 'RANGING'
        else:
            market_condition = 'VOLATILE'
        
        # Determine session from UTC hour
        utc_hour = datetime.now(timezone.utc).hour
        if 0 <= utc_hour < 7:
            session = 'ASIA'
        elif 7 <= utc_hour < 13:
            session = 'LONDON'
        elif 13 <= utc_hour < 21:
            session = 'NY'
        else:
            session = 'ASIA' if utc_hour < 24 else 'LONDON-NY'
        
        # Section 23: Simplified flow - Always follow latest signal, threshold is the only gate
        
        signal = Signal(
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            lot_size=0, # FORCE 0 for EA to calculate
            confidence=int(confidence),
            reason=full_reason,
            metadata={
                'analysis': analysis,
                'target_rr': actual_rr,
                'short_reason': mt5_reason,
                'score': entry_score,
                'entry_type': entry_type,
                'setup_id': setup_id,
                'is_trend_aligned': is_trend_aligned,
                'tp_multiplier': tp_multiplier,
                # Section 23: Opposite action now handled directly by EA
                'bot_state': self.bot_state.get_state_dict(),
                'htf_sync': htf_sync,
                'htf_trend': htf_analysis.get('trend').value if htf_analysis and htf_analysis.get('trend') else 'RANGE',
                'structure': 'RANGE',  # Will be overridden by metadata from analysis if available
                'pattern_type': signal_data.get('pattern_type', entry_setup.get('pattern_type', 'UNKNOWN')),  # Section 19: For EA pattern-specific BE/Trailing
                # Architecture Plan 2.2 & TASK 2: Institutional Grade & RR Signal Metadata
                'institutional_grade': signal_data.get('institutional_grade', False),  # True when Score >= 15 OR ICS >= 13
                'confluence_score': signal_data.get('confluence_score', 0),  # ICS score for EA risk management
                # required_rr removed from metadata — to_dict() now calculates dynamically from actual TP/SL
                # (keeps single source of truth in Signal.to_dict())
                # Architecture Plan Section 3.2: Dynamic Institutional Risk Management
                'be_trigger_pct': self._calculate_be_trigger_pct(signal_data.get('institutional_grade', False)),  # 0.2% for institutional, 0.4% for normal
                'trailing_config': self._calculate_trailing_config(signal_data.get('pattern_type', 'DA')),  # Pattern-specific trailing settings
                'lr_prediction': lr_prediction,  # v3.3: LR win probability
                # New Flow-First features for ML training
                'delta_divergence_signal': signal_data.get('delta_divergence_signal', 0),
                'ict_confluence_score': signal_data.get('ict_confluence_score', 0),
                'structure_state': signal_data.get('structure_state', 0),
                'regime_type': signal_data.get('regime_type', 0),
                'proximity_to_liquidity': signal_data.get('proximity_to_liquidity', 0.5),
                # Market data features
                'oi_shock': market_data.get('oi_shock', 0) if 'market_data' in locals() else 0,
                'cvd_aggression': market_data.get('cvd_aggression', 0) if 'market_data' in locals() else 0,
                'volume_surge_ratio': market_data.get('volume_surge_ratio', 1) if 'market_data' in locals() else 1,
                'wall_imbalance': market_data.get('wall_imbalance', 0) if 'market_data' in locals() else 0,
                'funding_bias': market_data.get('funding_bias', 0) if 'market_data' in locals() else 0,
                'htf_analysis': {
                    'trend': htf_analysis.get('trend').value if htf_analysis and htf_analysis.get('trend') else 'RANGE',
                    'structure': htf_analysis.get('structure_type') if htf_analysis else 'NONE',
                    'last_high': htf_analysis.get('last_high') if htf_analysis else 0,
                    'last_low': htf_analysis.get('last_low') if htf_analysis else 0
                } if htf_analysis else None,
                # Section 7: Counter-Trend Trading Quality
                'counter_trend': {
                    'is_counter_trend': entry_setup.get('counter_trend', {}).get('is_counter_trend', False),
                    'quality_level': entry_setup.get('counter_trend', {}).get('quality_level', 'NORMAL'),
                    'htf_trend': entry_setup.get('counter_trend', {}).get('htf_trend', 'NEUTRAL'),
                    'sl_multiplier': entry_setup.get('counter_trend', {}).get('sl_multiplier', 1.0),
                    'tp_multiplier': entry_setup.get('counter_trend', {}).get('tp_multiplier', 1.0)
                },
                # Section 23: Position protection and anti_churn removed (EA handles directly)
            }
        )
        
        if self.use_trailing_stop:
            self.trailing_stop_manager.register_position(
                signal_id=signal.signal_id,
                direction=direction,
                entry_price=entry_price,
                initial_sl=stop_loss
            )
        
        self.last_signal_time = datetime.now(timezone.utc)
        self.last_entry_price = current_price
        
        # Track last price per pattern for pattern-aware locking
        pattern_key = entry_setup.get('pattern_type', entry_type)
        self.last_pattern_prices[pattern_key] = {
            'price': current_price,
            'time': datetime.now(timezone.utc)
        }
        
        self.active_setups[setup_id] = datetime.now(timezone.utc)
        
        # Record trade in SmartFlowManager if applicable
        if entry_type == 'SMART_FLOW':
            self.smart_flow_manager.record_trade(pattern_key, entry_price)
            
        # Track zone to prevent duplicate entries (structure-based)
        zone_type = self._determine_zone_type(entry_setup, entry_type)
        self._track_entry_zone(entry_setup, entry_price, direction, zone_type)
        self._cleanup_broken_zones()
        
        # Save state after all updates
        self.save_state()
        
        # Record trade data for analytics
        trade_data = {
            'pattern_type': entry_setup.get('pattern_type', entry_type),
            'direction': direction,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'score': entry_score,
            'max_score': entry_setup.get('max_score', 20),
            'market_condition': market_condition,
            'session': session,
            'h1_trend': htf_analysis.get('trend').value if htf_analysis and htf_analysis.get('trend') else 'RANGE',
            'm5_trend': analysis.get('ict', {}).get('trend', 'RANGE'),
            'zone_context': analysis.get('zone_context', 'RANGE'),
            'cvd_delta': order_flow.get('cvd_delta', 0),
            'volume_ratio': volume_ratio,
            'score_breakdown': entry_setup.get('details', {})
        }
        
        self.trade_storage.record_trade_opened(trade_data)
        
        htf_log = f" | HTF: {htf_sync.get('reason', 'N/A')}" if htf_sync else ""
        logger.debug(
            f"Signal generated: {signal} | Score: {entry_score} | "
            f"Type: {entry_type} | RR: {actual_rr:.2f} | TP Mult: {tp_multiplier}x{htf_log}"
        )
        
        return signal
    
    # Section 23: Removed Section 20 helper functions (churn protection)
    # - _get_current_positions_summary
    # - _calculate_net_exposure
    # - _get_dominant_direction
    # - _determine_protection_mode
    # - _get_min_protection_score
    # - _suggest_position_action
    # - _get_escalation_factor
    # - _get_churn_level
    # - _get_session_churn_limit
    # - _assess_trend_strength
    
    def _log_blocked_throttled(self, reason: str, key: str):
        """Log blocked signal reason with throttling (5 minute interval per key)."""
        now = datetime.now(timezone.utc)
        log_key = (reason, key)
        last_log = self.last_block_log_time.get(log_key, datetime.min.replace(tzinfo=timezone.utc))
        
        if (now - last_log).total_seconds() / 60 >= 5:

            self.last_block_log_time[log_key] = now

    def _calculate_atr_distance(self, candles: pd.DataFrame, period: int = 14) -> float:
        """Calculate ATR-based stop loss distance."""
        if len(candles) < period:
            return 120
        
        high = candles['high']
        low = candles['low']
        close = candles['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        
        atr_multiplier = self.config.get('stop_loss', {}).get('atr_multiplier', 1.2)
        return atr * atr_multiplier
    
    def _detect_volatility_regime(self, candles: pd.DataFrame, institutional_grade: bool = False) -> tuple:
        """
        Detect market volatility regime for dynamic SL adjustment.
        
        Architecture Plan Section 35: Adaptive Volatility Guard
        
        Improvements:
        - Threshold Optimization: Changed from 85th to 95th percentile for EXTREME
        - Minimum Baseline Guard: Require minimum 300 candles for stable percentile calculation
        - Momentum Exception Rule: Allow institutional grade signals in high volatility
        
        Returns:
            tuple: (regime, atr_value, atr_percentile, is_blocked)
            - regime: 'LOW', 'NORMAL', 'HIGH', 'EXTREME'
            - atr_value: Current ATR value
            - atr_percentile: ATR percentile rank (0-100)
            - is_blocked: True if signal should be blocked due to extreme volatility
        """
        try:
            # Section 35.1: Minimum Baseline Guard
            # Require minimum 300 candles for stable percentile calculation
            if len(candles) < 300:
                return 'NORMAL', None, 50, False
            
            # Calculate ATR for last periods
            high = candles['high']
            low = candles['low']
            close = candles['close']
            
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_series = tr.rolling(14).mean()
            
            current_atr = atr_series.iloc[-1]
            
            if pd.isna(current_atr) or current_atr <= 0:
                return 'NORMAL', None, 50, False
            
            # Calculate ATR percentile over all available data (minimum 300)
            atr_history = atr_series.tail(len(candles)).dropna()
            
            if len(atr_history) < 300:
                return 'NORMAL', current_atr, 50, False
            
            # Rank current ATR in historical context
            atr_percentile = (atr_history < current_atr).sum() / len(atr_history) * 100
            
            # Section 35.1: Threshold Optimization
            # Changed from 85th to 95th percentile for EXTREME
            if atr_percentile < 20:
                regime = 'LOW'
                is_blocked = False
            elif atr_percentile < 60:
                regime = 'NORMAL'
                is_blocked = False
            elif atr_percentile < 95:  # Changed from 85 to 95
                regime = 'HIGH'
                is_blocked = False
            else:
                # Section 35.2: Momentum Exception Rule
                # If institutional_grade == true, allow trade even in EXTREME volatility
                # but flag for minimum lot sizing
                if institutional_grade:
                    regime = 'HIGH'  # Downgrade to HIGH instead of blocking
                    is_blocked = False
                    logger.debug(f"📊 VOLATILITY: Institutional grade allowed in high volatility (ATR percentile: {atr_percentile:.1f}%)")
                else:
                    regime = 'EXTREME'
                    is_blocked = True  # Block non-institutional signals
            
            return regime, current_atr, atr_percentile, is_blocked
            
        except Exception as e:
            logger.debug(f"Volatility regime detection error: {e}")
            return 'NORMAL', None, 50, False

    def get_htf_trend_strength(self, candles_h1: pd.DataFrame = None) -> Tuple[str, int, Dict]:
        """
        Section 7: HTF Trend Detection for Counter-Trend Trading Quality.
        
        Analyzes HTF (H1/H4) trend to determine:
        1. HTF Trend Direction: STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR
        2. Trend Strength: 1-5 scale
        
        This is used to adjust SL/TP for counter-trend trades.
        
        Args:
            candles_h1: H1 candles for trend analysis
            
        Returns:
            Tuple[str, int, Dict]: (htf_trend, strength, details)
        """
        try:
            details = {}
            
            # If no H1 data, use M5 candles to infer trend
            if candles_h1 is None or len(candles_h1) < 20:
                # Use current candles for basic trend detection
                return self._infer_htf_trend_from_m5(candles)
            
            # === Analyze H1/H4 Trend ===
            
            # 1. Calculate Higher Highs/Lows
            highs = candles_h1['high'].values
            lows = candles_h1['low'].values
            closes = candles_h1['close'].values
            
            # Look at last 20 candles for trend
            lookback = min(20, len(highs))
            recent_highs = highs[-lookback:]
            recent_lows = lows[-lookback:]
            recent_closes = closes[-lookback:]
            
            # Count HH/HL (Bullish) vs LH/LL (Bearish)
            hh_count = 0
            hl_count = 0
            lh_count = 0
            ll_count = 0
            
            for i in range(2, len(recent_highs)):
                # Higher High
                if recent_highs[i] > recent_highs[i-1] > recent_highs[i-2]:
                    hh_count += 1
                # Higher Low
                if recent_lows[i] > recent_lows[i-1] > recent_lows[i-2]:
                    hl_count += 1
                # Lower High
                if recent_highs[i] < recent_highs[i-1] < recent_highs[i-2]:
                    lh_count += 1
                # Lower Low
                if recent_lows[i] < recent_lows[i-1] < recent_lows[i-2]:
                    ll_count += 1
            
            # 2. Calculate ADX for trend strength
            adx_value = self._calculate_adx(candles_h1, period=14)
            
            # 3. Determine HTF Trend
            trend_score = 0
            trend_reason = []
            
            # Bullish signals
            if hh_count > lh_count:
                trend_score += 2
                trend_reason.append(f"HH>{LH}")
            if hl_count > ll_count:
                trend_score += 1
                trend_reason.append(f"HL>{LL}")
            if recent_closes[-1] > np.mean(recent_closes[-10:]):
                trend_score += 1
                trend_reason.append("Above MA10")
            
            # Bearish signals
            if lh_count > hh_count:
                trend_score -= 2
                trend_reason.append(f"LH>{HH}")
            if ll_count > hl_count:
                trend_score -= 1
                trend_reason.append(f"LL>{HL}")
            if recent_closes[-1] < np.mean(recent_closes[-10:]):
                trend_score -= 1
                trend_reason.append("Below MA10")
            
            # Strong trend indicators
            if adx_value > 25:
                if trend_score > 0:
                    trend_score += 1
                    trend_reason.append(f"Strong ADX Bull ({adx_value:.1f})")
                else:
                    trend_score -= 1
                    trend_reason.append(f"Strong ADX Bear ({adx_value:.1f})")
            
            # Determine final trend
            if trend_score >= 3:
                htf_trend = "STRONG_BULL"
                strength = 5
            elif trend_score == 2:
                htf_trend = "BULL"
                strength = 4
            elif trend_score == 1 or trend_score == 0:
                htf_trend = "NEUTRAL"
                strength = 3
            elif trend_score == -2:
                htf_trend = "BEAR"
                strength = 2
            else:  # trend_score <= -3
                htf_trend = "STRONG_BEAR"
                strength = 1
            
            details = {
                'trend_score': trend_score,
                'hh_count': hh_count,
                'lh_count': lh_count,
                'hl_count': hl_count,
                'll_count': ll_count,
                'adx': adx_value,
                'reasons': trend_reason
            }
            
            return htf_trend, strength, details
            
        except Exception as e:
            logger.debug(f"HTF trend detection error: {e}")
            return 'NEUTRAL', 3, {'error': str(e)}
    
    def _infer_htf_trend_from_m5(self, candles: pd.DataFrame) -> Tuple[str, int, Dict]:
        """
        Infer HTF trend from M5 candles when H1 data is not available.
        Uses longer lookback to simulate higher timeframe analysis.
        """
        try:
            if candles is None or len(candles) < 50:
                return 'NEUTRAL', 3, {'reason': 'insufficient_data'}
            
            # Use 50 candles (~4 hours of M5) as proxy for H1
            lookback = min(50, len(candles))
            recent = candles.tail(lookback)
            
            highs = recent['high'].values
            lows = recent['low'].values
            closes = recent['close'].values
            
            # Simple trend detection
            recent_high = highs[-1]
            recent_low = lows[-1]
            oldest_high = highs[0]
            oldest_low = lows[0]
            oldest_close = closes[0]
            
            trend_score = 0
            
            # Price direction
            if closes[-1] > oldest_close:
                trend_score += 1
            else:
                trend_score -= 1
            
            # Higher highs/lows
            max_high = max(highs)
            min_low = min(lows)
            
            if recent_high == max_high and recent_low > min_low:
                trend_score += 1
            elif recent_low == min_low and recent_high < max_high:
                trend_score -= 1
            
            # Determine trend
            if trend_score >= 2:
                htf_trend = "BULL"
                strength = 4
            elif trend_score == 1:
                htf_trend = "NEUTRAL"
                strength = 3
            else:
                htf_trend = "BEAR"
                strength = 2
            
            return htf_trend, strength, {'reason': 'inferred_from_m5'}
            
        except Exception as e:
            return 'NEUTRAL', 3, {'error': str(e)}
    
    def _calculate_adx(self, candles: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate Average Directional Index (ADX).
        
        ADX > 25 = Strong Trend
        ADX < 20 = Weak Trend / Ranging
        """
        try:
            if len(candles) < period + 1:
                return 20.0  # Default to weak trend
            
            high = candles['high'].values
            low = candles['low'].values
            close = candles['close'].values
            
            # Calculate True Range
            tr1 = high[1:] - low[1:]
            tr2 = abs(high[1:] - close[:-1])
            tr3 = abs(low[1:] - close[:-1])
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            
            # Calculate Directional Movement
            high_diff = high[1:] - high[:-1]
            low_diff = low[:-1] - low[1:]
            
            plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
            minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
            
            # Smooth values
            atr = np.mean(tr[-period:])
            plus_dm_smooth = np.mean(plus_dm[-period:])
            minus_dm_smooth = np.mean(minus_dm[-period:])
            
            if atr == 0:
                return 20.0
            
            # Calculate DI
            plus_di = (plus_dm_smooth / atr) * 100
            minus_di = (minus_dm_smooth / atr) * 100
            
            # Calculate DX
            di_sum = plus_di + minus_di
            if di_sum == 0:
                return 20.0
            
            dx = abs(plus_di - minus_di) / di_sum * 100
            
            # ADX is smoothed DX
            adx = dx  # Simplified - in production, use Wilder smoothing
            
            return float(adx)
            
        except Exception as e:
            return 20.0
    
    def _get_dynamic_atr_multiplier(self, regime: str, pattern_type: str, htf_trend: str = None, trade_direction: str = None) -> tuple:
        """
        Get dynamic ATR multiplier based on volatility regime and HTF trend.
        
        Higher volatility = larger SL to avoid getting stopped out.
        
        Section 7.5: Counter-Trend SL/TP Adjustment:
        - Counter-trend trades get wider SL (more room for HTF momentum)
        - Counter-trend trades get tighter TP (take profit faster)
        
        Args:
            regime: Volatility regime ('LOW', 'NORMAL', 'HIGH', 'EXTREME')
            pattern_type: Pattern type ('LP', 'DB', 'DA', 'OI_MOM', 'SWEEP', 'WALL', 'CVD_REV', 'ZONE')
            htf_trend: HTF trend from get_htf_trend_strength() (optional)
            trade_direction: Trade direction ('LONG', 'SHORT') (optional)
            
        Returns:
            tuple: (sl_multiplier, tp_multiplier, min_sl_distance)
        """
        # Base multipliers per pattern
        base_sl_mult = {
            'LP': 1.5,
            'DB': 1.2,
            'DA': 1.0,
            'OI_MOM': 1.5,
            'SWEEP': 1.5,
            'WALL': 1.2,
            'CVD_REV': 1.0,
            'ZONE': 1.0
        }
        
        base_tp_mult = {
            'LP': 3.0,
            'DB': 2.4,
            'DA': 2.0,
            'OI_MOM': 3.0,
            'SWEEP': 3.0,
            'WALL': 2.4,
            'CVD_REV': 2.0,
            'ZONE': 1.8
        }
        
        # Regime adjustments
        regime_adjustments = {
            'LOW': {'sl_mult': 0.8, 'tp_mult': 0.9, 'min_sl': 60},       # Tighter in low vol
            'NORMAL': {'sl_mult': 1.0, 'tp_mult': 1.0, 'min_sl': 80},    # Base settings
            'HIGH': {'sl_mult': 1.3, 'tp_mult': 1.2, 'min_sl': 120},      # Wider in high vol
            'EXTREME': {'sl_mult': 1.6, 'tp_mult': 1.5, 'min_sl': 180}    # Much wider in extreme
        }
        
        adj = regime_adjustments.get(regime, regime_adjustments['NORMAL'])
        
        sl_mult = base_sl_mult.get(pattern_type, 1.2) * adj['sl_mult']
        tp_mult = base_tp_mult.get(pattern_type, 2.0) * adj['tp_mult']
        min_sl = adj['min_sl']
        
        # === SECTION 7.5: Counter-Trend SL/TP Adjustment ===
        if htf_trend and trade_direction:
            ct_sl_mult, ct_tp_mult = self._get_counter_trend_multipliers(htf_trend, trade_direction)
            
            # Apply counter-trend multipliers
            sl_mult *= ct_sl_mult
            tp_mult *= ct_tp_mult
            
            logger.debug(f"📊 CT Adjustment: HTF={htf_trend}, Dir={trade_direction}, CT_SL={ct_sl_mult:.2f}x, CT_TP={ct_tp_mult:.2f}x")
        
        # === RR Cap: เฉพาะ Counter-Trend เท่านั้น ===
        # With-Trend ไม่ Cap — ใช้ RR เต็มจาก base_tp_mult
        if sl_mult > 0 and htf_trend and trade_direction:
            is_counter = (
                (htf_trend in ['STRONG_BULL', 'BULL'] and trade_direction == 'SHORT') or
                (htf_trend in ['STRONG_BEAR', 'BEAR'] and trade_direction == 'LONG')
            )
            if is_counter:
                effective_rr = tp_mult / sl_mult
                # Cap ต้องสูงกว่า EA Floor (0.95) อย่างน้อย 25% buffer
                # STRONG_BULL/BEAR -> Cap 1.2 | BULL/BEAR -> Cap 1.5
                if htf_trend in ['STRONG_BULL', 'STRONG_BEAR']:
                    max_rr = 1.2
                else:
                    max_rr = 1.5
                if effective_rr > max_rr:
                    tp_mult = sl_mult * max_rr
                    logger.debug(
                        f"📊 CT RR Cap: HTF={htf_trend}, Dir={trade_direction}, "
                        f"RR capped at {max_rr} (was {effective_rr:.2f})"
                    )

        return sl_mult, tp_mult, min_sl
    
    def _get_counter_trend_multipliers(self, htf_trend: str, trade_direction: str) -> Tuple[float, float]:
        """
        Section 7.5: Get SL/TP multipliers for counter-trend trades.
        
        Args:
            htf_trend: HTF trend level ('STRONG_BULL', 'BULL', 'NEUTRAL', 'BEAR', 'STRONG_BEAR')
            trade_direction: Trade direction ('LONG', 'SHORT')
            
        Returns:
            Tuple[float, float]: (sl_multiplier, tp_multiplier)
        """
        # From Section 7.5 Table: SL Width Adjustment for Counter-Trend
        # Counter-trend: BULL→SHORT, BEAR→LONG
        # STRONG_BULL→SHORT: SL 1.5x wider, TP 0.7x tighter
        # BULL→SHORT: SL 1.25x wider, TP 0.85x tighter
        # BEAR→LONG: SL 1.25x wider, TP 0.85x tighter
        # STRONG_BEAR→LONG: SL 1.5x wider, TP 0.7x tighter
        
        # SL multipliers (wider for counter-trend)
        sl_mult_map = {
            ('STRONG_BULL', 'SHORT'): 1.5,   # Very wide SL - strong HTF momentum
            ('BULL', 'SHORT'): 1.25,         # Wider SL - HTF momentum
            ('NEUTRAL', 'LONG'): 1.0,        # Normal
            ('NEUTRAL', 'SHORT'): 1.0,       # Normal
            ('BEAR', 'LONG'): 1.25,          # Wider SL - HTF momentum
            ('STRONG_BEAR', 'LONG'): 1.5     # Very wide SL - strong HTF momentum
        }
        
        # TP multipliers (tighter for counter-trend)
        tp_mult_map = {
            ('STRONG_BULL', 'SHORT'): 0.7,   # Take profit fast - strong HTF momentum
            ('BULL', 'SHORT'): 0.85,         # Take profit sooner - HTF momentum
            ('NEUTRAL', 'LONG'): 1.0,        # Normal
            ('NEUTRAL', 'SHORT'): 1.0,       # Normal
            ('BEAR', 'LONG'): 0.85,          # Take profit sooner - HTF momentum
            ('STRONG_BEAR', 'LONG'): 0.7     # Take profit fast - strong HTF momentum
        }
        
        key = (htf_trend, trade_direction)
        
        # Check if counter-trend
        is_counter = False
        if htf_trend in ['STRONG_BULL', 'BULL'] and trade_direction == 'SHORT':
            is_counter = True
        elif htf_trend in ['STRONG_BEAR', 'BEAR'] and trade_direction == 'LONG':
            is_counter = True
        
        if is_counter:
            sl_mult = sl_mult_map.get(key, 1.0)
            tp_mult = tp_mult_map.get(key, 1.0)
        else:
            # With-trend or neutral - no adjustment
            sl_mult = 1.0
            tp_mult = 1.0
        
        return sl_mult, tp_mult
    
    def _generate_setup_id(self, analysis: Dict, direction: str, entry_setup: Dict) -> str:
        """
        Generate unique setup ID with pattern differentiation.
        
        FIX: Issue 4 - Default pattern_type changed from 'ZONE' to 'DA' (Delta Absorption)
        FIX: Added null checks to prevent 'NoneType' object is not subscriptable error
        """
        # Null safety checks
        if entry_setup is None:
            logger.warning("⚠️ _generate_setup_id: entry_setup is None, using defaults")
            entry_setup = {}
        if direction is None:
            logger.warning("⚠️ _generate_setup_id: direction is None, using 'N'")
            direction = 'NEUTRAL'
        if analysis is None:
            analysis = {}
        
        entry_type = entry_setup.get('entry_type', 'DA')
        
        # Add pattern type for Smart Flow to differentiate LP/DB/DA (formerly SWEEP/WALL/ZONE)
        pattern_suffix = ""
        if entry_type == 'SMART_FLOW':
            pattern = entry_setup.get('pattern_type', 'DA')
            pattern_suffix = f"_{str(pattern)[:2]}"  # LP, DB, DA (2 chars)
        
        components = [
            str(direction)[0] if direction else 'N',
            f"{str(entry_type)[:3]}{pattern_suffix}",
            f"{int(analysis.get('price', 0) / 50) * 50}",
            str(self.bot_state.structure_quality) if hasattr(self, 'bot_state') else '0'
        ]
        return "_".join(components)
    
    def _is_setup_duplicate(self, setup_id: str) -> bool:
        """Check if setup is duplicate (Reduced cooldown for M5 scalping)."""
        if setup_id in self.active_setups:
            last_time = self.active_setups[setup_id]
            time_since = (datetime.now(timezone.utc) - last_time).total_seconds()
            
            # Reduced from 900s to 300s (5 mins) for better scalping frequency
            cooldown = self.config.get('duplication_cooldown', 300)
            return time_since < cooldown
        return False
    
    def _calculate_structural_sl(self, direction: str, entry_price: float, sl_boundary: float, candles: pd.DataFrame, pattern_type: str = 'DA') -> float:
        """
        Calculate SL based on institutional standards with ATR buffer and safety guards.
        
        Section 52: Heavy-Duty Institutional SL Standards:
        - ATR Period: 20 (more stable)
        - Pattern-Specific Buffers:
          - LP (Liquidity Purge): 2.0x ATR
          - DA (Delta Absorption): 2.5x ATR
          - DB (Defensive Block): 1.5x ATR
        - Hard Floor: 0.8% minimum
        """
        # Pattern-Specific Buffer Multipliers (Institutional Standard)
        if pattern_type == 'LP':
            buffer_mult = 2.0  # Liquidity Purge = High risk
        elif pattern_type == 'DA':
            buffer_mult = 2.5  # Delta Absorption = Highest risk
        elif pattern_type == 'DB':
            buffer_mult = 1.5  # Defensive Block = Moderate risk
        else:
            buffer_mult = 1.5  # Default
        
        # 1. Get ATR for dynamic buffer (using period 20 for stability)
        atr = self._calculate_atr_distance(candles, period=20)
        
        # 2. Calculate Hard Floor (0.8% minimum)
        floor_pct = 0.008  # 0.8%
        min_distance = entry_price * floor_pct
        
        # 3. Calculate buffer based on pattern type
        buffer = atr * buffer_mult if atr and atr > 0 else entry_price * 0.01
        
        # Ensure buffer is at least $100
        buffer = max(buffer, 100.0)
        
        # 4. Apply buffer to boundary
        if direction == 'LONG':
            sl_price = sl_boundary - buffer
            # Ensure SL is below entry
            sl_price = min(sl_price, entry_price - min_distance)
        else:
            sl_price = sl_boundary + buffer
            # Ensure SL is above entry
            sl_price = max(sl_price, entry_price + min_distance)
            
        # 5. Apply Safety Guards
        sl_dist = abs(entry_price - sl_price)
        
        # Default limits
        default_min_sl = min_distance  # 0.8% floor
        default_max_sl = 2500
        
        # Get config-based limits
        config_key_map = {
            'LP': 'isf_sweep',
            'DB': 'isf_wall',
            'DA': 'isf_inst',
            'OI_MOM': 'isf_sweep',
            'SWEEP': 'isf_sweep',
            'WALL': 'isf_wall',
            'CVD_REV': 'isf_inst',
            'ZONE': 'isf_inst'
        }
        config_key = config_key_map.get(pattern_type, 'isf_inst')
        mode_config = self.config.get(config_key, {})
        
        min_sl = mode_config.get('min_sl_distance', default_min_sl)
        max_sl = mode_config.get('max_sl_distance', default_max_sl)
        
        # Ensure minimum SL is at least the floor-based minimum
        min_sl = max(min_sl, min_distance)
        
        # DEBUG: Log distances
        logger.debug(f"📐 SL Calc: pattern={pattern_type}, entry={entry_price:.2f}, boundary={sl_boundary:.2f}, atr={atr:.2f}, buffer={buffer_mult}x, floor=0.8%, sl={sl_price:.2f}")
        
        if sl_dist < min_sl:
            sl_price = entry_price - min_sl if direction == 'LONG' else entry_price + min_sl
            logger.debug(f"📐 SL Adjusted to min_sl={min_sl:.2f}, new_sl={sl_price:.2f}")
        elif sl_dist > max_sl:
            sl_price = entry_price - max_sl if direction == 'LONG' else entry_price + max_sl

        return float(sl_price)
    
    def _is_price_too_close(self, current_price: float, pattern_type: str = None) -> bool:
        """Check if price too close to last entry of the SAME pattern with time expiry."""
        # For M5 Scalping, we use a smaller distance threshold (e.g. $150)
        min_dist = self.config.get('min_pattern_distance', 150)
        
        # Also check entry_scanner config
        entry_scanner = self.config.get('entry_scanner', {})
        min_dist = entry_scanner.get('min_pattern_distance', min_dist)
        
        pattern_data = self.last_pattern_prices.get(pattern_type)
        if not pattern_data:
            return False
            
        last_price = 0
        last_time = None
        
        if isinstance(pattern_data, dict):
            last_price = pattern_data.get('price', 0)
            last_time = pattern_data.get('time')
        else: # Legacy support
            last_price = float(pattern_data)
            
        if last_price == 0:
            return False

        # 1. Check Time Expiry (Default 20 minutes)
        # Allows re-entry at same price if enough time has passed
        if last_time and isinstance(last_time, datetime):
            elapsed_mins = (datetime.now(timezone.utc) - last_time).total_seconds() / 60
            expiry = entry_scanner.get('reentry_time_expiry', 20)
            if elapsed_mins >= expiry:
                return False # Block expired, allow re-entry
            
        # 2. Check Price Distance
        distance = abs(current_price - last_price)
        return distance < min_dist
    
    def _cleanup_old_setups(self, max_age_seconds: int = 1800):
        """Remove old setups and stale pattern prices."""
        now = datetime.now(timezone.utc)
        
        # Cleanup active_setups
        to_remove = [
            setup_id for setup_id, timestamp in self.active_setups.items()
            if (now - timestamp).total_seconds() > max_age_seconds
        ]
        for setup_id in to_remove:
            del self.active_setups[setup_id]
        
        # Cleanup last_pattern_prices (entries older than expiry time)
        expiry_mins = self.config.get('entry_scanner', {}).get('reentry_time_expiry', 20)
        expiry_seconds = expiry_mins * 60
        pattern_keys_to_remove = []
        for pattern_key, data in self.last_pattern_prices.items():
            if isinstance(data, dict):
                last_time = data.get('time')
                if last_time and isinstance(last_time, datetime):
                    if (now - last_time).total_seconds() > expiry_seconds:
                        pattern_keys_to_remove.append(pattern_key)
        for key in pattern_keys_to_remove:
            del self.last_pattern_prices[key]
    
    def _track_entry_zone(self, entry_setup: Dict, entry_price: float, direction: str, zone_type: str = None):
        """Track entry zone by structure (OB/FVG ID) instead of time."""
        zone_id = self._get_zone_id(entry_setup, zone_type)
        if zone_id:
            self.active_zones[zone_id] = {
                'entry_price': entry_price,
                'direction': direction,
                'zone_type': zone_type,
                'timestamp': datetime.now(timezone.utc),
                'status': 'ACTIVE'  # ACTIVE, BROKEN, or EXPIRED
            }
            self.last_zone_id = zone_id
    
    def _is_in_recent_zone(self, entry_setup: Dict, current_price: float, direction: str, zone_type: str = None) -> bool:
        """
        Check if trying to enter same zone that hasn't been broken yet.
        
        Enhanced (D-02): Now checks OB/FVG boundary instead of just zone_id.
        This prevents duplicate signals when price is still within the same OB/FVG zone.
        """
        zone_id = self._get_zone_id(entry_setup, zone_type)
        
        if not zone_id:
            return False
        
        zone_expiry_minutes = self.config.get('zone_expiry_minutes', 30)
        
        if zone_id in self.active_zones:
            zone_data = self.active_zones[zone_id]
            
            if zone_data['direction'] == direction:
                zone_timestamp = zone_data.get('timestamp')
                if zone_timestamp:
                    elapsed_mins = (datetime.now(timezone.utc) - zone_timestamp).total_seconds() / 60
                    if elapsed_mins >= zone_expiry_minutes:
                        zone_data['status'] = 'EXPIRED'
                        return False
                
                if self._is_zone_broken(zone_data, current_price):
                    zone_data['status'] = 'BROKEN'
                    return False
                
                if self._is_price_in_zone_boundary(entry_setup, current_price, zone_type):
                    return True
                
                return True
            else:
                return False
        
        return False
    
    def _is_price_in_zone_boundary(self, entry_setup: Dict, current_price: float, zone_type: str) -> bool:
        """
        Check if current price is still within the OB/FVG zone boundary.
        D-02: Enhanced zone boundary check.
        """
        details = entry_setup.get('details', {})
        
        if zone_type == 'OB':
            zone_high = details.get('zone_high') or details.get('high')
            zone_low = details.get('zone_low') or details.get('low') or details.get('invalidation_level')
            
            if zone_high and zone_low:
                return zone_low <= current_price <= zone_high
                
        elif zone_type == 'FVG':
            zone_top = details.get('top') or details.get('zone_high')
            zone_bottom = details.get('bottom') or details.get('zone_low')
            
            if zone_top and zone_bottom:
                return zone_bottom <= current_price <= zone_top
        
        # FIX: Issue 4 - Support both institutional naming (LP, DB, DA) and legacy (SWEEP, WALL, ZONE)
        elif zone_type in ('SWEEP', 'LP'):
            sweep_level = details.get('sweep_level')
            if sweep_level:
                tolerance = 0.001
                return abs(current_price - sweep_level) / sweep_level < tolerance
        
        return False
    
    def _get_zone_id(self, entry_setup: Dict, zone_type: str = None) -> str:
        """Generate unique zone ID from zone boundaries.
        
        FIX: Issue 4 - Support both institutional naming (LP, DB, DA) and legacy (SWEEP, WALL, ZONE)
        """
        details = entry_setup.get('details', {})
        
        if zone_type == 'OB':
            invalidation = details.get('invalidation_level')
            if invalidation:
                return f"OB_{int(invalidation)}"
        elif zone_type == 'FVG':
            invalidation = details.get('invalidation_level')
            if invalidation:
                return f"FVG_{int(invalidation)}"
        elif zone_type in ('SWEEP', 'LP'):
            sweep_level = details.get('sweep_level')
            if sweep_level:
                return f"LP_{int(sweep_level)}"  # Updated to LP naming
        elif zone_type in ('WALL', 'DB'):
            wall_level = details.get('invalidation_level')
            if wall_level:
                return f"DB_{int(wall_level)}"  # Updated to DB naming
        
        # Fallback for ZONE type or when no details available
        if zone_type in ('ZONE', 'DA'):
            entry_price = entry_setup.get('entry_price', 0)
            if entry_price:
                return f"DA_{int(entry_price)}"  # Updated to DA naming
        
        # Generic fallback
        return f"GEN_{id(entry_setup)}"
        
    def _calculate_structural_tp(
        self,
        direction: str,
        entry_price: float,
        entry_type: str,
        pattern_type: str,
        candles: pd.DataFrame,
        poc_data: Optional[Dict] = None,
        sl_distance: float = 0
    ) -> Optional[float]:
        """
        Calculate Institutional Structural Take Profit targets.
        
        Methods:
        - SWEEP: Target opposite Swing High/Low (Major Liquidity)
        - WALL: Target POC (Volume Profile) or Range High/Low
        - ZONE: Target next Opposite Zone (OB/FVG Inner Edge)
        """
        if candles is None or candles.empty:
            return None
            
        ict_summary = self.ict.get_ict_summary(candles, entry_price)
        structure = ict_summary.get('structure', {})
        
        # Minimum acceptable RR for a structural target to be selected
        # If no structural target reaches this RR, we return None (triggering higher RR distance fallback)
        min_structural_rr = 1.3
        target_dist_required = sl_distance * min_structural_rr
        
        target = None
        
        if entry_type == 'SMART_FLOW':
            # FIX: Issue 4 - Metadata Naming (SWEEP -> LP, WALL -> DB, ZONE -> DA)
            if pattern_type == 'LP':
                # Target the MAJOR liquidity on opposite side
                potential = structure.get('last_high') if direction == 'LONG' else structure.get('last_low')
                if potential and abs(potential - entry_price) >= target_dist_required:
                    target = potential
                    
            elif pattern_type == 'DB':
                # Priority 1: POC (Magnet)
                if poc_data and poc_data.get('poc'):
                    poc_price = poc_data['poc']
                    if abs(poc_price - entry_price) >= target_dist_required:
                        target = poc_price
                
                # Priority 2: Major structure
                if target is None:
                    potential = structure.get('last_high') if direction == 'LONG' else structure.get('last_low')
                    if potential and abs(potential - entry_price) >= target_dist_required:
                        target = potential
                        
            elif pattern_type == 'DA':
                # DA Pattern: Target OB/FVG zones
                obs = ict_summary.get('order_blocks', {})
                fvgs = ict_summary.get('fvgs', [])
                
                if not isinstance(obs, dict):
                    obs = {}
                if not isinstance(fvgs, (dict, list)):
                    fvgs = []
                    
                potential_targets = []
                
                if direction == 'LONG':
                    bearish_obs = obs.get('bearish', []) if isinstance(obs, dict) else []
                    potential_targets.extend([ob['low'] for ob in bearish_obs if isinstance(ob, dict) and ob.get('low', 0) > entry_price])
                    
                    bearish_fvgs = fvgs if isinstance(fvgs, list) else fvgs.get('bearish', []) if isinstance(fvgs, dict) else []
                    # FVG uses 'top'/'bottom' keys, not 'type'/'high'/'low'
                    potential_targets.extend([fvg['bottom'] for fvg in bearish_fvgs if isinstance(fvg, dict) and fvg.get('bottom', 0) > entry_price])
                    # Sort ascending
                    sorted_targets = sorted(list(set(potential_targets)))
                else:
                    # Handle both dict and string formats
                    bullish_obs = obs.get('bullish', []) if isinstance(obs, dict) else []
                    potential_targets.extend([ob['high'] for ob in bullish_obs if isinstance(ob, dict) and 'high' in ob and ob['high'] < entry_price])
                    
                    # Handle FVGs - FVG uses 'top'/'bottom' keys
                    bullish_fvgs = fvgs.get('bullish', []) if isinstance(fvgs, dict) else []
                    potential_targets.extend([fvg['top'] for fvg in bullish_fvgs if isinstance(fvg, dict) and fvg.get('top', 0) < entry_price])
                    # Sort descending
                    sorted_targets = sorted(list(set(potential_targets)), reverse=True)
                
                # Find first target that satisfies RR
                for target_price in sorted_targets:
                    if abs(target_price - entry_price) >= target_dist_required:
                        target = target_price
                        break
                
                # Priority 3: Major structure fallback (if RR satisfied)
                if target is None:
                    potential = structure.get('last_high') if direction == 'LONG' else structure.get('last_low')
                    if potential and abs(potential - entry_price) >= target_dist_required:
                        target = potential
        
        # Final Safety Check: If no structural target found that meets RR, return None 
        # so the caller falls back to distance-based (mode_rr) calculation.
        if target:
            # Directional validation
            if direction == 'LONG' and target <= entry_price: return None
            if direction == 'SHORT' and target >= entry_price: return None
            return float(target)
            
        return None

    # Helper helper...
    
    def _is_zone_broken(self, zone_data: Dict, current_price: float) -> bool:
        """Check if a zone has been broken by price."""
        zone_type = zone_data.get('type')
        zone_direction = zone_data.get('direction')
        
        if zone_type == 'OB':
            if zone_direction == 'BULLISH':
                return current_price < zone_data.get('invalidation_level', 0)
            else:
                return current_price > zone_data.get('invalidation_level', 0)
        elif zone_type == 'FVG':
            if zone_direction == 'BULLISH':
                return current_price < zone_data.get('bottom', 0)
            else:
                return current_price > zone_data.get('top', 0)
        elif zone_type == 'SWEEP':
            tolerance = 0.001
            sweep_level = zone_data.get('sweep_level', 0)
            return abs(current_price - sweep_level) / sweep_level < tolerance
        elif zone_type == 'LP':
            # LP (Liquidity Purge) uses same logic as SWEEP
            tolerance = 0.001
            sweep_level = zone_data.get('sweep_level', 0)
            return abs(current_price - sweep_level) / sweep_level < tolerance
        
        return False

    def _estimate_position_state(self, signal_id: str, entry_price: float, direction: str) -> str:
        """
        V-01: Estimate position profitability from TrailingPosition state (P&L proxy).
        
        Returns: 'LIKELY_PROFIT' | 'BREAKEVEN' | 'LIKELY_LOSS' | 'UNKNOWN'
        """
        if not hasattr(self, 'trailing_stop_manager') or signal_id not in self.trailing_stop_manager.positions:
            return 'UNKNOWN'
        
        pos = self.trailing_stop_manager.positions[signal_id]
        
        # Level 1: Trail activated = เคยกำไร >= activation_profit_pct
        if pos.activated and pos.trail_count >= 2:
            return 'LIKELY_PROFIT'      # trail หลายครั้ง = กำไรมาก
        
        if pos.activated and pos.trail_count >= 1:
            return 'BREAKEVEN'          # trail ครั้งแรก = อาจ BE แล้ว
        
        # Level 2: SL ที่ breakeven หรือดีกว่า
        if direction == 'LONG' and pos.current_sl >= entry_price:
            return 'BREAKEVEN'
        if direction == 'SHORT' and pos.current_sl <= entry_price:
            return 'BREAKEVEN'
        
        # Level 3: Peak profit เคยสูง
        if direction == 'LONG':
            peak_pct = (pos.peak_price - entry_price) / entry_price * 100
        else:
            peak_pct = (entry_price - pos.peak_price) / entry_price * 100
        
        if peak_pct >= 0.4:
            return 'LIKELY_PROFIT'      # เคยกำไร >= 0.4%
        if peak_pct >= 0.1:
            return 'BREAKEVEN'
        
        return 'LIKELY_LOSS'

    # Section 23: Removed churn tracking methods
    # - _record_direction_switch (was using _opposite_switch_log)
    # - _get_churn_level (was using _opposite_switch_log)
    
    def _calculate_reversal_confidence(
        self,
        new_direction: str,
        m5_structure: str,
        m5_structure_dir: str,
        cvd_divergence: bool,
        oi_divergence: bool,
        volume_exhaustion: bool,
        liquidity_ahead: bool,
        pattern_type: str,
    ) -> int:
        """
        V-02: คำนวณ Reversal Confidence Score (0-11) จาก M5 structure + indicators
        """
        rcs = 0
        
        # M5 Structure weight
        # Only count if structure direction matches new signal direction
        struct_matches = (
            (new_direction == 'LONG' and m5_structure_dir == 'BULLISH') or
            (new_direction == 'SHORT' and m5_structure_dir == 'BEARISH')
        )
        if struct_matches:
            rcs += struct_w
        else:
            rcs -= 1  # Structure matches direction? No -> penalty
        
        # Supporting indicators
        if cvd_divergence:     rcs += 2
        if oi_divergence:      rcs += 2
        if volume_exhaustion:  rcs += 1
        if liquidity_ahead:    rcs += 1
        # FIX: Issue 4 - Support both institutional naming (LP) and legacy (SWEEP)
        if pattern_type in ('SWEEP', 'LP'): rcs += 1
        
        return max(0, rcs)  # RCS range 0-11
    
    def _is_h1_weakening(self, htf_analysis: Dict, signal_direction: str) -> bool:
        """
        Check if H1 trend is showing signs of weakening/reversal.
        """
        if not htf_analysis:
            return False
        
        h1_structure = htf_analysis.get('structure_type', '')
        h1_trend = htf_analysis.get('trend')
        h1_trend_str = h1_trend.value if h1_trend else 'RANGE'
        
        # Check CHoCH pending
        if 'CHOCH_PENDING' in str(h1_structure):
            return True
        
        # Check if H1 BOS opposes current H1 trend
        h1_last_bos = htf_analysis.get('last_bos_direction', '')
        if h1_trend_str == 'BULLISH' and signal_direction == 'SHORT':
            if h1_last_bos == 'BEARISH':
                return True
        elif h1_trend_str == 'BEARISH' and signal_direction == 'LONG':
            if h1_last_bos == 'BULLISH':
                return True
        
        return False
    
    # Section 23: Removed _evaluate_opposite_action_v3 (now handled by EA directly)
    
    def _cleanup_broken_zones(self):
        """Remove broken zones from tracking."""
        to_remove = []
        for zone_id, zone_data in self.active_zones.items():
            if zone_data.get('status') == 'BROKEN':
                to_remove.append(zone_id)
        
        for zone_id in to_remove:
            del self.active_zones[zone_id]
    
    def _determine_zone_type(self, entry_setup: Dict, entry_type: str) -> str:
        """
        Determine zone type from entry setup.
        
        FIX: Issue 4 - Support both institutional naming (LP, DB, DA) and legacy (SWEEP, WALL, ZONE)
        """
        if entry_type == 'ISF_ENTRY':
            mode = entry_setup.get('mode', '')
            if mode == 'ISF_INST':
                return 'OB'  # or FVG - simplified
            elif mode == 'ISF_SWEEP':
                return 'LP'  # Updated: SWEEP -> LP
            elif mode == 'ISF_WALL':
                return 'DB'  # Updated: WALL -> DB
        
        # Check reason - support both old and new naming
        reason = entry_setup.get('reason', '')
        if 'OB' in reason:
            return 'OB'
        elif 'FVG' in reason:
            return 'FVG'
        elif 'SWEEP' in reason or 'LP' in reason:
            return 'LP'  # Updated: SWEEP -> LP
        elif 'WALL' in reason or 'DB' in reason:
            return 'DB'  # Updated: WALL -> DB
        elif 'ZONE' in reason or 'DA' in reason or 'CVD' in reason:
            return 'DA'  # Updated: ZONE -> DA
        
        return 'DA'  # Updated: Default to DA (formerly ZONE)
    
    def _log_blocked_signal(self, entry_setup: Dict, direction: str, current_price: float, reason: str):
        """
        Log blocked signals for analytics (D-06).
        This helps track why signals are blocked for later analysis.
        """
        blocked_record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'direction': direction,
            'entry_price': current_price,
            'score': entry_setup.get('score', 0),
            'entry_type': entry_setup.get('entry_type', 'UNKNOWN'),
            'pattern_type': entry_setup.get('pattern_type', 'UNKNOWN'),
            'block_reason': reason,
            'details': entry_setup.get('details', {})
        }
        
        try:
            blocked_file = Path("data/blocked_signals.json")
            blocked_file.parent.mkdir(parents=True, exist_ok=True)
            
            existing = []
            if blocked_file.exists():
                with open(blocked_file, 'r') as f:
                    existing = json.load(f)
            
            existing.append(blocked_record)
            
            with open(blocked_file, 'w') as f:
                json.dump(existing[-500:], f, indent=2)
        except Exception as e:
            logger.debug(f"Could not log blocked signal: {e}")
    
    def _get_last_same_direction_signal(self, direction: str) -> Optional[Dict]:
        """
        Get the last signal with the same direction for score comparison (D-03).
        Used to determine if a new signal should be allowed as an upgrade.
        """
        for setup_id in reversed(list(self.active_setups.keys())):
            if direction and direction.upper() in setup_id.upper():
                setup_time = self.active_setups[setup_id]
                if (datetime.now(timezone.utc) - setup_time).total_seconds() < 3600:
                    return {
                        'setup_id': setup_id,
                        'score': 0,
                        'timestamp': setup_time
                    }
        return None
        """Get current bot state."""
        return self.bot_state
    
    def get_bot_state_dict(self) -> Dict:
        """Get bot state as dictionary."""
        return self.bot_state.get_state_dict()
    
    def update_trailing_stop(
        self,
        signal_id: str,
        current_price: float,
        candles: pd.DataFrame = None
    ) -> Dict:
        """
        Update trailing stop for an active position.
        
        Args:
            signal_id: Signal ID to update
            current_price: Current market price
            candles: OHLCV DataFrame for structure-based trailing
        
        Returns:
            Trailing update result
        """
        if not self.use_trailing_stop:
            return {'updated': False, 'reason': 'Trailing stop disabled'}
        
        atr = None
        if candles is not None and len(candles) >= 14:
            atr = self._calculate_atr_distance(candles)
        
        return self.trailing_stop_manager.update(
            signal_id=signal_id,
            current_price=current_price,
            candles=candles,
            atr=atr,
            poc=self.last_poc_data.get('poc', 0) if self.last_poc_data else 0,
            liquidity_walls=self.last_liquidity_wall_data
        )
    
    def remove_trailing_position(self, signal_id: str) -> bool:
        """Remove position from trailing stop tracking."""
        return self.trailing_stop_manager.remove_position(signal_id)
    
    def get_htf_analysis(self) -> Optional[Dict]:
        """Get last HTF analysis result."""
        return self.last_htf_analysis
    
    def get_trailing_statistics(self) -> Dict:
        """Get trailing stop statistics."""
        return self.trailing_stop_manager.get_statistics()

    def _check_killzone(self) -> Tuple[bool, str]:
        """Check if current time is within a Killzone."""
        now = datetime.now(timezone.utc).time()
        
        # Killzones in UTC
        killzones = {
            'ASIA': (datetime.strptime("00:00", "%H:%M").time(), datetime.strptime("09:00", "%H:%M").time()),
            'LONDON': (datetime.strptime("08:00", "%H:%M").time(), datetime.strptime("17:00", "%H:%M").time()),
            'NY': (datetime.strptime("13:00", "%H:%M").time(), datetime.strptime("22:00", "%H:%M").time())
        }
        
        for name, (start, end) in killzones.items():
            if start <= now <= end:
                return True, name
                
        return False, ""
    
    # v3.3: Logistic Regression Trade Recording
    def record_trade_outcome(self, signal: 'Signal', outcome: str, market_data: Dict) -> bool:
        """
        Record trade outcome for LR model training.
        
        Args:
            signal: The signal that was traded
            outcome: 'WIN' or 'LOSS'
            market_data: Market conditions at entry
        
        Returns:
            True if recorded successfully
        """
        if not hasattr(self, 'lr_model') or not self.lr_model:
            return False
        
        try:
            metadata = signal.metadata or {}
            
            # Get all Flow-First features from metadata
            signal_data = {
                'pattern_type': metadata.get('entry_type', 'UNKNOWN'),
                'score': metadata.get('score', 0),
                'is_trend_aligned': metadata.get('is_trend_aligned', True),
                'entry_position_score': metadata.get('score', 0) // 5,
                'zone_quality': metadata.get('score', 0) // 4,
                # New Flow-First features
                'delta_divergence_signal': metadata.get('delta_divergence_signal', 0),
                'ict_confluence_score': metadata.get('ict_confluence_score', 0),
                'structure_state': metadata.get('structure_state', 0),
                'regime_type': metadata.get('regime_type', 0),
                'proximity_to_liquidity': metadata.get('proximity_to_liquidity', 0.5)
            }
            
            # Enrich market_data with flow features if not present
            enriched_market_data = market_data.copy() if market_data else {}
            if 'oi_shock' not in enriched_market_data:
                enriched_market_data['oi_shock'] = metadata.get('oi_shock', 0)
            if 'cvd_aggression' not in enriched_market_data:
                enriched_market_data['cvd_aggression'] = metadata.get('cvd_aggression', 0)
            if 'volume_surge_ratio' not in enriched_market_data:
                enriched_market_data['volume_surge_ratio'] = metadata.get('volume_surge_ratio', 1)
            if 'wall_imbalance' not in enriched_market_data:
                enriched_market_data['wall_imbalance'] = metadata.get('wall_imbalance', 0)
            if 'funding_bias' not in enriched_market_data:
                enriched_market_data['funding_bias'] = metadata.get('funding_bias', 0)
            
            return self.lr_model.record_trade(signal_data, enriched_market_data, outcome)
            
        except Exception as e:
            logger.debug(f"Error recording trade outcome: {e}")
            return False
    
    def _map_structure_to_number(self, structure: str) -> int:
        """Map structure string to number for LR model."""
        structure_map = {
            'BOS': 3,
            'CHoCH': 2,
            'CHoCH_PENDING': 1,
            'NONE': 0,
            'BULL': 1,
            'BEAR': 1,
            'RANGE': 0,
            'CONTRACTING': 0
        }
        return structure_map.get(structure.upper() if isinstance(structure, str) else 'NONE', 0)
    
    def _calculate_required_rr(self, pattern_type: str) -> float:
        """
        TASK 2: Calculate required RR based on pattern type.
        
        Architecture Plan Section 3.2 Progressive Trailing Adjustment:
        - LP Pattern: รันเทรนยาว (required_rr = 1.0)
        - DB Pattern: เก็บกำไรเร็ว (required_rr = 1.0)
        - DA Pattern: ความแม่นยำ (required_rr = 1.1)
        
        Returns:
            float: Required Risk-Reward ratio
        """
        required_rr_map = {
            # Institutional naming
            'LP': 1.2,
            'DB': 1.0,
            'DA': 1.1,
            # Legacy naming (backward compatibility)
            'SWEEP': 1.8,
            'WALL': 1.2,
            'ZONE': 1.1,
            'OI_MOM': 1.8,
            'CVD_REV': 1.1,
            # Default
            'UNKNOWN': 1.5
        }
        return required_rr_map.get(pattern_type, 1.1)
    
    def _calculate_be_trigger_pct(self, institutional_grade: bool) -> float:
        """
        Architecture Plan Section 3.2: Institutional Breakeven+
        
        - institutional_grade == true: BreakevenTriggerPct = 0.2%
        - institutional_grade == false: BreakevenTriggerPct = 0.4%
        
        Returns:
            float: Breakeven trigger percentage
        """
        return 0.2 if institutional_grade else 0.4
    
    def _calculate_trailing_config(self, pattern_type: str) -> Dict:
        """
        Architecture Plan Section 3.2: Progressive Trailing Adjustment
        
        Pattern-specific trailing stop configuration:
        - LP Pattern: รันเทรนยาว (Stage 4 Lock 70% ที่กำไร 1.8%)
        - DB Pattern: เก็บกำไรเร็ว (Stage 4 Lock 80% ที่กำไร 1.0%)
        - DA Pattern: ความแม่นยำ (Stage 4 Lock 75% ที่กำไร 1.5%)
        
        Returns:
            Dict: Trailing stage configuration for EA
        """
        trailing_config_map = {
            # Institutional naming
            'LP': {
                'lock_pct': 0.70,           # Lock 70% at trigger
                'trigger_profit_pct': 1.8,  # Trigger at 1.8% profit
                'mode': 'TRAILING_LONG',     # Run trend mode
                'description': 'LP_LARGE_MOVE'
            },
            'DB': {
                'lock_pct': 0.80,           # Lock 80% at trigger
                'trigger_profit_pct': 1.0,   # Trigger at 1.0% profit
                'mode': 'QUICK_EXIT',        # Quick profit mode
                'description': 'DB_FAST_PROFIT'
            },
            'DA': {
                'lock_pct': 0.75,           # Lock 75% at trigger
                'trigger_profit_pct': 1.5,   # Trigger at 1.5% profit
                'mode': 'BALANCED',          # Balanced mode
                'description': 'DA_PRECISION'
            },
            # Legacy naming (backward compatibility)
            'SWEEP': {
                'lock_pct': 0.70,
                'trigger_profit_pct': 1.8,
                'mode': 'TRAILING_LONG',
                'description': 'LP_LARGE_MOVE'
            },
            'WALL': {
                'lock_pct': 0.80,
                'trigger_profit_pct': 1.0,
                'mode': 'QUICK_EXIT',
                'description': 'DB_FAST_PROFIT'
            },
            'ZONE': {
                'lock_pct': 0.75,
                'trigger_profit_pct': 1.5,
                'mode': 'BALANCED',
                'description': 'DA_PRECISION'
            },
            'OI_MOM': {
                'lock_pct': 0.70,
                'trigger_profit_pct': 1.8,
                'mode': 'TRAILING_LONG',
                'description': 'LP_LARGE_MOVE'
            },
            'CVD_REV': {
                'lock_pct': 0.75,
                'trigger_profit_pct': 1.5,
                'mode': 'BALANCED',
                'description': 'DA_PRECISION'
            }
        }
        return trailing_config_map.get(pattern_type, {
            'lock_pct': 0.75,
            'trigger_profit_pct': 1.5,
            'mode': 'BALANCED',
            'description': 'DEFAULT'
        })
    
    def _map_regime_to_number(self, regime: str) -> int:
        """Map regime string to number for LR model."""
        regime_map = {
            'TRENDING': 2,
            'RANGING': 1,
            'VOLATILE': 3,
            'NORMAL': 0
        }
        return regime_map.get(regime.upper() if isinstance(regime, str) else 'NORMAL', 0)
    
    def _map_divergence_to_number(self, divergence: str) -> int:
        """Map divergence string to number for LR model."""
        divergence_map = {
            'BULLISH': 1,
            'BEARISH': 2,
            'NONE': 0
        }
        return divergence_map.get(divergence.upper() if isinstance(divergence, str) else 'NONE', 0)
    
    def get_lr_model_stats(self) -> Dict:
        """Get LR model statistics."""
        if not hasattr(self, 'lr_model') or not self.lr_model:
            return {'error': 'Model not initialized'}
        
        return self.lr_model.get_model_stats()




