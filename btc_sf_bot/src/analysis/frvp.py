"""
Multi-Layer Volume Profile Engine (v22.0)
SVP + AVP hybrid: 4 layers → composite POC/VAH/VAL + confluence zones
"""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta


class MultiLayerVolumeProfile:
    """
    Multi-Layer Volume Profile: SVP + AVP hybrid
    4 layers → composite POC/VAH/VAL
    
    Layer 1: Current Session Profile   (Asia/London/NY ปัจจุบัน)
    Layer 2: Previous Session Profile  (session ก่อนหน้า)
    Layer 3: Daily Profile             (24 ชม.)
    Layer 4: Swing-Anchored Profile    (จาก last H1 swing high/low)
    ─────────────────────────────────
    Composite: weighted POC/VAH/VAL    (รวมทุก layer)
    """
    
    SESSIONS = {
        'ASIA':   (1, 9),    # 01:00-09:00 UTC
        'LONDON': (7, 16),   # 07:00-16:00 UTC
        'NY':     (13, 22),  # 13:00-22:00 UTC
    }
    
    LAYER_WEIGHTS = {
        'current_session': 0.35,
        'prev_session':    0.25,
        'daily':           0.25,
        'swing_anchored':  0.15,
    }
    
    def __init__(self, bins: int = 50, value_area_pct: float = 0.70):
        self.bins = bins
        self.value_area_pct = value_area_pct
    
    def calculate(self, m5_candles: pd.DataFrame, h1_swings: Optional[Dict] = None) -> Dict:
        """
        Calculate Multi-Layer Volume Profile.
        
        Args:
            m5_candles: DataFrame with M5 OHLCV (must have >= 300 candles = 25h)
            h1_swings: dict with 'last_swing_high_time', 'last_swing_low_time'
            
        Returns:
            {
                'layers': {
                    'current_session': { 'poc', 'vah', 'val', 'session_name', 'candle_count' },
                    'prev_session':    { 'poc', 'vah', 'val', 'session_name', 'candle_count' },
                    'daily':           { 'poc', 'vah', 'val', 'candle_count' },
                    'swing_anchored':  { 'poc', 'vah', 'val', 'anchor_type', 'candle_count' },
                },
                'composite': { 'poc', 'vah', 'val' },
                'confluence_zones': [ { 'price', 'layers', 'strength' } ],
            }
        """
        h1_swings = h1_swings or {}
        
        # Convert DataFrame to list of dicts with datetime
        candles_list = []
        for idx, row in m5_candles.iterrows():
            candles_list.append({
                'time': idx,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']) if 'volume' in row else 0.0
            })
        
        if not candles_list:
            return self._empty_result()
        
        now = candles_list[-1]['time']
        
        # --- Layer 1: Current Session ---
        curr_name, curr_start, curr_end = self._get_current_session(now)
        curr_candles = [c for c in candles_list if curr_start <= c['time'] < curr_end]
        layer_current = self._calc_profile(curr_candles)
        layer_current['session_name'] = curr_name
        
        # --- Layer 2: Previous Session ---
        prev_name, prev_start, prev_end = self._get_prev_session(curr_name, now)
        prev_candles = [c for c in candles_list if prev_start <= c['time'] < prev_end]
        layer_prev = self._calc_profile(prev_candles)
        layer_prev['session_name'] = prev_name
        
        # --- Layer 3: Daily (24hr) ---
        daily_start = now - timedelta(hours=24)
        daily_candles = [c for c in candles_list if c['time'] >= daily_start]
        layer_daily = self._calc_profile(daily_candles)
        
        # --- Layer 4: Swing-Anchored ---
        swing_time = self._get_swing_anchor(h1_swings)
        swing_candles = [c for c in candles_list if c['time'] >= swing_time] if swing_time else candles_list[-60:]
        layer_swing = self._calc_profile(swing_candles)
        
        # Determine anchor type
        high_time = h1_swings.get('last_swing_high_time')
        low_time = h1_swings.get('last_swing_low_time')
        if high_time and low_time:
            anchor_type = 'swing_high' if high_time > low_time else 'swing_low'
        elif high_time:
            anchor_type = 'swing_high'
        else:
            anchor_type = 'swing_low'
        layer_swing['anchor_type'] = anchor_type
        
        layers = {
            'current_session': layer_current,
            'prev_session':    layer_prev,
            'daily':          layer_daily,
            'swing_anchored': layer_swing,
        }
        
        # --- Composite ---
        composite = self._build_composite(layers)
        
        # --- Confluence Zones ---
        confluence = self._find_confluence(layers)
        
        return {
            'layers': layers,
            'composite': composite,
            'confluence_zones': confluence,
        }
    
    def _empty_result(self) -> Dict:
        return {
            'layers': {},
            'composite': {'poc': None, 'vah': None, 'val': None},
            'confluence_zones': []
        }
    
    def _calc_profile(self, candles: List[Dict]) -> Dict:
        """Calculate POC, VAH, VAL from list of candle dicts."""
        if len(candles) < 3:
            return {'poc': None, 'vah': None, 'val': None, 'candle_count': len(candles)}
        
        lows = [c['low'] for c in candles]
        highs = [c['high'] for c in candles]
        price_min = min(lows)
        price_max = max(highs)
        
        if price_min >= price_max:
            return {'poc': None, 'vah': None, 'val': None, 'candle_count': len(candles)}
        
        # Create bins
        bins = np.linspace(price_min, price_max, self.bins + 1)
        volume_profile = np.zeros(self.bins)
        
        for c in candles:
            low = c['low']
            high = c['high']
            vol = c['volume']
            
            for i in range(self.bins):
                if bins[i] <= high and bins[i + 1] >= low:
                    overlap = min(high, bins[i + 1]) - max(low, bins[i])
                    candle_range = high - low
                    if candle_range > 0:
                        volume_profile[i] += vol * (overlap / candle_range)
        
        # POC = bin with max volume
        if volume_profile.sum() == 0:
            return {'poc': None, 'vah': None, 'val': None, 'candle_count': len(candles)}
        
        poc_idx = np.argmax(volume_profile)
        poc = (bins[poc_idx] + bins[poc_idx + 1]) / 2
        
        # Value Area (70%)
        total_vol = volume_profile.sum()
        target_vol = total_vol * self.value_area_pct
        
        # Expand from POC
        va_low_idx = poc_idx
        va_high_idx = poc_idx
        current_vol = volume_profile[poc_idx]
        
        while current_vol < target_vol:
            expand_up = volume_profile[va_high_idx + 1] if va_high_idx + 1 < self.bins else 0
            expand_down = volume_profile[va_low_idx - 1] if va_low_idx - 1 >= 0 else 0
            
            if expand_up >= expand_down and va_high_idx + 1 < self.bins:
                va_high_idx += 1
                current_vol += volume_profile[va_high_idx]
            elif va_low_idx - 1 >= 0:
                va_low_idx -= 1
                current_vol += volume_profile[va_low_idx]
            else:
                break
        
        vah = (bins[va_high_idx] + bins[va_high_idx + 1]) / 2
        val = (bins[va_low_idx] + bins[va_low_idx + 1]) / 2
        
        return {'poc': poc, 'vah': vah, 'val': val, 'candle_count': len(candles)}
    
    def _build_composite(self, layers: Dict) -> Dict:
        """Weighted average of POC/VAH/VAL from all layers."""
        composite = {}
        for key in ['poc', 'vah', 'val']:
            weighted_sum = 0
            weight_sum = 0
            for layer_name, layer in layers.items():
                if layer.get(key) is not None:
                    w = self.LAYER_WEIGHTS[layer_name]
                    weighted_sum += layer[key] * w
                    weight_sum += w
            composite[key] = weighted_sum / weight_sum if weight_sum > 0 else None
        return composite
    
    def _find_confluence(self, layers: Dict, tolerance_pct: float = 0.15) -> List[Dict]:
        """Find zones where POC/VAH/VAL from multiple layers overlap."""
        all_levels = []
        for layer_name, layer in layers.items():
            for key in ['poc', 'vah', 'val']:
                if layer.get(key) is not None:
                    all_levels.append({
                        'price': layer[key],
                        'layer': layer_name,
                        'type': key,
                    })
        
        if not all_levels:
            return []
        
        # Sort by price
        all_levels.sort(key=lambda x: x['price'])
        
        # Find clusters
        zones = []
        used = set()
        
        for i, level in enumerate(all_levels):
            if i in used:
                continue
            cluster = [level]
            used.add(i)
            
            for j, other in enumerate(all_levels):
                if j in used:
                    continue
                if level['price'] > 0:
                    pct_diff = abs(other['price'] - level['price']) / level['price'] * 100
                    if pct_diff < tolerance_pct:
                        cluster.append(other)
                        used.add(j)
            
            if len(cluster) >= 2:
                avg_price = sum(c['price'] for c in cluster) / len(cluster)
                layers_involved = list(set(c['layer'] for c in cluster))
                zones.append({
                    'price': round(avg_price, 1),
                    'layers': layers_involved,
                    'strength': len(cluster),
                })
        
        zones.sort(key=lambda x: x['strength'], reverse=True)
        return zones
    
    def _get_current_session(self, now) -> tuple:
        """Return (session_name, start_time, end_time) for current session."""
        # Handle both datetime and timestamp
        if isinstance(now, (int, float)):
            now = datetime.fromtimestamp(now / 1000) if now > 1e10 else datetime.fromtimestamp(now)
        
        hour = now.hour
        for name, (start_h, end_h) in self.SESSIONS.items():
            if start_h <= hour < end_h:
                return name, now.replace(hour=start_h, minute=0, second=0, microsecond=0), now.replace(hour=end_h, minute=0, second=0, microsecond=0)
        
        # Default: ASIA (overnight)
        return 'ASIA', now.replace(hour=1, minute=0, second=0, microsecond=0), now.replace(hour=9, minute=0, second=0, microsecond=0)
    
    def _get_prev_session(self, current_name: str, now) -> tuple:
        """Return previous session info."""
        # Handle both datetime and timestamp
        if isinstance(now, (int, float)):
            now = datetime.fromtimestamp(now / 1000) if now > 1e10 else datetime.fromtimestamp(now)
        
        order = ['ASIA', 'LONDON', 'NY']
        if current_name not in order:
            current_name = 'ASIA'
        idx = order.index(current_name)
        prev_name = order[(idx - 1) % 3]
        prev_start_h, prev_end_h = self.SESSIONS[prev_name]
        
        if prev_name == 'NY' and current_name == 'ASIA':
            # NY from previous day
            yesterday = now - timedelta(days=1)
            return prev_name, yesterday.replace(hour=prev_start_h, minute=0, second=0, microsecond=0), yesterday.replace(hour=prev_end_h, minute=0, second=0, microsecond=0)
        else:
            return prev_name, now.replace(hour=prev_start_h, minute=0, second=0, microsecond=0), now.replace(hour=prev_end_h, minute=0, second=0, microsecond=0)
    
    def _get_swing_anchor(self, h1_swings: Dict) -> Optional[datetime]:
        """Return the most recent swing time as anchor."""
        high_time = h1_swings.get('last_swing_high_time')
        low_time = h1_swings.get('last_swing_low_time')
        
        # Handle timestamp vs datetime
        if high_time and isinstance(high_time, (int, float)):
            high_time = datetime.fromtimestamp(high_time / 1000) if high_time > 1e10 else datetime.fromtimestamp(high_time)
        if low_time and isinstance(low_time, (int, float)):
            low_time = datetime.fromtimestamp(low_time / 1000) if low_time > 1e10 else datetime.fromtimestamp(low_time)
        
        if high_time and low_time:
            return max(high_time, low_time)
        return high_time or low_time
