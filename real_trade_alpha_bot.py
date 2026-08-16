import argparse
import json
import os
import sys
import time
import datetime
import threading
from datetime import timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import requests
import warnings
warnings.filterwarnings("ignore")

# =========================================================
# CONFIGURATION & REAL ACCOUNT SETTINGS
# =========================================================
KST = timezone(timedelta(hours=9))

APP_KEY = os.environ.get("KIS_APP_KEY") or "PSSDHdJ44C6dUaTxnD28Vht6hOHBhFRjiFkA"
APP_SECRET = os.environ.get("KIS_APP_SECRET") or "5/AQN2eoGs1hX/BuYqjsWCPr3zUMLfBcdlic8zp7Axr3JzESm1J0roAxyjOdOuY30sEDilPdu27ELVD/nqiUNJV9wvCtl4aEdZFlhoK5JOfqfVA98yMRK3J5bBQwJm/Ej0Bd1tX2Qb+ecvniSS4mmbZclDrh1vRqby9ZflhX+kKTvmNXJOg="
URL_BASE = "https://openapi.koreainvestment.com:9443"

# [일반 위탁계좌]
CANO = "64165136"
ACNT_PRDT_CD = "01"

DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_ALPHA_WEBHOOK_URL")
    or os.environ.get("DISCORD_WEBHOOK_URL")
    or "https://discordapp.com/api/webhooks/1534112008767803433/B1S87u-nnaokeMR2lut-FAPv1PJAbeVuQunoWr-4AoZfrG4g70XwhuD8PATpApYgeFt1"
)

class RealTradeAlphaBot:
    def __init__(self, top_n=200, ignore_regime=False, max_positions=5, allocation_ratio=0.20):
        self.top_n = top_n
        self.ignore_regime = ignore_regime
        self.max_positions = max_positions          # 최대 동시 보유 종목 수 (5개)
        self.allocation_ratio = allocation_ratio    # 종목당 진입 비중 (계좌의 20%)
        self.access_token = None
        self.scanned_signals = set()
        self.active_positions = {}
        self.universe = {}
        self.buy_lock = threading.Lock()
        self.init_kis_token()
        self.sync_holdings_from_balance()
        self.start_dedicated_monitor_thread()

    def init_kis_token(self):
        """한국투자증권 실전 API 토큰 발급"""
        url = f"{URL_BASE}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
        for attempt in range(3):
            try:
                res = requests.post(url, headers=headers, data=json.dumps(body), timeout=5)
                if res.status_code == 200:
                    self.access_token = res.json().get("access_token")
                    print("[SUCCESS] KIS Real API Token Generated Successfully!")
                    return
                else:
                    if "EGW00133" in res.text:
                        print(f"[WARN] Token limit hit. Waiting 5s... ({attempt+1}/3)")
                        time.sleep(5)
                    else:
                        print(f"[ERROR] Token Failed: {res.text}")
                        break
            except Exception as e:
                print(f"[EXCEPTION] Token Error: {e}")
                break

    def start_dedicated_monitor_thread(self):
        """독립 매도 전용 백그라운드 스레드 가동 (1초 간격 미세 감시)"""
        def dedicated_monitor_loop():
            while True:
                try:
                    now_kst = datetime.datetime.now(KST)
                    is_market_open = (now_kst.hour == 9 and now_kst.minute >= 0) or (10 <= now_kst.hour < 15) or (now_kst.hour == 15 and now_kst.minute <= 20)
                    if is_market_open:
                        self.monitor_and_auto_sell()
                except Exception as e:
                    print(f"[MONITOR THREAD EXCEPTION] {e}")
                time.sleep(1)

        t = threading.Thread(target=dedicated_monitor_loop, daemon=True)
        t.start()
        print("⚡ [SYSTEM] 1초 단위 초고속 독립 매도 감시 스레드 가동 완료!")

    def check_kospi_regime(self):
        """코스피 하락장 필터 임시 무력화 (실험용)"""
        return True

    def get_kis_realtime_stock_info(self, ticker: str):
        """한투 실시간 시세 단일 조회 (FHKST01010100)"""
        if not self.access_token:
            return None
        url = f"{URL_BASE}/uapi/domestic-stock/v1/quoting/inquire-price"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "FHKST01010100"
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker
        }
        try:
            res = requests.get(url, headers=headers, params=params, timeout=3)
            if res.status_code == 200:
                output = res.json().get("output", {})
                stck_prpr = output.get("stck_prpr")
                if stck_prpr:
                    return {
                        "price": int(stck_prpr),
                        "open": int(output.get("stck_oprc", 0)),
                        "high": int(output.get("stck_hgpr", 0)),
                        "low": int(output.get("stck_lwpr", 0)),
                        "volume": int(output.get("acml_vol", 0)),
                        "change_rate": float(output.get("prdy_vrss_rt", 0.0))
                    }
        except Exception:
            pass
        return None

    def sync_holdings_from_balance(self):
        """실잔고 동기화"""
        if not self.access_token:
            return
        url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "TTTC8434R"
        }
        params = {
            "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                output1 = res.json().get("output1", [])
                for item in output1:
                    qty = int(item.get("hldg_qty", 0))
                    if qty > 0:
                        ticker = item.get("pdno")
                        name = item.get("prdt_name")
                        buy_price = float(item.get("pchs_avg_pric", 0))
                        if buy_price > 0 and ticker not in self.active_positions:
                            target_price = int(buy_price * 1.018)
                            stop_price = int(buy_price * 0.992)
                            self.active_positions[ticker] = {
                                "name": name,
                                "buy_price": int(buy_price),
                                "qty": qty,
                                "target": target_price,
                                "stop": stop_price
                            }
                            print(f"📦 [실잔고 동기화 완료] {name}({ticker}) {qty}주 | 평단가: {int(buy_price):,}원 | 익절가(+1.8%): {target_price:,}원 | 손절가(-0.8%): {stop_price:,}원")
        except Exception as e:
            print(f"[SYNC ERROR] {e}")

    def get_available_cash(self):
        """실시간 주식주문 가능 예수금 조회"""
        if not self.access_token:
            return 0
        
        url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "TTTC8908R"
        }
        params = {
            "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "PDNO": "005930",
            "ORD_UNPR": "0", "ORD_DVSN": "01", "CORD_DVSN": "00", "OVRS_ICLD_YN": "N"
        }
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                output = res.json().get("output", {})
                cash = int(output.get("ord_psbl_cash", 0))
                if cash > 0:
                    return cash
        except Exception:
            pass

        url_bal = f"{URL_BASE}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers_bal = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "TTTC8434R"
        }
        params_bal = {
            "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }
        try:
            res = requests.get(url_bal, headers=headers_bal, params=params_bal, timeout=5)
            if res.status_code == 200:
                output2 = res.json().get("output2", [{}])[0]
                cash = max(
                    int(output2.get("dnca_tot_amt", 0)),
                    int(output2.get("ord_psbl_cash", 0)),
                    int(output2.get("prvs_rcdl_excn_amt", 0))
                )
                return cash
        except Exception:
            pass

        return 0

    def send_real_buy_order(self, name, ticker, curr_price):
        """현재가 기준 20% 비중 분할 시장가 매수 주문"""
        with self.buy_lock:
            if len(self.active_positions) >= self.max_positions:
                print(f"⚠️ [매수 스킵] 최대 포지션 개수 달성 ({len(self.active_positions)}/{self.max_positions})")
                return False, 0

            cash = self.get_available_cash()
            if cash < 10000:
                print(f"⚠️ [매수 스킵] 예수금 부족 (주문가능금액: {cash:,}원)")
                return False, 0

            # 예수금의 20% 금액을 현재가로 나누어 정확한 주수 산출
            target_cash = cash * self.allocation_ratio
            qty = int(target_cash // curr_price)

            if qty <= 0:
                print(f"⚠️ [매수 스킵] 수량 부족 (배정금액: {int(target_cash):,}원 / 현재가: {curr_price:,}원)")
                return False, 0

            url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/order-cash"
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {self.access_token}",
                "appkey": APP_KEY,
                "appsecret": APP_SECRET,
                "tr_id": "TTTC0802U"
            }
            body = {
                "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD,
                "PDNO": ticker, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"
            }
            try:
                res = requests.post(url, headers=headers, data=json.dumps(body), timeout=5)
                if res.status_code == 200 and res.json().get("rt_cd") == "0":
                    print(f"🔥 [실전 20% 분할 매수 체결 성공] {name}({ticker}) {qty:,}주 @ {curr_price:,}원 (투자금액: {qty * curr_price:,}원)")
                    return True, qty
                else:
                    print(f"[BUY ORDER REJECTED] {res.text}")
            except Exception as e:
                print(f"[BUY EXCEPTION] {e}")
            return False, 0

    def send_real_sell_order(self, name, ticker, qty, reason):
        """시장가 전량 매도 주문"""
        url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/order-cash"
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "TTTC0801U"
        }
        body = {
            "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD,
            "PDNO": ticker, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"
        }
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body), timeout=5)
            if res.status_code == 200 and res.json().get("rt_cd") == "0":
                print(f"💰 [실전 시장가 매도 체결 - {reason}] {name}({ticker}) {qty:,}주")
                return True
            else:
                print(f"[SELL ORDER REJECTED] {res.text}")
        except Exception as e:
            print(f"[SELL EXCEPTION] {e}")
        return False

    def send_discord_msg(self, message: str):
        if not DISCORD_WEBHOOK_URL:
            return
        headers = {"Content-Type": "application/json"}
        payload = {
            "content": message,
            "username": "🤖 [REAL TRADE ALPHA BOT]",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/2593/2593211.png"
        }
        try:
            requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=5)
        except Exception:
            pass

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
        print(f"🔍 [Universe Engine] Top {self.top_n} market cap universe loading...")
        try:
            df_krx = fdr.StockListing('KRX').dropna(subset=['Marcap'])
            df_sorted = df_krx.sort_values(by='Marcap', ascending=False)
            self.universe = {row['Name']: str(row['Code']).zfill(6) for _, row in df_sorted.head(self.top_n).iterrows()}
        except Exception as e:
            print(f"⚠️ KRX API 접근 차단 감지. 네이버 백업 엔진 가동...")
            self.universe = self.get_naver_top200()

        print(f"✅ Loaded {len(self.universe)} stocks.\n")

    def calculate_time_weighted_rvol(self, df):
        now_kst = datetime.datetime.now(KST)
        market_start = now_kst.replace(hour=9, minute=0, second=0, microsecond=0)
        elapsed_minutes = max((now_kst - market_start).total_seconds() / 60.0, 1.0) if now_kst >= market_start else 1.0
        time_factor = min(elapsed_minutes / 390.0, 1.0)
        vol_ma10 = df['Volume'].iloc[:-1].tail(10).mean() + 1e-9
        return (df['Volume'].iloc[-1] / time_factor) / vol_ma10

    def scan_stock_alpha(self, name: str, ticker: str):
        """대표님의 알파 퀀트 수식 스캔 및 실전 자동 매수 집행 엔진"""
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

                if signal_key not in self.scanned_signals and ticker not in self.active_positions:
                    self.scanned_signals.add(signal_key)

                    real_info = self.get_kis_realtime_stock_info(ticker)
                    curr_price = real_info["price"] if real_info else int(today['Close'])

                    print(f"🎯 [알파 퀀트 포착 ➔ 20% 분할 매수 실행] {name} ({ticker}) - 현재가: {curr_price:,}원")
                    
                    success, qty = self.send_real_buy_order(name, ticker, curr_price)
                    if success and qty > 0:
                        target_price = int(curr_price * 1.018)
                        stop_price = int(curr_price * 0.992)

                        self.active_positions[ticker] = {
                            "name": name, "buy_price": curr_price, "qty": qty,
                            "target": target_price, "stop": stop_price
                        }

                        msg = (
                            f"🚨 **[REAL TRADE] 알파 퀀트 20% 비중 매수 완료**\n"
                            f"• 종목명: **{name}** (`{ticker}`)\n"
                            f"• 체결가: {curr_price:,}원 ({qty:,}주 | 약 {qty * curr_price:,}원)\n"
                            f"• RVOL: {today['RVOL']:.2f}x | ATR 비중: {today['ATR_Ratio']:.2f}\n"
                            f"• 🎯 익절가 (+1.8%): {target_price:,}원 | 🛑 손절가 (-0.8%): {stop_price:,}원"
                        )
                        self.send_discord_msg(msg)
        except Exception:
            pass

    def monitor_and_auto_sell(self):
        """독립 스레드 매도 모니터링 (1초 단위 초고속 모니터링)"""
        if not self.active_positions:
            return

        for ticker, pos in list(self.active_positions.items()):
            try:
                curr_price = 0
                real_data = self.get_kis_realtime_stock_info(ticker)
                if real_data:
                    curr_price = real_data["price"]
                else:
                    start_dt = (datetime.datetime.now() - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
                    df_now = fdr.DataReader(ticker, start_dt)
                    if not df_now.empty:
                        curr_price = int(df_now['Close'].iloc[-1])

                if curr_price == 0:
                    continue

                profit_pct = ((curr_price - pos["buy_price"]) / pos["buy_price"]) * 100
                print(f"👀 [독립 감시 스레드] {pos['name']}({ticker}) 현재가: {curr_price:,}원 ({profit_pct:+.2f}%) | 목표가: {pos['target']:,}원 | 손절가: {pos['stop']:,}원")

                if curr_price >= pos["target"]:
                    if self.send_real_sell_order(pos["name"], ticker, pos["qty"], f"TARGET PROFIT (+{profit_pct:.2f}%)"):
                        profit = (curr_price - pos["buy_price"]) * pos["qty"]
                        msg = (
                            f"🎉 **[REAL TRADE] 익절 매도 성공 (+1.8% 달성)**\n"
                            f"• 종목: **{pos['name']}** (`{ticker}`)\n"
                            f"• 매수가: {pos['buy_price']:,}원 ➔ 매도가: {curr_price:,}원 ({profit_pct:+.2f}%)\n"
                            f"• 실현손익: +{profit:,}원"
                        )
                        self.send_discord_msg(msg)
                        del self.active_positions[ticker]

                elif curr_price <= pos["stop"]:
                    if self.send_real_sell_order(pos["name"], ticker, pos["qty"], f"STOP LOSS ({profit_pct:.2f}%)"):
                        loss = (curr_price - pos["buy_price"]) * pos["qty"]
                        msg = (
                            f"🛑 **[REAL TRADE] 손절 매도 실행 (-0.8%)**\n"
                            f"• 종목: **{pos['name']}** (`{ticker}`)\n"
                            f"• 매수가: {pos['buy_price']:,}원 ➔ 매도가: {curr_price:,}원 ({profit_pct:+.2f}%)\n"
                            f"• 실현손익: {loss:,}원"
                        )
                        self.send_discord_msg(msg)
                        del self.active_positions[ticker]
            except Exception as e:
                print(f"[MONITOR ERROR] {ticker}: {e}")

    def run_market_loop(self):
        self.update_universe()
        print(f"🚀 [RealTradeAlphaBot] Engine Active (Account: {CANO}-01 | Max Positions: {self.max_positions} | Ratio: {int(self.allocation_ratio*100)}% | Ignore Regime: {self.ignore_regime})")

        while True:
            now_kst = datetime.datetime.now(KST)
            is_market_open = (now_kst.hour == 9 and now_kst.minute >= 0) or (10 <= now_kst.hour < 15) or (now_kst.hour == 15 and now_kst.minute <= 20)

            if is_market_open:
                if not self.ignore_regime and not self.check_kospi_regime():
                    print(f"[{now_kst.strftime('%H:%M:%S')}] ⚠️ KOSPI Market Regime Warning - Buy Blocked")
                    time.sleep(10)
                    continue

                start_time = time.time()
                items = list(self.universe.items())
                with ThreadPoolExecutor(max_workers=15) as executor:
                    futures = [executor.submit(self.scan_stock_alpha, name, ticker) for name, ticker in items]
                    for future in as_completed(futures):
                        pass

                elapsed = time.time() - start_time
                print(f"✨ [{now_kst.strftime('%H:%M:%S')}] 알파 스캔 완료! (소요시간: {elapsed:.2f}초)")

            else:
                print(f"[{now_kst.strftime('%H:%M:%S')}] Market Closed. Sleeping...")

            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ignore-regime", action="store_true", help="Bypass Kospi market regime filter")
    args = parser.parse_args()

    bot = RealTradeAlphaBot(top_n=200, ignore_regime=args.ignore_regime, max_positions=5, allocation_ratio=0.20)
    bot.run_market_loop()