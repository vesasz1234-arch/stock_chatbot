import datetime
import numpy as np
import pandas as pd
import FinanceDataReader as fdr
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings("ignore")

class UpgradedTrailingStopMinuteBacktester:
    def __init__(self, timeframe='5m', initial_balance=1000000, max_positions=5, allocation_ratio=0.20):
        self.timeframe = timeframe
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.max_positions = max_positions
        self.allocation_ratio = allocation_ratio
        self.slippage_tax = 0.0028 # 수수료 + 세금 + 슬리피지 (0.28%)
        self.trade_history = []
        self.daily_equity = []

    def fetch_minute_data(self, ticker):
        for suffix in ['.KS', '.KQ']:
            try:
                yf_ticker = f"{ticker}{suffix}"
                df = yf.download(yf_ticker, period="1mo", interval=self.timeframe, progress=False)
                if not df.empty and len(df) > 100:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
                    return ticker, df
            except Exception:
                pass
        return ticker, None

    def preprocess_minute_indicators(self, df):
        df['MA5'] = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        vol_ma10 = df['Volume'].shift(1).rolling(10).mean() + 1e-9
        df['Vol_Surge'] = df['Volume'] / vol_ma10

        prev_close = df['Close'].shift(1)
        tr = pd.concat([
            df['High'] - df['Low'],
            (df['High'] - prev_close).abs(),
            (df['Low'] - prev_close).abs()
        ], axis=1).max(axis=1)
        df['ATR14'] = tr.rolling(14).mean()
        df['ATR_Pct'] = (df['ATR14'] / (df['Close'] + 1e-9)) * 100
        return df.dropna()

    def run_simulation(self, top_n=50):
        print(f"📊 [유니버스] 거래대금 상위 {top_n}개 종목 최신 분봉 데이터 수집 중...")
        df_krx = fdr.StockListing('KRX').dropna(subset=['Marcap'])
        top_tickers = df_krx.sort_values(by='Marcap', ascending=False).head(top_n)['Code'].tolist()

        minute_data_dict = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(self.fetch_minute_data, ticker) for ticker in top_tickers]
            for future in as_completed(futures):
                ticker, df = future.result()
                if df is not None:
                    minute_data_dict[ticker] = self.preprocess_minute_indicators(df)

        if not minute_data_dict:
            print("❌ 분봉 데이터 수집에 실패했습니다.")
            return

        print(f"✅ {len(minute_data_dict)}개 종목 분봉 데이터 수집 완료!\n")
        print(f"🚀 [09:00~10:00 골든타임 + 트레일링 스탑 엔진] 실시간 연산 시작...\n")

        all_timestamps = sorted(list(set.union(*[set(df.index) for df in minute_data_dict.values()])))
        all_dates = sorted(list(set([ts.strftime("%Y-%m-%d") for ts in all_timestamps])))

        for date_str in all_dates:
            active_positions_today = {}
            scanned_today = set()

            for ticker, df in minute_data_dict.items():
                df_today = df[df.index.strftime("%Y-%m-%d") == date_str]
                if df_today.empty:
                    continue

                for idx, row in df_today.iterrows():
                    time_val = idx.time()

                    # 1. 진입 타임윈도우: 09:00 ~ 10:00 골든타임
                    if datetime.time(9, 0) <= time_val <= datetime.time(10, 0):
                        if ticker not in scanned_today and len(active_positions_today) < self.max_positions:
                            cond_trend = (row['Close'] > row['Open']) and (row['Close'] > row['MA5'])
                            cond_volume = row['Vol_Surge'] >= 3.0

                            if cond_trend and cond_volume:
                                scanned_today.add(ticker)
                                entry_price = float(row['Close'])
                                atr_pct = float(row['ATR_Pct'])

                                if atr_pct >= 0.5:
                                    target_tp, stop_sl = 0.04, -0.015
                                else:
                                    target_tp, stop_sl = 0.02, -0.008

                                active_positions_today[ticker] = {
                                    'entry_time': idx,
                                    'entry_price': entry_price,
                                    'target_price': entry_price * (1 + target_tp),
                                    'stop_price': entry_price * (1 + stop_sl),
                                    'target_tp': target_tp,
                                    'initial_sl': stop_sl,
                                    'is_closed': False,
                                    'trailing_activated': False
                                }

                    # 2. 실시간 트레일링 스탑 및 청산 감시
                    if ticker in active_positions_today and not active_positions_today[ticker]['is_closed']:
                        pos = active_positions_today[ticker]

                        if idx > pos['entry_time']:
                            curr_high = float(row['High'])
                            curr_low = float(row['Low'])
                            curr_close = float(row['Close'])

                            if not pos['trailing_activated'] and curr_high >= pos['entry_price'] * 1.015:
                                pos['stop_price'] = pos['entry_price']
                                pos['trailing_activated'] = True

                            hit_tp = curr_high >= pos['target_price']
                            hit_sl = curr_low <= pos['stop_price']
                            is_market_close = time_val >= datetime.time(15, 15)

                            if hit_tp or hit_sl or is_market_close:
                                pnl = 0.0
                                reason = ""
                                if hit_tp and not hit_sl:
                                    pnl = pos['target_tp'] - self.slippage_tax
                                    reason = "익절 달성"
                                elif hit_sl and not hit_tp:
                                    if pos['trailing_activated'] and pos['stop_price'] == pos['entry_price']:
                                        pnl = -self.slippage_tax
                                        reason = "트레일링 본전 방어"
                                    else:
                                        pnl = pos['initial_sl'] - self.slippage_tax
                                        reason = "손절 실행"
                                else:
                                    pnl = ((curr_close - pos['entry_price']) / pos['entry_price']) - self.slippage_tax
                                    reason = "장마감 강제청산"

                                allocated_capital = self.balance * self.allocation_ratio
                                pnl_amount = allocated_capital * pnl
                                self.balance += pnl_amount

                                self.trade_history.append({
                                    'date': date_str,
                                    'time': idx.strftime("%H:%M"),
                                    'ticker': ticker,
                                    'pnl_pct': pnl * 100,
                                    'pnl_amount': pnl_amount,
                                    'reason': reason,
                                    'balance': self.balance
                                })
                                pos['is_closed'] = True

            self.daily_equity.append({'date': date_str, 'balance': self.balance})

        self.report_results()

    def report_results(self):
        df_trades = pd.DataFrame(self.trade_history)
        if df_trades.empty:
            print("⚠️ 조건에 부합하는 매매 내역이 없습니다.")
            return

        total_return = ((self.balance - self.initial_balance) / self.initial_balance) * 100
        total_trades = len(df_trades)
        wins = df_trades[df_trades['pnl_pct'] > 0]
        win_rate = (len(wins) / total_trades) * 100

        df_trades['peak'] = df_trades['balance'].cummax()
        df_trades['drawdown'] = (df_trades['balance'] - df_trades['peak']) / df_trades['peak']
        mdd = df_trades['drawdown'].min() * 100

        print("="*55)
        print(f"📈 [골든타임 09:00~10:00 + 트레일링 스탑 최적화 리포트]")
        print("="*55)
        print(f"• 최종 자산: {int(self.balance):,} 원 (초기 자금: {int(self.initial_balance):,} 원)")
        print(f"• 누적 수익률: {total_return:+.2f}%")
        print(f"• 최대 낙폭 (MDD): {mdd:.2f}%")
        print(f"• 총 거래 횟수: {total_trades:,} 회")
        print(f"• 승률: {win_rate:.2f}%")
        print("="*55)

if __name__ == "__main__":
    tester = UpgradedTrailingStopMinuteBacktester(timeframe='5m', initial_balance=1000000)
    tester.run_simulation(top_n=50)