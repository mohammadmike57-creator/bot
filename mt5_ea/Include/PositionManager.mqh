//+------------------------------------------------------------------+
//| PositionManager.mqh - own-position tracking for a HEDGING account |
//| Magic = symbolBase * 100 + strategyCode (e.g. 1000102).           |
//| Multiple BUY and SELL positions per symbol are tracked one by one.|
//+------------------------------------------------------------------+
#ifndef PMX_POSITIONMANAGER_MQH
#define PMX_POSITIONMANAGER_MQH

#include "Config.mqh"
#include "RiskManager.mqh"

struct PmxPosition
  {
   ulong    ticket;
   string   symbol;
   long     magic;
   int      strategyCode;
   bool     isBuy;
   double   volume;
   double   entry;
   double   current;
   double   sl;
   double   tp;
   double   profit;
   double   swap;
   datetime opened;
   string   comment;
   double   riskMoney;   // CAPITAL at risk: from the worse of entry/current to SL (0 at break-even+)
   double   giveback;    // equity lost if SL is hit from the current price (incl. floating profit)
  };

class CPositionManager
  {
private:
   int m_bases[];

public:
   void Init(const int &bases[])
     {
      ArrayCopy(m_bases, bases);
     }

   static int StrategyCode(const string strategy)
     {
      if(StringFind(strategy, "TOKYO_GOLD") == 0)       return 1;
      if(StringFind(strategy, "LONDON_TREND") == 0)     return 2;
      if(StringFind(strategy, "NEWYORK_TREND") == 0)    return 3;
      if(StringFind(strategy, "OVERLAP_MOMENTUM") == 0) return 4;
      if(StringFind(strategy, "OIL_TREND") == 0)        return 5;
      if(StringFind(strategy, "US30_NY") == 0)          return 6;
      return 99;
     }

   bool IsOwnMagic(const long magic) const
     {
      long base = magic / 100;
      for(int i = 0; i < ArraySize(m_bases); i++)
         if(m_bases[i] == base)
            return true;
      return false;
     }

   // Selects position by index and fills ``p``. Returns false if not ours (when managing own trades only).
   bool ReadSelected(PmxPosition &p) const
     {
      p.ticket  = (ulong)PositionGetInteger(POSITION_TICKET);
      p.symbol  = PositionGetString(POSITION_SYMBOL);
      p.magic   = PositionGetInteger(POSITION_MAGIC);
      if(InpManageOnlyOwnTrades && !IsOwnMagic(p.magic))
         return false;
      p.strategyCode = (int)(p.magic % 100);
      p.isBuy   = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      p.volume  = PositionGetDouble(POSITION_VOLUME);
      p.entry   = PositionGetDouble(POSITION_PRICE_OPEN);
      p.current = PositionGetDouble(POSITION_PRICE_CURRENT);
      p.sl      = PositionGetDouble(POSITION_SL);
      p.tp      = PositionGetDouble(POSITION_TP);
      p.profit  = PositionGetDouble(POSITION_PROFIT);
      p.swap    = PositionGetDouble(POSITION_SWAP);
      p.opened  = (datetime)PositionGetInteger(POSITION_TIME);
      p.comment = PositionGetString(POSITION_COMMENT);
      p.riskMoney = -1;  // unknown / unbounded until computed
      p.giveback = -1;
      if(p.sl > 0)
        {
         double worse = p.isBuy ? MathMin(p.entry, p.current) : MathMax(p.entry, p.current);
         double capital = 0, give = 0;
         if(CRiskManager::LossMoney(p.symbol, p.isBuy, p.volume, worse, p.sl, capital) &&
            CRiskManager::LossMoney(p.symbol, p.isBuy, p.volume, p.current, p.sl, give))
           {
            p.riskMoney = capital;
            p.giveback = give;
           }
        }
      return true;
     }

   int Collect(PmxPosition &out[]) const
     {
      ArrayResize(out, 0);
      int total = PositionsTotal();
      for(int i = 0; i < total; i++)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0)
            continue;
         PmxPosition p;
         if(!ReadSelected(p))
            continue;
         int n = ArraySize(out);
         ArrayResize(out, n + 1);
         out[n] = p;
        }
      return ArraySize(out);
     }

   static string SignalIdFromComment(const string comment)
     {
      if(StringFind(comment, PMX_COMMENT_PREFIX) != 0)
         return "";
      return StringSubstr(comment, StringLen(PMX_COMMENT_PREFIX));
     }

   // Was this signal already executed? Checks open positions and the last 7 days of deals.
   bool SignalAlreadyExecuted(const string signalId) const
     {
      string tag = PMX_COMMENT_PREFIX + signalId;
      int total = PositionsTotal();
      for(int i = 0; i < total; i++)
        {
         if(PositionGetTicket(i) == 0)
            continue;
         if(PositionGetString(POSITION_COMMENT) == tag)
            return true;
        }
      if(HistorySelect(TimeCurrent() - 7 * 86400, TimeCurrent() + 86400))
        {
         int deals = HistoryDealsTotal();
         for(int i = deals - 1; i >= 0; i--)
           {
            ulong d = HistoryDealGetTicket(i);
            if(d > 0 && HistoryDealGetString(d, DEAL_COMMENT) == tag)
               return true;
           }
         int orders = HistoryOrdersTotal();
         for(int i = orders - 1; i >= 0; i--)
           {
            ulong o = HistoryOrderGetTicket(i);
            if(o > 0 && HistoryOrderGetString(o, ORDER_COMMENT) == tag)
               return true;
           }
        }
      return false;
     }
  };

#endif
