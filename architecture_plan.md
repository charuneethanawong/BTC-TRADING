# Architecture Plan — v80.0 — Volume Profile Sideway Liberation
**Status:** 🔲 PENDING
**Date:** 2026-04-10
**Lead Architect:** Arch

---

## 1. VP_POC: Gate-Based Migration & Sideway Liberation (MOD-101)

**Objective:**
ปัจจุบัน VP_POC มีลอจิกบล็อกสัญญาณในสภาวะ SIDEWAY ซึ่งขัดต่อหลักการของ Volume Profile ที่ควรจะเทรดได้ดีที่สุดในกรอบสะสมพลัง เราจะทำการปลดล็อกนี้และเปลี่ยนระบบเป็น Gate-based เพื่อความเด็ดขาด

### 1.1 ปลดบล็อก SIDEWAY (Unblock Sideway)
*   **Action:** ลบเงื่อนไขที่บล็อก m5_state หากไม่ใช่ Trending/Accumulation/Expansion ทิ้งทั้งหมด
*   **เป้าหมาย:** อนุญาตให้ VP_POC และ VP_BOUNCE ทำงานได้ในทุกสภาวะตลาด (ยกเว้น EXHAUSTION ที่มีความเสี่ยงสูง)

### 1.2 ระบบ Gate-Based (VP_POC v80.0)
*   **Gate 1 (Reaction):** ต้องเป็น FALSE_BO, REJECT, หรือ BREAK (เน้น FALSE_BO และ REJECT ในไซด์เวย์)
*   **Gate 2 (Flow):** der > 0.15 (ลดเกณฑ์เพื่อให้เข้าเทรดในไซด์เวย์ได้ง่ายขึ้น)
*   **Gate 3 (Proximity):** ราคาต้องอยู่ใกล้ POC ในระยะ < 1.0 ATR
*   **Gate 4 (Safety):** บล็อกเฉพาะ EXHAUSTION เท่านั้น

---

## 2. VP_BOUNCE: Verification & State Audit
*   ตรวจสอบเพื่อให้มั่นใจว่าไม่มีลอจิกซ่อนเร้นที่บล็อกสถานะ SIDEWAY หรือ RANGING
*   ยืนยันการใช้ Gate-based ที่รองรับการเทรดในกรอบ

---

## 3. ขั้นตอนการทำงาน (Execution Steps)

1.  [ ] **Dev:** Refactor p_poc.py (Remove Score -> Implement Gates, Remove Sideway Block)
2.  [ ] **Dev:** Audit p_bounce.py เพื่อลบ State Block ที่ไม่จำเป็นออก (ถ้ามี)
3.  [ ] **Auditor:** ตรวจสอบความถูกต้องของการตัดสินใจทิศทางในจังหวะสะบัด (Manipulation)
4.  [ ] **Test:** ตรวจสอบว่าบอทยิงสัญญาณในกรอบ SIDEWAY ได้จริง
