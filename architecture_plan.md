# Architecture Plan — v77.0 — Dual-Detector Condition-Based Migration
**Status:** 🔲 PENDING
**Date:** 2026-04-10
**Lead Architect:** Arch

---

## 1. VP_BREAKOUT: Score-to-Condition Migration (MOD-97)

**Objective:**
เปลี่ยนจากระบบสะสมแต้ม (Score-based) ที่ทำให้ "ตกรถ" เพราะแต้ม Lagging เป็นระบบ **Gate-based (Two-Lane)** เพื่อการตัดสินใจที่เฉียบขาดและทันที (Zero-Lag)

### 1.1 เงื่อนไขบังคับ (Gates)
*   **Gate 1 (Trigger):** ราคาเบรก VAH/VAL และ olume_ratio > 1.2
*   **Gate 2 (Confirmation):** der > 0.4 และ delta สอดคล้องกับทิศทางเบรก
*   **Gate 3 (Blocker):** บล็อกถ้า oi_change < -0.05 (Liquidation) หรือ m5_state == EXHAUSTION
*   **Gate 4 (Wall):** บล็อกถ้ามีกำแพงหนา (> 20x) ขวางหน้าในระยะใกล้

---

## 2. VP_BOUNCE: Score-to-Condition Migration (MOD-98)

**Objective:**
อัปเกรด VP_BOUNCE ให้มีความเด็ดขาดและแม่นยำขึ้น โดยใช้ระบบ Gate-based แทนการสะสมแต้มแบบเดิม เพื่อป้องกันความสับสนและลดโอกาสพลาดสัญญาณเด้งที่มีคุณภาพ (Quality Bounce)

### 2.1 เงื่อนไขบังคับ (Gates)
*   **Gate 1 (Precise Reaction):** ต้องเป็น FALSE_BO หรือ REJECT เท่านั้น (บล็อก BREAK ทุกกรณี)
*   **Gate 2 (Flow Alignment):** der > 0.15 และทิศทางต้องสอดคล้องกับการเด้ง (Confirm เงินไหลเข้าช่วยดัน)
*   **Gate 3 (Distance Guard):** ระยะห่างจากระดับนัยสำคัญ (HVN/VAH/VAL) ต้อง < 1.0 ATR (เพื่อ SL ที่แคบและคุ้มค่า)
*   **Gate 4 (Blocker):** บล็อกถ้า m5_state == EXHAUSTION (เสี่ยงโดนลากทะลุ)

---

## 3. ขั้นตอนการทำงาน (Execution Steps)

1.  [ ] **Dev:** Refactor p_breakout.py (Remove Score -> Implement Gates)
2.  [ ] **Dev:** Refactor p_bounce.py (Remove Score -> Implement Gates)
3.  [ ] **Auditor:** ตรวจทานความปลอดภัยของ Gates และการตั้ง score_threshold = 1
4.  [ ] **Test:** ตรวจสอบว่าระบบส่งสัญญาณได้ตามเป้าหมายและไม่ติดคอขวดคะแนนอีกต่อไป
