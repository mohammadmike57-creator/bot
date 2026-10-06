//+------------------------------------------------------------------+
//|                                                ExportHistory.mq5 |
//| Exports OHLC bars (with per-bar spread) to MQL5/Files/<sym>_<tf>.csv |
//| with timestamps converted from broker SERVER time to UTC.        |
//| Copy the CSV to the Mac's data/ folder for backtests / training. |
//+------------------------------------------------------------------+
#property script_show_inputs
#property version "1.00"

enum ENUM_SERVER_TZ
  {
   TZ_AUTO_CURRENT = 0, // Current server offset for all bars (simple; ignores past DST switches)
   TZ_NY_CLOSE     = 1, // Broker clock = New York + 7h (GMT+2 winter / GMT+3 summer) - common for CFD brokers
   TZ_FIXED        = 2  // Fixed offset in hours (InpFixedOffsetHours)
  };

input string          InpSymbol           = "";          // Symbol (empty = chart symbol)
input ENUM_TIMEFRAMES InpTimeframe        = PERIOD_M15;  // Timeframe
input datetime        InpFrom             = D'2023.01.01 00:00';
input datetime        InpTo               = D'2030.01.01 00:00';
input ENUM_SERVER_TZ  InpServerTz         = TZ_NY_CLOSE; // How the broker clock relates to UTC (check with your broker)
input int             InpFixedOffsetHours = 2;           // Used with TZ_FIXED

bool IsUsDst(const datetime utc)
  {
   MqlDateTime d;
   TimeToStruct(utc, d);
   MqlDateTime m;
   ZeroMemory(m);
   m.year = d.year;
   m.mon = 3;
   m.day = 1;
   datetime mar1 = StructToTime(m);
   TimeToStruct(mar1, m);
   datetime start = mar1 + (((7 - m.day_of_week) % 7) + 7) * 86400 + 7 * 3600;
   ZeroMemory(m);
   m.year = d.year;
   m.mon = 11;
   m.day = 1;
   datetime nov1 = StructToTime(m);
   TimeToStruct(nov1, m);
   datetime end = nov1 + ((7 - m.day_of_week) % 7) * 86400 + 6 * 3600;
   return utc >= start && utc < end;
  }

long ToUtc(const datetime server)
  {
   if(InpServerTz == TZ_FIXED)
      return (long)server - InpFixedOffsetHours * 3600;
   if(InpServerTz == TZ_NY_CLOSE)
     {
      datetime guess = (datetime)((long)server - 2 * 3600);
      return IsUsDst(guess) ? (long)server - 3 * 3600 : (long)guess;
     }
   long diff = (long)TimeTradeServer() - (long)TimeGMT();
   return (long)server - (long)MathRound(diff / 900.0) * 900;
  }

void OnStart()
  {
   string sym = InpSymbol == "" ? _Symbol : InpSymbol;
   MqlRates rates[];
   ResetLastError();
   int n = CopyRates(sym, InpTimeframe, InpFrom, InpTo, rates);
   if(n <= 0)
     {
      Print("CopyRates failed for ", sym, " err=", GetLastError(), " (scroll the chart back / raise Max bars in chart)");
      return;
     }
   string tf = StringSubstr(EnumToString(InpTimeframe), 7);
   string file = sym + "_" + tf + ".csv";
   int h = FileOpen(file, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
     {
      Print("FileOpen failed err=", GetLastError());
      return;
     }
   FileWrite(h, "time", "open", "high", "low", "close", "tick_volume", "spread", "server_time");
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   for(int i = 0; i < n; i++)
     {
      if(i == n - 1 && rates[i].time == iTime(sym, InpTimeframe, 0))
         break;  // skip the still-forming bar
      datetime utc = (datetime)ToUtc(rates[i].time);
      FileWrite(h, TimeToString(utc, TIME_DATE | TIME_SECONDS), DoubleToString(rates[i].open, digits),
                DoubleToString(rates[i].high, digits), DoubleToString(rates[i].low, digits),
                DoubleToString(rates[i].close, digits), IntegerToString(rates[i].tick_volume),
                IntegerToString(rates[i].spread), TimeToString(rates[i].time, TIME_DATE | TIME_SECONDS));
     }
   FileClose(h);
   PrintFormat("Exported %d bars of %s %s to MQL5\\Files\\%s (UTC timestamps)", n, sym, tf, file);
  }
