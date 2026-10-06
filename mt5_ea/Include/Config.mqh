//+------------------------------------------------------------------+
//| Config.mqh - inputs, enums and shared structures for PRO_MAX_EA   |
//| Defaults are conservative: PAPER mode, live trading disabled.     |
//+------------------------------------------------------------------+

#ifndef PMX_CONFIG_MQH
#define PMX_CONFIG_MQH

#define PMX_EA_VERSION        "1.0.0"
#define PMX_COMMENT_PREFIX    "PMX|"
#define PMX_LIVE_PHRASE       "I_UNDERSTAND_LIVE_TRADING_RISKS"
#define PMX_MAX_SYMBOLS       16

enum ENUM_PMX_MODE
  {
   PMX_MODE_PAPER = 0, // PAPER - never sends orders
   PMX_MODE_DEMO  = 1, // DEMO  - orders only on a DEMO account
   PMX_MODE_LIVE  = 2  // LIVE  - requires every safeguard
  };

//--- Mode & safety
input group "=== Mode & safety ==="
input ENUM_PMX_MODE InpTradingMode          = PMX_MODE_PAPER; // Trading mode
input bool          InpEnableLiveTrading    = false;          // Enable LIVE trading (also needs confirmation phrase)
input string        InpLiveConfirmation     = "";             // Type I_UNDERSTAND_LIVE_TRADING_RISKS for LIVE
input bool          InpLocalKillSwitch      = false;          // Local emergency stop (no new trades)
input bool          InpCloseOnKillSwitch    = false;          // Close own positions when kill switch activates
input bool          InpManualResetKillSwitch= false;          // Set true once to clear a persisted kill switch
input bool          InpManualResetDrawdown  = false;          // Set true once to clear a drawdown lock
input bool          InpManualResetDailyLoss = false;          // Set true once to clear today's daily-loss lock
input bool          InpManageOnlyOwnTrades  = true;           // Never touch manual / other EA trades

//--- Backend
input group "=== Backend connection ==="
input string InpBackendUrl        = "";      // e.g. https://your-service.onrender.com (also add it to WebRequest list)
input string InpApiKeyId          = "";      // EA_API_KEY_ID from the backend .env
input string InpHmacSecret        = "";      // EA_HMAC_SECRET from the backend .env (never share)
input int    InpHttpTimeoutMs     = 5000;    // HTTP timeout (ms)
input int    InpHeartbeatSec      = 15;      // Heartbeat interval (s)
input int    InpAccountSyncSec    = 30;      // Account / position sync interval (s)
input int    InpBackendOfflineSec = 90;      // Backend considered offline after (s) without success

//--- Symbols
input group "=== Symbols (broker names are NOT assumed) ==="
input string          InpSymbols          = "";          // LOGICAL=BROKER list, e.g. XAUUSD=XAUUSD,OIL=USOIL,US30=US30
input ENUM_TIMEFRAMES InpSignalTimeframe  = PERIOD_M15;  // Decision timeframe (one request per closed candle)
input int             InpBarsToSend       = 300;         // Closed bars sent per decision request (60..2000)
input int             InpMagicGold        = 10001;       // Magic base for XAUUSD
input int             InpMagicOil         = 10002;       // Magic base for OIL
input int             InpMagicUS30        = 10003;       // Magic base for US30
input int             InpMagicOther       = 10099;       // Magic base for other symbols

//--- Local risk limits
input group "=== Local risk limits (enforced even if backend says otherwise) ==="
input double InpMaxRiskPerTradePct   = 0.5;   // Max risk per trade (% equity)
input double InpMaxDailyLossPct      = 2.0;   // Max daily loss (% of day-start equity)
input double InpMaxDrawdownPct       = 8.0;   // Max drawdown from peak (%)
input double InpMaxPortfolioRiskPct  = 3.0;   // Max open risk, all positions (% equity)
input double InpMaxSymbolRiskPct     = 1.5;   // Max open risk per symbol (% equity)
input double InpMaxMarginUsagePct    = 40.0;  // Max margin usage (% equity)
input int    InpMaxPositions         = 8;     // Max own open positions (secondary limit)
input int    InpMaxPositionsPerSymbol= 3;     // Max own positions per symbol
input int    InpMaxSpreadPoints      = 0;     // Max spread in points (0 = ATR ratio only)
input double InpMaxSpreadAtrRatio    = 0.15;  // Max spread / ATR
input int    InpMaxDeviationPoints   = 30;    // Max slippage accepted by the broker (points)
input double InpMaxEntryDriftAtr     = 0.25;  // Reject if price moved more than this x ATR since the signal
input int    InpMaxClockSkewSec      = 30;    // Max |backend time - local GMT| allowed

//--- Trade management
input group "=== Trade management (runs locally, also when backend is offline) ==="
input double          InpTightenTriggerR   = 0.8;   // Tighten stop at +R
input double          InpTightenToR        = -0.5;  // ...to this R (negative = still below entry)
input double          InpBETriggerR        = 1.0;   // Break-even trigger (R)
input double          InpBEOffsetR         = 0.05;  // Break-even offset (R, covers costs)
input double          InpTrailStartR       = 1.0;   // Start ATR trailing at +R
input double          InpTrailAtrMult      = 1.0;   // Trailing distance (x ATR)
input double          InpTP1R              = 1.0;   // Partial take-profit at +R
input double          InpTP1CloseFraction  = 0.5;   // Fraction closed at TP1 (0 disables)
input ENUM_TIMEFRAMES InpAtrTimeframe      = PERIOD_M15; // ATR timeframe for trailing
input int             InpAtrPeriod         = 14;    // ATR period
input double          InpEmergencySlAtr    = 3.0;   // Emergency stop distance if a position has no SL (x ATR)

//--- News
input group "=== News (MT5 built-in economic calendar) ==="
input bool InpUseLocalNewsFilter = true;  // Second, independent news check
input int  InpNewsBeforeMin      = 15;    // Block minutes before HIGH impact events
input int  InpNewsAfterMin       = 20;    // Block minutes after HIGH impact events
input bool InpNewsFailSafe       = true;  // Block trading if the calendar cannot be read

//--- Logging
input group "=== Logging ==="
input bool InpLogToFile = true;   // Also write MQL5/Files/PRO_MAX_EA_<login>.log
input bool InpVerbose   = false;  // Verbose logs

//--- Shared structures ------------------------------------------------
struct SymbolEntry
  {
   string   logical;
   string   broker;
   int      magicBase;
   datetime lastBarTime;
   int      atrHandle;
   bool     valid;
  };

struct SignalData
  {
   string   signalId;
   string   symbol;
   string   action;          // BUY / SELL / NO_TRADE
   string   strategy;
   string   tradingMode;     // PAPER / DEMO / LIVE (backend view)
   string   reason;
   bool     riskApproved;
   bool     paper;
   double   confidence;
   double   riskPercent;
   double   volume;
   double   entryPrice;
   double   stopLoss;
   double   takeProfit;
   double   takeProfit2;
   double   atr;
   long     createdAt;
   long     expiresAt;
  };

#endif
