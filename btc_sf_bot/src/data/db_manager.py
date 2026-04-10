"""
TradeDB - SQLite Database Manager for BTC SMC AI Bot
v36.0: Migrated from JSONL to SQLite for better performance and querying
"""
import sqlite3
import json
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, Dict, Any, List

# Schema SQL
SCHEMA_SQL = """
-- v50.4: Trades table (Execution Ledger — can be cleared)
-- Analytical data lives in signal_telemetry, linked by signal_id
CREATE TABLE IF NOT EXISTS trades (
    signal_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    signal_type TEXT,
    direction TEXT,
    mode TEXT,
    score INTEGER,

    -- Prices
    entry_price REAL,
    actual_entry_price REAL,
    stop_loss REAL,
    take_profit REAL,
    actual_rr REAL,

    -- Execution
    status TEXT DEFAULT 'SENT',
    ea_opened INTEGER DEFAULT 0,
    opened_at TEXT,
    closed_at TEXT,

    -- Result
    pnl REAL,
    exit_reason TEXT,
    mfe REAL,
    mae REAL,
    price_at_close REAL,
    duration_seconds INTEGER,
    skip_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_signal_type ON trades(signal_type);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);

-- Gate blocks table
CREATE TABLE IF NOT EXISTS gate_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    signal_id TEXT,
    mode TEXT,
    direction TEXT,
    signal_type TEXT,
    score INTEGER,
    gate_reason TEXT NOT NULL,
    regime TEXT,
    h1_dist REAL,
    wall_info TEXT,
    wall_stability_seconds REAL DEFAULT 0,
    delta REAL,
    der_persistence INTEGER,
    m5_state TEXT,
    price REAL,
    breakdown TEXT
);

CREATE INDEX IF NOT EXISTS idx_blocks_gate ON gate_blocks(gate_reason);
CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON gate_blocks(timestamp);

-- v50.4 Institutional Telemetry Table (Analysis Warehouse — never delete)
CREATE TABLE IF NOT EXISTS signal_telemetry (
    signal_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    signal_type TEXT,
    direction TEXT,
    mode TEXT,
    gate_status TEXT,       -- PASSED | BLOCKED
    block_reason TEXT,

    -- Context at Entry
    regime TEXT,
    regime_confidence TEXT,
    m5_state TEXT,
    m5_bias TEXT,
    m5_bias_level TEXT,
    m5_dist_pct REAL,          -- v53.1: distance from M5 EMA20 as %
    m5_swing_structure TEXT,   -- v51.1: 9 patterns
    m5_swing_ema_overextended INTEGER, -- v51.2 MOD-40: 0/1 bool
    m5_swing_reversal_hint INTEGER,   -- v51.2 MOD-40: 0/1 bool
    h1_swing_structure TEXT,   -- v51.1: 9 patterns
    h1_swing_ema_overextended INTEGER, -- v51.2 MOD-40: 0/1 bool
    h1_swing_reversal_hint INTEGER,   -- v51.2 MOD-40: 0/1 bool
    m5_efficiency REAL,
    m5_ema_position TEXT,
    session TEXT,
    atr_m5 REAL,
    sl_reason TEXT,

    -- H1 Bias
    h1_bias_level TEXT,
    h1_dist_pct REAL,
    ema_trend TEXT,
    h1_ema9 REAL,
    h1_ema20 REAL,
    h1_ema50 REAL,
    h1_layers TEXT,         -- JSON: {"l0","l1","l2","l3","lc","lr"}
    pullback TEXT,

    -- Price Context (NOT execution prices — analytical snapshot only)
    price_at_signal REAL,      -- market price when signal generated

    -- H1 Structure
    h1_bias TEXT,              -- BULLISH | BEARISH | NEUTRAL (direction)
    h1_last_high REAL,         -- last completed H1 candle high
    h1_last_low REAL,          -- last completed H1 candle low
    frvp_anchor_type TEXT,     -- BULLISH | BEARISH (FRVP anchor)

    -- Order Flow
    der REAL,
    der_direction TEXT,
    der_persistence INTEGER,
    der_sustainability TEXT,
    delta REAL,
    wall_info TEXT,
    wall_stability_seconds REAL,
    oi REAL,
    funding REAL,

    -- Volume Profile
    vp_poc REAL,
    vp_vah REAL,
    vp_val REAL,
    vp_price_vs_va TEXT,

    -- AI Analysis
    ai_bias TEXT,
    ai_confidence INTEGER,
    ai_action TEXT,
    ai_reason TEXT,
    ai_aligned INTEGER,

    -- Score Breakdown
    breakdown TEXT,

    -- 1. CORE SCORES (Weights)
    score_total INTEGER,
    score_order_flow_force INTEGER,
    score_wall_resistance INTEGER,
    score_volume_surge INTEGER,
    score_price_efficiency INTEGER,
    score_momentum_persistence INTEGER,
    score_open_interest_alignment INTEGER,
    score_structural_continuity INTEGER,
    score_wick_rejection INTEGER,
    score_h1_structure_strength INTEGER,
    score_m5_entry_quality INTEGER,
    score_eqs_total INTEGER,
    score_hvn_proximity INTEGER,
    score_volume_decline INTEGER,
    score_false_breakout_risk INTEGER,
    score_poc_shift_strength INTEGER,
    score_lvn_speed_ahead INTEGER,
    score_breakout_volume INTEGER,
    score_delta_divergence INTEGER,
    score_exhaustion_evidence INTEGER,

    -- 2. RAW MARKET DATA
    raw_order_flow_force REAL,
    raw_delta_value REAL,
    raw_wall_ratio REAL,
    raw_volume_ratio REAL,
    raw_m5_efficiency REAL,
    raw_oi_change_pct REAL,
    raw_h1_distance_pct REAL,
    raw_atr_ratio REAL,
    raw_hvn_distance_atr REAL,
    raw_poc_shift_distance REAL,
    raw_retrace_pct REAL,
    raw_imbalance_avg_5m REAL,
    raw_order_block_body_pct REAL
    -- NO close context (moved to trade_outcomes)
    -- NO entry/SL/TP (execution data stays in trades)
    -- NO FK: trades can be cleared independently
);

CREATE INDEX IF NOT EXISTS idx_telemetry_type ON signal_telemetry(signal_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_status ON signal_telemetry(gate_status);

-- v50.5: Trade Outcomes (Permanent Results — never delete)
CREATE TABLE IF NOT EXISTS trade_outcomes (
    signal_id TEXT PRIMARY KEY,
    timestamp_closed TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    mode TEXT,

    -- Outcome
    result TEXT NOT NULL,           -- WIN | LOSS | BE
    pnl REAL NOT NULL,
    exit_reason TEXT,

    -- Execution Quality
    mfe REAL,
    mae REAL,
    duration_seconds INTEGER,
    price_at_close REAL,
    actual_rr REAL,

    -- Close Context Snapshot
    regime_at_close TEXT,
    m5_state_at_close TEXT,
    wall_at_close TEXT,
    delta_at_close REAL,
    der_at_close REAL,
    h1_bias_at_close TEXT,
    h1_dist_at_close REAL
);

CREATE INDEX IF NOT EXISTS idx_outcomes_result ON trade_outcomes(result);
CREATE INDEX IF NOT EXISTS idx_outcomes_type ON trade_outcomes(signal_type);
CREATE INDEX IF NOT EXISTS idx_outcomes_timestamp ON trade_outcomes(timestamp_closed);

-- Regime snapshots table
CREATE TABLE IF NOT EXISTS regime_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    price REAL,
    regime TEXT,
    regime_confidence TEXT,
    adx REAL,
    bb_width REAL,
    m5_state TEXT,
    m5_bias TEXT,              -- v43.1: M5 bias (BULLISH/BEARISH/NEUTRAL)
    h1_bias TEXT,
    h1_dist_pct REAL,
    wall_info TEXT,
    delta REAL,
    der REAL,
    signals_sent INTEGER DEFAULT 0,
    -- v37.6: M5 state debug data
    er_long REAL DEFAULT 0,
    er_short REAL DEFAULT 0,
    vol_rising INTEGER DEFAULT 0,
    ema_slope REAL DEFAULT 0,
    net_long REAL DEFAULT 0,
    net_short REAL DEFAULT 0,
    atr_est REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_snapshots_regime ON regime_snapshots(regime);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON regime_snapshots(timestamp);

-- AI analysis table
CREATE TABLE IF NOT EXISTS ai_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bias TEXT,
    confidence INTEGER,
    action TEXT,
    reason TEXT,
    key_level REAL,
    price REAL,
    regime TEXT,
    m5_state TEXT,
    h1_dist REAL,
    der REAL,
    wall TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_timestamp ON ai_analysis(timestamp);
CREATE INDEX IF NOT EXISTS idx_ai_bias ON ai_analysis(bias);

-- AI skipped table
CREATE TABLE IF NOT EXISTS ai_skipped (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    signal_id TEXT,
    mode TEXT,
    direction TEXT,
    score INTEGER,
    gate_blocked TEXT,
    ai_bias TEXT,
    ai_confidence INTEGER,
    ai_action TEXT,
    ai_reason TEXT,
    ai_aligned INTEGER
);

CREATE INDEX IF NOT EXISTS idx_skipped_gate ON ai_skipped(gate_reason);

-- AI market results table (v36.2)
CREATE TABLE IF NOT EXISTS ai_market_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bias TEXT,
    confidence INTEGER,
    action TEXT,
    price REAL,
    price_future REAL,
    correct INTEGER,
    error_pct REAL
);

CREATE INDEX IF NOT EXISTS idx_market_results_timestamp ON ai_market_results(timestamp);

-- AI accuracy log table (v36.2)
CREATE TABLE IF NOT EXISTS ai_accuracy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bias TEXT,
    confidence INTEGER,
    action TEXT,
    price REAL,
    direction TEXT,
    actual_outcome TEXT,
    correct INTEGER,
    pnl_1h REAL,
    pnl_4h REAL
);

CREATE INDEX IF NOT EXISTS idx_accuracy_timestamp ON ai_accuracy_log(timestamp);

-- Bot state table (v36.2)
CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- FRVP anchor events from bot.log (v44.1)
CREATE TABLE IF NOT EXISTS frvp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    anchor_price REAL NOT NULL,
    swing_type TEXT NOT NULL,
    move_size REAL NOT NULL,
    log_line INTEGER
);
CREATE INDEX IF NOT EXISTS idx_frvp_timestamp ON frvp_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_frvp_anchor ON frvp_events(anchor_price);

-- M5 state transitions from bot.log (v44.1)
CREATE TABLE IF NOT EXISTS m5_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    log_line INTEGER
);
CREATE INDEX IF NOT EXISTS idx_m5_timestamp ON m5_transitions(timestamp);

-- Signals sent via ZeroMQ from bot.log (v44.1)
CREATE TABLE IF NOT EXISTS signals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    signal_id TEXT UNIQUE NOT NULL,
    signal_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    log_line INTEGER
);
CREATE INDEX IF NOT EXISTS idx_siglog_timestamp ON signals_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_siglog_type ON signals_log(signal_type);

-- Bot warnings/errors from bot.log (v44.1)
CREATE TABLE IF NOT EXISTS bot_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    module TEXT NOT NULL,
    message TEXT NOT NULL,
    log_line INTEGER
);
CREATE INDEX IF NOT EXISTS idx_warn_timestamp ON bot_warnings(timestamp);
"""


class TradeDB:
    """SQLite Database Manager for BTC SMC AI Bot"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent.parent / 'data' / 'trades.db')
        self.db_path = db_path
        # Ensure directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def _conn(self):
        """Context manager for database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")      # concurrent read
        conn.execute("PRAGMA synchronous=NORMAL")  # faster write
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database with schema + migration for new columns"""
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
        
        # v50.5: Migration — gate_blocks extras
        for col, typ in [('wall_stability_seconds', 'REAL DEFAULT 0'), ('breakdown', 'TEXT'),
                         ('m5_bias', 'TEXT'), ('m5_bias_level', 'TEXT')]:
            try:
                with self._conn() as conn:
                    conn.execute(f"ALTER TABLE gate_blocks ADD COLUMN {col} {typ}")
            except Exception:
                pass
        # regime_snapshots extras
        for col, typ in [('m5_bias', 'TEXT'), ('vp_poc', 'REAL DEFAULT 0'), ('vp_vah', 'REAL DEFAULT 0'),
                         ('vp_val', 'REAL DEFAULT 0'), ('vp_price_vs_va', "TEXT DEFAULT 'INSIDE'")]:
            try:
                with self._conn() as conn:
                    conn.execute(f"ALTER TABLE regime_snapshots ADD COLUMN {col} {typ}")
            except Exception:
                pass
        # bot_state
        try:
            with self._conn() as conn:
                conn.execute("INSERT OR IGNORE INTO bot_state (key, value, timestamp) VALUES ('plan_version', '50.4', datetime('now'))")
        except Exception:
            pass

        # v50.4: Rebuild trades table if old schema detected (has > 25 columns)
        try:
            with self._conn() as conn:
                cols = conn.execute("PRAGMA table_info(trades)").fetchall()
                if len(cols) > 25:
                    # Old fat schema — migrate to slim
                    conn.execute("""CREATE TABLE IF NOT EXISTS trades_new (
                        signal_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                        signal_type TEXT, direction TEXT, mode TEXT, score INTEGER,
                        entry_price REAL, actual_entry_price REAL, stop_loss REAL,
                        take_profit REAL, actual_rr REAL,
                        status TEXT DEFAULT 'SENT', ea_opened INTEGER DEFAULT 0,
                        opened_at TEXT, closed_at TEXT,
                        pnl REAL, exit_reason TEXT, mfe REAL, mae REAL,
                        price_at_close REAL, duration_seconds INTEGER, skip_reason TEXT
                    )""")
                    conn.execute("""INSERT OR IGNORE INTO trades_new
                        (signal_id, timestamp, signal_type, direction, mode, score,
                         entry_price, actual_entry_price, stop_loss, take_profit, actual_rr,
                         status, ea_opened, opened_at, closed_at,
                         pnl, exit_reason, mfe, mae, price_at_close, duration_seconds, skip_reason)
                        SELECT signal_id, timestamp, signal_type, direction, mode, score,
                         entry_price, actual_entry_price, stop_loss, take_profit, actual_rr,
                         status, ea_opened, opened_at, closed_at,
                         pnl, exit_reason, mfe, mae, price_at_close, duration_seconds, skip_reason
                        FROM trades""")
                    conn.execute("DROP TABLE trades")
                    conn.execute("ALTER TABLE trades_new RENAME TO trades")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_signal_type ON trades(signal_type)")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")
                    print(f"[DB] trades migrated: {len(cols)} cols → 22 cols (slim)")
        except Exception as e:
            print(f"[DB] trades migration skipped: {e}")

        # v50.4: Institutional Telemetry migration (idempotent)
        try:
            with self._conn() as conn:
                conn.executescript(SCHEMA_SQL)
        except Exception:
            pass
        # v50.4: Add new telemetry columns for existing DBs
        _new_telemetry_cols = [
            ('mode', 'TEXT'), ('regime_confidence', 'TEXT'), ('m5_efficiency', 'REAL'),
            ('m5_ema_position', 'TEXT'), ('atr_m5', 'REAL'), ('sl_reason', 'TEXT'),
            ('ema_trend', 'TEXT'), ('h1_ema9', 'REAL'), ('h1_ema20', 'REAL'), ('h1_ema50', 'REAL'),
            ('h1_layers', 'TEXT'), ('pullback', 'TEXT'),
            ('der_direction', 'TEXT'), ('der_persistence', 'INTEGER'), ('der_sustainability', 'TEXT'),
            ('wall_stability_seconds', 'REAL'), ('oi', 'REAL'), ('funding', 'REAL'),
            ('vp_poc', 'REAL'), ('vp_vah', 'REAL'), ('vp_val', 'REAL'), ('vp_price_vs_va', 'TEXT'),
            ('ai_bias', 'TEXT'), ('ai_confidence', 'INTEGER'), ('ai_action', 'TEXT'),
            ('ai_reason', 'TEXT'), ('ai_aligned', 'INTEGER'),
            ('breakdown', 'TEXT'), ('der', 'REAL'), ('delta', 'REAL'), ('wall_info', 'TEXT'),
            ('h1_dist_pct', 'REAL'),
            ('regime_at_close', 'TEXT'), ('m5_state_at_close', 'TEXT'), ('wall_at_close', 'TEXT'),
            ('delta_at_close', 'REAL'), ('der_at_close', 'REAL'),
            ('h1_bias_at_close', 'TEXT'), ('h1_dist_at_close', 'REAL'), ('price_at_close', 'REAL'),
            # v50.3: m5_bias columns
            ('m5_bias', 'TEXT'), ('m5_bias_level', 'TEXT'),
            # v50.5 MOD-35: swing structure
            ('m5_swing_structure', 'TEXT'),
            ('h1_swing_structure', 'TEXT'),
            # v51.2 MOD-40: EMA reversal hints
            ('m5_swing_ema_overextended', 'INTEGER'),
            ('m5_swing_reversal_hint', 'INTEGER'),
            ('h1_swing_ema_overextended', 'INTEGER'),
            ('h1_swing_reversal_hint', 'INTEGER'),
            # v50.5: price context + h1 structure (no execution prices)
            ('price_at_signal', 'REAL'),
            ('h1_bias', 'TEXT'), ('h1_last_high', 'REAL'), ('h1_last_low', 'REAL'), ('frvp_anchor_type', 'TEXT'),
            ('m5_dist_pct', 'REAL'),
        ]
        for col, typ in _new_telemetry_cols:
            try:
                with self._conn() as conn:
                    conn.execute(f"ALTER TABLE signal_telemetry ADD COLUMN {col} {typ}")
            except Exception:
                pass

        # v50.5: Backfill trade_outcomes from old trades data (one-time migration)
        try:
            with self._conn() as conn:
                existing = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
                if existing == 0:
                    # Check if old trades have closed results to migrate
                    old_closed = conn.execute(
                        "SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL"
                    ).fetchone()[0]
                    if old_closed > 0:
                        conn.execute("""
                            INSERT OR IGNORE INTO trade_outcomes
                            (signal_id, timestamp_closed, signal_type, direction, mode,
                             result, pnl, exit_reason, mfe, mae, duration_seconds,
                             price_at_close, actual_rr)
                            SELECT signal_id,
                                   COALESCE(closed_at, timestamp),
                                   signal_type, direction, mode,
                                   CASE WHEN pnl > 0 THEN 'WIN' WHEN pnl = 0 THEN 'BE' ELSE 'LOSS' END,
                                   pnl, exit_reason, mfe, mae, duration_seconds,
                                   price_at_close, actual_rr
                            FROM trades WHERE pnl IS NOT NULL
                        """)
                        migrated = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
                        print(f"[DB] Backfilled {migrated} trade outcomes from old trades")
        except Exception as e:
            print(f"[DB] Backfill migration skipped: {e}")

        # v44.1: Migration — create log-derived tables if not exists (idempotent via SCHEMA_SQL)
        # Tables created via SCHEMA_SQL CREATE TABLE IF NOT EXISTS above
        # v50.2 Institutional Normalization: Columns that belong in 'signal_telemetry', not in 'trades'.
    # We keep signal_type, direction, and score in 'trades' for basic identity.
    # v50.4: ALL analytical columns — stripped from trades, stored in signal_telemetry only
    LOGIC_COLUMNS = [
        # Context
        'regime', 'regime_confidence', 'm5_state', 'm5_bias', 'm5_bias_level',
        'm5_efficiency', 'm5_ema_position', 'session', 'atr_m5', 'sl_reason',
        # H1
        'h1_bias_level', 'h1_dist_pct', 'ema_trend',
        'h1_ema9', 'h1_ema20', 'h1_ema50', 'l0', 'l1', 'l2', 'l3', 'lc', 'lr', 'pullback',
        # Order Flow
        'der', 'der_direction', 'der_persistence', 'der_sustainability',
        'delta', 'wall_info', 'wall_stability_seconds', 'oi', 'funding',
        'poc', 'vah', 'val', 'vp_poc', 'vp_price_vs_va',
        # AI
        'ai_bias', 'ai_confidence', 'ai_action', 'ai_reason', 'ai_aligned', 'ai_neutral_low_conf',
        # Other
        'breakdown',
    ]

    # ==================== Trades ====================
    
    def insert_trade(self, data: dict):
        """v51.2: Insert trade record only — telemetry is written separately by main.py"""
        # Ensure timestamp
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()

        # v51.2: Telemetry NOT called here — main.py calls insert_signal_telemetry() directly
        # with the FULL payload (not the slim trade_data subset)

        # Institutional Normalization
        processed_data = data.copy()
        
        for col in self.LOGIC_COLUMNS:
            if col in processed_data:
                del processed_data[col]

        # Convert remaining special types
        if 'ai_aligned' in processed_data:
            v = processed_data['ai_aligned']
            processed_data['ai_aligned'] = 1 if v is True else 0 if v is False else None

        cols = ', '.join(processed_data.keys())
        placeholders = ', '.join(['?' for _ in processed_data])
        try:
            with self._conn() as conn:
                conn.execute(f'INSERT OR REPLACE INTO trades ({cols}) VALUES ({placeholders})',
                            list(processed_data.values()))
        except sqlite3.IntegrityError:
            # Update instead if insert fails
            self.update_trade(data.get('signal_id', ''), processed_data)
    
    def update_trade(self, signal_id: str, updates: dict):
        """Update a trade record by signal_id with normalization (v50.5)"""
        # v50.5: If trade is closing (has pnl), persist to trade_outcomes
        if 'pnl' in updates and updates.get('pnl') is not None:
            self._insert_trade_outcome(signal_id, updates)

        processed_updates = updates.copy()

        # Apply Normalization filtering
        for col in self.LOGIC_COLUMNS:
            if col in processed_updates:
                del processed_updates[col]
        # Filter close context (lives in trade_outcomes now)
        for col in ('regime_at_close', 'm5_state_at_close', 'wall_at_close',
                     'delta_at_close', 'der_at_close', 'h1_bias_at_close',
                     'h1_dist_at_close'):
            processed_updates.pop(col, None)

        if not processed_updates:
            return

        # Convert breakdown dict to JSON string (if any survived)
        if 'breakdown' in processed_updates and isinstance(processed_updates['breakdown'], dict):
            processed_updates['breakdown'] = json.dumps(processed_updates['breakdown'])

        # Convert ai_aligned bool to int
        if 'ai_aligned' in processed_updates:
            v = processed_updates['ai_aligned']
            processed_updates['ai_aligned'] = 1 if v is True else 0 if v is False else None

        sets = ', '.join([f'{k}=?' for k in processed_updates.keys()])
        try:
            with self._conn() as conn:
                conn.execute(f'UPDATE trades SET {sets} WHERE signal_id=?',
                            list(processed_updates.values()) + [signal_id])
        except Exception as e:
            print(f"[DB] Update trade error: {e}")
    
    def _insert_trade_outcome(self, signal_id: str, data: dict):
        """v50.5: Persist trade result to permanent trade_outcomes table."""
        try:
            pnl = data.get('pnl', 0) or 0
            result = 'WIN' if pnl > 0 else 'BE' if pnl == 0 else 'LOSS'
            # Get identity from existing trade if not in updates
            trade = self.get_trade(signal_id) or {}
            outcome = {
                'signal_id': signal_id,
                'timestamp_closed': data.get('closed_at') or datetime.now(timezone.utc).isoformat(),
                'signal_type': data.get('signal_type') or trade.get('signal_type', ''),
                'direction': data.get('direction') or trade.get('direction', ''),
                'mode': data.get('mode') or trade.get('mode', ''),
                'result': result,
                'pnl': pnl,
                'exit_reason': data.get('exit_reason'),
                'mfe': data.get('mfe'),
                'mae': data.get('mae'),
                'duration_seconds': data.get('duration_seconds'),
                'price_at_close': data.get('price_at_close'),
                'actual_rr': data.get('actual_rr') or trade.get('actual_rr'),
                'regime_at_close': data.get('regime_at_close'),
                'm5_state_at_close': data.get('m5_state_at_close'),
                'wall_at_close': data.get('wall_at_close'),
                'delta_at_close': data.get('delta_at_close'),
                'der_at_close': data.get('der_at_close'),
                'h1_bias_at_close': data.get('h1_bias_at_close'),
                'h1_dist_at_close': data.get('h1_dist_at_close'),
            }
            outcome = {k: v for k, v in outcome.items() if v is not None}
            cols = ', '.join(outcome.keys())
            placeholders = ', '.join(['?' for _ in outcome])
            with self._conn() as conn:
                conn.execute(f'INSERT OR REPLACE INTO trade_outcomes ({cols}) VALUES ({placeholders})',
                            list(outcome.values()))
        except Exception as e:
            print(f"[DB] Insert trade_outcome error: {e}")

    def clear_trades(self):
        """v50.5: Clear trades table — telemetry + outcomes remain intact."""
        with self._conn() as conn:
            conn.execute('DELETE FROM trades')
        # Also clear gate state file (zone memory)
        if GATE_STATE_FILE.exists():
            GATE_STATE_FILE.unlink()
        print("[DB] Trades cleared + gate state reset — telemetry + outcomes preserved")

    def get_trade(self, signal_id: str) -> Optional[Dict]:
        """Get a trade by signal_id"""
        with self._conn() as conn:
            row = conn.execute('SELECT * FROM trades WHERE signal_id=?', (signal_id,)).fetchone()
            return dict(row) if row else None
    
    def get_trades(self, status: str = None, limit: int = 100) -> List[Dict]:
        """Get trades with optional status filter"""
        with self._conn() as conn:
            if status:
                rows = conn.execute('SELECT * FROM trades WHERE status=? ORDER BY timestamp DESC LIMIT ?',
                                   (status, limit)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?',
                                   (limit,)).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== Gate Blocks ====================
    
    def insert_gate_block(self, data: dict):
        """v51.2: Insert gate block only — telemetry is written separately by main.py"""
        # Ensure timestamp
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()

        # v51.2: Telemetry NOT called here — main.py calls insert_signal_telemetry() directly

        # Convert breakdown dict to JSON string for legacy storage
        processed_data = data.copy()
        if 'breakdown' in processed_data and isinstance(processed_data['breakdown'], dict):
            processed_data['breakdown'] = json.dumps(processed_data['breakdown'])
        
        cols = ', '.join(processed_data.keys())
        placeholders = ', '.join(['?' for _ in processed_data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO gate_blocks ({cols}) VALUES ({placeholders})',
                        list(processed_data.values()))

    # ==================== Institutional Telemetry (v50.2) ====================

    def insert_signal_telemetry(self, signal_id: str, payload: dict):
        """
        Universal Institutional Interpreter.
        Maps raw breakdown data to descriptive database columns.
        """
        try:
            breakdown = payload.get('breakdown', {})
            if not isinstance(breakdown, dict):
                breakdown = {}

            # v50.4: Universal Mapping — all analytical data lives here
            telemetry = {
                'signal_id': signal_id,
                'timestamp': payload.get('timestamp') or datetime.now(timezone.utc).isoformat(),
                'signal_type': payload.get('signal_type'),
                'direction': payload.get('direction'),
                'mode': payload.get('mode'),
                'gate_status': payload.get('gate_status', 'UNKNOWN'),
                'block_reason': payload.get('block_reason'),

                # Price Context (analytical only — NOT execution prices)
                'price_at_signal': payload.get('current_price'),

                # Context at Entry
                'regime': payload.get('regime'),
                'regime_confidence': payload.get('regime_confidence'),
                'm5_state': payload.get('m5_state'),
                'm5_bias': payload.get('m5_bias'),
                'm5_bias_level': payload.get('m5_bias_level'),
                'm5_dist_pct': payload.get('m5_dist_pct'),
                'm5_swing_structure': payload.get('m5_swing_structure'),
                'm5_swing_ema_overextended': 1 if payload.get('m5_swing_ema_overextended') else 0,
                'm5_swing_reversal_hint': 1 if payload.get('m5_swing_reversal_hint') else 0,
                'h1_swing_structure': payload.get('h1_swing_structure'),
                'h1_swing_ema_overextended': 1 if payload.get('h1_swing_ema_overextended') else 0,
                'h1_swing_reversal_hint': 1 if payload.get('h1_swing_reversal_hint') else 0,
                'm5_efficiency': payload.get('m5_efficiency'),
                'm5_ema_position': payload.get('m5_ema_position'),
                'session': payload.get('session'),
                'atr_m5': payload.get('atr_m5'),
                'sl_reason': payload.get('sl_reason'),

                # H1 Bias + Structure
                'h1_bias': payload.get('h1_bias'),
                'h1_bias_level': payload.get('h1_bias_level'),
                'h1_dist_pct': payload.get('h1_dist_pct'),
                'ema_trend': payload.get('ema_trend'),
                'h1_ema9': payload.get('h1_ema9'),
                'h1_ema20': payload.get('h1_ema20'),
                'h1_ema50': payload.get('h1_ema50'),
                'h1_layers': json.dumps({k: payload.get(k) for k in ('l0','l1','l2','l3','lc','lr') if payload.get(k)}) or None,
                'pullback': payload.get('pullback'),
                'h1_last_high': payload.get('h1_last_high'),
                'h1_last_low': payload.get('h1_last_low'),
                'frvp_anchor_type': payload.get('anchor_type'),

                # Order Flow
                'der': payload.get('der'),
                'der_direction': payload.get('der_direction'),
                'der_persistence': payload.get('der_persistence'),
                'der_sustainability': payload.get('der_sustainability'),
                'delta': payload.get('delta'),
                'wall_info': payload.get('wall_info'),
                'wall_stability_seconds': payload.get('wall_stability_seconds'),
                'oi': payload.get('oi'),
                'funding': payload.get('funding'),

                # Volume Profile
                'vp_poc': payload.get('vp_poc') or payload.get('poc'),
                'vp_vah': payload.get('vp_vah') or payload.get('vah'),
                'vp_val': payload.get('vp_val') or payload.get('val'),
                'vp_price_vs_va': payload.get('vp_price_vs_va'),

                # AI Analysis
                'ai_bias': payload.get('ai_bias'),
                'ai_confidence': payload.get('ai_confidence'),
                'ai_action': payload.get('ai_action'),
                'ai_reason': payload.get('ai_reason'),
                'ai_aligned': 1 if payload.get('ai_aligned') is True else 0 if payload.get('ai_aligned') is False else None,

                # Breakdown
                'breakdown': json.dumps(breakdown) if breakdown else None,

                # 1. Scores (Weights)
                'score_total': payload.get('score'),
                'score_order_flow_force': breakdown.get('der'),
                'score_wall_resistance': breakdown.get('wall') or breakdown.get('wall_hold'),
                'score_volume_surge': breakdown.get('vol'),
                'score_price_efficiency': breakdown.get('er'),
                'score_momentum_persistence': breakdown.get('pers'),
                'score_open_interest_alignment': breakdown.get('oi'),
                'score_structural_continuity': breakdown.get('cont'),
                'score_wick_rejection': breakdown.get('rej') or breakdown.get('wick_rej') or breakdown.get('reaction') or breakdown.get('wick_rej_score'),
                'score_h1_structure_strength': breakdown.get('h1_structure') or breakdown.get('h1_score'),
                'score_m5_entry_quality': breakdown.get('m5_entry') or breakdown.get('m5_score'),
                'score_eqs_total': breakdown.get('eqs') or breakdown.get('eqs_total') or breakdown.get('eqs_score'),
                'score_hvn_proximity': breakdown.get('hvn_near') or breakdown.get('hvn_touch') or breakdown.get('dist_score'),
                'score_volume_decline': breakdown.get('vol_decline') or breakdown.get('hvn_vol') or breakdown.get('vol_score'),
                'score_false_breakout_risk': breakdown.get('false_bo_score'),
                'score_poc_shift_strength': breakdown.get('poc_shift') or breakdown.get('poc_shift_pts'),
                'score_lvn_speed_ahead': breakdown.get('lvn_ahead'),
                'score_breakout_volume': breakdown.get('breakout_vol'),
                'score_delta_divergence': breakdown.get('delta_bonus') or breakdown.get('divergence_score'),
                'score_exhaustion_evidence': breakdown.get('exh') or breakdown.get('exh_score'),

                # 2. Raw Market Metrics
                'raw_order_flow_force': payload.get('der') or breakdown.get('raw_der'),
                'raw_delta_value': payload.get('delta') or breakdown.get('raw_delta'),
                'raw_wall_ratio': payload.get('raw_wall_ratio') or breakdown.get('wall_ratio'),
                'raw_volume_ratio': payload.get('raw_volume_ratio') or payload.get('vol_ratio') or breakdown.get('eqs_volume_ratio') or breakdown.get('vol_ratio_m5'),
                'raw_m5_efficiency': payload.get('m5_efficiency') or breakdown.get('er_value'),
                'raw_oi_change_pct': payload.get('raw_oi_change_pct') or payload.get('oi_change_pct') or payload.get('oi'),
                'raw_h1_distance_pct': payload.get('h1_dist_pct'),
                'raw_atr_ratio': payload.get('raw_atr_ratio') or payload.get('atr_ratio'),
                'raw_hvn_distance_atr': breakdown.get('raw_hvn_dist') or breakdown.get('hvn_dist_atr'),
                'raw_poc_shift_distance': payload.get('poc_shift') or breakdown.get('poc_shift_pts'),
                'raw_retrace_pct': breakdown.get('eqs_retrace_pct'),
                'raw_imbalance_avg_5m': payload.get('imb_avg_5m') or breakdown.get('imb_avg_5m'),
                'raw_order_block_body_pct': breakdown.get('ob_body_pct') or breakdown.get('eqs_body_size'),
                # NO close context — moved to trade_outcomes
                # NO entry/SL/TP — execution data stays in trades
            }

            # Filter out None values AND convert dicts/lists to JSON strings
            cleaned = {}
            for k, v in telemetry.items():
                if v is None:
                    continue
                # Handle bool and numpy.bool_ types
                if isinstance(v, (bool, np.bool_)):
                    cleaned[k] = 1 if v else 0
                elif isinstance(v, (dict, list)):
                    cleaned[k] = json.dumps(v, default=str)  # default=str handles numpy types
                elif hasattr(v, 'item'):  # numpy scalar (numpy.bool_, numpy.float64, etc.)
                    cleaned[k] = v.item()
                else:
                    cleaned[k] = v
            telemetry = cleaned

            cols = ', '.join(telemetry.keys())
            placeholders = ', '.join(['?' for _ in telemetry])
            with self._conn() as conn:
                conn.execute(f'INSERT OR REPLACE INTO signal_telemetry ({cols}) VALUES ({placeholders})',
                            list(telemetry.values()))
        except Exception as e:
            print(f"[DB] Insert telemetry error: {e}")
    
    def get_gate_blocks(self, gate_reason: str = None, limit: int = 100) -> List[Dict]:
        """Get gate blocks with optional filter"""
        with self._conn() as conn:
            if gate_reason:
                rows = conn.execute('SELECT * FROM gate_blocks WHERE gate_reason=? ORDER BY timestamp DESC LIMIT ?',
                                   (gate_reason, limit)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM gate_blocks ORDER BY timestamp DESC LIMIT ?',
                                   (limit,)).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== Regime Snapshots ====================
    
    def insert_snapshot(self, data: dict):
        """Insert a regime snapshot record"""
        # Ensure timestamp
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO regime_snapshots ({cols}) VALUES ({placeholders})',
                        list(data.values()))
    
    def get_snapshots(self, regime: str = None, limit: int = 100) -> List[Dict]:
        """Get regime snapshots with optional filter"""
        with self._conn() as conn:
            if regime:
                rows = conn.execute('SELECT * FROM regime_snapshots WHERE regime=? ORDER BY timestamp DESC LIMIT ?',
                                   (regime, limit)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM regime_snapshots ORDER BY timestamp DESC LIMIT ?',
                                   (limit,)).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== AI Analysis ====================
    
    def insert_ai_analysis(self, data: dict):
        """Insert an AI analysis record"""
        # Ensure timestamp
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO ai_analysis ({cols}) VALUES ({placeholders})',
                        list(data.values()))
    
    def get_ai_analysis(self, limit: int = 100) -> List[Dict]:
        """Get recent AI analysis records"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT * FROM ai_analysis ORDER BY timestamp DESC LIMIT ?',
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== AI Skipped ====================
    
    def insert_ai_skipped(self, data: dict):
        """Insert an AI skipped record"""
        # Ensure timestamp
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO ai_skipped ({cols}) VALUES ({placeholders})',
                        list(data.values()))
    
    # ==================== AI Market Results (v36.2) ====================
    
    def insert_ai_market_result(self, data: dict):
        """Insert an AI market result for accuracy tracking"""
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO ai_market_results ({cols}) VALUES ({placeholders})',
                        list(data.values()))
    
    def get_ai_market_results(self, limit: int = 100) -> List[Dict]:
        """Get recent AI market results"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT * FROM ai_market_results ORDER BY timestamp DESC LIMIT ?',
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== AI Accuracy Log (v36.2) ====================
    
    def insert_ai_accuracy_log(self, data: dict):
        """Insert an AI accuracy log entry"""
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO ai_accuracy_log ({cols}) VALUES ({placeholders})',
                        list(data.values()))
    
    def get_ai_accuracy_logs(self, limit: int = 100) -> List[Dict]:
        """Get recent AI accuracy logs"""
        with self._conn() as conn:
            rows = conn.execute(
                'SELECT * FROM ai_accuracy_log ORDER BY timestamp DESC LIMIT ?',
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== Analytics ====================
    
    def get_win_rate_by_mode(self) -> List[Dict]:
        """Get win rate stats by mode and signal_type (from trade_outcomes)"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT mode, signal_type,
                    COUNT(*) as total,
                    SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                    ROUND(AVG(pnl), 2) as avg_pnl,
                    ROUND(SUM(pnl), 2) as total_pnl
                FROM trade_outcomes
                WHERE result IN ('WIN','LOSS')
                GROUP BY mode, signal_type
                ORDER BY total_pnl DESC
            """).fetchall()
            return [dict(r) for r in rows]
    
    def get_gate_block_stats(self) -> List[Dict]:
        """Get gate block frequency stats"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT gate_reason, COUNT(*) as cnt
                FROM gate_blocks 
                GROUP BY gate_reason 
                ORDER BY cnt DESC
            """).fetchall()
            return [dict(r) for r in rows]
    
    def get_regime_distribution(self) -> List[Dict]:
        """Get regime distribution"""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT regime, regime_confidence, COUNT(*) as cnt
                FROM regime_snapshots 
                GROUP BY regime, regime_confidence
            """).fetchall()
            return [dict(r) for r in rows]
    
    # ==================== Log-derived tables (v44.1) ====================

    def insert_frvp_event(self, data: dict):
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT OR IGNORE INTO frvp_events ({cols}) VALUES ({placeholders})',
                        list(data.values()))

    def insert_m5_transition(self, data: dict):
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO m5_transitions ({cols}) VALUES ({placeholders})',
                        list(data.values()))

    def insert_signal_log(self, data: dict):
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT OR IGNORE INTO signals_log ({cols}) VALUES ({placeholders})',
                        list(data.values()))

    def insert_bot_warning(self, data: dict):
        cols = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        with self._conn() as conn:
            conn.execute(f'INSERT INTO bot_warnings ({cols}) VALUES ({placeholders})',
                        list(data.values()))

    def get_last_imported_line(self) -> int:
        row = self.get_state('log_importer_last_line')
        return row.get('line', 0) if row else 0

    def set_last_imported_line(self, line: int):
        self.set_state('log_importer_last_line', {'line': line})

    # ==================== Bot State (v36.2) ====================
    
    def get_state(self, key: str) -> Optional[dict]:
        """Get a state value by key (as dict)"""
        with self._conn() as conn:
            row = conn.execute('SELECT value FROM bot_state WHERE key=?', (key,)).fetchone()
            if row:
                try:
                    return json.loads(row['value'])
                except json.JSONDecodeError:
                    return None
            return None
    
    def set_state(self, key: str, value: dict) -> None:
        """Set a state value (dict stored as JSON string)"""
        with self._conn() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO bot_state (key, value, timestamp) VALUES (?, ?, ?)',
                (key, json.dumps(value, default=str), datetime.now(timezone.utc).isoformat())
            )


# Singleton instance
_db_instance = None

def get_db() -> TradeDB:
    """Get singleton TradeDB instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = TradeDB()
    return _db_instance