# Design Spec: AI Trade Analysis Report UI

## Data Source
`ai_trade_log.jsonl` — ทุก trade ที่ bot ส่ง signal มี fields:
```
timestamp, signal_id, mode, direction, signal_type, score,
entry_price, stop_loss, take_profit, session,
ai_bias, ai_confidence, ai_action, ai_reason, ai_aligned,
status (WIN/LOSS/EA_SKIPPED/OPENED), pnl, exit_reason
```

---

## Report Sections (Top → Bottom)

### 1. Overall Performance Summary
แสดงเป็น **4 cards** แถวเดียว:

| Card | ค่า | สี |
|------|-----|-----|
| Total PnL | +$42.50 หรือ -$12.30 | เขียว/แดง |
| Win Rate | 71.4% (15W / 6L) | เขียว >60%, เหลือง 40-60%, แดง <40% |
| Profit Factor | 2.1 (กำไรรวม / ขาดทุนรวม) | เขียว >1.5, แดง <1.0 |
| Avg Win vs Avg Loss | +$3.20 / -$5.10 | แสดงคู่กัน |

### 2. BE (Breakeven) Analysis
ปัญหาสำคัญ: WIN ส่วนใหญ่อาจเป็น BE ($0.30) ไม่ใช่ TP จริง

| ค่า | แสดงอะไร |
|-----|---------|
| BE Count | 12 ใน 15 WIN = 80% เป็น BE |
| Real TP Count | 3 ใน 15 WIN = 20% ถึง TP |
| Avg BE PnL | $0.26 (แทบไม่มีกำไร) |
| Avg Real TP PnL | $8.26 (กำไรจริง) |
| BE Trigger RR | TP1 RR ที่ตั้งไว้ (ปัจจุบัน 1.2) |

**Visual:** Donut chart — BE% vs Real TP%

### 3. Mode Performance
4 modes เปรียบเทียบกัน:

| Mode | Trades | Win | Loss | WR% | PnL | Avg SL Dist |
|------|--------|-----|------|-----|-----|-------------|
| IPA | 6 | 5 | 1 | 83% | +$25 | $293 |
| IOF | 3 | 2 | 1 | 67% | +$4 | $356 |
| IPAF | 5 | 3 | 2 | 60% | -$2 | $793 |
| IOFF | 7 | 5 | 2 | 71% | +$15 | $386 |

**Visual:** Horizontal bar chart — PnL per mode (เขียว/แดง)

**Insight ที่ต้องแสดง:**
- Mode ที่ดีที่สุด (WR + PnL)
- Mode ที่ SL กว้างเกิน (IPAF?)
- Mode ที่ควรปิด (ถ้ามี)

### 4. Signal Type Performance
แยกตาม MOMENTUM / ABSORPTION / REVERSAL_OB / REVERSAL_OS / MEAN_REVERT

| Type | Trades | WR% | PnL | BE% |
|------|--------|-----|-----|-----|
| MOMENTUM | 10 | 70% | +$8 | 85% |
| ABSORPTION | 5 | 60% | +$3 | 80% |
| REVERSAL_OB | 3 | 33% | -$5 | 0% |
| REVERSAL_OS | 2 | 50% | +$1 | 50% |
| MEAN_REVERT | 1 | 100% | +$2 | 0% |

**Insight ที่ต้องแสดง:**
- Type ไหน BE บ่อยสุด
- Type ไหนถึง TP จริงบ่อยสุด
- Type ไหนขาดทุนเยอะสุด

### 5. Session Performance
3 sessions เปรียบเทียบ:

| Session | Trades | WR% | PnL | AI Match% | Best Type |
|---------|--------|-----|-----|-----------|-----------|
| ASIA | 8 | 75% | +$12 | 80% | MOMENTUM |
| LONDON | 10 | 60% | +$5 | 65% | ABSORPTION |
| NY | 3 | 33% | -$8 | 40% | — |

**Visual:** 3 cards + heatmap (session x signal_type → WR%)

**Insight ที่ต้องแสดง:**
- Session ไหนกำไรมากสุด
- Session ไหนควรหยุดเทรด
- AI แม่นที่สุดใน session ไหน

### 6. AI vs Bot Agreement Analysis
วิเคราะห์ว่าฟัง AI ดีไหม:

| Scenario | Count | WR% | PnL |
|----------|-------|-----|-----|
| AI Aligned + WIN | 10 | — | +$30 |
| AI Aligned + LOSS | 3 | — | -$10 |
| AI Conflict + WIN | 2 | — | +$5 |
| AI Conflict + LOSS | 6 | — | -$20 |

**Visual:** 2x2 matrix (aligned/conflict × win/loss) — สีตามค่า

**สรุปชัดเจน:**
- Aligned WR: 77% vs Conflict WR: 25%
- **"ฟัง AI ดีกว่า"** หรือ **"AI ยังไม่แม่น"**

### 7. AI Confidence vs Accuracy
วิเคราะห์ว่า AI มั่นใจมาก = แม่นจริงไหม:

| Confidence Range | Trades | WR% |
|-----------------|--------|-----|
| 70-100% | 5 | 80% |
| 50-69% | 12 | 67% |
| 30-49% | 4 | 25% |

**Insight:** ถ้า confidence > 70% WR สูง → เชื่อ AI ตอนมั่นใจ

### 8. Loss Pattern Analysis
ตาราง LOSS trades ทั้งหมด + หาจุดร่วม:

| Pattern | Count | % of Losses |
|---------|-------|-------------|
| AI Conflict | 4/6 | 67% |
| IPAF mode | 2/6 | 33% |
| NY session | 3/6 | 50% |
| SL > $500 | 2/6 | 33% |
| REVERSAL type | 2/6 | 33% |

**Insight:** "67% ของ LOSS เกิดตอน AI ไม่เห็นด้วย + 50% เกิดใน NY session"

### 9. SL/TP Efficiency
วิเคราะห์ว่า SL/TP ตั้งเหมาะหรือไม่:

| Metric | ค่า |
|--------|-----|
| Avg SL Distance | $380 |
| Avg TP Distance | $680 |
| Avg Actual RR | 1.80 |
| TP Hit Rate | 20% (3/15 WIN) |
| BE Rate | 80% (12/15 WIN) |
| SL Hit Rate | 29% (6/21 total) |

**Visual:** Bar — SL dist per mode (IPAF กว้างสุด?)

### 10. AI Recommendations
ผลจากปุ่ม ANALYZE (Claude/Gemini/DeepSeek):
- 5-8 bullet points
- Actionable: "ปิด IPAF ใน NY", "เพิ่ม BE trigger RR เป็น 1.5"
- Data-driven: อ้างอิงตัวเลขจาก log

---

## Layout

```
┌─────────────────────────────────────────────────────────┐
│ [1] Overall: PnL | WR | PF | AvgW/L     (4 cards)      │
├─────────────────────────────────────────────────────────┤
│ [2] BE Analysis: Donut + stats           (1 section)    │
├──────────────────────┬──────────────────────────────────┤
│ [3] Mode Performance │ [4] Signal Type Performance      │
│     (bar chart)      │     (table + bar)                │
├──────────────────────┴──────────────────────────────────┤
│ [5] Session Performance: 3 cards + heatmap              │
├─────────────────────────────────────────────────────────┤
│ [6] AI vs Bot: 2x2 matrix   │ [7] Confidence vs WR     │
├──────────────────────────────┴──────────────────────────┤
│ [8] Loss Patterns: table                                │
├─────────────────────────────────────────────────────────┤
│ [9] SL/TP Efficiency: bars                              │
├─────────────────────────────────────────────────────────┤
│ [10] AI Recommendations: [CLAUDE] [GEMINI] [DEEPSEEK]   │
│      bullet points from AI analysis                     │
└─────────────────────────────────────────────────────────┘
```

---

## Color System

| สถานะ | สี | Hex |
|--------|-----|-----|
| WIN / Positive / Aligned | เขียว | #9cff93 |
| LOSS / Negative / Conflict | แดง | #ff7351 |
| BE / Neutral / Warning | เหลือง | #ffc15b |
| Info / Session / AI | ฟ้า | #00e3fd |
| Inactive / Label | เทา | #ababab |
| Background | ดำ | #0e0e0e / #131313 |
| Card surface | เทาเข้ม | #191919 / #262626 |

## Typography
- Headlines: Space Grotesk (bold, uppercase, tight tracking)
- Data/Numbers: JetBrains Mono (monospace)
- Labels: 9-11px uppercase tracking-widest
- Values: 14-28px bold
