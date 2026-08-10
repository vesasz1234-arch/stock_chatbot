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
APP_KEY = "PSSDHdJ44C6dUaTxnD28Vht6hOHBhFRjiFkA"
APP_SECRET = "5/AQN2eoGs1hX/BuYqjsWCPr3zUMLfBcdlic8zp7Axr3JzESm1J0roAxyjOdOuY30sEDilPdu27ELVD/nqiUNJV9wvCtl4aEdZFlhoK5JOfqfVA98yMRK3J5bBQwJm/Ej0Bd1tX2Qb+ecvniSS4mmbZclDrh1vRqby9ZflhX+kKTvmNXJOg="
URL_BASE = "https://openapi.koreainvestment.com:9443"

CANO = "64165137"
ACNT_PRDT_CD = "01"
DISCORD_WEBHOOK_URL = "https://discordapp.com/api/webhooks/1534112008767803433/B1S87u-nnaokeMR2lut-FAPv1PJAbeVuQunoWr-4AoZfrG4g70XwhuD8PATpApYgeFt1"

class RealTradeAlphaBot:
    def __init__(self, top_n=200):
        self.top_n = top_n
        self.access_token = None
        self.scanned_signals = set()
        self.active_positions = {}
        self.universe = {}
        self.init_kis_token()

    def init_kis_token(self):
        url = f"{URL_BASE}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
        for attempt in range(3):
            try:
                res = requests.post(url, headers=headers, data=json.dumps(body), timeout=5)
                if res.status_code == 200:
                    self.access_token = res.json().get("access_token")
                    print("[SUCCESS] KIS Real API Token Generated.")
                    return
                else:
                    if "EGW00133" in res.text:
                        print(f"[WARN] Token limit hit. Retrying... ({attempt+1}/3)")
                        time.sleep(5)
                    else:
                        print(f"[ERROR] Token Failed: {res.text}")
                        break
            except Exception as e:
                print(f"[EXCEPTION] Token Error: {e}")
                break

    def get_available_cash(self):
        if not self.access_token: return 0
        url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.access_token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "TTTC8434R"}
        params = {"CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "AFHR_FLPR_YN": "N", "INQR_DVSN": "02", "UNPR_DVSN": "01", "PRCS_DVSN": "01"}
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                output2 = res.json().get("output2", [{}])[0]
                return int(output2.get("dnca_tot_amt", 0))
        except Exception: pass
        return 0

    def send_real_buy_order(self, name, ticker, curr_price):
        cash = self.get_available_cash()
        qty = int((cash * 0.99) // curr_price)
        if qty <= 0: return False, 0
        url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/order-cash"
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.access_token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "TTTC0802U"}
        body = {"CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "PDNO": ticker, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"}
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=5)
        return (res.status_code == 200 and res.json().get("rt_cd") == "0"), qty

    def send_real_sell_order(self, name, ticker, qty, reason):
        url = f"{URL_BASE}/uapi/domestic-stock/v1/trading/order-cash"
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.access_token}", "appkey": APP_KEY, "appsecret": APP_SECRET, "tr_id": "TTTC0801U"}
        body = {"CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "PDNO": ticker, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"}
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=5)
        return (res.status_code == 200 and res.json().get("rt_cd") == "0")

    def send_discord_msg(self, message):
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={"content": message, "username": "🤖 [REAL TRADE BOT]"}, timeout=5)
        except Exception: pass

    def update_universe(self):
        try:
            df = fdr.StockListing('KRX').sort_values(by='Marcap', ascending=False)
            self.universe = {row['Name']: str(row['Code']).zfill(6) for _, row in df.head(self.top_n).iterrows()}
            print(f"[UNIVERSE] {len(self.universe)} stocks loaded.")
        except: pass

    def run_market_loop(self):
        self.update_universe()
        print("🚀 [RealTradeAlphaBot] Engine Running...")
        while True:
            now = datetime.datetime.now(KST)
            # Market hours check
            if (now.hour == 9 and now.minute >= 0) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 20):
                # Monitoring sell positions
                if self.active_positions:
                    for ticker, pos in list(self.active_positions.items()):
                        try:
                            price = int(fdr.DataReader(ticker, now - timedelta(days=2))['Close'].iloc[-1])
                            if price >= pos["target"]:
                                if self.send_real_sell_order(pos["name"], ticker, pos["qty"], "TARGET"):
                                    self.send_discord_msg(f"🎉 Profit Taken: {pos['name']}")
                                    del self.active_positions[ticker]
                            elif price <= pos["stop"]:
                                if self.send_real_sell_order(pos["name"], ticker, pos["qty"], "STOP"):
                                    self.send_discord_msg(f"🛑 Stop Loss: {pos['name']}")
                                    del self.active_positions[ticker]
                        except: pass
                
                # Scan new signals
                with ThreadPoolExecutor(max_workers=10) as executor:
                    for name, ticker in self.universe.items():
                        executor.submit(self.scan_and_trade, name, ticker)
            time.sleep(10)

    def scan_and_trade(self, name, ticker):
        try:
            df = fdr.DataReader(ticker, datetime.datetime.now(KST) - timedelta(days=90))
            if len(df) < 35: return
            df['MA20'] = df['Close'].rolling(20).mean()
            std20 = df['Close'].rolling(20).std()
            df['BB_Upper'] = df['MA20'] + (std20 * 2.0)
            
            # Simple Alpha Logic
            today = df.iloc[-1]
            if today['Close'] > today['BB_Upper'] and ticker not in self.active_positions:
                success, qty = self.send_real_buy_order(name, ticker, int(today['Close']))
                if success:
                    self.active_positions[ticker] = {"name": name, "buy_price": int(today['Close']), "qty": qty, "target": int(today['Close']*1.018), "stop": int(today['Close']*0.992)}
                    self.send_discord_msg(f"🚨 Buy Executed: {name} ({qty} shares)")
        except: pass

if __name__ == "__main__":
    bot = RealTradeAlphaBot()
    bot.run_market_loop()