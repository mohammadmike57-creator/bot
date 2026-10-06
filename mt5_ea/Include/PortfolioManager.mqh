//+------------------------------------------------------------------+
//| PortfolioManager.mqh - local portfolio limits (risk, count, margin)|
//| Position count is a secondary limit; open RISK is the real one.   |
//+------------------------------------------------------------------+
#ifndef PMX_PORTFOLIOMANAGER_MQH
#define PMX_PORTFOLIOMANAGER_MQH

#include "Config.mqh"
#include "PositionManager.mqh"

class CPortfolioManager
  {
public:
   // Can a new position with ``newRisk`` money at risk on ``symbol`` be added?
   bool Allows(CPositionManager *pm, const string symbol, const bool isBuy, const double newRisk, string &reason)
     {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity <= 0)
        {
         reason = "NON_POSITIVE_EQUITY";
         return false;
        }
      PmxPosition pos[];
      int n = pm.Collect(pos);
      if(n >= InpMaxPositions)
        {
         reason = "MAX_POSITIONS_REACHED";
         return false;
        }
      double total = 0, sym = 0;
      int symCount = 0;
      for(int i = 0; i < n; i++)
        {
         if(pos[i].riskMoney < 0)
           {
            reason = "OPEN_POSITION_WITHOUT_STOP";
            return false;
           }
         total += pos[i].riskMoney;
         if(pos[i].symbol == symbol)
           {
            sym += pos[i].riskMoney;
            symCount++;
           }
        }
      if(symCount >= InpMaxPositionsPerSymbol)
        {
         reason = "MAX_SYMBOL_POSITIONS_REACHED";
         return false;
        }
      if((total + newRisk) / equity * 100.0 > InpMaxPortfolioRiskPct + 1e-9)
        {
         reason = "MAX_PORTFOLIO_RISK_REACHED";
         return false;
        }
      if((sym + newRisk) / equity * 100.0 > InpMaxSymbolRiskPct + 1e-9)
        {
         reason = "MAX_SYMBOL_RISK_REACHED";
         return false;
        }
      return true;
     }

   static bool MarginAllows(const string symbol, const bool isBuy, const double volume, const double price,
                            string &reason)
     {
      double required = 0;
      if(!OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, symbol, volume, price, required))
        {
         reason = "MARGIN_CALC_FAILED";
         return false;
        }
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      double used = AccountInfoDouble(ACCOUNT_MARGIN);
      double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(required > freeMargin)
        {
         reason = "INSUFFICIENT_FREE_MARGIN";
         return false;
        }
      if(equity <= 0 || (used + required) / equity * 100.0 > InpMaxMarginUsagePct)
        {
         reason = "MAX_MARGIN_USAGE";
         return false;
        }
      return true;
     }
  };

#endif
