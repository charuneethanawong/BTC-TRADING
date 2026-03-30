"""
Telegram Alert Module
"""
import os
import httpx
from typing import Dict, Optional
from datetime import datetime, timezone

from ..utils.logger import get_logger

logger = get_logger(__name__)


class TelegramAlert:
    """Telegram alert sender."""
    
    def __init__(self, bot_token: str = None, chat_id: str = None):
        """
        Initialize Telegram alert.
        
        Args:
            bot_token: Telegram bot token
            chat_id: Telegram chat ID
        """
        self.bot_token = bot_token or os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID')
        
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if self.enabled:
            logger.info("Telegram alerts enabled and connected")
        else:
            logger.warning("Telegram alerts disabled (no token/chat_id)")
    
    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send message to Telegram.
        
        Args:
            message: Message text
            parse_mode: Parse mode (HTML or Markdown)
        
        Returns:
            True if successful
        """
        if not self.enabled:
            logger.debug(f"Telegram disabled, message: {message}")
            return False
        
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data, timeout=10.0)
                
                if response.status_code == 200:
                    logger.debug(f"Telegram message sent: {message[:50]}...")
                    return True
                else:
                    logger.error(f"Telegram error: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False
    
    async def send_signal_alert(self, signal: Dict) -> bool:
        """
        Send signal alert.

        Args:
            signal: Signal dictionary

        Returns:
            True if successful
        """
        direction = signal.get('direction', 'UNKNOWN')
        price = signal.get('entry_price', 0)
        sl = signal.get('stop_loss', 0)
        tp = signal.get('take_profit', 0)       # TP2 (actual TP)
        tp1 = signal.get('tp1_level', 0)         # TP1 (BE trigger) — v6.1
        tp2 = signal.get('tp2_level', tp)       # TP2 (actual TP) — v6.1
        confidence = signal.get('confidence', 0)
        reason = signal.get('reason', '')
        htf_trend = signal.get('regime', 'NEUTRAL')
        structure = signal.get('structure', 'RANGE')
        score = signal.get('score', 0)
        mode = signal.get('mode', 'UNKNOWN')

        # Calculate RR Ratio
        rr_ratio = 0
        sl_dist = abs(price - sl)
        if sl_dist > 0:
            rr_ratio = abs(tp - price) / sl_dist

        emoji = "🟢" if direction == "LONG" else "🔴"
        # v6.1: htf_trend now = regime (TRENDING/RANGING/VOLATILE/DEAD)
        trend_emoji = "📈" if htf_trend == "TRENDING" else "📉" if htf_trend == "VOLATILE" else "↔️"

        # Format reason - replace dots with newlines for better readability
        formatted_reason = reason.replace('.', '\n• ')
        if formatted_reason:
            formatted_reason = "• " + formatted_reason

        # v6.1: Mode comes from signal['mode'] field (IPA or IOF)
        mode_str = "NORMAL"
        if mode == "IPA":
            mode_str = "📊 IPA (Price Action)"
        elif mode == "IOF":
            mode_str = "⚡ IOF (Order Flow)"

        # v6.1: TP1 (BE trigger) & TP2 (actual TP)
        tp1_str = f"${tp1:,.2f}" if tp1 else "N/A"
        tp2_str = f"${tp2:,.2f}" if tp2 else f"${tp:,.2f}"

        message = f"""
{emoji} <b>NEW SIGNAL: {direction}</b>
🏷️ <b>Mode:</b> {mode_str}

💰 <b>Entry:</b> ${price:,.2f}
🛡️ <b>SL:</b> ${sl:,.2f}
⚡ <b>TP1 (BE):</b> {tp1_str}
🎯 <b>TP2:</b> {tp2_str}
⚖️ <b>RR:</b> 1:{rr_ratio:.1f}

📊 <b>ANALYSIS</b>
 🔥 <b>Confidence:</b> {confidence}%
 {trend_emoji} <b>Regime:</b> {htf_trend}
 🏗️ <b>Structure:</b> {structure}

 📝 <b>FULL REASON:</b>
{formatted_reason}
        """

        return await self.send_message(message)
    
    async def send_trade_alert(
        self,
        action: str,
        symbol: str,
        price: float,
        lot_size: float,
        sl: float = None,
        tp: float = None
    ) -> bool:
        """
        Send trade execution alert.
        
        Args:
            action: BUY or SELL
            symbol: Trading symbol
            price: Entry price
            lot_size: Lot size
            sl: Stop loss
            tp: Take profit
        
        Returns:
            True if successful
        """
        emoji = "✅" if action == "BUY" else "❌"
        
        sl_text = f"${sl:,.2f}" if sl else "N/A"
        tp_text = f"${tp:,.2f}" if tp else "N/A"
        
        message = f"""
{emoji} <b>TRADE EXECUTED</b>

<b>Action:</b> {action}
<b>Symbol:</b> {symbol}
<b>Price:</b> ${price:,.2f}
<b>Lot:</b> {lot_size}
<b>SL:</b> {sl_text}
<b>TP:</b> {tp_text}
        """
        
        return await self.send_message(message)
    
    async def send_tp_alert(
        self,
        action: str,
        symbol: str,
        tp_level: float,
        profit_r: float
    ) -> bool:
        """
        Send take profit alert.
        
        Args:
            action: BUY or SELL
            symbol: Trading symbol
            tp_level: TP level hit
            profit_r: Profit in R units
        
        Returns:
            True if successful
        """
        message = f"""
🎯 <b>TAKE PROFIT HIT</b>

<b>Symbol:</b> {symbol}
<b>TP Level:</b> ${tp_level:,.2f}
<b>Profit:</b> +{profit_r:.1f}R
        """
        
        return await self.send_message(message)
    
    async def send_sl_alert(
        self,
        action: str,
        symbol: str,
        sl_level: float,
        loss_r: float
    ) -> bool:
        """
        Send stop loss alert.
        
        Args:
            action: BUY or SELL
            symbol: Trading symbol
            sl_level: SL level hit
            loss_r: Loss in R units
        
        Returns:
            True if successful
        """
        message = f"""
🛑 <b>STOP LOSS HIT</b>

<b>Symbol:</b> {symbol}
<b>SL Level:</b> ${sl_level:,.2f}
<b>Loss:</b> {loss_r:.1f}R
        """
        
        return await self.send_message(message)
    
    async def send_error_alert(self, error: str) -> bool:
        """
        Send error alert.
        
        Args:
            error: Error message
        
        Returns:
            True if successful
        """
        message = f"""
⚠️ <b>ERROR</b>

<b>Error:</b> {error}
<b>Time:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
        """
        
        return await self.send_message(message)
    
    async def send_daily_summary(
        self,
        total_trades: int,
        wins: int,
        losses: int,
        profit: float,
        profit_r: float,
        trades: list = None
    ) -> bool:
        """
        Send daily summary with optional trade list.
        """
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        trade_list_str = ""
        if trades:
            trade_list_str = "\n<b>Trades Detail:</b>\n"
            for t in trades:
                icon = "✅" if t['pnl'] > 0 else "❌"
                trade_list_str += f"{icon} {t['direction']} {t['symbol']}: {t['pnl_pct']:.2f}% (${t['pnl']:.2f})\n"

        message = f"""
📊 <b>DAILY SUMMARY</b>

<b>Total Trades:</b> {total_trades}
<b>Wins:</b> {wins}
<b>Losses:</b> {losses}
<b>Win Rate:</b> {win_rate:.1f}%

<b>Profit:</b> ${profit:,.2f}
<b>Profit:</b> {profit_r:.1f}R
{trade_list_str}
        """
        
        return await self.send_message(message)
    
    async def send_position_alert(
        self,
        direction: str,
        entry: float,
        current: float,
        sl: float,
        tp: float,
        unrealized_pnl: float
    ) -> bool:
        """
        Send position update alert.
        
        Args:
            direction: LONG or SHORT
            entry: Entry price
            current: Current price
            sl: Stop loss
            tp: Take profit
            unrealized_pnl: Unrealized P&L
        
        Returns:
            True if successful
        """
        pnl_emoji = "🟢" if unrealized_pnl >= 0 else "🔴"
        
        message = f"""
📈 <b>POSITION UPDATE</b>

<b>Direction:</b> {direction}
<b>Entry:</b> ${entry:,.2f}
<b>Current:</b> ${current:,.2f}
<b>SL:</b> ${sl:,.2f}
<b>TP:</b> ${tp:,.2f}

{pnl_emoji} <b>Unrealized P&L:</b> ${unrealized_pnl:,.2f}
        """
        
        return await self.send_message(message)
