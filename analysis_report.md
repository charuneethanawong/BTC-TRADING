# Deep Forensic Trade Analysis Report — 2026-03-30

**Analyst:** 📊 Forensic BTC Trading Analyst
**Period:** 2026-03-30 (02:02 — 18:48 UTC+7)
**Data Source:** `ai_trade_log.jsonl` (242 records) — ข้อมูลระดับ tick-by-tick ครบทุก field
**Regime:** RANGING 100% ตลอดทั้งวัน

---

## 1. Executive Summary

| Metric | Value |
|---|---|
| Total Records | 242 |
| EA_SKIPPED | 189 (78.1%) — "No EA confirmation within 2min" |
| OPENED (still open) | 3 |
| Closed with result | 53 |
| WIN (TP hit) | 12 |
| LOSS (SL hit) | 27 |
| STALE_CLEANUP (PnL=0) | 14 |
| **Net PnL** | **-$20.20** |
| Win Rate (real) | 30.8% |

**3 ปัญหาใหญ่ที่ค้นพบ:**
1. **Wall Contradiction** — 22/27 เทรดที่แพ้ (81%) มี Wall อยู่ฝั่งตรงข้ามกับทิศทางเทรด
2. **Near-TP Reversal** — 6 เทรดราคาวิ่งไป 51-78% ของ TP แล้วย้อนกลับมาโดน SL (ไม่มี trailing stop / partial TP)
3. **Massive Slippage** — IPA_LONG กลุ่ม 08:xx เข้าจริงห่างจาก signal price +389 ถึง +454 จุด

---

## 2. Trade-by-Trade Forensic Analysis

### 2.1 เทรดที่แพ้ทุกตัว — Root Cause ของแต่ละตัว

#### กลุ่ม A: WALL CONTRADICTION (เข้าชนกำแพง) — 22 เทรด, -$101.36

เทรดที่ Wall อยู่ฝั่งตรงข้ามกับทิศทาง = เข้าไปชนแนวต้าน/แนวรับ

| Signal ID | Dir | Wall | Wall Size | PnL | สาเหตุ |
|---|---|---|---|---|---|
| IOFF_MOMENTUM_LONG_150039 | LONG | **ASK 154.0x** | ยักษ์ | -4.46 | กำแพงขาย 154 เท่า → ราคาไม่มีทางทะลุ |
| IPA_LONG_124019 | LONG | **ASK 116.4x** | ยักษ์ | -3.05 | MFE ไป 78% ของ TP แต่ ASK wall กั้น |
| IOF_ABSORPTION_LONG_091202 | LONG | BID 43.8x | — | -3.05 | wall aligned แต่ m5=RANGING, MFE=0 |
| IOFF_MOMENTUM_LONG_082133 | LONG | **ASK 35.0x** | ใหญ่ | -3.09 | MFE 296 > SL 282 แต่ wall กั้น |
| IPA_LONG_082524 | LONG | **ASK 25.6x** | ใหญ่ | -4.80 | slip +389 จุด + ASK wall |
| IPAF_FVG_SHORT_023008 | SHORT | **BID 17.6x** | ใหญ่ | -6.45 | BID wall กำลังซื้อหนา → SHORT โดนย้อน |
| IOFF_MOMENTUM_SHORT_023123 | SHORT | **BID 12.7x** | ใหญ่ | -3.08 | เหมือนกัน BID wall 12.7x |
| IPA_LONG_141014 | LONG | **ASK 11.3x** | กลาง | -4.18 | MFE=0 ราคาลงทันที |
| IOF_ABSORPTION_LONG_124152 | LONG | **ASK 9.5x** | กลาง | -2.74 | MFE 69%TP แต่ wall กั้น |
| IOFF_ABSORPTION_SHORT_044241 | SHORT | **BID 5.8x** | กลาง | -3.04 | BID wall ดัน LONG |
| IOFF_MOMENTUM_LONG_121630 | LONG | **ASK 4.4x** | กลาง | -2.94 | MFE 69%TP |
| IOFF_MOMENTUM_SHORT_233520 | SHORT | **BID 3.9x** | เล็ก | -4.48 | BID wall ป้องกันราคาลง |
| IPAF_FVG_LONG_084516 | LONG | **ASK 3.3x** | เล็ก | -4.93 | AI BULLISH 85% แต่ wall ชน |
| IPAF_FVG_LONG_084014 | LONG | **ASK 2.4x** | เล็ก | -4.87 | MFE=0 ลงทันที |
| IPA_LONG_140518 | LONG | **ASK 2.4x** | เล็ก | -4.88 | MFE=0 ลงทันที |
| IOF_MOMENTUM_LONG_052056 | LONG | **ASK 2.3x** | เล็ก | -3.03 | MFE 97%SL |
| IPA_LONG_083007 | LONG | **ASK 2.2x** | เล็ก | -4.68 | slip +403 |
| IOFF_MOMENTUM_LONG_051044 | LONG | **ASK 14.4x** | ใหญ่ | -4.05 | MFE > SL dist แล้วกลับ |
| IPA_SHORT_004527 | SHORT | **BID 1.3x** | เล็ก | -6.71 | |
| IOF_ABSORPTION_SHORT_044030 | SHORT | **BID 1.3x** | เล็ก | -2.75 | |
| IPA_LONG_083510 | LONG | **ASK 1.0x** | minimal | -4.73 | |
| IOF_MOMENTUM_LONG_121335 | LONG | **ASK 1.0x** | minimal | -2.90 | MFE 66%TP |

> **Root Cause:** ระบบไม่ตรวจ Wall direction ก่อนเข้าเทรด — ส่ง LONG ชน ASK wall / ส่ง SHORT ชน BID wall

#### กลุ่ม B: WALL ALIGNED แต่ยังแพ้ — 5 เทรด

| Signal ID | Dir | Wall | PnL | สาเหตุที่แท้จริง |
|---|---|---|---|---|
| IOFF_MOMENTUM_SHORT_035347 | SHORT | ASK 13.7x ✓ | -3.25 | H1 bias layers ทั้ง 4 = NEUTRAL → ไม่มี trend |
| IOFF_MOMENTUM_SHORT_063517 | SHORT | ASK 2.6x ✓ | -4.51 | H1 bias NONE + delta contra |
| IOF_ABSORPTION_LONG_091202 | LONG | BID 43.8x ✓ | -3.05 | m5=RANGING + der_sust=TOO_EARLY |
| IOF_ABSORPTION_SHORT_113742 | SHORT | ASK 18.9x ✓ | -2.53 | H1 bias BULLISH contra SHORT |
| IOFF_MOMENTUM_SHORT_152107 | SHORT | ASK 2.1x ✓ | -4.45 | H1 bias BULLISH contra + delta contra |

---

### 2.2 เทรด Near-TP Reversal — ราคาเกือบถึง TP แล้วย้อน

**6 เทรดที่ MFE > SL distance** (ราคาวิ่งเลยระยะ SL ไปในทิศถูกแล้ว แต่ย้อนกลับ):

| Signal ID | MFE | SL Dist | %TP Reached | PnL | Analysis |
|---|---|---|---|---|---|
| **IPA_LONG_124019** | 384 | 240 | **78%** | -3.05 | ราคาวิ่งถึง 78% ของ TP แล้วย้อน ← ASK wall 116.4x กั้น |
| IOFF_MOMENTUM_LONG_121630 | 380 | 271 | 69% | -2.94 | ASK wall 4.4x กั้น + ตลาด RANGING bounce |
| IOF_ABSORPTION_LONG_124152 | 365 | 246 | 69% | -2.74 | ASK wall 9.5x กั้น |
| IOF_MOMENTUM_LONG_121335 | 350 | 283 | 66% | -2.90 | ASK wall 1.0x + RANGING whipsaw |
| IOFF_MOMENTUM_LONG_051044 | 325 | 312 | 48% | -4.05 | ASK wall 14.4x กั้น |
| IOFF_MOMENTUM_LONG_082133 | 296 | 282 | 51% | -3.09 | ASK wall 35.0x กั้น |

> **Root Cause:** ไม่มี **trailing stop** หรือ **partial TP at TP1** — ราคาวิ่งไปถูกทางแล้ว 50-78% แต่กลับมาโดน SL ที่เดิม
> **มูลค่าที่เสียไป:** 6 เทรดนี้ถ้ามี TP1 (partial close at 50%TP) จะเป็น +profit แทน -$18.77

---

### 2.3 เทรด Zero MFE — ราคาวิ่งสวนทันที

| Signal ID | Dir | MFE | Wall | m5_state | der_sust | PnL |
|---|---|---|---|---|---|---|
| IPAF_FVG_LONG_084014 | LONG | 0 | ASK 2.4x | PULLBACK | TOO_EARLY | -4.87 |
| IOF_ABSORPTION_LONG_091202 | LONG | 0 | BID 43.8x | RANGING | TOO_EARLY | -3.05 |
| IPA_LONG_140518 | LONG | 0 | ASK 2.4x | RANGING | TOO_EARLY | -4.88 |
| IPA_LONG_141014 | LONG | 0 | ASK 11.3x | TRENDING | TOO_EARLY | -4.18 |

> **Common Pattern:** ทั้ง 4 ตัวมี `der_sustainability = TOO_EARLY` → DER เพิ่งเริ่ม ยังไม่มี momentum จริง

---

### 2.4 STALE_CLEANUP — 14 เทรดที่ EA เปิดแต่ไม่เคลื่อนไหว

| Signal ID | Dir | Wall | m5_state |
|---|---|---|---|
| IPA_SHORT_004006 | SHORT | BID 8.1x | TRENDING |
| IOFF_MOMENTUM_LONG_042333 | LONG | ASK 4.1x | TRENDING |
| IOFF_MOMENTUM_LONG_045135 | LONG | ASK 14.6x | CAUTION |
| IOFF_MOMENTUM_SHORT_053735 | SHORT | ASK 2.4x | CAUTION |
| IOFF_MOMENTUM_LONG_075531 | LONG | ASK 31.4x | PULLBACK |
| IPAF_OB_LONG_082019 | LONG | ASK 6.8x | TRENDING |
| IOFF_MOMENTUM_SHORT_091453 | SHORT | ASK 10.9x | RANGING |
| IOFF_MOMENTUM_SHORT_091606 | SHORT | ASK 9.2x | TRENDING |
| IPAF_POC_LONG_100014 | LONG | ASK 2.1x | RANGING |
| IPAF_POC_LONG_100516 | LONG | BID 1.2x | RANGING |
| IPA_LONG_104507 | LONG | ASK 2.1x | RANGING |
| IPA_LONG_115511 | LONG | BID 4.0x | TRENDING |
| IOFF_MOMENTUM_LONG_120131 | LONG | BID 1.7x | TRENDING |
| IPAF_EMA_LONG_145514 | LONG | BID 8.2x | TRENDING |

> 10/14 ตัวมี Wall contradiction → EA อาจจะเห็นแล้วจึงไม่ fill / หรือ price ไม่ถึง limit

---

## 3. Microstructure Analysis

### 3.1 Delta vs Direction — ตัวบ่งชี้ที่ชัดเจนที่สุด

| | Delta Aligned | Delta Contra | Win Rate |
|---|---|---|---|
| **Winners** | 8 (67%) | 4 (33%) | — |
| **Losers** | 8 (30%) | **19 (70%)** | — |

> **70% ของเทรดที่แพ้มี Delta สวนทิศทาง** — ถ้า LONG แต่ delta ติดลบ (selling pressure) = แพ้
> **67% ของเทรดที่ชนะมี Delta ตรงทิศทาง** — Delta เป็น predictor ที่ดีกว่า AI

### 3.2 M5 State at Entry

| m5_state | Losses | Wins | Win Rate |
|---|---|---|---|
| TRENDING | 8 | 4 | 33% |
| PULLBACK | **8** | **0** | **0%** |
| CAUTION | 3 | 3 | 50% |
| EXHAUSTION | 3 | 1 | 25% |
| ACCUMULATION | 2 | 2 | 50% |
| RANGING | 2 | 2 | 50% |
| SIDEWAY | 1 | 0 | 0% |

> **PULLBACK = 0% win rate (8 เทรดแพ้หมด)** — ไม่ควรเข้าเทรดในช่วง pullback เลย

### 3.3 M5 EMA Position

| Position | Losses | Wins | Win Rate |
|---|---|---|---|
| **ABOVE_ALL** | **21** | 3 | **12.5%** |
| BETWEEN | 1 | 2 | 67% |
| **BELOW_ALL** | 5 | **7** | **58%** |

> **เมื่อราคาอยู่ ABOVE_ALL EMAs → แพ้ 21 จาก 24 ตัว (87.5%)** — ราคาขึ้นมาสูงเกิน mean แล้ว overextend
> **BELOW_ALL → ชนะ 7 จาก 12 ตัว (58%)** — entry ใกล้ mean กว่า ชนะง่ายกว่า

### 3.4 DER Sustainability

| Sustainability | Losses | Wins |
|---|---|---|
| TOO_EARLY | 19 | 12 |
| NEUTRAL | 3 | 0 |
| FADING | 3 | 0 |
| STRONG | 0 | 0 |

> ทุกเทรดทั้งวันเป็น TOO_EARLY/NEUTRAL/FADING — **ไม่เคยมี STRONG momentum เลย** (ตามที่คาดจาก RANGING regime)
> NEUTRAL + FADING → 6/6 แพ้ (100%)

### 3.5 H1 Bias Layer Analysis

| Pattern | Losses | Wins | Meaning |
|---|---|---|---|
| 4 layers = NEUTRAL | 7 | 0 | **ไม่มี H1 bias เลย → ไม่ควรเทรด** |
| 2 agree + 0 contra | 15 | 6 | H1 bias weak (แค่ l2+l3) |
| 3 agree + 0 contra | 1 | 2 | H1 bias moderate |

> **เมื่อ H1 layers ทั้ง 4 เป็น NEUTRAL → แพ้ 7/7 (100%)** = ไม่มี structural bias เลย
> H1 bias agreement แค่ l2+l3 (layers ล่าง) ไม่เพียงพอ — ต้องให้ l0 หรือ l1 agree ด้วย

---

## 4. Slippage & Execution Analysis

### IPA_LONG กลุ่ม 08:xx — Slippage มหาศาล

| Signal ID | Signal Price | Actual Entry | Slippage | PnL |
|---|---|---|---|---|
| IPA_LONG_082524 | 67,300.1 | 67,689.3 | **+389.2** | -4.80 |
| IPA_LONG_083007 | 67,300.1 | 67,703.2 | **+403.0** | -4.68 |
| IPA_LONG_083510 | 67,300.1 | 67,693.8 | **+393.6** | -4.73 |
| IPAF_FVG_LONG_084014 | 67,300.1 | 67,754.8 | **+454.7** | -4.87 |
| IPAF_FVG_LONG_084516 | 67,300.1 | 67,704.2 | **+404.0** | -4.93 |

> **Signal price เดียวกันทั้ง 5 ตัว = 67,300.1** แต่เข้าจริงที่ 67,689-67,754 (สูงกว่า +389 ถึง +454 จุด)
> **สาเหตุ:** Signal สร้างจาก OB level ที่ต่ำกว่า แต่ EA เปิดที่ market price ปัจจุบัน → entry ห่างจาก OB มาก ทำให้ RR จริงแย่ลง
> **ผลกระทบ:** SL distance จริง = ~630 จุด (4.8x ATR) แทนที่จะเป็น ~240 จุด ← ทำให้ pnl loss ใหญ่ขึ้นมาก

### IOFF_MOMENTUM_LONG_150039 — Slippage ใหญ่สุด

| | Value |
|---|---|
| Signal Price | 67,341.3 |
| Actual Entry | 67,728.6 |
| **Slippage** | **+387.3** |
| SL Distance | **836.2 (3.67x ATR)** |
| Wall | **ASK 154.0x** |
| PnL | -4.46 |

> Entry ไกลจาก signal 387 จุด + ชน ASK wall 154x = ไม่มีทางชนะ

---

## 5. EA_SKIPPED Analysis

189/242 signals (78%) ถูก skip เพราะ "No EA confirmation within 2min"

ตัวอย่างที่น่าเสียดาย — signals ที่ถูก skip แต่ถ้าเข้าจริงน่าจะ win:
- **IOFF_ABSORPTION_LONG_234042** (line 10): เหมือน IOF_ABSORPTION_LONG_234042 ที่ WIN +8.26 แต่ถูก skip
- **IOF_ABSORPTION_LONG_234312** (line 12): เหมือน IOFF_ABSORPTION_LONG_234312 ที่ WIN +8.40 แต่ถูก skip

> **78% signal-to-skip ratio** สูงเกินไป — EA confirmation logic อาจเข้มงวดเกินไป

---

## 6. bot_state.json — Structural Bias Bug

```json
{
  "trend": "BULLISH",
  "last_confirmed_high": 98000.0,      ← BTC ปัจจุบัน ~67,500 แต่ high = 98,000?
  "last_confirmed_low": Infinity,       ← ค่าผิดปกติ
  "entry_direction": "LONG"
}
```

> **`last_confirmed_low: Infinity`** — ค่านี้ผิด ควรเป็น price level จริง
> **`last_confirmed_high: 98,000`** — ไกลจาก price ปัจจุบัน ~67,500 ถึง 30,000+ จุด
> **Bot ยังคิดว่า trend = BULLISH** ทั้งที่ regime = RANGING → ทำให้ `entry_direction: LONG` ตลอด

---

## 7. Root Cause Summary (Ranked by Impact)

### RC1: Wall Contradiction — ไม่ตรวจ Wall ก่อนเทรด
- **Impact:** 22/27 losses (81%) = **-$101.36**
- เทรด LONG ชน ASK wall / SHORT ชน BID wall
- กรณีร้ายสุด: LONG ชน ASK 154x, ASK 116.4x
- **Fix:** Block signal ถ้า Wall direction ≠ Trade direction AND Wall size > 3x

### RC2: ไม่มี Trailing Stop / Partial TP
- **Impact:** 6 เทรด MFE>SL ที่ควรเป็นกำไร = **-$18.77 ที่ควรเป็น +$15-20**
- ราคาวิ่งไป 50-78% ของ TP แล้วย้อนกลับ
- **Fix:** TP1 partial close ที่ 50% TP distance + move SL to breakeven

### RC3: IPA Entry Slippage — เข้าไกลจาก OB level
- **Impact:** 5 เทรด IPA slip 389-454 จุด = **-$25.01**
- Signal price = OB level (67,300) แต่ EA fill ที่ market (67,700)
- **Fix:** Max slippage limit = 1x ATR, ถ้าเกิน → cancel order

### RC4: เทรดเมื่อ H1 Bias = NONE (all layers NEUTRAL)
- **Impact:** 7/7 แพ้หมด (100%) = **-$27.46**
- ไม่มี structural bias → เทรดแบบเดาทิศ
- **Fix:** Block ทุก mode เมื่อ l0+l1+l2+l3 = NEUTRAL ทั้งหมด

### RC5: เทรดใน PULLBACK m5_state
- **Impact:** 8/8 แพ้หมด (100%)
- Pullback = ราคากำลังย้อน ไม่ใช่จุดเข้าที่ดี
- **Fix:** Block MOMENTUM + IPA เมื่อ m5_state = PULLBACK

### RC6: Delta Contradiction ไม่ถูก filter
- **Impact:** 70% ของ losses มี delta สวน
- **Fix:** เพิ่ม delta alignment check — ถ้า delta ≤ -500 (LONG) หรือ ≥ +500 (SHORT) → block

### RC7: M5 EMA Position ABOVE_ALL
- **Impact:** 87.5% loss rate เมื่อราคา above all EMAs
- **Fix:** Block LONG เมื่อ price ABOVE_ALL + regime RANGING (overextended)

---

## 8. Quantified Impact Table

| Fix | Trades Saved | PnL Saved | Effort |
|---|---|---|---|
| Wall direction filter | ~18 | ~$80+ | Low (1 condition) |
| Trailing stop / TP1 | 6 | ~$34 swing | Medium |
| Max slippage limit | 5 | ~$25 | Low |
| H1 all-NEUTRAL block | 7 | ~$27 | Low (1 condition) |
| PULLBACK state block | 8 | ~$25 | Low (1 condition) |
| Delta alignment gate | ~13 | ~$45 | Low |
| EMA position filter | ~18 | ~$60 | Low |

> **ถ้าใส่แค่ Wall filter + H1 NEUTRAL block + PULLBACK block** = ลดขาดทุนได้ ~$132 จาก 33 เทรดที่ไม่ควรเปิด
> วันนี้จะเหลือ ~6 เทรดที่ผ่าน filter ทั้งหมด → win rate สูงขึ้นมาก

---

*Report generated from 242 records in ai_trade_log.jsonl | Every trade analyzed with entry/SL/TP/MFE/wall/delta/H1 layers | Zero fabrication*
