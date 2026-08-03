import warnings
warnings.filterwarnings("ignore")

import json
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import os
import time

# =========================================================
# 🔑 환경 변수 및 백업 토큰 자가 진단 로드 Engine
# =========================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 텔레그램 토큰 및 [STOCK BOT] 데일리 시황 채널 ID (-1004358276766)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

# 카카오톡 토큰 로드
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN")

if os.path.exists("kakao_token.json"):
    try:
        with open("kakao_token.json", "r", encoding="utf-8") as f:
            k_data = json.load(f)
            KAKAO_REST_API_KEY = KAKAO_REST_API_KEY or k_data.get("rest_api_key") or k_data.get("app_key") or k_data.get("client_id")
            KAKAO_REFRESH_TOKEN = KAKAO_REFRESH_TOKEN or k_data.get("refresh_token")
    except Exception as e:
        print(f"⚠️ kakao_token.json 로드 실패: {e}")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)


def get_kakao_access_token():
    if not KAKAO_REST_API_KEY or not KAKAO_REFRESH_TOKEN:
        return None

    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": KAKAO_REFRESH_TOKEN
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        tokens = response.json()
        return tokens.get("access_token")
    except Exception as e:
        print(f"❌ 카카오 토큰 예외 발생: {e}")
        return None


def send_kakao_message(text_content, part_title):
    access_token = get_kakao_access_token()
    if not access_token:
        print(f"⚠️ 카카오 토큰 미설정 또는 갱신 실패 (건너뜀) - {part_title}")
        return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    chunks = [text_content[i:i+900] for i in range(0, len(text_content), 900)]
    
    for idx, chunk in enumerate(chunks):
        template_object = {
            "object_type": "text",
            "text": chunk,
            "link": {
                "web_url": "https://finance.naver.com",
                "mobile_web_url": "https://finance.naver.com"
            },
            "button_title": f"{part_title} ({idx+1})"
        }
        data = {"template_object": json.dumps(template_object)}
        res = requests.post(url, headers=headers, data=data, timeout=10)
        if res.status_code == 200:
            print(f"✅ [카카오톡] {part_title} 파트 {idx+1} 전송 성공!")
        else:
            print(f"❌ [카카오톡] 전송 실패 (코드 {res.status_code}): {res.text}")
        time.sleep(1)


def send_telegram_message(text_content, part_title):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"⚠️ 텔레그램 토큰/채널ID 미설정 (건너뜀) - {part_title}")
        return

    chat_id = str(TELEGRAM_CHAT_ID).strip()
    if not chat_id.startswith("-") and not chat_id.startswith("@"):
        chat_id = f"-100{chat_id}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [text_content[i:i+3500] for i in range(0, len(text_content), 3500)]
    
    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown"
        }
        res = requests.post(url, data=payload, timeout=10)
        
        if res.status_code == 200:
            print(f"✅ [텔레그램] {part_title} 파트 {idx+1} 채널 전송 성공!")
        else:
            payload.pop("parse_mode", None)
            res_retry = requests.post(url, data=payload, timeout=10)
            if res_retry.status_code == 200:
                print(f"✅ [텔레그램] {part_title} 파트 {idx+1} 일반 텍스트 전송 성공!")
            else:
                print(f"❌ [텔레그램] 전송 실패: {res_retry.text}")
        time.sleep(1)


def fetch_global_yahoo_data():
    tickers = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "미 10년물 금리": "^TNX",
        "원/달러 환율": "KRW=X",
        "WTI 유가": "CL=F",
        "금 선물": "GC=F"
    }
    data = {}
    try:
        for name, ticker in tickers.items():
            t = yf.Ticker(ticker)
            todays_data = t.history(period="1d")
            if not todays_data.empty:
                data[name] = round(float(todays_data["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"Yahoo 에러: {e}")
    return data


def fetch_market_intelligence():
    yahoo_news, naver_featured_news, top_stocks, top_sectors = [], [], [], []
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.get("https://finance.yahoo.com/news/", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for item in soup.select("h3")[:6]:
                title = item.get_text().strip()
                if title:
                    yahoo_news.append(title)
    except Exception as e:
        print(f"Yahoo 뉴스 에러: {e}")

    try:
        res = requests.get("https://finance.naver.com/news/mainnews.naver", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for article in soup.select(".articleList .articleSubject a"):
                title = article.get_text().strip()
                naver_featured_news.append(title)
                if len(naver_featured_news) >= 6:
                    break
    except Exception as e:
        print(f"Naver 뉴스 에러: {e}")

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
                    if name:
                        top_stocks.append(f"{name} ({price}원 / 등락: {change})")
                    if len(top_stocks) >= 6:
                        break
    except Exception as e:
        print(f"거래대금 수집 에러: {e}")

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
        print(f"업종 수집 에러: {e}")

    return yahoo_news, naver_featured_news, top_stocks, top_sectors


def call_gemini(prompt):
    models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    for m in models:
        try:
            model = genai.GenerativeModel(m)
            res = model.generate_content(prompt)
            if res and res.text:
                return res.text.strip()
        except Exception as e:
            print(f"모델 {m} 에러: {e}")
            continue
    raise Exception("모든 Gemini 모델 호출 실패")


def generate_macro_report(global_data, yahoo_news, naver_news):
    prompt = f"""
    너는 월스트리트저널(WSJ) 및 블룸버그(Bloomberg)의 수석 경제 에디터인 최고급 금융 분석가 [STOCK BOT]이다. 
    투자자들이 거시경제 흐름, 야후·네이버 핵심 뉴스의 맥락, 그리고 중요 경제지표 일정을 한눈에 파악하고 실전에 즉시 활용할 수 있도록 '데일리 매크로 & 시황 리포트'를 완벽한 한국어로 작성해라.
    
    [입력 데이터]
    - 글로벌 매크로 지표: {global_data}
    - 야후 글로벌 뉴스 헤드라인: {yahoo_news}
    - 네이버 주요 뉴스 헤드라인: {naver_news}

    [작성 원칙]
    - 이모티콘과 굵은 글씨(**)를 사용하여 가독성을 극대화할 것.
    - 단순 제목 나열이 아니라, 각 이슈별로 '핵심 내용 요약'과 '시장에 미치는 실전 시사점'을 구체적으로 서술할 것.
    - 별 3개(⭐⭐⭐) 수준의 시장 파급력이 큰 주요 경제지표 및 글로벌 이슈를 심층 진단할 것.
    - 무조건 완성된 한국어 리포트 본문만 출력할 것.

    [출력 양식]
    📈 **[STOCK BOT] 데일리 매크로 & 시황 리포트**

    ---

    ### 1. 🌐 거시경제 환경 진단
    - **지표 한 줄 평**: 환율·금리·원자재 현황 및 시장 함의를 날카롭게 진단
    - **매크로 기조**: 현재 시장이 직면한 거시적 리스크와 기회 요인 분석

    ---

    ### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3 (야후·네이버 종합)
    - **이슈 1 (중요도 ⭐⭐⭐)**: 제목 및 구체적인 뉴스 내용 요약, 그리고 증시 파급력 분석
    - **이슈 2**: 제목 및 구체적인 뉴스 내용 요약, 그리고 증시 파급력 분석
    - **이슈 3**: 제목 및 구체적인 뉴스 내용 요약, 그리고 증시 파급력 분석
    """
    return call_gemini(prompt)


def generate_micro_report(top_stocks, top_sectors):
    prompt = f"""
    너는 실전 주식 투자 분석 및 수급 트레이딩 전문 봇 [STOCK BOT]이다. 
    오늘 시장에서 돈이 몰린 섹터와 거래대금 폭발 종목을 바탕으로 '마감 수급 & 섹터 트레이딩 리포트'를 완벽한 한국어로 작성해라.
    
    [입력 데이터]
    - 거래대금 폭발 종목 (수급 집중): {top_stocks}
    - 주요 업종별 등락 현황 (주도 섹터 판세): {top_sectors}

    [작성 원칙]
    - 이모티콘과 굵은 글씨(**)를 사용하여 가독성을 극대화할 것.
    - 어떤 섹터가 돈을 빨아들였는지 명확히 짚어줄 것.
    - 거래대금 상위 특징주와 대장주를 분석하고 내일 장 플레이북을 제시할 것.
    - 무조건 완성된 한국어 리포트 본문만 출력할 것.

    [출력 양식]
    ⚡ **[STOCK BOT] 마감 수급 & 섹터 트레이딩 리포트**

    ---

    ### 1. 🏢 주도 섹터 및 자금 쏠림 판세
    - **강세/약세 업종**: 돈이 집중된 주도 섹터 분석 및 수급 판세 특징
    - **수급 특징**: 시장 자금의 이동 경로 진단

    ---

    ### 2. 🎯 거래대금 폭발 종목 & 대장주 분석
    - **핵심 특징주**: 수급이 터진 주요 종목의 상승/하락 배경
    - **섹터별 대장주**: 주목해야 할 주도주 현황

    ---

    ### 3. 🚀 [STOCK BOT] Tomorrow 플레이북
    - **관전 포인트**: 내일 장 체크할 핵심 수급 변수
    - **대응 전략**: 추격 매수 자제 및 눌림목 타점 가이드
    """
    return call_gemini(prompt)


if __name__ == "__main__":
    print("🚀 [STOCK BOT] 24시간 클라우드 자동화 파이프라인 시작")
    
    # 1. 데이터 수집
    global_macro = fetch_global_yahoo_data()
    yahoo_news, naver_news, top_stocks, top_sectors = fetch_market_intelligence()

    # 2. AI 리포트 생성
    macro_report = generate_macro_report(global_macro, yahoo_news, naver_news)
    micro_report = generate_micro_report(top_stocks, top_sectors)

    # 3. 플랫폼별 송출
    print("📲 [STOCK BOT] 리포트 송출 시작...")
    send_kakao_message(macro_report, "매크로 시황 리포트")
    send_kakao_message(micro_report, "수급 트레이딩 리포트")
    send_telegram_message(macro_report, "매크로 시황 리포트")
    send_telegram_message(micro_report, "수급 트레이딩 리포트")
    print("✨ 모든 송출 완료!")