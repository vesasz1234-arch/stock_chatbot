import argparse
import json
import os
import sys
import time
import datetime
from datetime import timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import requests
import warnings
warnings.filterwarnings("ignore")

# =========================================================
# CONFIGURATION
# =========================================================
KST = timezone(timedelta(hours=9))

DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_ALPHA_WEBHOOK_URL")
    or os.environ.get("DISCORD_WEBHOOK_URL")
    or "https://discordapp.com/api/webhooks/1534112008767803433/B1S87u-nnaokeMR2lut-FAPv1PJAbeVuQunoWr-4AoZfrG4g70XwhuD8PATpApYgeFt1"
)

class StockAlphaBot:
    def __init__(self, top_n=200, ignore_regime=False):
        self.top_n = top_n
        self.ignore_regime = ignore_regime
        self.scanned_signals = set()
        self.universe = {}

    def check_kospi_regime(self):
        """코스피 하락장 필터 임시 무력화 (실험용)"""
        return True

    def get_naver_top200(self):
        """KRX IP 차단 대응 네이버 금융 시가총액 상위 종목 수집 백업 엔진"""
        universe = {}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            for market in ["KOSPI", "KOSDAQ"]:
                for page in range(1, 3):
                    url = f"https://m.stock.naver.com/api/stocks/marketCap/{market}?page={page}&pageSize=100"
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        stocks = res.json().get("stocks", [])
                        for s in stocks:
                            code = str(s.get("itemCode", "")).zfill(6)
                            name = str(s.get("stockName", ""))
                            marcap = int(s.get("marketValue", 0))
                            if code and name:
                                universe[name] = (code, marcap)
        except Exception as e:
            print(f"[NAVER FALLBACK ERROR] {e}")

        sorted_items = sorted(universe.items(), key=lambda x: x[1][1], reverse=True)[:self.top_n]
        return {name: item[0] for name, item in sorted_items}

    def update_universe(self):
        print("🔍 [Universe Engine] 상위 200개 주도주 유니버스 스캔 중...")
        try:
            df_krx = fdr.StockListing('KRX').dropna(subset=['Marcap'])
            df_sorted = df_krx.sort_values(by='Marcap', ascending=False)
            self.universe = {row['Name']: str(row['Code']).zfill(6) for _, row in df_sorted.head(self.top_n).iterrows()}
        except Exception as e:
            print(f"⚠️ KRX API 접근 차단 감지. 네이버 백업 엔진 가동...")
            self.universe = self.get_naver_top200()

        if self.universe:
            print(f"✅ 주도주 유니버스 {len(self.universe)}개 종목 로딩 완료.\n")
        else:
            print("❌ 유니버스 로딩 최종 실패.\n")

    def calculate_time_weighted_rvol(self, df):
        now_kst = datetime.datetime.now(KST)
        market_start = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed_minutes = max((now_kst - market_start).total_seconds() / 60.0, 1.0) if now_kst >= market_start else 1.0
        time_factor = min(elapsed_minutes / 390.0, 1.0)
        vol_ma10 = df['Volume'].iloc[:-1].tail(10).mean() + 1e-9
        return (df['Volume'].iloc[-1] / time_factor) / vol_ma10

    def send_discord_msg(self, message: str):
        if not DISCORD_WEBHOOK_URL:
            return
        headers = {"Content-Type": "application/json"}
        payload = {
            "content": message,
            "username": "🤖 [ALPHA SIGNAL BOT]",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/2593/2593211.png"
        }
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=5)
            if res.status_code in (200, 204):
                print("✅ [디스코드] 유료 알파 시그널 채널 송출 완료!")
        except Exception:
            pass

    def scan_stock(self, name: str, ticker: str):
        try:
            now_kst = datetime.datetime.now(KST)
            start_dt = (now_kst - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
            df = fdr.DataReader(ticker, start_dt)
            if df.empty or len(df) < 35:
                return

            df['MA5'] = df['Close'].rolling(5).mean()
            df['MA20'] = df['Close'].rolling(20).mean()
            df['RVOL'] = self.calculate_time_weighted_rvol(df)

            prev_close = df['Close'].shift(1)
            tr = pd.concat([
                df['High'] - df['Low'],
                (df['High'] - prev_close).abs(),
                (df['Low'] - prev_close).abs()
            ], axis=1).max(axis=1)
            df['ATR14'] = tr.rolling(14).mean()
            df['ATR_MA20'] = df['ATR14'].rolling(20).mean()

            atr_ma20_val = df['ATR_MA20'].iloc[-1] if not pd.isna(df['ATR_MA20'].iloc[-1]) else df['ATR14'].iloc[-1]
            df['ATR_Ratio'] = float(df['ATR14'].iloc[-1] / (atr_ma20_val + 1e-9)) if atr_ma20_val > 0 else 1.0

            std20 = df['Close'].rolling(20).std()
            df['BB_Upper'] = df['MA20'] + (std20 * 2.0)
            df['Gap_Up_Pct'] = (df['Open'] - prev_close) / (prev_close + 1e-9)
            df['First_Breakout'] = df['Close'].shift(1) < df['BB_Upper'].shift(1)

            total_range = df['High'] - df['Low'] + 1e-9
            df['Upper_Shadow_Ratio'] = (df['High'] - np.maximum(df['Open'], df['Close'])) / total_range

            today = df.iloc[-1]
            today_str = now_kst.strftime("%Y-%m-%d")

            cond_gap = today['Gap_Up_Pct'] <= 0.025
            cond_first = today['First_Breakout'] == True
            cond_shadow = today['Upper_Shadow_Ratio'] <= 0.30
            cond_volume = today['RVOL'] >= 1.35
            cond_trend = (today['Close'] > today['Open']) and (today['Close'] > today['MA5'])
            cond_volatility = (today['Close'] >= today['BB_Upper']) or (today['ATR_Ratio'] >= 1.10)

            if cond_gap and cond_first and cond_shadow and cond_volume and cond_trend and cond_volatility:
                signal_key = f"{ticker}_{today_str}"

                if signal_key not in self.scanned_signals:
                    self.scanned_signals.add(signal_key)
                    curr_price = int(today['Close'])

                    print(f"🎯 [알파 시그널 송출] {name} ({ticker}) - 진입가: {curr_price:,}원")
                    msg = (
                        f"🚨 **[ALPHA SIGNAL] 주도주 돌파 포착 시그널**\n"
                        f"• 종목명: **{name}** (`{ticker}`)\n"
                        f"• 진입 권장가: {curr_price:,}원\n"
                        f"• RVOL: {today['RVOL']:.2f}x | ATR 비중: {today['ATR_Ratio']:.2f}\n"
                        f"• 🎯 목표 수익률: +1.8% | 🛑 손절 기준: -0.8%"
                    )
                    self.send_discord_msg(msg)
        except Exception:
            pass

    def run_scan(self):
        self.update_universe()
        now_kst = datetime.datetime.now(KST)
        now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S KST")
        
        print(f"🔎 [{now_str}] 멀티스레드 알파 초고속 스캔 시작...")

        if not self.ignore_regime and not self.check_kospi_regime():
            print("⚠️ 코스피 하락장 스위치 발동 - 진입 동결 (정상 동작)")
            return

        start_time = time.time()
        items = list(self.universe.items())
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(self.scan_stock, name, ticker) for name, ticker in items]
            for future in as_completed(futures):
                pass

        elapsed = time.time() - start_time
        print(f"✨ 멀티스레드 알파 스캔 완료! (소요시간: {elapsed:.2f}초)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run scan once and exit")
    parser.add_argument("--ignore-regime", action="store_true", help="Bypass Kospi market regime filter")
    args = parser.parse_args()

    bot = StockAlphaBot(top_n=200, ignore_regime=args.ignore_regime)
    bot.run_scan()