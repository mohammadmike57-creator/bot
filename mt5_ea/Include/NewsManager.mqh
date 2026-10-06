//+------------------------------------------------------------------+
//| NewsManager.mqh - independent local news check (MT5 calendar)     |
//| The backend news filter is primary; this is a second safety net. |
//| Calendar functions are not available in the Strategy Tester.     |
//+------------------------------------------------------------------+
#ifndef PMX_NEWSMANAGER_MQH
#define PMX_NEWSMANAGER_MQH

#include "Logger.mqh"

class CNewsManager
  {
private:
   bool m_enabled;
   int  m_before;
   int  m_after;
   bool m_failSafe;

   static bool IsEnergyEvent(const string name)
     {
      string n = name;
      StringToLower(n);
      return StringFind(n, "crude") >= 0 || StringFind(n, "eia") >= 0 || StringFind(n, "opec") >= 0 ||
             StringFind(n, "inventor") >= 0 || StringFind(n, "gasoline") >= 0 || StringFind(n, "distillate") >= 0;
     }

public:
   void Init(const bool enabled, const int beforeMin, const int afterMin, const bool failSafe)
     {
      m_enabled = enabled;
      m_before = beforeMin;
      m_after = afterMin;
      m_failSafe = failSafe;
     }

   // true = blocked. ``isEnergy``: the symbol is crude oil.
   bool Blocked(const string logical, const bool isEnergy, string &reason)
     {
      reason = "";
      if(!m_enabled)
         return false;
      if(MQLInfoInteger(MQL_TESTER))
        {
         reason = "NEWS_LOCAL_UNAVAILABLE_IN_TESTER";
         return m_failSafe;
        }
      datetime now = TimeTradeServer();  // calendar times are in trade-server time
      MqlCalendarValue values[];
      ResetLastError();
      int n = CalendarValueHistory(values, now - m_after * 60, now + m_before * 60, NULL, "USD");
      int err = GetLastError();
      if(n < 0 || (n == 0 && err != 0))
        {
         reason = "NEWS_LOCAL_CALENDAR_ERROR_" + IntegerToString(err);
         return m_failSafe;
        }
      for(int i = 0; i < n; i++)
        {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev))
            continue;
         bool energy = IsEnergyEvent(ev.name);
         if(energy && !isEnergy)
            continue;  // crude inventories do not block gold / US30
         if(ev.importance == CALENDAR_IMPORTANCE_HIGH || (energy && isEnergy &&
               ev.importance == CALENDAR_IMPORTANCE_MODERATE))
           {
            reason = "NEWS_LOCAL_BLACKOUT:" + ev.name;
            return true;
           }
        }
      return false;
     }
  };

#endif
