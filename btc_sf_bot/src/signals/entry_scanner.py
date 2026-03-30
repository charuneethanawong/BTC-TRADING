"""
Entry Setup Scanner Module
Scans for optimal entry points after structure confirmation
"""
from typing import Dict, List, Optional, Tuple

from ..utils.logger import get_logger
from ..enums import TrendState, EntryType

logger = get_logger(__name__)


class EntrySetupScanner:
    """
    Scans for optimal entry points after structure confirmation.
    
    Scoring Factors (Total 0-10):
    1. Price in Discount/Premium Zone (+2 pts)
    2. Touched Order Block (+2 pts)
    3. Touched FVG (+1 pt)
    4. Sweep Liquidity confirmed (+2 pts)
    5. Order Flow supports (+2 pts)
    6. Structure Quality bonus (+1 pt)
    
    Minimum Entry Score: 7
    
    EMA 50 Role (NOT in scoring):
    - EMA aligns with BOS → TP Multiplier = 1.0x
    - EMA opposes BOS → TP Multiplier = 0.7x
    """
    
    def __init__(self, config: Dict = None):
        """
        Initialize EntrySetupScanner.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        
        self.min_entry_score = self.config.get('min_entry_score', 6)  # Lowered from 7
        self.ob_min_quality = self.config.get('ob_min_quality', 1)
        self.sweep_min_quality = self.config.get('sweep_min_quality', 2)
        self.wall_min_strength = self.config.get('wall_min_strength', 1)
        self.poc_distance_threshold = self.config.get('poc_distance_threshold', 0.5) # 0.5%
        self.structure_quality_bonus_threshold = self.config.get('structure_quality_bonus_threshold', 8)
    
    def scan(
        self,
        bot_state: BotState,
        candles,
        current_price: float,
        analysis: Dict,
        htf_trend: str = 'RANGE'
    ) -> Dict:
        """
        Scan for entry setup.
        
        Args:
            bot_state: Current bot state
            candles: OHLCV DataFrame
            current_price: Current price
            analysis: Market analysis results
            htf_trend: HTF trend from EMA 50 (for TP adjustment only)
        
        Returns:
            {
                'found': bool,
                'score': 0-10,
                'entry_type': EntryType,
                'entry_price': float,
                'reasons': [...],
                'tp_multiplier': float,
                'is_trend_aligned': bool
            }
        """
        if not bot_state.can_look_for_entry():
            return self._no_entry_result('Bot not ready for entry (no confirmed structure)')
        
        direction = bot_state.get_entry_direction()
        
        if direction is None:
            return self._no_entry_result('No entry direction')
        
        score = 0
        reasons = []
        entry_type = None
        entry_price = current_price
        
        ict = analysis.get('ict', {})
        order_flow = analysis.get('order_flow', {})
        zone_context = analysis.get('zone_context', 'RANGE')
        
        # Factor 1: Price in Zone (+2 pts)
        zone_score, zone_reason = self._check_zone(direction, zone_context)
        score += zone_score
        if zone_reason:
            reasons.append(zone_reason)
        
        # Factor 2: Touched Order Block (+2 pts)
        ob_score, ob_reason, ob_type, ob_price = self._check_order_block(
            direction, ict, current_price
        )
        score += ob_score
        if ob_reason:
            reasons.append(ob_reason)
        if ob_type:
            entry_type = ob_type
            entry_price = ob_price
        
        # Factor 3: Touched FVG (+1 pt)
        if not entry_type:
            fvg_score, fvg_reason, fvg_price = self._check_fvg(direction, ict, current_price)
            score += fvg_score
            if fvg_reason:
                reasons.append(fvg_reason)
            if fvg_price:
                entry_type = EntryType.FVG_ENTRY
                entry_price = fvg_price
        
        # Factor 4: Sweep Liquidity (+2 pts)
        sweep_score, sweep_reason = self._check_sweep(direction, ict)
        score += sweep_score
        if sweep_reason:
            reasons.append(sweep_reason)
        if not entry_type and sweep_score > 0:
            entry_type = EntryType.SWEEP_ENTRY
        
        # Factor 5: Order Flow supports (+2 pts)
        of_score, of_reasons = self._check_order_flow(direction, order_flow)
        score += of_score
        reasons.extend(of_reasons)
        
        # Factor 6: Structure Quality bonus (+1 pt)
        struct_score, struct_reason = self._check_structure_quality(bot_state)
        score += struct_score
        if struct_reason:
            reasons.append(struct_reason)
            
        # Factor 7: POC Support (+1 pt) [NEW]
        poc_score, poc_reason = self._check_poc_support(direction, analysis, current_price)
        score += poc_score
        if poc_reason:
            reasons.append(poc_reason)
            
        # Factor 8: Liquidity Walls (+2 pts) [NEW]
        wall_score, wall_reason = self._check_liquidity_walls(direction, analysis)
        score += wall_score
        if wall_reason:
            reasons.append(wall_reason)
        
        # Default entry type if none set
        if not entry_type:
            entry_type = EntryType.ZONE_ENTRY
        
        # EMA 50 alignment (for TP adjustment only, NOT scoring)
        is_trend_aligned = self._check_trend_alignment(direction, htf_trend)
        tp_multiplier = 1.0 if is_trend_aligned else 0.7
        
        # Decision
        found = score >= self.min_entry_score
        
        # Get sl_boundary from the same source as entry_price
        sl_boundary = None
        if entry_type == EntryType.OB_ENTRY:
             # For OB: SL is at the other side of the block
             sl_boundary = getattr(self, '_last_ob_boundary', entry_price)
        elif entry_type == EntryType.FVG_ENTRY:
             sl_boundary = getattr(self, '_last_fvg_boundary', entry_price)
        elif entry_type == EntryType.SWEEP_ENTRY:
             sl_boundary = getattr(self, '_last_sweep_boundary', entry_price)
        
        result = {
            'found': found,
            'is_aggressive': False,
            'score': score,
            'entry_type': entry_type.value if entry_type else None,
            'entry_price': entry_price,
            'sl_boundary': sl_boundary,
            'reasons': reasons,
            'tp_multiplier': tp_multiplier,
            'is_trend_aligned': is_trend_aligned,
            'direction': direction
        }
        
        if found:
            logger.info(
                f"Entry Setup Found: {direction} | Score: {score}/10 | "
                f"Type: {entry_type.value if entry_type else 'N/A'} | "
                f"Entry: {entry_price} | TP Mult: {tp_multiplier}x"
            )
        else:
            logger.debug(f"No entry: Score {score} < {self.min_entry_score}")
        
        return result

    
    def _check_zone(self, direction: str, zone_context: str) -> Tuple[int, str]:
        """Check price zone context."""
        if direction == 'LONG':
            if zone_context == 'DISCOUNT':
                return 2, 'ZONE_DISC'
        else:
            if zone_context == 'PREMIUM':
                return 2, 'ZONE_PREM'
        return 0, ''
    
    def _check_order_block(
        self,
        direction: str,
        ict: Dict,
        current_price: float
    ) -> Tuple[int, str, Optional[EntryType], float]:
        """Check if price touched order block."""
        obs = ict.get('order_blocks', {})
        
        if direction == 'LONG':
            bullish_obs = obs.get('bullish', [])
            quality_obs = [
                ob for ob in bullish_obs
                if ob.get('quality', 0) >= self.ob_min_quality
                and ob.get('low', 0) <= current_price <= ob.get('high', float('inf'))
            ]
            
            if quality_obs:
                best_ob = max(quality_obs, key=lambda x: x.get('quality', 0))
                ob_quality = best_ob.get('quality', 0)
                entry_price = best_ob.get('high', current_price)
                # Store boundary for later use
                self._last_ob_boundary = best_ob.get('low', entry_price)
                return 2, f'OB_BULL_Q{ob_quality}', EntryType.OB_ENTRY, entry_price
        else:
            bearish_obs = obs.get('bearish', [])
            quality_obs = [
                ob for ob in bearish_obs
                if ob.get('quality', 0) >= self.ob_min_quality
                and ob.get('low', 0) <= current_price <= ob.get('high', float('inf'))
            ]
            
            if quality_obs:
                best_ob = max(quality_obs, key=lambda x: x.get('quality', 0))
                ob_quality = best_ob.get('quality', 0)
                entry_price = best_ob.get('low', current_price)
                # Store boundary for later use
                self._last_ob_boundary = best_ob.get('high', entry_price)
                return 2, f'OB_BEAR_Q{ob_quality}', EntryType.OB_ENTRY, entry_price
        
        return 0, '', None, current_price
    
    def _check_fvg(
        self,
        direction: str,
        ict: Dict,
        current_price: float
    ) -> Tuple[int, str, Optional[float]]:
        """Check if price touched FVG."""
        fvgs = ict.get('fvgs', {})
        
        if direction == 'LONG':
            bullish_fvgs = fvgs.get('bullish', [])
            for fvg in bullish_fvgs:
                bottom = fvg.get('bottom', 0)
                top = fvg.get('top', 0)
                if bottom <= current_price <= top:
                    mid = fvg.get('mid', (bottom + top) / 2)
                    self._last_fvg_boundary = bottom
                    return 1, 'FVG_BULL', mid
        else:
            bearish_fvgs = fvgs.get('bearish', [])
            for fvg in bearish_fvgs:
                bottom = fvg.get('bottom', 0)
                top = fvg.get('top', 0)
                if bottom <= current_price <= top:
                    mid = fvg.get('mid', (bottom + top) / 2)
                    self._last_fvg_boundary = top
                    return 1, 'FVG_BEAR', mid
        
        return 0, '', None
    
    def _check_sweep(self, direction: str, ict: Dict) -> Tuple[int, str]:
        """Check if liquidity sweep occurred."""
        sweep = ict.get('liquidity_sweep', {})
        sweep_type = sweep.get('type', '')
        sweep_quality = sweep.get('quality', 0)
        
        if sweep_quality < self.sweep_min_quality:
            return 0, ''
        
        if direction == 'LONG':
            if sweep_type == 'SWEEP_LOW':
                self._last_sweep_boundary = sweep.get('sweep_level', 0)
                return 2, f'SWEEP_LOW_Q{sweep_quality}'
        else:
            if sweep_type == 'SWEEP_HIGH':
                self._last_sweep_boundary = sweep.get('sweep_level', 0)
                return 2, f'SWEEP_HIGH_Q{sweep_quality}'
        
        return 0, ''
    
    def _check_order_flow(
        self,
        direction: str,
        order_flow: Dict
    ) -> Tuple[int, List[str]]:
        """Check if order flow supports direction."""
        score = 0
        reasons = []
        
        imbalance = order_flow.get('imbalance_direction', '')
        cvd_trend = order_flow.get('cvd_trend', '')
        
        if direction == 'LONG':
            if imbalance == 'BULLISH':
                score += 1
                reasons.append('OF_BULL')
            if cvd_trend == 'BULLISH':
                score += 1
                reasons.append('CVD_BULL')
        else:
            if imbalance == 'BEARISH':
                score += 1
                reasons.append('OF_BEAR')
            if cvd_trend == 'BEARISH':
                score += 1
                reasons.append('CVD_BEAR')
        
        return score, reasons
    
    def _check_structure_quality(self, bot_state: BotState) -> Tuple[int, str]:
        """Check structure quality for bonus."""
        quality = bot_state.structure_quality
        
        if quality >= self.structure_quality_bonus_threshold:
            return 1, f'STRUCT_Q{quality}'
        
        return 0, ''
    
    def _check_trend_alignment(self, direction: str, htf_trend: str) -> bool:
        """
        Check if HTF trend aligns with entry direction.
        
        Note: This does NOT affect scoring, only TP multiplier.
        """
        if htf_trend == 'RANGE':
            return True
        
        if direction == 'LONG':
            return htf_trend == 'BULLISH'
        else:
            return htf_trend == 'BEARISH'
    
    def _no_entry_result(self, reason: str) -> Dict:
        """Return no entry result."""
        return {
            'found': False,
            'score': 0,
            'entry_type': None,
            'entry_price': 0,
            'reasons': [reason],
            'tp_multiplier': 1.0,
            'is_trend_aligned': True,
            'direction': None
        }
    
    def _check_poc_support(self, direction: str, analysis: Dict, current_price: float) -> Tuple[int, str]:
        """Check if price is near Point of Control (POC)."""
        vp = analysis.get('volume_profile', {})
        poc = vp.get('poc', 0)
        
        if poc <= 0:
            return 0, ''
            
        distance_pct = abs(current_price - poc) / current_price * 100
        
        if distance_pct <= self.poc_distance_threshold:
            return 1, 'POC_SUPP'
            
        return 0, ''

    def _check_liquidity_walls(self, direction: str, analysis: Dict) -> Tuple[int, str]:
        """Check for liquidity wall support/resistance."""
        walls = analysis.get('liquidity_walls', {}) 
        if not walls:
            return 0, ''
            
        if direction == 'LONG':
            wall = walls.get('nearest_bid_wall')
            if wall and wall.strength >= self.wall_min_strength:
                # Stronger wall gives more points
                points = 2 if wall.strength >= 2 else 1
                return points, f'WALL_BID_Q{wall.strength}'
        else:
            wall = walls.get('nearest_ask_wall')
            if wall and wall.strength >= self.wall_min_strength:
                points = 2 if wall.strength >= 2 else 1
                return points, f'WALL_ASK_Q{wall.strength}'
                
        return 0, ''

    def get_min_score(self) -> int:
        """Get minimum entry score required."""
        return self.min_entry_score
