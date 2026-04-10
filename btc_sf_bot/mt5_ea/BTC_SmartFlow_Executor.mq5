//+------------------------------------------------------------------+
//|                                BTC_SmartFlow_Executor.mq5        |
//|                                BTC Smart Flow Bot - EA           |
//+------------------------------------------------------------------+
#property copyright "BTC Smart Flow Bot"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>
#include "JAson.mqh"

//--- ZeroMQ 64-bit Imports
#import "libzmq.dll"
long zmq_ctx_new();
long zmq_socket(long ctx, int type);
int  zmq_connect(long socket, uchar &endpoint[]);
int  zmq_recv(long socket, uchar &buffer[], int len, int flags);
int  zmq_send(long socket, uchar &buffer[], int len, int flags);
int  zmq_bind(long socket, uchar &endpoint[]);
int  zmq_close(long socket);
int  zmq_ctx_destroy(long ctx);
int  zmq_setsockopt(long socket, int option, int &value, int optlen);
int  zmq_setsockopt(long socket, int option, uchar &value[], int optlen);
#import

#define ZMQ_SUB 2
#define ZMQ_SUBSCRIBE 6
#define ZMQ_DONTWAIT 1
#define ZMQ_RCVHWM 24

//--- Input Parameters
input group "=== Connection Settings ==="
input bool     EnableZMQ         = true;            // Enable ZeroMQ (Real-time)
input string   ZMQHost           = "tcp://127.0.0.1:5555"; // ZMQ Endpoint
input string   ZMQTopic          = "signal";        // ZMQ Subscriber Topic
input bool     EnableZMQPub      = true;            // Enable MT5 -> Python Data
input int      ZMQPubPort        = 5556;            // Port for MT5 to Publish
input bool     EnableDebug       = false;           // Enable Debug Logs (Experts Tab)

input group "=== Trading Settings ==="
input string   TradeSymbol       = "";              // Symbol (empty = use chart symbol)
input double   LotSize           = 0.01;            // Lot Size
input int      MagicNumber       = 123456;          // Magic Number
input int      Slippage          = 100;             // Max Slippage (points)
input ENUM_ORDER_TYPE_FILLING FillingMode = ORDER_FILLING_IOC; // Order Filling Mode

input group "=== Price Tolerance ==="
input bool     EnablePriceTolerance = true;         // Enable Price Tolerance Check
input int      PriceTolerancePoints = 50000;         // Max Price Difference (points) - Default improved for BTC
input bool     UseDynamicOffset     = true;         // Auto-calculate Price Basis (Binance vs Broker)

input group "=== Signal Settings ==="
input int      SignalExpireMinutes = 15;           // Signal Expire Time (minutes)
input string   SignalFilePath    = "";              // Signal File Path (empty = default)
input int      HeartbeatTimeoutSeconds = 30;       // Heartbeat Timeout (seconds) - v15.8: 90→30

input group "=== Risk Management ==="
input double   RiskPercent       = 0.5;             // Risk per Trade (%)
input double   MaxDailyLossPct   = 3.0;             // Max Daily Loss (%)
input double   MaxSpread         = 3000;            // Max Spread (points) - Default for BTC ($30)
// v11.x: Cleaned up redundant position limits
// Enforcement hierarchy:
//   1. Total: MaxPositions=8 (safety net)
//   2. Per Mode+Direction: MaxPositionsPerModeDir=1 (REAL enforcement - prevents duplicate orders)
//      → This means max 1 LONG and max 1 SHORT per mode = max 8 total (4 modes x 2 directions)
//   3. REMOVED: PerMode=1 was redundant (mode+LIMIT and mode+SHORT already enforce it)
//   4. REMOVED: PerPattern was same as PerMode (redundant)
input int      MaxPositions      = 8;               // Max Open Positions (Total) - safety net only
input int      MaxPositionsPerModeDir = 1;           // Max per Mode+Direction (IPA/IPAF)

input group "=== v25.0: Per Signal Type Limits (IOF/IOFF) ==="
input int      MaxMomentumPerDir  = 1;              // Max MOMENTUM per direction
input int      MaxAbsorptionPerDir = 1;             // Max ABSORPTION per direction
input int      MaxReversalPerDir  = 1;              // Max REVERSAL_OB/OS per direction
input int      MaxMeanRevertPerDir = 1;             // Max MEAN_REVERT per direction

input group "=== Execution Guards (v4.0 - Section 43) ==="
input double   PriceDistancePct       = 0.15;        // % Price distance to allow new trade (was time-based)
input int      HardLockSeconds        = 60;          // Minimum hard lock (60s) to prevent duplication - v12.5: increased from 30
input bool     EnableBreakevenUnlock  = true;        // Allow trade if previous position at BE/Profit
input double   ZoneResetPct          = 0.15;        // % Move required to reset a zone
input double   MinRRRatio            = 1.5;         // Minimum Risk/Reward Ratio

input group "=== Institutional Breakeven+ ==="
input bool     EnableBreakeven   = true;            // Enable Auto Breakeven
input double   BreakevenTriggerPct = 0.4;           // Profit % to trigger BE
// v9.2-S4: BreakevenBufferPoints deleted — buffer now ATR-based (0.3 × ATR, floor $30)

input group "=== Global News Filter ==="
input bool     EnableNewsFilter  = true;            // Enable EA-side News Filter
input int      AvoidNewsMinutes  = 15;              // Pause before/after news (mins)
input bool     CloseAllBeforeNews = false;          // Close all positions before news (10m)

input group "=== Dashboard ==="
input bool     ShowDashboard     = true;            // Show Dashboard on Chart
input color    DashboardBgColor  = clrDarkSlateGray;// Dashboard Background
input int      DashboardX        = 10;              // Dashboard X Position
input int      DashboardY        = 30;              // Dashboard Y Position

//--- Global Variables
CTrade         trade;
CPositionInfo  positionInfo;
CSymbolInfo    symbolInfo;

long           g_zmqCtx = 0;
long           g_zmqSocket = 0;
long           g_zmqPubSocket = 0; // New: Publisher socket
bool           g_zmqConnected = false;
bool           g_zmqPubBound = false;

bool           g_showDashboard = true;
string         g_symbol = "";
string         g_lastSignalId = "";
datetime       g_lastSignalTime = 0;

// Dashboard data
string         g_lastDirection = "---";
double         g_lastEntryPrice = 0;
double         g_lastSL = 0;
double         g_lastTP = 0;
int            g_lastScore = 0;
datetime       g_lastSignalDt = 0;
string         g_priceStatus = "Waiting...";

// v6.1: TP1 (BE trigger) & TP2 (actual TP) levels per position
double         g_tp1Level = 0;    // TP1 level: when reached, move SL to BE
double         g_tp2Level = 0;    // TP2 level: actual take profit
bool           g_beTriggered = false; // Whether BE has been triggered for current position
double         g_priceGap = 0;
int            g_totalTrades = 0;
int            g_winTrades = 0;
int            g_lossTrades = 0;

// Daily P&L Tracking
datetime       g_dailyStartTime = 0;
double         g_dailyStartBalance = 0;
double         g_dailyProfit = 0;
double         g_dailyWinAmount = 0;
double         g_dailyLossAmount = 0;
int            g_dailyWinCount = 0;
int            g_dailyLossCount = 0;

// Real-time Indicators
string         g_currRegime = "NEUTRAL";   // v6.1: Market Regime (TRENDING/RANGING/VOLATILE/DEAD)
string         g_currStructure = "NEUTRAL";
string         g_htfStructure = "NONE";
string         g_currZone = "NEUTRAL";
double         g_currDelta = 0;
datetime       g_lastIndicatorDt = 0;
datetime       g_lastFileTime = 0;
string         g_lastDataSource = "NONE";
double         g_priceBasis = 0;                    // Calculated difference between Signal and Broker price
datetime       g_lastHeartbeatTime = 0;             // Last time any message received
bool           g_systemSafe = true;                 // System health status
int            g_reconnectRetries = 0;              // Current retry count
const int      g_maxReconnectRetries = 999;          // v15.8: Max retries (unlimited - 5→999)
datetime       g_lastReconnectAttempt = 0;          // Time of last attempt
datetime       g_autoRecoverAttempt = 0;            // Time of last auto-recovery attempt

// News State
datetime       g_nextNewsTime = 0;                  // Next high-impact news time
datetime       g_lastNewsCloseTime = 0;             // Last time we closed positions due to news (cooldown)

// V-10: Trail Tighten State
bool            g_trailTightened = false;           // Whether trail is currently tightened
datetime       g_trailTightenTime = 0;             // When trail was tightened
const int      TIGHTEN_DURATION_MINS = 30;        // Max duration for tightened trail
string         g_nextNewsTitle = "";

// Section 23: Removed churn protection state variables
// - g_lastSignalSwitchTime
// - g_recentSwitchCount
bool           g_inNewsPause = false;               // Current news pause status
datetime       g_lastNewsLogTime = 0;               // Throttling news logs

// Execution Guard State
struct LastTradeRecord {
    string   signal_id;
    string   short_reason;
    string   direction;
    string   mode;
    double   entry_price;
    double   invalidation_price;
    datetime time;
    bool     is_reset;
    double   max_dist_from_sl;
    // v26.0: MFE/MAE — track how far price moved for/against
    double   max_favorable;     // max profit distance from entry ($)
    double   max_adverse;       // max loss distance from entry ($)
};

LastTradeRecord g_lastTrade;

// v3.2 Real-time Dashboard Info
int            g_phase1Score = 0;
int            g_phase2Score = 0;
string         g_currSession = "---";
string         g_currentPatternType = "UNKNOWN";  // C-02: Pattern type for BE/Trailing adjustments
int            g_riskTier = 0;
double         g_drawdown = 0;
bool           g_isAggressive = false;

// Pattern-specific BE Trigger thresholds (C-02)
// v4.0 Architecture Plan: Updated to support LP, DB, DA naming
// v4.9 M5 Upgrade: Replaced with Mode-based (IPA/IOF)
double         g_beTriggerLP = 0.5;    // LP: Liquidity Purge - aggressive BE
double         g_beTriggerDB = 0.3;    // DB: Defensive Block - quick BE
double         g_beTriggerDA = 0.4;    // DA: Delta Absorption - balanced BE

// v4.9 M5 Upgrade: Mode-specific parameters (IPA/IOF)
input group "=== Mode Control (v4.9 M5) ==="
input bool     EnableIPA           = true;         // Enable IPA Mode (Institutional Price Action)
input bool     EnableIOF           = true;          // Enable IOF Mode (Institutional Order Flow)

// IPA Trailing Settings (let trend run)
input double   IPA_BE_Trigger     = 0.5;         // % profit -> Breakeven
input double   IPA_Trail_Stage2  = 1.0;         // % profit -> Trail Stage2 (M5 Swing)
input double   IPA_Trail_Stage3  = 1.5;         // % profit -> Trail Stage3 (Tight)
input double   IPA_Lock_Stage4   = 2.0;         // % profit -> Lock 70%

// IOF Trailing Settings (take profit faster)
input double   IOF_BE_Trigger    = 0.3;         // % profit -> Breakeven
input double   IOF_Lock_Stage2   = 0.6;         // % profit -> Lock 40%
input double   IOF_Lock_Stage3   = 1.0;         // % profit -> Lock 60%
input double   IOF_Lock_Stage4   = 1.5;         // % profit -> Lock 80%

// RR Guard
input double   MinRR_IPA         = 1.0;         // Min RR for IPA mode (v6.0: was 1.8)
input double   MinRR_IOF         = 1.0;         // Min RR for IOF mode (v6.0: was 1.5)
input double   RRTolerance        = 0.92;         // 8% tolerance for spread/slippage

// Max positions per mode removed - redundant with per-mode+direction
// input int      MaxPositionsPerMode = 1;           // Max positions per mode (REMOVED v11.x)
// Per-mode+direction is the real enforcement (1 per mode+direction = max 2 per mode, 8 total)

// Legacy pattern names for backward compatibility
double         g_beTriggerOIMOM = 0.5;  // Legacy -> LP
double         g_beTriggerWALL = 0.3;    // Legacy -> DB
double         g_beTriggerCVDREV = 0.4;  // Legacy -> DA

// Pattern-specific Progressive Trailing stages (C-03)
// v4.0 Architecture Plan: LP=Run Trend, DB=Quick Exit, DA=Balanced
struct TrailingStage {
   double profitPct;
   double lockPct;
};
// LP Pattern: Run trend longer (Lock 70% at 1.8% profit)
TrailingStage g_trailingLP[4] = {{0.5, 0}, {0.8, 0.30}, {1.2, 0.50}, {1.8, 0.70}};
// DB Pattern: Quick profit (Lock 80% at 1.0% profit)
TrailingStage g_trailingDB[4] = {{0.3, 0}, {0.5, 0.40}, {0.7, 0.60}, {1.0, 0.80}};
// DA Pattern: Balanced precision (Lock 75% at 1.5% profit)
TrailingStage g_trailingDA[4] = {{0.4, 0}, {0.6, 0.25}, {0.9, 0.45}, {1.3, 0.65}};

// Legacy trailing stages
TrailingStage g_trailingOIMOM[4] = {{0.5, 0}, {0.8, 0.30}, {1.2, 0.50}, {1.8, 0.70}};
TrailingStage g_trailingWALL[4] = {{0.3, 0}, {0.5, 0.40}, {0.7, 0.60}, {1.0, 0.80}};
TrailingStage g_trailingCVDREV[4] = {{0.4, 0}, {0.6, 0.25}, {0.9, 0.45}, {1.3, 0.65}};

// Section 23: Removed g_lastTrend (was used for churn reset)

//+------------------------------------------------------------------+
//| Prototypes                                                        |
//+------------------------------------------------------------------+
void CheckForSignals();
void ProcessSignal(string content);
void InitAllTimeStats();
void ProcessIndicator(string content);
void UpdateDashboard();
void CreateDashboard();
void DeleteDashboard();
void CreateLabel(string name, string text, int x, int y, int fontsize, color clr);
int  CountPositions();
void ClosePositions(ENUM_POSITION_TYPE type);
bool OpenPosition(ENUM_ORDER_TYPE type, double lot, double price, double sl, double tp, string comment);
void ProcessTrailing(string content);
void CreateShowButton();
void ReportAccountInfo(); // New: Prototype for account info reporting
void ReportPositionInfo(); // New: Prototype for position info reporting
void CloseAllPositions(); // New: Close all positions on command
void InitZMQ();           // Forward declaration for reconnection
void CloseZMQ();          // Forward declaration for reconnection
// v16.7: ใช้ entryPrice จาก signal ไม่ใช่ Ask
double CalculateLotSize(double entryPrice, double slPrice); // Calculate lot based on risk
void ManageTrailingRisk(); // Handle Breakeven in real-time
void HandleNewsFilter();   // Handle news logic 
void ProcessNews(string content); // Parse news time from Python
void InitDailyTracking();         // Initialize daily P&L tracking
void CheckClosedPositions();      // Check closed positions and update stats

//+------------------------------------------------------------------+
//| Utility functions                                                 |
//+------------------------------------------------------------------+

// Extract string from JSON
string ExtractString(string json, string key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos == -1) return "";
   
   int start = StringFind(json, "\"", pos + StringLen(search));
   if(start == -1) return "";
   
   int end = StringFind(json, "\"", start + 1);
   if(end == -1) return "";
   
   string result = StringSubstr(json, start + 1, end - start - 1);
   return result;
}

// Extract double from JSON
double ExtractDouble(string json, string key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if(pos == -1) return 0;
   
   int start = pos + StringLen(search);
   while(start < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, start);
      if(ch != ' ' && ch != '\t' && ch != '\n' && ch != '\r' && ch != '\"' && ch != ':')
         break;
      start++;
   }
   
   int end = start;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if((ch < '0' || ch > '9') && ch != '.' && ch != '-' && ch != 'e' && ch != 'E' && ch != '+')
         break;
      end++;
   }
   
   string value = StringSubstr(json, start, end - start);
   return StringToDouble(value);
}

// Extract int from JSON
int ExtractInt(string json, string key)
{
   return (int)ExtractDouble(json, key);
}

// Count positions
int CountPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
            count++;
      }
   }
    return count;
}

// Count positions by mode and direction (v10.3: Per-mode direction check)
// v11.0: Count positions by mode and direction (supports 4 modes: IPA, IOF, IPAF, IOFF)
int CountPositionsByModeAndDirection(string mode, string direction)
{
   int count = 0;
   ENUM_POSITION_TYPE targetType = (direction == "LONG") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol &&
            positionInfo.Magic() == MagicNumber &&
            positionInfo.PositionType() == targetType)
         {
            string comment = positionInfo.Comment();
            // Comment starts with mode (e.g., "IPA", "IOF", "IPAF", "IOFF")
            string posMode = GetModeFromComment(comment);
            
            // v12.9: แสดง comment จริง + mode ที่ extract ได้
            Print("MODE_DEBUG: comment='", comment, "' -> posMode='", posMode, "' vs query='", mode, "' match=", (posMode == mode));
            
            if(posMode == mode)
               count++;
         }
      }
   }
   return count;
}

// Count positions by direction ("LONG" or "SHORT") - Legacy
int CountPositionsByDirection(string direction)
{
   int count = 0;
   ENUM_POSITION_TYPE targetType = (direction == "LONG") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && 
            positionInfo.Magic() == MagicNumber &&
            positionInfo.PositionType() == targetType)
         {
            count++;
         }
      }
   }
   return count;
}

// Count positions by pattern type (from comment)
int CountPositionsByPattern(string patternType)
{
   int count = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
         {
            string comment = positionInfo.Comment();
            // Pattern types: OI, WALL, CVD, ISF
            if(StringFind(comment, patternType) != -1)
               count++;
         }
      }
   }
   return count;
}

// v4.9 M5: Count positions by mode (IPA or IOF) - from comment field
// v12.7: Extract precise mode from comment to prevent IPA/IPA_FRVP collision
// v15.8: Map new comment formats (IPAF_ → IPA_FRVP, IOFF_ → IOF_FRVP)
// v72.1: Added REVERSAL handling
string GetModeFromComment(string comment)
{
   // v15.8: Check IPAF first (before IPA because IPAF starts with IPA)
   if(StringFind(comment, "IPAF_") == 0) return "IPA_FRVP";
   // Check IOFF first (before IOF because IOFF starts with IOF)
   if(StringFind(comment, "IOFF_") == 0) return "IOF_FRVP";
   // Check IPA after IPAF
   if(StringFind(comment, "IPA_") == 0) return "IPA";
   // Check IOF after IOFF
   if(StringFind(comment, "IOF_") == 0) return "IOF";
   
   // v72.1: Check REVERSAL modes
   if(StringFind(comment, "REVERSAL_OB_") == 0) return "REVERSAL_OB";
   if(StringFind(comment, "REVERSAL_OS_") == 0) return "REVERSAL_OS";
   
   // Fallback: legacy comments
   if(StringFind(comment, "IPA_FRVP") >= 0) return "IPA_FRVP";
   if(StringFind(comment, "IOF_FRVP") >= 0) return "IOF_FRVP";
   if(StringFind(comment, "IPA") >= 0) return "IPA";
   if(StringFind(comment, "IOF") >= 0) return "IOF";
   if(StringFind(comment, "REVERSAL") >= 0) return "REVERSAL";
   
   return "UNKNOWN";
}

// v25.0: Extract signal type from comment for independent position counting
// Comment: "IOF_MOMENTUM_SHORT_183944" → "MOMENTUM"
//          "IOFF_REVERSAL_OB_SHORT_203032" → "REVERSAL_OB"
//          "IPA_SHORT_160130" → "IPA"
string GetSignalTypeFromComment(string comment)
{
   string rest = "";
   if(StringFind(comment, "IOFF_") == 0) rest = StringSubstr(comment, 5);
   else if(StringFind(comment, "IPAF_") == 0) rest = StringSubstr(comment, 5);
   else if(StringFind(comment, "IOF_") == 0) rest = StringSubstr(comment, 4);
   else if(StringFind(comment, "IPA_") == 0) rest = StringSubstr(comment, 4);
   else return "UNKNOWN";

   if(StringFind(rest, "MOMENTUM_") == 0) return "MOMENTUM";
   if(StringFind(rest, "ABSORPTION_") == 0) return "ABSORPTION";
   if(StringFind(rest, "REVERSAL_OB_") == 0) return "REVERSAL_OB";
   if(StringFind(rest, "REVERSAL_OS_") == 0) return "REVERSAL_OS";
   if(StringFind(rest, "MEAN_REVERT_") == 0) return "MEAN_REVERT";
   return "IPA";
}

// v25.0: Count positions by signal type + direction (independent)
int CountPositionsBySignalTypeAndDir(string signalType, string direction)
{
   int count = 0;
   ENUM_POSITION_TYPE targetType = (direction == "LONG") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol &&
            positionInfo.Magic() == MagicNumber &&
            positionInfo.PositionType() == targetType)
         {
            string comment = positionInfo.Comment();
            string posType = GetSignalTypeFromComment(comment);
            if(posType == signalType)
               count++;
         }
      }
   }
   return count;
}

int CountPositionsByMode(string mode)
{
   int count = 0;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
         {
            string comment = positionInfo.Comment();
            // Mode is stored in comment (IPA_xxx or IOF_xxx)
            string posMode = GetModeFromComment(comment);
            if(posMode == mode)
               count++;
         }
      }
   }
   return count;
}

// Calculate daily P&L (from today's positions)
double CalculateDailyPnL()
{
   double dailyPnL = 0;
   datetime todayStart = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
         {
            if(positionInfo.Time() >= todayStart)
            {
               dailyPnL += positionInfo.Profit();
            }
         }
      }
   }
   return dailyPnL;
}

// Close positions by type
void ClosePositions(ENUM_POSITION_TYPE type)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && 
            positionInfo.Magic() == MagicNumber &&
            positionInfo.PositionType() == type)
         {
            trade.PositionClose(positionInfo.Ticket());
         }
      }
   }
}

// Open position with validation
bool OpenPosition(ENUM_ORDER_TYPE type, double lot, double price, double sl, double tp, string comment)
{
   // Check AutoTrading
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      Print("❌ ORDER FAILED: AutoTrading is disabled! Enable it in MT5 toolbar");
      return false;
   }
   
   double ask = symbolInfo.Ask();
   double bid = symbolInfo.Bid();
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   
   // Check spread
   double spread = (ask - bid) / symbolInfo.Point();
   if(spread > MaxSpread)
   {
      Print("❌ ORDER FAILED: Spread too high (", (int)spread, " pts > ", (int)MaxSpread, " pts)");
      return false;
   }
   
   // Volume Normalization
   double minLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   
   if(lot <= 0) lot = LotSize;
   
    // Round lot to step (use ceil if below minLot to ensure we don't get 0)
    if(lotStep > 0)
    {
       double steppedLot = MathFloor(lot / lotStep) * lotStep;
       // If stepped lot is 0 (or less than minLot), round up instead
       if(steppedLot < minLot)
          lot = MathCeil(lot / lotStep) * lotStep;
       else
          lot = steppedLot;
    }
    
    // Constraint check - ensure minimum lot
    if(lot < minLot) 
    {
       lot = minLot;
    }
    if(lot > maxLot) lot = maxLot;
   
   // Re-normalize to avoid floating point issues
   lot = NormalizeDouble(lot, 2); // Most brokers use 2 decimal places for lot
   if(lotStep == 0.1) lot = NormalizeDouble(lot, 1);
   if(lotStep == 1.0) lot = NormalizeDouble(lot, 0);
   
   // v26.0: Auto-enforce broker stop rules before sending order
   {
      long stopsLvl = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL);
      long freezeLvl = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
      double pt = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
      double brokerMin = MathMax(stopsLvl, freezeLvl) * pt;
      double spreadPrice = ask - bid;
      brokerMin = MathMax(brokerMin, spreadPrice * 2);

      double execPrice = (type == ORDER_TYPE_BUY) ? ask : bid;

      // Adjust SL if too close
      if(sl > 0 && MathAbs(execPrice - sl) < brokerMin)
      {
         double oldSL = sl;
         if(type == ORDER_TYPE_BUY)
            sl = execPrice - brokerMin;
         else
            sl = execPrice + brokerMin;
         sl = NormalizeDouble(sl, digits);
         Print("⚠️ SL adjusted: ", DoubleToString(oldSL, digits), " → ", DoubleToString(sl, digits),
               " (broker min: ", DoubleToString(brokerMin, 2), ")");
      }

      // Adjust TP if too close
      if(tp > 0 && MathAbs(execPrice - tp) < brokerMin)
      {
         double oldTP = tp;
         if(type == ORDER_TYPE_BUY)
            tp = execPrice + brokerMin;
         else
            tp = execPrice - brokerMin;
         tp = NormalizeDouble(tp, digits);
         Print("⚠️ TP adjusted: ", DoubleToString(oldTP, digits), " → ", DoubleToString(tp, digits),
               " (broker min: ", DoubleToString(brokerMin, 2), ")");
      }
   }

   bool result = false;
   if(type == ORDER_TYPE_BUY)
   {
      price = NormalizeDouble(ask, digits);
      result = trade.Buy(lot, g_symbol, price, NormalizeDouble(sl, digits), NormalizeDouble(tp, digits), comment);
   }
   else
   {
      price = NormalizeDouble(bid, digits);
      result = trade.Sell(lot, g_symbol, price, NormalizeDouble(sl, digits), NormalizeDouble(tp, digits), comment);
   }
   
   // Check result
   if(!result)
   {
      Print("❌ ORDER FAILED: ", trade.ResultRetcodeDescription(), " (Code: ", trade.ResultRetcode(), ")");
      Print("   Details: Type=", (type == ORDER_TYPE_BUY ? "BUY" : "SELL"), 
            " Lot=", DoubleToString(lot, 3), 
            " Price=", DoubleToString(price, digits),
            " SL=", DoubleToString(sl, digits), 
            " TP=", DoubleToString(tp, digits));
   }
   return result;
}

// Calculate lot based on account risk and SL distance
// v16.7: ใช้ entryPrice จาก signal ไม่ใช่ Ask (ป้องกัน lot ผิดเมื่อ Ask ใกล้ SL)
double CalculateLotSize(double entryPrice, double slPrice)
{
   double distance = MathAbs(entryPrice - slPrice);
   
   if(distance == 0) return LotSize;
   
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * (RiskPercent / 100.0);
   
   // Formula: Lot = Risk Amount / (Distance * TickValue)
   double tickValue = symbolInfo.TickValue();
   double tickSize = symbolInfo.TickSize();
   
   if(tickSize == 0 || tickValue == 0) return LotSize;
   
   double points = distance / tickSize;
   double lot = riskAmount / (points * tickValue);
   
   if(EnableDebug) 
      Print("⚖️ EA Lot Calc: Balance=", balance, " Risk=", RiskPercent, "% ($", riskAmount, ") SL_Dist=", distance, " -> Lot=", lot);
      
   return lot;
}

// Real-time Trailing SL & Breakeven (Progressive Trailing) - v6.1
// v6.1: TP1 price-based BE trigger (instead of profit%-based)
// When price reaches TP1 (BE trigger level), move SL to breakeven.
// Then continue with progressive locking stages as price approaches TP2.
void ManageTrailingRisk()
{
     if(!EnableBreakeven) return;
     
     string mode = g_currentPatternType;
     
     for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
        if(positionInfo.SelectByIndex(i))
        {
           if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
           {
              double entry = positionInfo.PriceOpen();
              double current = positionInfo.PriceCurrent();
              double sl = positionInfo.StopLoss();
              
              // v9.2 Section 1: ATR-based BE buffer (replace fixed points)
              double atr = iATR(g_symbol, PERIOD_M5, 14);
              double buffer = atr * 0.3;
              buffer = MathMax(buffer, 30.0); // floor $30
              int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
              buffer = NormalizeDouble(buffer, digits);
              
              // Calculate profit distance relative to TP2
              double tp2 = (g_tp2Level > 0) ? g_tp2Level : positionInfo.TakeProfit();
              double profitDist = (positionInfo.PositionType() == POSITION_TYPE_BUY)
                  ? (current - entry) : (entry - current);
              double tp2Dist = (positionInfo.PositionType() == POSITION_TYPE_BUY)
                  ? (tp2 - entry) : (entry - tp2);
              
              if(profitDist <= 0) continue; // No profit, skip
              
              // Calculate profit progress toward TP2 (0.0 to 1.0+)
              double progressToTP2 = (tp2Dist > 0) ? NormalizeDouble(profitDist / tp2Dist, 3) : 0;
              
              double targetSL = 0;
              string stage = "";
              double lockPct = 0;
              
              // === v9.2 Section 2: TP1 check FIRST, then progressive stages ===
              // Check if price has reached TP1 level (BE trigger)
              bool tp1Reached = false;
              if(g_tp1Level > 0)
              {
                 if(positionInfo.PositionType() == POSITION_TYPE_BUY)
                    tp1Reached = (current >= g_tp1Level);
                 else
                    tp1Reached = (current <= g_tp1Level);
              }
              
              // === TP1: BE trigger — checked FIRST (Section 2 fix) ===
              if(tp1Reached && !g_beTriggered)
              {
                 lockPct = 0;
                 stage = "STAGE0_BE"; // SL = entry + buffer
              }
              // === MODE-BASED PROGRESSIVE TRAILING (after BE triggered) ===
              else if(g_beTriggered)
              {
                 // BE already active — use progressive locking
                 if(mode == "IOF")
                 {
                    if(progressToTP2 >= 0.8)
                    { lockPct = 0.60; stage = "IOF_LOCK60%"; }
                    else if(progressToTP2 >= 0.6)
                    { lockPct = 0.40; stage = "IOF_LOCK40%"; }
                    else
                    { lockPct = 0; stage = "IOF_BE"; }
                 }
                 else // IPA
                 {
                    if(progressToTP2 >= 1.5)
                    { lockPct = 0.70; stage = "IPA_LOCK70%"; }
                    else if(progressToTP2 >= 1.2)
                    { lockPct = 0.50; stage = "IPA_LOCK50%"; }
                    else if(progressToTP2 >= 0.8)
                    { lockPct = 0.30; stage = "IPA_LOCK30%"; }
                    else
                    { lockPct = 0; stage = "IPA_BE"; }
                 }
              }
              else
              {
                 continue; // Not enough progress before TP1 reached
              }
              
              if(positionInfo.PositionType() == POSITION_TYPE_BUY)
              {
                 if(lockPct > 0)
                    targetSL = entry + (current - entry) * lockPct;
                 else
                    targetSL = entry + buffer;
                 
                 // Only move SL up, never down
                 if(targetSL <= sl) continue;
              }
              else // SELL
              {
                 if(lockPct > 0)
                    targetSL = entry - (entry - current) * lockPct;
                 else
                    targetSL = entry - buffer;
                 
                 // Only move SL down, never up
                 if(sl > 0 && targetSL >= sl) continue;
              }
              
               targetSL = NormalizeDouble(targetSL, digits);
               
               // v26.0: Auto-detect broker stop rules — no hardcoded values
               long stopsLevelPoints = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_STOPS_LEVEL);
               long freezeLevelPoints = SymbolInfoInteger(g_symbol, SYMBOL_TRADE_FREEZE_LEVEL);
               double pointSize = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
               double spread = SymbolInfoDouble(g_symbol, SYMBOL_ASK) - SymbolInfoDouble(g_symbol, SYMBOL_BID);

               // Broker minimum = max(StopsLevel, FreezeLevel) in price + spread buffer
               double brokerMinDist = MathMax(stopsLevelPoints, freezeLevelPoints) * pointSize;
               brokerMinDist = MathMax(brokerMinDist, spread * 2);  // at least 2x spread

               double newSLDist = MathAbs(targetSL - current);
               if(newSLDist < brokerMinDist)
               {
                  // SL ใกล้เกินไปตามกฏ broker → ข้ามไม่ modify
                  if(EnableDebug) Print("🛡️ TRAIL SKIP: SL dist ", DoubleToString(newSLDist, 2),
                     " < broker min ", DoubleToString(brokerMinDist, 2),
                     " (stops:", stopsLevelPoints, " freeze:", freezeLevelPoints, " spread:", DoubleToString(spread, 2), ")");
                  continue;
               }
               
                // v16.2: Cooldown - retry ไม่เกินทุก 5 วินาที
                static datetime lastTrailAttempt = 0;
                static int failCount = 0;
                
                if(failCount > 0 && TimeCurrent() - lastTrailAttempt < 5)
                   continue;  // cooldown 5s หลัง fail
                
                // v7.0 Bug#5 FIX: Check Modify return value BEFORE setting flag
               bool modifySuccess = trade.PositionModify(positionInfo.Ticket(), targetSL, positionInfo.TakeProfit());
               if(modifySuccess)
               {
                  if(tp1Reached && !g_beTriggered)
                     g_beTriggered = true;
                  
                   Print("🛡️ v9.2 TRAIL [", stage, "][", mode, "]: SL=", DoubleToString(targetSL, digits), 
                         " | Buf:", DoubleToString(buffer, digits),
                         " | TP1:", DoubleToString(g_tp1Level, digits),
                         " | TP2:", DoubleToString(g_tp2Level, digits),
                         " | Prog:", DoubleToString(progressToTP2*100, 0), "%");
                   
                   lastTrailAttempt = TimeCurrent();  // v16.2: Update cooldown timestamp
                   failCount = 0;  // v16.3: reset เมื่อสำเร็จ
               }
               else
               {
                  failCount++;
                  lastTrailAttempt = TimeCurrent();
                  if(failCount <= 3)  // v16.3: log แค่ 3 ครั้งแรก
                     Print("⚠️ v9.2 TRAIL FAILED [", stage, "]: ", trade.ResultRetcodeDescription(),
                           " (Code: ", trade.ResultRetcode(), ") — Will retry next tick");
               }
           }
        }
     }
}


// News Filter Decision Logic
void HandleNewsFilter()
{
   if(!EnableNewsFilter || g_nextNewsTime == 0) 
   {
      g_inNewsPause = false;
      return;
   }
   
   datetime now = TimeCurrent();
   long diffSeconds = (long)g_nextNewsTime - (long)now;
   int diffMins = (int)(diffSeconds / 60);
   
   // 1. Pause Entry (AvoidNewsMinutes)
   if(MathAbs(diffMins) <= AvoidNewsMinutes)
   {
      if(!g_inNewsPause)
      {
         Print("📰 NEWS GUARD: Entering pause zone for '", g_nextNewsTitle, "' (", diffMins, "m remaining)");
         g_inNewsPause = true;
      }
   }
   else if(diffMins < -AvoidNewsMinutes)
   {
      // News has passed
      if(g_inNewsPause) Print("📰 NEWS GUARD: News passed, resuming...");
      g_inNewsPause = false;
      g_nextNewsTime = 0; // Clear until next update
   }
   else
   {
      g_inNewsPause = false;
   }
   
// 2. Early Exit (CloseAllBeforeNews - 10m)
   // Fix: Add cooldown to prevent closing on every tick
   if(CloseAllBeforeNews && diffMins > 0 && diffMins <= 10 && CountPositions() > 0)
   {
      // Only close once per 5 minutes to prevent continuous closing
      if(TimeCurrent() - g_lastNewsCloseTime >= 300)
      {
         Print("📰 NEWS GUARD: 10 mins to news! Closing all positions for safety.");
         CloseAllPositions();
         g_lastNewsCloseTime = TimeCurrent();
      }
   }
}

// Parse News from Python
void ProcessNews(string content)
{
   CJAVal json;
   if(!json.Deserialize(content)) return;
   
   g_nextNewsTime = (datetime)json["time"].ToInt();
   g_nextNewsTitle = json["title"].ToStr();
   
   if(EnableDebug) 
   {
      datetime now = TimeCurrent();
      if(now - g_lastNewsLogTime >= 300) // 5 min cooldown
      {
         Print("📰 News Updated: '", g_nextNewsTitle, "' at ", TimeToString(g_nextNewsTime));
         g_lastNewsLogTime = now;
      }
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   trade.SetTypeFilling(FillingMode);
   
   g_showDashboard = ShowDashboard;
   g_symbol = TradeSymbol;
   if(g_symbol == "")
      g_symbol = _Symbol;
   
   if(!symbolInfo.Name(g_symbol))
   {
      Print("Failed to initialize symbol: ", g_symbol);
      return INIT_FAILED;
   }
   
   if(EnableZMQ)
   {
      if(!TerminalInfoInteger(TERMINAL_DLLS_ALLOWED))
      {
         Print("❌ ERROR: DLL imports are not allowed! Please go to Tools -> Options -> Expert Advisors and check 'Allow DLL imports'");
         Alert("❌ EA Error: DLL imports not allowed!");
         g_zmqConnected = false;
      }
      else
      {
         InitZMQ();
      }
   }
   
   if(g_showDashboard)
   {
      CreateDashboard();
      UpdateDashboard();
   }
   
   EventSetTimer(1); // Update dashboard every second
   
    Print("BTC Smart Flow Executor v2.0 Started");
     Print("Symbol: ", g_symbol);
     Print("ZMQ Enabled: ", EnableZMQ);
     
     InitDailyTracking();
     InitAllTimeStats();
     
     // v6.0: Reset g_lastTrade on startup to prevent stale cooldown
     g_lastTrade.entry_price = 0;
     g_lastTrade.invalidation_price = 0;
     g_lastTrade.direction = "";
     g_lastTrade.is_reset = true;
     
     // v6.1: Reset TP levels and BE state
     g_tp1Level = 0;
     g_tp2Level = 0;
     g_beTriggered = false;
     
     return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Initialize ZeroMQ                                                 |
//+------------------------------------------------------------------+
void InitZMQ()
{
   g_zmqCtx = zmq_ctx_new();
   if(g_zmqCtx == 0)
   {
      Print("❌ Failed to create ZMQ context. Error: ", _LastError);
      return;
   }
   
   g_zmqSocket = zmq_socket(g_zmqCtx, ZMQ_SUB);
   if(g_zmqSocket == 0)
   {
      Print("❌ Failed to create ZMQ socket");
      zmq_ctx_destroy(g_zmqCtx);
      g_zmqCtx = 0;
      return;
   }
   
   // Set HWM
   int hwm = 10;
   zmq_setsockopt(g_zmqSocket, ZMQ_RCVHWM, hwm, 4);
   
   // Connect
   uchar host_buffer[];
   StringToCharArray(ZMQHost, host_buffer);
   if(zmq_connect(g_zmqSocket, host_buffer) != 0)
   {
      Print("❌ Failed to connect to ZMQ: ", ZMQHost);
      CloseZMQ();
      return;
   }
   
   // Subscribe to all topics (standard way)
   uchar subscribe_all[]; 
   StringToCharArray("", subscribe_all); 
   int res = zmq_setsockopt(g_zmqSocket, ZMQ_SUBSCRIBE, subscribe_all, 0); 
   
   g_zmqConnected = true;
   g_reconnectRetries = 0; // Reset retries on success
   
   // v19.1: Connect สำเร็จ = system safe ทันที (ไม่ต้องรอ heartbeat)
   g_systemSafe = true;
   g_lastHeartbeatTime = TimeCurrent();
   Print("✅ ZeroMQ Connected — System SAFE (auto-recovery stopped)");
   
   // Initialize Publisher for Account Info
   if(EnableZMQPub)
   {
      g_zmqPubSocket = zmq_socket(g_zmqCtx, 1); // ZMQ_PUB = 1
      if(g_zmqPubSocket != 0)
      {
         string pubAddr = "tcp://*:" + IntegerToString(ZMQPubPort);
         uchar pub_buffer[];
         StringToCharArray(pubAddr, pub_buffer);
         if(zmq_bind(g_zmqPubSocket, pub_buffer) == 0)
         {
            g_zmqPubBound = true;
            Print("✅ ZeroMQ Publisher started on port ", ZMQPubPort);
         }
         else
         {
            Print("❌ Failed to bind ZeroMQ Publisher on port ", ZMQPubPort);
            zmq_close(g_zmqPubSocket);
            g_zmqPubSocket = 0;
          }
       }
    }
}

// v23.0: Send trade confirmation back to Python via ZMQ PUB + File Fallback
void SendTradeConfirm(string signalId, string status, double price, double profit, string mode)
{
    string json = "{";
    json += "\"signal_id\":\"" + signalId + "\",";
    json += "\"status\":\"" + status + "\",";
    json += "\"price\":" + DoubleToString(price, 2) + ",";
    json += "\"profit\":" + DoubleToString(profit, 2) + ",";
    json += "\"mode\":\"" + mode + "\",";
    // v26.0: MFE/MAE — max favorable/adverse excursion from entry
    json += "\"mfe\":" + DoubleToString(g_lastTrade.max_favorable, 2) + ",";
    json += "\"mae\":" + DoubleToString(g_lastTrade.max_adverse, 2);
    json += "}";

    // Try ZeroMQ first
    bool zmqSuccess = false;
    if(g_zmqPubBound && g_zmqPubSocket != 0)
    {
        string message = "trade_confirm " + json;
        uchar buffer[];
        StringToCharArray(message, buffer, 0, WHOLE_ARRAY, CP_UTF8);
        int res = zmq_send(g_zmqPubSocket, buffer, ArraySize(buffer) - 1, ZMQ_DONTWAIT);
        zmqSuccess = (res >= 0);
        
        if(zmqSuccess)
            Print("📨 Trade Confirm sent via ZMQ: ", signalId, " -> ", status);
    }
    
    // v26.1: Fallback to file if ZeroMQ fails
    if(!zmqSuccess)
    {
        string filename = "trade_confirm.json";
        int handle = FileOpen(filename, FILE_WRITE|FILE_SHARE_READ|FILE_SHARE_WRITE);
        if(handle != INVALID_HANDLE)
        {
            FileWriteString(handle, json);
            FileClose(handle);
            Print("📨 Trade Confirm sent via File: ", signalId, " -> ", status, " | (ZMQ failed)");
        }
        else
        {
            Print("❌ Failed to send trade confirm: ", signalId, " -> ", status);
        }
    }
}

//+------------------------------------------------------------------+
//| Close ZeroMQ                                                      |
//+------------------------------------------------------------------+
void CloseZMQ()
{
   if(g_zmqSocket != 0) zmq_close(g_zmqSocket);
   if(g_zmqPubSocket != 0) zmq_close(g_zmqPubSocket);
   if(g_zmqCtx != 0) zmq_ctx_destroy(g_zmqCtx);
   
   g_zmqSocket = 0;
   g_zmqPubSocket = 0;
   g_zmqCtx = 0;
   g_zmqConnected = false;
   g_zmqPubBound = false;
   Print("ZeroMQ Closed");
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   
   if(EnableZMQ)
   {
      CloseZMQ();
   }
   
   if(g_showDashboard)
   {
      DeleteDashboard();
   }
}

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   if(!symbolInfo.RefreshRates()) return;
   
   // Real-time Risk & News Management
   HandleNewsFilter();
   if(EnableBreakeven) ManageTrailingRisk();
   
    // v6.1: Reset BE state if no positions exist
    if(CountPositions() == 0)
    {
       if(g_tp1Level != 0 || g_beTriggered)
       {
          g_tp1Level = 0;
          g_tp2Level = 0;
          g_beTriggered = false;
       }
    }
    
    // V-10: Check if tightened trail should be reset after 30 minutes
    if(g_trailTightened)
   {
      datetime now = TimeCurrent();
      if((now - g_trailTightenTime) >= TIGHTEN_DURATION_MINS * 60)
      {
          if(EnableDebug) Print("🔓 TRAIL RESET: Tightened trail expired after ", TIGHTEN_DURATION_MINS, " minutes - restoring normal trail");
         g_trailTightened = false;
         g_trailTightenTime = 0;
      }
   }
   
   // --- Zone Reset Tracking ---
   if(g_lastTrade.invalidation_price > 0 && !g_lastTrade.is_reset)
   {
      double currentPrice = symbolInfo.Bid();
      double dist = MathAbs(currentPrice - g_lastTrade.invalidation_price);
      
      if(dist > g_lastTrade.max_dist_from_sl)
         g_lastTrade.max_dist_from_sl = dist;
         
      double resetThreshold = g_lastTrade.invalidation_price * (ZoneResetPct / 100.0);
      if(g_lastTrade.max_dist_from_sl > resetThreshold)
      {
         g_lastTrade.is_reset = true;
         if(EnableDebug) Print("🛡️ [GUARD] Zone Reset confirmed! Max move from SL: ", DoubleToString(g_lastTrade.max_dist_from_sl, 2));
      }
   }
   
   // v26.0: MFE/MAE tracking — how far price moved for/against entry
   if(g_lastTrade.entry_price > 0 && CountPositions() > 0)
   {
      double curP = symbolInfo.Bid();
      double moveFromEntry = 0;
      if(g_lastTrade.direction == "LONG")
         moveFromEntry = curP - g_lastTrade.entry_price;
      else
         moveFromEntry = g_lastTrade.entry_price - curP;

      if(moveFromEntry > g_lastTrade.max_favorable)
         g_lastTrade.max_favorable = moveFromEntry;
      if(moveFromEntry < 0 && MathAbs(moveFromEntry) > g_lastTrade.max_adverse)
         g_lastTrade.max_adverse = MathAbs(moveFromEntry);
   }

   // We still check on tick for fastest entry
   CheckForSignals();
   
   if(g_showDashboard)
   {
      UpdateDashboard();
   }
}

//+------------------------------------------------------------------+
//| Timer function                                                    |
//+------------------------------------------------------------------+
void OnTimer()
{
    // News monitoring 
    HandleNewsFilter();
    
    // Daily P&L tracking
    InitDailyTracking();
    CheckClosedPositions();
    
    // CRITICAL: Check for signals on timer
    if(EnableZMQ || SignalFilePath != "")
    {
       CheckForSignals();
    }

   // Send account and position info periodically
   if(g_zmqPubBound && (TimeCurrent() % 5 == 0))
   {
      ReportAccountInfo();
      ReportPositionInfo();
   }

   if(g_showDashboard)
   {
      UpdateDashboard();
   }

    // Reconnection Logic
    if(EnableZMQ && !g_systemSafe && g_reconnectRetries < g_maxReconnectRetries)
    {
       datetime now = TimeCurrent();
       if(now - g_lastReconnectAttempt >= 10) // Retry every 10 seconds
       {
          // Only log every 3rd attempt to reduce noise
          if(g_reconnectRetries % 3 == 0)
             Print("🔄 Attempting to reconnect ZeroMQ... (Try ", g_reconnectRetries + 1, "/", g_maxReconnectRetries, ")");
          g_lastReconnectAttempt = now;
          g_reconnectRetries++;
          CloseZMQ();
          InitZMQ();
       }
    }
}

//+------------------------------------------------------------------+
//| Chart event function                                              |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      if(sparam == "DASHBOARD_HIDE")
      {
         g_showDashboard = false;
         DeleteDashboard();
         CreateShowButton();
         ChartRedraw(0);
      }
      else if(sparam == "DASHBOARD_SHOW")
      {
         g_showDashboard = true;
         ObjectDelete(0, "DASHBOARD_SHOW");
         CreateDashboard();
         UpdateDashboard();
         ChartRedraw(0);
      }
   }
}

//+------------------------------------------------------------------+
//| Create small show button                                          |
//+------------------------------------------------------------------+
void CreateShowButton()
{
   int x = DashboardX;
   int y = DashboardY;
   ObjectCreate(0, "DASHBOARD_SHOW", OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_XSIZE, 100);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_YSIZE, 20);
   ObjectSetString(0, "DASHBOARD_SHOW", OBJPROP_TEXT, "Show Dashboard");
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_BGCOLOR, clrDarkBlue);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, "DASHBOARD_SHOW", OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
//| Create dashboard objects                                          |
//+------------------------------------------------------------------+
void CreateDashboard()
{
   int x = DashboardX;
   int y = DashboardY;
   int width = 220;
   int rowHeight = 18;
   int startY = y + 35;
   
   // Background
   ObjectCreate(0, "DASHBOARD_BG", OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_XSIZE, width);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_YSIZE, 340);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_BGCOLOR, DashboardBgColor);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_BORDER_TYPE, BORDER_RAISED);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, "DASHBOARD_BG", OBJPROP_SELECTABLE, false);
   
   // Title
   CreateLabel("DASHBOARD_TITLE", "BTC Smart Flow", x + 10, y + 8, 12, clrWhite);
   
   // Hide Button
   ObjectCreate(0, "DASHBOARD_HIDE", OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_XDISTANCE, x + 150);
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_YDISTANCE, y + 5);
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_XSIZE, 50);
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_YSIZE, 18);
   ObjectSetString(0, "DASHBOARD_HIDE", OBJPROP_TEXT, "Hide");
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_BGCOLOR, clrFireBrick);
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, "DASHBOARD_HIDE", OBJPROP_FONTSIZE, 8);
   
   // Status
   CreateLabel("DASHBOARD_STATUS", "Status: Waiting...", x + 10, startY, 10, clrWhite);
   
   // Market Context
   CreateLabel("DASHBOARD_LIVE_TREND", "Regime: NEUTRAL", x + 10, startY + rowHeight * 1, 10, clrWhite);
   CreateLabel("DASHBOARD_LIVE_STRUCT","Struct(M5): NEUTRAL", x + 10, startY + rowHeight * 2, 10, clrWhite);
   CreateLabel("DASHBOARD_LIVE_ZONE",  "Zone: NEUTRAL",      x + 10, startY + rowHeight * 3, 10, clrWhite);
   CreateLabel("DASHBOARD_LIVE_DELTA", "Delta: 0.0",         x + 10, startY + rowHeight * 4, 10, clrWhite);

   // Price Info
   CreateLabel("DASHBOARD_GAP", "Gap: ---", x + 10, startY + rowHeight * 6, 10, clrWhite);
   CreateLabel("DASHBOARD_BASIS", "Basis: 0.0", x + 10, startY + rowHeight * 7, 10, clrGray);
   
   // News
   CreateLabel("DASHBOARD_NEWS", "News: Clear", x + 10, startY + rowHeight * 9, 10, clrMediumSpringGreen);
   
   // Session
   CreateLabel("DASHBOARD_SESSION", "Session: ---", x + 10, startY + rowHeight * 10, 10, clrCyan);

   // Daily Stats
   CreateLabel("DASHBOARD_STATS", "Today: $0.00 (0W/0L)", x + 10, startY + rowHeight * 12, 10, clrWhite);
   CreateLabel("DASHBOARD_POS", "Positions: 0/" + IntegerToString(MaxPositions), x + 10, startY + rowHeight * 13, 10, clrOrange);
   
   // System Status
   CreateLabel("DASHBOARD_TIME", "System: INITIALIZING", x + 10, startY + rowHeight * 15, 9, clrGray);
}

//+------------------------------------------------------------------+
//| Create label helper                                               |
//+------------------------------------------------------------------+
void CreateLabel(string name, string text, int x, int y, int fontsize, color clr)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontsize);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
//| Update dashboard                                                  |
//+------------------------------------------------------------------+
void UpdateDashboard()
{
   if(!g_showDashboard) return;
   
   // Update status
   string status = "Waiting...";
   datetime now = TimeTradeServer();
   datetime lastAnyData = MathMax(g_lastSignalDt, g_lastIndicatorDt);
   
   if(now - lastAnyData < 60 && lastAnyData > 0)
   {
      status = "✓ Active (" + g_lastDataSource + ")";
   }
   else
   {
      status = "Waiting (" + ((g_zmqConnected) ? "ZMQ" : "FILE") + ")...";
   }
   ObjectSetString(0, "DASHBOARD_STATUS", OBJPROP_TEXT, status);
   
   // Update price gap with color
   string gapText = "Gap: $" + DoubleToString(g_priceGap, 2);
   color gapColor = (g_priceGap <= PriceTolerancePoints * symbolInfo.Point()) ? clrLime : clrRed;
   ObjectSetString(0, "DASHBOARD_GAP", OBJPROP_TEXT, gapText);
   ObjectSetInteger(0, "DASHBOARD_GAP", OBJPROP_COLOR, gapColor);
   
   // Update Basis
   ObjectSetString(0, "DASHBOARD_BASIS", OBJPROP_TEXT, "Basis: " + DoubleToString(g_priceBasis, 2));
   
   // News Status
   string newsTxt = "News: Clear";
   color newsClr = clrMediumSpringGreen;
   if(g_inNewsPause)
   {
      newsTxt = "STOP: " + g_nextNewsTitle;
      newsClr = clrOrangeRed;
   }
   else if(g_nextNewsTime > 0)
   {
      long diff = (long)g_nextNewsTime - (long)TimeCurrent();
      newsTxt = g_nextNewsTitle + " (" + IntegerToString((int)(diff/60)) + "m)";
      newsClr = clrYellow;
   }
   ObjectSetString(0, "DASHBOARD_NEWS", OBJPROP_TEXT, newsTxt);
   ObjectSetInteger(0, "DASHBOARD_NEWS", OBJPROP_COLOR, newsClr);
   
    // Daily Stats section
    string dailyPL = (g_dailyProfit >= 0) ? "+$" + DoubleToString(g_dailyProfit, 2) : "-$" + DoubleToString(MathAbs(g_dailyProfit), 2);
    color plColor = (g_dailyProfit >= 0) ? clrLime : clrRed;
    
    ObjectSetString(0, "DASHBOARD_STATS", OBJPROP_TEXT, 
       "Today: " + dailyPL + " (" + IntegerToString(g_dailyWinCount) + "W/" + IntegerToString(g_dailyLossCount) + "L)");
    ObjectSetInteger(0, "DASHBOARD_STATS", OBJPROP_COLOR, plColor);
    
    // Markets (v4.0 Enhanced H1/M5 display)
    ObjectSetString(0, "DASHBOARD_LIVE_TREND", OBJPROP_TEXT, "Regime: " + g_currRegime);
    // v6.1: Regime colors - TRENDING (lime), VOLATILE (red), RANGING/DEAD (white)
    color trendColor = (g_currRegime == "TRENDING") ? clrLime : (g_currRegime == "VOLATILE") ? clrRed : clrWhite;
    ObjectSetInteger(0, "DASHBOARD_LIVE_TREND", OBJPROP_COLOR, trendColor);
   
   
    ObjectSetString(0, "DASHBOARD_LIVE_STRUCT", OBJPROP_TEXT, "Struct(M5): " + g_currStructure);
    // v4.6: Simplified color logic - only BULLISH/BEARISH/RANGE
    color structColor = (StringFind(g_currStructure, "BULLISH") != -1) ? clrLime : (StringFind(g_currStructure, "BEARISH") != -1) ? clrRed : clrWhite;
    ObjectSetInteger(0, "DASHBOARD_LIVE_STRUCT", OBJPROP_COLOR, structColor);
   
   ObjectSetString(0, "DASHBOARD_LIVE_ZONE", OBJPROP_TEXT, "Zone: " + g_currZone);
   color zoneColor = (g_currZone == "DISCOUNT") ? clrLime : (g_currZone == "PREMIUM") ? clrRed : clrWhite;
   ObjectSetInteger(0, "DASHBOARD_LIVE_ZONE", OBJPROP_COLOR, zoneColor);

   ObjectSetString(0, "DASHBOARD_LIVE_DELTA", OBJPROP_TEXT, "Delta: " + DoubleToString(g_currDelta, 1));
   color deltaColor = (g_currDelta > 0) ? clrLime : (g_currDelta < 0) ? clrRed : clrWhite;
   ObjectSetInteger(0, "DASHBOARD_LIVE_DELTA", OBJPROP_COLOR, deltaColor);

    // Session update handled below
    
    ObjectSetString(0, "DASHBOARD_SESSION", OBJPROP_TEXT, "Session: " + g_currSession);
    color sessionColor = (g_currSession == "LONDON" || g_currSession == "NY" || g_currSession == "LONDON-NY") ? clrCyan : clrWhite;
    ObjectSetInteger(0, "DASHBOARD_SESSION", OBJPROP_COLOR, sessionColor);

   // Update positions
   int openPos = CountPositions();
   ObjectSetString(0, "DASHBOARD_POS", OBJPROP_TEXT, 
      "Pos: " + IntegerToString(openPos) + "/" + IntegerToString(MaxPositions));
      
   // Update Heartbeat Status
   string hbStatus = "System: 🟢 READY";
   color hbColor = clrLime;
   
   if(!g_systemSafe)
   {
      hbStatus = "System: 🔴 DISCONNECTED";
      hbColor = clrRed;
   }
   else if(g_lastHeartbeatTime == 0)
   {
      hbStatus = "System: 🟡 WAITING...";
      hbColor = clrYellow;
   }
   
    ObjectSetString(0, "DASHBOARD_TIME", OBJPROP_TEXT, hbStatus);
    ObjectSetInteger(0, "DASHBOARD_TIME", OBJPROP_COLOR, hbColor);
}

//+------------------------------------------------------------------+
//| Delete dashboard                                                  |
//+------------------------------------------------------------------+
void DeleteDashboard()
{
   ObjectDelete(0, "DASHBOARD_BG");
   ObjectDelete(0, "DASHBOARD_TITLE");
   ObjectDelete(0, "DASHBOARD_HIDE");
   ObjectDelete(0, "DASHBOARD_SEP1");
   ObjectDelete(0, "DASHBOARD_STATUS_LBL");
   ObjectDelete(0, "DASHBOARD_STATUS");
   ObjectDelete(0, "DASHBOARD_NEWS");
   ObjectDelete(0, "DASHBOARD_PRICE_LBL");
    ObjectDelete(0, "DASHBOARD_GAP");
    ObjectDelete(0, "DASHBOARD_STATS_LBL");
   ObjectDelete(0, "DASHBOARD_STATS");
   ObjectDelete(0, "DASHBOARD_CONTEXT_LBL");
   ObjectDelete(0, "DASHBOARD_LIVE_TREND");
   ObjectDelete(0, "DASHBOARD_LIVE_STRUCT");
   ObjectDelete(0, "DASHBOARD_LIVE_ZONE");
   ObjectDelete(0, "DASHBOARD_LIVE_DELTA");
   ObjectDelete(0, "DASHBOARD_BASIS");
   ObjectDelete(0, "DASHBOARD_POS");
   ObjectDelete(0, "DASHBOARD_PHASE_LBL");
    ObjectDelete(0, "DASHBOARD_SESSION");
    ObjectDelete(0, "DASHBOARD_TIME");
    
    // Also remove legacy if any
   ObjectDelete(0, "DASHBOARD_SIGNAL_LBL");
   ObjectDelete(0, "DASHBOARD_DIR");
   ObjectDelete(0, "DASHBOARD_ENTRY");
   ObjectDelete(0, "DASHBOARD_SL");
   ObjectDelete(0, "DASHBOARD_TP");
   ObjectDelete(0, "DASHBOARD_SCORE");
}

//+------------------------------------------------------------------+
//| Check for signals (Hybrid: ZMQ -> File)                           |
//+------------------------------------------------------------------+
void CheckForSignals()
{
   string content = "";
   
   // 1. Try ZeroMQ first (Real-time) - Loop to drain the queue
    if(g_zmqConnected)
    {
       while(true)
       {
          uchar buffer[16384]; // Increased buffer size for large JSON
          int bytes = zmq_recv(g_zmqSocket, buffer, 16384, ZMQ_DONTWAIT);
         
         if(bytes <= 0) break; // No more messages or error
          content = CharArrayToString(buffer, 0, bytes, CP_UTF8);
          StringReplace(content, "\0", ""); // Clean up potential null terminators
          
          // Parse topic and json
          string topic = "";
          string json = content;
          
          StringTrimLeft(content);
          
          // Improved splitting: find first space but only if it's within first 32 chars (topic limit)
          int spacePos = StringFind(content, " ");
          if(spacePos > 0 && spacePos < 32)
          {
             topic = StringSubstr(content, 0, spacePos);
             json = StringSubstr(content, spacePos + 1);
             StringTrimLeft(topic);
             StringTrimRight(topic);
          }
          else if(StringGetCharacter(content, 0) == '{')
          {
             topic = "signal";
             json = content;
          }
          else
          {
             if(EnableDebug) Print("⚠️ Unknown message format: ", content);
             continue;
          }
         
         StringTrimLeft(json);
         StringTrimRight(json);
         
          // Silent processing - only log on debug
          // if(EnableDebug) Print("📡 ZMQ Message: Topic='", topic, "' Length=", StringLen(json));
          
          g_lastDataSource = "ZMQ";
          g_lastHeartbeatTime = TimeCurrent();
          
          if(topic == "signal")
          {
              if(EnableDebug) Print("📡 ZMQ SIGNAL RECEIVED");
             ProcessSignal(json);
          }
          else if(topic == "indicator")
          {
             // Silent - indicators update dashboard only
             // if(EnableDebug) Print("📡 ZMQ INDICATOR: ", json);
             g_systemSafe = true; // Indicators also confirm life
             ProcessIndicator(json);
          }
          else if(topic == "heartbeat")
          {
             // Silent - heartbeat only updates connection status
             // if(EnableDebug) Print("📡 ZMQ HEARTBEAT: ", json);
             if(g_showDashboard) UpdateDashboard();
          }
          else if(topic == "command")
          {
             Print("🚀 COMMAND RECEIVED: ", json);
             CJAVal cmd;
             if(cmd.Deserialize(json))
             {
                string action = cmd["action"].ToStr();
                if(action == "CLOSE_ALL") CloseAllPositions();
             }
          }
          else if(topic == "trailing")
          {
             // Silent - trailing stops update quietly
             // if(EnableDebug) Print("🚀 ZMQ TRAILING: ", json);
             ProcessTrailing(json);
          }
      }
   }
   
   // 2. Fallback to File Reader if no ZMQ signal
   string filename = (SignalFilePath != "") ? SignalFilePath : "signal.json";
   if(FileIsExist(filename))
   {
      datetime fileTime = (datetime)FileGetInteger(filename, FILE_MODIFY_DATE);
      if(fileTime > g_lastFileTime)
      {
         int fileHandle = FileOpen(filename, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
         if(fileHandle != INVALID_HANDLE)
         {
            content = FileReadString(fileHandle);
            FileClose(fileHandle);
            g_lastFileTime = fileTime;
            
            if(StringLen(content) > 10) // Basic sanity check
            {
               if(EnableDebug) Print("📁 FILE SIGNAL: ", content);
               ProcessSignal(content);
               g_lastDataSource = "FILE";
            }
         }
      }
   }
   
    // Check for heartbeat timeout
    if(g_lastHeartbeatTime > 0 && TimeCurrent() - g_lastHeartbeatTime > HeartbeatTimeoutSeconds)
    {
       if(g_systemSafe) 
       {
          Print("⚠️ SYSTEM ALERT: Heartbeat timeout! Python connection lost. Auto-recovery enabled.");
          g_systemSafe = false;
          g_autoRecoverAttempt = TimeCurrent();  // Mark time for auto-recovery
          if(g_showDashboard) UpdateDashboard();
       }
       
        // AUTO-RECOVERY: Try to reconnect automatically every 5 seconds
        // v19.1: ไม่ reconnect ถ้า connected อยู่แล้ว
        if(!g_systemSafe && !g_zmqConnected && TimeCurrent() - g_autoRecoverAttempt >= 5)
        {
          if(EnableZMQ && g_reconnectRetries < g_maxReconnectRetries)
          {
             Print("🔄 AUTO-RECOVERY: Attempting to reconnect... (Try ", g_reconnectRetries + 1, "/", g_maxReconnectRetries, ")");
             CloseZMQ();
             InitZMQ();
             g_reconnectRetries++;
             g_autoRecoverAttempt = TimeCurrent();
          }
       }
    }
    else
    {
       // Reset reconnect counter when connection is good
       g_reconnectRetries = 0;
    }
}

//+------------------------------------------------------------------+
//| Process indicator content                                         |
//+------------------------------------------------------------------+
void ProcessIndicator(string content)
{
   // AUTO-RECOVERY: If we receive indicator while in SAFE MODE, Python is back!
   if(!g_systemSafe)
   {
      g_systemSafe = true;
      g_lastHeartbeatTime = TimeCurrent();
      g_reconnectRetries = 0;
      if(g_showDashboard) UpdateDashboard();
      // Silent recovery - no need to log every indicator
   }
   
   StringTrimLeft(content);
   StringTrimRight(content);
   
   // Verbose debug disabled
   // if(EnableDebug) Print("DEBUG RAW INDICATOR: ", content);

   CJAVal json;
   if(!json.Deserialize(content)) 
   {
      // Only log error if signal, not indicator
      // Print("❌ Failed to deserialize INDICATOR JSON");
      return;
   }

   // Debug: List all keys at root - disabled
   // if(EnableDebug)
   // {
   //    string keys = "";
   //    for(int i=0; i<ArraySize(json.m_list); i++) keys += json.m_list[i].m_key + ", ";
   //    Print("DEBUG Root Keys: ", keys);
   // }

   CJAVal *ind = json["indicators"];
   if(CheckPointer(ind) == POINTER_INVALID)
   {
      Print("❌ JSON Error: 'indicators' key not found");
      return;
   }

    // JAson.mqh returns pointers, so chained [] fails. Must dereference.
     g_currRegime = (*ind)["regime"].ToStr();
    
    string structVal = (*ind)["structure"].ToStr();
    g_currStructure = (structVal != "") ? structVal : "RANGE";
    g_htfStructure = (*ind)["htf_structure"].ToStr();
   g_currZone = (*ind)["zone_context"].ToStr();
   g_currDelta = (*ind)["delta"].ToDbl();
   
   g_phase1Score = (*ind)["phase1_score"].ToInt();
   g_phase2Score = (*ind)["phase2_score"].ToInt();
   g_currSession = (*ind)["session"].ToStr();
   g_riskTier = (*ind)["risk_tier"].ToInt();
   g_drawdown = (*ind)["drawdown"].ToDbl();
    g_isAggressive = (*ind)["is_aggressive"].ToBool();
    
     // Parse News info from indicators
    CJAVal *news = (*ind)["next_news"];
    if(CheckPointer(news) != POINTER_INVALID && news.m_type == JOBJECT)
    {
       g_nextNewsTitle = (*news)["title"].ToStr();
       g_nextNewsTime = (datetime)(*news)["timestamp"].ToInt();
    }

    g_lastIndicatorDt = TimeTradeServer();
    
    // Silent dashboard update
    // if(EnableDebug) Print("📊 Dashboard Updated: Trend=", g_currTrend, " Score=", g_phase1Score, "/", g_phase2Score);
    
    if(g_showDashboard) UpdateDashboard();
    // Silent indicator processing
    // if(EnableDebug) Print("📊 Indicators Processed: Trend=", g_currTrend, " Score=", g_phase1Score, "/", g_phase2Score, " Tier=", g_riskTier);
}

//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Tighten SL to lock in profit (v4.0) - Fixed H-01                 |
//+------------------------------------------------------------------+
void TightenPosition(ulong ticket)
{
   if(positionInfo.SelectByTicket(ticket))
   {
      double pEntry = positionInfo.PriceOpen();
      double pClose = positionInfo.PriceCurrent();
      double pSL = positionInfo.StopLoss();
      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)positionInfo.PositionType();
      
      // v9.2-S4: ATR-based buffer (0.3 × ATR, floor $30)
      double atrVal = iATR(g_symbol, PERIOD_CURRENT, 14);
      double buffer = MathMax(atrVal * 0.3, 30 * SymbolInfoDouble(g_symbol, SYMBOL_POINT));
      double newSL = 0;
      
      // Calculate current profit
      double profit = (type == POSITION_TYPE_BUY) ? (pClose - pEntry) : (pEntry - pClose);
      double profitPct = (pEntry > 0) ? (profit / pEntry) * 100.0 : 0;
      
      if(type == POSITION_TYPE_BUY)
      {
         // H-01 Fix: Choose 50% profit lock when profit is sufficient (>1%), otherwise BE
         double bePrice = pEntry + buffer;
         double profitLock = pEntry + profit * 0.5;
         
         // If profit > 1%, prefer 50% lock over BE (more aggressive)
         // If profit <= 1%, use BE (conservative)
         if(profitPct > 1.0)
            newSL = profitLock;  // Force 50% lock when profit is good
         else
            newSL = bePrice;     // Use BE when profit is small
         
         if(newSL <= pSL) return; 
      }
      else
      {
         double bePrice = pEntry - buffer;
         double profitLock = pEntry - profit * 0.5;
         
         if(profitPct > 1.0)
            newSL = profitLock;  // Force 50% lock when profit is good
         else
            newSL = bePrice;     // Use BE when profit is small
         
         if(newSL >= pSL && pSL > 0) return;
      }
      
      int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
      newSL = NormalizeDouble(newSL, digits);
      
      if(trade.PositionModify(ticket, newSL, positionInfo.TakeProfit()))
      {
         Print("🛡️ POSITION TIGHTENED: SL moved to ", DoubleToString(newSL, digits), " (Profit: ", DoubleToString(profitPct, 2), "%)");
      }
      else
      {
         Print("❌ TIGHTEN FAILED: Error code=", trade.ResultRetcode());
      }
    }
}

//+------------------------------------------------------------------+
//| Section 23: Removed Section 20 (Smart Position Management)       |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Process signal content                                            |
//+------------------------------------------------------------------+
void ProcessSignal(string content)
{
   // AUTO-RECOVERY: If we receive a signal while in SAFE MODE, Python is back!
   if(!g_systemSafe)
   {
      Print("✅ AUTO-RECOVERY: Signal received while in SAFE MODE - Python is back! Resuming trading...");
      g_systemSafe = true;
      g_lastHeartbeatTime = TimeCurrent();
      g_reconnectRetries = 0;
      if(g_showDashboard) UpdateDashboard();
   }

    CJAVal json;
    if(!json.Deserialize(content))
    {
       Print("❌ SIGNAL REJECTED: Invalid JSON format");
       return;
    }
    
    // Check Max Daily Loss
    if(MaxDailyLossPct > 0)
    {
       double dailyPnL = CalculateDailyPnL();
       double balance = AccountInfoDouble(ACCOUNT_BALANCE);
       double dailyLossPct = 0;
       if(balance > 0)
       {
          dailyLossPct = -dailyPnL / balance * 100;
       }
       
       if(dailyLossPct >= MaxDailyLossPct)
       {
          Print("🛑 SIGNAL BLOCKED: Daily Loss Limit Reached (", DoubleToString(dailyLossPct, 2), "% / ", MaxDailyLossPct, "%)");
          g_systemSafe = false; // Enter safe mode
          return;
       }
    }

    string signalId = json["signal_id"].ToStr();
    string direction = json["direction"].ToStr();
    string shortReason = json["short_reason"].ToStr();
    double entryPrice = json["entry_price"].ToDbl();
    double stopLoss = json["stop_loss"].ToDbl();
    double takeProfit = json["take_profit"].ToDbl();
    int    score = json["score"].ToInt();
    string reason = json["reason"].ToStr();
    
      // --- Lot and TP Extraction (v3.5) ---
      double lotSize = json["lot_size"].ToDbl();
      
      // v6.1: Extract TP1 (BE trigger) & TP2 (actual TP) from signal
      double tp1 = json["tp1_level"].ToDbl();
      double tp2 = json["tp2_level"].ToDbl();
      double tp3 = 0; // v6.1: No TP3 (replaced by TP1/TP2 system)
      
      // v6.1: Store TP1/TP2 levels for BE trail logic
      g_tp1Level = tp1;
      g_tp2Level = (tp2 > 0) ? tp2 : takeProfit;
      g_beTriggered = false;
      
      g_beTriggered = false;
      
      // v11.x: Extract mode (IPA, IOF, IPA_FRVP, IOF_FRVP)
      string mode = json["mode"].ToStr();
      // Normalize mode aliases
      string modeCategory = mode;
      if(mode == "IPA_FRVP") modeCategory = "IPA";
      if(mode == "IOF_FRVP") modeCategory = "IOF";
      
      if(mode != "IPA" && mode != "IOF" && mode != "IPA_FRVP" && mode != "IOF_FRVP")
      {
         Print("❌ SIGNAL REJECTED: Unknown mode '", mode, "'");
         return;
      }
      if(modeCategory == "IPA" && !EnableIPA)
      {
         Print("🛡️ SIGNAL BLOCKED: IPA mode disabled");
         return;
      }
      if(modeCategory == "IOF" && !EnableIOF)
      {
         Print("🛡️ SIGNAL BLOCKED: IOF mode disabled");
         return;
      }
      
      // v4.9 M5: Extract RR from signal
      double requiredRR = json["required_rr"].ToDbl();
      bool institutionalGrade = json["institutional_grade"].ToBool();
     
      // v16.0: EA คำนวณ lot เอง เสมอ (ไม่ใช้จาก Python)
      // v16.7: ใช้ entryPrice จาก signal ไม่ใช่ Ask (ป้องกัน lot ผิดเมื่อ Ask ใกล้ SL)
      lotSize = CalculateLotSize(entryPrice, stopLoss);
    
// --- EA EXECUTION GUARDS (v4.0 - Section 43: Adaptive Execution Guard) ---
    datetime now = TimeCurrent();
    
    // === Section 43.3: Minimum Safety Lock (Hard Lock) ===
     // Always enforce minimum HardLockSeconds (default 30s) to prevent message duplication
    int minLockSeconds = HardLockSeconds;
    if((long)now - (long)g_lastSignalTime < minLockSeconds)
    {
       Print("🛡️ SIGNAL IGNORED [HARD LOCK]: Minimum ", minLockSeconds, "s not elapsed since last signal");
       return;
    }
    
    // === Section 43.1: Price-Distance Based Cooldown ===
    // Allow new trade if price has moved 0.15% - 0.20% from last entry
    double priceDistancePct = MathAbs(entryPrice - g_lastTrade.entry_price) / g_lastTrade.entry_price * 100;
    bool priceDistancePassed = (priceDistancePct >= PriceDistancePct);
    
    // === Section 43.2: Risk-Free State Acceptance ===
    // Allow new trade immediately if previous position is at breakeven or in profit
    bool breakevenUnlock = false;
    if(EnableBreakevenUnlock)
    {
       // Check if there's an open position for this symbol
       if(PositionsTotal() > 0)
       {
          for(int i = PositionsTotal() - 1; i >= 0; i--)
          {
             if(PositionGetSymbol(i) == g_symbol)
             {
                double posOpenPrice = PositionGetDouble(POSITION_PRICE_OPEN);
                double posSL = PositionGetDouble(POSITION_SL);
                double posTP = PositionGetDouble(POSITION_TP);
                double currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
                ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
                
                // Check if position is at breakeven (SL at or better than entry)
                if(posType == POSITION_TYPE_BUY)
                {
                   // Long position: SL >= Entry means breakeven or profit
                   if(posSL >= posOpenPrice - 10 * SymbolInfoDouble(g_symbol, SYMBOL_POINT))
                   {
                      breakevenUnlock = true;
                       if(EnableDebug) Print("✅ BREAKEVEN UNLOCK: Long position SL at ", posSL, " >= Entry ", posOpenPrice);
                      break;
                   }
                   // Or position is in profit
                   if(currentPrice > posOpenPrice)
                   {
                      breakevenUnlock = true;
                       if(EnableDebug) Print("✅ PROFIT UNLOCK: Long position in profit (Current: ", currentPrice, " > Entry: ", posOpenPrice, ")");
                      break;
                   }
                }
                else if(posType == POSITION_TYPE_SELL)
                {
                   // Short position: SL <= Entry means breakeven or profit
                   if(posSL <= posOpenPrice + 10 * SymbolInfoDouble(g_symbol, SYMBOL_POINT))
                   {
                      breakevenUnlock = true;
                       if(EnableDebug) Print("✅ BREAKEVEN UNLOCK: Short position SL at ", posSL, " <= Entry ", posOpenPrice);
                      break;
                   }
                   // Or position is in profit
                   if(currentPrice < posOpenPrice)
                   {
                      breakevenUnlock = true;
                       if(EnableDebug) Print("✅ PROFIT UNLOCK: Short position in profit (Current: ", currentPrice, " < Entry: ", posOpenPrice, ")");
                      break;
                   }
                }
             }
            }
       }
}
       
       // v6.0: If no positions exist → reset g_lastTrade (no position = no cooldown needed)
      // v10.1: Reset regardless of direction (stale data blocks new signals)
       if(CountPositions() == 0)
       {
          g_lastTrade.entry_price = 0;
          g_lastTrade.invalidation_price = 0;
          g_lastTrade.direction = "";
          g_lastTrade.short_reason = "";
          g_lastTrade.is_reset = true;
          priceDistancePassed = true;
          
          // v13.6 FIX (BUG 02): Reset BE state ONLY when no positions exist (was outside block - reset every signal!)
          g_tp1Level = 0;
          g_tp2Level = 0;
          g_beTriggered = false;
        }
        
        // v16.1: ลบ Combined Cooldown Check - ปลดล็อกให้เทรดได้มากขึ้น
    
    // 1. Duplicate Guard (Signal ID)
    if(signalId == g_lastSignalId)
    {
       Print("🛡️ SIGNAL IGNORED [DUPLICATE]: Same signal ID '", signalId, "'");
       return;
    }
    
    // 4. Recent Zone Guard (Invalidation Level + Direction Check)
    if(MathAbs(stopLoss - g_lastTrade.invalidation_price) < 5.0 * SymbolInfoDouble(g_symbol, SYMBOL_POINT))
    {
       if(direction == g_lastTrade.direction && !g_lastTrade.is_reset)
       {
          Print("🛡️ SIGNAL IGNORED [ZONE]: Same direction in Recent Zone (Dir: ", direction, ", SL: ", stopLoss, ") - Waiting for escape move (", ZoneResetPct, "%)");
          return;
       }
    }
   
// === SECTION 5: PRICE BASIS ADJUSTMENT (ต้องทำก่อน RR Check) ===
// v4.0 Architecture Plan Section 34: Strategist (Python) sends Binance price
// Executioner (EA) adjusts to Broker price before any RR validation
    double currentBid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
    double currentAsk = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
    double currentPrice = (direction == "LONG") ? currentAsk : currentBid;
    
    if(UseDynamicOffset)
    {
       g_priceBasis = currentPrice - entryPrice;
       if(EnableDebug) Print("⚖️ PRICE BASIS: Broker=", DoubleToString(currentPrice, 2),
          " Signal=", DoubleToString(entryPrice, 2),
          " Basis=", DoubleToString(g_priceBasis, 2));
       stopLoss   += g_priceBasis;
       
       // v6.0: Adjust TP ในทิศทางที่ถูกต้องเพื่อรักษา RR
        // v12.3: TP Basis Adjustment — ใช้ += ทุกทิศ (เหมือน SL)
        // Logic: ถ้า broker สูงกว่า Python → basis บวก → SL/TP ขยับขึ้น
        //        ถ้า broker ต่ำกว่า Python → basis ลบ → SL/TP ขยับลง
        // ถ้า broker = entry → basis = 0 → SL/TP ไม่เปลี่ยน = RR ตรงตาม Python
        takeProfit += g_priceBasis;
        tp1 = (tp1 > 0) ? tp1 + g_priceBasis : 0;
        tp2 = (tp2 > 0) ? tp2 + g_priceBasis : 0;
        tp3 = (tp3 > 0) ? tp3 + g_priceBasis : 0;
      }
      
      // v24.0: Validate TP/SL after basis adjustment
      // หลัง basis adjust SL/TP relative กับ currentPrice ไม่ใช่ entryPrice
      double validatePrice = UseDynamicOffset ? currentPrice : entryPrice;
      if(direction == "LONG")
      {
         if(takeProfit > 0 && takeProfit <= validatePrice)
         {
            Print("⚠️ TP INVALID: LONG but TP ", DoubleToString(takeProfit, 2),
                  " <= Price ", DoubleToString(validatePrice, 2),
                  " (basis: ", DoubleToString(g_priceBasis, 2), ") → SKIP");
            return;
         }
         if(stopLoss >= validatePrice)
         {
            Print("⚠️ SL INVALID: LONG but SL ", DoubleToString(stopLoss, 2),
                  " >= Price ", DoubleToString(validatePrice, 2),
                  " (basis: ", DoubleToString(g_priceBasis, 2), ") → SKIP");
            return;
         }
      }
      else // SHORT
      {
         if(takeProfit > 0 && takeProfit >= validatePrice)
         {
            Print("⚠️ TP INVALID: SHORT but TP ", DoubleToString(takeProfit, 2),
                  " >= Price ", DoubleToString(validatePrice, 2),
                  " (basis: ", DoubleToString(g_priceBasis, 2), ") → SKIP");
            return;
         }
         if(stopLoss <= validatePrice)
         {
            Print("⚠️ SL INVALID: SHORT but SL ", DoubleToString(stopLoss, 2),
                  " <= Price ", DoubleToString(validatePrice, 2),
                  " (basis: ", DoubleToString(g_priceBasis, 2), ") → SKIP");
            return;
         }
      }

      // v13.1: Store ADJUSTED TP levels for BE trail logic (Unconditional)
      g_tp1Level = tp1;
      g_tp2Level = (tp2 > 0) ? tp2 : takeProfit;
      g_beTriggered = false;
      

    
// === SECTION 5: RR CHECK (v12.0 - Trust Python's required_rr) ===
// v12.0: Skip broker RR recalculation — trust Python's required_rr directly
// Python's SLTP calculator already validates RR before sending signal
// EA acts as executioner, not RR validator
// Broker price differences cause RR mismatch (e.g., 1.71 vs 1.56)
// Just verify requiredRR meets mode minimum
    double modeRRMin = (mode == "IOF") ? MinRR_IOF : MinRR_IPA;
    
    if(requiredRR > 0 && requiredRR < modeRRMin)
    {
       Print("🛡️ SIGNAL REJECTED [RR]: requiredRR (", DoubleToString(requiredRR, 2),
             ") < mode min (", DoubleToString(modeRRMin, 2), ")");
       return;
    }
    
    // v12.0: No broker RR recalculation — trust Python's calculation
     if(EnableDebug) Print("✅ RR CHECK PASSED: requiredRR=", DoubleToString(requiredRR, 2));
    
// === SECTION 5: INVALIDATION CHECK ===
    bool isInvalid = false;
    string invalidReason = "";
    if(direction == "LONG")
    {
       if(currentBid <= stopLoss)
          { isInvalid = true; invalidReason = "Price Already @ SL"; }
       if(takeProfit > 0 && currentBid >= takeProfit)
          { isInvalid = true; invalidReason = "Price Already @ TP"; }
    }
    else
    {
       if(currentAsk >= stopLoss)
          { isInvalid = true; invalidReason = "Price Already @ SL"; }
       if(takeProfit > 0 && currentAsk <= takeProfit)
          { isInvalid = true; invalidReason = "Price Already @ TP"; }
    }
    if(isInvalid)
    {
       Print("🛡️ SIGNAL REJECTED [INVALID]: ", invalidReason,
             " (Price: ", DoubleToString(currentPrice, 2),
             " SL: ", DoubleToString(stopLoss, 2), ")");
       return;
    }
    
    // v4.0: RR check done after Basis Adjustment (Section 34)
     // Position checks first (before setting g_lastSignalId)
     if(CountPositions() >= MaxPositions)
    {
       Print("⚠️ SIGNAL IGNORED [LIMIT]: Max positions reached (", MaxPositions, ")");
       return;
    }
     
     // v25.0: IOF/IOFF count by signal_type independently (MOMENTUM ≠ REVERSAL)
     // IPA/IPAF count by mode (no signal_type separation needed)
     string shortReason_local = json["short_reason"].ToStr();
     string sigType = GetSignalTypeFromComment(shortReason_local);
     bool isIOFMode = (mode == "IOF" || mode == "IOF_FRVP");

     if(isIOFMode && sigType != "IPA" && sigType != "UNKNOWN")
     {
        // v25.0: IOF/IOFF per signal_type limit (user-configurable)
        int maxForType = MaxMomentumPerDir; // default
        if(sigType == "MOMENTUM") maxForType = MaxMomentumPerDir;
        else if(sigType == "ABSORPTION") maxForType = MaxAbsorptionPerDir;
        else if(sigType == "REVERSAL_OB" || sigType == "REVERSAL_OS") maxForType = MaxReversalPerDir;
        else if(sigType == "MEAN_REVERT") maxForType = MaxMeanRevertPerDir;

        int typeDirCount = CountPositionsBySignalTypeAndDir(sigType, direction);
        if(typeDirCount >= maxForType)
        {
           Print("⚠️ SIGNAL IGNORED [TYPE_DIR]: Max ", sigType, " ", direction, " (", typeDirCount, "/", maxForType, ")");
           return;
        }
     }
     else
     {
        // IPA/IPAF: count per mode + direction (original)
        int modeDirCount = CountPositionsByModeAndDirection(mode, direction);
        if(modeDirCount >= MaxPositionsPerModeDir)
        {
           Print("⚠️ SIGNAL IGNORED [MODE_DIR]: Max ", mode, " ", direction, " (", modeDirCount, "/", MaxPositionsPerModeDir, ")");
           return;
        }
     }
     
     // === v16.5: Duplicate Entry Guard ===
     // ไม่เปิดออเดอร์ซ้ำในราคาใกล้กัน สำหรับ mode+direction เดียวกัน
     // เช็คตาม mode ตรง (IPA ≠ IPAF ≠ IOF ≠ IOFF) ไม่ใช่ category
     
     // คำนวณ ATR สำหรับ min distance
     double entryATR = 0;
     MqlRates rates[];
     if(CopyRates(g_symbol, PERIOD_M5, 0, 14, rates) >= 14)
     {
        double trSum = 0;
        for(int k = 1; k < 14; k++)
        {
            double tr = MathMax(rates[k].high - rates[k].low,
                       MathMax(MathAbs(rates[k].high - rates[k-1].close),
                               MathAbs(rates[k].low - rates[k-1].close)));
            trSum += tr;
        }
        entryATR = trSum / 13;
     }
     // v26.0: min distance เพิ่มจาก $50 → $150 (IPAF ซ้ำ entry $66,095 x6 = -$39)
     double minEntryDistance = entryATR * 0.5;
     if(minEntryDistance < 150) minEntryDistance = 150;  // minimum $150 for BTC
     
     bool tooClose = false;
     double closestDist = 999999;
     double closestPrice = 0;
     
     for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
        if(positionInfo.SelectByIndex(i))
        {
            if(positionInfo.Symbol() == g_symbol &&
               positionInfo.Magic() == MagicNumber)
            {
               string comment = positionInfo.Comment();
               string posMode = GetModeFromComment(comment);
               
               // เช็คตาม mode ตรง (ไม่ใช่ category)
               if(posMode == mode)
               {
                  // v29.1: Skip if signal_type different (MOMENTUM ≠ ABSORPTION → allow for data collection)
                  string posSigType = GetSignalTypeFromComment(comment);
                  if(posSigType != sigType)
                     continue;  // Different signal type → don't block

                  ENUM_POSITION_TYPE posType = positionInfo.PositionType();
                  bool sameDir = (direction == "LONG" && posType == POSITION_TYPE_BUY) ||
                                 (direction == "SHORT" && posType == POSITION_TYPE_SELL);

                  if(sameDir)
                  {
                     double posEntry = positionInfo.PriceOpen();
                     double dist = MathAbs(entryPrice - posEntry);

                     if(dist < closestDist)
                     {
                        closestDist = dist;
                        closestPrice = posEntry;
                     }

                     if(dist < minEntryDistance)
                        tooClose = true;
                  }
               }
            }
        }
     }
     
     if(tooClose)
     {
        Print("🛡️ SIGNAL IGNORED [ENTRY_TOO_CLOSE]: ", mode, " ", direction,
              " | New:", DoubleToString(entryPrice, 2),
              " vs Existing:", DoubleToString(closestPrice, 2),
              " | Dist:", DoubleToString(closestDist, 2),
              " < Min:", DoubleToString(minEntryDistance, 2));
        return;
     }
     
     // v12.1: Set signal ID only AFTER passing all checks
     // (Previously set BEFORE checks — if rejected, duplicate guard would block re-send)
     g_lastSignalId = signalId;
     g_lastSignalTime = now;
      
     // v23.1: Stateless tracking requires orderComment to ALWAYS be signalId
     // signalId is already formatted as {ShortReason}_{HHMMSS} (max 29 chars)
     string orderComment = signalId;
     if(StringLen(orderComment) > 31) orderComment = StringSubstr(orderComment, 0, 31);
    
    // v4.9 M5: Store mode in g_currentPatternType for trailing
    g_currentPatternType = mode;
    
     Print("🚀 OPENING POSITION [", mode, "]: ", direction, " | Lot: ", DoubleToString(lotSize, 3), " | @", DoubleToString(entryPrice, 2), " | Score:", score);
     // v6.1 TP Levels removed per user request
     g_totalTrades++;
    
    // Update Last Trade State for Guards
    g_lastTrade.signal_id = signalId;
    g_lastTrade.short_reason = shortReason;
    g_lastTrade.direction = direction;  // D-04: Store direction for zone guard
    g_lastTrade.mode = mode;             // v4.9 M5: Store mode
    g_lastTrade.entry_price = entryPrice;
    g_lastTrade.invalidation_price = stopLoss;
    g_lastTrade.time = TimeCurrent();
    g_lastTrade.is_reset = false;
    g_lastTrade.max_dist_from_sl = 0;
    g_lastTrade.max_favorable = 0;
    g_lastTrade.max_adverse = 0;
   
   // v11.7: Single position only - partial split removed
   // tp1_level (g_tp1Level) is used for BE trail in ManageTrailingRisk() only
   // tp2_level (g_tp2Level) is the actual take profit
   ENUM_ORDER_TYPE orderType = (direction == "LONG") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   
    // Use TP2 as the actual take profit (TP1 is for BE trigger only)
    double actualTP = (g_tp2Level > 0) ? g_tp2Level : takeProfit;
    
    // v23.0: Open position and send confirmation
    bool orderSuccess = OpenPosition(orderType, lotSize, entryPrice, stopLoss, actualTP, orderComment);
    
    // v23.0: Send trade confirmation to Python
    if(orderSuccess)
    {
        // Wait for order to be filled (check order result)
        Sleep(500); // Brief wait for market execution
        double filledPrice = (direction == "LONG") ? SymbolInfoDouble(g_symbol, SYMBOL_ASK) : SymbolInfoDouble(g_symbol, SYMBOL_BID);
        SendTradeConfirm(signalId, "OPENED", filledPrice, 0, mode);
    }
}

//+------------------------------------------------------------------+
//| Process trailing stop update                                      |
//+------------------------------------------------------------------+
void ProcessTrailing(string content)
{
   CJAVal json;
   if(!json.Deserialize(content)) return;

   string signalId = json["signal_id"].ToStr();
   double newSL = json["new_sl"].ToDbl();
   string reason = json["reason"].ToStr();
   
   if(signalId == "" || newSL <= 0) return;
   
   int digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   newSL = NormalizeDouble(newSL, digits);
   
   bool found = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
         {
            string comment = positionInfo.Comment();
            // Match signalId in comment
            if(StringFind(comment, signalId) != -1 || StringFind(signalId, comment) != -1)
            {
               double currentSL = positionInfo.StopLoss();
               
               // Avoid redundant updates or moving SL in wrong direction
               if(MathAbs(currentSL - newSL) < symbolInfo.Point()) continue;
               
               if(trade.PositionModify(positionInfo.Ticket(), newSL, positionInfo.TakeProfit()))
               {
                  Print(">> SL Updated for ", signalId, ": ", DoubleToString(newSL, digits), " (", reason, ")");
                  found = true;
               }
               else
               {
                  Print("!! Failed to update SL for ", signalId, ". Error: ", trade.ResultRetcodeDescription());
               }
            }
         }
      }
   }
   
    // Silent - no need to log when position not found for trailing
    // if(!found && EnableDebug)
    //    Print("?? Trailing update received for ", signalId, " but no matching position found.");
}

//+------------------------------------------------------------------+
//| Report Account Info via ZeroMQ                                    |
//+------------------------------------------------------------------+
void ReportAccountInfo()
{
   if(!g_zmqPubBound || g_zmqPubSocket == 0) return;
   
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double profit = AccountInfoDouble(ACCOUNT_PROFIT);
   double margin = AccountInfoDouble(ACCOUNT_MARGIN);
   string company = AccountInfoString(ACCOUNT_COMPANY);
   
   string json = StringFormat(
      "{\"balance\":%.2f,\"equity\":%.2f,\"profit\":%.2f,\"margin\":%.2f,\"company\":\"%s\",\"account\":%d,\"timestamp\":\"%s\"}",
      balance, equity, profit, margin, company, (int)AccountInfoInteger(ACCOUNT_LOGIN), TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS)
   );
   
   string message = "account_info " + json;
   uchar buffer[];
   StringToCharArray(message, buffer, 0, WHOLE_ARRAY, CP_UTF8);
   int res = zmq_send(g_zmqPubSocket, buffer, ArraySize(buffer) - 1, ZMQ_DONTWAIT);
   if(res < 0 && EnableDebug)
      Print("❌ Failed to send account info via ZMQ");
}

//+------------------------------------------------------------------+
//| Report Position Info via ZeroMQ                                   |
//+------------------------------------------------------------------+
void ReportPositionInfo()
{
   if(!g_zmqPubBound || g_zmqPubSocket == 0) return;
   
   string positionsJson = "[";
   bool first = true;
   
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
         {
            if(!first) positionsJson += ",";
            
            string pos = StringFormat(
               "{\"ticket\":%d,\"type\":%d,\"volume\":%.2f,\"price\":%.2f,\"sl\":%.2f,\"tp\":%.2f,\"profit\":%.2f,\"comment\":\"%s\"}",
               (int)positionInfo.Ticket(),
               (int)positionInfo.PositionType(),
               positionInfo.Volume(),
               positionInfo.PriceOpen(),
               positionInfo.StopLoss(),
               positionInfo.TakeProfit(),
               positionInfo.Profit(),
               positionInfo.Comment()
            );
            positionsJson += pos;
            first = false;
         }
      }
   }
   positionsJson += "]";
   
   string message = "position_info " + positionsJson;
   uchar buffer[];
   StringToCharArray(message, buffer, 0, WHOLE_ARRAY, CP_UTF8);
   zmq_send(g_zmqPubSocket, buffer, ArraySize(buffer) - 1, ZMQ_DONTWAIT);
}

//+------------------------------------------------------------------+
//| Close all open positions immediately                              |
//+------------------------------------------------------------------+
void CloseAllPositions()
{
   Print("⚠️ EMERGENCY: Closing all positions!");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(positionInfo.SelectByIndex(i))
      {
         if(positionInfo.Symbol() == g_symbol && positionInfo.Magic() == MagicNumber)
         {
            trade.PositionClose(positionInfo.Ticket());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Daily P&L Tracking - Initialize or Reset at Start of Day          |
//+------------------------------------------------------------------+
void InitDailyTracking()
{
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime todayStart = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   
   if(g_dailyStartTime == 0 || g_dailyStartTime < todayStart)
   {
      g_dailyStartTime = todayStart;
      g_dailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      
      // Initialize from history for today
      HistorySelect(todayStart, TimeCurrent());
      int total = HistoryDealsTotal();
      
      g_dailyProfit = 0;
      g_dailyWinCount = 0;
      g_dailyLossCount = 0;
      g_dailyWinAmount = 0;
      g_dailyLossAmount = 0;
      
      for(int i = 0; i < total; i++)
      {
         ulong ticket = HistoryDealGetTicket(i);
         if(ticket == 0) continue;
         
         long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
         if(magic != MagicNumber) continue;
         
         double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
         if(profit == 0) continue;
         
         // Mark as already counted to avoid double counting in CheckClosedPositions
         string key = "closed_" + IntegerToString((int)ticket);
         GlobalVariableSet(key, 1);
         
         if(profit > 0)
         {
            g_dailyWinCount++;
            g_dailyWinAmount += profit;
         }
         else
         {
            g_dailyLossCount++;
            g_dailyLossAmount += profit;
         }
      }
      
      g_dailyProfit = g_dailyWinAmount + g_dailyLossAmount;
       if(EnableDebug) Print("📊 Daily Tracking Initialized: Today's P&L = $", DoubleToString(g_dailyProfit, 2), " (", g_dailyWinCount, "W/", g_dailyLossCount, "L)");
   }
}

//+------------------------------------------------------------------+
//| Check for closed positions and update daily stats                 |
//+------------------------------------------------------------------+
void CheckClosedPositions()
{
   static datetime lastCheck = 0;
   datetime now = TimeCurrent();
   
   if(now - lastCheck < 5) return;
   lastCheck = now;
   
   HistorySelect(0, now);
   int total = HistoryDealsTotal();
   
   for(int i = total - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(magic != MagicNumber) continue;
      
      datetime closeTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      if(closeTime < g_dailyStartTime) continue;
      
      string key = "closed_" + IntegerToString((int)ticket);
      if(GlobalVariableCheck(key)) continue;
      
      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      if(profit == 0) continue;
      
      GlobalVariableSet(key, 1);
      
      if(profit > 0)
      {
         g_dailyWinCount++;
         g_dailyWinAmount += profit;
         g_winTrades++;
         Print("✅ WIN: +$", DoubleToString(profit, 2), " | Daily: ", g_dailyWinCount, "W/", g_dailyLossCount, "L");
      }
      else
      {
         g_dailyLossCount++;
         g_dailyLossAmount += profit;
         g_lossTrades++;
         Print("❌ LOSS: -$", DoubleToString(MathAbs(profit), 2), " | Daily: ", g_dailyWinCount, "W/", g_dailyLossCount, "L");
      }
      
      g_totalTrades++;
      g_dailyProfit = g_dailyWinAmount + g_dailyLossAmount;
   }
}

//+------------------------------------------------------------------+
//| Initialize All-Time stats from history                            |
//+------------------------------------------------------------------+
void InitAllTimeStats()
{
   HistorySelect(0, TimeCurrent());
   int total = HistoryDealsTotal();
   
   g_totalTrades = 0;
   g_winTrades = 0;
   g_lossTrades = 0;
   
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      
      long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
      if(magic != MagicNumber) continue;
      
      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
      if(profit == 0) continue;
      
      g_totalTrades++;
      if(profit > 0) g_winTrades++;
      else g_lossTrades++;
   }
   
    Print("📊 All-Time Stats Loaded: ", g_totalTrades, " Trades (", g_winTrades, "W/", g_lossTrades, "L)");
}

// v23.0: OnTradeTransaction - Catch TP/SL/Close events
void OnTradeTransaction(const MqlTradeTransaction &trans,
                         const MqlTradeRequest &request,
                         const MqlTradeResult &result)
{
    // Only handle DEAL_ADD (trade executed/closed)
    if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
    
    // Only handle deal that closes position (DEAL_ENTRY_OUT)
    if(!HistoryDealSelect(trans.deal)) return;
    
    ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
    if(entry != DEAL_ENTRY_OUT) return;
    
    // Only handle our magic number
    long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
    if(magic != MagicNumber) return;
    
    string symbol = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
    if(symbol != g_symbol) return;
    
    double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
    double commission = HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
    double swap = HistoryDealGetDouble(trans.deal, DEAL_SWAP);
    double totalPnL = profit + commission + swap;
    
    double price = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
    ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(trans.deal, DEAL_REASON);
    
    // Determine exit status
    string exitStatus = "CLOSED";
    if(reason == DEAL_REASON_TP)
        exitStatus = "TP";
    else if(reason == DEAL_REASON_SL)
        exitStatus = "SL";
    else if(reason == DEAL_REASON_EXPERT)
        exitStatus = "CLOSE";
    
    // v23.1: Stateless Tracking - Extract signal_id from original Deal Comment
    string signalId = "UNKNOWN";
    string mode = "UNKNOWN";
    
    long posID = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
    if(HistorySelectByPosition(posID))
    {
        int totalDeals = HistoryDealsTotal();
        for(int i=0; i<totalDeals; i++)
        {
            ulong dealTicket = HistoryDealGetTicket(i);
            if(HistoryDealGetInteger(dealTicket, DEAL_ENTRY) == DEAL_ENTRY_IN)
            {
                string originalComment = HistoryDealGetString(dealTicket, DEAL_COMMENT);
                signalId = originalComment;
                mode = GetModeFromComment(originalComment);
                break;
            }
        }
    }
    
    Print("📊 Trade Closed: ", signalId, " -> ", exitStatus, " | PnL: $", DoubleToString(totalPnL, 2));
    
    // Send confirmation to Python
    SendTradeConfirm(signalId, exitStatus, price, totalPnL, mode);
    
    // Reset last trade (legacy guard)
    g_lastTrade.is_reset = true;
}

//--- File End









