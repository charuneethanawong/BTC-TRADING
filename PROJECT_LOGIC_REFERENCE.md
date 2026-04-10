# SMC AI Trading Bot — Complete Logic & Architecture Reference

**Project:** SMC AI Trading Bot (BTC M5 Scalping)
**Version:** v38.5
**Location:** `D:\CODING WORKS\SMC_AI_Project`
**Purpose:** Comprehensive logic reference for AI agents working on this project

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Principles](#2-architecture-principles)
3. [Three Core Engines (Single Source of Truth)](#3-three-core-engines-single-source-of-truth)
4. [Mode Timing — Smart Interval](#4-mode-timing--smart-interval)
5. [Mode 1: IPA — Institutional Price Action](#5-mode-1-ipa--institutional-price-action)
6. [Mode 2: IOF — Institutional Order Flow](#6-mode-2-iof--institutional-order-flow)
7. [Mode 3: IPAF — IPA + FRVP](#7-mode-3-ipaf--ipa--frvp)
8. [Mode 4: IOFF — IOF + FRVP](#8-mode-4-ioff--iof--frvp)
9. [Signal Gate System (17 Gates)](#9-signal-gate-system-17-gates)
10. [SL/TP Calculator](#10-sltp-calculator)
11. [Signal Builder & Contract](#11-signal-builder--contract)
12. [AI Analyzer (DeepSeek V3)](#12-ai-analyzer-deepseek-v3)
13. [Execution Pipeline](#13-execution-pipeline)
14. [Data Flow](#14-data-flow)
15. [Risk Management](#15-risk-management)
16. [Dashboard State](#16-dashboard-state)
17. [Configuration Reference](#17-configuration-reference)
18. [File Map](#18-file-map)

---

## 1. System Overview

### What This Bot Does
BTC M5 scalping bot that analyzes BTC/USDT on 5-minute and 1-hour timeframes, generates trading signals through 4 independent analysis modes, filters signals through 17 gates (v38.4+), and sends validated signals to a MetaTrader 5 Expert Advisor via ZeroMQ/Webhook.

### Tech Stack
| Component | Technology |
|---|---|
| Backend | Python 3.11+ (asyncio) |
| Data Source | Binance WebSocket + REST API |
| Execution | MetaTrader 5 EA (MQL5) via ZeroMQ/Webhook |
| AI | OpenRouter → DeepSeek V3.2 |
| Database | SQLite (signals, gate blocks, snapshots, trades) |
| Dashboard | React/TypeScript frontend via webhook server |
| Notifications | Telegram alerts |

### Key Files
```
btc_sf_bot/src/main.py          — Main bot class, run loop, orchestrates all modes
btc_sf_bot/src/analysis/        — All analyzers (IPA, IOF, FRVP, Regime, Bias, etc.)
btc_sf_bot/src/signals/         — Signal building, gating, SL/TP calculation
btc_sf_bot/src/execution/       — Signal publishing (ZeroMQ), Telegram, webhook server
btc_sf_bot/src/data/            — Binance connector, WebSocket, database, cache
btc_sf_bot/src/risk/            — Risk management, position sizing, trailing
btc_sf_bot/mt5_ea/              — MetaTrader Expert Advisor (MQL5)
frontend/                       — React dashboard
```

---

## 2. Architecture Principles

### Single Source of Truth (SSOT)
Three engines compute data **once per cycle**. All modes read from these results — never recalculate.

```
1. RegimeResult     ← market_regime.py (ADX, +DI/-DI, BB, WEAKENING)
2. H1BiasResult     ← h1_bias_engine.py (4-Layer Bias: L0-L3, EMAs, LC, LR)
3. MarketSnapshot   ← market_snapshot.py (ATR, Delta, DER, Volume, M5 State)
```

### No Duplication
- Data computed in one place only
- Terminal = view layer (no calculations)
- AI receives raw data, not pre-judged labels
- No duplicate logging (if MARKET shows it, don't log again)

### Absolute Paths
All file paths use `Path(__file__).resolve()` — never relative paths.

### Error Handling
Use `logger.warning` for production errors, not `logger.debug`.

---

## 3. Three Core Engines (Single Source of Truth)

### 3.1 MarketRegimeDetector (`market_regime.py`)

**Purpose:** Classify market state to determine which modes are suitable.

**Inputs:** M5 candles, H1 candles

**Outputs:** `RegimeResult` dataclass

**Regime Types:**
| Regime | Condition | IPA Suitable | IOF Suitable |
|---|---|---|---|
| TRENDING | ADX > 40 | ✅ | ❌ (too extreme) |
| RANGING | ADX 20-40 | ✅ | ✅ |
| VOLATILE | ADX 20-40 + BB > 0.015 | ✅ | ✅ |
| DEAD | BB < 0.003 | ❌ | ❌ |
| WEAKENING | DI spread < 10 + ATR ratio < 0.75 | ✅ | ✅ |
| CHOPPY | ADX <= 20 + BB > 0.008 | ✅ | ✅ |

**Key Metrics:**
- `adx_h1`: ADX(14) on H1
- `plus_di`, `minus_di`: Directional indicators
- `di_spread`: |plus_di - minus_di|
- `bb_width`: Bollinger Band width on M5
- `atr_ratio`: ATR(5) / ATR(14) — volatility contraction

**Confidence (v34.3):** Cross-check with M5 state for HIGH/LOW confidence.

### 3.2 H1BiasEngine (`h1_bias_engine.py`)

**Purpose:** 4-layer H1 bias analysis — single source for all modes.

**Inputs:** H1 candles, M5 candles, binance_data, regime

**Outputs:** `H1BiasResult` dataclass

**4 Layers:**
| Layer | Name | Logic |
|---|---|---|
| L0 | Structure Bias | H1 BOS/CHoCH direction from fractal swings |
| L1 | Break Bias | Persistent broken high/low tracking (survives cycles) |
| L2 | EMA Trend | EMA9 vs EMA20 on H1 |
| L3 | EMA Macro | EMA20 vs EMA50 on H1 |

**Additional Signals:**
- `lc` (Liquidity Candle): H1 candle bias — where price closes relative to range
- `lr` (Early Reversal): Confluence of early reversal signals
- `lr_count`: Number of reversal confluence factors

**Bias Levels:** STRONG, CONFIRMED+, CONFIRMED, EARLY, EARLY_STRUCTURE, NONE

**M5 Reality Check (v29.1):** H1 bias is refined against M5 state — C4 (M5 confirms H1) and C5 (M5 contradicts H1).

### 3.3 MarketSnapshotBuilder (`market_snapshot.py`)

**Purpose:** Consolidate all market indicators calculated once per cycle.

**Inputs:** M5 candles, H1 candles, binance_data, regime_result, current_price

**Outputs:** `MarketSnapshot` dataclass

**Key Metrics:**
| Metric | Description |
|---|---|
| `atr_m5` | ATR(14) on M5 |
| `delta` | Net delta from recent trades |
| `der` | Delta Efficiency Ratio = |delta| / total_volume |
| `der_direction` | LONG/SHORT/NEUTRAL based on DER |
| `der_persistence` | Consecutive candles with same DER direction (0-5) |
| `der_sustainability` | LOADING/EXHAUSTION/LIKELY/FADING/TOO_EARLY/NEUTRAL |
| `m5_efficiency` | Kaufman Efficiency Ratio (0=sideway, 1=trend) |
| `m5_state` | SIDEWAY/ACCUMULATION/TRENDING/EXHAUSTION/PULLBACK/CAUTION/RANGING |
| `m5_ema_position` | ABOVE_ALL/BELOW_ALL/BETWEEN |
| `m5_candle_pattern` | HAMMER/ENGULFING_BULL/ENGULFING_BEAR/MARUBOZU_BULL/MARUBOZU_BEAR/NONE |
| `volume_ratio_m5` | Last 5 candles vs avg 20 |
| `wall_scan` | Order book wall analysis (raw_dominant, raw_ratio) |
| `oi_change_pct` | Open Interest change |
| `funding_rate` | Binance funding rate |

**M5 State Logic:**
- **TRENDING:** Efficiency > 0.3 + volume rising
- **SIDEWAY:** Efficiency < 0.15 + low volume
- **ACCUMULATION:** Efficiency < 0.15 + volume rising (consolidation with interest)
- **EXHAUSTION:** DER sustainability = EXHAUSTION
- **PULLBACK:** Price pulling back against trend
- **RANGING:** Default when no clear state

**M5 State Calculation (v38.5):** Recalculated ONLY on M5 candle close. Mid-candle results are cached to prevent transition noise (previously 76% of transitions were noise). Forced recalculation occurs only if H1 bias changes within the same candle cycle.

**v38.1 Changes:**
- ER_short (10 candle) is now primary, ER_long is confirmation
- Volume uses gradual ratio instead of binary vol_rising
- State hold timer requires 2 consecutive same state to transition
- Returns er_short instead of er_long as efficiency value

---

## 4. Mode Timing — Smart Interval

### Timing Strategy (v27.2)

| Mode | Trigger | Interval | Data Dependency |
|---|---|---|---|
| IPA | M5 candle close | ~5 minutes | Needs closed candle for structure analysis |
| IPAF | M5 candle close | ~5 minutes | Same as IPA + FRVP data |
| IOF | Timer | Every 15 seconds | Real-time order flow (Delta, DER, OI) |
| IOFF | Timer | Every 15 seconds | Same as IOF + FRVP data |

### Cycle Flow
```
Every 15 seconds:
  1. Fetch M5 candles, H1 candles (cached), current price
  2. Fetch order book, OI, trades, funding rate
  3. Build binance_data dict
  4. Detect new M5 candle (new_candle flag)
  5. Run _run_ipa_iof_analysis():
     a. Regime detection (once)
     b. MarketSnapshot (once)
     c. H1BiasEngine (once)
     d. Terminal display updates
     e. IPA (only if new_candle AND m5_state not SIDEWAY/ACCUMULATION)
     f. IOF (every 15s, if regime.is_iof_suitable)
     g. IPAF (only if new_candle AND FRVP data available)
     h. IOFF (every 15s, if FRVP data AND regime.is_iof_suitable)
  6. Update dashboard state
  7. Publish indicators via ZeroMQ
  8. Heartbeat log every 60s
```

### Heartbeat System
- **To EA:** ZeroMQ heartbeat sent mid-analysis (prevents EA timeout during long analysis)
- **To Log:** Heartbeat log every 60 seconds with price, session, regime, scores

---

## 5. Mode 1: IPA — Institutional Price Action

**File:** `btc_sf_bot/src/analysis/ipa_analyzer.py`

### Purpose
Detect trading opportunities based on Smart Money Concepts: Order Blocks, Fair Value Gaps, Market Structure, Liquidity Sweeps.

### When It Runs
- Only on M5 candle close (`new_candle=True`)
- Blocked if `m5_state` is SIDEWAY or ACCUMULATION
- Blocked if regime is DEAD

### Gate Flow (Inside Analyzer)
```
1. H1 Bias Check → Determine BULLISH/BEARISH/NEUTRAL
2. M5 Structure Break → CHoCH or BOS aligned with H1
3. Order Block Detection → Body > 0.05%, not mitigated, retest within 12 candles
4. Liquidity Context → Discount/Premium zone or recent Sweep
5. Session Filter → Volume multiplier adapted by session
```

### Scoring (Max 20 points, Threshold: 10)

**H1 Structure (max 6):**
| Factor | Points | Condition |
|---|---|---|
| H1 BOS/Break | +3 | Body close beyond swing level |
| H1 CHoCH | +4 (v38.4: +2 in RANGING/CHOPPY) | Change of character detected |
| H1 FVG | +1 | Unfilled fair value gap |

**M5 Entry Quality (max 9):**
| Factor | Points | Condition |
|---|---|---|
| M5 CHoCH | +3 | Change of character on M5 |
| M5 BOS | +2 | Break of structure on M5 |
| OB Quality | +2 | Body > 0.05%, not mitigated |
| OB Retest | +1 | Within 12 candles or zone entry |
| FVG Overlap | +1 | FVG overlaps with OB |

**Liquidity & Context (max 5):**
| Factor | Points | Condition |
|---|---|---|
| Liquidity Sweep | +2 | Sweep confirmed before signal |
| Discount/Premium | +2 | Zone alignment |
| Volume Spike | +1 | Volume > session threshold |

### Signal Types
- **MOMENTUM:** Primary — trend-following entry at OB retest
- **REVERSAL_OB:** OB-based reversal at key level
- **REVERSAL_OS:** Oversold/overbought reversal

### SL/TP Method
Uses `calculate_ipa()` — structural SL based on OB boundaries, magnet-based TP.

### Output: `IPAResult`
- `direction`: LONG/SHORT
- `score`: 0-20
- `ob_high`, `ob_low`: Order block boundaries
- `entry_zone_min`, `entry_zone_max`: Entry price range
- `h1_bias`: BULLISH/BEARISH/NEUTRAL
- `sweep_confirmed`: bool
- `fvg_overlap`: bool
- `volume_spike`: bool
- `atr_m5`: float
- `swing_highs`, `swing_lows`: Lists of swing levels
- `score_breakdown`: Dict of points per factor

---

## 6. Mode 2: IOF — Institutional Order Flow

**File:** `btc_sf_bot/src/analysis/iof_analyzer.py`

### Purpose
Detect trading opportunities based on order flow: Delta, DER, Open Interest, Liquidity Walls, Funding Rate.

### When It Runs
- Every 15 seconds (real-time order flow data)
- Only if `regime.is_iof_suitable` (ADX < 40 or WEAKENING)

### Gate Flow (Inside Analyzer)
```
1. Market Regime → NOT extreme trending (ADX < 40)
2. Delta Absorption → DER > 0.3, Volume > 1.0x average
3. OI Signal → OI change > 0.1%, direction opposite to price (soft)
4. Order Book Wall → Size > session threshold, within 0.5% of price
5. M5 Rejection Candle → Wick or close rejection at wall level
```

### Scoring (Max 20 points, Threshold: 8)

**Delta Absorption Quality (max 7):**
| Factor | Points | Condition |
|---|---|---|
| DER > 3.0 | +5 | Strong absorption |
| DER 2.0-3.0 | +4 | Moderate absorption |
| DER 1.5-2.0 | +3 | Weak absorption |
| Volume Surge > 2.0x | +2 | Significant volume spike |
| Volume Surge 1.2-2.0x | +1 | Moderate volume spike |

**OI & Funding Signal (max 6):**
| Factor | Points | Condition |
|---|---|---|
| OI Divergence > 0.3% | +3 | Strong OI change opposite to price |
| OI Divergence 0.1-0.3% | +2 | Moderate OI change |
| Funding Rate Extreme > |0.05%| | +2 | Opposite to price |
| Funding Rate Moderate 0.02-0.05% | +1 | Moderate funding |

**Wall Quality (max 5):**
| Factor | Points | Condition |
|---|---|---|
| Wall > $1M + Refill | +3 | Large wall confirmed refilled |
| Wall $500K-$1M + Stable | +2 | Medium wall holding |
| Wall $300K-$500K + Stable | +1 | Small wall (ASIA only) |
| Wall stability > 60s | +1 | Persistent wall |

**Confirmation & Penalties (max 2):**
| Factor | Points | Condition |
|---|---|---|
| Liquidation cascade | +1 | Cascade opposite direction |
| M5 Rejection candle | +1 | Rejection at wall level |
| DER Dir Contra (v38.4) | -2 | Penalty: DER points against signal |

### Signal Types
- **MOMENTUM:** Primary — following order flow direction
- **ABSORPTION:** When large orders absorb selling/buying pressure
- **MEAN_REVERT:** When price is far from mean with wall support

### Wall Anti-Spoofing (v28.2)
- Wall must be stable at least 8 seconds (7.5s on weekends)
- Tracks wall persistence via `_wall_tracking` dict
- Refill detection for iceberg behavior

### Cross-Validation IPA ↔ IOF (v9.6)
Block IOF if: IPA has strong bias + IOF direction conflicts + IOF is MOMENTUM type.

### SL/TP Method
Uses `calculate_iof()` — ATR-based SL from current price, magnet-based TP.

### Output: `IOFResult`
- `direction`: LONG/SHORT
- `score`: 0-20
- `wall_price`: Iceberg wall price level
- `wall_size_usd`: Wall size in USD
- `der_score`: Delta Efficiency Ratio
- `oi_change_pct`: OI change percentage
- `signal_type`: MOMENTUM/ABSORPTION/MEAN_REVERT
- `reversal_mode`: STRUCTURAL/STRONG_EXHAUST/EXHAUSTED/MODERATE
- `custom_magnets`: Optional custom magnet levels

---

## 7. Mode 3: IPAF — IPA + FRVP

**File:** `btc_sf_bot/src/analysis/ipa_frvp_analyzer.py`

### Purpose
Combine IPA (OB/FVG/Structure) with FRVP (Fixed Range Volume Profile) for enhanced confluence.

### When It Runs
- Only on M5 candle close (`new_candle=True`)
- Blocked if `m5_state` is SIDEWAY or ACCUMULATION
- Requires FRVP data to be available

### FRVP Engine (`frvp.py`)
**MultiLayerVolumeProfile** calculates:
- **POC** (Point of Control): Price level with most volume
- **VAH/VAL** (Value Area High/Low): 70% value area boundaries
- **HVN** (High Volume Nodes): Price levels with 1.5x median volume
- **LVN** (Low Volume Nodes): Price levels with 0.5x median volume
- **Confluence Zones:** Where OB/FVG overlaps with HVN

**Lookback:** 60 M5 candles (5 hours), $10 price step

### Scoring (Max 20 points, Threshold: 10)
Inherits IPA scoring + FRVP bonuses:
| Factor | Points | Condition |
|---|---|---|
| OB + HVN confluence | +3 | Order block overlaps high volume node |
| OB without HVN | +1 | OB alone (no volume support) |

### Special Rules
- **SL Floor (v26.0):** Minimum $300 SL distance (54% WR below $300 vs 82% above)
- **NY TP Cap (v26.0):** TP capped at SL × 2.0 during NY session

### SL/TP Method
Uses `calculate_ipa()` (same as IPA) with SL floor and NY TP cap adjustments.

---

## 8. Mode 4: IOFF — IOF + FRVP

**File:** `btc_sf_bot/src/analysis/iof_frvp_analyzer.py`

### Purpose
Combine IOF (Wall/Delta/OI) with FRVP for enhanced order flow signals.

### When It Runs
- Every 15 seconds (same as IOF)
- Only if `regime.is_iof_suitable`
- Requires FRVP data

### Scoring (Max 20 points, Threshold: 8)
Inherits IOF scoring + FRVP bonuses:
| Factor | Points | Condition |
|---|---|---|
| Wall + HVN confluence | +2 | Liquidity wall at high volume node |
| LVN breakout | Bonus | Extend TP to next HVN |

### SL/TP Method
Uses `calculate_iof()` (same as IOF).

---

## 9. Signal Gate System (17 Gates)

**File:** `btc_sf_bot/src/signals/signal_gate.py`

All signals must pass ALL 17 gates (v38.4) before being sent to EA. Gates are ordered fastest-first.

| Gate | Name | Logic | Exemptions |
|---|---|---|---|
| 1 | Score Threshold | Score >= mode threshold (IPA/IPAF=10, IOF/IOFF=8) | None |
| 2 | RR Minimum | RR >= 1.0 × 0.85 (15% tolerance) | None |
| 3 | Daily Loss Limit | Daily loss < 3% of balance | None |
| 4 | Max Positions | Max 1 per mode+direction | None |
| 5 | Hard Lock | 60s between same-mode signals (120s if no position) | None |
| 6 | Duplicate ID | No duplicate signal_id | None |
| 7 | Regime Suitability | Block DEAD regime only | None |
| 8 | Wall Contradiction | Block LONG vs ASK wall ≥3x, SHORT vs BID wall ≥3x | MEAN_REVERT |
| 9 | H1 Overextension | Block h1_dist_pct > 1.0% | MEAN_REVERT |
| 10 | DER Climax | Block der_persistence ≥ 3 | None |
| 11 | H1 Bias All Neutral | Block if l0+l1+l2+l3 all NEUTRAL | None |
| 12 | M5 Pullback | Block m5_state = PULLBACK | None |
| 13 | Delta Contra | Block LONG with delta < -500, SHORT with delta > 500 | MEAN_REVERT |
| 14 | EMA Overextension | Block ABOVE_ALL LONG in RANGING/CHOPPY, BELOW_ALL SHORT in RANGING/CHOPPY | None |
| 15 | OB Slippage | Block if price moved > 1.5× ATR from entry | None |
| 16 | DER Zero | Block if `der == 0.0` (v38.4 — no institutional flow) | None |
| 17 | Short Above All | Block SHORT when price ABOVE_ALL EMA in IPA modes (v38.4) | None |

### Gate Result Format
```
PASSED: GateResult(passed=True, reason='PASSED')
BLOCKED: GateResult(passed=False, reason='GATE_NAME_details', blocked_count=1)
```

### Blocked Signals Logged to DB
Every blocked signal is recorded in `gate_blocks` table with:
- signal_id, mode, direction, signal_type, score
- gate_reason, regime, h1_dist, wall_info, delta, der_persistence, m5_state, price

### False Pullback Trap — REMOVED (v37.8)
Previously blocked signals that went against H1 bias. Data proved H1 bias has NO predictive value for MOMENTUM signals (12% WR when following H1 vs 29% neutral). Removed entirely — Wall + DER gates handle direction filtering.

---

## 10. SL/TP Calculator

**File:** `btc_sf_bot/src/signals/sl_tp_calculator.py`

### IPA Method (`calculate_ipa`)
- **SL:** Based on OB boundary + ATR buffer
  - Base: ATR × 1.0 (IPA structural OB → less buffer)
  - Session scaling: ASIA 0.85x, LONDON 1.0x, LONDON-NY 1.15x, NY 1.1x
  - Min: 0.8× ATR, Max: 1.5× ATR
  - Absolute min: 0.2% of entry price
- **TP:** Magnet-based (swing highs/lows, FVG boundaries, POC, VAH/VAL)
  - TP1: Nearest magnet with RR >= 0.8 (BE trigger)
  - TP2: Next magnet with RR >= 1.2 (actual TP)
  - Fallback: ATR × 1.2 (TP1) / ATR × 1.8 (TP2)

### IOF Method (`calculate_iof`)
- **SL:** ATR-based from current price
  - Base: ATR × 1.2 (wall → more buffer)
  - Same session scaling as IPA
- **TP:** Magnet-based + session-adaptive RR
  - Same TP logic as IPA

### IPAF Special Rules
- SL floor: $300 minimum distance
- NY TP cap: TP = SL × 2.0 max

---

## 11. Signal Builder & Contract

**File:** `btc_sf_bot/src/signals/signal_builder.py`

### Signal JSON Contract
```json
{
  "signal_id": "IPA_20260401_120500_LONG",
  "mode": "IPA",
  "direction": "LONG",
  "signal_type": "MOMENTUM",
  "entry_price": 85000.0,
  "stop_loss": 84700.0,
  "take_profit": 85600.0,
  "score": 14,
  "required_rr": 1.90,
  "actual_rr": 2.0,
  "sl_reason": "OB_BOUNDARY",
  "tp_reason": "SWING_HIGH_LIQ",
  "session": "LONDON",
  "regime": "TRENDING",
  "timestamp": "2026-04-01T12:05:00Z",
  "score_breakdown": {...},
  "wall_info": "ASK 154.0x",
  "h1_dist_pct": 0.5,
  "der_persistence": 1,
  "m5_state": "TRENDING",
  "delta": 250.0,
  "m5_ema_position": "BETWEEN",
  "l0": "BULLISH",
  "l1": "BULLISH",
  "l2": "BULLISH",
  "l3": "NEUTRAL",
  "current_price": 85050.0,
  "regime_confidence": "HIGH"
}
```

### Signal ID Format
```
{MODE}_{YYYYMMDD}_{HHMMSS}_{DIRECTION}
Example: IPA_20260401_120500_LONG
         IOF_FRVP_20260401_120515_SHORT
```

---

## 12. AI Analyzer (DeepSeek V3)

**File:** `btc_sf_bot/src/analysis/ai_analyzer.py`

### Provider
- **Platform:** OpenRouter
- **Model:** `deepseek/deepseek-v3.2` ($0.26/$0.38 per 1M tokens)
- **Cost:** ~700 tokens/call, ~288 calls/day = ~$0.09/day

### When It Runs
- **Previously:** Every 5 minutes (candle close)
- **Currently (v28.1):** AI call triggered when EA confirms OPENED trade (saves API cost on skipped signals)
- Cached result available for dashboard display

### Output Format
```json
{
  "bias": "BULLISH",
  "confidence": 75,
  "action": "TRADE",
  "reason": "H1 bullish structure with M5 confirmation...",
  "key_level": 85200.0,
  "timestamp": "2026-04-01T12:05:00Z"
}
```

### AI Integration Phases
| Phase | Status | Description |
|---|---|---|
| Phase 1 | ✅ DONE | Log-only AI analysis |
| Phase 1.5 | ✅ ACTIVE | Alert + Conflict tracking |
| Phase 2 | ⏳ Pending | Score +1/-1 (soft integration) |
| Phase 3 | ⏳ Pending | Score +2/-2 (full integration) |

### AI vs Signal Comparison (Phase 1.5)
- **ALIGNED:** Signal direction matches AI bias → ✅ terminal alert
- **CONFLICT:** Signal direction opposes AI bias → ⚠️ terminal alert (does NOT block)
- **Trade Tracking:** Entry logged with AI data, exit matched by signal_id

### AI Freshness Tag
- **FRESH:** AI result < 30 seconds old (same candle cycle)
- **STALE_{N}s:** AI result N seconds old (IOF/IOFF may use stale AI)

### Trade Log Fields
Each trade in `ai_trade_log` stores:
- signal_id, mode, direction, signal_type, score
- entry_price, stop_loss, take_profit, session
- ai_bias, ai_confidence, ai_action, ai_reason, ai_aligned
- result (WIN/LOSS/BE/PENDING), pnl, exit_reason

---

## 13. Execution Pipeline

### Signal Flow
```
Analyzer (IPA/IOF/IPAF/IOFF)
  → IPAResult / IOFResult
    → SLTPCalculator → SLTPResult
      → SignalBuilder → Signal dict
        → SignalGate → GateResult
          → If PASSED:
            → SignalPublisher (ZeroMQ) → MT5 EA
            → TelegramNotifier → Telegram alert
            → AI Analyzer → Trade entry logged
          → If BLOCKED:
            → DB insert_gate_block()
```

### SignalPublisher (`signal_publisher.py`)
- **Primary:** ZeroMQ (PUB/SUB pattern)
- **Fallback:** File-based (JSON in MT5 Common/Files)
- **Heartbeat:** Published every cycle to keep EA alive

### WebhookServer (`webhook_server.py`)
- Listens on port 8000
- Receives EA confirmations (OPENED, TP, SL, CLOSED)
- Serves dashboard state via HTTP API
- Saves indicators to file for frontend

### EA Confirmation Handling (`on_ea_confirmation`)
| Status | Action |
|---|---|
| OPENED | Log trade opened, trigger AI analysis |
| TP/SL/CLOSED | Log trade exit with PnL, MFE, MAE |

---

## 14. Data Flow

### Input Data Sources
| Source | Data | Frequency |
|---|---|---|
| Binance REST | OHLCV candles (M5, H1) | Every cycle (H1 cached 60s) |
| Binance WebSocket | Real-time trades, order book | Continuous |
| Binance REST | Open Interest | Every cycle |
| Binance REST | Funding Rate | Every cycle |

### Internal Data Pipeline
```
Binance Data
  → MarketCache (trades, order book, volume history)
  → binance_data dict (consolidated)
    → 3 Core Engines (Regime, H1Bias, Snapshot)
      → 4 Analyzers (IPA, IOF, IPAF, IOFF)
        → Signal Gate → Signal Publisher → EA
```

### Database Tables (SQLite)
| Table | Purpose |
|---|---|
| `gate_blocks` | Every blocked signal with reason |
| `ai_trades` | AI-analyzed trades with results |
| `snapshots` | Regime snapshots every 60s |
| `signals` | All signals sent to EA |

---

## 15. Risk Management

### Position Sizing
- **Risk per trade:** 0.5% of account
- **Max daily loss:** 5.0%
- **Max weekly loss:** 10.0%
- **Max positions:** 5 concurrent
- **Max consecutive losses:** 5

### Drawdown Protection
| Tier | Threshold | Action |
|---|---|---|
| Tier 1 | 2.0% | Reduce position size |
| Tier 2 | 4.0% | Block new signals |
| Tier 3 | 6.0% | Close all positions |

### Trailing Stops
- **Handled by EA,** not Python
- IPA mode: Let run (wide trailing)
- IOF mode: Fast lock (tight trailing)

---

## 16. Dashboard State

**File:** `btc_sf_bot/src/execution/webhook_server.py`

### Shared State (`dashboard_state`)
Updated every cycle, served via HTTP API:
```python
dashboard_state = {
    'price': float,
    'session': str,
    'regime': str,
    'timestamp': str,
    'cycle_time': float,
    'cycle_count': int,
    'bot_uptime': str,
    'price_history': [float],
    'ai': {
        'bias': str, 'confidence': int, 'action': str,
        'reason': str, 'key_level': float, 'enabled': bool,
    },
    'market': {
        'ema9': float, 'ema20': float, 'ema50': float,
        'h1_dist_pct': float, 'pullback_status': str,
        'wall_info': str, 'm5_state': str, 'regime': str,
        'h1_bias_level': str, 'm5_efficiency': float,
        'm5_ema_position': str, 'atr_m5': float,
    },
    'bias_layers': {
        'lc': str, 'lr': str, 'lr_count': int,
        'l0': str, 'l1': str, 'l2': str, 'l3': str,
    },
    'modes': {
        'IPA': {'active': bool, 'score': int, 'direction': str},
        'IOF': {'active': bool, 'score': int, 'direction': str},
        'IPAF': {'active': bool, 'score': int, 'direction': str},
        'IOFF': {'active': bool, 'score': int, 'direction': str},
    },
    'last_signal': {...},
    'mlvp': {'composite_poc': float, 'composite_vah': float, ...},
    'ai_stats': {...},
    'order_flow': {
        'delta': float, 'volume_24h': float, 'oi': float,
        'oi_change': float, 'der': float, 'funding_rate': float,
        'der_direction': str, 'der_persistence': int,
        'der_sustainability': str,
    },
    'account': {'balance': float, 'equity': float, ...},
    'positions': [...],
}
```

---

## 17. Configuration Reference

**File:** `btc_sf_bot/config/config.yaml`

### Key Thresholds
| Setting | Value | Description |
|---|---|---|
| `ipa.score_threshold` | 10 | Min score for IPA signal |
| `iof.score_threshold` | 8 | Min score for IOF signal |
| `ipa_frvp.score_threshold` | 10 | Min score for IPAF signal |
| `iof_frvp.score_threshold` | 8 | Min score for IOFF signal |
| `iof.der_min` | 0.3 | Min DER for IOF |
| `iof.min_wall_stability` | 8 | Min wall stability seconds |
| `sl.ipa_base_mult` | 1.0 | IPA SL = ATR × 1.0 |
| `sl.iof_base_mult` | 1.2 | IOF SL = ATR × 1.2 |
| `tp.tp1_rr_min` | 0.8 | TP1 min RR |
| `tp.tp2_rr_min` | 1.2 | TP2 min RR |
| `risk.risk_per_trade` | 0.5 | % account per trade |
| `risk.max_daily_loss` | 5.0 | % max daily loss |
| `signal_gate.HARD_LOCK_SECONDS` | 60 | Min seconds between signals |
| `signal_gate.MAX_POSITIONS_PER_MODE` | 1 | Max 1 per mode+direction |

### Session Thresholds (IOF Walls)
| Session | Wall Threshold |
|---|---|
| ASIA | $100K |
| LONDON | $200K |
| NY | $300K |

### Session SL Scaling
| Session | SL Multiplier |
|---|---|
| ASIA | 0.85x |
| LONDON | 1.0x |
| LONDON-NY | 1.15x |
| NY | 1.1x |
| ASIA-LATE | 0.85x |

---

## 18. File Map

### Core Bot
```
btc_sf_bot/src/main.py                  — Main bot class, run loop
btc_sf_bot/src/enums.py                 — Enumerations
```

### Analysis (src/analysis/)
```
market_regime.py        — Regime detection (TRENDING/RANGING/VOLATILE/DEAD/WEAKENING/CHOPPY)
h1_bias_engine.py       — 4-layer H1 bias (L0-L3, EMAs, LC, LR)
market_snapshot.py      — MarketSnapshot (ATR, Delta, DER, Volume, M5 State)
ipa_analyzer.py         — Mode 1: Institutional Price Action
iof_analyzer.py         — Mode 2: Institutional Order Flow
ipa_frvp_analyzer.py    — Mode 3: IPA + FRVP
iof_frvp_analyzer.py    — Mode 4: IOF + FRVP
frvp.py                 — MultiLayer Volume Profile (POC, VAH, VAL, HVN, LVN)
pullback_detector.py    — Pullback detection
ict.py                  — ICT analysis (OB, FVG, magnets, structure)
order_flow.py           — Order flow metrics (Delta, DER, CVD)
volume_profile.py       — Volume profile analysis
ai_analyzer.py          — AI market analysis (DeepSeek V3)
news_filter.py          — News event filtering
liquidity_wall_analyzer.py — Liquidity wall detection
institutional_flow.py   — Institutional flow patterns (LP, DB, DA)
htf_mss_analyzer.py     — Higher timeframe MSS analysis
structure_validator.py  — Structure validation (BOS, CHoCH)
pattern_tracker.py      — Pattern tracking
```

### Signals (src/signals/)
```
signal_builder.py       — Build signal JSON contracts
signal_gate.py          — 17-gate pre-send filter (v38.4)
signal_manager_v3.py    — Legacy signal manager (deprecated)
sl_tp_calculator.py     — SL/TP calculation (IPA + IOF methods)
bot_state.py            — Bot state management
confluence.py           — Confluence detection
entry_scanner.py        — Entry scanning
session_detector.py     — Session detection (ASIA/LONDON/NY)
smart_flow_manager.py   — Smart flow management
logistic_regression_model.py — ML model for signal scoring
```

### Execution (src/execution/)
```
signal_publisher.py     — ZeroMQ + file-based signal publishing
telegram_alert.py       — Telegram notifications
webhook_server.py       — HTTP server for EA callbacks + dashboard
```

### Data (src/data/)
```
connector.py            — Binance REST connector
websocket.py            — Binance WebSocket handler
binance_fetcher.py      — Additional Binance data fetching
cache.py                — Market data cache
db_manager.py           — SQLite database manager
trade_storage.py        — Trade history storage
ai_report_generator.py  — AI report generation
```

### Risk (src/risk/)
```
position_sizer.py       — Position sizing
trailing_stop_manager.py — Trailing stop management
```

### Utils (src/utils/)
```
logger.py               — Logging setup
terminal_display.py     — Terminal UI display
decorators.py           — Retry, circuit breaker, log_errors
metrics.py              — Performance metrics
config_v2.py            — Configuration manager with validation
```

---

## Quick Reference: Mode Comparison

| Aspect | IPA | IOF | IPAF | IOFF |
|---|---|---|---|---|
| **Basis** | Price Action (OB, FVG, Structure) | Order Flow (Delta, DER, Walls) | IPA + Volume Profile | IOF + Volume Profile |
| **Trigger** | M5 candle close | Every 15s | M5 candle close | Every 15s |
| **Score Threshold** | 10 | 8 | 10 | 8 |
| **SL Method** | OB boundary + ATR | ATR from current price | OB + ATR (floor $300) | ATR from current price |
| **Regime Check** | Not DEAD | ADX < 40 | Not DEAD + not SIDEWAY | ADX < 40 |
| **Signal Types** | MOMENTUM, REVERSAL_OB, REVERSAL_OS | MOMENTUM, ABSORPTION, MEAN_REVERT | Same as IPA | Same as IOF |
| **Key Strength** | Structure-based entries | Real-time flow detection | Volume confluence | Flow + volume confluence |
| **Key Weakness** | Needs closed candle | Can be noisy | Same as IPA | Same as IOF |

---

## Common Patterns to Know

### Signal Type Exemptions from Gates
`MEAN_REVERT` is exempt from Gates 8, 9, and 13 because it trades counter-trend (with the wall, not against it).

### Mode Independence
Each mode operates independently. IPA sending a signal does NOT block IOF from sending. Each mode has its own:
- Score threshold
- Hard lock timer
- Directional lock
- Position count

### Data Parity
What AI receives = What MARKET display shows. No hidden calculations.

### EA Communication
- Python sends signals → EA receives via ZeroMQ
- EA sends confirmations → Python receives via webhook
- EA handles: position sizing, trailing stops, news filtering, execution
- Python handles: analysis, signal generation, gate filtering

---

*Generated: 2026-04-01 | Based on architecture_plan.md v38.5*
