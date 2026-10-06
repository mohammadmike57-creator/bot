//+------------------------------------------------------------------+
//| Json.mqh - minimal JSON builder and flat-object parser            |
//| The backend's responses are flat JSON objects (no nesting needed).|
//+------------------------------------------------------------------+
#ifndef PMX_JSON_MQH
#define PMX_JSON_MQH

string JsonEscape(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\r", "\\r");
   StringReplace(s, "\n", "\\n");
   StringReplace(s, "\t", "\\t");
   return s;
  }

string JsonNumber(const double v, const int digits)
  {
   if(!MathIsValidNumber(v))
      return "0";
   string s = DoubleToString(v, digits);
   if(StringFind(s, ".") >= 0)
     {
      int len = StringLen(s);
      while(len > 1 && StringGetCharacter(s, len - 1) == '0')
         len--;
      if(len > 1 && StringGetCharacter(s, len - 1) == '.')
         len--;
      s = StringSubstr(s, 0, len);
     }
   return s;
  }

class CJsonBuilder
  {
private:
   string m_s;
   bool   m_first;
   void   Key(const string k)
     {
      if(!m_first)
         m_s += ",";
      m_first = false;
      m_s += "\"" + JsonEscape(k) + "\":";
     }
public:
          CJsonBuilder() { m_s = "{"; m_first = true; }
   void   Str(const string k, const string v)          { Key(k); m_s += "\"" + JsonEscape(v) + "\""; }
   void   Num(const string k, const double v, int d=8) { Key(k); m_s += JsonNumber(v, d); }
   void   Int(const string k, const long v)            { Key(k); m_s += IntegerToString(v); }
   void   Bool(const string k, const bool v)           { Key(k); m_s += (v ? "true" : "false"); }
   void   Raw(const string k, const string raw)        { Key(k); m_s += raw; }
   string Build()                                       { return m_s + "}"; }
  };

//--- Flat parser: top-level keys -> raw values (strings unescaped)
class CJsonFlat
  {
private:
   string m_keys[];
   string m_vals[];
   int    m_count;

   static bool IsWs(const ushort c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }

   bool ParseString(const string js, int &i, string &out)
     {
      int n = StringLen(js);
      if(i >= n || StringGetCharacter(js, i) != '"')
         return false;
      i++;
      out = "";
      while(i < n)
        {
         ushort c = StringGetCharacter(js, i);
         if(c == '"')
           {
            i++;
            return true;
           }
         if(c == '\\' && i + 1 < n)
           {
            ushort e = StringGetCharacter(js, i + 1);
            if(e == 'n')
               out += "\n";
            else
               if(e == 't')
                  out += "\t";
               else
                  if(e == 'r')
                     out += "\r";
                  else
                     if(e == 'u' && i + 5 < n)
                       {
                        out += "?";  // non-ASCII escapes are not needed by this protocol
                        i += 4;
                       }
                     else
                        out += ShortToString(e);
            i += 2;
            continue;
           }
         out += ShortToString(c);
         i++;
        }
      return false;
     }

   bool SkipNested(const string js, int &i)
     {
      int n = StringLen(js), depth = 0;
      bool inStr = false;
      for(; i < n; i++)
        {
         ushort c = StringGetCharacter(js, i);
         if(inStr)
           {
            if(c == '\\')
               i++;
            else
               if(c == '"')
                  inStr = false;
            continue;
           }
         if(c == '"')
            inStr = true;
         else
            if(c == '{' || c == '[')
               depth++;
            else
               if(c == '}' || c == ']')
                 {
                  depth--;
                  if(depth == 0)
                    {
                     i++;
                     return true;
                    }
                 }
        }
      return false;
     }

   int Find(const string key)
     {
      for(int k = 0; k < m_count; k++)
         if(m_keys[k] == key)
            return k;
      return -1;
     }

public:
   CJsonFlat() { m_count = 0; }

   bool Parse(const string js)
     {
      m_count = 0;
      ArrayResize(m_keys, 0);
      ArrayResize(m_vals, 0);
      int n = StringLen(js), i = 0;
      while(i < n && IsWs(StringGetCharacter(js, i)))
         i++;
      if(i >= n || StringGetCharacter(js, i) != '{')
         return false;
      i++;
      while(i < n)
        {
         while(i < n && (IsWs(StringGetCharacter(js, i)) || StringGetCharacter(js, i) == ','))
            i++;
         if(i < n && StringGetCharacter(js, i) == '}')
            return true;
         string key;
         if(!ParseString(js, i, key))
            return false;
         while(i < n && IsWs(StringGetCharacter(js, i)))
            i++;
         if(i >= n || StringGetCharacter(js, i) != ':')
            return false;
         i++;
         while(i < n && IsWs(StringGetCharacter(js, i)))
            i++;
         string val = "";
         ushort c = StringGetCharacter(js, i);
         if(c == '"')
           {
            if(!ParseString(js, i, val))
               return false;
           }
         else
            if(c == '{' || c == '[')
              {
               int start = i;
               if(!SkipNested(js, i))
                  return false;
               val = StringSubstr(js, start, i - start);
              }
            else
              {
               int start = i;
               while(i < n && StringGetCharacter(js, i) != ',' && StringGetCharacter(js, i) != '}')
                  i++;
               val = StringSubstr(js, start, i - start);
               StringTrimLeft(val);
               StringTrimRight(val);
              }
         ArrayResize(m_keys, m_count + 1);
         ArrayResize(m_vals, m_count + 1);
         m_keys[m_count] = key;
         m_vals[m_count] = val;
         m_count++;
        }
      return false;
     }

   bool   Has(const string key)                          { return Find(key) >= 0; }
   string GetString(const string key, const string def = "")
     {
      int k = Find(key);
      return (k < 0 || m_vals[k] == "null") ? def : m_vals[k];
     }
   double GetDouble(const string key, const double def = 0.0)
     {
      int k = Find(key);
      return (k < 0 || m_vals[k] == "null" || m_vals[k] == "") ? def : StringToDouble(m_vals[k]);
     }
   long   GetLong(const string key, const long def = 0)
     {
      int k = Find(key);
      return (k < 0 || m_vals[k] == "null" || m_vals[k] == "") ? def : StringToInteger(m_vals[k]);
     }
   bool   GetBool(const string key, const bool def = false)
     {
      int k = Find(key);
      if(k < 0)
         return def;
      return m_vals[k] == "true";
     }
  };

#endif
