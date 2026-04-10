"""
Log Importer v44.1
Parse bot.log → insert into SQLite DB (incremental, idempotent)

Tables populated:
  frvp_events    — FRVP major swing events
  m5_transitions — M5 state transitions
  signals_log    — signals sent via ZeroMQ
  bot_warnings   — WARNING/ERROR lines
"""
import re
from pathlib import Path
from .db_manager import get_db

LOG_PATH = Path(__file__).resolve().parent.parent.parent / 'src' / 'logs' / 'bot.log'

_RE_TS   = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
_RE_FRVP = re.compile(r'type:(major_swing_high|major_swing_low) price:(\d+(?:\.\d+)?) move:(\d+(?:\.\d+)?)')
_RE_M5   = re.compile(r'M5 State refined: (\w+)\s*\S+\s*(\w+)')
_RE_SIG  = re.compile(r'Signal (\S+) sent via ZeroMQ')
_RE_WARN = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (WARNING|ERROR)\s+\| ([^\|]+)\| (.+)')


def _ts(line: str) -> str:
    m = _RE_TS.match(line)
    return m.group(1) if m else ''


def _parse_direction(signal_id: str) -> str:
    if 'LONG' in signal_id:
        return 'LONG'
    if 'SHORT' in signal_id:
        return 'SHORT'
    return 'UNKNOWN'


def _parse_signal_type(signal_id: str) -> str:
    """Strip trailing _HHMMSS and extract type before direction token."""
    # e.g. REVERSAL_OS_LONG_002505 → REVERSAL_OS
    #      ABSORPTION_SHORT_010018 → ABSORPTION
    #      MOMENTUM_SHORT_013010   → MOMENTUM
    parts = signal_id.rsplit('_', 1)          # remove timestamp suffix
    base = parts[0] if len(parts) == 2 else signal_id
    for tok in ('_LONG', '_SHORT'):
        if tok in base:
            return base[:base.rfind(tok)]
    return base


def import_log(log_path: Path = LOG_PATH, incremental: bool = True) -> dict:
    """
    Parse log file and insert new records into DB.
    Returns counts of inserted rows per table.
    """
    db = get_db()
    start_line = db.get_last_imported_line() if incremental else 0

    counts = {'frvp': 0, 'm5': 0, 'signals': 0, 'warnings': 0, 'lines_read': 0}

    if not log_path.exists():
        print(f"[LogImporter] Log not found: {log_path}")
        return counts

    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, raw in enumerate(f, start=1):
            if line_num <= start_line:
                continue

            line = raw.rstrip()
            counts['lines_read'] += 1
            ts = _ts(line)
            if not ts:
                continue

            # FRVP event
            if '[FRVP] Major swing found' in line:
                m = _RE_FRVP.search(line)
                if m:
                    db.insert_frvp_event({
                        'timestamp'   : ts,
                        'anchor_price': float(m.group(2)),
                        'swing_type'  : m.group(1),
                        'move_size'   : float(m.group(3)),
                        'log_line'    : line_num,
                    })
                    counts['frvp'] += 1
                continue

            # M5 state transition
            if 'M5 State refined:' in line:
                m = _RE_M5.search(line)
                if m:
                    db.insert_m5_transition({
                        'timestamp' : ts,
                        'from_state': m.group(1),
                        'to_state'  : m.group(2),
                        'log_line'  : line_num,
                    })
                    counts['m5'] += 1
                continue

            # Signal sent
            if 'sent via ZeroMQ' in line:
                m = _RE_SIG.search(line)
                if m:
                    sig_id = m.group(1)
                    db.insert_signal_log({
                        'timestamp'  : ts,
                        'signal_id'  : sig_id,
                        'signal_type': _parse_signal_type(sig_id),
                        'direction'  : _parse_direction(sig_id),
                        'log_line'   : line_num,
                    })
                    counts['signals'] += 1
                continue

            # Warning / Error
            if '| WARNING' in line or '| ERROR' in line:
                m = _RE_WARN.match(line)
                if m:
                    db.insert_bot_warning({
                        'timestamp': m.group(1),
                        'level'    : m.group(2),
                        'module'   : m.group(3).strip(),
                        'message'  : m.group(4).strip(),
                        'log_line' : line_num,
                    })
                    counts['warnings'] += 1
                continue

        last_line = start_line + counts['lines_read']
        if incremental:
            db.set_last_imported_line(last_line)

    return counts


if __name__ == '__main__':
    result = import_log(incremental=False)
    print(f"[LogImporter] Done:")
    print(f"  Lines read  : {result['lines_read']}")
    print(f"  FRVP events : {result['frvp']}")
    print(f"  M5 changes  : {result['m5']}")
    print(f"  Signals     : {result['signals']}")
    print(f"  Warnings    : {result['warnings']}")
