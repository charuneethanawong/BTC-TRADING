# 🏛️ BTC Smart Flow v4.0 - Institutional Grade AI Trading

ระบบเทรดอัจฉริยะที่ใช้หลักการ Smart Money Concepts (SMC) ร่วมกับข้อมูลพฤติกรรมคำสั่งซื้อขายจริง (Institutional Order Flow) เพื่อตามรอยเท้าของธนาคารและสถาบันการเงินรายใหญ่

---

## 🌟 ฟีเจอร์หลัก (Key Features)

### 1. 🧠 Institutional Flow Intelligence (IFI) Engine
- **LP Pattern (Liquidity Purge)**: ตรวจจับการกวาดสภาพคล่องพร้อมแรงดูดซับ (Absorption) และ OI Spike
- **DB Pattern (Defensive Block)**: ตรวจจับออเดอร์ซ่อน (Iceberg) และกำแพงสถาบันที่แท้จริงผ่าน Refill Rate
- **DA Pattern (Delta Absorption)**: วิเคราะห์จุดหมดแรง (Exhaustion) ของรายย่อยด้วยค่า Delta Efficiency Ratio (DER)

### 2. ⚡ การประมวลผลประสิทธิภาพสูง
- **Tick-Level Analysis**: วิเคราะห์ข้อมูลละเอียดยิบในระดับมิลลิวินาที
- **ZeroMQ Communication**: สื่อสารระหว่าง Python (สมองกล) และ MT5 (ตัวเปิดออเดอร์) ด้วยความไวสูง (< 100ms)
- **Institutional Confluence Score**: ระบบบวกคะแนนโบนัสให้กับสัญญาณที่มีความเชื่อมั่นสูงระดับสถาบัน

---

## 📂 โครงสร้างระบบ (System Structure)

- `/btc_sf_bot/src/`: หัวใจหลักของ Logic การวิเคราะห์
- `/btc_sf_bot/src/analysis/institutional_flow.py`: สมองกล IFI ชุดใหม่
- `/btc_sf_bot/mt5_ea/`: ชุดไฟล์สำหรับ MetaTrader 5 (EA และ JAson library)
- `/btc_sf_bot/config/`: ไฟล์ตั้งค่า `config.yaml` และ `.env`
- `/frontend/`: Dashboard สำหรับดูสถานะและการทำงานของบอทแบบ Real-time

---

## 🚀 วิธีการติดตั้งและใช้งาน (Installation & Usage)

### 1. เตรียมความพร้อม (Prerequisites)
- ติดตั้ง Python 3.10 ขึ้นไป
- ติดตั้ง MetaTrader 5
- บัญชี Binance (Futures) สำหรับดึงข้อมูล Flow Data

### 2. การติดตั้ง (Setup)
1. ติดตั้งไลบรารีที่จำเป็น:
   ```bash
   pip install -r requirements.txt
   ```
2. ตั้งค่า API Key ในไฟล์ `config/.env`
3. ติดตั้ง EA ใน MT5:
   - ก๊อปปี้ไฟล์ใน `mt5_ea/` ไปไว้ที่โฟลเดอร์ `MQL5/Experts/`
   - ก๊อปปี้ `libzmq.dll` ไปไว้ที่โฟลเดอร์ `MQL5/Libraries/`

### 3. เริ่มต้นใช้งาน (Running)
1. รันระบบวิเคราะห์หลัก (Python):
   ```bash
   python src/main.py
   ```
2. เปิด MT5 และ Attach EA เข้าที่กราฟ **BTC/USDT**
3. ตรวจสอบสถานะการเชื่อมต่อบน Dashboard ของบอท

---

## 🛡️ การบริหารความเสี่ยง (Risk Management)
- **Volatility-Adjusted Exposure**: ปรับ Lot size ตามความผันผวนของตลาด
- **Institutional Breakeven+**: เลื่อน SL มาหน้าทุนเร็วขึ้นเมื่อพบรอยเท้าสถาบันเข้าข้างเรา
- **News Filter**: ระบบหยุดเทรดอัตโนมัติก่อนข่าวแรง (CPI, FOMC) 15 นาที

---

## 📜 แผนงานสถาปัตยกรรม (Architecture Plan)
อ้างอิงรายละเอียดทางเทคนิคเชิงลึกได้ที่ไฟล์: `D:\CODING WORKS\SMC_AI_Project\btc_sf_bot\architecture_plan.md`

---

**สถานะโครงการ**: 🟢 **พร้อมส่งมอบ (READY FOR PRODUCTION)**  
**เวอร์ชัน**: 4.0 (Final Institutional Edition)
