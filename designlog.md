# Separated Raw Data Specification with Real Samples

## [PART 1] UI: AI Intelligence & Logic Panel
**Source File:** `ai_analysis_log.jsonl`
(AI's Reasoning Logic)

---

## [PART 2] UI: Performance & Market Realization Dashboard
**Source File:** `ai_market_results.jsonl`
(Comparison with Market Outcome)

---

## [PART 3] Data Relationship (The Linking Logic)
- `ai_analysis_log.timestamp` <---> `ai_market_results.analysis_time`
- `ai_analysis_log.timestamp` <---> `ai_trade_log.timestamp`

---

## [PART 4] UI: Trade Execution History & Signal Audit
**Source File:** `ai_trade_log.jsonl`
**Role:** Displays the real-time execution of trading signals (Opened, Skipped, Win/Loss).

### 4.1 Technical Schema
- `timestamp`: (ISO-8601) Exact time of signal generation.
- `signal_id`: (String) Unique ID for the trade (e.g., `IOFF_MOMENTUM_SHORT_124314`).
- `direction`: (String) `SHORT` | `LONG`.
- `signal_type`: (String) `MOMENTUM`, `REVERSAL`, `MEAN_REVERSE`, `ABSORP`.
- `score`: (Int) System confidence score (x/20).
- `entry_price` / `stop_loss` / `take_profit`: (Float) Strategic price levels.
- `status`: (String) `WIN`, `LOSS`, `EA_SKIPPED`, `OPEN`.
- `ea_opened`: (Boolean) Whether the MT5 EA actually opened the trade.
- `pnl`: (Float) Profit or Loss value (null if not closed).
- `skip_reason`: (String) Why the trade wasn't taken (e.g., `No EA confirmation`).

### 4.2 Real Data Samples
- **SKIPPED (No Confirmation):**
```json
{
  "timestamp": "2026-03-27T12:43:14.653913+00:00",
  "signal_id": "IOFF_MOMENTUM_SHORT_124314",
  "direction": "SHORT",
  "signal_type": "MOMENTUM",
  "score": 7,
  "entry_price": 66563.9,
  "stop_loss": 66848.1,
  "take_profit": 66052.35,
  "status": "EA_SKIPPED",
  "skip_reason": "No EA confirmation within 5min"
}
```
- **TRADE OPENED (WIN/LOSS):**
```json
{
  "timestamp": "2026-03-27T01:05:52.000000+00:00",
  "signal_id": "IPAF_SHORT_000547",
  "direction": "SHORT",
  "status": "WIN",
  "pnl": 0.52,
  "ea_opened": true
}
```

---
*Analyzed by Arch (อาช) - Execution Systems Architect*
