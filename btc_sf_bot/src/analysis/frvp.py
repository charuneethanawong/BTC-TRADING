"""
Multi-Layer Volume Profile Engine (v43.7)
SVP + AVP hybrid: 4 layers → composite POC/VAH/VAL + confluence zones + HVN/LVN + POC shift

Layer 1: Swing-Anchored Profile  (institutional positioning — highest weight)
Layer 2: Current Session Profile  (intraday fair value)
Layer 3: Trigger Profile          (liquidity sweep / volume climax — fresh positioning)
Layer 4: Context Profile          (previous session — background context)
─────────────────────────────────
Composite: weighted POC/VAH/VAL    (รวมทุก layer)
"""
import logging
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# MOD-5: Anchor Dead Zone - min price move to trigger anchor recalculation
ANCHOR_DEAD_ZONE = 200  # pts


class MultiLayerVolumeProfile:
    """
    Multi-Layer Volume Profile: SVP + AVP hybrid
    4 layers → composite POC/VAH/VAL
    
    Layer 1: Swing-Anchored Profile    (จาก last H1 swing high/low)
    Layer 2: Current Session Profile   (Asia/London/NY ปัจจุบัน)
    Layer 3: Trigger Profile           (liquidity sweep / volume climax)
    Layer 4: Context Profile           (previous session)
    ─────────────────────────────────
    Composite: weighted POC/VAH/VAL    (รวมทุก layer)
    """
    
    SESSIONS = {
        'ASIA':   (1, 9),    # 01:00-09:00 UTC
        'LONDON': (7, 16),   # 07:00-16:00 UTC
        'NY':     (13, 22),  # 13:00-22:00 UTC
    }
    
    # v43.7: Reweighted — swing_anchored highest (institutional positioning)
    LAYER_WEIGHTS = {
        'swing_anchored':  0.40,  # สถาบัน position ทั้ง trend (สำคัญสุด)
        'current_session': 0.30,  # intraday fair value
        'trigger':         0.20,  # liquidity sweep / volume climax
        'context':         0.10,  # previous session
    }
    
    def __init__(self, bins: int = 24, value_area_pct: float = 0.70):
        self.bins = bins
        self.value_area_pct = value_area_pct
        # v43.7: Track previous POC for shift detection
        self._prev_swing_poc: Optional[float] = None
        
        # MOD-5: Locked anchor for stability (prevent flip-flopping)
        self._locked_anchor: Optional[Dict] = None  # {'time': datetime, 'price': float, 'type': str}
    
    def calculate(self, m5_candles: pd.DataFrame, h1_swings: Optional[Dict] = None,
                  ict_data: Optional[Dict] = None, h1_candles: Optional[pd.DataFrame] = None) -> Dict:
        """
        Calculate Multi-Layer Volume Profile.
        
        Args:
            m5_candles: DataFrame with M5 OHLCV (must have >= 300 candles = 25h)
            h1_swings: dict with 'last_swing_high_time', 'last_swing_low_time'
            ict_data: dict with 'last_sweep' for trigger layer anchor
            h1_candles: v43.8: H1 OHLCV for swing-anchored layer (wider range)
            
        Returns:
            {
                'layers': {
                    'swing_anchored':  { 'poc', 'vah', 'val', 'anchor_type', 'hvn', 'lvn', 'candle_count' },
                    'current_session': { 'poc', 'vah', 'val', 'session_name', 'hvn', 'lvn', 'candle_count' },
                    'trigger':         { 'poc', 'vah', 'val', 'anchor_type', 'hvn', 'lvn', 'candle_count' },
                    'context':         { 'poc', 'vah', 'val', 'session_name', 'hvn', 'lvn', 'candle_count' },
                },
                'composite': { 'poc', 'vah', 'val' },
                'confluence_zones': [ { 'price', 'layers', 'strength' } ],
            }
        """
        h1_swings = h1_swings or {}
        ict_data = ict_data or {}
        
        # Convert M5 DataFrame to list of dicts with datetime
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
        
        # v43.8: Convert H1 candles for swing-anchored layer (wider range)
        h1_candles_list = []
        if h1_candles is not None and len(h1_candles) > 0:
            for idx, row in h1_candles.iterrows():
                h1_candles_list.append({
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
        
        # --- Layer 1: Swing-Anchored (highest weight) ---
        # v50.8: Always use M5 candles for VP (matches TradingView precision)
        # H1 candles lose intra-hour volume detail → POC inaccurate
        anchor = self._get_swing_anchor(h1_swings, candles_list)
        swing_time = anchor.get('time')
        swing_candles = [c for c in candles_list if c['time'] >= swing_time] if swing_time else candles_list[-60:]
        # v51.4: No cap — use full institutional swing range (VA width = institutional reality)
        layer_swing = self._calc_profile(swing_candles)
        
        # v43.8: Anchor metadata from _get_swing_anchor (no more matching needed)
        current_price = candles_list[-1]['close']
        
        # Calculate anchor age in M5 candles
        anchor_age_candles = 0
        if swing_time and candles_list:
            last_dt = self._parse_time(candles_list[-1]['time']) if isinstance(candles_list[-1]['time'], (int, float)) else candles_list[-1]['time']
            if swing_time and last_dt:
                diff_seconds = (last_dt - swing_time).total_seconds()
                anchor_age_candles = int(diff_seconds / 300)  # M5 = 300 seconds
        
        layer_swing['anchor_type'] = anchor.get('type', 'fallback_24h')
        layer_swing['anchor_price'] = anchor.get('price', 0)
        layer_swing['anchor_move'] = anchor.get('move', 0)
        layer_swing['anchor_age_candles'] = anchor_age_candles
        
        # --- Layer 2: Current Session ---
        curr_name, curr_start, curr_end = self._get_current_session(now)
        curr_candles = [c for c in candles_list if curr_start <= c['time'] < curr_end]
        layer_current = self._calc_profile(curr_candles)
        layer_current['session_name'] = curr_name
        
        # --- Layer 3: Trigger (new — liquidity sweep / volume climax) ---
        layer_trigger = self._calc_trigger_layer(candles_list, ict_data)
        
        # --- Layer 4: Context (previous session) ---
        prev_name, prev_start, prev_end = self._get_prev_session(curr_name, now)
        prev_candles = [c for c in candles_list if prev_start <= c['time'] < prev_end]
        layer_prev = self._calc_profile(prev_candles)
        layer_prev['session_name'] = prev_name
        
        layers = {
            'swing_anchored':  layer_swing,
            'current_session': layer_current,
            'trigger':         layer_trigger,
            'context':         layer_prev,
        }
        
        # --- Composite ---
        composite = self._build_composite(layers)
        
        # --- Confluence Zones ---
        confluence = self._find_confluence(layers)
        
        # v43.7: POC Shift Detection
        poc_shift, poc_shift_dir = self.calc_poc_shift(layers)
        
        return {
            'layers': layers,
            'composite': composite,
            'poc_shift': poc_shift,
            'poc_shift_direction': poc_shift_dir,
            'confluence_zones': confluence,
            
            
        }
    
    def _empty_result(self) -> Dict:
        return {
            'layers': {},
            'composite': {'poc': None, 'vah': None, 'val': None},
            'confluence_zones': [],
            
            
        }
    
    def _calc_profile(self, candles: List[Dict]) -> Dict:
        """Calculate POC, VAH, VAL from list of candle dicts."""
        if len(candles) < 3:
            return {'poc': None, 'vah': None, 'val': None, 'candle_count': len(candles),
                    'hvn': [], 'lvn': []}
        
        lows = [c['low'] for c in candles]
        highs = [c['high'] for c in candles]
        price_min = min(lows)
        price_max = max(highs)
        
        if price_min >= price_max:
            return {'poc': None, 'vah': None, 'val': None, 'candle_count': len(candles),
                    'hvn': [], 'lvn': []}
        
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
            return {'poc': None, 'vah': None, 'val': None, 'candle_count': len(candles),
                    'hvn': [], 'lvn': []}
        
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
        
        result = {
            'poc': poc, 'vah': vah, 'val': val, 'candle_count': len(candles),
        }
        
        # v43.7: HVN/LVN Detection
        hvn_lvn = self._detect_hvn_lvn(volume_profile, bins)
        result.update(hvn_lvn)
        
        return result
    
    def _detect_hvn_lvn(self, volume_profile: np.ndarray, bins: np.ndarray) -> Dict:
        """v43.7: High Volume Node = สถาบัน defend, Low Volume Node = speed zone"""
        avg = volume_profile.mean()
        std = volume_profile.std()
        hvn_threshold = avg + 0.5 * std
        lvn_threshold = max(avg - 0.5 * std, 0.01)
        
        hvn = []
        lvn = []
        for i in range(len(volume_profile)):
            price = (bins[i] + bins[i + 1]) / 2
            vol = volume_profile[i]
            if vol >= hvn_threshold:
                hvn.append({'price': round(price, 2), 'volume': round(float(vol), 2)})
            elif 0 < vol <= lvn_threshold:
                lvn.append({'price': round(price, 2), 'volume': round(float(vol), 2)})
        
        return {'hvn': hvn, 'lvn': lvn}
    
    def _calc_trigger_layer(self, candles_list: List[Dict], ict_data: Optional[Dict] = None) -> Dict:
        """v43.7: Trigger VP — anchor จาก liquidity sweep หรือ volume climax"""
        anchor_time = None
        anchor_type = 'none'
        
        # Priority 1: Liquidity Sweep
        sweep = ict_data.get('last_sweep') if ict_data else None
        if sweep and sweep.get('time'):
            anchor_time = sweep['time']
            anchor_type = 'liquidity_sweep'
        
        # Priority 2: Volume Climax (volume > 2x avg ใน 20 candle ล่าสุด)
        if not anchor_time and len(candles_list) >= 20:
            avg_vol = sum(c['volume'] for c in candles_list[-20:]) / 20
            for i in range(-1, -20, -1):
                if abs(i) >= len(candles_list):
                    break
                if candles_list[i]['volume'] > avg_vol * 2.0:
                    anchor_time = candles_list[i]['time']
                    anchor_type = 'volume_climax'
                    break
        
        if not anchor_time:
            return {'poc': None, 'vah': None, 'val': None,
                    'hvn': [], 'lvn': [], 'anchor_type': 'none', 'candle_count': 0}
        
        trigger_candles = [c for c in candles_list if c['time'] >= anchor_time]
        profile = self._calc_profile(trigger_candles)
        profile['anchor_type'] = anchor_type
        return profile
    
    def calc_poc_shift(self, current_layers: Dict) -> tuple:
        """v43.7: Calculate POC shift without updating state."""
        curr_poc = current_layers.get('swing_anchored', {}).get('poc', 0)

        if self._prev_swing_poc is None or not curr_poc:
            return 0, 'NEUTRAL'

        shift = curr_poc - self._prev_swing_poc
        direction = 'BULLISH' if shift > 0 else 'BEARISH' if shift < 0 else 'NEUTRAL'

        return round(shift, 2), direction

    def commit_poc_state(self, current_layers: Dict):
        """v43.7: Update previous POC state at end of cycle."""
        curr_poc = current_layers.get('swing_anchored', {}).get('poc', 0)
        if curr_poc:
            self._prev_swing_poc = curr_poc
    
    def update_poc_state(self, current_layers: Dict):
        """v43.7: Update internal POC state — call ONCE per cycle after all calculations done."""
        curr_poc = current_layers.get('swing_anchored', {}).get('poc', 0)
        if curr_poc:
            self._prev_swing_poc = curr_poc
    
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
    
    def _get_swing_anchor(self, h1_swings: Dict, candles_list: List[Dict] = None) -> Dict:
        """v43.8: Find MAJOR H1 swing — progressive threshold, no M5 fallback.
        
        v44.4 (MOD-5): Anchor Dead Zone - lock anchor until price moves 200 pts.
        
        ค้นหา major swing โดยลด threshold ลงเรื่อยๆ จนเจอ:
        1st pass: move > 2.0x ATR (strong major swing)
        2nd pass: move > 1.5x ATR
        3rd pass: move > 1.0x ATR
        4th pass: move > 0.5x ATR (weakest — ยังดีกว่า M5 fallback)
        
        ต้องเจอเสมอ — BTC ไม่มีทาง sideways 8 วันโดยไม่มี H1 swing
        """
        all_highs = h1_swings.get('all_highs', [])
        all_lows = h1_swings.get('all_lows', [])
        atr_h1 = h1_swings.get('atr_h1', 200)
        
        if not candles_list:
            return {'time': None, 'type': 'no_data', 'price': 0, 'move': 0}
        
        current_price = candles_list[-1]['close']
        
        # MOD-5: Check dead zone - if locked anchor exists and price hasn't moved enough, use it
        if self._locked_anchor is not None:
            locked_price = self._locked_anchor.get('price', 0)
            if locked_price > 0 and abs(current_price - locked_price) < ANCHOR_DEAD_ZONE:
                logger.info(f"[FRVP] Dead zone active — using locked anchor {locked_price:.0f} (price moved {abs(current_price - locked_price):.0f} < {ANCHOR_DEAD_ZONE})")
                return {
                    'time': self._locked_anchor.get('time'),
                    'type': self._locked_anchor.get('type', 'locked'),
                    'price': locked_price,
                    'move': abs(current_price - locked_price),
                }
        
        def parse_time(t):
            if isinstance(t, (int, float)):
                return datetime.fromtimestamp(t / 1000) if t > 1e10 else datetime.fromtimestamp(t)
            return t
        
        # Progressive threshold — ลดลงจนเจอ
        for multiplier in [2.0, 1.5, 1.0, 0.5]:
            min_move = atr_h1 * multiplier
            major_swings = []
            
            # หา major swing lows (price ขึ้นจาก swing > threshold)
            for sw in all_lows:
                sw_price = sw.get('level', sw.get('price', 0))
                sw_time = sw.get('time')
                if not sw_price or not sw_time:
                    continue
                move_after = current_price - sw_price
                if move_after > min_move:
                    major_swings.append({'time': sw_time, 'price': sw_price, 'type': 'major_swing_low', 'move': move_after})
            
            # หา major swing highs (price ลงจาก swing > threshold)
            for sw in all_highs:
                sw_price = sw.get('level', sw.get('price', 0))
                sw_time = sw.get('time')
                if not sw_price or not sw_time:
                    continue
                move_after = sw_price - current_price
                if move_after > min_move:
                    major_swings.append({'time': sw_time, 'price': sw_price, 'type': 'major_swing_high', 'move': move_after})
            
            if major_swings:
                major_swings.sort(key=lambda s: parse_time(s['time']), reverse=True)
                best = major_swings[0]
                found_anchor = {
                    'time': parse_time(best['time']),
                    'type': best['type'],
                    'price': best['price'],
                    'move': round(best['move'], 2),
                }
                # MOD-5: Lock this anchor for stability
                self._locked_anchor = {
                    'time': found_anchor['time'],
                    'price': best['price'],
                    'type': best['type'],
                }
                logger.debug(f"[FRVP] Major swing found & LOCKED | threshold:{multiplier}x ATR | "
                           f"type:{best['type']} price:{best['price']:.0f} move:{best['move']:.0f}")
                return found_anchor
        
        # ทุก threshold ไม่เจอ → ใช้ H1 swing เก่าสุดที่มี
        all_swings = all_highs + all_lows
        if all_swings:
            oldest = min(all_swings, key=lambda s: parse_time(s.get('time', 0)))
            logger.warning(f"[FRVP] No major swing found — using oldest H1 swing")
            return {
                'time': parse_time(oldest['time']),
                'type': 'oldest_h1_swing',
                'price': oldest.get('price', 0),
                'move': 0,
            }
        
        return {'time': None, 'type': 'no_h1_swing', 'price': 0, 'move': 0}
    
    @staticmethod
    def _parse_time(t):
        """Parse timestamp to datetime."""
        if isinstance(t, (int, float)):
            return datetime.fromtimestamp(t / 1000) if t > 1e10 else datetime.fromtimestamp(t)
        return t

    def calc_poc_shift(self, current_layers: Dict) -> tuple:
        """v43.7: Calculate POC shift without updating state."""
        curr_poc = current_layers.get('swing_anchored', {}).get('poc', 0)

        if self._prev_swing_poc is None or not curr_poc:
            return 0, 'NEUTRAL'

        shift = curr_poc - self._prev_swing_poc
        direction = 'BULLISH' if shift > 0 else 'BEARISH' if shift < 0 else 'NEUTRAL'

        return round(shift, 2), direction

    def commit_poc_state(self, current_layers: Dict):
        curr_poc = current_layers.get('swing_anchored', {}).get('poc', 0)
        if curr_poc:
            self._prev_swing_poc = curr_poc

    def update_poc_state(self, current_layers: Dict):
        curr_poc = current_layers.get('swing_anchored', {}).get('poc', 0)
        if curr_poc:
            self._prev_swing_poc = curr_poc