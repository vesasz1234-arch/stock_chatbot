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
    def __init__(self, top_n=200, ignore_regime=False):
        self.top_n = top_n
        self.ignore_regime = ignore_regime
        self.access_token = None
        self.scanned_signals = set()
        self.active_positions = {}
        self.universe = {}
        self.init_kis_token()
        self.sync_holdings_from_balance()

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
                if output.get("stck_prpr"):
                    return {
                        "price": int(output.get("stck_prpr", 0)),
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
        """예수금 99% 비중으로 시장가 즉시 매수 주문"""
        cash = self.get_available_cash()
        if cash < 10000:
            print(f"[WARN] Insufficient Cash: {cash:,} KRW. Order Skipped.")
            return False, 0

        qty = int((cash * 0.99) // curr_price)
        if qty <= 0:
            print(f"[WARN] Order Qty is 0 (Cash: {cash:,} KRW, Stock Price: {curr_price:,} KRW)")
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
                print(f"🔥 [REAL BUY EXECUTED] {name}({ticker}) {qty} shares @ {curr_price:,} KRW")
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
                print(f"💰 [REAL SELL EXECUTED - {reason}] {name}({ticker}) {qty} shares")
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

    def update_universe(self):
        print(f"🔍 [Universe Engine] Top {self.top_n} market cap universe loading...")
        try:
            df_krx = fdr.StockListing('KRX').dropna(subset=['Marcap'])
            df_sorted = df_krx.sort_values(by='Marcap', ascending=False)
            self.universe = {row['Name']: str(row['Code']).zfill(6) for _, row in df_sorted.head(self.top_n).iterrows()}
            print(f"✅ Loaded {len(self.universe)} stocks.\n")
        except Exception as e:
            print(f"[UNIVERSE ERROR] {e}")

    def check_kospi_regime(self):
        try:
            now_kst = datetime.datetime.now(KST)
            start_dt = (now_kst - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
            df_k = fdr.DataReader('KS11', start_dt)
            if df_k.empty or len(df_k) < 20:
                return True
            df_k['MA20'] = df_k['Close'].rolling(20).mean()
            return float(df_k.iloc[-1]['Close']) >= float(df_k.iloc[-1]['MA20'])
        except Exception:
            return True

    def scan_and_trade_kis(self, name: str, ticker: str):
        """100% 한투 API 실시간 데이터 기반 종목 분석 및 매수"""
        try:
            time.sleep(0.05)  # 초당 호출 제한(Rate Limit) 방어 스로틀링
            real_data = self.get_kis_realtime_stock_info(ticker)
            if not real_data:
                return

            curr_price = real_data["price"]
            open_price = real_data["open"]
            high_price = real_data["high"]
            low_price = real_data["low"]
            vol = real_data["volume"]
            chg_rate = real_data["change_rate"]

            # 한투 실시간 스캔 돌파 조건
            cond_trend = (curr_price > open_price) and (chg_rate >= 1.5)  # 양봉 및 당일 +1.5% 이상 강세
            cond_volume = vol >= 50000                                    # 최소 거래량 확보
            
            total_range = max(high_price - low_price, 1)
            upper_shadow_ratio = (high_price - max(open_price, curr_price)) / total_range
            cond_shadow = upper_shadow_ratio <= 0.35                       # 윗꼬리 35% 이하 깔끔한 장대양봉

            if cond_trend and cond_volume and cond_shadow:
                now_kst = datetime.datetime.now(KST)
                today_str = now_kst.strftime("%Y-%m-%d")
                signal_key = f"{ticker}_{today_str}"

                if signal_key not in self.scanned_signals and ticker not in self.active_positions:
                    self.scanned_signals.add(signal_key)

                    success, qty = self.send_real_buy_order(name, ticker, curr_price)
                    if success and qty > 0:
                        target_price = int(curr_price * 1.018)
                        stop_price = int(curr_price * 0.992)

                        self.active_positions[ticker] = {
                            "name": name, "buy_price": curr_price, "qty": qty,
                            "target": target_price, "stop": stop_price
                        }

                        msg = (
                            f"🚨 **[REAL TRADE] 실전 계좌 매수 체결 완료 (KIS 실시간 스캔)**\n"
                            f"• 종목명: **{name}** (`{ticker}`)\n"
                            f"• 체결가: {curr_price:,}원 ({qty:,}주)\n"
                            f"• 🎯 익절가 (+1.8%): {target_price:,}원\n"
                            f"• 🛑 손절가 (-0.8%): {stop_price:,}원"
                        )
                        self.send_discord_msg(msg)
        except Exception:
            pass

    def monitor_and_auto_sell(self):
        """한투 실시간 API 기반 초단위 익절/손절 감시 매도"""
        if not self.active_positions:
            return

        for ticker, pos in list(self.active_positions.items()):
            try:
                real_data = self.get_kis_realtime_stock_info(ticker)
                if not real_data:
                    continue
                
                curr_price = real_data["price"]
                profit_pct = ((curr_price - pos["buy_price"]) / pos["buy_price"]) * 100
                print(f"👀 [KIS 실시간 감시] {pos['name']}({ticker}) 현재가: {curr_price:,}원 ({profit_pct:+.2f}%) | 익절가: {pos['target']:,}원 | 손절가: {pos['stop']:,}원")

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
        print(f"🚀 [RealTradeAlphaBot] Engine Active (Account: {CANO}-01 | Ignore Regime: {self.ignore_regime})")

        while True:
            now_kst = datetime.datetime.now(KST)
            is_market_open = (now_kst.hour == 9 and now_kst.minute >= 0) or (10 <= now_kst.hour < 15) or (now_kst.hour == 15 and now_kst.minute <= 20)

            if is_market_open:
                if not self.ignore_regime and not self.check_kospi_regime():
                    print(f"[{now_kst.strftime('%H:%M:%S')}] ⚠️ KOSPI Market Regime Warning - Buy Blocked")
                    time.sleep(10)
                    continue

                # 1. 보유 종목 실시간 익절/손절 감시
                self.monitor_and_auto_sell()

                # 2. 한투 실시간 API 100% 기반 유니버스 스캔
                for name, ticker in self.universe.items():
                    self.scan_and_trade_kis(name, ticker)

            else:
                print(f"[{now_kst.strftime('%H:%M:%S')}] Market Closed. Sleeping...")

            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ignore-regime", action="store_true", help="Bypass Kospi market regime filter")
    args = parser.parse_args()

    bot = RealTradeAlphaBot(top_n=200, ignore_regime=args.ignore_regime)
    bot.run_market_loop()
