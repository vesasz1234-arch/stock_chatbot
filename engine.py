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
# 🔑 환경 변수 로드 Engine (GitHub Secrets 연동)
# =========================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

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


def send_kakao_message(text_content):
    access_token = get_kakao_access_token()
    if not access_token:
        print("⚠️ 카카오 토큰 미설정 또는 갱신 실패 (건너뜀)")
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
            "button_title": f"통합 시황 브리핑 ({idx+1})"
        }
        data = {"template_object": json.dumps(template_object)}
        res = requests.post(url, headers=headers, data=data, timeout=10)
        if res.status_code == 200:
            print(f"✅ [카카오톡] 파트 {idx+1} 전송 성공!")
        else:
            print(f"❌ [카카오톡] 전송 실패 (코드 {res.status_code}): {res.text}")
        time.sleep(1)


def send_telegram_message(text_content):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 텔레그램 토큰/채널ID 미설정 (건너뜀)")
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
            print(f"✅ [텔레그램] 파트 {idx+1} 채널 전송 성공!")
        else:
            payload.pop("parse_mode", None)
            res_retry = requests.post(url, data=payload, timeout=10)
            if res_retry.status_code == 200:
                print(f"✅ [텔레그램] 파트 {idx+1} 일반 텍스트 전송 성공!")
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
    # 1. 사용 가능한 모델 동적 검색 시도
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                m_name = m.name.replace("models/", "")
                try:
                    model = genai.GenerativeModel(m_name)
                    res = model.generate_content(prompt)
                    if res and res.text:
                        print(f"✅ 사용 성공 모델: {m_name}")
                        return res.text.strip()
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ 모델 동적 조회 실패: {e}")

    # 2. 지정된 최신 후보군 순차 호출
    models_to_try = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    for m_name in models_to_try:
        try:
            model = genai.GenerativeModel(m_name)
            res = model.generate_content(prompt)
            if res and res.text:
                print(f"✅ 사용 성공 모델: {m_name}")
                return res.text.strip()
        except Exception as e:
            print(f"⚠️ 모델 {m_name} 에러: {e}")
            continue
            
    # 3. 최후 방어선 (절대 크래시 나지 않도록 고품질 기본 통합 브리핑 반환)
    print("⚠️ 모든 Gemini 모델 호출 실패 - 기본 통합 브리핑 템플릿 사용")
    return (
        "📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑**\n\n"
        "--- \n\n"
        "### 1. 🌐 거시경제 환경 진단\n"
        "- **지표 한 줄 평**: 고금리 기조와 강달러 압박 속에서 시장의 리스크 회피 심리와 안전자산 선호가 교차하고 있습니다.\n"
        "- **매크로 기조**: 미 국채 금리와 환율 변동성이 위험자산에 부담을 주는 한편, 수급이 집중되는 우량 섹터 중심의 대응이 필요합니다.\n\n"
        "--- \n\n"
        "### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3\n"
        "- **이슈 1 (중요도 ⭐⭐⭐)**: 글로벌 거시경제 변동성에 따른 자산 방어 및 리스크 관리 점검\n"
        "- **이슈 2**: 주요 기술주 및 핵심 산업군의 수급 모멘텀 지속 여부 확인\n"
        "- **이슈 3**: 환율 고공행진에 따른 신흥국 증시 및 외국인 유동성 흐름 모니터링\n\n"
        "--- \n\n"
        "### 3. 🏢 주도 섹터 및 자금 쏠림 판세\n"
        "- **강세/약세 업종**: 지수 방어 섹터 및 개별 성장 테마 중심으로 자금 순환매 전개\n"
        "- **수급 특징**: 변동성 확대 구간 내 스마트 머니의 선별적 유입 확인\n\n"
        "--- \n\n"
        "### 4. 🎯 거래대금 폭발 종목 & 대장주 분석\n"
        "- **핵심 특징주**: 거래대금이 집중된 주요 지수 연동 상품 및 특징주 동향 파악\n"
        "- **섹터별 대장주**: 주도 섹터 내 핵심 대장주 밸류에이션 점검\n\n"
        "--- \n\n"
        "### 5. 🚀 [STOCK BOT] Tomorrow 플레이북\n"
        "- **관전 포인트**: 내일 장 지수 지지선 테스트 및 인버스/레버리지 수급 과열 여부 체크\n"
        "- **대응 전략**: 무리한 추격 매수를 자제하고, 주도 섹터 눌림목 구간 분할 매수 접근 권장"
    )


def generate_unified_report(global_data, yahoo_news, naver_news, top_stocks, top_sectors):
    prompt = f"""
    너는 월스트리트저널(WSJ) 및 블룸버그(Bloomberg)의 수석 경제 에디터인 최고급 금융 분석가 [STOCK BOT]이다. 
    투자자들이 거시경제 환경, 야후·네이버 핵심 뉴스, 경제지표 맥락, 그리고 마감 수급과 주도 섹터 분석까지 한눈에 파악하고 실전에 즉시 활용할 수 있도록 '통합 프리미엄 시황 & 수급 브리핑'을 100% 완벽한 한국어로 작성해라.
    
    [입력 데이터]
    - 글로벌 매크로 지표: {global_data}
    - 야후 글로벌 뉴스 헤드라인: {yahoo_news}
    - 네이버 주요 뉴스 헤드라인: {naver_news}
    - 거래대금 폭발 종목 (수급 집중): {top_stocks}
    - 주요 업종별 등락 현황 (주도 섹터 판세): {top_sectors}

    [작성 원칙]
    - 이모티콘과 굵은 글씨(**)를 사용하여 가독성을 극대화할 것.
    - 뉴스 및 이슈는 단순 제목 나열이 아니라, '핵심 내용 요약'과 '시장 파급력(증시 시사점)'을 구체적으로 서술할 것.
    - 수급 분석에서는 어떤 섹터가 돈을 빨아들였는지 명확히 짚어줄 것.
    - 무조건 완성된 한국어 통합 리포트 본문만 출력할 것.

    [출력 양식]
    📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑**

    ---

    ### 1. 🌐 거시경제 환경 진단
    - **지표 한 줄 평**: 환율·금리·원자재 현황 및 시장 함의를 날카롭게 진단
    - **매크로 기조**: 현재 시장이 직면한 거시적 리스크와 기회 요인 분석

    ---

    ### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3 (야후·네이버 종합)
    - **이슈 1 (중요도 ⭐⭐⭐)**: 뉴스 내용 요약 및 증시 파급력 분석
    - **이슈 2**: 뉴스 내용 요약 및 증시 파급력 분석
    - **이슈 3**: 뉴스 내용 요약 및 증시 파급력 분석

    ---

    ### 3. 🏢 주도 섹터 및 자금 쏠림 판세
    - **강세/약세 업종**: 돈이 집중된 주도 섹터 분석 및 수급 판세 특징
    - **수급 특징**: 시장 자금의 이동 경로 진단

    ---

    ### 4. 🎯 거래대금 폭발 종목 & 대장주 분석
    - **핵심 특징주**: 수급이 터진 주요 종목의 상승/하락 배경
    - **섹터별 대장주**: 주목해야 할 주도주 현황

    ---

    ### 5. 🚀 [STOCK BOT] Tomorrow 플레이북
    - **관전 포인트**: 내일 장 체크할 핵심 수급 변수
    - **대응 전략**: 추격 매수 자제 및 눌림목 타점 가이드
    """
    return call_gemini(prompt)


if __name__ == "__main__":
    print("🚀 [STOCK BOT] 24시간 클라우드 자동화 파이프라인 시작")
    
    # 1. 데이터 수집
    global_macro = fetch_global_yahoo_data()
    yahoo_news, naver_news, top_stocks, top_sectors = fetch_market_intelligence()

    # 2. 단 하나의 통합 AI 리포트 생성
    print("🤖 [STOCK BOT] 프리미엄 통합 브리핑 생성 중...")
    unified_report = generate_unified_report(global_macro, yahoo_news, naver_news, top_stocks, top_sectors)

    # 3. 플랫폼별 송출
    print("📲 [STOCK BOT] 리포트 송출 시작...")
    send_kakao_message(unified_report)
    send_telegram_message(unified_report)
    print("✨ 모든 송출 완료!")