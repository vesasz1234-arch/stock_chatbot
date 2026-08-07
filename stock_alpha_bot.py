import requests
import json
import time
import datetime
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import os
import warnings
warnings.filterwarnings("ignore")

# =========================================================
# 🔑 1. KIS API & 메신저 인증 정보 (디스코드/텔레그램 하이브리드)
# =========================================================
APP_KEY = os.environ.get("KIS_APP_KEY") or "PSSDHdJ44C6dUaTxnD28Vht6hOHBhFRjiFkA"
APP_SECRET = os.environ.get("KIS_APP_SECRET") or "5/AQN2eoGs1hX/BuYqjsWCPr3zUMLfBcdlic8zp7Axr3JzESm1J0roAxyjOdOuY30sEDilPdu27ELVD/nqiUNJV9wvCtl4aEdZFlhoK5JOfqfVA98yMRK3J5bBQwJm/Ej0Bd1tX2Qb+ecvniSS4mmbZclDrh1vRqby9ZflhX+kKTvmNXJOg="
URL_BASE = "https://openapi.koreainvestment.com:9443"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

# [알파봇 전용 디스코드 웹후크 URL - #🤖-알파-시그널 채널용]
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"

class StockAlphaBot:
    def __init__(self, top_n=300):
        self.top_n = top_n
        self.access_token = self.get_access_token()
        self.scanned_signals = set()
        self.universe = {}

    def get_access_token(self):
        """KIS API 인증 토큰 발급"""
        url = f"{URL_BASE}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET
        }
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
            if res.status_code == 200:
                print("✅ [KIS Open API] 실시간 알파 시그널 서버 연동 성공!")
                return res.json().get("access_token")
            return None
        except Exception as e:
            print(f"❌ KIS API 토큰 발급 실패: {e}")
            return None

    def send_telegram_msg(self, message: str):
        """텔레그램 실시간 알림 송신"""
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            res = requests.post(url, data=payload, timeout=5)
            if res.status_code != 200:
                payload_plain = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
                requests.post(url, data=payload_plain, timeout=5)
        except Exception as e:
            print(f"❌ 텔레그램 송신 오류: {e}")

    def send_discord_msg(self, message: str):
        """디스코드 실시간 알림 송신"""
        if not DISCORD_WEBHOOK_URL:
            return
        headers = {"Content-Type": "application/json"}
        payload = {
            "content": message,
            "username": "🤖 [ALPHA BOT]",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/2593/2593211.png"
        }
        try:
            requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=5)
            print("✅ [디스코드] 알파 시그널 송출 완료!")
        except Exception as e:
            print(f"❌ 디스코드 송신 오류: {e}")

    def broadcast_signal(self, message: str):
        """텔레그램 및 디스코드 동시 송출"""
        self.send_telegram_msg(message)
        self.send_discord_msg(message)

    def update_universe(self):
        """시가총액/거래대금 상위 300개 주도주 동적 유니버스 갱신"""
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
        """KOSPI 지수 20일선 추세 검증 (하락장 폭락 방어)"""
        try:
            df_k = fdr.DataReader('KS11', datetime.datetime.now() - datetime.timedelta(days=40))
            if df_k.empty or len(df_k) < 20:
                return True
            df_k['MA20'] = df_k['Close'].rolling(20).mean()
            is_bull = df_k.iloc[-1]['Close'] >= df_k.iloc[-1]['MA20']
            return is_bull
        except Exception:
            return True

    def calculate_time_weighted_rvol(self, df):
        """장중 경과 시간을 반영한 시간가중 상대거래량(RVOL) 산출"""
        now = datetime.datetime.now()
        market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if now < market_start:
            elapsed_minutes = 1.0
        else:
            elapsed_minutes = max((now - market_start).total_seconds() / 60.0, 1.0)
        
        time_factor = min(elapsed_minutes / 390.0, 1.0)
        
        vol_ma10 = df['Volume'].iloc[:-1].tail(10).mean() + 1e-9
        current_volume = df['Volume'].iloc[-1]
        
        projected_volume = current_volume / time_factor
        return projected_volume / vol_ma10

    def scan_stock_alpha(self, name: str, ticker: str):
        """Wall Street Real Fix 알파 알고리즘 장중 실시간 스캔"""
        try:
            start_dt = (datetime.datetime.now() - datetime.timedelta(days=45)).strftime("%Y-%m-%d")
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
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")

            # 🎯 알파 매수 알고리즘 조건
            cond_gap = today['Gap_Up_Pct'] <= 0.025               # 1. 시가 갭상승 <= 2.5%
            cond_first = today['First_Breakout'] == True           # 2. 최초 돌파 첫날
            cond_shadow = today['Upper_Shadow_Ratio'] <= 0.30     # 3. 윗꼬리 <= 30%
            cond_volume = today['RVOL'] >= 1.35                   # 4. 시간가중 RVOL >= 1.35배
            cond_trend = (today['Close'] > today['Open']) and (today['Close'] > today['MA5']) # 5. 양봉 모멘텀
            cond_volatility = (today['Close'] >= today['BB_Upper']) or (today['ATR_Ratio'] >= 1.10) # 6. 변동성 스파이크

            if cond_gap and cond_first and cond_shadow and cond_volume and cond_trend and cond_volatility:
                signal_key = f"{ticker}_{today_str}"
                
                if signal_key not in self.scanned_signals:
                    self.scanned_signals.add(signal_key)

                    curr_price = int(today['Close'])
                    target_price = int(curr_price * 1.018)  # +1.8% 익절
                    stop_price = int(curr_price * 0.992)    # -0.8% 손절

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
                        f"⏰ **포착시각**: {datetime.datetime.now().strftime('%H:%M:%S')}\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    self.broadcast_signal(msg)
                    print(f"🎯 [알파 시그널 송출] {name} ({ticker}) - 진입가: {curr_price:,}원")

        except Exception:
            pass

    def run_market_loop(self):
        """장중 모니터링 메인 루프"""
        self.update_universe()
        
        start_msg = (
            "🏛️ **[ALPHA BOT] 퀀트 시그널 파이프라인 가동**\n"
            "• 상위 300개 유동성주 실시간 감시 시작\n"
            "• 승률 target 64%+ | 손익비 +1.8% / -0.8%"
        )
        self.broadcast_signal(start_msg)
        print("🚀 [stock_alpha_bot] 장중 실시간 스캔 가동 중... (Ctrl+C 종료)")

        try:
            while True:
                now = datetime.datetime.now()
                # 정규장 시간 (09:00 ~ 15:30)
                is_market_open = (now.hour == 9 and now.minute >= 0) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 30)

                if is_market_open:
                    is_bull_market = self.check_kospi_regime()
                    if is_bull_market:
                        print(f"🔎 [{now.strftime('%H:%M:%S')}] 300개 유니버스 실시간 알파 스캔 실행 중...")
                        for name, ticker in list(self.universe.items()):
                            self.scan_stock_alpha(name, ticker)
                            time.sleep(0.05)
                    else:
                        print(f"⚠️ [{now.strftime('%H:%M:%S')}] KOSPI 하락장 스위치 발동 - 진입 동결 중")

                time.sleep(20)

        except KeyboardInterrupt:
            print("\n알림봇 가동을 안전하게 종료합니다.")

if __name__ == "__main__":
    bot = StockAlphaBot(top_n=300)
    bot.run_market_loop()   