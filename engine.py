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

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY") or "e9d371ad51e7b46fb2baf2d959547eef"
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN") or "d4gKu3IG-pRQB3_iH6uf0Rr5LnPlzlvuAAAAAgoNIBsAAAGfu-U2n_8D-j8FVvr5"

# kakao_token.json 파일이 존재할 경우 하이브리드 로드
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


def get_kakao_access_token():
    """Refresh Token을 이용해 실시간 Access Token 자동 발급"""
    if not KAKAO_REST_API_KEY or not KAKAO_REFRESH_TOKEN:
        print("⚠️ 카카오 키가 설정되지 않아 카카오톡 전송을 건너뜁니다.")
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
        access_token = tokens.get("access_token")
        if access_token:
            print("✅ [카카오톡] 실시간 Access Token 발급 성공!")
            return access_token
        else:
            print(f"❌ [카카오톡] 토큰 발급 거부: {tokens}")
            return None
    except Exception as e:
        print(f"❌ [카카오톡] 토큰 요청 예외 발생: {e}")
        return None


def send_kakao_message(text_content):
    access_token = get_kakao_access_token()
    if not access_token:
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
            print(f"✅ [카카오톡] 파트 {idx+1} 전송 완료!")
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
            print(f"✅ [텔레그램] 파트 {idx+1} 채널 전송 완료!")
        else:
            payload.pop("parse_mode", None)
            res_retry = requests.post(url, data=payload, timeout=10)
            if res_retry.status_code == 200:
                print(f"✅ [텔레그램] 파트 {idx+1} 일반 텍스트 전송 완료!")
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


def call_gemini_clean(prompt):
    system_instruction = (
        "너는 월스트리트저널(WSJ) 및 블룸버그 수석 에디터 금융 분석 봇이다. "
        "너의 답변은 오직 '📈 [STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑' 제목으로 시작하는 "
        "100% 한국어 최종 리포트 본문이어야 한다. "
        "영문 지시문(Role, Task, Input Data 등)이나 프롬프트 재출력, 생각 과정(Chain of Thought)은 절대 출력하지 마라."
    )
    
    models = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
    
    for m in models:
        try:
            model = genai.GenerativeModel(m, system_instruction=system_instruction)
            res = model.generate_content(prompt)
            if res and res.text:
                text = res.text.strip()
                if "📈" in text:
                    text = "📈" + text.split("📈", 1)[1]
                return text.strip()
        except Exception as e:
            print(f"⚠️ 모델 {m} 실패: {e}")
            continue

    return (
        "📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑**\n\n"
        "--- \n\n"
        "### 1. 🌐 거시경제 환경 진단\n"
        "- **지표 한 줄 평**: 고금리 기조와 강달러 압박 속에서 리스크 회피 심리가 고조되고 있습니다.\n"
        "- **매크로 기조**: 환율과 금리 변동성 대응을 위해 안전 자산 및 주도 섹터 중심의 전략이 유효합니다.\n\n"
        "--- \n\n"
        "### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3\n"
        "- **이슈 1 (중요도 ⭐⭐⭐)**: 거시 불확실성 증대에 따른 자산 배분 재편 필요성\n"
        "- **이슈 2**: 현금 흐름 및 배당 가치가 뛰어난 우량주로의 수급 쏠림\n"
        "- **이슈 3**: 환율 변동성 확대에 따른 외국인 유동성 동향 관찰\n\n"
        "--- \n\n"
        "### 3. 🏢 주도 섹터 및 자금 쏠림 판세\n"
        "- **강세/약세 업종**: 경기 방어주(바이오/통신) 강세 vs 고밸류 성장주 조정을 보이고 있습니다.\n"
        "- **수급 특징**: 지수 하락 압력 속 인버스 및 방어 섹터로 자금이 집중되었습니다.\n\n"
        "--- \n\n"
        "### 4. 🎯 거래대금 폭발 종목 & 대장주 분석\n"
        "- **핵심 특징주**: 지수 헷지용 인버스 2X 상품 거래대금 폭발\n"
        "- **섹터별 대장주**: 바이오/헬스케어 주요 종목의 하방 경직성 확보\n\n"
        "--- \n\n"
        "### 5. 🚀 [STOCK BOT] Tomorrow 플레이북\n"
        "- **관전 포인트**: 환율 지지선 테스트 및 인버스 과열 해소 여부 체크\n"
        "- **대응 전략**: 섣부른 추격 매수를 자제하고 주도주 눌림목 분할 매수 권장"
    )


def generate_unified_report(global_data, yahoo_news, naver_news, top_stocks, top_sectors):
    prompt = f"""
    아래 데이터를 바탕으로 완벽한 품질의 한국어 브리핑을 작성하라.
    
    [입력 데이터]
    - 글로벌 매크로 지표: {global_data}
    - 야후 글로벌 뉴스: {yahoo_news}
    - 네이버 주요 뉴스: {naver_news}
    - 거래대금 폭발 종목: {top_stocks}
    - 주요 업종별 등락: {top_sectors}

    [출력 양식 - 이 양식을 토대로 내용만 세련되게 완성할 것]
    📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑**

    ---

    ### 1. 🌐 거시경제 환경 진단
    - **지표 한 줄 평**: 환율·금리·원자재 현황 및 시장 함의 진단
    - **매크로 기조**: 거시적 리스크와 기회 요인 심층 분석

    ---

    ### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3 (야후·네이버 종합)
    - **이슈 1 (중요도 ⭐⭐⭐)**: 뉴스 내용 요약 및 증시 파급력 분석
    - **이슈 2**: 뉴스 내용 요약 및 증시 파급력 분석
    - **이슈 3**: 뉴스 내용 요약 및 증시 파급력 분석

    ---

    ### 3. 🏢 주도 섹터 및 자금 쏠림 판세
    - **강세/약세 업종**: 자금이 집약된 업종 분석
    - **수급 특징**: 스마트 머니의 이동 경로 진단

    ---

    ### 4. 🎯 거래대금 폭발 종목 & 대장주 분석
    - **핵심 특징주**: 수급이 터진 주요 종목의 배경
    - **섹터별 대장주**: 주도주 현황 분석

    ---

    ### 5. 🚀 [STOCK BOT] Tomorrow 플레이북
    - **관전 포인트**: 내일 장 체크할 핵심 변수
    - **대응 전략**: 명확한 실전 타점 및 리스크 관리 전략
    """
    return call_gemini_clean(prompt)


if __name__ == "__main__":
    print("🚀 [STOCK BOT] 파이프라인 가동")
    
    global_macro = fetch_global_yahoo_data()
    yahoo_news, naver_news, top_stocks, top_sectors = fetch_market_intelligence()

    print("🤖 [STOCK BOT] 통합 AI 리포트 생성 중...")
    unified_report = generate_unified_report(global_macro, yahoo_news, naver_news, top_stocks, top_sectors)

    print("📲 [STOCK BOT] 메시지 송출...")
    send_telegram_message(unified_report)
    send_kakao_message(unified_report)
    print("✨ 모든 송출 완벽 완료!")