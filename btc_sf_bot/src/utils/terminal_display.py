"""
Terminal Display Module - v40.2 (Fully Independent Signal Types)
ANSI Colors + Box Drawing + Icons + AI Section
v40.2: Removed group from SIGNAL_CONFIG — each signal type is fully independent
v40.1: Updated detector_header, detector_no_signal, detector_signal, detector_blocked
v51.0: Updated for unified signal types (MOMENTUM, MEAN_REVERT, ABSORPTION, IPA, REVERSAL_OB, REVERSAL_OS, VP_*)
"""
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List


class TerminalDisplay:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BG_BLUE = '\033[44m'

    W = 66  # width

    # v51.0: Signal type colors and icons — 10 types (IPA unified)
    SIGNAL_CONFIG = {
        'MOMENTUM':    {'color': GREEN,  'icon': '🚀'},
        'MEAN_REVERT': {'color': YELLOW, 'icon': '🔄'},
        'ABSORPTION':  {'color': CYAN,   'icon': '🧱'},
        'REVERSAL_OB': {'color': RED,    'icon': '🔻'},   # v40.3: reversal short
        'REVERSAL_OS': {'color': GREEN, 'icon': '🔺'},   # v40.3: reversal long
        'IPA':         {'color': GREEN,  'icon': '📦'},
        # v43.7: VP signal types
        'VP_BOUNCE':   {'color': GREEN,  'icon': '🔃'},
        'VP_BREAKOUT': {'color': CYAN,   'icon': '💥'},
        'VP_ABSORB':   {'color': YELLOW, 'icon': '🧲'},
        'VP_REVERT':   {'color': MAGENTA,'icon': '↩️'},
        'VP_POC':      {'color': BLUE,   'icon': '🎯'},
        # Legacy mode support
        'IOF':         {'color': BLUE,   'icon': '🌊'},
        'IPAF':        {'color': MAGENTA,'icon': '📊'},
        'IOFF':        {'color': CYAN,   'icon': '🌊'},
    }

    def __init__(self, use_colors: bool = True):
        self.use_colors = use_colors and sys.stdout.isatty()

    @staticmethod
    def header(price, session, regime, timestamp):
        T = TerminalDisplay
        text = f"  💓 BTC ${price:,.0f} | {session} | {regime} | {timestamp}  "
        print(f"\n{T.BOLD}{T.BG_BLUE}{T.WHITE}{'═' * T.W}{T.RESET}")
        print(f"{T.BOLD}{T.BG_BLUE}{T.WHITE}{text:<{T.W}}{T.RESET}")
        print(f"{T.BOLD}{T.BG_BLUE}{T.WHITE}{'═' * T.W}{T.RESET}")

    @staticmethod
    def ai_section(ai_result, age_seconds=None):
        T = TerminalDisplay
        if not ai_result:
            print(f"\n{T.DIM}┌─ 🤖 AI ANALYSIS {'─' * (T.W - 18)}┐{T.RESET}")
            print(f"{T.DIM}│  waiting for first M5 candle close...{T.RESET}")
            print(f"{T.DIM}└{'─' * T.W}┘{T.RESET}")
            return

        # v37.5: AI responds with signal (BUY/SELL/WAIT) not bias
        signal = ai_result.get('action', 'WAIT')  # BUY/SELL/WAIT
        reason = ai_result.get('reason', '')
        key_level = ai_result.get('key_level', 0)

        color = T.GREEN if signal == 'BUY' else T.RED if signal == 'SELL' else T.YELLOW
        icon = '🟢' if signal == 'BUY' else '🔴' if signal == 'SELL' else '🟡'

        # v27.3: Show age if using cached result
        age_str = ""
        if age_seconds is not None and age_seconds > 30:
            mins = age_seconds // 60
            secs = age_seconds % 60
            age_str = f" {T.DIM}({mins}m{secs}s ago){T.RESET}"

        print(f"\n{T.BOLD}{T.CYAN}┌─ 🤖 AI ANALYSIS {'─' * (T.W - 19)}┐{T.RESET}")
        print(f"{T.CYAN}│{T.RESET}  {icon} Signal: {color}{T.BOLD}{signal}{T.RESET}{age_str}")
        if reason:
            import textwrap
            # หักขอบซ้ายขวาออก (T.W - 6)
            wrapped = textwrap.wrap(reason, width=T.W - 6)
            for i, line in enumerate(wrapped):
                prefix = '"' if i == 0 else ' '
                suffix = '"' if i == len(wrapped) - 1 else ' '
                print(f"{T.CYAN}│{T.RESET}  {T.DIM}{prefix}{line}{suffix}{T.RESET}")
        if key_level > 0:
            print(f"{T.CYAN}│{T.RESET}  Key Level: ${key_level:,.0f}")
        print(f"{T.CYAN}└{'─' * T.W}┘{T.RESET}")

    @staticmethod
    def market_context(
        # v27.0: Accept Result Objects directly (single source of truth)
        regime=None,           # RegimeResult object
        h1_bias_result=None,   # H1BiasResult object
        snapshot=None,         # MarketSnapshot object
        # Legacy parameters for backward compatibility
        h1_dist=0, pullback_status="NONE", wall_info="",
        news_context="none",  # v27.3: upcoming news event
        anchor_info=None,     # v43.8: Actual VP anchor (major swing used)
        ):
        """
        v27.0: Refactored to accept Result Objects directly.
        v42.8: Added Persistence Tracking (imbalance_avg, wall_stability)
        """
        T = TerminalDisplay

        # Extract from regime object
        regime_str = "RANGING"
        adx_h1 = 25.0
        plus_di = minus_di = di_spread = atr_ratio = 0
        if regime and hasattr(regime, 'regime'):
            regime_str = regime.regime
            adx_h1 = getattr(regime, 'adx_h1', 25.0)
            plus_di = getattr(regime, 'plus_di', 0)
            minus_di = getattr(regime, 'minus_di', 0)
            di_spread = getattr(regime, 'di_spread', 0)
            atr_ratio = getattr(regime, 'atr_ratio', 0)

        # Extract from h1_bias_result object
        h1_bias = "NEUTRAL"
        bias_level = ""
        ema9 = ema20 = ema50 = 0
        l0 = l1 = l2 = l3 = "NEUTRAL"
        lc = lr = "NEUTRAL"
        lr_count = 0
        if h1_bias_result and hasattr(h1_bias_result, 'bias'):
            h1_bias = h1_bias_result.bias
            bias_level = getattr(h1_bias_result, 'bias_level', "")
            ema9 = getattr(h1_bias_result, 'ema9', 0)
            ema20 = getattr(h1_bias_result, 'ema20', 0)
            ema50 = getattr(h1_bias_result, 'ema50', 0)
            l0 = getattr(h1_bias_result, 'l0', "NEUTRAL")
            l1 = getattr(h1_bias_result, 'l1', "NEUTRAL")
            l2 = getattr(h1_bias_result, 'l2', "NEUTRAL")
            l3 = getattr(h1_bias_result, 'l3', "NEUTRAL")
            lc = getattr(h1_bias_result, 'lc', "NEUTRAL")
            lr = getattr(h1_bias_result, 'lr', "NEUTRAL")
            lr_count = getattr(h1_bias_result, 'lr_count', 0)

        # Extract from snapshot object
        atr_m5 = delta = der = volume_ratio = oi_change = funding_rate = 0
        imbalance_avg = 1.0
        wall_stability = 0
        der_dir = 'N'
        der_persist = 0
        der_sustain = ''
        m5_state = ''
        m5_er = 0
        m5_dist = 0
        if snapshot and hasattr(snapshot, 'atr_m5'):
            atr_m5 = getattr(snapshot, 'atr_m5', 0)
            delta = getattr(snapshot, 'delta', 0)
            der = getattr(snapshot, 'der', 0)
            der_dir = getattr(snapshot, 'der_direction', 'NEUTRAL')[0]  # L/S/N
            der_persist = getattr(snapshot, 'der_persistence', 0)
            der_sustain = getattr(snapshot, 'der_sustainability', '')
            volume_ratio = getattr(snapshot, 'volume_ratio_m5', 0)
            oi_change = getattr(snapshot, 'oi_change_pct', 0)
            imbalance_avg = getattr(snapshot, 'imbalance_avg_5m', 1.0)
            wall_stability = getattr(snapshot, 'wall_stability_sec', 0)
            funding_rate = getattr(snapshot, 'funding_rate', 0)
            m5_state = getattr(snapshot, 'm5_state', '')
            m5_er = getattr(snapshot, 'm5_efficiency', 0)
            m5_dist = getattr(snapshot, 'm5_dist_pct', 0)
            vp_poc_shift = getattr(snapshot, 'vp_poc_shift', 0) if snapshot else 0

        # ADX color based on trend strength
        adx_color = T.GREEN if regime_str == "TRENDING" else T.YELLOW if regime_str == "RANGING" else T.RED

        # H1 bias from bot analysis (L0 structure)
        bias_color = T.GREEN if h1_bias == "BULLISH" else T.RED if h1_bias == "BEARISH" else T.YELLOW

        # Layer info: L0, L1, L2, L3 status
        def layer_color(d):
            return T.GREEN if d == "BULLISH" else T.RED if d == "BEARISH" else T.DIM

        layer_str = f" | L0:{layer_color(l0)}{l0[0] if l0 != 'NEUTRAL' else '-'}{T.RESET} L1:{layer_color(l1)}{l1[0] if l1 != 'NEUTRAL' else '-'}{T.RESET} L2:{layer_color(l2)}{l2[0] if l2 != 'NEUTRAL' else '-'}{T.RESET} L3:{layer_color(l3)}{l3[0] if l3 != 'NEUTRAL' else '-'}{T.RESET}"

        # LC/LR info
        lc_color = T.GREEN if lc == "BULLISH" else T.RED if lc == "BEARISH" else T.DIM
        lr_color = T.GREEN if lr == "BULLISH" else T.RED if lr == "BEARISH" else T.DIM
        lc_lr_str = f" | LC:{lc_color}{lc[0] if lc != 'NEUTRAL' else '-'}{T.RESET} LR:{lr_color}{lr[0] if lr != 'NEUTRAL' else '-'}{T.RESET}({lr_count}/4)"

        print(f"\n{T.BOLD}┌─ 📊 MARKET {'─' * (T.W - 13)}┐{T.RESET}")
        print(f"│  {bias_color}{h1_bias:8s}{T.RESET} {bias_level if bias_level else ''} (adj:{h1_bias}){layer_str}")
        print(f"│  EMA: {ema9:.0f} / {ema20:.0f} / {ema50:.0f} | Dist H1:{h1_dist:.1f}% M5:{abs(m5_dist):.1f}%")

        # v27.0: Enhanced display with snapshot data
        if atr_m5 is not None and delta is not None and der is not None:
            print(f"│  {regime_str:10s} ADX:{adx_color}{adx_h1:.0f}{T.RESET} | +DI:{plus_di:.0f} -DI:{minus_di:.0f} (spread:{di_spread:.0f}) | ATR_R:{atr_ratio:.2f}")
            # v27.2: DER with persistence + sustainability
            der_extra = f" {der_dir}×{der_persist}" if der_persist > 0 else ""
            der_sust = f" {der_sustain}" if der_sustain and der_sustain not in ('NEUTRAL', 'TOO_EARLY', '') else ""
            
            # v42.8: Show 5m Imbalance
            print(f"│  ATR:{atr_m5:.0f} | Delta:{delta:+.1f} | DER:{der:.3f}{der_extra}{der_sust} | Vol:{volume_ratio:.1f}x (5m:{T.DIM}{imbalance_avg:.2f}x{T.RESET})")
            
            # v27.2: M5 flow direction (same as what AI sees)
            m5_dir = der_dir if der_persist > 0 else 'N'
            m5_flow_color = T.GREEN if m5_dir == 'L' else T.RED if m5_dir == 'S' else T.DIM
            state_colors = {'SIDEWAY': T.YELLOW, 'ACCUMULATION': T.MAGENTA, 'TRENDING': T.GREEN, 'EXHAUSTION': T.RED, 'RANGING': T.DIM, 'PULLBACK': T.CYAN, 'RECOVERY': T.CYAN}
            sc = state_colors.get(m5_state, T.DIM)
            # v28.1: M5 EMA position + candle pattern + range
            m5_ema_pos = getattr(snapshot, 'm5_ema_position', 'BETWEEN') if snapshot else 'BETWEEN'
            m5_pattern = getattr(snapshot, 'm5_candle_pattern', 'NONE') if snapshot else 'NONE'
            m5_rng_h = getattr(snapshot, 'm5_range_high', 0) if snapshot else 0
            m5_rng_l = getattr(snapshot, 'm5_range_low', 0) if snapshot else 0
            ema_pos_color = T.GREEN if m5_ema_pos == 'ABOVE_ALL' else T.RED if m5_ema_pos == 'BELOW_ALL' else T.DIM
            pattern_str = f" {T.CYAN}{m5_pattern}{T.RESET}" if m5_pattern != 'NONE' else ""
            range_str = f" Rng:{m5_rng_l:.0f}-{m5_rng_h:.0f}" if m5_rng_h > 0 else ""
            # v43.1: M5 Bias display
            m5_bias = getattr(snapshot, 'm5_bias', 'NEUTRAL') if snapshot else 'NEUTRAL'
            m5_bias_level = getattr(snapshot, 'm5_bias_level', 'NEUTRAL') if snapshot else 'NEUTRAL'
            bias_color = T.GREEN if m5_bias == 'BULLISH' else T.RED if m5_bias == 'BEARISH' else T.YELLOW
            bias_str = f"{bias_color}{m5_bias}({m5_bias_level}){T.RESET}"
            
            print(f"│  M5:{bias_str} {m5_flow_color}{m5_dir}{T.RESET} {sc}{m5_state}{T.RESET}(ER:{m5_er:.2f}) {ema_pos_color}{m5_ema_pos}{T.RESET}{pattern_str}{range_str}")

            # v42.8: Show Wall Stability
            print(f"│  PB:{pullback_status} | {wall_info} ({T.DIM}stable:{wall_stability}s{T.RESET}) | OI:{oi_change:+.2f}% | Fund:{funding_rate:.4f}{lc_lr_str}")
            
            # v43.7: VP Line
            vp_poc = getattr(snapshot, 'vp_poc', 0) if snapshot else 0
            vp_vah = getattr(snapshot, 'vp_vah', 0) if snapshot else 0
            vp_val = getattr(snapshot, 'vp_val', 0) if snapshot else 0
            vp_price_vs_va = getattr(snapshot, 'vp_price_vs_va', 'INSIDE') if snapshot else 'INSIDE'
            
            if vp_poc:
                shift_color = T.GREEN if vp_poc_shift > 0 else T.RED if vp_poc_shift < 0 else T.DIM
                print(f"│  VP: POC:{vp_poc:,.0f} VAH:{vp_vah:,.0f} VAL:{vp_val:,.0f} | {vp_price_vs_va} | POC shift:{shift_color}{vp_poc_shift:+.0f}{T.RESET}")
            
            # v43.8: Show actual VP anchor (major swing used for analysis) - ALWAYS show if exists
            if anchor_info and anchor_info.get('price', 0) > 0:
                atype = anchor_info.get('type', 'unknown')
                aprice = anchor_info.get('price', 0)
                amove = anchor_info.get('move', 0)
                aatr = anchor_info.get('atr_h1', 0)
                
                type_color = T.GREEN if 'low' in atype else T.RED if 'high' in atype else T.DIM
                move_color = T.GREEN if amove > 0 else T.RED
                
                type_label = 'SH' if 'high' in atype else 'SL' if 'low' in atype else '24h'
                print(f"│  Anchor: {type_color}{type_label}:{aprice:,.0f}{T.RESET} | move:{move_color}{amove:+,.0f}{T.RESET} | ATR:{aatr:,.0f}")
            
            # v43.9: Show HVN/LVN if available
            vp_nearest_hvn = getattr(snapshot, 'vp_nearest_hvn', 0) if snapshot else 0
            vp_nearest_lvn = getattr(snapshot, 'vp_nearest_lvn', 0) if snapshot else 0
            if vp_nearest_hvn > 0 or vp_nearest_lvn > 0:
                hvn_str = f"HVN:{vp_nearest_hvn:,.0f}" if vp_nearest_hvn > 0 else ""
                lvn_str = f"LVN:{vp_nearest_lvn:,.0f}" if vp_nearest_lvn > 0 else ""
                sep = " | " if hvn_str and lvn_str else ""
                print(f"│  Nodes: {T.GREEN}{hvn_str}{T.RESET}{sep}{T.RED}{lvn_str}{T.RESET}")
        else:
            print(f"│  {regime_str:10s} ADX:{adx_color}{adx_h1:.0f}{T.RESET}{lc_lr_str}")
            print(f"│  Pullback: {pullback_status} | {wall_info}")

        # v27.3: News warning
        if news_context and news_context != 'none':
            print(f"│  {T.RED}⚠ NEWS: {news_context}{T.RESET}")
        print(f"└{'─' * T.W}┘{T.RESET}")

    @staticmethod
    def mode_header(mode_name, mode_num):
        """v40.0: Mode header — supports both legacy modes and new signal types."""
        T = TerminalDisplay
        # v40.0: Use SIGNAL_CONFIG for colors/icons
        cfg = T.SIGNAL_CONFIG.get(mode_name, {'color': T.WHITE, 'icon': '📡'})
        c = cfg['color']
        icon = cfg['icon']
        fill_len = max(0, T.W - len(mode_name) - 18)
        print(f"\n{c}──── {icon} [{mode_name}] {'─' * fill_len}{T.RESET}")

    @staticmethod
    def detector_header(signal_type, timing=''):
        """v40.2: Detector header — no groups, fully independent signal types."""
        T = TerminalDisplay
        cfg = T.SIGNAL_CONFIG.get(signal_type, {'color': T.WHITE, 'icon': '📡'})
        c = cfg['color']
        icon = cfg['icon']
        timing_str = f" ({timing})" if timing else ""
        fill = '─' * max(0, T.W - len(signal_type) - len(timing_str) - 12)
        print(f"\n{c}──── {icon} [{signal_type}]{timing_str} {fill}{T.RESET}")

    @staticmethod
    def gate(name, status, detail=""):
        T = TerminalDisplay
        # status can be True, False, or icons
        icon = '✅' if status is True else '❌' if status is False else status
        color = T.GREEN if status is True else T.RED if status is False else T.YELLOW
        print(f"  {name:10s} {icon} {T.DIM}{detail}{T.RESET}")

    @staticmethod
    def score_line(value, threshold, sent=False):
        T = TerminalDisplay
        if sent:
            print(f"  {'Score:':10s} {T.GREEN}{T.BOLD}{value}/{threshold} → SIGNAL SENT{T.RESET}")
        else:
            print(f"  {'Score:':10s} {T.RED}{value}/{threshold} → BLOCKED{T.RESET}")

    @staticmethod
    def detector_no_signal(signal_type, reason='No signal detected'):
        """v40.1: Show WHY detector didn't find a signal."""
        T = TerminalDisplay
        cfg = T.SIGNAL_CONFIG.get(signal_type, {'color': T.WHITE, 'icon': '📡'})
        print(f"  {T.DIM}Detect:  ❌ {reason}{T.RESET}")

    @staticmethod
    def detector_signal(signal_type, direction, score, threshold, entry_price, sl, tp, rr,
                        der=0, der_dir='', wall='', m5_state=''):
        """v40.1: Display a signal from a detector with market context."""
        T = TerminalDisplay
        cfg = T.SIGNAL_CONFIG.get(signal_type, {'color': T.WHITE, 'icon': '📡'})
        c = cfg['color']
        icon = cfg['icon']
        dir_color = T.GREEN if direction == 'LONG' else T.RED

        fill = '─' * max(0, T.W - len(signal_type) - len(direction) - 8)
        print(f"\n{T.BOLD}{c}┌─ {icon} {signal_type} {dir_color}{direction}{c} {fill}┐{T.RESET}")
        print(f"{c}│{T.RESET}  Score: {T.BOLD}{score}/{threshold}{T.RESET} | Entry: ${entry_price:,.0f}")
        print(f"{c}│{T.RESET}  SL: ${sl:,.0f} | TP: ${tp:,.0f} | RR: {rr:.2f}")
        if der or wall or m5_state:
            der_str = f"DER:{der:.3f} {der_dir}" if der else ""
            wall_str = f"| {wall}" if wall else ""
            m5_str = f"| M5:{m5_state}" if m5_state else ""
            context_parts = [p for p in [der_str, wall_str, m5_str] if p]
            if context_parts:
                print(f"{c}│{T.RESET}  {' '.join(context_parts)}")
        print(f"{c}└{'─' * T.W}┘{T.RESET}")

    @staticmethod
    def detector_blocked(signal_type, direction, score, reason, gate_name='Gate'):
        """v40.1: Display a blocked detector signal with context."""
        T = TerminalDisplay
        cfg = T.SIGNAL_CONFIG.get(signal_type, {'color': T.WHITE, 'icon': '📡'})
        dir_color = T.GREEN if direction == 'LONG' else T.RED
        print(f"  {T.DIM}Detect:  ✅ {dir_color}{direction}{T.RESET} Score:{score}")
        print(f"  {gate_name}:    {T.RED}❌ {reason}{T.RESET}")

    @staticmethod
    def footer(signals_sent, cycle_time, ai_stats=None, use_detectors=False):
        """
        v40.0: Updated footer for signal type architecture.

        Args:
            signals_sent: List of signal types or mode names sent this cycle
            cycle_time: Cycle duration in seconds
            ai_stats: AI trade statistics
            use_detectors: Whether v40.0 detector architecture is active
        """
        T = TerminalDisplay
        print(f"\n{T.DIM}{'─' * T.W}{T.RESET}")

        # v40.0: Show architecture mode
        if use_detectors:
            print(f"  {T.BOLD}{T.CYAN}🔧 Architecture: v40.0 Detectors (7 types){T.RESET}")
        else:
            print(f"  {T.DIM}🔧 Architecture: Legacy 4-Mode (IPA/IOF/IPAF/IOFF){T.RESET}")

        # v19.0: AI Trade Stats
        if ai_stats:
            wins = ai_stats.get('wins', 0)
            losses = ai_stats.get('losses', 0)
            total = ai_stats.get('total', 0)
            skipped = ai_stats.get('skipped', 0)
            opened = ai_stats.get('opened', 0)

            stats_str = f"🤖 AI Stats: {total} trades ({T.GREEN}{wins}W{T.RESET} {T.RED}{losses}L{T.RESET})"
            if skipped > 0 or opened > 0:
                pending = ai_stats.get('signal_sent', 0)  # v27.1: pending = SIGNAL_SENT (not OPENED)
                stats_str += f" | {T.YELLOW}{skipped} skipped{T.RESET}"
                if opened > 0:
                    stats_str += f" | {T.CYAN}{opened} open{T.RESET}"
                if pending > 0:
                    stats_str += f" | {T.MAGENTA}{pending} pending{T.RESET}"
            print(f"  {stats_str}")

        if signals_sent:
            # v40.0: Format signals with icons and colors
            sig_parts = []
            for sig in signals_sent:
                cfg = T.SIGNAL_CONFIG.get(sig, {'color': T.WHITE, 'icon': '📡'})
                c = cfg['color']
                icon = cfg['icon']
                sig_parts.append(f"{c}{icon} {sig}{T.RESET}")
            sigs = ' | '.join(sig_parts)
            print(f"  {T.GREEN}📨 Signals: {sigs}{T.RESET}")
        else:
            print(f"  {T.DIM}📨 No signals this cycle{T.RESET}")

        print(f"  {T.DIM}⏱️  Cycle: {cycle_time:.1f}s{T.RESET}")
        print(f"{T.BOLD}{'═' * T.W}{T.RESET}")



# Singleton instance logic
_instance = TerminalDisplay()
def get_display(): return _instance

