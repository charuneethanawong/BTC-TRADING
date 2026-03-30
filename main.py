from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
from google import genai
import json
import os
import asyncio

from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ==========================================
# ⚙️ CONFIGURATION (ตั้งค่าระบบ)
# ==========================================
# 1. รับ API Key จากไฟล์ .env
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# 2. ระบุที่อยู่โฟลเดอร์ Files ของ MT5 (รับจาก .env หรือใช้ค่าเริ่มต้น)
# Path นี้สำคัญมากสำหรับการส่งสัญญาณแบบ File-based
default_mt5_path = str(Path.home() / "AppData/Roaming/MetaQuotes/Terminal/Common/Files")
COMMON_FILES_DIR = os.getenv("MT5_COMMON_DIR", default_mt5_path)

# 3. ตั้งค่า Timeframe สำหรับดึงแท่งเทียนไปให้ AI วิเคราะห์
TIMEFRAME = mt5.TIMEFRAME_M5

# ==========================================
# 🚀 SERVER SETUP
# ==========================================
app = FastAPI(title="SMC Sniper Pro - API Backend")

# อนุญาตให้ Web UI ทุกที่สามารถยิง API เข้ามาขอข้อมูลได้ (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Gemini Client
client = genai.Client(api_key=GEMINI_API_KEY)

@app.on_event("startup")
def start_server():
    if not mt5.initialize():
        print(f"❌ MT5 Initialization failed: {mt5.last_error()}")
    else:
        print("✅ เชื่อมต่อ MT5 พร้อมสำหรับทำเว็บแล้ว!")

@app.on_event("shutdown")
def stop_server():
    mt5.shutdown()
    print("🛑 ตัดการเชื่อมต่อ MT5")

# ==========================================
# 📡 ENDPOINT 1: ดึงยอดเงินรวมของพอร์ต (Account Info)
# ==========================================
@app.get("/api/account")
def get_account_info():
    account = mt5.account_info()
    if account is not None:
        return {
            "balance": account.balance,
            "equity": account.equity,
            "profit": account.profit
        }
    return {"error": "ไม่สามารถดึงข้อมูลบัญชีจาก MT5 ได้"}

# ==========================================
# 📡 ENDPOINT 2: ค้นหาคู่เงินทั้งหมดที่มีไฟล์ JSON จาก EA
# ==========================================
@app.get("/api/symbols")
def get_available_symbols():
    symbols = []
    if os.path.exists(COMMON_FILES_DIR):
        for filename in os.listdir(COMMON_FILES_DIR):
            if filename.startswith("SMC_Sniper_") and filename.endswith(".json"):
                sym = filename.replace("SMC_Sniper_", "").replace(".json", "")
                symbols.append(sym)
    return {"symbols": symbols}

# ==========================================
# 📡 ENDPOINT 3: ดึงข้อมูลสถานะ EA ตามชื่อคู่เงิน
# ==========================================
@app.get("/api/ea_status/{symbol}")
def get_ea_status(symbol: str):
    file_path = os.path.join(COMMON_FILES_DIR, f"SMC_Sniper_{symbol}.json")
    
    if not os.path.exists(file_path):
        return {"error": f"ยังไม่พบข้อมูลของ {symbol}"}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            ea_data = json.load(f)
        return ea_data
    except Exception as e:
        return {"error": f"อ่านไฟล์ไม่ได้: {str(e)}"}

# ==========================================
# 📊 ENDPOINT 4: ดึงประวัติการเทรดและ PnL สรุปผล
# ==========================================
@app.get("/api/trade_history")
def get_trade_history(days: int = 7):
    """ดึงประวัติกำไรขาดทุนย้อนหลังจาก MT5 โดยตรง"""
    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)
    
    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None or len(deals) == 0:
        return {"total_profit": 0, "history": []}

    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    # เอาเฉพาะ deal ที่เป็นการปิดออเดอร์ (entry = 1)
    df_closed = df[df['entry'] == 1].copy()
    
    if df_closed.empty:
        return {"total_profit": 0, "history": []}

    history_data = []
    for _, row in df_closed.iterrows():
        history_data.append({
            "ticket": row['position_id'],
            "symbol": row['symbol'],
            "time": datetime.fromtimestamp(row['time']).strftime('%Y-%m-%d %H:%M'),
            "type": "BUY" if row['type'] == 1 else "SELL", # สลับกันตอนปิดออเดอร์
            "volume": row['volume'],
            "profit": row['profit'],
            "comment": row['comment']
        })

    return {
        "period_days": days,
        "total_profit": round(df_closed['profit'].sum(), 2),
        "total_trades": len(df_closed),
        "history": history_data[::-1] # กลับด้านให้รายการล่าสุดอยู่บนสุด
    }

# ==========================================
# 🧠 ENDPOINT 5: Gemini AI Market Analysis
# ==========================================
@app.get("/api/ai_analysis/{symbol}")
async def get_ai_analysis(symbol: str):
    """ให้ Gemini วิเคราะห์สถานการณ์ตลาดปัจจุบันของคู่เงินนั้นๆ"""
    
    # 1. อ่าน State ล่าสุดจาก EA
    file_path = os.path.join(COMMON_FILES_DIR, f"SMC_Sniper_{symbol}.json")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูล JSON ของคู่เงินนี้")
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            ea_data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail="ไม่สามารถอ่านไฟล์ JSON ได้")

    # 2. ดึงกราฟ 3 แท่งล่าสุดจาก MT5 เพื่อให้ AI เห็นราคา
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, 3)
    if rates is None:
        raise HTTPException(status_code=500, detail="ดึงข้อมูลแท่งเทียนไม่ได้")
    
    df_rates = pd.DataFrame(rates)
    df_rates['time'] = pd.to_datetime(df_rates['time'], unit='s')
    market_context = df_rates[['time', 'open', 'high', 'low', 'close']].to_string()

    # 3. สร้าง Prompt คุยกับ Gemini (นำโครงสร้าง SMC ส่งไปด้วย)
    prompt = f"""
    คุณคือนักเทรดมืออาชีพสาย SMC (Smart Money Concepts)
    นี่คือข้อมูลปัจจุบันของระบบเทรด EA บนคู่เงิน {symbol}:
    
    [สถานะระบบ EA]
    - สภาวะตลาด (Market Trend): {ea_data.get('market_trend', 'N/A')}
    - Bullish MSS เกิดขึ้นหรือไม่: {ea_data.get('mss_bullish', False)}
    - Bearish MSS เกิดขึ้นหรือไม่: {ea_data.get('mss_bearish', False)}
    
    [ข้อมูลราคา 3 แท่งเทียนล่าสุด]
    {market_context}
    
    โปรดวิเคราะห์สภาวะตลาดแบบมืออาชีพ สรุปสั้นๆ ไม่เกิน 3-4 บรรทัด และประเมิน Trade Probability Score (0-100%) ว่าช่วงเวลานี้เหมาะแก่การหาจังหวะเทรดมากน้อยแค่ไหน
    ตอบเป็นภาษาไทยแบบกระชับ ชัดเจน
    """

    try:
        # ส่งไปให้ Gemini วิเคราะห์ (ใช้ asyncio.to_thread เพื่อไม่ให้ API ค้าง)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        return {"symbol": symbol, "ai_analysis": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# 📉 ENDPOINT 6: Market Status (Spread & News)
# ==========================================
@app.get("/api/market_status/{symbol}")
def get_market_status(symbol: str):
    """ดึงค่า Spread ปัจจุบันและข่าวนับถอยหลัง (Mockup News)"""
    info = mt5.symbol_info(symbol)
    if info is None:
        return {"spread": 0, "news_countdown": "N/A"}
    
    return {
        "spread": info.spread,
        "news_countdown": "02:45:00 (FOMC)" # ในอนาคตสามารถเชื่อม API ข่าวจริงได้
    }

# ==========================================
# 🚨 ENDPOINT 7: Emergency Close All
# ==========================================
@app.post("/api/close_all/{symbol}")
def close_all_positions(symbol: str):
    """ปิดทุกออเดอร์ของคู่เงินนี้ทันที"""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None or len(positions) == 0:
        return {"message": "No open positions to close."}

    count = 0
    for pos in positions:
        # สร้างคำสั่งปิด (ตรงข้ามกับสถานะที่ถืออยู่)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": mt5.symbol_info_tick(symbol).bid if pos.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).ask,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "Emergency Close via Web UI",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            count += 1
            
    return {"message": f"Successfully closed {count} positions for {symbol}"}