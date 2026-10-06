//+------------------------------------------------------------------+
//| SessionManager.mqh - DST-aware sessions + broker trading sessions |
//| The backend's session engine (IANA time zones) is authoritative;  |
//| this local copy is used for logging and the local trading day.    |
//+------------------------------------------------------------------+
#ifndef PMX_SESSIONMANAGER_MQH
#define PMX_SESSIONMANAGER_MQH

class CSessionManager
  {
private:
   static datetime DayStart(const int year, const int month, const int day)
     {
      MqlDateTime d;
      ZeroMemory(d);
      d.year = year;
      d.mon = month;
      d.day = day;
      return StructToTime(d);
     }
   // n-th Sunday (1-based) of month at 00:00 UTC
   static datetime NthSunday(const int year, const int month, const int n)
     {
      datetime first = DayStart(year, month, 1);
      MqlDateTime d;
      TimeToStruct(first, d);
      int offset = (7 - d.day_of_week) % 7;
      return first + (offset + 7 * (n - 1)) * 86400;
     }
   static datetime LastSunday(const int year, const int month)
     {
      datetime next = (month == 12) ? DayStart(year + 1, 1, 1) : DayStart(year, month + 1, 1);
      datetime last = next - 86400;
      MqlDateTime d;
      TimeToStruct(last, d);
      return last - d.day_of_week * 86400;
     }

public:
   // US: 2nd Sunday March 02:00 local (07:00 UTC) -> 1st Sunday November 02:00 local (06:00 UTC)
   static bool IsUsDst(const datetime utc)
     {
      MqlDateTime d;
      TimeToStruct(utc, d);
      datetime s = NthSunday(d.year, 3, 2) + 7 * 3600;
      datetime e = NthSunday(d.year, 11, 1) + 6 * 3600;
      return utc >= s && utc < e;
     }
   // EU/UK: last Sunday March 01:00 UTC -> last Sunday October 01:00 UTC
   static bool IsEuDst(const datetime utc)
     {
      MqlDateTime d;
      TimeToStruct(utc, d);
      return utc >= LastSunday(d.year, 3) + 3600 && utc < LastSunday(d.year, 10) + 3600;
     }
   // Sydney: 1st Sunday October 02:00 AEST (Sat 16:00 UTC) -> 1st Sunday April 03:00 AEDT (Sat 16:00 UTC)
   static bool IsAuDst(const datetime utc)
     {
      MqlDateTime d;
      TimeToStruct(utc, d);
      datetime octStart = NthSunday(d.year, 10, 1) - 8 * 3600;
      datetime aprEnd = NthSunday(d.year, 4, 1) - 8 * 3600;
      return utc >= octStart || utc < aprEnd;
     }

   static int NewYorkOffset(const datetime utc) { return IsUsDst(utc) ? -4 : -5; }
   static int LondonOffset(const datetime utc)  { return IsEuDst(utc) ? 1 : 0; }
   static int SydneyOffset(const datetime utc)  { return IsAuDst(utc) ? 11 : 10; }

   static int LocalMinutes(const datetime utc, const int offsetHours)
     {
      MqlDateTime d;
      TimeToStruct(utc + offsetHours * 3600, d);
      return d.hour * 60 + d.min;
     }

   // Trading day key: rolls over at 17:00 New York (same rule as the backend).
   static long TradingDayKey(const datetime utc)
     {
      return (long)((utc + NewYorkOffset(utc) * 3600 + 7 * 3600) / 86400);
     }

   static string PrimarySession(const datetime utc)
     {
      int ny = LocalMinutes(utc, NewYorkOffset(utc));
      int ld = LocalMinutes(utc, LondonOffset(utc));
      int tk = LocalMinutes(utc, 9);
      int sy = LocalMinutes(utc, SydneyOffset(utc));
      bool london = ld >= 8 * 60 && ld < 17 * 60;
      bool newyork = ny >= 8 * 60 && ny < 17 * 60;
      if(london && newyork)
         return "OVERLAP";
      if(newyork)
         return "NEW_YORK";
      if(london)
         return "LONDON";
      if(tk >= 9 * 60 && tk < 18 * 60)
         return "TOKYO";
      if(sy >= 7 * 60 && sy < 16 * 60)
         return "SYDNEY";
      return "OFF_HOURS";
     }

   // Broker's real trading session for the symbol (server time), with a margin before the close.
   static bool BrokerSessionOpen(const string symbol, const int minutesBeforeClose = 10)
     {
      long mode = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
      if(mode == SYMBOL_TRADE_MODE_DISABLED || mode == SYMBOL_TRADE_MODE_CLOSEONLY)
         return false;
      datetime now = TimeTradeServer();
      MqlDateTime d;
      TimeToStruct(now, d);
      int secs = d.hour * 3600 + d.min * 60 + d.sec;
      datetime from, to;
      for(uint i = 0; i < 10; i++)
        {
         if(!SymbolInfoSessionTrade(symbol, (ENUM_DAY_OF_WEEK)d.day_of_week, i, from, to))
            break;
         int f = (int)((long)from % 86400);
         int t = (int)((long)to);
         if(t > 86400)
            t = t % 86400;
         if(secs >= f && secs < t - minutesBeforeClose * 60)
            return true;
        }
      return false;
     }
  };

#endif
