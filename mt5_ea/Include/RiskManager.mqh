//+------------------------------------------------------------------+
//| RiskManager.mqh - local, independent risk limits                  |
//| State is persisted in terminal Global Variables so restarting the |
//| EA or the terminal never resets daily loss or drawdown.           |
//+------------------------------------------------------------------+
#ifndef PMX_RISKMANAGER_MQH
#define PMX_RISKMANAGER_MQH

#include "Logger.mqh"
#include "SessionManager.mqh"

class CRiskManager
  {
private:
   string m_prefix;
   long   m_day;
   double m_dayStartEquity;
   double m_peakEquity;
   bool   m_dailyLocked;
   bool   m_ddLocked;
   bool   m_killSwitch;
   double m_dailyLossPct;
   double m_drawdownPct;

   string Gv(const string name) { return m_prefix + name; }
   double Load(const string name, const double def)
     {
      return GlobalVariableCheck(Gv(name)) ? GlobalVariableGet(Gv(name)) : def;
     }
   void   Save(const string name, const double v) { GlobalVariableSet(Gv(name), v); }

public:
   void Init()
     {
      m_prefix = "PMX_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "_";
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      m_day            = (long)Load("DAY", 0);
      m_dayStartEquity = Load("DAYEQ", equity);
      m_peakEquity     = Load("PEAK", equity);
      m_dailyLocked    = Load("DLLOCK", 0) > 0.5;
      m_ddLocked       = Load("DDLOCK", 0) > 0.5;
      m_killSwitch     = Load("KILL", 0) > 0.5;

      if(InpManualResetKillSwitch && m_killSwitch)
        {
         m_killSwitch = false;
         Log.Warn("MANUAL RESET: local kill switch cleared");
        }
      if(InpManualResetDrawdown && m_ddLocked)
        {
         m_ddLocked = false;
         m_peakEquity = equity;
         Log.Warn("MANUAL RESET: drawdown lock cleared, peak re-based to " + DoubleToString(equity, 2));
        }
      if(InpManualResetDailyLoss && m_dailyLocked)
        {
         m_dailyLocked = false;
         m_dayStartEquity = equity;
         Log.Warn("MANUAL RESET: daily loss lock cleared");
        }
      if(InpLocalKillSwitch)
         m_killSwitch = true;
      Persist();
      Update();
     }

   void Persist()
     {
      Save("DAY", (double)m_day);
      Save("DAYEQ", m_dayStartEquity);
      Save("PEAK", m_peakEquity);
      Save("DLLOCK", m_dailyLocked ? 1 : 0);
      Save("DDLOCK", m_ddLocked ? 1 : 0);
      Save("KILL", m_killSwitch ? 1 : 0);
     }

   // Call on every timer tick.
   void Update()
     {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity <= 0)
         return;
      long day = CSessionManager::TradingDayKey(TimeGMT());
      if(day != m_day)
        {
         m_day = day;
         m_dayStartEquity = equity;
         m_dailyLocked = false;  // only a real trading-day rollover resets the daily limit
         Log.Info("new trading day, day-start equity=" + DoubleToString(equity, 2));
        }
      if(equity > m_peakEquity)
         m_peakEquity = equity;
      m_dailyLossPct = m_dayStartEquity > 0 ? MathMax(0.0, (m_dayStartEquity - equity) / m_dayStartEquity * 100.0) : 0;
      m_drawdownPct  = m_peakEquity > 0 ? MathMax(0.0, (m_peakEquity - equity) / m_peakEquity * 100.0) : 0;
      if(!m_dailyLocked && m_dailyLossPct >= InpMaxDailyLossPct)
        {
         m_dailyLocked = true;
         Log.Error("DAILY LOSS LIMIT REACHED (" + DoubleToString(m_dailyLossPct, 2) + "%) - no new trades today");
        }
      if(!m_ddLocked && m_drawdownPct >= InpMaxDrawdownPct)
        {
         m_ddLocked = true;
         Log.Error("MAX DRAWDOWN REACHED (" + DoubleToString(m_drawdownPct, 2) + "%) - manual reset required");
        }
      Persist();
     }

   void   ActivateKillSwitch(const string reason)
     {
      if(!m_killSwitch)
         Log.Error("LOCAL KILL SWITCH ACTIVATED: " + reason);
      m_killSwitch = true;
      Persist();
     }
   bool   KillSwitch()   const { return m_killSwitch; }
   bool   DailyLocked()  const { return m_dailyLocked; }
   bool   DrawdownLocked() const { return m_ddLocked; }
   double DailyLossPct() const { return m_dailyLossPct; }
   double DrawdownPct()  const { return m_drawdownPct; }

   bool CanOpenNew(string &reason)
     {
      if(m_killSwitch)
        {
         reason = "LOCAL_KILL_SWITCH";
         return false;
        }
      if(m_ddLocked)
        {
         reason = "MAX_DRAWDOWN_REACHED";
         return false;
        }
      if(m_dailyLocked)
        {
         reason = "MAX_DAILY_LOSS_REACHED";
         return false;
        }
      return true;
     }

   // Money lost (positive) if price goes from ``from`` to ``to`` for ``volume`` lots.
   static bool LossMoney(const string symbol, const bool isBuy, const double volume, const double from,
                         const double to, double &loss)
     {
      double profit = 0;
      ENUM_ORDER_TYPE type = isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      if(!OrderCalcProfit(type, symbol, volume, from, to, profit))
         return false;
      loss = MathMax(0.0, -profit);
      return true;
     }

   static double NormalizeVolume(const string symbol, const double volume)
     {
      double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
      double vmin = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
      double vmax = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
      if(step <= 0 || volume <= 0)
         return 0;
      double v = MathFloor(volume / step + 1e-9) * step;  // always round DOWN
      v = MathMin(v, vmax);
      int digits = (int)MathMax(0, MathCeil(-MathLog10(step)));
      v = NormalizeDouble(v, digits);
      if(v < vmin - 1e-12)
         return 0;
      return v;
     }

   // Volume so that a stop-out loses at most ``riskMoney``; 0 + reason if impossible.
   static double VolumeForRisk(const string symbol, const bool isBuy, const double entry, const double sl,
                               const double riskMoney, string &reason)
     {
      double lossPerLot = 0;
      if(!LossMoney(symbol, isBuy, 1.0, entry, sl, lossPerLot) || lossPerLot <= 0)
        {
         reason = "LOSS_PER_LOT_UNAVAILABLE";
         return 0;
        }
      double v = NormalizeVolume(symbol, riskMoney / lossPerLot);
      if(v <= 0)
        {
         double minRisk = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN) * lossPerLot;
         reason = "MIN_VOLUME_EXCEEDS_RISK: Trade rejected: minimum broker volume exceeds risk budget (budget=" +
                  DoubleToString(riskMoney, 2) + " min_volume_risk=" + DoubleToString(minRisk, 2) + ")";
         return 0;
        }
      return v;
     }
  };

#endif
