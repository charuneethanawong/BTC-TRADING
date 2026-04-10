"""
Signal Gate — v49.0

Gate checks BEFORE signal is sent to EA.
Blocks signals that fail any of these checks.
Incorporates MOD-1 to MOD-28.
"""
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import logging
import json
from pathlib import Path

from src.utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)

# v49.0: Gate state persistence
GATE_STATE_FILE = Path(__file__).resolve().parent.parent / 'data' / 'gate_state.json'

# v51.0: MOD-38 Signal Types (unified IPA)
SIGNAL_TYPES = [
    'MOMENTUM', 'MEAN_REVERT', 'ABSORPTION',
    'REVERSAL_OB', 'REVERSAL_OS',
    'IPA',  # v51.0: unified (was IPA_OB, IPA_FVG, IPA_EMA)
    'VP_BOUNCE', 'VP_BREAKOUT', 'VP_ABSORB', 'VP_REVERT', 'VP_POC'
]

# Mode aliases for backward compatibility (v51.0)
MODE_ALIASES = {
    # v51.0: Unified IPA
    'IPA': 'IPA',              # direct
    'IPA_OB': 'IPA',         # legacy
    'IPA_FVG': 'IPA',        # legacy
    'IPA_EMA': 'IPA',        # legacy (deleted)
    'IPA_FRVP': 'IPA',      # legacy
    'IPAF': 'IPA',          # legacy
    # Other modes
    'IOF': 'ABSORPTION',
    'IOF_FRVP': 'ABSORPTION',
    'IOFF': 'ABSORPTION',
}

def normalize_mode(mode: str) -> str:
    """Normalize mode name to standard form."""
    return MODE_ALIASES.get(mode, mode)

@dataclass
class GateResult:
    """Result of gate check."""
    passed: bool
    reason: str
    blocked_count: int = 0

    def __str__(self) -> str:
        status = '✅ PASSED' if self.passed else f'❌ BLOCKED ({self.reason})'
        return f"GateResult: {status}"

@dataclass
class PositionInfo:
    """Information about an active position."""
    ticket: int
    symbol: str
    direction: str
    signal_type: str
    open_time: datetime
    entry_price: float
    current_pnl: float = 0.0

@dataclass
class AccountState:
    """Account state snapshot."""
    daily_pnl: float
    daily_loss_pct: float
    equity: float
    balance: float
    open_positions: List[PositionInfo]

    @staticmethod
    def empty() -> 'AccountState':
        return AccountState(0.0, 0.0, 0.0, 0.0, [])

class SignalGate:
    """
    Pre-send gate checks for all signals (v45.0: 23 Gates).
    """
    HARD_LOCK_SECONDS: int = 30
    MAX_POSITIONS_PER_TYPE: int = 1
    DAILY_LOSS_LIMIT_PCT: float = 3.0

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.hard_lock_seconds = self.config.get('hard_lock_seconds', self.HARD_LOCK_SECONDS)
        self.max_positions_per_type = self.config.get('max_positions_per_type', self.MAX_POSITIONS_PER_TYPE)
        self.daily_loss_limit = self.config.get('daily_loss_limit_pct', self.DAILY_LOSS_LIMIT_PCT)

        # Signal type score thresholds (v54.0: MOD-49 - MOMENTUM conditions-based)
        self.score_thresholds = {
            'MOMENTUM': 1,    # v54.0: conditions-based, no score threshold
            'MEAN_REVERT': 8, 'ABSORPTION': 9,
            'REVERSAL_OB': 9, 'REVERSAL_OS': 9,
            'IPA': 8,  # v54.0: synced with detector
            'VP_BOUNCE': 6, 'VP_BREAKOUT': 8, 'VP_ABSORB': 7, 'VP_REVERT': 7, 'VP_POC': 8,  # v51.2: synced with detector thresholds
        }
        self.score_min = min(self.score_thresholds.values())
        self.rr_min = 1.0

        self._sent_signal_ids: set = set()
        self._last_signal_time_by_type: Dict[str, Optional[datetime]] = {st: None for st in SIGNAL_TYPES}
        self._last_signal_by_type: Dict[str, Optional[Dict[str, Any]]] = {st: None for st in SIGNAL_TYPES}
        self._mitigated_zones: List[Dict[str, Any]] = []  # v47.0: MOD-17 Structural Memory

        # v51.3: MOD-42 Loss Streak Cooldown
        self._consecutive_losses = 0
        self._last_loss_time = None

        # v50.4: Data Collection Mode — all signals pass, gates log only (shadow mode)
        self.DATA_COLLECTION_MODE = True
        
        # v49.0: MOD-28 M5 state flicker guard
        self._m5_state_stable_count = 0
        self._m5_prev_state = ''
        
        # v49.0: MOD-26 Load persisted state from JSON
        self._load_state()
    
    # v49.0: MOD-26 State persistence
    def _save_state(self):
        """Save gate state to JSON file — only save non-invalidated zones."""
        try:
            now = datetime.now(timezone.utc)
            state = {
                'zones': [
                    {**z, 'time': z['time'].isoformat() if isinstance(z.get('time'), datetime) else z.get('time')}
                    for z in self._mitigated_zones
                    if isinstance(z.get('time'), datetime)
                    and (now - z['time']).total_seconds() / 3600 <= self.ZONE_MAX_TTL_HOURS
                ],
                'last_signal_time': {
                    k: v.isoformat() if v and isinstance(v, datetime) else None
                    for k, v in self._last_signal_time_by_type.items()
                },
                'sent_signal_ids': list(self._sent_signal_ids)
            }
            GATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            GATE_STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning(f"[Gate] Save state failed: {e}")
    
    def _load_state(self):
        """Load gate state from JSON file — skip invalidated zones (distance + TTL)."""
        try:
            if not GATE_STATE_FILE.exists():
                return
            state = json.loads(GATE_STATE_FILE.read_text())
            now = datetime.now(timezone.utc)
            loaded = 0
            expired = 0
            for z in state.get('zones', []):
                if 'time' in z and isinstance(z['time'], str):
                    z['time'] = datetime.fromisoformat(z['time'])
                # Skip zones older than max TTL
                if isinstance(z.get('time'), datetime):
                    age_hours = (now - z['time']).total_seconds() / 3600
                    if age_hours > self.ZONE_MAX_TTL_HOURS:
                        expired += 1
                        continue
                # Preserve max_distance from previous session
                z.setdefault('max_distance', 0)
                self._mitigated_zones.append(z)
                loaded += 1
            for k, v in state.get('last_signal_time', {}).items():
                if v and k in self._last_signal_time_by_type:
                    self._last_signal_time_by_type[k] = datetime.fromisoformat(v)

            sent_ids = state.get('sent_signal_ids', [])
            self._sent_signal_ids.update(sent_ids)

            logger.info(f"[Gate] State restored: {loaded} zones ({expired} expired/dropped), {len(self._sent_signal_ids)} IDs")
        except Exception as e:
            logger.warning(f"[Gate] Load state failed: {e}")

    @log_errors
    @timed_metric("SignalGate.check")
    def check(self, signal: Dict[str, Any], account_state: AccountState, active_positions: List[PositionInfo]) -> GateResult:
        sig_type = normalize_mode(signal.get('signal_type', signal.get('mode', 'MOMENTUM')))
        direction = signal.get('direction', 'LONG')
        score = signal.get('score', 0)
        required_rr = signal.get('required_rr', 0)
        signal_id = signal.get('signal_id', '')

        # v50.5: Helper — shadow mode (log but don't block, for data collection)
        def _shadow(gate_result):
            if gate_result and self.DATA_COLLECTION_MODE:
                logger.info(f"[Gate] 👁️ SHADOW: {signal_id} | {gate_result.reason}")
                signal.setdefault('shadow_blocks', []).append(gate_result.reason)
                return True  # was triggered but shadowed
            return False

        # ═══════════════════════════════════════════════════════
        # CRITICAL GATES — always block regardless of mode
        # ═══════════════════════════════════════════════════════

        # Gate: MOD-42 Loss Streak Cooldown (3 losses → 15 min pause)
        gate_result = self._check_loss_streak(signal)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 3: Daily Loss
        gate_result = self._check_daily_loss(account_state)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 4: Max Positions
        gate_result = self._check_max_positions(sig_type, direction, active_positions)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 5: Hard Lock
        gate_result = self._check_hard_lock(sig_type)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 6: Duplicate
        gate_result = self._check_duplicate(signal_id)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 8: Wall Contradiction
        gate_result = self._check_wall_contradiction(signal)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 9: DER=0 No Flow
        gate_result = self._check_der_zero(signal)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 19: DEAD_REGIME_BLOCK
        gate_result = self._check_dead_regime(signal)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # v54.0: Gate 24 STRUCTURAL_MITIGATION — DISABLED (zone stacking blocks too many)
        # gate_result = self._check_structural_mitigation(signal)
        # if gate_result:
        #     logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
        #     return gate_result

        # Gate 25: H1_OVEREXTENSION — v53.1: shadow mode (collect data, don't block)
        gate_result = self._check_h1_overextension_v2(signal)
        if gate_result:
            logger.info(f"[Gate] 👁️ SHADOW: {signal_id} | {gate_result.reason}")
            signal.setdefault('shadow_blocks', []).append(gate_result.reason)

        # Gate 26: SWING_STRUCTURE (multi-TF)
        gate_result = self._check_swing_structure(signal)
        if gate_result:
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # ═══════════════════════════════════════════════════════
        # DATA COLLECTION GATES — shadow only when DATA_COLLECTION_MODE
        # Block normally when mode is OFF
        # ═══════════════════════════════════════════════════════

        # Gate 1: Score
        gate_result = self._check_score(sig_type, score)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 10: SHORT ABOVE EMA
        gate_result = self._check_short_above_ema(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 11: H1 Overextension (old)
        gate_result = self._check_h1_overextension(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 12: DER Climax
        gate_result = self._check_der_climax(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 13: H1 Bias Neutral
        gate_result = self._check_h1_bias_none(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 14: M5 Pullback
        gate_result = self._check_m5_pullback(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 15: Delta Alignment
        gate_result = self._check_delta_alignment(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 16: EMA Overextension (Ranging)
        gate_result = self._check_ema_overextension(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 17: OB Slippage
        gate_result = self._check_ob_slippage(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 18: M5_STATE_INVALID
        gate_result = self._check_m5_state_invalid(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 20: FRVP_DIRECTION_BLOCK
        gate_result = self._check_frvp_direction_block(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 21: IPA_H1_BIAS_CLIMAX_BLOCK
        gate_result = self._check_ipa_h1_bias_climax(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 22: DER_LATE_ENTRY
        gate_result = self._check_der_late_entry(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        # Gate 23: MOMENTUM_RANGING_BLOCK
        gate_result = self._check_momentum_ranging_block(signal)
        if gate_result and not _shadow(gate_result):
            logger.info(f"[Gate] ⛔ BLOCKED: {signal_id} | {gate_result.reason}")
            return gate_result

        logger.info(f"[Gate] ✅ PASSED: {signal_id} ({sig_type})")
        return GateResult(passed=True, reason='PASSED')

    def mark_sent(self, signal: Dict[str, Any]):
        sig_id = signal.get('signal_id', '')
        sig_type = normalize_mode(signal.get('signal_type', signal.get('mode', 'MOMENTUM')))
        self._sent_signal_ids.add(sig_id)
        self._last_signal_time_by_type[sig_type] = datetime.now(timezone.utc)
        self._last_signal_by_type[sig_type] = signal

        # v47.0: MOD-17 - Log structural footprint for future mitigation blocking
        try:
            entry_price = signal.get('entry_price', 0)
            z_min = signal.get('entry_zone_min', 0)
            z_max = signal.get('entry_zone_max', 0)
            atr = signal.get('atr_m5', 100) # Fallback ATR

            # If no explicit zone (FVG/OB), create a proxy ATR zone
            if z_min == 0 or z_max == 0:
                z_min = entry_price - (atr * 0.5)
                z_max = entry_price + (atr * 0.5)

            self._mitigated_zones.append({
                'signal_id': sig_id,  # v50.6: track for cleanup if EA never opens
                'type': sig_type,
                'direction': signal.get('direction'),
                'min': z_min,
                'max': z_max,
                'entry': entry_price,
                'max_distance': 0,  # v50.6: MOD-36 smart invalidation
                'time': datetime.now(timezone.utc)
            })

            # Keep rolling limit
            if len(self._mitigated_zones) > 100:
                self._mitigated_zones.pop(0)
            
            # v49.0: MOD-26 Save state after adding zone
            self._save_state()
        except Exception as e:
            logger.error(f"[Gate] Error marking mitigation: {e}")

    def mark_blocked(self, signal: Dict[str, Any], reason: str):
        logger.info(f"[Gate] ⛔ BLOCKED: {signal.get('signal_id')} | Reason: {reason}")

    # === Gate Implementations ===

    def _check_score(self, sig_type: str, score: int) -> Optional[GateResult]:
        threshold = self.score_thresholds.get(sig_type, self.score_min)
        if score < threshold:
            return GateResult(False, f'SCORE_TOO_LOW_{score}_min_{threshold}')
        return None

    def _check_daily_loss(self, account_state: AccountState) -> Optional[GateResult]:
        if account_state.daily_loss_pct >= self.daily_loss_limit:
            return GateResult(False, f'DAILY_LOSS_LIMIT_{account_state.daily_loss_pct:.1f}%')
        return None

    def _check_loss_streak(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """v51.3 MOD-42: Loss Streak Cooldown — pause after 3 consecutive losses."""
        if self._consecutive_losses >= 3 and self._last_loss_time:
            elapsed = (datetime.now(timezone.utc) - self._last_loss_time).total_seconds()
            if elapsed < 60:  # 1 minute cooldown
                remaining = int(60 - elapsed)
                return GateResult(False, f'LOSS_STREAK_COOLDOWN_{self._consecutive_losses}losses_{remaining}s_remaining')
        return None

    def on_trade_result(self, result: str):
        """v51.3 MOD-42: Update loss streak counter."""
        if result == 'WIN':
            self._consecutive_losses = 0
        elif result == 'LOSS':
            self._consecutive_losses += 1
            self._last_loss_time = datetime.now(timezone.utc)

    def _check_max_positions(self, sig_type: str, direction: str, active_positions: List[PositionInfo]) -> Optional[GateResult]:
        count = sum(1 for p in active_positions if p.signal_type == sig_type and p.direction == direction)
        if count >= self.max_positions_per_type:
            return GateResult(False, f'MAX_POSITIONS_{sig_type}_{direction}')
        return None

    def _check_hard_lock(self, sig_type: str) -> Optional[GateResult]:
        last_time = self._last_signal_time_by_type.get(sig_type)
        if last_time:
            elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
            if elapsed < self.hard_lock_seconds:
                return GateResult(False, f'HARD_LOCK_{sig_type}_{self.hard_lock_seconds - elapsed:.0f}s')
        return None

    def _check_duplicate(self, signal_id: str) -> Optional[GateResult]:
        if signal_id in self._sent_signal_ids:
            return GateResult(False, f'DUPLICATE_SIGNAL_{signal_id}')
        return None

    def _check_wall_contradiction(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        sig_type = normalize_mode(signal.get('signal_type', ''))
        if sig_type == 'MEAN_REVERT': return None

        direction = signal.get('direction', 'LONG')
        wall_info = signal.get('wall_info', '')
        if not wall_info or wall_info == 'NONE': return None

        try:
            parts = wall_info.split(' ')
            side, size = parts[0], float(parts[1].replace('x', ''))
            if (direction == 'LONG' and side == 'ASK' and size >= 3.0) or \
               (direction == 'SHORT' and side == 'BID' and size >= 3.0):
                # v51.2: Spoof filter — wall must be stable > 15s to block
                wall_stability = signal.get('wall_stability_seconds', 0)
                if wall_stability < 15:
                    logger.info(f"[Gate] 👁️ WALL_CONTRA_{side}_{size:.1f}x stable {wall_stability:.0f}s < 15s — possible spoof")
                    return None
                return GateResult(False, f'WALL_CONTRA_{side}_{size:.1f}x_stable{wall_stability:.0f}s')
        except: pass
        return None

    def _check_der_zero(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        # v38.5 No flow
        if signal.get('der', 0) == 0.0:
            return GateResult(False, 'DER_ZERO_NO_FLOW')
        return None

    def _check_short_above_ema(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        sig_type = normalize_mode(signal.get('signal_type', ''))
        if not sig_type.startswith('IPA'): return None
        if signal.get('direction') == 'SHORT' and signal.get('m5_ema_position') == 'ABOVE_ALL':
            return GateResult(False, 'SHORT_ABOVE_ALL_EMA')
        return None

    def _check_h1_overextension(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        if signal.get('signal_type') == 'MEAN_REVERT': return None
        h1_dist = signal.get('h1_dist_pct', 0.0)
        if h1_dist > 1.0:
            return GateResult(False, f'H1_OVEREXTENDED_{h1_dist:.2f}%')
        return None

    def _check_der_climax(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        if signal.get('der_persistence', 0) >= 3:
            return GateResult(False, f'DER_CLIMAX_pers_{signal.get("der_persistence")}')
        return None

    def _check_h1_bias_none(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        layers = [signal.get(f'l{i}', '') for i in range(4)]
        if all(l == 'NEUTRAL' for l in layers):
            return GateResult(False, 'H1_BIAS_ALL_NEUTRAL')
        return None

    def _check_m5_pullback(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        if signal.get('m5_state') == 'PULLBACK':
            return GateResult(False, 'M5_STATE_PULLBACK')
        return None

    def _check_delta_alignment(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        if signal.get('signal_type') == 'MEAN_REVERT': return None
        direction, delta = signal.get('direction'), signal.get('delta', 0.0)
        if (direction == 'LONG' and delta < -500) or (direction == 'SHORT' and delta > 500):
            return GateResult(False, f'DELTA_CONTRA_{delta:.0f}')
        return None

    def _check_ema_overextension(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        if signal.get('regime') in ('RANGING', 'CHOPPY'):
            ema_pos, direction = signal.get('m5_ema_position'), signal.get('direction')
            if (ema_pos == 'ABOVE_ALL' and direction == 'LONG') or (ema_pos == 'BELOW_ALL' and direction == 'SHORT'):
                return GateResult(False, f'EMA_OVEREXTENDED_{ema_pos}')
        return None

    def _check_ob_slippage(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        curr, entry, atr = signal.get('current_price', 0), signal.get('entry_price', 0), signal.get('atr_m5', 0)
        if curr > 0 and entry > 0 and atr > 0:
            dist = abs(curr - entry)
            if dist > atr * 1.5:
                return GateResult(False, f'OB_SLIPPAGE_{dist/atr:.1f}xATR')
        return None

    # === NEW GATES v44.4/v45.0 ===

    def _check_m5_state_invalid(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 18 (MOD-1 + MOD-28): M5_STATE_INVALID with flicker guard"""
        m5_state = signal.get('m5_state', '')
        BLOCKED_STATES = {'RECOVERY', 'EXHAUSTION', 'ACCUMULATION'}
        
        # v49.0: MOD-28 Track stability
        if m5_state != self._m5_prev_state:
            self._m5_state_stable_count = 1
            self._m5_prev_state = m5_state
        else:
            self._m5_state_stable_count += 1
        
        # Block if in blocked state
        if m5_state in BLOCKED_STATES:
            return GateResult(False, f'M5_STATE_INVALID_{m5_state}')
        
        # Flicker guard: if just transitioned from blocked state, require 2 stable cycles
        if self._m5_state_stable_count < 2:
            return GateResult(False, f'M5_STATE_UNSTABLE_{m5_state}_count{self._m5_state_stable_count}')
        
        return None

    def _check_dead_regime(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 19 (MOD-3): DEAD_REGIME_BLOCK"""
        if signal.get('regime') == 'DEAD':
            return GateResult(False, 'DEAD_REGIME_BLOCK')
        return None

    def _check_frvp_direction_block(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 20 (MOD-2): FRVP_DIRECTION_BLOCK — Bypass for VP_REVERT (v49.1)"""
        sig_type = signal.get('signal_type', '')
        
        # v49.1: Bypass FRVP direction block for VP_REVERT (allow reverts)
        if sig_type == 'VP_REVERT':
            return None
        
        anchor = signal.get('anchor_type', '')
        direction = signal.get('direction', '')
        if (anchor == 'major_swing_low' and direction == 'SHORT') or \
           (anchor == 'major_swing_high' and direction == 'LONG'):
            return GateResult(False, f'FRVP_DIRECTION_BLOCK_{anchor}')
        return None

    def _check_ipa_h1_bias_climax(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 21 (MOD-4): IPA_H1_BIAS_CLIMAX_BLOCK"""
        sig_type = normalize_mode(signal.get('signal_type', ''))
        if not sig_type.startswith('IPA'): return None
        h1_level = signal.get('h1_bias_level', '')
        if h1_level in ('STRONG', 'CONFIRMED+'):
            return GateResult(False, f'IPA_H1_BIAS_CLIMAX_{h1_level}')
        return None

    def _check_der_late_entry(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 22 (MOD-8): DER_LATE_ENTRY"""
        if signal.get('der_direction') == signal.get('direction') and signal.get('der_persistence', 0) >= 2:
            return GateResult(False, f'DER_LATE_ENTRY_pers_{signal.get("der_persistence")}')
        return None

    def _check_momentum_ranging_block(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 23 (MOD-11): MOMENTUM_RANGING_BLOCK"""
        if signal.get('signal_type') == 'MOMENTUM' and signal.get('regime') == 'RANGING':
            return GateResult(False, 'MOMENTUM_RANGING_BLOCK')
        return None

    # v50.6: MOD-36 Smart Zone Invalidation — price distance based
    # v54.0: Reduced from 2.5 to 1.0 ATR (zone expires faster, less blocking)
    ZONE_INVALIDATION_ATR = 1.0   # zone expires when price moved > 1.0× ATR away
    ZONE_MAX_TTL_HOURS = 8.0      # safety fallback — never keep zone > 8h
    ZONE_TTL_HOURS = ZONE_MAX_TTL_HOURS  # backward compat for _load/_save

    def _check_structural_mitigation(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """
        Gate 24 (MOD-36): STRUCTURAL_MITIGATION — Smart Zone Invalidation
        v54.0: Zone expires when price has moved > 1.0× ATR away (reduced from 2.5)
        Fallback: max 8h TTL as safety net.
        """
        sig_type = normalize_mode(signal.get('signal_type', ''))
        direction = signal.get('direction', '')
        entry = signal.get('entry_price', 0)
        current_price = signal.get('current_price', entry)
        atr = signal.get('atr_m5', 100)

        if entry == 0:
            return None

        now = datetime.now(timezone.utc)
        threshold = atr * self.ZONE_INVALIDATION_ATR

        for zone in self._mitigated_zones:
            # Update max distance seen from this zone
            zone_mid = (zone['min'] + zone['max']) / 2
            current_dist = abs(current_price - zone_mid)
            zone['max_distance'] = max(zone.get('max_distance', 0), current_dist)

            # Invalidation 1: price moved far enough away → zone structurally dead
            if zone['max_distance'] > threshold:
                continue

            # Invalidation 2: safety TTL fallback (8h max)
            if isinstance(zone.get('time'), datetime):
                zone_age = (now - zone['time']).total_seconds() / 3600
                if zone_age > self.ZONE_MAX_TTL_HOURS:
                    continue

            # Zone still valid — check match
            if zone['type'] == sig_type and zone['direction'] == direction:
                if zone['min'] <= entry <= zone['max']:
                    age_min = (now - zone['time']).total_seconds() / 60 if isinstance(zone.get('time'), datetime) else 0
                    return GateResult(False, f'STRUCTURAL_MITIGATION_{sig_type}_dist{zone["max_distance"]:.0f}_age{age_min:.0f}m')

        return None
    
    def _check_h1_overextension_v2(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """
        Gate 25 (MOD-27): H1_OVEREXTENSION_BLOCK
        Block entries at H1 candle wick extremes (top 15% for LONG, bottom 15% for SHORT).
        """
        direction = signal.get('direction', '')
        entry = signal.get('entry_price', 0)
        h1_high = signal.get('h1_last_high', 0)
        h1_low = signal.get('h1_last_low', 0)
        
        if not (h1_high > h1_low > 0):
            return None
        
        h1_range = h1_high - h1_low
        position_pct = (entry - h1_low) / h1_range  # 0.0 = at low, 1.0 = at high
        
        if direction == 'LONG' and position_pct > 0.85:
            return GateResult(False, f'H1_OVEREXTENSION_LONG_{position_pct*100:.0f}%')
        if direction == 'SHORT' and position_pct < 0.15:
            return GateResult(False, f'H1_OVEREXTENSION_SHORT_{position_pct*100:.0f}%')
        
        return None

    def _check_swing_structure(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 26 v51.2: Multi-TF Swing Structure 9-Pattern + Reversal Hint."""
        return None # v53.0: MOD-47 - Disabled due to inaccuracy (Telemetry only)
        
        # v51.1 MOD-39: 9-pattern block rules
        BLOCK_LONG  = {'BEARISH', 'DESCENDING_TRIANGLE', 'DESCENDING_FLAT'}
        BLOCK_SHORT = {'BULLISH', 'ASCENDING_TRIANGLE', 'RISING_FLAT'}
        
        h1_swing = signal.get('h1_swing_structure', 'NEUTRAL')
        m5_swing = signal.get('m5_swing_structure', 'NEUTRAL')
        h1_reversal_hint = signal.get('h1_swing_reversal_hint', False)
        m5_reversal_hint = signal.get('m5_swing_reversal_hint', False)
        direction = signal.get('direction', '')
        signal_id = signal.get('signal_id', '')

        # Priority 1: H1 COMPRESSION → block all (range market)
        if h1_swing == 'COMPRESSION' and m5_swing == 'COMPRESSION':
            return GateResult(False, 'SWING_H1M5_COMPRESSION')

        # Priority 2: H1 conflicts direction → block
        # Counter-trend signals always bypass (REVERSAL_*, VP_REVERT, MEAN_REVERT)
        sig_type = signal.get('signal_type', '')
        is_counter_trend = sig_type.startswith('REVERSAL') or sig_type == 'VP_REVERT' or 'MEAN_REVERT' in sig_type

        # v51.2: Counter-trend signals always bypass swing structure gate
        if is_counter_trend:
            logger.info(f"[Gate] 👁️ SHADOW: {signal_id} | {sig_type} bypasses swing gate")
            signal['gate_status'] = 'SHADOW_PASSED'
            signal['block_reason'] = f'SWING_BYPASS_{sig_type}'
            return None

        if h1_swing in BLOCK_LONG and direction == 'LONG':
            if signal.get('h1_swing_reversal_hint', False):
                logger.info(f"[Gate] 👁️ SHADOW: {signal_id} | {h1_swing} vs LONG but REVERSAL_HINT")
                signal['gate_status'] = 'SHADOW_PASSED'
                signal['block_reason'] = f'SWING_H1_{h1_swing}_REVERSAL_HINT'
                return None
            return GateResult(False, f'SWING_{h1_swing}_vs_LONG')
        if h1_swing in BLOCK_SHORT and direction == 'SHORT':
            if signal.get('h1_swing_reversal_hint', False):
                logger.info(f"[Gate] 👁️ SHADOW: {signal_id} | {h1_swing} vs SHORT but REVERSAL_HINT")
                signal['gate_status'] = 'SHADOW_PASSED'
                signal['block_reason'] = f'SWING_H1_{h1_swing}_REVERSAL_HINT'
                return None
            return GateResult(False, f'SWING_{h1_swing}_vs_SHORT')

        # Priority 3: M5 conflicts → SHADOW only (collect data, don't block)
        m5_conflict = False
        shadow_reason = ''
        
        # TRUE_RANGE → shadow both directions (no clear direction)
        if m5_swing == 'TRUE_RANGE':
            m5_conflict = True
            shadow_reason = f'SWING_M5_TRUE_RANGE'
        elif m5_swing in BLOCK_LONG and direction == 'LONG':
            m5_conflict = True
            shadow_reason = f'SWING_M5_BLOCK_{m5_swing}_vs_LONG'
        elif m5_swing in BLOCK_SHORT and direction == 'SHORT':
            m5_conflict = True
            shadow_reason = f'SWING_M5_BLOCK_{m5_swing}_vs_SHORT'
        # M5 COMPRESSION → shadow (data collection)
        elif m5_swing == 'COMPRESSION':
            m5_conflict = True
            shadow_reason = 'SWING_M5_COMPRESSION'

        if m5_conflict:
            logger.info(f"[Gate] 👁️ SHADOW: {signal_id} | {shadow_reason} (data collect)")
            signal['gate_status'] = 'SHADOW_PASSED'
            signal['block_reason'] = shadow_reason

        return None  # Pass

    def reset(self):
        self._sent_signal_ids.clear()
        self._last_signal_time_by_type = {st: None for st in SIGNAL_TYPES}
        self._mitigated_zones.clear() # v47.0
        # v49.0: MOD-26 Delete state file
        if GATE_STATE_FILE.exists():
            GATE_STATE_FILE.unlink()
        logger.info("[Gate] State reset")
