import argparse
import json
import os
import sys
import time
import datetime
from datetime import timezone, timedelta
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import requests
import warnings
warnings.filterwarnings("ignore")

# =========================================================
# ⏰ 한국 타임존(KST) 및 환경변수 설정
# =========================================================
KST = timezone(timedelta(hours=9))

APP_KEY = os.environ.get("KIS_APP_KEY") or "PSSDHdJ44C6dUaTxnD28Vht6hOHBhFRjiFkA"
APP_SECRET = os.environ.get("KIS_APP_SECRET") or "5/AQN2eoGs1hX/BuYqjsWCPr3zUMLfBcdlic8zp7Axr3JzESm1J0roAxyjOdOuY30sEDilPdu27ELVD/nqiUNJV9wvCtl4aEdZFlhoK5JOfqfVA98yMRK3J5bBQwJm/Ej0Bd1tX2Qb+ecvniSS4mmbZclDrh1vRqby9ZflhX+kKTvmNXJOg="
URL_BASE = "https://openapi.koreainvestment.com:9443"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"

class StockAlphaBot:
    def __init__(self, top_n=200):
        self.top_n = top_n
        self.scanned_signals = set()
        self.universe = {}

    def send_telegram_msg(self, message: str):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            res = requests.post(url, data=payload, timeout=5)
            if res.status_code != 200:
                payload_plain = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
                requests.post(url, data=payload_plain, timeout=5)
        except Exception as e:
            print(f"❌ 텔레그램 송신 오류: {e}")

    def send_discord_msg(self, message: str):
        if not DISCORD_WEBHOOK_URL:
            return
        headers = {"Content-Type": "application/json"}
        payload = {
            "content": message,
            "username": "🤖 [ALPHA BOT]",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/2593/2593211.png"
        }
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=5)
            if res.status_code in [200, 204]:
                print("✅ [디스코드] 알파 시그널 송출 완료!")
        except Exception as e:
            print(f"❌ 디스코드 송신 오류: {e}")

    def broadcast_signal(self, message: str):
        self.send_telegram_msg(message)
        self.send_discord_msg(message)

    def update_universe(self):
        print(f"🔍 [Universe Engine] 상위 {self.top_n}개 주도주 유니버스 스캔 중...")
        try:
            df_krx = fdr.StockListing('KRX').dropna(subset=['Marcap'])
            df_sorted = df_krx.sort_values(by='Marcap', ascending=False)
            self.universe = {}
            for _, row in df_sorted.head(self.top_n).iterrows():
                code = str(row['Code']).zfill(6)
                self.universe[row['Name']] = code
            print(f"✅ 주도주 유니버스 {len(self.universe)}개 종목 로딩 완료.\n")
        except Exception as e:
            print(f"❌ 유니버스 로딩 실패: {e}")

    def check_kospi_regime(self):
        try:
            now_kst = datetime.datetime.now(KST)
            df_k = fdr.DataReader('KS11', now_kst - datetime.timedelta(days=40))
            if df_k.empty or len(df_k) < 20:
                return True
            df_k['MA20'] = df_k['Close'].rolling(20).mean()
            is_bull = df_k.iloc[-1]['Close'] >= df_k.iloc[-1]['MA20']
            return is_bull
        except Exception as e:
            print(f"⚠️ 코스피 레짐 체크 예외 (기본 허용): {e}")
            return True

    def calculate_time_weighted_rvol(self, df):
        now_kst = datetime.datetime.now(KST)
        market_start = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if now_kst < market_start:
            elapsed_minutes = 1.0
        else:
            elapsed_minutes = max((now_kst - market_start).total_seconds() / 60.0, 1.0)
        
        time_factor = min(elapsed_minutes / 390.0, 1.0)
        
        vol_ma10 = df['Volume'].iloc[:-1].tail(10).mean() + 1e-9
        current_volume = df['Volume'].iloc[-1]
        
        projected_volume = current_volume / time_factor
        return projected_volume / vol_ma10

    def scan_stock_alpha(self, name: str, ticker: str):
        try:
            now_kst = datetime.datetime.now(KST)
            start_dt = (now_kst - datetime.timedelta(days=45)).strftime("%Y-%m-%d")
            df = fdr.DataReader(ticker, start_dt)
            if df.empty or len(df) < 20:
                return

            df['MA5'] = df['Close'].rolling(5).mean()
            df['MA20'] = df['Close'].rolling(20).mean()

            rvol_weighted = self.calculate_time_weighted_rvol(df)
            df['RVOL'] = rvol_weighted

            prev_close = df['Close'].shift(1)
            tr = pd.concat([
                df['High'] - df['Low'],
                (df['High'] - prev_close).abs(),
                (df['Low'] - prev_close).abs()
            ], axis=1).max(axis=1)
            df['ATR14'] = tr.rolling(14).mean()
            df['ATR_MA20'] = df['ATR14'].rolling(20).mean()
            df['ATR_Ratio'] = df['ATR14'] / (df['ATR_MA20'] + 1e-9)

            std20 = df['Close'].rolling(20).std()
            df['BB_Upper'] = df['MA20'] + (std20 * 2.0)

            df['Gap_Up_Pct'] = (df['Open'] - prev_close) / (prev_close + 1e-9)
            df['First_Breakout'] = df['Close'].shift(1) < df['BB_Upper'].shift(1)
            
            total_range = df['High'] - df['Low'] + 1e-9
            upper_shadow = df['High'] - np.maximum(df['Open'], df['Close'])
            df['Upper_Shadow_Ratio'] = upper_shadow / total_range

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
                    target_price = int(curr_price * 1.018)
                    stop_price = int(curr_price * 0.992)

                    msg = (
                        f"🚨 **[ALPHA BOT] 실시간 알파 타격 포착**\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"• **종목명**: {name} (`{ticker}`)\n"
                        f"• **현재가 (진입)**: {curr_price:,}원\n"
                        f"• 🎯 **목표가 (+1.8%)**: {target_price:,}원\n"
                        f"• 🛑 **손절가 (-0.8%)**: {stop_price:,}원\n"
                        f"────────────────────\n"
                        f"📊 **시간가중 수급(RVOL)**: {today['RVOL']:.2f}배\n"
                        f"📈 **변동성 비율(ATR)**: {today['ATR_Ratio']:.2f}배\n"
                        f"💡 **알고리즘**: Wall St. Real Fix (손익비 1:2.25)\n"
                        f"⏰ **포착시각**: {now_kst.strftime('%H:%M:%S')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    self.broadcast_signal(msg)
                    print(f"🎯 [알파 시그널 송출] {name} ({ticker}) - 진입가: {curr_price:,}원")

        except Exception as e:
            pass

    def run_single_scan(self):
        """깃허브 액션 스케줄러용 1회 스캔 스크립트"""
        self.update_universe()
        now_kst = datetime.datetime.now(KST)
        print(f"🔎 [{now_kst.strftime('%Y-%m-%d %H:%M:%S KST')}] 알파봇 스캔 시작")
        
        is_bull_market = self.check_kospi_regime()
        if not is_bull_market:
            print("⚠️ 코스피 하락장 스위치 발동 - 진입 동결")
            return

        for name, ticker in list(self.universe.items()):
            self.scan_stock_alpha(name, ticker)
            time.sleep(0.02)
        print("✨ 스캔 완료!")

    def run_market_loop(self):
        """로컬 PC / EC2 연속 실행용 루프"""
        self.update_universe()
        start_msg = (
            "🏛️ **[ALPHA BOT] 퀀트 시그널 파이프라인 가동**\n"
            "• 유동성 주도주 실시간 감시 시작\n"
            "• 승률 target 64%+ | 손익비 +1.8% / -0.8%"
        )
        self.broadcast_signal(start_msg)

        while True:
            now_kst = datetime.datetime.now(KST)
            is_market_open = (now_kst.hour == 9 and now_kst.minute >= 0) or (10 <= now_kst.hour < 15) or (now_kst.hour == 15 and now_kst.minute <= 30)

            if is_market_open:
                if self.check_kospi_regime():
                    print(f"🔎 [{now_kst.strftime('%H:%M:%S')}] 실시간 알파 스캔 중...")
                    for name, ticker in list(self.universe.items()):
                        self.scan_stock_alpha(name, ticker)
                        time.sleep(0.02)
            time.sleep(20)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="1회 스캔 후 종료 (GitHub Actions 전용)")
    args = parser.parse_args()

    bot = StockAlphaBot(top_n=200)
    if args.once:
        bot.run_single_scan()
    else:
        bot.run_market_loop()