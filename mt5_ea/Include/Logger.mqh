//+------------------------------------------------------------------+
//| Logger.mqh - explainable KEY=VALUE logging (Experts tab + file)   |
//+------------------------------------------------------------------+
#ifndef PMX_LOGGER_MQH
#define PMX_LOGGER_MQH

class CLogger
  {
private:
   bool   m_toFile;
   bool   m_verbose;
   string m_file;

   void Write(const string level, const string msg)
     {
      string line = TimeToString(TimeGMT(), TIME_DATE | TIME_SECONDS) + " UTC [" + level + "] " + msg;
      Print(line);
      if(!m_toFile)
         return;
      int h = FileOpen(m_file, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
      if(h == INVALID_HANDLE)
         return;
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, line + "\r\n");
      FileClose(h);
     }

public:
   void Init(const bool toFile, const bool verbose)
     {
      m_toFile  = toFile;
      m_verbose = verbose;
      m_file    = "PRO_MAX_EA_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".log";
     }
   void Info(const string msg)  { Write("INFO", msg); }
   void Warn(const string msg)  { Write("WARN", msg); }
   void Error(const string msg) { Write("ERROR", msg); }
   void Debug(const string msg) { if(m_verbose) Write("DEBUG", msg); }

   // DECISION=REJECT REASON=... SYMBOL=... style line
   void Decision(const string symbol, const string decision, const string reason, const string extra = "")
     {
      string line = "SYMBOL=" + symbol + " DECISION=" + decision + " REASON=" + reason;
      if(extra != "")
         line += " " + extra;
      Write("DECISION", line);
     }
  };

CLogger Log;

#endif
