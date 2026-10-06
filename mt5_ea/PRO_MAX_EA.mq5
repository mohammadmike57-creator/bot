//+------------------------------------------------------------------+
//|                                                   PRO_MAX_EA.mq5 |
//|  Multi-symbol execution agent for an MT5 HEDGING account.        |
//|                                                                  |
//|  - Asks the Python backend for a decision ONCE per closed candle |
//|    per symbol (never "because a tick arrived").                  |
//|  - Re-validates every signal locally (mode, permissions, risk,   |
//|    spread, news, market hours, SL/TP, volume, margin, freshness, |
//|    duplicates) - the backend cannot override local safety.       |
//|  - Manages open positions locally (emergency SL, tighten,        |
//|    break-even, partial TP, ATR trailing) even if the backend is  |
//|    offline.                                                      |
//|  - Defaults to PAPER mode: no orders are sent.                   |
//+------------------------------------------------------------------+
#property copyright "trading-ai-platform"
#property version   "1.00"
#property description "PRO_MAX_EA - risk-first execution agent (PAPER mode by default)"

#include <Trade\Trade.mqh>
#include "Include\Config.mqh"
#include "Include\Logger.mqh"
#include "Include\Json.mqh"
#include "Include\Security.mqh"
#include "Include\SignalClient.mqh"
#include "Include\SessionManager.mqh"
#include "Include\NewsManager.mqh"
#include "Include\RiskManager.mqh"
#include "Include\PositionManager.mqh"
#include "Include\PortfolioManager.mqh"
#include "Include\TrailingStop.mqh"
#include "Include\TradeManager.mqh"

//--- services
CTrade            g_trade;
CSignalClient     g_client;
CNewsManager      g_news;
CRiskManager      g_risk;
CPositionManager  g_positions;
CPortfolioManager g_portfolio;
CTrailingStop     g_trail;
CTradeManager     g_tm;

//--- state
SymbolEntry g_symbols[];
datetime    g_lastHeartbeat = 0;
datetime    g_lastSync      = 0;
datetime    g_lastStatus    = 0;
bool        g_backendKill   = false;
bool        g_backendPaused = false;
bool        g_backendSafe   = false;
bool        g_closeRequested= false;
bool        g_killCloseDone = false;
string      g_backendMode   = "UNKNOWN";
string      g_backendReason = "";
string      g_tfString      = "M15";
string      g_lastReqSymbol = "";
double      g_lastReqPrice  = 0;

//--- outbox for trade events (retried until the backend accepts them)
string g_outPath[];
string g_outBody[];
int    g_outTries[];

#define PMX_KILL_BUTTON "PMX_EMERGENCY_STOP"

//+------------------------------------------------------------------+
//| MT5 bar / tick / deal times are TRADE-SERVER time, the backend    |
//| works in UTC. Offset = server - UTC, rounded to 15 minutes.       |
//| (Bars older than the broker's last DST switch may be 1h off; the |
//| backend only uses the last few days of bars.)                    |
//+------------------------------------------------------------------+
long ServerUtcOffset()
  {
   long diff = (long)TimeTradeServer() - (long)TimeGMT();
   return (long)MathRound(diff / 900.0) * 900;
  }

long ToUtc(const datetime serverTime)
  {
   return (long)serverTime - ServerUtcOffset();
  }

string ModeName()
  {
   if(InpTradingMode == PMX_MODE_LIVE)
      return "LIVE";
   if(InpTradingMode == PMX_MODE_DEMO)
      return "DEMO";
   return "PAPER";
  }

bool TimeframeToString(const ENUM_TIMEFRAMES tf, string &out)
  {
   switch(tf)
     {
      case PERIOD_M1:  out = "M1";  return true;
      case PERIOD_M5:  out = "M5";  return true;
      case PERIOD_M15: out = "M15"; return true;
      case PERIOD_M30: out = "M30"; return true;
      case PERIOD_H1:  out = "H1";  return true;
      case PERIOD_H4:  out = "H4";  return true;
      default:         return false;
     }
  }

int MagicBaseFor(const string logical)
  {
   if(logical == "XAUUSD") return InpMagicGold;
   if(logical == "OIL")    return InpMagicOil;
   if(logical == "US30")   return InpMagicUS30;
   return InpMagicOther;
  }

string SymbolTradeModeName(const string sym)
  {
   long m = SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
   if(m == SYMBOL_TRADE_MODE_DISABLED)  return "DISABLED";
   if(m == SYMBOL_TRADE_MODE_LONGONLY)  return "LONGONLY";
   if(m == SYMBOL_TRADE_MODE_SHORTONLY) return "SHORTONLY";
   if(m == SYMBOL_TRADE_MODE_CLOSEONLY) return "CLOSEONLY";
   return "FULL";
  }

double CurrentAtr(const int idx)
  {
   if(idx < 0 || g_symbols[idx].atrHandle == INVALID_HANDLE)
      return 0;
   double buf[];
   if(CopyBuffer(g_symbols[idx].atrHandle, 0, 1, 1, buf) != 1)
      return 0;
   return buf[0];
  }

int SymbolIndex(const string broker)
  {
   for(int i = 0; i < ArraySize(g_symbols); i++)
      if(g_symbols[i].broker == broker)
         return i;
   return -1;
  }

//--- Helps the user find the broker's real symbol names (they are never assumed).
void SuggestSymbols()
  {
   string hints = "";
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
     {
      string name = SymbolName(i, false);
      string u = name;
      StringToUpper(u);
      if(StringFind(u, "XAU") >= 0 || StringFind(u, "GOLD") >= 0 || StringFind(u, "OIL") >= 0 ||
         StringFind(u, "WTI") >= 0 || StringFind(u, "BRENT") >= 0 || StringFind(u, "US30") >= 0 ||
         StringFind(u, "DJ") >= 0 || StringFind(u, "WALL") >= 0)
         hints += name + " ";
     }
   Log.Info("Candidate broker symbols: " + (hints == "" ? "(none found - open Market Watch > Symbols)" : hints));
  }

bool ParseSymbols()
  {
   ArrayResize(g_symbols, 0);
   string items[];
   int n = StringSplit(InpSymbols, ',', items);
   for(int i = 0; i < n && ArraySize(g_symbols) < PMX_MAX_SYMBOLS; i++)
     {
      string pair[];
      string item = items[i];
      StringTrimLeft(item);
      StringTrimRight(item);
      if(StringSplit(item, '=', pair) != 2)
        {
         Log.Error("Bad InpSymbols entry '" + item + "' (expected LOGICAL=BROKER)");
         continue;
        }
      string logical = pair[0], broker = pair[1];
      StringTrimLeft(logical);
      StringTrimRight(logical);
      StringTrimLeft(broker);
      StringTrimRight(broker);
      StringToUpper(logical);
      bool custom = false;
      if(!SymbolExist(broker, custom) || !SymbolSelect(broker, true))
        {
         Log.Error("Broker symbol '" + broker + "' not found for " + logical);
         continue;
        }
      int k = ArraySize(g_symbols);
      ArrayResize(g_symbols, k + 1);
      g_symbols[k].logical = logical;
      g_symbols[k].broker = broker;
      g_symbols[k].magicBase = MagicBaseFor(logical);
      g_symbols[k].lastBarTime = iTime(broker, InpSignalTimeframe, 0);  // act only on the NEXT new candle
      g_symbols[k].atrHandle = iATR(broker, InpAtrTimeframe, InpAtrPeriod);
      g_symbols[k].valid = true;
      Log.Info("Symbol mapped " + logical + " -> " + broker + " magicBase=" + IntegerToString(g_symbols[k].magicBase) +
               " contract=" + DoubleToString(SymbolInfoDouble(broker, SYMBOL_TRADE_CONTRACT_SIZE), 2) +
               " vmin=" + DoubleToString(SymbolInfoDouble(broker, SYMBOL_VOLUME_MIN), 2) +
               " step=" + DoubleToString(SymbolInfoDouble(broker, SYMBOL_VOLUME_STEP), 2));
     }
   return ArraySize(g_symbols) > 0;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   Log.Init(InpLogToFile, InpVerbose);
   MathSrand((uint)(GetTickCount() ^ (uint)TimeLocal()));
   Log.Info("PRO_MAX_EA " + PMX_EA_VERSION + " starting, mode=" + ModeName());

   if(!PmxSecuritySelfTest())
     {
      Log.Error("HMAC-SHA256 self-test failed - refusing to run");
      return INIT_FAILED;
     }
   if(!TimeframeToString(InpSignalTimeframe, g_tfString))
     {
      Log.Error("InpSignalTimeframe must be M1, M5, M15, M30, H1 or H4");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpBarsToSend < 60 || InpBarsToSend > 2000)
     {
      Log.Error("InpBarsToSend must be within 60..2000");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpMaxRiskPerTradePct <= 0 || InpMaxRiskPerTradePct > InpMaxSymbolRiskPct ||
      InpMaxSymbolRiskPct > InpMaxPortfolioRiskPct)
     {
      Log.Error("Risk inputs inconsistent: need 0 < per-trade <= per-symbol <= portfolio");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(!ParseSymbols())
     {
      Log.Error("No valid symbols. Set InpSymbols, e.g. XAUUSD=<broker gold>,OIL=<broker oil>,US30=<broker dow>");
      SuggestSymbols();
      return INIT_PARAMETERS_INCORRECT;
     }

   long marginMode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(marginMode != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
      Log.Error("Account is NOT in hedging mode - orders will be refused (positions still managed)");
   long accMode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(InpTradingMode == PMX_MODE_DEMO && accMode != ACCOUNT_TRADE_MODE_DEMO)
      Log.Error("DEMO mode selected but this is NOT a demo account - orders will be refused");
   if(InpTradingMode == PMX_MODE_LIVE && (!InpEnableLiveTrading || InpLiveConfirmation != PMX_LIVE_PHRASE))
      Log.Error("LIVE selected without all safeguards - orders will be refused");
   if(InpTradingMode == PMX_MODE_PAPER)
      Log.Info("PAPER MODE: signals are simulated by the backend; this EA will NOT send orders");

   int bases[4];
   bases[0] = InpMagicGold;
   bases[1] = InpMagicOil;
   bases[2] = InpMagicUS30;
   bases[3] = InpMagicOther;
   g_positions.Init(bases);
   g_risk.Init();
   g_news.Init(InpUseLocalNewsFilter, InpNewsBeforeMin, InpNewsAfterMin, InpNewsFailSafe);
   g_trail.Init(GetPointer(g_trade));
   g_tm.Init(GetPointer(g_trade), GetPointer(g_risk), GetPointer(g_positions), GetPointer(g_portfolio),
             GetPointer(g_news));
   g_client.Init(InpBackendUrl, InpApiKeyId, InpHmacSecret, InpHttpTimeoutMs);
   if(!g_client.Configured())
      Log.Warn("Backend not configured (URL / key id / secret). No new trades; local management only.");

   CreateKillButton();
   EventSetTimer(1);
   SendAccount();
   SendHeartbeat();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   for(int i = 0; i < ArraySize(g_symbols); i++)
      if(g_symbols[i].atrHandle != INVALID_HANDLE)
         IndicatorRelease(g_symbols[i].atrHandle);
   ObjectDelete(0, PMX_KILL_BUTTON);
   Comment("");
   Log.Info("PRO_MAX_EA stopped, reason=" + IntegerToString(reason));
  }

//+------------------------------------------------------------------+
//| Tick: manage this chart's symbol at tick resolution              |
//+------------------------------------------------------------------+
void OnTick()
  {
   ManagePositions(_Symbol);
  }

//+------------------------------------------------------------------+
//| Timer (1 s): risk state, management, heartbeat, sync, new bars   |
//+------------------------------------------------------------------+
void OnTimer()
  {
   g_risk.Update();
   if((g_risk.KillSwitch() && InpCloseOnKillSwitch) || g_closeRequested)
     {
      if(!g_killCloseDone)
        {
         int closed = g_tm.CloseAllOwn();
         Log.Error("EMERGENCY CLOSE: closed " + IntegerToString(closed) + " own positions");
         g_killCloseDone = true;
        }
     }
   ManagePositions("");

   datetime now = TimeGMT();
   if(now - g_lastHeartbeat >= InpHeartbeatSec)
      SendHeartbeat();
   if(now - g_lastSync >= InpAccountSyncSec)
     {
      SendAccount();
      SendPositions();
     }
   for(int i = 0; i < ArraySize(g_symbols); i++)
     {
      datetime bar = iTime(g_symbols[i].broker, InpSignalTimeframe, 0);
      if(bar > 0 && bar != g_symbols[i].lastBarTime)
        {
         g_symbols[i].lastBarTime = bar;
         RequestSignal(i);
        }
     }
   FlushOutbox();
   if(now - g_lastStatus >= 2)
     {
      UpdateStatus();
      g_lastStatus = now;
     }
  }

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == PMX_KILL_BUTTON)
     {
      g_risk.ActivateKillSwitch("chart EMERGENCY STOP button");
      if(InpCloseOnKillSwitch)
         g_tm.CloseAllOwn();
      ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_STATE, false);
      UpdateStatus();
     }
  }

//+------------------------------------------------------------------+
//| Local trade management (independent of the backend)              |
//+------------------------------------------------------------------+
void ManagePositions(const string onlySymbol)
  {
   PmxPosition pos[];
   int n = g_positions.Collect(pos);
   for(int i = 0; i < n; i++)
     {
      if(onlySymbol != "" && pos[i].symbol != onlySymbol)
         continue;
      g_trail.Manage(pos[i], CurrentAtr(SymbolIndex(pos[i].symbol)));
     }
  }

//+------------------------------------------------------------------+
//| Backend communication                                            |
//+------------------------------------------------------------------+
bool BackendOnline()
  {
   return g_client.IsOnline(InpBackendOfflineSec);
  }

void SendHeartbeat()
  {
   g_lastHeartbeat = TimeGMT();
   if(!g_client.Configured())
      return;
   string quotes = "[";
   for(int i = 0; i < ArraySize(g_symbols); i++)
     {
      MqlTick t;
      if(!SymbolInfoTick(g_symbols[i].broker, t) || t.bid <= 0 || t.ask < t.bid)
         continue;
      CJsonBuilder q;
      q.Str("symbol", g_symbols[i].broker);
      q.Num("bid", t.bid, 8);
      q.Num("ask", t.ask, 8);
      q.Num("spread_points", (t.ask - t.bid) / SymbolInfoDouble(g_symbols[i].broker, SYMBOL_POINT), 1);
      q.Int("time", t.time > 0 ? MathMax(0, ToUtc(t.time)) : 0);
      quotes += (StringLen(quotes) > 1 ? "," : "") + q.Build();
     }
   quotes += "]";
   PmxPosition pos[];
   int open = g_positions.Collect(pos);
   CJsonBuilder j;
   j.Str("account_login", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   j.Str("ea_version", PMX_EA_VERSION);
   j.Str("ea_mode", ModeName());
   j.Bool("terminal_connected", (bool)TerminalInfoInteger(TERMINAL_CONNECTED));
   j.Bool("trade_allowed", (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED));
   j.Bool("local_kill_switch", g_risk.KillSwitch());
   j.Int("open_positions", open);
   j.Raw("quotes", quotes);
   j.Num("latency_ms", (double)g_client.LastLatencyMs(), 0);
   string resp;
   int code = g_client.Post("/v1/heartbeat", j.Build(), resp);
   if(code != 200)
      return;
   CJsonFlat r;
   if(!r.Parse(resp))
      return;
   g_backendKill   = r.GetBool("kill_switch");
   g_backendPaused = r.GetBool("paused");
   g_backendSafe   = r.GetBool("safe_mode");
   g_backendMode   = r.GetString("trading_mode", "UNKNOWN");
   g_backendReason = r.GetString("reason");
   if(r.GetBool("close_positions") && !g_closeRequested)
     {
      g_closeRequested = true;  // backend emergency policy: flatten own positions
      g_killCloseDone = false;
      Log.Error("Backend requested emergency close: " + g_backendReason);
     }
   long serverTime = 0;
   // Clock sanity: signatures and signal expiry depend on accurate UTC.
   string st = r.GetString("server_time");
   if(st != "")
     {
      string norm = st;
      StringReplace(norm, "-", ".");
      StringReplace(norm, "T", " ");
      serverTime = (long)StringToTime(StringSubstr(norm, 0, 19));
      if(serverTime > 0 && MathAbs((double)(serverTime - (long)TimeGMT())) > InpMaxClockSkewSec)
         Log.Warn("Clock skew vs backend > " + IntegerToString(InpMaxClockSkewSec) + "s - sync the Windows clock");
     }
  }

string SymbolSpecJson(const string sym)
  {
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double contract = SymbolInfoDouble(sym, SYMBOL_TRADE_CONTRACT_SIZE);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(tickValue <= 0 || tickSize <= 0 || vmin <= 0 || vmax <= 0 || step <= 0 || contract <= 0 || point <= 0)
     {
      Log.Warn("Symbol spec for " + sym + " incomplete (market closed or not subscribed?) - skipped");
      return "";
     }
   MqlTick t;
   SymbolInfoTick(sym, t);
   double marginPerLot = 0;
   if(t.ask > 0 && !OrderCalcMargin(ORDER_TYPE_BUY, sym, 1.0, t.ask, marginPerLot))
      marginPerLot = 0;
   CJsonBuilder s;
   s.Str("broker_symbol", sym);
   s.Int("digits", SymbolInfoInteger(sym, SYMBOL_DIGITS));
   s.Num("point", point, 10);
   s.Num("tick_size", tickSize, 10);
   s.Num("tick_value", tickValue, 10);
   s.Num("tick_value_loss", MathMax(0.0, SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS)), 10);
   s.Num("contract_size", contract, 4);
   s.Num("volume_min", vmin, 4);
   s.Num("volume_max", vmax, 4);
   s.Num("volume_step", step, 4);
   s.Int("stops_level", SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL));
   s.Int("freeze_level", SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL));
   s.Num("margin_per_lot", MathMax(0.0, marginPerLot), 4);
   s.Str("currency_profit", SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT));
   s.Str("currency_margin", SymbolInfoString(sym, SYMBOL_CURRENCY_MARGIN));
   s.Str("trade_mode", SymbolTradeModeName(sym));
   s.Num("swap_long", SymbolInfoDouble(sym, SYMBOL_SWAP_LONG), 4);
   s.Num("swap_short", SymbolInfoDouble(sym, SYMBOL_SWAP_SHORT), 4);
   s.Num("spread_points", (double)SymbolInfoInteger(sym, SYMBOL_SPREAD), 1);
   s.Num("bid", MathMax(0.0, t.bid), 8);
   s.Num("ask", MathMax(0.0, t.ask), 8);
   return s.Build();
  }

void SendAccount()
  {
   g_lastSync = TimeGMT();
   if(!g_client.Configured())
      return;
   long tm = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   long mm = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   string specs = "[";
   for(int i = 0; i < ArraySize(g_symbols); i++)
     {
      string js = SymbolSpecJson(g_symbols[i].broker);
      if(js == "")
         continue;
      specs += (StringLen(specs) > 1 ? "," : "") + js;
     }
   specs += "]";
   CJsonBuilder j;
   j.Str("login", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   j.Str("server", AccountInfoString(ACCOUNT_SERVER));
   j.Str("company", AccountInfoString(ACCOUNT_COMPANY));
   j.Str("currency", AccountInfoString(ACCOUNT_CURRENCY));
   j.Int("leverage", AccountInfoInteger(ACCOUNT_LEVERAGE));
   j.Str("trade_mode", tm == ACCOUNT_TRADE_MODE_DEMO ? "DEMO" : (tm == ACCOUNT_TRADE_MODE_CONTEST ? "CONTEST" : "REAL"));
   j.Str("margin_mode", mm == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING ? "HEDGING" :
         (mm == ACCOUNT_MARGIN_MODE_RETAIL_NETTING ? "NETTING" : "EXCHANGE"));
   j.Bool("trade_allowed", (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) && (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT));
   j.Num("balance", AccountInfoDouble(ACCOUNT_BALANCE), 2);
   j.Num("equity", AccountInfoDouble(ACCOUNT_EQUITY), 2);
   j.Num("margin", AccountInfoDouble(ACCOUNT_MARGIN), 2);
   j.Num("free_margin", AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2);
   j.Num("margin_level", MathMax(0.0, AccountInfoDouble(ACCOUNT_MARGIN_LEVEL)), 2);
   j.Raw("symbols", specs);
   string resp;
   int code = g_client.Post("/v1/account", j.Build(), resp);
   if(code == 200 && StringFind(resp, "\"warnings\":[]") < 0)
      Log.Warn("backend account warnings: " + StringSubstr(resp, 0, 300));
  }

void SendPositions()
  {
   if(!g_client.Configured())
      return;
   PmxPosition pos[];
   int n = g_positions.Collect(pos);
   string arr = "[";
   for(int i = 0; i < n; i++)
     {
      if(pos[i].current <= 0 || pos[i].entry <= 0 || pos[i].volume <= 0)
         continue;
      CJsonBuilder p;
      p.Str("ticket", IntegerToString((long)pos[i].ticket));
      p.Str("symbol", pos[i].symbol);
      p.Int("magic", pos[i].magic);
      p.Str("direction", pos[i].isBuy ? "BUY" : "SELL");
      p.Num("volume", pos[i].volume, 4);
      p.Num("entry_price", pos[i].entry, 8);
      p.Num("current_price", pos[i].current, 8);
      p.Num("sl", pos[i].sl, 8);
      p.Num("tp", pos[i].tp, 8);
      p.Num("profit", pos[i].profit, 2);
      p.Num("swap", pos[i].swap, 2);
      p.Num("commission", 0, 2);
      p.Int("opened_at", ToUtc(pos[i].opened));
      p.Str("comment", StringSubstr(pos[i].comment, 0, 64));
      arr += (StringLen(arr) > 1 ? "," : "") + p.Build();
     }
   arr += "]";
   CJsonBuilder j;
   j.Str("account_login", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   j.Raw("positions", arr);
   string resp;
   g_client.Post("/v1/position", j.Build(), resp);
  }

//+------------------------------------------------------------------+
//| One decision per closed candle per symbol                        |
//+------------------------------------------------------------------+
void RequestSignal(const int idx)
  {
   string sym = g_symbols[idx].broker;
   string logical = g_symbols[idx].logical;
   string reason = "";
   if(!g_client.Configured())
      return;
   if(!g_risk.CanOpenNew(reason))
     {
      Log.Decision(logical, "REJECT", reason, "STAGE=LOCAL_PRECHECK");
      return;
     }
   if(g_backendKill || g_backendPaused || g_backendSafe)
     {
      Log.Decision(logical, "REJECT", g_backendKill ? "BACKEND_KILL_SWITCH" : (g_backendPaused ? "BACKEND_PAUSED" : "BACKEND_SAFE_MODE"),
                   "DETAIL=" + g_backendReason);
      return;
     }
   MqlRates rates[];
   int got = CopyRates(sym, InpSignalTimeframe, 1, InpBarsToSend, rates);  // closed bars only, oldest first
   if(got < 60)
     {
      Log.Decision(logical, "REJECT", "INSUFFICIENT_HISTORY", "BARS=" + IntegerToString(got));
      return;
     }
   MqlTick t;
   if(!SymbolInfoTick(sym, t) || t.bid <= 0 || t.ask < t.bid)
     {
      Log.Decision(logical, "REJECT", "NO_QUOTE");
      return;
     }
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   long offset = ServerUtcOffset();
   string bars = "[";
   for(int i = 0; i < got; i++)
     {
      if(i > 0)
         bars += ",";
      bars += "[" + IntegerToString((long)rates[i].time - offset) + "," + DoubleToString(rates[i].open, digits) + "," +
              DoubleToString(rates[i].high, digits) + "," + DoubleToString(rates[i].low, digits) + "," +
              DoubleToString(rates[i].close, digits) + "," + IntegerToString(rates[i].tick_volume) + "," +
              IntegerToString(rates[i].spread) + "]";
     }
   bars += "]";
   CJsonBuilder j;
   j.Str("account_login", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   j.Str("symbol", sym);
   j.Str("timeframe", g_tfString);
   j.Num("bid", t.bid, digits);
   j.Num("ask", t.ask, digits);
   j.Num("spread_points", (t.ask - t.bid) / SymbolInfoDouble(sym, SYMBOL_POINT), 1);
   j.Bool("market_open", CSessionManager::BrokerSessionOpen(sym));
   j.Str("ea_mode", ModeName());
   j.Raw("bars", bars);
   string resp;
   int code = g_client.Post("/v1/signal", j.Build(), resp);
   if(code != 200)
     {
      Log.Decision(logical, "REJECT", code == -1 ? "BACKEND_UNREACHABLE" : "BACKEND_HTTP_" + IntegerToString(code));
      return;
     }
   CJsonFlat r;
   if(!r.Parse(resp))
     {
      Log.Decision(logical, "REJECT", "BAD_BACKEND_RESPONSE");
      return;
     }
   SignalData s;
   s.signalId     = r.GetString("signal_id");
   s.symbol       = r.GetString("symbol");
   s.action       = r.GetString("action", "NO_TRADE");
   s.strategy     = r.GetString("strategy");
   s.tradingMode  = r.GetString("trading_mode", "PAPER");
   s.reason       = r.GetString("reason");
   s.riskApproved = r.GetBool("risk_approved");
   s.paper        = r.GetBool("paper", true);
   s.confidence   = r.GetDouble("confidence");
   s.riskPercent  = r.GetDouble("risk_percent");
   s.volume       = r.GetDouble("volume");
   s.entryPrice   = r.GetDouble("entry_price");
   s.stopLoss     = r.GetDouble("stop_loss");
   s.takeProfit   = r.GetDouble("take_profit");
   s.takeProfit2  = r.GetDouble("take_profit_2");
   s.atr          = r.GetDouble("atr");
   s.createdAt    = r.GetLong("created_at_epoch");
   s.expiresAt    = r.GetLong("expires_at_epoch");
   Log.Debug("backend decision: " + r.GetString("decision"));

   if(s.action != "BUY" && s.action != "SELL")
     {
      Log.Decision(logical, "NO_TRADE", s.reason, "SESSION=" + CSessionManager::PrimarySession(TimeGMT()));
      return;
     }
   if(s.paper || InpTradingMode == PMX_MODE_PAPER)
     {
      Log.Decision(logical, "PAPER_" + s.action, "SIMULATED_BY_BACKEND", "SIGNAL=" + s.signalId + " STRATEGY=" +
                   s.strategy + " VOL=" + DoubleToString(s.volume, 2) + " CONF=" + DoubleToString(s.confidence, 2));
      return;
     }
   double atr = CurrentAtr(idx);
   if(atr <= 0)
      atr = s.atr;
   g_lastReqSymbol = sym;
   g_lastReqPrice = s.action == "BUY" ? t.ask : t.bid;
   string why = g_tm.Execute(s, sym, logical, g_symbols[idx].magicBase, atr, BackendOnline());
   if(why != "")
     {
      Log.Decision(logical, "REJECT", why, "STAGE=EA_LOCAL_VALIDATION SIGNAL=" + s.signalId);
      ReportRejection(s, sym, why);
     }
   else
      Log.Decision(logical, s.action, "EXECUTED", "SIGNAL=" + s.signalId + " STRATEGY=" + s.strategy);
  }

void ReportRejection(const SignalData &s, const string sym, const string why)
  {
   CJsonBuilder j;
   j.Str("event_id", "rej-" + s.signalId);
   j.Str("account_login", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   j.Str("event", "REJECTED");
   j.Str("signal_id", s.signalId);
   j.Str("symbol", sym);
   j.Str("direction", s.action);
   j.Num("volume", 0, 2);
   j.Str("reason", StringSubstr(why, 0, 128));
   j.Int("time", (long)TimeGMT());
   Enqueue("/v1/trade", j.Build());
  }

//+------------------------------------------------------------------+
//| Fill / close reporting                                           |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0)
      return;
   if(!HistoryDealSelect(trans.deal))
      return;
   long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
   if(!g_positions.IsOwnMagic(magic))
      return;
   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   long dtype = HistoryDealGetInteger(trans.deal, DEAL_TYPE);
   string sym = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   long posId = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   double price = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   double vol = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   double pnl = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) + HistoryDealGetDouble(trans.deal, DEAL_SWAP) +
                HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
   string comment = HistoryDealGetString(trans.deal, DEAL_COMMENT);
   long reasonCode = HistoryDealGetInteger(trans.deal, DEAL_REASON);
   string evName = "";
   string direction = "";
   string why = "";
   string signalId = CPositionManager::SignalIdFromComment(comment);
   double requested = 0;
   if(entry == DEAL_ENTRY_IN)
     {
      evName = "OPENED";
      direction = dtype == DEAL_TYPE_BUY ? "BUY" : "SELL";
      requested = (sym == g_lastReqSymbol && g_lastReqPrice > 0) ? g_lastReqPrice : price;
     }
   else
      if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
        {
         direction = dtype == DEAL_TYPE_SELL ? "BUY" : "SELL";  // closing deal is opposite to the position
         evName = PositionSelectByTicket((ulong)posId) ? "PARTIAL" : "CLOSED";
         why = reasonCode == DEAL_REASON_SL ? "STOP_LOSS" : reasonCode == DEAL_REASON_TP ? "TAKE_PROFIT" :
               reasonCode == DEAL_REASON_SO ? "STOP_OUT" : reasonCode == DEAL_REASON_EXPERT ? "EA" : "MANUAL";
         if(evName == "CLOSED")
            CTrailingStop::Forget((ulong)posId);
        }
      else
         return;
   double sl = 0, tp = 0;
   if(PositionSelectByTicket((ulong)posId))
     {
      sl = PositionGetDouble(POSITION_SL);
      tp = PositionGetDouble(POSITION_TP);
      if(signalId == "")
         signalId = CPositionManager::SignalIdFromComment(PositionGetString(POSITION_COMMENT));
     }
   CJsonBuilder j;
   j.Str("event_id", "deal-" + IntegerToString((long)trans.deal));
   j.Str("account_login", IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)));
   j.Str("event", evName);
   j.Str("signal_id", signalId);
   j.Str("ticket", IntegerToString(posId));
   j.Str("symbol", sym);
   j.Int("magic", magic);
   j.Str("direction", direction);
   j.Num("volume", vol, 4);
   j.Num("requested_price", requested, 8);
   j.Num("executed_price", price, 8);
   j.Num("sl", sl, 8);
   j.Num("tp", tp, 8);
   j.Num("profit", pnl, 2);
   j.Str("reason", why);
   j.Int("time", ToUtc((datetime)HistoryDealGetInteger(trans.deal, DEAL_TIME)));
   Enqueue("/v1/trade", j.Build());
   FlushOutbox();
  }

void Enqueue(const string path, const string body)
  {
   int n = ArraySize(g_outPath);
   if(n >= 500)
     {
      Log.Warn("outbox full - dropping oldest event (position sync will reconcile)");
      for(int i = 1; i < n; i++)
        {
         g_outPath[i - 1] = g_outPath[i];
         g_outBody[i - 1] = g_outBody[i];
         g_outTries[i - 1] = g_outTries[i];
        }
      n--;
     }
   ArrayResize(g_outPath, n + 1);
   ArrayResize(g_outBody, n + 1);
   ArrayResize(g_outTries, n + 1);
   g_outPath[n] = path;
   g_outBody[n] = body;
   g_outTries[n] = 0;
  }

void FlushOutbox()
  {
   if(!g_client.Configured())
      return;
   int i = 0;
   int budget = 5;  // keep the timer responsive
   while(i < ArraySize(g_outPath) && budget-- > 0)
     {
      string resp;
      int code = g_client.Post(g_outPath[i], g_outBody[i], resp);
      g_outTries[i]++;
      bool done = (code >= 200 && code < 300) || (code >= 400 && code < 500 && code != 401 && code != 409 && code != 429) ||
                  g_outTries[i] > 50;
      if(!done)
        {
         i++;
         if(code == -1)
            break;  // backend unreachable - retry later
         continue;
        }
      int n = ArraySize(g_outPath);
      for(int k = i + 1; k < n; k++)
        {
         g_outPath[k - 1] = g_outPath[k];
         g_outBody[k - 1] = g_outBody[k];
         g_outTries[k - 1] = g_outTries[k];
        }
      ArrayResize(g_outPath, n - 1);
      ArrayResize(g_outBody, n - 1);
      ArrayResize(g_outTries, n - 1);
     }
  }

//+------------------------------------------------------------------+
//| Chart UI                                                         |
//+------------------------------------------------------------------+
void CreateKillButton()
  {
   if(ObjectFind(0, PMX_KILL_BUTTON) < 0)
      ObjectCreate(0, PMX_KILL_BUTTON, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_XDISTANCE, 170);
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_YDISTANCE, 20);
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_XSIZE, 160);
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_YSIZE, 30);
   ObjectSetString(0, PMX_KILL_BUTTON, OBJPROP_TEXT, "EMERGENCY STOP");
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_BGCOLOR, clrFireBrick);
   ObjectSetInteger(0, PMX_KILL_BUTTON, OBJPROP_COLOR, clrWhite);
  }

void UpdateStatus()
  {
   string mode = ModeName();
   string s = "PRO_MAX_EA " + PMX_EA_VERSION + "\n";
   s += "MODE: " + mode + (mode == "PAPER" ? "  (no orders are sent)" : "") + "   backend mode: " + g_backendMode + "\n";
   s += "BACKEND: " + (g_client.Configured() ? (BackendOnline() ? "ONLINE" : "OFFLINE (no new trades)") : "NOT CONFIGURED") +
        "   latency " + IntegerToString((int)g_client.LastLatencyMs()) + " ms\n";
   s += "SESSION: " + CSessionManager::PrimarySession(TimeGMT()) + "\n";
   s += "DAILY LOSS: " + DoubleToString(g_risk.DailyLossPct(), 2) + "% / " + DoubleToString(InpMaxDailyLossPct, 2) +
        "%   DRAWDOWN: " + DoubleToString(g_risk.DrawdownPct(), 2) + "% / " + DoubleToString(InpMaxDrawdownPct, 2) + "%\n";
   s += "LOCKS: kill=" + (g_risk.KillSwitch() ? "ON" : "off") + " backendKill=" + (g_backendKill ? "ON" : "off") +
        " daily=" + (g_risk.DailyLocked() ? "ON" : "off") + " drawdown=" + (g_risk.DrawdownLocked() ? "ON" : "off") +
        " paused=" + (g_backendPaused ? "ON" : "off") + " safe=" + (g_backendSafe ? "ON" : "off") + "\n";
   s += "OUTBOX: " + IntegerToString(ArraySize(g_outPath));
   Comment(s);
  }
//+------------------------------------------------------------------+
