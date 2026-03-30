# Audit Bug Report — BTC SMC AI Project
**Date:** 2026-03-30
**Auditor:** Audit Agent (The Hardcore Reviewer)
**Status:** ACTIVE

---

## ❌ CRITICAL: Data Parity & Logic Issues

### 1. Stale Market Context in AI Trade Log
- **File:** `btc_sf_bot/src/main.py`
- **Location:** `_send_signal` method (around line 1750)
- **Description:** The method uses `dashboard_state` from `src.execution.webhook_server` to build the `market_ctx` for AI trade entry logging. However, `dashboard_state` is only updated at the very end of the `analyze_and_trade` loop via `_update_dashboard_state`. Since `_send_signal` is called during the analysis phase (before the update), it reads market data (regime, bias, etc.) from the **PREVIOUS** analysis cycle.
- **Impact:** AI trade logs contain incorrect/stale market context, leading to biased or incorrect accuracy analysis.
- **Recommendation:** Build `market_ctx` using the current `binance_data` or the results from the current analysis cycle instead of relying on the global `dashboard_state`.

### 2. Missing "Market Snapshot Every Cycle" (Phase 1.5)
- **File:** `btc_sf_bot/src/main.py` and `btc_sf_bot/src/analysis/ai_analyzer.py`
- **Plan Reference:** `AI_TRADING_PLAN.md` (Phase 1.5: Market Snapshot — เก็บทุก cycle)
- **Description:** The plan specifies collecting market snapshots every 5 minutes (regardless of signals) to accelerate AI accuracy measurement. This is currently **MISSING** in the implementation.
- **Notes:** A comment in `main.py` (v28.1) states that AI analysis is now only performed on `OPENED` status to save API costs. While this is a valid optimization, it contradicts the requirement for high-frequency snapshotting needed for rapid accuracy evaluation.
- **Impact:** Slower accuracy measurement (requires waiting for actual trades/signals). Missed opportunities and "correct rejections" are not being tracked.

### 3. Missing `log_market_snapshot` and `evaluate_snapshots`
- **File:** `btc_sf_bot/src/analysis/ai_analyzer.py`
- **Description:** These methods, although planned in `AI_TRADING_PLAN.md`, are missing from the `ai_analyzer.py` implementation. The current methods (`track_analysis` and `evaluate_and_log_accuracy`) only handle per-signal or hourly accuracy, not the 5-minute snapshots.

---

## ✅ PASS: Verified Implementation (v33.1)

### 1. Regime-Adaptive Execution (RAE)
- **Verified:** `signal_gate.py` correctly implements `_check_regime_suitability` to block `MOMENTUM` signals during `RANGING` regimes.

### 2. Mandatory Order Flow Verification (MOFV)
- **Verified:** `signal_gate.py` correctly implements `_check_wall_contradiction` to block trades if opposite wall ratio >= 50x.

### 3. Efficiency-Weighted Confidence (EWC)
- **Verified:** `ai_analyzer.py` correctly implements the 50% confidence penalty for `m5_efficiency < 0.20`.

### 4. Code Standards & Architecture
- **Verified:** All paths use `Path(__file__).resolve()`.
- **Verified:** No calculations in Frontend (Pure View Layer).
- **Verified:** EA handles webhook logging and heartbeat timeout correctly.
- **Verified:** Analyzers follow Single Source of Truth for regime and bias.

---
**Next Actions:**
- Fix `_send_signal` to use current cycle data.
- Decide on the implementation of 5-minute snapshots vs. cost-saving (v28.1).
