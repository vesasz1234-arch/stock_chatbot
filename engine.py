import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import google.generativeai as genai
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

# =========================================================
# ⏰ 타임존 및 CLI 실행 인자 기반 모드 판별 Engine
# =========================================================
def parse_arguments():
    parser = argparse.ArgumentParser(description="Stock Bot Engine")
    parser.add_argument(
        "--mode",
        choices=["morning", "evening"],
        help="명시적 실행 모드 설정 (morning: 장전, evening: 장후)"
    )
    return parser.parse_args()

args = parse_arguments()
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)

# CLI 인자가 있으면 우선 적용, 없으면 실행 시각 기준 판단
if args.mode:
    is_morning = (args.mode == "morning")
else:
    is_morning = now_kst.hour < 12

mode_title = (
    "장전 프리미엄 글로벌 모닝 브리핑"
    if is_morning
    else "장후 프리미엄 마감 시황 브리핑"
)

# =========================================================
# 🔑 환경 변수 및 토큰 설정 (보안 강화)
# =========================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

if os.path.exists("kakao_token.json"):
    try:
        with open("kakao_token.json", "r", encoding="utf-8") as f:
            k_data = json.load(f)
            KAKAO_REST_API_KEY = k_data.get("rest_api_key") or KAKAO_REST_API_KEY
            KAKAO_REFRESH_TOKEN = k_data.get("refresh_token") or KAKAO_REFRESH_TOKEN
    except Exception as e:
        print(f"⚠️ kakao_token.json 로드 실패: {e}")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


def fetch_krx_market_summary():
    """국내 증시 핵심 지수(KOSPI, KOSDAQ) 및 수급 독립 수집"""
    krx_data = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # KOSPI 수집
    try:
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSI", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            now_val = soup.select_one("#now_value")
            change_val = soup.select_one("#change_value_and_rate")
            if now_val and change_val:
                c_text = change_val.get_text().strip().replace("\n", " ").replace("\t", "")
                krx_data["KOSPI"] = f"{now_val.get_text().strip()} ({c_text})"
    except Exception as e:
        print(f"⚠️ KOSPI 수집 에러: {e}")

    # KOSDAQ 수집 (독립 블록으로 분리)
    try:
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSQ", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            now_val = soup.select_one("#now_value")
            change_val = soup.select_one("#change_value_and_rate")
            if now_val and change_val:
                c_text = change_val.get_text().strip().replace("\n", " ").replace("\t", "")
                krx_data["KOSDAQ"] = f"{now_val.get_text().strip()} ({c_text})"
    except Exception as e:
        print(f"⚠️ KOSDAQ 수집 에러: {e}")

    # 투자자 동향 수집
    try:
        res_sise = requests.get("https://finance.naver.com/sise/", headers=headers, timeout=5)
        if res_sise.status_code == 200:
            soup_sise = BeautifulSoup(res_sise.text, "html.parser")
            kospi_tab = soup_sise.select_one("#num2")
            if kospi_tab:
                supply_items = [
                    li.get_text().strip().replace("\n", "").replace("\t", "")
                    for li in kospi_tab.select("li")
                ]
                if supply_items:
                    krx_data["KOSPI_투자자동향"] = ", ".join(supply_items)
    except Exception as e:
        print(f"⚠️ KRX 수급 수집 에러: {e}")

    return krx_data


def fetch_global_yahoo_data():
    """글로벌 매크로 지표, 원자재, 환율 정밀 수집"""
    tickers = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "미 10년물 국채금리": "^TNX",
        "원/달러 환율": "KRW=X",
        "WTI 유가": "CL=F",
        "금 선물": "GC=F",
        "비트코인": "BTC-USD",
    }
    data = {}
    try:
        for name, ticker in tickers.items():
            t = yf.Ticker(ticker)
            todays_data = t.history(period="2d")
            if not todays_data.empty:
                val = round(float(todays_data["Close"].iloc[-1]), 2)
                if len(todays_data) > 1:
                    prev = float(todays_data["Close"].iloc[-2])
                    chg = round(((val - prev) / prev) * 100, 2)
                    data[name] = f"{val} ({'+' if chg > 0 else ''}{chg}%)"
                else:
                    data[name] = str(val)
    except Exception as e:
        print(f"Yahoo 데이터 수집 에러: {e}")
    return data


def fetch_market_intelligence():
    """네이버 증시 주요 뉴스, 거래대금 상위 특징주, 주도 업종 수집"""
    naver_news, top_stocks, top_sectors = [], [], []
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.get("https://finance.naver.com/news/mainnews.naver", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for article in soup.select(".articleList .articleSubject a"):
                title = article.get_text().strip()
                if title and len(title) > 5:
                    naver_news.append(title)
                if len(naver_news) >= 8:
                    break
    except Exception as e:
        print(f"Naver 뉴스 수집 에러: {e}")

    try:
        res = requests.get("https://finance.naver.com/sise/sise_quant.naver?sosok=0", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for row in soup.select("table.type_2 tr"):
                cols = row.select("td")
                if len(cols) > 5:
                    name = cols[1].get_text().strip()
                    price = cols[2].get_text().strip()
                    change = cols[4].get_text().strip().replace("\n", "").replace("\t", "")
                    if name and name not in ["종목명", ""]:
                        top_stocks.append(f"{name} ({price}원 | {change})")
                    if len(top_stocks) >= 6:
                        break
    except Exception as e:
        print(f"거래대금 종목 수집 에러: {e}")

    try:
        res = requests.get("https://finance.naver.com/sise/sise_group.naver?type=upjong", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for row in soup.select("table.type_1 tr"):
                cols = row.select("td")
                if len(cols) > 2:
                    sec_name = cols[0].get_text().strip()
                    sec_change = cols[2].get_text().strip().replace("\n", "").replace("\t", "")
                    if sec_name:
                        top_sectors.append(f"{sec_name} ({sec_change})")
                    if len(top_sectors) >= 5:
                        break
    except Exception as e:
        print(f"주도 업종 수집 에러: {e}")

    return naver_news, top_stocks, top_sectors


def call_gemini_clean(prompt, krx_data, global_data, naver_news, top_stocks, top_sectors):
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY 없음 - 백업 엔진을 가동합니다.")
        return build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors)

    system_instruction = (
        f"너는 블룸버그(Bloomberg) 수석 특파원이자 골드만삭스 수석 글로벌 마켓 전략가인 [STOCK BOT]이다.\n"
        f"너는 단순 수급이나 ETF 시세를 나열하는 자가 아니다. 시장의 거시 경제 흐름, 기업 실적, 헤드라인 뉴스의 파급력을 깊이 있게 분석하라.\n"
        f"답변은 반드시 '📈 **[STOCK BOT] 블룸버그 프리미엄 {'장전 모닝' if is_morning else '마감 시황'} 브리핑**'으로 시작해야 한다.\n"
        f"제공된 [입력 데이터]에 없는 코스피/코스닥 지수 수치를 왜곡하거나 지어내지 마라(할루시네이션 절대 금지).\n"
        f"소제목 작성 시 '3성급(⭐⭐⭐)' 표기에서 괄호 안 별표(⭐⭐⭐)를 절대로 누락하지 말고 '3성급(⭐⭐⭐)' 형태 그대로 출력하라.\n"
        f"반드시 3성급(⭐⭐⭐) 핵심 이슈, 3성급(⭐⭐⭐) 주요 기업 실적/모멘텀, 3성급(⭐⭐⭐) 경제지표 분석을 명확히 구분하여 월가 최고 수준의 한국어로 작성하라."
    )

    # Correct Gemini model strings
    target_models = [
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    for m_name in target_models:
        try:
            model = genai.GenerativeModel(model_name=m_name, system_instruction=system_instruction)
            res = model.generate_content(prompt)
            if res and res.text:
                cleaned = sanitize_report_text(res.text)
                if len(cleaned) > 300 and ("📈" in cleaned or "[STOCK BOT]" in cleaned):
                    print(f"✅ AI 모델 ({m_name}) 리포트 생성 성공!")
                    return cleaned
        except Exception as e:
            print(f"⚠️ 모델 {m_name} 호출 실패: {e}")
            time.sleep(1)
            continue

    print("🚨 AI 모델 호출 불가 - 동적 프리미엄 리포트 백업 엔진 가동")
    return build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors)


def build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors):
    kospi_info = krx_data.get("KOSPI", "KOSPI 지수 집계 중")
    kosdaq_info = krx_data.get("KOSDAQ", "KOSDAQ 지수 집계 중")
    supply_info = krx_data.get("KOSPI_투자자동향", "외인/기관 동향 분석 중")

    macro_str = (
        ", ".join([f"{k}: {v}" for k, v in global_data.items()])
        if global_data
        else "글로벌 매크로 지표 변동성 유지"
    )

    clean_news = [n for n in naver_news if not re.search(r"[a-zA-Z]{6,}", n)]
    n1 = clean_news[0] if len(clean_news) > 0 else "미 연준 통화정책 향방 및 글로벌 국채 금리 변동성 상존"
    n2 = clean_news[1] if len(clean_news) > 1 else "국내 핵심 주도 섹터 실적 전망 및 수급 집중"
    n3 = clean_news[2] if len(clean_news) > 2 else "원/달러 환율 추이 및 지정학적 리스크 점검"

    stocks_str = (
        "\n".join([f" • {s}" for s in top_stocks[:5]])
        if top_stocks
        else " • 거래대금 상위 주도주 집계 중"
    )
    sectors_str = ", ".join(top_sectors[:4]) if top_sectors else "핵심 업종 순환매 진행"

    return f"""📈 **[STOCK BOT] 블룸버그 프리미엄 브리핑 ({mode_title})**

---

### 1. 🌐 글로벌 매크로 & 국내 증시 스코어카드
- **국내 증시 현황**: 📊
    - **KOSPI**: {kospi_info}
    - **KOSDAQ**: {kosdaq_info}
    - **시장 투자자 수급**: {supply_info}
- **글로벌 핵심 경제 지표**: 🏛️
    - {macro_str}

---

### 2. 📰 ⭐3성급(⭐⭐⭐) 글로벌 & 국내 핵심 이슈 분석

- **이슈 1 (중요도 ⭐⭐⭐): 통화정책 & 매크로 환경 재편** ⚠️
  • {n1}

- **이슈 2 (중요도 ⭐⭐⭐): 글로벌 수급 이탈 및 환율 변동성** 📊
  • {n3}

---

### 3. 🏢 ⭐3성급(⭐⭐⭐) 핵심 기업 실적 & 시장 주도 섹터

- **이슈 3 (중요도 ⭐⭐⭐): 실적 가시성 보유 주도 업종 쏠림** 💰
  • 주도 강세 섹터: {sectors_str}
  • {n2}

- **거래대금 집중 핵심 종목**: 🧨
{stocks_str}

---

### 4. 🎯 ⭐3성급(⭐⭐⭐) 원자재, 환율 & 자산시장 시사점
- **환율 & 금리**: 원/달러 환율과 국채 금리 추이가 가치 평가(Valuation) 부담을 완화시키고 있습니다.
- **원자재 & 대체 자산**: 유가 및 금 선물 흐름을 적극 모니터링해야 합니다."""


def generate_unified_report(krx_data, global_data, naver_news, top_stocks, top_sectors):
    prompt = f"""
    [현재 모드]: {mode_title}
    아래 수집된 실제 데이터 기반으로 블룸버그 수석 기자의 관점에서 깊이 있는 리포트를 작성하라.

    [실제 수집 데이터]
    - 국내 증시 지수 및 수급: {krx_data}
    - 해외 매크로 지표: {global_data}
    - 헤드라인 뉴스: {naver_news}
    - 거래대금 상위 핵심 종목: {top_stocks}
    - 주도 섹터: {top_sectors}
    """
    return call_gemini_clean(prompt, krx_data, global_data, naver_news, top_stocks, top_sectors)


def split_text_smartly(text, max_length=1700):
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    for p in paragraphs:
        if len(current_chunk) + len(p) + 2 <= max_length:
            current_chunk += p + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = p + "\n\n"
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks


def sanitize_report_text(text):
    if not text:
        return ""
    if "📈" in text:
        text = "📈" + text.split("📈", 1)[1]
    elif "[STOCK BOT]" in text:
        text = "[STOCK BOT]" + text.split("[STOCK BOT]", 1)[1]

    lines = text.split("\n")
    cleaned_lines = [
        line
        for line in lines
        if not any(
            bad in line
            for bad in [
                "Language:",
                "Input Data:",
                "Macro Analysis:",
                "Predictions:",
                "Keywords to use:",
                "Title:",
                "Start with required header?",
            ]
        )
    ]
    return "\n".join(cleaned_lines).strip()


def send_discord_message(text_content):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ DISCORD_WEBHOOK_URL 미설정으로 스킵합니다.")
        return
    cleaned_content = sanitize_report_text(text_content)
    chunks = split_text_smartly(cleaned_content, max_length=1700)
    headers = {"Content-Type": "application/json"}

    for idx, chunk in enumerate(chunks):
        payload = {
            "content": chunk,
            "username": f"📈 [STOCK BOT] ({'장전' if is_morning else '장후'})",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712109.png",
        }
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=10)
            if res.status_code in [200, 204]:
                print(f"✅ [디스코드] 파트 {idx+1} 전송 완료!")
        except Exception as e:
            print(f"⚠️ 디스코드 전송 에러: {e}")
        time.sleep(1)


def send_telegram_message(text_content):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not chat_id.startswith("-") and not chat_id.startswith("@"):
        chat_id = f"-100{chat_id}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [text_content[i : i + 3500] for i in range(0, len(text_content), 3500)]

    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
        res = requests.post(url, data=payload, timeout=10)
        if res.status_code != 200:
            payload.pop("parse_mode", None)
            requests.post(url, data=payload, timeout=10)
        time.sleep(1)


if __name__ == "__main__":
    print(f"🚀 [STOCK BOT] 파이프라인 가동 (모드: {mode_title})")

    krx_data = fetch_krx_market_summary()
    global_macro = fetch_global_yahoo_data()
    naver_news, top_stocks, top_sectors = fetch_market_intelligence()

    print("🤖 [STOCK BOT] 리포트 생성 중...")
    unified_report = generate_unified_report(krx_data, global_macro, naver_news, top_stocks, top_sectors)

    print("📲 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_discord_message(unified_report)
    print("✨ 모든 프로세스 완료!")
