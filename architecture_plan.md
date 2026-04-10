# Architecture Plan — v75.0 — Symmetric Trend Support Sniper
**Status:** 🔲 PENDING
**Date:** 2026-04-07
**Lead Architect:** Arch

---

## 1. REVERSAL: Trend Support Sniper Lane (MOD-96)

**Objective:** ปลดล็อกจุดเข้า "ย่อซื้อ/เด้งขาย" (Buy the Dip / Sell the Rally) ที่บริเวณเส้นค่าเฉลี่ย H1 ซึ่งเป็นจุดที่ได้เปรียบที่สุด แต่ปัจจุบันถูกบล็อกด้วยกฎ H1 Distance > 0.5%

### **Actions: Lane C - Trend Support (Symmetric)**
อนุญาตให้เทรด Reversal ได้แม้ **H1 Dist < 0.5%** หากเข้าเงื่อนไข **"3 ประสานแห่งการพักตัว"** ครบทุกข้อ:

1.  **Trend Alignment (ทิศทางหลัก):**
    *   **LONG:** H1 Bias == BULLISH
    *   **SHORT:** H1 Bias == BEARISH
2.  **M5 Deep Stretch (แรงย่อถึงจุดพีค):**
    *   **LONG:** ราคาต้องอยู่ใต้เส้น M5 EMA20 > 70 pts
    *   **SHORT:** ราคาต้องอยู่เหนือเส้น M5 EMA20 > 70 pts
3.  **Institutional Hard Confirmation (สถาบันยืนยัน):**
    *   **Wall Ratio:** ต้องหนา **> 5.0x** (ป้องกันการไหลทะลุเส้นแนวรับ/ต้านใหญ่)
    *   **DER Force:** ต้องอ่อนแรงสุดขีด **< 0.15** (ยืนยันว่า Pullback จบแล้ว ไม่ใช่เทรนด์เปลี่ยนทิศ)
    *   **OI Change:** ต้องเป็นบวก **> 0.0%** (มีเงินใหม่เข้ามาช่วยยันที่เส้น)

---

## 2. REVERSAL: Triple-Lane Decision Matrix

ปรับโครงสร้าง Reversal เป็น 3 เลนเพื่อให้ครอบคลุมทุกโอกาส:
- **Lane A (Standard):** H1 Dist > 0.5% + Wall > 1.4x + Exhaustion (<0.15)
- **Lane B (V-Shape):** H1 Dist > 0.5% + Wall > 5.0x + V-Shape (>0.35)
- **Lane C (Trend Sniper):** H1 Dist < 0.5% + Wall > 5.0x + Exhaustion (<0.15) + **Trend Support**

---

## 3. Implementation History (Consolidated)

| MOD | Description | Status |
|-----|-------------|--------|
| 1-92 | Momentum & Global Fixes | ✅ FIXED |
| 93-94 | Reversal Dual-Confirmation & Buffer | ✅ FIXED |
| 95 | Momentum Sensitivity Tuning (v74.0) | ✅ FIXED |
| 96 | Reversal Lane C: Trend Support Sniper | 🔲 PENDING |

---

## System Reference (v75.0 Target)
`
REVERSAL:     3-Lane System (Standard | V-Shape | Trend Support)
MOMENTUM:     2-Lane System (Perfect Alignment | Pure Force Rocket)
IPA:          Adaptive Tension Sniper (H1 Dist > 0.5% for Late Bias)
ABSORPTION:   Flexible Wall (30x or 15x+ER>0.4) + ATR_R < 1.2
`

---

*Architecture Plan v75.0 — Buying the dip with institutional precision.*