//+------------------------------------------------------------------+
//| TrailingStop.mqh - local trade management (no backend required)   |
//|   +0.8R tighten | +1R break-even (+offset) | +1R partial | trail  |
//| Invariant: a stop only ever moves in the protective direction.    |
//| Per-position state (initial risk, flags) persists in Global Vars. |
//+------------------------------------------------------------------+
#ifndef PMX_TRAILINGSTOP_MQH
#define PMX_TRAILINGSTOP_MQH

#include <Trade\Trade.mqh>
#include "Config.mqh"
#include "Logger.mqh"
#include "PositionManager.mqh"
#include "RiskManager.mqh"

class CTrailingStop
  {
private:
   CTrade *m_trade;

   static string Key(const ulong ticket, const string name)
     {
      return "PMX_T" + IntegerToString((long)ticket) + "_" + name;
     }
   static double Get(const ulong ticket, const string name, const double def)
     {
      string k = Key(ticket, name);
      return GlobalVariableCheck(k) ? GlobalVariableGet(k) : def;
     }
   static void Set(const ulong ticket, const string name, const double v)
     {
      GlobalVariableSet(Key(ticket, name), v);
     }

   static bool Tighter(const bool isBuy, const double candidate, const double current)
     {
      if(current <= 0)
         return true;
      return isBuy ? candidate > current : candidate < current;
     }

public:
   void Init(CTrade *trade) { m_trade = trade; }

   // Remember the initial stop distance when a position is first seen.
   static void Register(const PmxPosition &p)
     {
      if(GlobalVariableCheck(Key(p.ticket, "R")))
         return;
      double r = p.sl > 0 ? MathAbs(p.entry - p.sl) : 0;
      Set(p.ticket, "R", r);
      Set(p.ticket, "V0", p.volume);
      Set(p.ticket, "TP1", 0);
     }

   static void Forget(const ulong ticket)
     {
      GlobalVariableDel(Key(ticket, "R"));
      GlobalVariableDel(Key(ticket, "V0"));
      GlobalVariableDel(Key(ticket, "TP1"));
     }

   // Manage one position. ``atr`` from the local indicator handle (0 if unavailable).
   void Manage(const PmxPosition &p, const double atr)
     {
      Register(p);
      double risk = Get(p.ticket, "R", 0);
      string sym = p.symbol;
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double point = SymbolInfoDouble(sym, SYMBOL_POINT);
      double tick = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
      if(tick <= 0)
         tick = point;
      double bid = SymbolInfoDouble(sym, SYMBOL_BID);
      double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      double price = p.isBuy ? bid : ask;  // closing-side price
      if(price <= 0)
         return;
      double sign = p.isBuy ? 1.0 : -1.0;
      long stopsLevel = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
      long freezeLevel = SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL);
      double minGap = MathMax((double)MathMax(stopsLevel, freezeLevel) * point, tick);

      // Emergency stop: an own position must never stay without a stop.
      if(p.sl <= 0)
        {
         if(atr <= 0)
           {
            Log.Error("position " + IntegerToString((long)p.ticket) + " has no SL and ATR unavailable - closing");
            m_trade.PositionClose(p.ticket, (ulong)InpMaxDeviationPoints);
            return;
           }
         double esl = NormalizeDouble(MathRound((price - sign * InpEmergencySlAtr * atr) / tick) * tick, digits);
         if(m_trade.PositionModify(p.ticket, esl, p.tp))
            Log.Warn("EMERGENCY SL set on " + IntegerToString((long)p.ticket) + " sl=" + DoubleToString(esl, digits));
         else
            m_trade.PositionClose(p.ticket, (ulong)InpMaxDeviationPoints);
         return;
        }
      if(risk <= 0)
         return;

      double r = (price - p.entry) * sign / risk;

      // Partial take-profit at TP1 (once).
      if(InpTP1CloseFraction > 0 && r >= InpTP1R && Get(p.ticket, "TP1", 0) < 0.5)
        {
         Set(p.ticket, "TP1", 1);
         double v0 = Get(p.ticket, "V0", p.volume);
         double closeVol = CRiskManager::NormalizeVolume(sym, v0 * InpTP1CloseFraction);
         double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
         if(closeVol > 0 && p.volume - closeVol >= vmin - 1e-12)
           {
            if(m_trade.PositionClosePartial(p.ticket, closeVol, (ulong)InpMaxDeviationPoints))
               Log.Info("PARTIAL_TP1 ticket=" + IntegerToString((long)p.ticket) + " vol=" + DoubleToString(closeVol, 2));
            else
               Log.Warn("partial close failed retcode=" + IntegerToString((int)m_trade.ResultRetcode()));
           }
        }

      // Candidate stops; keep the most protective one that respects the broker's stop level.
      double best = p.sl;
      string why = "";
      double cands[3];
      string names[3];
      int nc = 0;
      if(r >= InpTightenTriggerR)
        {
         cands[nc] = p.entry + sign * InpTightenToR * risk;
         names[nc++] = "TIGHTEN";
        }
      if(r >= InpBETriggerR)
        {
         cands[nc] = p.entry + sign * InpBEOffsetR * risk;
         names[nc++] = "BREAK_EVEN";
        }
      if(r >= InpTrailStartR && atr > 0)
        {
         cands[nc] = price - sign * InpTrailAtrMult * atr;
         names[nc++] = "TRAIL_ATR";
        }
      for(int i = 0; i < nc; i++)
        {
         double lvl = NormalizeDouble(MathRound(cands[i] / tick) * tick, digits);
         if((price - lvl) * sign < minGap)
            continue;
         if(Tighter(p.isBuy, lvl, best) && MathAbs(lvl - best) >= tick / 2)
           {
            best = lvl;
            why = names[i];
           }
        }
      if(why == "")
         return;
      if(m_trade.PositionModify(p.ticket, best, p.tp))
         Log.Info(why + " ticket=" + IntegerToString((long)p.ticket) + " sl " + DoubleToString(p.sl, digits) +
                  " -> " + DoubleToString(best, digits) + " r=" + DoubleToString(r, 2));
      else
         Log.Warn(why + " modify failed retcode=" + IntegerToString((int)m_trade.ResultRetcode()));
     }
  };

#endif
