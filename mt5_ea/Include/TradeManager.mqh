//+------------------------------------------------------------------+
//| TradeManager.mqh - final local validation + order execution       |
//| The backend cannot override these checks. ANY failure => no trade.|
//+------------------------------------------------------------------+
#ifndef PMX_TRADEMANAGER_MQH
#define PMX_TRADEMANAGER_MQH

#include <Trade\Trade.mqh>
#include "Config.mqh"
#include "Logger.mqh"
#include "RiskManager.mqh"
#include "PositionManager.mqh"
#include "PortfolioManager.mqh"
#include "SessionManager.mqh"
#include "NewsManager.mqh"

class CTradeManager
  {
private:
   CTrade            *m_trade;
   CRiskManager      *m_risk;
   CPositionManager  *m_positions;
   CPortfolioManager *m_portfolio;
   CNewsManager      *m_news;
   string             m_processed[];  // recent signal ids (in-memory; history check covers restarts)

   bool AlreadyProcessed(const string id)
     {
      for(int i = 0; i < ArraySize(m_processed); i++)
         if(m_processed[i] == id)
            return true;
      return false;
     }
   void MarkProcessed(const string id)
     {
      int n = ArraySize(m_processed);
      if(n >= 200)
        {
         for(int i = 1; i < n; i++)
            m_processed[i - 1] = m_processed[i];
         n--;
        }
      ArrayResize(m_processed, n + 1);
      m_processed[n] = id;
     }

public:
   void Init(CTrade *trade, CRiskManager *risk, CPositionManager *positions, CPortfolioManager *portfolio,
             CNewsManager *news)
     {
      m_trade = trade;
      m_risk = risk;
      m_positions = positions;
      m_portfolio = portfolio;
      m_news = news;
     }

   // Is the EA allowed to send real orders for this signal at all?
   static bool ModeAllowsOrders(const SignalData &s, string &reason)
     {
      long accMode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
      if(InpTradingMode == PMX_MODE_PAPER || s.paper || s.tradingMode == "PAPER")
        {
         reason = "PAPER_MODE";
         return false;
        }
      if(InpTradingMode == PMX_MODE_DEMO)
        {
         if(accMode != ACCOUNT_TRADE_MODE_DEMO)
           {
            reason = "DEMO_MODE_BUT_ACCOUNT_IS_NOT_DEMO";
            return false;
           }
         return true;
        }
      // LIVE: every safeguard must be set, and the backend must also be in LIVE.
      if(!InpEnableLiveTrading || InpLiveConfirmation != PMX_LIVE_PHRASE)
        {
         reason = "LIVE_SAFEGUARDS_NOT_SET";
         return false;
        }
      if(accMode != ACCOUNT_TRADE_MODE_DEMO && s.tradingMode != "LIVE")
        {
         reason = "BACKEND_NOT_IN_LIVE_MODE";
         return false;
        }
      return true;
     }

   // Full pre-trade checklist; returns "" when the order was sent successfully.
   string Execute(const SignalData &s, const string broker, const string logical, const int magicBase,
                  const double atr, const bool backendOnline)
     {
      string reason = "";
      bool isBuy = s.action == "BUY";
      // 1. signal sanity / freshness / duplicates
      if(s.action != "BUY" && s.action != "SELL")
         return "NO_TRADE";
      if(!s.riskApproved)
         return "NOT_RISK_APPROVED";
      if(s.symbol != broker)
         return "SYMBOL_MISMATCH";
      long now = (long)TimeGMT();
      if(s.expiresAt <= now)
         return "SIGNAL_EXPIRED";
      if(MathAbs((double)(now - s.createdAt)) > InpMaxClockSkewSec + 60)
         return "SIGNAL_CLOCK_SKEW";
      if(StringLen(s.signalId) < 16 || StringLen(s.signalId) > 27)
         return "INVALID_SIGNAL_ID";
      if(AlreadyProcessed(s.signalId) || m_positions.SignalAlreadyExecuted(s.signalId))
         return "DUPLICATE_SIGNAL";
      // 2. mode / permissions
      if(!ModeAllowsOrders(s, reason))
         return reason;
      if(!backendOnline)
         return "BACKEND_OFFLINE";
      if(!TerminalInfoInteger(TERMINAL_CONNECTED))
         return "TERMINAL_DISCONNECTED";
      if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
         return "ALGO_TRADING_DISABLED";
      if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) || !AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
         return "ACCOUNT_TRADING_NOT_ALLOWED";
      if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
         return "ACCOUNT_NOT_HEDGING";
      // 3. local risk state
      if(!m_risk.CanOpenNew(reason))
         return reason;
      // 4. market / symbol
      long tradeMode = SymbolInfoInteger(broker, SYMBOL_TRADE_MODE);
      if(tradeMode == SYMBOL_TRADE_MODE_DISABLED || tradeMode == SYMBOL_TRADE_MODE_CLOSEONLY ||
         (isBuy && tradeMode == SYMBOL_TRADE_MODE_SHORTONLY) || (!isBuy && tradeMode == SYMBOL_TRADE_MODE_LONGONLY))
         return "SYMBOL_NOT_TRADABLE";
      if(!CSessionManager::BrokerSessionOpen(broker))
         return "MARKET_CLOSED";
      // 5. news (independent local check)
      if(m_news.Blocked(logical, logical == "OIL", reason))
         return reason;
      // 6. spread & price drift
      MqlTick tick;
      if(!SymbolInfoTick(broker, tick) || tick.bid <= 0 || tick.ask <= 0)
         return "NO_QUOTE";
      double point = SymbolInfoDouble(broker, SYMBOL_POINT);
      int digits = (int)SymbolInfoInteger(broker, SYMBOL_DIGITS);
      double spreadPts = (tick.ask - tick.bid) / point;
      if(InpMaxSpreadPoints > 0 && spreadPts > InpMaxSpreadPoints)
         return "SPREAD_TOO_HIGH";
      if(atr > 0 && (tick.ask - tick.bid) / atr > InpMaxSpreadAtrRatio)
         return "SPREAD_TOO_HIGH";
      double price = isBuy ? tick.ask : tick.bid;
      if(atr > 0 && MathAbs(price - s.entryPrice) > InpMaxEntryDriftAtr * atr)
         return "PRICE_DRIFTED_SINCE_SIGNAL";
      // 7. stop loss / take profit validity
      double sl = NormalizeDouble(s.stopLoss, digits);
      double tp = NormalizeDouble(s.takeProfit, digits);
      long stopsLevel = SymbolInfoInteger(broker, SYMBOL_TRADE_STOPS_LEVEL);
      double minGap = MathMax((double)stopsLevel * point, point);
      double closeSide = isBuy ? tick.bid : tick.ask;
      if(sl <= 0)
         return "STOP_LOSS_REQUIRED";
      if(isBuy ? (closeSide - sl < minGap) : (sl - closeSide < minGap))
         return "INVALID_SL";
      if(tp > 0 && (isBuy ? (tp - closeSide < minGap) : (closeSide - tp < minGap)))
         return "INVALID_TP";
      // 8. position size: min(backend volume, local risk-based volume)
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double riskPct = MathMin(InpMaxRiskPerTradePct, s.riskPercent > 0 ? s.riskPercent : InpMaxRiskPerTradePct);
      double localVol = CRiskManager::VolumeForRisk(broker, isBuy, price, sl, equity * riskPct / 100.0, reason);
      if(localVol <= 0)
         return reason;
      double volume = CRiskManager::NormalizeVolume(broker, MathMin(localVol, s.volume));
      if(volume <= 0)
         return "INVALID_VOLUME";
      double newRisk = 0;
      if(!CRiskManager::LossMoney(broker, isBuy, volume, price, sl, newRisk))
         return "RISK_CALC_FAILED";
      if(newRisk / equity * 100.0 > InpMaxRiskPerTradePct * 1.0001)
         return "TRADE_RISK_ABOVE_LIMIT";
      // 9. portfolio limits & margin
      if(!m_portfolio.Allows(m_positions, broker, isBuy, newRisk, reason))
         return reason;
      if(!CPortfolioManager::MarginAllows(broker, isBuy, volume, price, reason))
         return reason;
      // 10. send
      ulong magic = (ulong)magicBase * 100 + (ulong)CPositionManager::StrategyCode(s.strategy);
      m_trade.SetExpertMagicNumber(magic);
      m_trade.SetDeviationInPoints((ulong)InpMaxDeviationPoints);
      m_trade.SetTypeFillingBySymbol(broker);
      string comment = PMX_COMMENT_PREFIX + s.signalId;
      MarkProcessed(s.signalId);  // mark BEFORE sending: a timeout must never cause a second order
      bool ok = isBuy ? m_trade.Buy(volume, broker, price, sl, tp, comment)
                      : m_trade.Sell(volume, broker, price, sl, tp, comment);
      uint rc = m_trade.ResultRetcode();
      if(!ok || (rc != TRADE_RETCODE_DONE && rc != TRADE_RETCODE_PLACED && rc != TRADE_RETCODE_DONE_PARTIAL))
         return "ORDER_FAILED_RETCODE_" + IntegerToString((int)rc) + "_" + m_trade.ResultRetcodeDescription();
      Log.Info("ORDER SENT " + s.action + " " + DoubleToString(volume, 2) + " " + broker + " @" +
               DoubleToString(m_trade.ResultPrice(), digits) + " sl=" + DoubleToString(sl, digits) + " tp=" +
               DoubleToString(tp, digits) + " risk=" + DoubleToString(newRisk, 2) + " signal=" + s.signalId);
      return "";
     }

   // Close every own position (kill switch with close policy).
   int CloseAllOwn()
     {
      PmxPosition pos[];
      int n = m_positions.Collect(pos);
      int closed = 0;
      for(int i = 0; i < n; i++)
         if(m_trade.PositionClose(pos[i].ticket, (ulong)InpMaxDeviationPoints))
            closed++;
      return closed;
     }
  };

#endif
