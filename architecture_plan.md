# Architecture Plan — BTC M5 Scalping Bot
**Status:** PHASE 1 COMPLETED & VERIFIED (v33.1)
**Date:** 2026-03-30
**Lead Architect:** Arch (Lead Architect)
**Based on:** Analyst Report (Deep Trade Analysis)

---

## 🎯 Strategic Optimization (v33.1)

### 1. 🛡️ Regime-Adaptive Execution (RAE) [COMPLETED]
**Findings:** 100% of recent LOSS trades occurred in `regime: RANGING`.
**Implementation:**
- **Dynamic Gating:** In `RANGING` mode, only `IPAF_POC` or `Mean Reversion` signals are allowed. `MOMENTUM` signals are automatically suppressed.
- **Stop Loss Adjustment:** Use tighter ATR-based SL during ranging markets.

### 2. 🧱 Mandatory Order Flow Verification (MOFV) [COMPLETED]
**Findings:** AI occasionally ignores massive opposite-side walls (e.g., 154x ASK wall during LONG bias).
**Implementation:**
- **Hard-Coded Contradiction Check:** Blocks trades if opposite wall ratio >= 50x.
- **Wall Logic Calibration:** Signal now carries `wall_scan` data for gate verification.

### 3. ⚡ Execution Latency & EA Bridge (ELEB) [IN PROGRESS]
**Findings:** High `EA_SKIPPED` rate due to "No EA confirmation within 2min".
**Implementation:**
- **Optimization:** (Pending Phase 2) Profile Signal -> Webhook -> EA latency.

### 4. ⚖️ Efficiency-Weighted Confidence (EWC) [COMPLETED]
**Findings:** High AI confidence (85%+) often fails when `m5_efficiency` is low (< 0.15).
**Implementation:**
- **Adjusted Confidence Score:** Penalty of 50% applied to AI confidence if `m5_efficiency` < 0.20.

---

## 🛠️ Updated Roadmap (Q2 2026)

### Phase 1: Engine Hardening (COMPLETED)
- [x] Implement **RAE** (Regime-Adaptive Execution) in `signals/signal_gate.py`.
- [x] Implement **MOFV** (Mandatory Order Flow Verification) in `signals/signal_gate.py`.
- [x] Implement **EWC** (Efficiency-Weighted Confidence) in `analysis/ai_analyzer.py`.

### Phase 2: Bridge Optimization (NEXT)
- [ ] Profile the Signal -> Webhook -> EA latency.
- [ ] Optimize MT5 EA tick handler for faster signal acquisition.

---
*Architecture plan updated and verified by Audit.*
