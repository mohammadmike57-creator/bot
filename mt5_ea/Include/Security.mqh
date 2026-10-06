//+------------------------------------------------------------------+
//| Security.mqh - SHA-256 / HMAC-SHA256 request signing              |
//|                                                                    |
//| canonical = METHOD\nPATH\nTIMESTAMP\nREQUEST_ID\nhex(sha256(body)) |
//| signature = hex(HMAC_SHA256(secret, canonical))                    |
//| Must match server/app/services/security.py exactly.               |
//+------------------------------------------------------------------+
#ifndef PMX_SECURITY_MQH
#define PMX_SECURITY_MQH

ulong g_pmxRequestCounter = 0;

void PmxStringToBytes(const string s, uchar &out[])
  {
   int n = StringToCharArray(s, out, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0)
      ArrayResize(out, n - 1);  // drop the terminating zero
   else
      ArrayResize(out, 0);
  }

string PmxBytesToHex(const uchar &b[])
  {
   string s = "";
   int n = ArraySize(b);
   for(int i = 0; i < n; i++)
      s += StringFormat("%02x", b[i]);
   return s;
  }

bool PmxSha256(const uchar &data[], uchar &out[])
  {
   uchar key[];
   ArrayResize(key, 0);
   int n = CryptEncode(CRYPT_HASH_SHA256, data, key, out);
   return n == 32;
  }

bool PmxHmacSha256(const uchar &key[], const uchar &msg[], uchar &out[])
  {
   const int block = 64;
   uchar k[];
   if(ArraySize(key) > block)
     {
      if(!PmxSha256(key, k))
         return false;
     }
   else
      ArrayCopy(k, key);
   int klen = ArraySize(k);
   ArrayResize(k, block);
   for(int i = klen; i < block; i++)
      k[i] = 0;

   int mlen = ArraySize(msg);
   uchar inner[];
   ArrayResize(inner, block + mlen);
   for(int i = 0; i < block; i++)
      inner[i] = (uchar)(k[i] ^ 0x36);
   for(int i = 0; i < mlen; i++)
      inner[block + i] = msg[i];
   uchar innerHash[];
   if(!PmxSha256(inner, innerHash))
      return false;

   uchar outer[];
   ArrayResize(outer, block + 32);
   for(int i = 0; i < block; i++)
      outer[i] = (uchar)(k[i] ^ 0x5c);
   for(int i = 0; i < 32; i++)
      outer[block + i] = innerHash[i];
   return PmxSha256(outer, out);
  }

string PmxSignRequest(const string secret, const string method, const string path, const string timestamp,
                      const string requestId, const uchar &body[])
  {
   uchar bodyHash[];
   if(!PmxSha256(body, bodyHash))
      return "";
   string canonical = method + "\n" + path + "\n" + timestamp + "\n" + requestId + "\n" + PmxBytesToHex(bodyHash);
   uchar key[], msg[], mac[];
   PmxStringToBytes(secret, key);
   PmxStringToBytes(canonical, msg);
   if(!PmxHmacSha256(key, msg, mac))
      return "";
   return PmxBytesToHex(mac);
  }

// Unique request id: [A-Za-z0-9-]{16,64}
string PmxNewRequestId()
  {
   g_pmxRequestCounter++;
   return StringFormat("%I64d-%I64x-%I64x-%04x%04x",
                       AccountInfoInteger(ACCOUNT_LOGIN), (long)TimeGMT(), GetMicrosecondCount(),
                       MathRand(), (int)(g_pmxRequestCounter & 0xFFFF));
  }

// Self-test against RFC 4231 test case 2 ("Jefe" / "what do ya want for nothing?").
bool PmxSecuritySelfTest()
  {
   uchar key[], msg[], mac[];
   PmxStringToBytes("Jefe", key);
   PmxStringToBytes("what do ya want for nothing?", msg);
   if(!PmxHmacSha256(key, msg, mac))
      return false;
   return PmxBytesToHex(mac) == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843";
  }

#endif
