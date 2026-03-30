"""
Confluence Checker Module
"""
from typing import Dict, List, Tuple
import pandas as pd

from ..utils.logger import get_logger

logger = get_logger(__name__)


class ConfluenceChecker:
    """Check confluence of multiple factors."""
    
    def __init__(self, config: Dict = None):
        """
        Initialize Confluence Checker.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config or {}
        self.min_confluence_score = self.config.get('min_confluence_score', 60)
    
    def calculate_confluence(
        self,
        order_flow: Dict = None,
        volume_profile: Dict = None,
        ict: Dict = None,
        structure: Dict = None
    ) -> Tuple[int, List[str]]:
        """
        Calculate confluence score from multiple factors.
        
        Args:
            order_flow: Order flow analysis
            volume_profile: Volume profile analysis
            ict: ICT analysis
            structure: Market structure
        
        Returns:
            Tuple of (score, factors)
        """
        score = 0
        factors = []
        
        # Order Flow Factors (0-30 points)
        if order_flow:
            if order_flow.get('imbalance_direction') == 'BULLISH':
                score += 10
                factors.append('OF:BULLISH_IB')
            elif order_flow.get('imbalance_direction') == 'BEARISH':
                score += 10
                factors.append('OF:BEARISH_IB')
            
            delta = order_flow.get('delta', 0)
            if delta > 0:
                score += 10
                factors.append('OF:POSITIVE_DELTA')
            elif delta < 0:
                score += 10
                factors.append('OF:NEGATIVE_DELTA')
            
            if order_flow.get('buy_pct', 50) > 60:
                score += 5
                factors.append('OF:BUYING_PRESSURE')
            elif order_flow.get('sell_pct', 50) > 60:
                score += 5
                factors.append('OF:SELLING_PRESSURE')
        
        # Volume Profile Factors (0-20 points)
        if volume_profile:
            if volume_profile.get('hvns'):
                score += 10
                factors.append('VP:HVN')
            
            shape = volume_profile.get('shape', 'UNKNOWN')
            if shape == 'P_SHAPE':
                score += 10
                factors.append('VP:P_SHAPE')
            elif shape == 'B_SHAPE':
                score += 10
                factors.append('VP:B_SHAPE')
        
        # ICT Factors (0-30 points)
        if ict:
            zone = ict.get('zone_context', 'RANGE')
            if zone == 'DISCOUNT':
                score += 15
                factors.append('ICT:DISCOUNT')
            elif zone == 'PREMIUM':
                score += 15
                factors.append('ICT:PREMIUM')
            
            # Liquidity Sweep
            sweep = ict.get('liquidity_sweep', {})
            if sweep.get('type') == 'SWEEP_LOW':
                score += 15
                factors.append('ICT:SWEEP_LOW')
            elif sweep.get('type') == 'SWEEP_HIGH':
                score += 15
                factors.append('ICT:SWEEP_HIGH')
        
        # Structure Factors (0-20 points)
        if structure:
            trend = structure.get('trend', 'UNKNOWN')
            if trend == 'BULLISH':
                score += 20
                factors.append('STRUCT:BULLISH')
            elif trend == 'BEARISH':
                score += 20
                factors.append('STRUCT:BEARISH')
            
            struct_type = structure.get('structure', 'UNKNOWN')
            if struct_type == 'TRENDING':
                score += 10
                factors.append('STRUCT:TRENDING')
        
        return score, factors
    
    def check_alignment(
        self,
        analysis: Dict,
        direction: str
    ) -> Tuple[bool, int, List[str]]:
        """
        Check if factors align for given direction.
        
        Args:
            analysis: Market analysis results
            direction: 'LONG' or 'SHORT'
        
        Returns:
            Tuple of (aligned, score, factors)
        """
        score, factors = self.calculate_confluence(
            order_flow=analysis.get('order_flow'),
            volume_profile=analysis.get('volume_profile'),
            ict=analysis.get('ict'),
            structure=analysis.get('structure')
        )
        
        # For LONG, prefer bullish factors
        # For SHORT, prefer bearish factors
        if direction == 'LONG':
            bullish_factors = [f for f in factors if 'BULLISH' in f or 'DISCOUNT' in f or 'POSITIVE' in f]
            if len(bullish_factors) >= 2:
                aligned = True
            else:
                aligned = score >= self.min_confluence_score
        else:  # SHORT
            bearish_factors = [f for f in factors if 'BEARISH' in f or 'PREMIUM' in f or 'NEGATIVE' in f]
            if len(bearish_factors) >= 2:
                aligned = True
            else:
                aligned = score >= self.min_confluence_score
        
        return aligned, score, factors
    
    def filter_signals(
        self,
        signals: List[Dict],
        min_confluence: int = None
    ) -> List[Dict]:
        """
        Filter signals based on confluence.
        
        Args:
            signals: List of signal dictionaries
            min_confluence: Minimum confluence score
        
        Returns:
            Filtered signals
        """
        if min_confluence is None:
            min_confluence = self.min_confluence_score
        
        filtered = [
            s for s in signals 
            if s.get('confidence', 0) >= min_confluence
        ]
        
        # Sort by confidence
        filtered.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        
        return filtered
    
    def get_confidence_breakdown(self, analysis: Dict) -> Dict:
        """
        Get detailed confidence breakdown.
        
        Args:
            analysis: Market analysis results
        
        Returns:
            Dictionary with breakdown
        """
        score, factors = self.calculate_confluence(
            order_flow=analysis.get('order_flow'),
            volume_profile=analysis.get('volume_profile'),
            ict=analysis.get('ict'),
            structure=analysis.get('structure')
        )
        
        return {
            'total_score': score,
            'max_score': 100,
            'percentage': score,
            'factors': factors,
            'meets_threshold': score >= self.min_confluence_score
        }
    
    def calculate_zone_confluence(
        self,
        fvg_zone: Dict = None,
        order_block: Dict = None,
        liquidity_pool: Dict = None,
        trend_line: Dict = None,
        timeframe_alignment: List[str] = None
    ) -> Tuple[float, List[str]]:
        """
        P3.4: Calculate zone confluence scoring.
        
        Args:
            fvg_zone: Fair Value Gap zone data
            order_block: Order Block zone data
            liquidity_pool: Liquidity Pool zone data
            trend_line: Trend line data
            timeframe_alignment: List of aligned timeframes (e.g., ['M5', 'M15', 'H1'])
        
        Returns:
            Tuple of (confluence_score, descriptions)
            - FVG + Order Block: +2 pts
            - FVG + Liquidity Pool: +1.5 pts
            - Order Block + Trend Line: +1 pt
            - Multiple timeframe alignment: +1 pt per additional TF
        """
        score = 0.0
        descriptions = []
        
        has_fvg = fvg_zone is not None
        has_ob = order_block is not None
        has_lp = liquidity_pool is not None
        has_tl = trend_line is not None
        tf_count = len(timeframe_alignment) if timeframe_alignment else 0
        
        if has_fvg and has_ob:
            score += 2.0
            descriptions.append("FVG+OB")
        
        if has_fvg and has_lp:
            score += 1.5
            descriptions.append("FVG+LP")
        
        if has_ob and has_tl:
            score += 1.0
            descriptions.append("OB+TL")
        
        if has_lp and has_tl:
            score += 1.0
            descriptions.append("LP+TL")
        
        if tf_count > 1:
            tf_bonus = min(tf_count - 1, 3) * 1.0
            score += tf_bonus
            descriptions.append(f"TF_ALIGNMENT({tf_count}TFs)")
        
        return score, descriptions
