# AI Trading Analysis Plan
**Version:** 2.0
**Date:** 2026-03-25
**Status:** ACTIVE — Phase 1.5

---

## AI Provider: OpenRouter — Strategy A (DeepSeek V3 ตัวเดียว)

```
Model:  deepseek/deepseek-chat-v3-0324:free หรือ deepseek/deepseek-chat ($0.30/$0.88)
Budget: $5 credit → ~55 วัน ($0.09/วัน)

Usage: ~700 tokens/call
  DeepSeek V3: 288 calls/วัน (ทุก 5 นาที) = $0.09/วัน

ทำไม DeepSeek V3:
  - เก่งตัวเลข/pattern recognition (Chinese model แข็งด้าน math)
  - ราคาถูก ($0.30/$0.88 per 1M)
  - มี free tier ด้วย
  - Context window ใหญ่
  - Prompt ภาษาอังกฤษ (bot ส่งข้อมูลเป็นตัวเลข — ไม่ใช่ปัญหา)
```

---

## Phase Overview

```
Phase 1:   Log only ✅ DONE (เก็บ AI analysis log)
Phase 1.5: Alert + Conflict tracking ← CURRENT
Phase 2:   Score +1/-1 (soft integration)
Phase 3:   Score +2/-2 + 2-tier AI (full integration)
```

---

## Phase 1.5: Alert + Conflict Tracking (ACTIVE)

### เป้าหมาย

```
1. แสดง alert เมื่อ AI สวน signal (ไม่กระทบ score)
2. เก็บ trade entry + AI analysis → รอ trade result
3. วัดว่า "AI conflict = loss จริงกี่ %"
4. สะสมข้อมูลก่อนไป Phase 2
```

### Token Cost: เท่า Phase 1 ($0.04-0.09/วัน)

```
ไม่ call AI เพิ่ม — แค่เอา result ที่มีอยู่ → เทียบกับ signal → log + alert
```

### Flow

```
ทุก 5 นาที:
  ┌──────────────┐
  │ AI Analysis   │ ← เหมือน Phase 1
  │ (Gemini/Claude)│
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │ Analyzers     │
  │ IPA/IOF/etc.  │
  └──────┬───────┘
         │
         ▼
  ┌──────────────────────────────────────────────────┐
  │ Signal Built?                                      │
  │                                                    │
  │ YES → เทียบ AI vs Signal:                          │
  │       AI aligned  → ✅ terminal alert              │
  │       AI conflict → ⚠️ terminal alert              │
  │       → เก็บ trade entry + AI data → PENDING       │
  │                                                    │
  │ NO  → log AI analysis ปกติ                         │
  └──────────────────────────────────────────────────┘

ตอน trade ปิด (EA confirmation):
  ┌──────────────────────────────────────────────────┐
  │ จับคู่ signal_id → update result:                  │
  │   AI aligned + WIN  → "AI ถูก"                    │
  │   AI aligned + LOSS → "AI ถูกแต่ trade ยังแพ้"    │
  │   AI conflict + WIN → "AI ผิด"                    │
  │   AI conflict + LOSS → "AI เตือนถูก!"             │
  └──────────────────────────────────────────────────┘
```

### Implementation

#### 1. main.py — Signal Alert (ตอนส่ง signal)

```python
# หลัง signal built + ก่อน send:

ai_result = binance_data.get('ai_analysis')
if ai_result and signal:
    ai_bias = ai_result.get('bias', 'NEUTRAL')
    signal_dir = signal.get('direction', '')

    ai_aligned = (
        (signal_dir == 'LONG' and ai_bias == 'BULLISH') or
        (signal_dir == 'SHORT' and ai_bias == 'BEARISH')
    )

    if not ai_aligned and ai_bias != 'NEUTRAL':
        # ⚠️ AI สวน signal → alert (ไม่ block)
        self.terminal.gate('AI:', 'bolt',
            f"⚠️ CONFLICT: {signal.get('mode')} {signal_dir} vs AI {ai_bias} {ai_result.get('confidence')}%")
        logger.warning(
            f"⚠️ AI CONFLICT: {signal.get('mode')} {signal_dir} "
            f"but AI says {ai_bias} {ai_result.get('confidence')}%"
        )
    else:
        self.terminal.gate('AI:', True,
            f"✓ ALIGNED: {ai_bias} {ai_result.get('confidence')}%")

    # เก็บ trade entry + AI data
    self.ai_analyzer.log_trade_entry(
        signal_id=signal.get('signal_id'),
        signal=signal,
        ai_analysis=ai_result
    )
```

#### 2. main.py — Trade Exit (ตอน EA ส่ง confirmation)

```python
# on_confirmation_from_webhook():

async def on_confirmation_from_webhook(self, confirmation_data):
    # ... existing code ...

    # v18.7 Phase 1.5: Update AI trade result
    signal_id = confirmation_data.get('signal_id', '')
    if not signal_id:
        # ลอง extract จาก comment
        signal_id = confirmation_data.get('comment', '')

    pnl = confirmation_data.get('profit', 0)
    exit_reason = confirmation_data.get('status', 'UNKNOWN')

    if signal_id and hasattr(self, 'ai_analyzer'):
        self.ai_analyzer.log_trade_exit(
            signal_id=signal_id,
            pnl=pnl,
            exit_reason=exit_reason
        )
```

#### 3. ai_analyzer.py — Trade Entry Log

```python
def log_trade_entry(self, signal_id: str, signal: Dict, ai_analysis: Optional[Dict]):
    """บันทึก trade entry + AI analysis — PENDING result"""
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'signal_id': signal_id,
        'mode': signal.get('mode'),
        'direction': signal.get('direction'),
        'signal_type': signal.get('signal_type', 'MOMENTUM'),
        'score': signal.get('score'),
        'entry_price': signal.get('entry_price'),
        'stop_loss': signal.get('stop_loss'),
        'take_profit': signal.get('take_profit'),
        'session': signal.get('session'),

        # AI data ตอนเข้า
        'ai_bias': ai_analysis.get('bias') if ai_analysis else None,
        'ai_confidence': ai_analysis.get('confidence') if ai_analysis else None,
        'ai_action': ai_analysis.get('action') if ai_analysis else None,
        'ai_reason': ai_analysis.get('reason', '')[:100] if ai_analysis else None,
        'ai_aligned': self._check_aligned(signal.get('direction'), ai_analysis),

        # Result — จะ update ทีหลัง
        'result': 'PENDING',
        'pnl': None,
        'exit_reason': None,
        'closed_at': None,
    }
    self._append_jsonl('data/ai_trade_log.jsonl', entry)
```

#### 4. ai_analyzer.py — Trade Exit Update

```python
def log_trade_exit(self, signal_id: str, pnl: float, exit_reason: str):
    """อัปเดต trade result — จับคู่ signal_id"""
    try:
        log_path = Path('data/ai_trade_log.jsonl')
        if not log_path.exists():
            return

        lines = log_path.read_text().strip().split('\n')
        updated = False

        for i, line in enumerate(lines):
            record = json.loads(line)
            if record.get('signal_id') == signal_id and record.get('result') == 'PENDING':
                record['result'] = 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'BE'
                record['pnl'] = round(pnl, 2)
                record['exit_reason'] = exit_reason
                record['closed_at'] = datetime.now(timezone.utc).isoformat()
                lines[i] = json.dumps(record, default=str)
                updated = True

                # Log สรุป
                aligned = record.get('ai_aligned')
                result = record['result']
                logger.info(
                    f"[AI] Trade Result: {signal_id} → {result} PnL:{pnl:.2f} | "
                    f"AI {'✓ aligned' if aligned else '✗ conflict'} "
                    f"({record.get('ai_bias')} {record.get('ai_confidence')}%)"
                )
                break

        if updated:
            log_path.write_text('\n'.join(lines) + '\n')
    except Exception as e:
        logger.debug(f"[AI] Trade exit log error: {e}")

def _check_aligned(self, direction: str, ai_analysis: Optional[Dict]) -> Optional[bool]:
    if not ai_analysis:
        return None
    bias = ai_analysis.get('bias', 'NEUTRAL')
    if bias == 'NEUTRAL':
        return None
    return (direction == 'LONG' and bias == 'BULLISH') or \
           (direction == 'SHORT' and bias == 'BEARISH')
```

#### 5. ai_analyzer.py — Conflict Summary (เรียกทุก 1 ชม.)

```python
def get_conflict_stats(self) -> Dict:
    """สรุปสถิติ AI aligned vs conflict"""
    try:
        log_path = Path('data/ai_trade_log.jsonl')
        if not log_path.exists():
            return {}

        aligned_win = 0
        aligned_loss = 0
        conflict_win = 0
        conflict_loss = 0
        pending = 0

        with open(log_path, 'r') as f:
            for line in f:
                r = json.loads(line.strip())
                result = r.get('result')
                aligned = r.get('ai_aligned')

                if result == 'PENDING':
                    pending += 1
                    continue
                if aligned is None:
                    continue

                if aligned and result == 'WIN': aligned_win += 1
                elif aligned and result == 'LOSS': aligned_loss += 1
                elif not aligned and result == 'WIN': conflict_win += 1
                elif not aligned and result == 'LOSS': conflict_loss += 1

        total_aligned = aligned_win + aligned_loss
        total_conflict = conflict_win + conflict_loss

        return {
            'aligned_win_rate': round(aligned_win / total_aligned * 100, 1) if total_aligned > 0 else 0,
            'conflict_win_rate': round(conflict_win / total_conflict * 100, 1) if total_conflict > 0 else 0,
            'aligned_total': total_aligned,
            'conflict_total': total_conflict,
            'pending': pending,
        }
    except:
        return {}
```

### Terminal Display

```
──── ▶ [IPA] Mode 1 ─────────────────────────────
  Score:    ✅ 12/10 → SIGNAL SENT
  AI:       ✅ ALIGNED: BULLISH 75%

──── ▶ [IOF] Mode 2 ─────────────────────────────
  Score:    ✅ 7/6 → SIGNAL SENT
  AI:       ⚠️ CONFLICT: LONG vs AI BEARISH 68%

──────────────────────────────────────────────────
  📨 Signals: IPA_LONG S12 | IOF_LONG S7
  🤖 AI Stats: Aligned 65% win (20 trades) | Conflict 33% win (12 trades)
  ⏱️  Cycle: 1.2s
══════════════════════════════════════════════════
```

### Data Files

```
data/
├── ai_analysis_log.jsonl      # ทุก AI analysis (Phase 1)
├── ai_trade_log.jsonl         # trade entry/exit + AI data (Phase 1.5)
├── ai_snapshots.jsonl         # ทุก cycle snapshot + price after (Phase 1.5) ← ใหม่!
├── ai_market_results.jsonl    # AI bias vs actual price (Phase 1)
├── ai_accuracy_log.jsonl      # accuracy สะสม (Phase 1)
└── ai_conflicts.jsonl         # conflict alerts (Phase 1.5)
```

---

### Market Snapshot — เก็บทุก cycle (มี signal หรือไม่)

#### ปัญหา

```
ปัจจุบัน: เก็บ AI vs trade เฉพาะตอนมี signal
  → ข้อมูลน้อย (10-30 signals/วัน)
  → ต้องรอ 1-2 สัปดาห์กว่าจะวัด accuracy ได้

ไม่มี signal (ส่วนใหญ่ 70%+ ของเวลา):
  → AI วิเคราะห์แล้วทิ้ง → เสียข้อมูล
```

#### แก้: เก็บ Market Snapshot ทุก cycle

```python
# main.py — ท้าย analyze cycle ทุกครั้ง:

snapshot = {
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'price': current_price,
    'session': session,

    # AI
    'ai_bias': ai_result.get('bias') if ai_result else None,
    'ai_confidence': ai_result.get('confidence') if ai_result else None,
    'ai_action': ai_result.get('action') if ai_result else None,

    # Signals sent?
    'ipa_sent': ipa_signal_sent,
    'iof_sent': iof_signal_sent,
    'ipaf_sent': ipaf_sent,
    'ioff_sent': ioff_sent,
    'any_signal': ipa_signal_sent or iof_signal_sent or ipaf_sent or ioff_sent,

    # Block reasons (เฉพาะตอนไม่มี signal)
    'ipa_block': getattr(self, '_last_ipa_block_reason', None),
    'iof_block': getattr(self, '_last_iof_block_reason', None),

    # จะ update ทีหลัง
    'price_after_5m': None,
    'price_after_1h': None,
    'ai_correct': None,
    'missed_opportunity': None,
}
self.ai_analyzer.log_market_snapshot(snapshot)
```

#### ai_analyzer.py — Snapshot Log + Evaluate

```python
def log_market_snapshot(self, snapshot: Dict):
    """เก็บ market snapshot ทุก cycle"""
    self._append_jsonl('data/ai_snapshots.jsonl', snapshot)
    self._snapshots.append(snapshot)
    if len(self._snapshots) > 500:
        self._snapshots = self._snapshots[-300:]

def evaluate_snapshots(self, current_price: float):
    """ประเมิน snapshots เก่า — เรียกทุก cycle"""
    now = datetime.now(timezone.utc)

    for snap in self._snapshots:
        if snap.get('ai_correct') is not None:
            continue

        snap_time = datetime.fromisoformat(snap['timestamp'])
        age_min = (now - snap_time).total_seconds() / 60
        snap_price = snap.get('price', 0)
        if snap_price <= 0:
            continue

        if age_min >= 5 and snap.get('price_after_5m') is None:
            snap['price_after_5m'] = current_price

        if age_min >= 60 and snap.get('price_after_1h') is None:
            snap['price_after_1h'] = current_price
            change_pct = ((current_price - snap_price) / snap_price) * 100
            ai_bias = snap.get('ai_bias')

            if ai_bias == 'BULLISH':
                snap['ai_correct'] = change_pct > 0.2
            elif ai_bias == 'BEARISH':
                snap['ai_correct'] = change_pct < -0.2
            else:
                snap['ai_correct'] = None

            # Missed opportunity?
            any_signal = snap.get('any_signal', False)
            ai_action = snap.get('ai_action')
            if not any_signal and ai_action == 'TRADE' and abs(change_pct) > 0.3:
                snap['missed_opportunity'] = True
            else:
                snap['missed_opportunity'] = False
```

#### 3 ประโยชน์

```
1. AI Accuracy เร็วขึ้น:
   288 snapshots/วัน → วัด accuracy ได้ใน 1 วัน (ไม่ต้องรอ 1 สัปดาห์)

2. Missed Opportunities:
   AI TRADE + price +0.5%+ แต่ bot block → gate เข้มเกินไป?
   → ปรับ threshold ตามข้อมูลจริง

3. Correct Rejections:
   AI WAIT + bot ไม่เทรด + ราคาลง → bot ถูก!
   → ยืนยันว่า gate ทำงานดี
```

#### Terminal Footer (ไม่มี signal):

```
──────────────────────────────────────────────────
  📨 No signals (all modes blocked)
  🤖 AI: BULLISH 75% TRADE ← AI อยากให้เทรด
  📊 Missed: 3/50 cycles (AI TRADE + price moved +0.5%+)
  🎯 AI Accuracy: 62% (150 snapshots)
  ⏱️  Cycle: 1.2s
══════════════════════════════════════════════════
```

---

### ข้อมูลที่ได้หลัง 1 สัปดาห์

```
ai_trade_log.jsonl:
  100+ trades with:
  - AI aligned / conflict
  - WIN / LOSS / BE
  - PnL

วิเคราะห์:
  AI aligned trades:  win rate = ?%
  AI conflict trades: win rate = ?%

  ถ้า aligned 60%+ vs conflict 35%:
  → AI conflict prediction ถูก! → Phase 2: score -1 สำหรับ conflict

  ถ้า aligned 50% ≈ conflict 50%:
  → AI ไม่มีประโยชน์ → ปรับ prompt/model ก่อน
```

---

## Phase 2: Score Integration (เมื่อข้อมูลพอ)

### เงื่อนไขเข้า Phase 2

```
✅ ai_trade_log ≥ 100 trades (closed, not pending)
✅ AI aligned win rate ≥ 55%
✅ AI conflict win rate ≤ 40%
✅ Gap: aligned - conflict ≥ 15% (มี edge จริง)
```

### Score Adjustment

```python
# Phase 2: Soft +1/-1

if ai_aligned and ai_result['action'] == 'TRADE':
    score += 1; breakdown['ai_aligned'] = 1
elif not ai_aligned and ai_result['confidence'] >= 65:
    score -= 1; breakdown['ai_conflict'] = -1
```

---

## Phase 3: Full Integration (เป้าหมาย)

### เงื่อนไขเข้า Phase 3

```
✅ Phase 2 ทำงาน 2+ สัปดาห์
✅ AI contribution ทำให้ win rate เพิ่ม ≥ 3%
```

### Score Matrix Phase 3 (DeepSeek V3 ตัวเดียว)

```
AI TRADE + aligned + confidence ≥ 80%:  +2
AI TRADE + aligned + confidence 60-79%: +1
AI WAIT:                                -1
AI CAUTION:                             -2
AI TRADE + conflict + confidence ≥ 70%: -2
```

---

## Timeline

```
Week 1-2: Phase 1.5
  → Alert + Conflict tracking
  → สะสม 100+ trades

Week 3:   ประเมินข้อมูล
  → aligned vs conflict win rate
  → ถ้ามี edge → Phase 2
  → ถ้าไม่มี → ปรับ prompt/model

Week 4-5: Phase 2
  → Score +1/-1
  → วัด performance change

Week 6+:  Phase 3 (ถ้า Phase 2 สำเร็จ)
  → 2-Tier AI
  → Score +2/-2
```
