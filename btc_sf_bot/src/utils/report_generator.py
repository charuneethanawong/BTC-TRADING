import pandas as pd
import json
import os
import asyncio
from datetime import datetime, timezone
import sys

# เพิ่ม Path เพื่อนำเข้าโมดูลจากในโปรเจกต์
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from src.execution.telegram_alert import TelegramAlert
from src.utils.config import load_config

async def generate_and_send_report():
    config = load_config()
    trade_file = "D:/CODING WORKS/SMC_AI_Project/btc_sf_bot/data/trades/all_trades.csv"
    
    report_msg = "🏛️ *SMC AI v4.0 DAILY PERFORMANCE*\n"
    report_msg += f"📅 Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
    report_msg += "------------------------------------------\n"

    if os.path.exists(trade_file):
        try:
            df = pd.read_csv(trade_file)
            if not df.empty:
                total_trades = len(df)
                wins = len(df[df['pnl_usd'] > 0])
                total_pnl = df['pnl_usd'].sum()
                win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
                
                report_msg += f"💰 *Net PnL:* `${total_pnl:,.2f}`\n"
                report_msg += f"📈 *Total Trades:* `{total_trades}`\n"
                report_msg += f"🎯 *Win Rate:* `{win_rate:.1f}%`\n\n"
                
                report_msg += "🧩 *Breakdown by Mode:*\n"
                for p in ['LP', 'DB', 'DA']:
                    p_df = df[df['pattern_type'] == p]
                    if not p_df.empty:
                        p_win = len(p_df[p_df['pnl_usd'] > 0])
                        p_wr = (p_win / len(p_df)) * 100
                        report_msg += f"• *{p}:* `{len(p_df)}` trades | `{p_wr:.1f}% WR`\n"
                    else:
                        report_msg += f"• *{p}:* `0` trades\n"
            else:
                report_msg += "❌ No trades recorded for this period."
        except Exception as e:
            report_msg += f"⚠️ Error processing data: {str(e)}"
    else:
        report_msg += "❌ Trade history file not found."

    report_msg += "\n------------------------------------------\n"
    report_msg += "🚀 *Status:* Ready for next session"

    # ส่งเข้า Telegram
    if config.get('alerts', {}).get('telegram', {}).get('enabled', False):
        notifier = TelegramAlert(config)
        await notifier.send_message(report_msg)
        print("✅ Report sent to Telegram successfully.")
    else:
        print(report_msg)
        print("⚠️ Telegram alerts disabled in config.")

if __name__ == "__main__":
    asyncio.run(generate_and_send_report())
