import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pykrx import stock
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")

class AlphaSignalBacktester:
    def __init__(self, start_date: str, end_date: str):
        self.start_date = start_date
        self.end_date = end_date
        self.trades = []

    def get_candidate_universe(self, target_date: str):
        try:
            df_market = stock.get_market_ohlcv_by_ticker(target_date, market="ALL")
            df_cap = stock.get_market_cap_by_ticker(target_date, market="ALL")
            
            if df_market.empty or df_cap.empty:
                return []

            df = pd.merge(df_market[['거래량', '거래대금', '등락률']], 
                          df_cap[['시가총액']], left_index=True, right_index=True)

            cond_amount = df['거래대금'] >= 100_000_000_000
            df['회전율'] = df['거래대금'] / df['시가총액']
            cond_turnover = df['회전율'] >= 0.15

            candidates = df[cond_amount & cond_turnover].index.tolist()
            return candidates
        except Exception:
            return []

    def simulate_intraday_vwap_trade(self, ticker: str, target_date: str):
        try:
            df_minute = stock.get_market_ohlcv_by_date(target_date, target_date, ticker, timeframe="m")
            if df_minute.empty or len(df_minute) < 60:
                return None

            df_minute['Cumulative_TPV'] = (df_minute['종가'] * df_minute['거래량']).cumsum()
            df_minute['Cumulative_Vol'] = df_minute['거래량'].cumsum()
            df_minute['VWAP'] = df_minute['Cumulative_TPV'] / df_minute['Cumulative_Vol']

            df_golden = df_minute.between_time('09:15', '10:15')
            if df_golden.empty:
                return None

            entry_price = None
            entry_time = None
            stop_loss_pct = 0.025  # -2.5% 손절
            take_profit_pct = 0.050 # +5.0% 익절

            for timestamp, row in df_golden.iterrows():
                low_price = row['저가']
                vwap_val = row['VWAP']
                
                if low_price <= vwap_val * 1.005 and low_price >= vwap_val * 0.985:
                    entry_price = vwap_val
                    entry_time = timestamp
                    break

            if entry_price is None:
                return None

            df_after_entry = df_minute.loc[df_minute.index > entry_time]
            if df_after_entry.empty:
                return None

            target_price = entry_price * (1 + take_profit_pct)
            stop_price = entry_price * (1 - stop_loss_pct)

            for _, row in df_after_entry.iterrows():
                curr_high = row['고가']
                curr_low = row['저가']

                if curr_high >= target_price:
                    return {"result": "WIN", "return": take_profit_pct, "ticker": ticker, "date": target_date}
                if curr_low <= stop_price:
                    return {"result": "LOSS", "return": -stop_loss_pct, "ticker": ticker, "date": target_date}

            eod_price = df_minute.iloc[-1]['종가']
            eod_return = (eod_price - entry_price) / entry_price
            res_type = "WIN" if eod_return > 0 else "LOSS"
            return {"result": res_type, "return": eod_return, "ticker": ticker, "date": target_date}

        except Exception:
            return None

    def run(self):
        print(f"\n🚀 [{self.start_date} ~ {self.end_date}] STOCK BOT 2.0 알파 시그널 백테스팅 개시...\n")
        
        business_days = stock.get_previous_business_days(fromdate=self.start_date, todate=self.end_date)
        
        for date_dt in tqdm(business_days, desc="백테스팅 진행률"):
            date_str = date_dt.strftime("%Y%m%d")
            candidates = self.get_candidate_universe(date_str)

            for ticker in candidates:
                trade_res = self.simulate_intraday_vwap_trade(ticker, date_str)
                if trade_res:
                    self.trades.append(trade_res)

        self.print_report()

    def print_report(self):
        if not self.trades:
            print("\n❌ 조건에 부합하는 매매 포착 실패. 기간 또는 조건 보정 필요.")
            return

        df_trades = pd.DataFrame(self.trades)
        total_trades = len(df_trades)
        wins = df_trades[df_trades['result'] == 'WIN']
        losses = df_trades[df_trades['result'] == 'LOSS']

        win_rate = (len(wins) / total_trades) * 100
        total_profit = wins['return'].sum()
        total_loss = abs(losses['return'].sum()) if len(losses) > 0 else 0.0001
        profit_factor = total_profit / total_loss
        avg_return = df_trades['return'].mean() * 100

        print("\n" + "="*50)
        print("📊 STOCK BOT 2.0 알파 시그널 백테스팅 최종 결과 리포트")
        print("="*50)
        print(f"🎯 총 진입 횟수 (Total Trades) : {total_trades}회")
        print(f"📈 승률 (Win Rate)           : {win_rate:.2f}%")
        print(f"⚖️ Profit Factor (손익비)    : {profit_factor:.2f}")
        print(f"💰 거래당 평균 수익률         : {avg_return:.2f}%")
        print("="*50)


if __name__ == "__main__":
    # 지난달(2026년 7월) 1개월치 백테스팅 실행
    backtester = AlphaSignalBacktester(start_date="20260701", end_date="20260731")
    backtester.run()
