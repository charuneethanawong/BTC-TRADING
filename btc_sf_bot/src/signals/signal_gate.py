"""
Signal Gate — v11.0

Gate checks BEFORE signal is sent to EA.
Blocks signals that fail any of these checks:
  1. Daily loss limit exceeded
  2. Max positions per mode (4 โหมดอิสระ: IPA, IOF, IPAF, IOFF)
  3. Hard lock (30s between signals)
  4. Duplicate signal_id
  5. Score threshold
  6. RR minimum

v11.0: 4 โหมดอิสระ — IPA, IOF, IPAF, IOFF
"""
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

from src.utils.logger import get_logger
from src.utils.decorators import log_errors, retry, circuit_breaker
from src.utils.metrics import timed_metric

logger = get_logger(__name__)

# Mode aliases for v11.0
MODE_ALIASES = {
    'IPA': 'IPA',
    'IOF': 'IOF',
    'IPA_FRVP': 'IPAF',
    'IOF_FRVP': 'IOFF',
    'IPAF': 'IPAF',
    'IOFF': 'IOFF',
}


def normalize_mode(mode: str) -> str:
    """Normalize mode name to standard form."""
    return MODE_ALIASES.get(mode, mode)


@dataclass
class GateResult:
    """
    Result of gate check.
    passed=True means signal can be sent to EA.
    passed=False means signal is blocked with reason.
    """
    passed: bool
    reason: str
    blocked_count: int = 0  # How many gates this signal failed

    def __str__(self) -> str:
        status = '✅ PASSED' if self.passed else f'❌ BLOCKED ({self.reason})'
        return f"GateResult: {status}"


@dataclass
class PositionInfo:
    """
    Information about an active position.
    Used for mode-based position counting.
    """
    ticket: int
    symbol: str
    direction: str
    mode: str          # 'IPA', 'IOF', 'IPAF', 'IOFF'
    open_time: datetime
    entry_price: float
    current_pnl: float = 0.0


@dataclass
class AccountState:
    """
    Account state snapshot for gate checks.
    """
    daily_pnl: float           # Today's P&L in USD
    daily_loss_pct: float      # Today's loss as % of starting balance
    equity: float              # Current equity
    balance: float             # Starting balance
    open_positions: List[PositionInfo]

    @staticmethod
    def empty() -> 'AccountState':
        """Create empty account state (all zeros)."""
        return AccountState(
            daily_pnl=0.0,
            daily_loss_pct=0.0,
            equity=0.0,
            balance=0.0,
            open_positions=[]
        )


class SignalGate:
    """
    Pre-send gate checks for all signals (v11.0: 4 โหมดอิสระ).

    Usage:
        gate = SignalGate()
        result = gate.check(
            signal=signal_dict,
            account_state=account_state,
            active_positions=positions_list
        )
        if result.passed:
            await self.send_signal(signal)
    """

    # Gate constants
    HARD_LOCK_SECONDS: int = 60           # Min seconds between signals
    MAX_POSITIONS_PER_MODE: int = 1       # Max 1 per mode per direction

    # Score thresholds
    SCORE_MIN: int = 6  # Unified for all modes

    # RR thresholds (v6.0: Unified to 1.0 for all modes)
    RR_MIN: float = 1.0

    # Daily loss thresholds
    DAILY_LOSS_LIMIT_PCT: float = 3.0     # 3% daily loss limit

    def __init__(self, config: dict = None):
        self.config = config or {}

        # Override from config if provided
        self.hard_lock_seconds = self.config.get('hard_lock_seconds', self.HARD_LOCK_SECONDS)
        self.max_positions_per_mode = self.config.get('max_positions_per_mode', self.MAX_POSITIONS_PER_MODE)
        
        # Mode-specific score thresholds
        self.score_thresholds = {
            'IPA': self.config.get('ipa', {}).get('score_threshold', self.config.get('ipa_frvp', {}).get('score_threshold', 10)),
            'IOF': self.config.get('iof', {}).get('score_threshold', self.config.get('iof_frvp', {}).get('score_threshold', 6)),
            'IPAF': self.config.get('ipa_frvp', {}).get('score_threshold', self.config.get('ipa', {}).get('score_threshold', 10)),
            'IOFF': self.config.get('iof_frvp', {}).get('score_threshold', self.config.get('iof', {}).get('score_threshold', 6)),
        }
        # Backward compatibility: keep score_min as fallback
        self.score_min = min(self.score_thresholds.values()) if self.score_thresholds else self.SCORE_MIN
        self.rr_min = self.config.get('rr_min', self.RR_MIN)
        
        # Daily loss threshold
        self.daily_loss_limit = self.config.get('daily_loss_limit_pct', self.DAILY_LOSS_LIMIT_PCT)

        # Internal state (not persisted — resets on bot restart)
        self._sent_signal_ids: set = set()
        # v12.6: Per-mode hard lock — each mode has its own lock timer
        self._last_signal_time_by_mode: Dict[str, Optional[datetime]] = {
            'IPA': None, 'IOF': None, 'IPAF': None, 'IOFF': None,
        }
        
        # v11.0: Per-mode directional lock — each mode has its own lock
        self._last_signal_by_mode: Dict[str, Optional[Dict[str, Any]]] = {
            'IPA': None,
            'IOF': None,
            'IPAF': None,
            'IOFF': None,
        }

    @log_errors
    @timed_metric("SignalGate.check")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def check(self,
             signal: Dict[str, Any],
             account_state: AccountState,
             active_positions: List[PositionInfo]) -> GateResult:
        """
        Run all gate checks against a signal (v11.0: 4 โหมดอิสระ).

        Checks are ordered by speed (fastest first).
        Returns immediately on first failure.

        Args:
            signal: Signal dictionary from SignalBuilder
            account_state: Current account state
            active_positions: List of open positions

        Returns:
            GateResult with passed=True or blocked reason
        """
        mode = normalize_mode(signal.get('mode', 'IPA'))
        direction = signal.get('direction', 'LONG')
        score = signal.get('score', 0)
        required_rr = signal.get('required_rr', 0)
        signal_id = signal.get('signal_id', '')

        # Gate 1: Score threshold (fast — just a number check)
        gate_result = self._check_score(signal, mode, score)
        if gate_result:
            return gate_result

        # Gate 2: RR threshold (fast — just a number check)
        gate_result = self._check_rr(signal, mode, required_rr)
        if gate_result:
            return gate_result

        # Gate 3: Daily loss limit (fast — just a number check)
        gate_result = self._check_daily_loss(account_state)
        if gate_result:
            return gate_result

        # Gate 4: Max positions per mode (v11.0: per mode+direction)
        gate_result = self._check_max_positions(mode, direction, active_positions)
        if gate_result:
            return gate_result

        # Gate 5: Hard lock time per-mode (v12.6 — each mode has its own lock)
        gate_result = self._check_hard_lock(mode, active_positions)
        if gate_result:
            return gate_result

        # Gate 6: Duplicate signal_id (fast — set lookup)
        gate_result = self._check_duplicate(signal_id)
        if gate_result:
            return gate_result

        # Gate 7: Regime-Adaptive Execution (v33.0)
        gate_result = self._check_regime_suitability(signal)
        if gate_result:
            return gate_result

        # Gate 8: Mandatory Order Flow Verification (v33.0)
        gate_result = self._check_wall_contradiction(signal)
        if gate_result:
            return gate_result

        # v12.3: Removed Gate 7 (Directional Lock) — let EA handle it
        # Directional lock causes false blocks when mode extraction from MT5 comment fails
        # EA has Hard Lock (15-30s) + Price Distance Guard + MaxPositions to prevent duplicates

        # All gates passed
        logger.debug(
            f"[Gate] ✅ All gates passed for {mode} {direction} | "
            f"Score: {score}/{20} | RR: {required_rr:.2f}"
        )
        return GateResult(passed=True, reason='PASSED')

    @log_errors
    @timed_metric("SignalGate.mark_sent")
    @retry(max_attempts=3, delay=0.1, backoff=2.0, exceptions=(Exception,))
    @circuit_breaker(failure_threshold=5, timeout=30.0, expected_exception=Exception)
    def mark_sent(self, signal: Dict[str, Any]):
        """
        Mark a signal as sent (updates internal state).
        Call this AFTER successful send.
        """
        signal_id = signal.get('signal_id', '')
        mode = normalize_mode(signal.get('mode', 'IPA'))
        
        self._sent_signal_ids.add(signal_id)
        # v12.6: Per-mode hard lock — set timer for THIS mode only
        self._last_signal_time_by_mode[mode] = datetime.now(timezone.utc)

        # v11.0: Store last signal per-mode for directional lock
        self._last_signal_by_mode[mode] = signal

        logger.debug(f"[Gate] Marked signal sent: {signal_id} ({mode})")

    def mark_blocked(self, signal: Dict[str, Any], reason: str):
        """
        Log a blocked signal (for debugging/auditing).
        """
        signal_id = signal.get('signal_id', 'unknown')
        mode = signal.get('mode', '?')
        score = signal.get('score', 0)
        logger.info(
            f"[Gate] ⛔ BLOCKED {mode} signal | "
            f"ID: {signal_id} | Score: {score} | Reason: {reason}"
        )

    def _check_score(self, signal: Dict[str, Any], mode: str, score: int) -> Optional[GateResult]:
        """Gate 1: Score must meet minimum threshold (mode-specific)."""
        # Get mode-specific threshold, fallback to unified minimum
        normalized_mode = normalize_mode(mode)
        mode_threshold = self.score_thresholds.get(normalized_mode, self.score_min)
        
        if score < mode_threshold:
            return GateResult(
                passed=False,
                reason=f'SCORE_TOO_LOW_{score}_min_{mode_threshold}',
                blocked_count=1
            )
        return None

    def _check_rr(self, signal: Dict[str, Any], mode: str, required_rr: float) -> Optional[GateResult]:
        """Gate 2: RR must meet minimum threshold (unified for all modes)."""
        if required_rr < self.rr_min * 0.85:  # 15% tolerance
            return GateResult(
                passed=False,
                reason=f'RR_TOO_LOW_{required_rr:.2f}_min_{self.rr_min}',
                blocked_count=1
            )
        return None

    def _check_daily_loss(self, account_state: AccountState) -> Optional[GateResult]:
        """Gate 3: Daily loss must not exceed limit."""
        if account_state.daily_loss_pct >= self.daily_loss_limit:
            return GateResult(
                passed=False,
                reason=f'DAILY_LOSS_LIMIT_{account_state.daily_loss_pct:.1f}%_max_{self.daily_loss_limit}%',
                blocked_count=1
            )
        return None

    def _check_max_positions(self, mode: str, direction: str,
                             active_positions: List[PositionInfo]) -> Optional[GateResult]:
        """
        Gate 4: Max positions per mode+direction (v11.0).
        
        Each mode (IPA, IOF, IPAF, IOFF) can have max 1 LONG and 1 SHORT.
        Example: IPA SHORT + IOF SHORT = OK (different modes)
                 IPA SHORT + IPA SHORT = BLOCK (same mode+direction)
        """
        # Normalize mode names
        mode = normalize_mode(mode)
        
        # Count positions for this mode+direction
        mode_dir_positions = [
            p for p in active_positions 
            if normalize_mode(p.mode) == mode and p.direction == direction
        ]

        if len(mode_dir_positions) >= self.max_positions_per_mode:
            return GateResult(
                passed=False,
                reason=f'MAX_POSITIONS_{mode}_{direction}_({len(mode_dir_positions)}/{self.max_positions_per_mode})',
                blocked_count=1
            )
        return None

    def _check_hard_lock(self, mode: str, active_positions=None) -> Optional[GateResult]:
        """
        Gate 5: Min time between signals for THIS mode only (v12.6).
        
        Per-mode lock means: IOF sends → IPAF is NOT blocked by IOF's lock.
        Only same-mode signals block each other.
        """
        mode = normalize_mode(mode)
        last_time = self._last_signal_time_by_mode.get(mode)
        
        if last_time is None:
            return None

        now = datetime.now(timezone.utc)
        elapsed = (now - last_time).total_seconds()

        # v12.8: Unlock if no position of this mode exists AND 2 mins passed
        if active_positions is not None:
            mode_positions = [p for p in active_positions if normalize_mode(getattr(p, "mode", "IPA")) == mode]
            if len(mode_positions) == 0 and elapsed >= 120:
                return None  # EA rejected or closed, unlock hard lock

        if elapsed < self.hard_lock_seconds:
            remaining = self.hard_lock_seconds - elapsed
            return GateResult(
                passed=False,
                reason=f'HARD_LOCK_{mode}_{remaining:.0f}s_remaining',
                blocked_count=1
            )
        return None

    def _check_duplicate(self, signal_id: str) -> Optional[GateResult]:
        """Gate 6: No duplicate signal_id."""
        if signal_id in self._sent_signal_ids:
            return GateResult(
                passed=False,
                reason=f'DUPLICATE_SIGNAL_{signal_id}',
                blocked_count=1
            )
        return None

    def _check_directional_lock(self, signal: Dict[str, Any], mode: str,
                                active_positions: Optional[List] = None) -> Optional[GateResult]:
        """
        Gate 7 (v11.3): Per-Mode Entry Price Lock

        Each mode has its own directional lock (IPA doesn't block IOFF, etc.).

        Block signal if:
          - Same direction as last signal for THIS mode
          - AND entry price hasn't moved >= 0.5 ATR from last entry
            (regardless of whether position exists)

        Unlock when:
          - Price moved >= 0.5 ATR from last entry (entry zone changed)
          - OR direction changed
          - OR position closed (still blocked by entry lock until price moves)
        """
        mode = normalize_mode(mode)
        last_signal = self._last_signal_by_mode.get(mode)

        if last_signal is None:
            return None

        last_dir = last_signal.get('direction', '')
        last_entry = last_signal.get('entry_price', 0)
        last_atr = last_signal.get('atr_m5', 300)

        if signal.get('direction') != last_dir:
            return None  # Different direction — allow

        # v11.5: Timeout unlock — if no position + 2 minutes passed, unlock
        # Use utc_timestamp (ISO format) for elapsed calculation
        # Fallback: use wall-clock per-mode timer from when mark_sent was called
        # v12.6: Use per-mode timer (was global _last_signal_time)
        last_ts_str = last_signal.get('utc_timestamp', '')
        elapsed = None  # None = unknown, use fallback
        if last_ts_str:
            from datetime import datetime as dt
            from dateutil import parser as dateutil_parser
            try:
                last_ts = dateutil_parser.isoparse(last_ts_str)
                elapsed = (dt.now(dt.timezone.utc).replace(tzinfo=last_ts.tzinfo) - last_ts).total_seconds()
            except Exception:
                pass  # Use per-mode fallback below
        # Fallback: use wall-clock per-mode time from when mark_sent was called
        if elapsed is None:
            last_mode_time = self._last_signal_time_by_mode.get(mode)
            elapsed = (datetime.now(timezone.utc) - last_mode_time).total_seconds() if last_mode_time else 9999

        # Check if this mode has active positions
        has_position = False
        if active_positions is not None:
            mode_positions = [p for p in active_positions if normalize_mode(getattr(p, 'mode', 'IPA')) == mode]
            has_position = len(mode_positions) > 0

        # v11.5: Unlock conditions
        # 1. No position + 2 minutes passed → unlock (EA didn't open)
        if not has_position and elapsed >= 120:
            return None

        # 2. Entry price moved >= 0.5 ATR → unlock (new entry zone)
        entry_price = signal.get('entry_price', 0)
        entry_distance = abs(entry_price - last_entry)
        min_entry_distance = last_atr * 0.5

        if entry_distance >= min_entry_distance:
            return None  # Entry zone moved enough

        # Otherwise → block
        return GateResult(
            passed=False,
            reason=f'Gate 7: ENTRY_LOCK_same_entry_{entry_distance:.0f}_below_{min_entry_distance:.0f}'
        )


    def _check_regime_suitability(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 7: Regime-Adaptive Execution (v33.0)."""
        regime = signal.get('regime', 'RANGING')
        signal_type = signal.get('signal_type', 'MOMENTUM')
        
        if regime == 'DEAD':
            return GateResult(
                passed=False,
                reason='REGIME_DEAD_no_trades',
                blocked_count=1
            )

        if regime == 'RANGING':
            # In Ranging mode, MOMENTUM signals are blocked.
            # Only IPAF_POC, IPAF_EMA, or specialized reversal types allowed.
            if 'MOMENTUM' in signal_type or signal_type == 'IPA':
                return GateResult(
                    passed=False,
                    reason=f'REGIME_RANGING_blocked_{signal_type}',
                    blocked_count=1
                )
        return None

    def _check_wall_contradiction(self, signal: Dict[str, Any]) -> Optional[GateResult]:
        """Gate 8: Mandatory Order Flow Verification (v33.0)."""
        direction = signal.get('direction', 'LONG')
        wall_scan = signal.get('wall_scan', {})
        
        if not wall_scan:
            return None
            
        ratio = wall_scan.get('raw_ratio', 1.0)
        dominant = wall_scan.get('raw_dominant', 'NEUTRAL')
        
        # Threshold from architecture plan: 50x
        threshold = self.config.get('max_opposite_wall_ratio', 50.0)
        
        if direction == 'LONG' and dominant == 'ASK' and ratio >= threshold:
            return GateResult(
                passed=False,
                reason=f'WALL_CONTRADICTION_LONG_vs_ASK_{ratio:.1f}x',
                blocked_count=1
            )
        elif direction == 'SHORT' and dominant == 'BID' and ratio >= threshold:
            return GateResult(
                passed=False,
                reason=f'WALL_CONTRADICTION_SHORT_vs_BID_{ratio:.1f}x',
                blocked_count=1
            )
            
        return None

    def reset(self):
        """Reset all internal state (call on bot restart or new trading day)."""
        self._sent_signal_ids.clear()
        self._last_signal_time_by_mode = {
            'IPA': None, 'IOF': None, 'IPAF': None, 'IOFF': None,
        }
        self._last_signal_by_mode = {
            'IPA': None, 'IOF': None, 'IPAF': None, 'IOFF': None,
        }
        
        logger.info("[Gate] Gate state reset")

    def get_stats(self) -> Dict[str, Any]:
        """Get gate statistics for monitoring."""
        return {
            'sent_count': len(self._sent_signal_ids),
            'signal_ids': list(self._sent_signal_ids),
            # v12.6: Per-mode hard lock timers
            'last_signal_time_by_mode': {
                m: t.isoformat() if t else None
                for m, t in self._last_signal_time_by_mode.items()
            },
            'last_signals_by_mode': {k: v.get('signal_id') if v else None for k, v in self._last_signal_by_mode.items()},
        }




