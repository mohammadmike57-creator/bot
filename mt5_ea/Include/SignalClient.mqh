//+------------------------------------------------------------------+
//| SignalClient.mqh - signed HTTPS calls to the Python backend      |
//| Requires: Tools > Options > Expert Advisors > Allow WebRequest    |
//|           for the backend URL.                                    |
//+------------------------------------------------------------------+
#ifndef PMX_SIGNALCLIENT_MQH
#define PMX_SIGNALCLIENT_MQH

#include "Security.mqh"
#include "Logger.mqh"

class CSignalClient
  {
private:
   string   m_base;
   string   m_keyId;
   string   m_secret;
   int      m_timeout;
   datetime m_lastOk;
   int      m_failures;
   uint     m_lastLatencyMs;
   bool     m_warned4014;

public:
   void Init(const string baseUrl, const string keyId, const string secret, const int timeoutMs)
     {
      m_base = baseUrl;
      while(StringLen(m_base) > 0 && StringGetCharacter(m_base, StringLen(m_base) - 1) == '/')
         m_base = StringSubstr(m_base, 0, StringLen(m_base) - 1);
      m_keyId = keyId;
      m_secret = secret;
      m_timeout = timeoutMs;
      m_lastOk = 0;
      m_failures = 0;
      m_lastLatencyMs = 0;
      m_warned4014 = false;
     }

   bool     Configured()     const { return m_base != "" && m_keyId != "" && m_secret != ""; }
   uint     LastLatencyMs()  const { return m_lastLatencyMs; }
   datetime LastSuccess()    const { return m_lastOk; }
   int      Failures()       const { return m_failures; }

   bool IsOnline(const int offlineAfterSec) const
     {
      return m_lastOk > 0 && (TimeGMT() - m_lastOk) <= offlineAfterSec;
     }

   // Returns HTTP status code (or -1 on transport error).
   int Post(const string path, const string body, string &response)
     {
      response = "";
      if(!Configured())
         return -1;
      uchar bodyBytes[];
      PmxStringToBytes(body, bodyBytes);
      string ts  = IntegerToString((long)TimeGMT());
      string rid = PmxNewRequestId();
      string sig = PmxSignRequest(m_secret, "POST", path, ts, rid, bodyBytes);
      if(sig == "")
        {
         Log.Error("request signing failed (CryptEncode)");
         return -1;
        }
      string headers = "Content-Type: application/json\r\n"
                       "X-Api-Key-Id: " + m_keyId + "\r\n"
                       "X-Timestamp: " + ts + "\r\n"
                       "X-Request-Id: " + rid + "\r\n"
                       "X-Signature: " + sig + "\r\n";
      char post[];
      int n = ArraySize(bodyBytes);
      ArrayResize(post, n);
      for(int i = 0; i < n; i++)
         post[i] = (char)bodyBytes[i];
      char result[];
      string resultHeaders;
      ResetLastError();
      uint t0 = GetTickCount();
      int code = WebRequest("POST", m_base + path, headers, m_timeout, post, result, resultHeaders);
      m_lastLatencyMs = GetTickCount() - t0;
      if(code == -1)
        {
         int err = GetLastError();
         m_failures++;
         if(err == 4014 && !m_warned4014)
           {
            Log.Error("WebRequest not allowed for " + m_base +
                      ". Add it in Tools > Options > Expert Advisors > 'Allow WebRequest for listed URL'.");
            m_warned4014 = true;
           }
         else
            Log.Debug("WebRequest failed path=" + path + " err=" + IntegerToString(err));
         return -1;
        }
      response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
      if(code >= 200 && code < 300)
        {
         m_lastOk = TimeGMT();
         m_failures = 0;
        }
      else
        {
         m_failures++;
         Log.Warn("backend " + path + " HTTP " + IntegerToString(code) + " " + StringSubstr(response, 0, 200));
        }
      return code;
     }
  };

#endif
