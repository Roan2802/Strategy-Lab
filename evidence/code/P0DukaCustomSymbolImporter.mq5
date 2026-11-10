#property strict
#property script_show_inputs
input string InpSymbol = "P0Duka_XAUUSD_2026";
input string InpCsvFile = "P0DukaAudit\\Duka_XAUUSD_rates.csv";
input int InpDigits = 3;
input double InpTickSize = 0.001;
input double InpContractSize = 1.0;

void OnStart()
{
   if(MQLInfoInteger(MQL_PROGRAM_TYPE) != PROGRAM_SCRIPT)
   {
      Print("P0 importer: only run this script from a terminal chart.");
      return;
   }
   ResetLastError();
   if(!CustomSymbolCreate(InpSymbol, ""))
   {
      PrintFormat("P0 importer: CustomSymbolCreate failed symbol=%s error=%d", InpSymbol, GetLastError());
      return;
   }
   CustomSymbolSetString(InpSymbol, SYMBOL_DESCRIPTION, "P0 Dukascopy-derived XAUUSD test symbol");
   CustomSymbolSetInteger(InpSymbol, SYMBOL_DIGITS, InpDigits);
   CustomSymbolSetInteger(InpSymbol, SYMBOL_TRADE_CALC_MODE, SYMBOL_CALC_MODE_CFD);
   CustomSymbolSetInteger(InpSymbol, SYMBOL_TRADE_MODE, SYMBOL_TRADE_MODE_FULL);
   CustomSymbolSetDouble(InpSymbol, SYMBOL_TRADE_TICK_SIZE, InpTickSize);
   CustomSymbolSetDouble(InpSymbol, SYMBOL_TRADE_CONTRACT_SIZE, InpContractSize);
   CustomSymbolSetDouble(InpSymbol, SYMBOL_VOLUME_MIN, 0.01);
   CustomSymbolSetDouble(InpSymbol, SYMBOL_VOLUME_STEP, 0.01);
   CustomSymbolSetDouble(InpSymbol, SYMBOL_VOLUME_MAX, 100.0);

   int h = FileOpen(InpCsvFile, FILE_READ|FILE_CSV|FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
   {
      PrintFormat("P0 importer: FileOpen failed file=%s error=%d", InpCsvFile, GetLastError());
      return;
   }
   for(int c=0; c<8 && !FileIsEnding(h); c++) FileReadString(h); // Dukascopy-derived CSV header

   MqlRates rates[];
   ArrayResize(rates, 200000);
   int count=0;
   double spread_sum=0.0;
   while(!FileIsEnding(h))
   {
      string ts=FileReadString(h);
      if(ts=="" || FileIsEnding(h)) break;
      string op=FileReadString(h), hi=FileReadString(h), lo=FileReadString(h), cl=FileReadString(h);
      string tv=FileReadString(h), sp=FileReadString(h), rv=FileReadString(h);
      if(ts=="" || op=="" || hi=="" || lo=="" || cl=="") continue;
      if(count>=ArraySize(rates)) ArrayResize(rates, ArraySize(rates)+50000);
      rates[count].time=(datetime)StringToInteger(ts);
      rates[count].open=StringToDouble(op);
      rates[count].high=StringToDouble(hi);
      rates[count].low=StringToDouble(lo);
      rates[count].close=StringToDouble(cl);
      rates[count].tick_volume=(long)StringToInteger(tv);
      rates[count].spread=(int)StringToInteger(sp);
      spread_sum += rates[count].spread;
      rates[count].real_volume=(long)StringToInteger(rv);
      count++;
   }
   FileClose(h);
   if(count<1)
   {
      Print("P0 importer: no data rows found.");
      return;
   }
   ArrayResize(rates, count);
   ResetLastError();
   int replaced=CustomRatesReplace(InpSymbol, rates[0].time, rates[count-1].time, rates);
   if(replaced<0)
   {
      PrintFormat("P0 importer: CustomRatesReplace failed count=%d error=%d", count, GetLastError());
      return;
   }
   SymbolSelect(InpSymbol, true);
   PrintFormat("P0_IMPORT_OK symbol=%s bars=%d replaced=%d first_epoch=%I64d last_epoch=%I64d digits=%d tick_size=%.3f contract_size=%.1f spread_points_avg=%.2f",
      InpSymbol, count, replaced, (long)rates[0].time, (long)rates[count-1].time, InpDigits, InpTickSize, InpContractSize,
      spread_sum/count);
}



