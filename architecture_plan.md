# Architecture Plan — v78.0 — Lane A Optimization & Expansion
**Status:** 🔲 PENDING
**Date:** 2026-04-10
**Lead Architect:** Arch

---

## 1. MOMENTUM: Lane A (Sniper) Quality Relaxation (MOD-99)

**Objective:**
ปรับปรุง Lane A (Institutional Standard) ให้ "ออกหมัด" ได้บ่อยขึ้นในจังหวะที่ต้นเทรนด์กำลังก่อตัว โดยการลดความตึงของเกณฑ์ตัวเลขลงเล็กน้อย แต่ยังคงรักษาคุณภาพการคัดกรองด้วย Order Flow (DER/Imbalance)

### 1.1 ปรับเพดานระยะห่าง (Relax Distance Block)
*   **เดิม:** บล็อกทันทีหากราคาห่าง EMA5 > 50 pts (m5_dist_pts > 50)
*   **ใหม่:** ขยับเพดานเป็น **75 pts**
*   *เหตุผล:* BTC ผันผวนสูง ระยะ 50 pts มักถูกทะลุผ่านไปเร็วเกินไป ทำให้ Lane A ตกรถบ่อยครั้ง

### 1.2 ปรับเกณฑ์ความดุดัน (Relax Imbalance Threshold)
*   **เดิม:** บล็อกถ้า imbalance <= 1.10 (LONG) หรือ >= 0.90 (SHORT)
*   **ใหม่:** ปรับเป็น **<= 1.08** (LONG) และ **>= 0.92** (SHORT)
*   *เหตุผล:* ให้โอกาสสัญญาณที่แรงซื้อขายเริ่มดุดัน (Aggressive) แต่ยังไม่ถึงระดับ Extreme ได้เข้าเทรดเร็วขึ้น

### 1.3 ด่านตรวจพิเศษ (High Flow Bypass)
*   **เงื่อนไข:** หาก der > 0.5 (กระแสเงินไหลเข้าสูงชัดเจน) ให้ถือว่าผ่านด่าน is_perfect_alignment ได้ทันที แม้ Imbalance จะยังไม่ถึงเกณฑ์
*   *เหตุผล:* เมื่อสถาบันอัดฉีดเงิน (High DER) ทิศทางตลาดมักจะคอนเฟิร์มตามมา การรอ Imbalance อาจทำให้เสียจังหวะ

---

## 2. ขั้นตอนการทำงาน (Execution Steps)

1.  [ ] **Dev:** แก้ไข tc_sf_bot/src/detectors/momentum.py (v78.0) ตามเกณฑ์ใหม่
2.  [ ] **Auditor:** ตรวจสอบว่า Lane A ไม่ทับซ้อนกับ Lane B (Rocket) จนเกินไป
3.  [ ] **Test:** ตรวจสอบ Trade Frequency ของ Lane A ว่าเพิ่มขึ้นจริงในจังหวะ Early Trend
