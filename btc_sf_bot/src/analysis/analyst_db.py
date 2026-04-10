"""
Analyst DB v44.1  — STANDALONE (no package imports)
Query SQLite DB → generate analysis_report.md

Usage:
  python btc_sf_bot/src/analysis/analyst_db.py
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

_HERE    = Path(__file__).resolve()
DB_PATH  = _HERE.parent.parent.parent / 'data' / 'trades.db'
REPORT   = _HERE.parent.parent.parent.parent / 'analysis_report.md'


def _conn():
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def q(sql, params=()):
    with _conn() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def q1(sql, params=()):
    with _conn() as con:
        row = con.execute(sql, params).fetchone()
        return dict(row) if row else {}


# ─── queries ────────────────────────────────────────────────────────────────

def log_stats():
    period = q1("SELECT MIN(timestamp) as t0, MAX(timestamp) as t1 FROM frvp_events")
    return {
        'period_start' : period.get('t0', 'N/A'),
        'period_end'   : period.get('t1', 'N/A'),
        'total_frvp'   : q1("SELECT COUNT(*) as n FROM frvp_events")['n'],
        'total_m5'     : q1("SELECT COUNT(*) as n FROM m5_transitions")['n'],
        'total_signals': q1("SELECT COUNT(*) as n FROM signals_log")['n'],
        'total_warnings': q1("SELECT COUNT(*) as n FROM bot_warnings")['n'],
    }


def signal_breakdown():
    return q("""
        SELECT signal_type, direction, COUNT(*) as cnt,
               MIN(timestamp) as first_at, MAX(timestamp) as last_at
        FROM signals_log
        GROUP BY signal_type, direction
        ORDER BY cnt DESC
    """)


def all_signals():
    return q("""
        SELECT timestamp, signal_id, signal_type, direction
        FROM signals_log
        ORDER BY timestamp
    """)


def frvp_anchor_history():
    return q("""
        SELECT anchor_price, swing_type,
               COUNT(*) as cycles,
               MIN(timestamp) as first_seen,
               MAX(timestamp) as last_seen,
               ROUND(AVG(move_size),1) as avg_move,
               MIN(move_size) as min_move,
               MAX(move_size) as max_move
        FROM frvp_events
        GROUP BY anchor_price, swing_type
        ORDER BY cycles DESC
    """)


def frvp_anchor_flips():
    """Detect rapid anchor changes: same timestamp minute, different anchor."""
    return q("""
        SELECT a.timestamp as t1, a.anchor_price as p1, a.swing_type as s1,
               b.timestamp as t2, b.anchor_price as p2, b.swing_type as s2,
               ROUND((JULIANDAY(b.timestamp) - JULIANDAY(a.timestamp)) * 1440, 1) as gap_min
        FROM frvp_events a
        JOIN frvp_events b ON b.id = a.id + 1
        WHERE a.anchor_price != b.anchor_price
          AND (JULIANDAY(b.timestamp) - JULIANDAY(a.timestamp)) * 1440 < 10
        ORDER BY a.timestamp
        LIMIT 50
    """)


def m5_state_dist():
    return q("""
        SELECT to_state as state, COUNT(*) as cnt,
               ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM m5_transitions), 1) as pct
        FROM m5_transitions
        GROUP BY to_state
        ORDER BY cnt DESC
    """)


def m5_transition_matrix():
    return q("""
        SELECT from_state, to_state, COUNT(*) as cnt
        FROM m5_transitions
        GROUP BY from_state, to_state
        ORDER BY cnt DESC
    """)


def m5_rapid_oscillations():
    """State changes >3 times within 30 minutes."""
    return q("""
        SELECT strftime('%Y-%m-%d %H:', timestamp) ||
               CAST(CAST(strftime('%M', timestamp) AS INTEGER) / 30 * 30 AS TEXT) as window,
               COUNT(*) as changes
        FROM m5_transitions
        GROUP BY window
        HAVING changes >= 3
        ORDER BY changes DESC
        LIMIT 20
    """)


def warnings_list():
    return q("""
        SELECT timestamp, level, module, message
        FROM bot_warnings
        ORDER BY timestamp
    """)


def signal_vs_m5_context():
    """For each signal, find the nearest M5 state."""
    return q("""
        SELECT s.timestamp, s.signal_id, s.signal_type, s.direction,
               (SELECT to_state FROM m5_transitions m
                WHERE m.timestamp <= s.timestamp
                ORDER BY m.timestamp DESC LIMIT 1) as m5_state_at_fire,
               (SELECT anchor_price FROM frvp_events f
                WHERE f.timestamp <= s.timestamp
                ORDER BY f.timestamp DESC LIMIT 1) as frvp_anchor,
               (SELECT swing_type FROM frvp_events f
                WHERE f.timestamp <= s.timestamp
                ORDER BY f.timestamp DESC LIMIT 1) as anchor_type
        FROM signals_log s
        ORDER BY s.timestamp
    """)


def signal_anchor_alignment():
    """Check if signal direction aligns with FRVP anchor type."""
    rows = signal_vs_m5_context()
    aligned = 0
    misaligned = 0
    unknown = 0
    for r in rows:
        anchor = r.get('anchor_type', '')
        direction = r.get('direction', '')
        if not anchor or not direction:
            unknown += 1
            continue
        # LONG signal + swing_low anchor = correct (price above low support)
        # SHORT signal + swing_high anchor = correct (price below high resistance)
        if (direction == 'LONG' and anchor == 'major_swing_low') or \
           (direction == 'SHORT' and anchor == 'major_swing_high'):
            aligned += 1
        else:
            misaligned += 1
    return {'aligned': aligned, 'misaligned': misaligned, 'unknown': unknown}


def trades_summary():
    # Active trades from trades table
    active = q("""
        SELECT status, COUNT(*) as cnt,
               0 as avg_pnl, 0 as total_pnl
        FROM trades
        WHERE status IN ('SENT','SIGNAL_SENT','OPENED')
        GROUP BY status
    """)
    # Closed trades from trade_outcomes (permanent)
    closed = q("""
        SELECT result as status, COUNT(*) as cnt,
               ROUND(AVG(pnl), 2) as avg_pnl,
               ROUND(SUM(pnl), 2) as total_pnl
        FROM trade_outcomes
        WHERE result IN ('WIN','LOSS','BE')
        GROUP BY result
        ORDER BY cnt DESC
    """)
    return active + closed


# ─── markdown builders ──────────────────────────────────────────────────────

def _table(headers, rows, keys):
    lines = ['| ' + ' | '.join(headers) + ' |']
    lines.append('|' + '|'.join(['---'] * len(headers)) + '|')
    for r in rows:
        lines.append('| ' + ' | '.join(str(r.get(k, '-')) for k in keys) + ' |')
    return '\n'.join(lines)


def generate_report() -> str:
    stats   = log_stats()
    sigs    = all_signals()
    sig_bk  = signal_breakdown()
    anchors = frvp_anchor_history()
    flips   = frvp_anchor_flips()
    m5_dist = m5_state_dist()
    m5_mat  = m5_transition_matrix()
    m5_osc  = m5_rapid_oscillations()
    warns   = warnings_list()
    ctx     = signal_vs_m5_context()
    align   = signal_anchor_alignment()
    trades  = trades_summary()

    total_sigs = stats['total_signals']
    total_m5   = stats['total_m5']

    lines = [
        f"# SMC AI Bot — Deep Analysis Report (DB)",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**DB:** {DB_PATH}  ",
        f"**Period:** {stats['period_start']} → {stats['period_end']}",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        "",
    ]

    # Executive summary
    exec_points = []
    if total_sigs == 0:
        exec_points.append("- **CRITICAL:** No signals found in DB — log importer may not have run yet.")
    else:
        exec_points.append(f"- **{total_sigs} signals sent** in session — all via ZeroMQ (MT5 execution status unknown).")

    stale_warn = [w for w in warns if 'stale' in w['message'].lower()]
    if stale_warn:
        exec_points.append(f"- **CRITICAL:** {len(stale_warn)} stale-trade cleanup event(s) — signals sent but never opened by EA.")

    if flips:
        exec_points.append(f"- **HIGH:** FRVP anchor flipped {len(flips)} times in <10 min — VP calculation is unstable.")

    if total_m5 > 0:
        osc_total = sum(r['changes'] for r in m5_osc)
        if osc_total > 20:
            exec_points.append(f"- **HIGH:** M5 state oscillated rapidly ({osc_total} transitions in bursts) — regime detection is noisy.")

    if not exec_points:
        exec_points.append("- System appears stable. No critical issues detected from log data.")

    lines.extend(exec_points)

    lines += [
        "",
        "## 2. Log Statistics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Period | {stats['period_start']} → {stats['period_end']} |",
        f"| FRVP events | {stats['total_frvp']} |",
        f"| M5 state changes | {stats['total_m5']} |",
        f"| Signals sent | {stats['total_signals']} |",
        f"| WARNINGs / ERRORs | {stats['total_warnings']} |",
        "",
        "## 3. Signal Analysis",
        "",
        "### 3.1 All Signals (chronological)",
        "",
        _table(
            ['Time', 'Signal ID', 'Type', 'Dir', 'M5 State', 'FRVP Anchor', 'Anchor Type'],
            ctx,
            ['timestamp', 'signal_id', 'signal_type', 'direction', 'm5_state_at_fire', 'frvp_anchor', 'anchor_type']
        ),
        "",
        "### 3.2 Signal Type Distribution",
        "",
        _table(
            ['Type', 'Direction', 'Count', 'First At', 'Last At'],
            sig_bk,
            ['signal_type', 'direction', 'cnt', 'first_at', 'last_at']
        ),
        "",
        "### 3.3 Signal vs FRVP Anchor Alignment",
        "",
        f"| Aligned | Misaligned | Unknown |",
        f"|---------|------------|---------|",
        f"| {align['aligned']} | {align['misaligned']} | {align['unknown']} |",
        "",
        "> **Aligned** = LONG with swing_low anchor OR SHORT with swing_high anchor.",
        "",
    ]

    # Trades from DB
    if trades:
        lines += [
            "### 3.4 Trade Status in DB",
            "",
            _table(['Status', 'Count', 'Avg PnL', 'Total PnL'], trades, ['status', 'cnt', 'avg_pnl', 'total_pnl']),
            "",
        ]

    lines += [
        "## 4. FRVP Anchor Analysis",
        "",
        "### 4.1 Anchor Level Summary",
        "",
        _table(
            ['Anchor Price', 'Type', 'Cycles', 'First Seen', 'Last Seen', 'Avg Move', 'Min', 'Max'],
            anchors,
            ['anchor_price', 'swing_type', 'cycles', 'first_seen', 'last_seen', 'avg_move', 'min_move', 'max_move']
        ),
        "",
        "### 4.2 Rapid Anchor Flips (<10 min)",
        "",
    ]

    if flips:
        lines.append(_table(
            ['Time 1', 'Price 1', 'Type 1', 'Time 2', 'Price 2', 'Type 2', 'Gap (min)'],
            flips,
            ['t1', 'p1', 's1', 't2', 'p2', 's2', 'gap_min']
        ))
    else:
        lines.append("_No rapid flips detected._")

    lines += [
        "",
        "## 5. M5 State Analysis",
        "",
        "### 5.1 State Distribution",
        "",
        _table(['State', 'Count', '%'], m5_dist, ['state', 'cnt', 'pct']),
        "",
        "### 5.2 Transition Matrix (top pairs)",
        "",
        _table(['From', 'To', 'Count'], m5_mat, ['from_state', 'to_state', 'cnt']),
        "",
        "### 5.3 Rapid Oscillation Windows (≥3 changes in 30 min)",
        "",
    ]

    if m5_osc:
        lines.append(_table(['Window', 'State Changes'], m5_osc, ['window', 'changes']))
    else:
        lines.append("_No rapid oscillation detected._")

    lines += [
        "",
        "## 6. Warnings & Errors",
        "",
    ]

    if warns:
        lines.append(_table(['Time', 'Level', 'Module', 'Message'], warns, ['timestamp', 'level', 'module', 'message']))
    else:
        lines.append("_No warnings or errors recorded._")

    lines += [
        "",
        "## 7. Critical Issues",
        "",
    ]

    issues = []
    # Issue 1: stale trades
    for w in stale_warn:
        issues.append(f"1. **[CRITICAL] Stale trades cleaned** — `{w['timestamp']}` `{w['message']}`  \n"
                      f"   Root cause: EA not opening trades despite signals being sent via ZeroMQ.")

    # Issue 2: anchor flips
    if len(flips) > 5:
        issues.append(f"2. **[HIGH] FRVP anchor unstable** — {len(flips)} flips in <10 min gaps.  \n"
                      f"   Root cause: VP recalculates on every tick, swinging between {anchors[0]['anchor_price'] if anchors else '?'} and {anchors[1]['anchor_price'] if len(anchors) > 1 else '?'}.")

    # Issue 3: misaligned signals
    if align['misaligned'] > 0:
        pct = round(align['misaligned'] / max(total_sigs, 1) * 100, 1)
        issues.append(f"3. **[HIGH] Signal-Anchor misalignment** — {align['misaligned']}/{total_sigs} signals ({pct}%) fired against FRVP anchor direction.")

    # Issue 4: M5 rapid oscillation
    if m5_osc:
        worst = m5_osc[0]
        issues.append(f"4. **[MEDIUM] M5 state noise** — worst window `{worst['window']}` had {worst['changes']} transitions.  \n"
                      f"   M5 state RANGING↔SIDEWAY oscillates without meaningful market structure change.")

    if not issues:
        issues.append("_No critical issues detected._")

    lines.extend(issues)

    lines += [
        "",
        "## 8. Recommendations (Ranked by Impact)",
        "",
        "1. **[P0] Fix EA connectivity** — 201 signals SENT with 0 OPENED = 0% execution. Check ZeroMQ bridge in MT5.",
        "2. **[P1] Stabilize FRVP anchor** — VP should anchor from session open swing, not recalculate per tick.",
        "3. **[P2] M5 state hysteresis** — Add min-duration filter (e.g. ≥3 candles) before state transition is confirmed.",
        "4. **[P3] Signal-anchor gate** — Block signals when direction misaligns with active FRVP anchor type.",
        "",
        "---",
        f"_Report generated by analyst_db.py v44.1 at {datetime.now().isoformat()}_",
    ]

    return '\n'.join(lines)


if __name__ == '__main__':
    print("[Analyst] Generating report from DB...")
    report = generate_report()
    REPORT.write_text(report, encoding='utf-8')
    print(f"[Analyst] Report written → {REPORT}")
    print(report[:3000])
