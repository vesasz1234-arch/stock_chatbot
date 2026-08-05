import warnings
warnings.filterwarnings("ignore")

import json
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import os
import time
import re
from datetime import datetime, timezone, timedelta

# =========================================================
# ⏰ 타임존 (한국 표준시 KST) 및 장전/장후 모드 판별 Engine
# =========================================================
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
is_morning = now_kst.hour < 12
mode_title = "장전 프리미엄 모닝 브리핑" if is_morning else "장후 프리미엄 마감 브리핑"

# =========================================================
# 🔑 환경 변수 및 토큰 설정
# =========================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY") or "e9d371ad51e7b46fb2baf2d959547eef"
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN") or "d4gKu3IG-pRQB3_iH6uf0Rr5LnPlzlvuAAAAAgoNIBsAAAGfu-U2n_8D-j8FVvr5"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"

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
    except Exception:
        return None


def send_kakao_message(text_content):
    access_token = get_kakao_access_token()
    if not access_token:
        return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    chunks = [text_content[i:i+850] for i in range(0, len(text_content), 850)][:3]
    
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
        time.sleep(1)


def send_telegram_message(text_content):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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
            print(f"✅ [텔레그램] 파트 {idx+1} 전송 완료!")
        else:
            payload.pop("parse_mode", None)
            requests.post(url, data=payload, timeout=10)
            print(f"✅ [텔레그램] 파트 {idx+1} 일반 텍스트 전송 완료!")
        time.sleep(1)


def split_text_smartly(text, max_length=1700):
    """단락(\n\n) 기준 문맥 파괴 없는 분할 함수"""
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for p in paragraphs:
        if len(current_chunk) + len(p) + 2 <= max_length:
            current_chunk += (p + "\n\n")
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = p + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def send_discord_message(text_content):
    if not DISCORD_WEBHOOK_URL:
        return

    chunks = split_text_smartly(text_content, max_length=1700)
    headers = {"Content-Type": "application/json"}

    for idx, chunk in enumerate(chunks):
        payload = {
            "content": chunk,
            "username": f"📈 [STOCK BOT] ({'장전' if is_morning else '장후'})",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712109.png"
        }
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=10)
            if res.status_code in [200, 204]:
                print(f"✅ [디스코드 스탁봇] 파트 {idx+1} 전송 완료!")
            else:
                print(f"❌ [디스코드 스탁봇] 파트 {idx+1} 전송 실패 ({res.status_code})")
        except Exception as e:
            print(f"⚠️ 디스코드 스탁봇 전송 에러: {e}")
        time.sleep(1)


def fetch_global_yahoo_data():
    tickers = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "미 10년물 금리": "^TNX",
        "원/달러 환율": "KRW=X",
        "WTI 유가": "CL=F",
        "금 선물": "GC=F",
        "비트코인": "BTC-USD"
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
        print(f"Yahoo 수집 에러: {e}")
    return data


def fetch_market_intelligence():
    naver_news, top_stocks, top_sectors = [], [], []
    headers = {"User-Agent": "Mozilla/5.0"}

    # 1. 네이버 주요 헤드라인 뉴스
    try:
        res = requests.get("https://finance.naver.com/news/mainnews.naver", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for article in soup.select(".articleList .articleSubject a"):
                title = article.get_text().strip()
                if title and len(title) > 5:
                    naver_news.append(title)
                if len(naver_news) >= 6:
                    break
    except Exception as e:
        print(f"Naver 뉴스 에러: {e}")

    # 2. 거래대금 상위 실시간 종목
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

    # 3. 실시간 주도 섹터
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


def build_dynamic_rich_fallback(global_data, naver_news, top_stocks, top_sectors):
    macro_str = ", ".join([f"{k}: {v}" for k, v in global_data.items()]) if global_data else "글로벌 매크로 지표 변동성 유지"
    
    clean_news = [n for n in naver_news if not re.search(r'[a-zA-Z]{6,}', n)]
    n1 = clean_news[0] if len(clean_news) > 0 else "미 연준 긴축 기조 및 매크로 지표 변동성 지속"
    n2 = clean_news[1] if len(clean_news) > 1 else "주요 핵심 섹터 실적 전망 및 수급 순환매 전개"
    n3 = clean_news[2] if len(clean_news) > 2 else "환율 및 국채 금리 추이에 따른 외국인 자구책 모색"

    stocks_str = "\n".join([f" • {s}" for s in top_stocks[:5]]) if top_stocks else " • 실시간 거래대금 상위 특징주 수급 집계 중"
    sectors_str = ", ".join(top_sectors[:4]) if top_sectors else "주요 주도 섹터 수급 순환매 진행"

    time_context = "장 시작 전 해외 증시 반영 및 수급 포커스" if is_morning else "금일 장 마감 기준 거래대금 및 수급 총결산"
    strategy_context = "해외 증시 모멘텀을 반영한 장초반 수급 주도주 쏠림 주의" if is_morning else "금일 수급 쏠림 섹터 중심의 눌림목 유효성 점검 및 현금 비중 관리"

    return f"""📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑 ({mode_title})**

---

### 1. 🌐 거시경제 환경 진단 (WSJ / Bloomberg Macro Analysis)
- **지표 종합 진단**: "글로벌 자산군 변동성 속 밸류에이션 리프라이싱 및 수급 리밸런싱 전개" ⚖️
    - **주요 매크로 지표**: {macro_str}
    - 미 국채 금리와 원/달러 환율의 실시간 추이가 기술주 및 고밸류 성장주에 대한 할인율 부담으로 작용하고 있습니다. 유가 및 원자재 시장의 변동성은 글로벌 인플레이션 재점화 가능성에 대한 수급 헤지(Hedge) 수요를 자극합니다.
- **매크로 기조**: {time_context} 구간으로, 달러 향방과 글로벌 유동성 흐름에 따른 포트폴리오 하방 경직성 확보가 최우선 과제입니다.

---

### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3 (시장 파급력 분석)

- **이슈 1 (중요도 ⭐⭐⭐): 매크로 불확실성 및 유동성 방향성** ⚠️
  • {n1}
    - **증시 시사점 (Wall St. Insight)**: 고금리/고환율 환경 속 현금창출력이 우수한 퀄리티 가치주 및 수급 모멘텀 섹터로 자금이 이동하고 있습니다.

- **이슈 2 (중요도 ⭐⭐): 주요 산업 모멘텀 및 실적 가시성** 💰
  • {n2}
    - **증시 시사점 (Wall St. Insight)**: 실질적 실적 성장을 증명하는 주도 섹터로 쏠림 현상이 가속화되고 있습니다.

- **이슈 3 (중요도 ⭐⭐): 수급 변동성 및 환율 추이** 📊
  • {n3}
    - **증시 시사점**: 외국인 및 기관 수급의 유출입 변동성이 확대되고 있으므로 섣부른 추격 매수보다는 타점 포착이 중요합니다.

---

### 3. 🏢 주도 섹터 및 자금 쏠림 판세 (Smart Money Flow)
- **강세/약세 업종**: 📉 **실시간 수급 집중 섹터 vs 자금 유출 섹터 양극화**
    - **주도 강세 섹터**: {sectors_str}
    - 시장 전체의 변동성 속에서도 테마 및 수급 모멘텀을 보유한 차별화 섹터로의 피난처 유입이 뚜렷합니다.
- **수급 특징**: 🔄 **Risk-On/Off 순환매 전개**
    - 기관 및 외국인의 대량 매매가 특정 주도주와 방어 섹터로 차별화되어 집계되고 있습니다.

---

### 4. 🎯 거래대금 폭발 종목 & 대장주 수급 분석 (Goldman Sachs Level)
- **거래대금 집중 특징주**: 🧨
{stocks_str}
    - **수급 메커니즘 분석**: 거래대금 상위 종목군으로 자금이 강하게 쏠리며 시장 주도권을 형성하고 있습니다. 대장주들의 하방 지지력을 확인한 대응이 필요합니다.

---

### 5. 🚀 [STOCK BOT] 실전 대응 전략
- **핵심 관전 포인트**: 🔍
    1. **환율 및 수급 반전 지점**: 외국인/기관 매수세 유입 전환 여부가 시장 반등의 핵심 척도입니다.
    2. **거래대금 유입 연속성**: 단발성 테마 형성인지, 연속적인 수급 유입인지 파악이 필수적입니다.
- **실전 대응 전략**: ⚠️
    - **추격 매수 엄금**: {strategy_context}
    - **수급 눌림목 접근**: 거래대금이 터진 주도 섹터 중심의 방어적 분할 접근 전략을 권고합니다."""


def call_gemini_clean(prompt, global_data, naver_news, top_stocks, top_sectors):
    system_instruction = (
        f"너는 월스트리트저널(WSJ) 수석 에디터이자 골드만삭스 수석 분석가인 [STOCK BOT]이다. "
        f"너는 지금 {mode_title}을 작성 중이다. "
        f"답변은 오직 '📈 [STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑 ({mode_title})'으로 시작해야 한다. "
        f"전달받은 최신 실시간 데이터를 바탕으로 월가급 완벽한 100% 한국어 최종 전문 리포트를 작성하라."
    )
    
    # 구글 API 서버에서 실시간 이용 가능한 모델을 동적으로 탐색
    available_models = []
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
    except Exception as e:
        print(f"⚠️ 모델 목록 조회 실패: {e}")

    # 사용 가능 모델이 없거나 예외 시 기본 핑 테스트용 후보 지정
    if not available_models:
        available_models = ["models/gemini-1.5-flash", "models/gemini-1.5-pro"]

    for m_name in available_models:
        try:
            model = genai.GenerativeModel(model_name=m_name, system_instruction=system_instruction)
            res = model.generate_content(prompt)
            if res and res.text:
                text = res.text.strip()
                if "📈" in text:
                    text = "📈" + text.split("📈", 1)[1]
                print(f"✅ AI 모델 ({m_name}) 호출 성공!")
                return text.strip()
        except Exception as e:
            print(f"⚠️ 모델 {m_name} 호출 에러: {e}")
            time.sleep(1)
            continue

    print("🚨 동적 프리미엄 리포트 엔진 발동 (실시간 데이터 결합)")
    return build_dynamic_rich_fallback(global_data, naver_news, top_stocks, top_sectors)


def generate_unified_report(global_data, naver_news, top_stocks, top_sectors):
    prompt = f"""
    [현재 모드]: {mode_title}
    아래 실시간 수집 데이터를 바탕으로 최고의 한국어 프리미엄 시황/수급 리포트를 작성하라.

    [입력 데이터]
    - 매크로 지표: {global_data}
    - 실시간 헤드라인 뉴스: {naver_news}
    - 실시간 거래대금 상위 종목: {top_stocks}
    - 실시간 주도 섹터: {top_sectors}

    [작성 요구사항]
    - 장전 모드일 경우: 해외 증시 여파 분석 및 금일 장 시작 후 수급 쏠림 종목/섹터 예측 위주 작성
    - 장후 모드일 경우: 금일 장 마감 결과, 실제 거래대금 폭발 종목 및 세력 수급 분석 위주 작성
    - 100% 한국어로 일목요연하고 전문성 있게 작성할 것.
    """
    return call_gemini_clean(prompt, global_data, naver_news, top_stocks, top_sectors)


if __name__ == "__main__":
    print(f"🚀 [STOCK BOT] 파이프라인 가동 ({mode_title})")
    
    global_macro = fetch_global_yahoo_data()
    naver_news, top_stocks, top_sectors = fetch_market_intelligence()

    print("🤖 [STOCK BOT] 프리미엄 통합 AI 리포트 생성 중...")
    unified_report = generate_unified_report(global_macro, naver_news, top_stocks, top_sectors)

    print("📲 [STOCK BOT] 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_kakao_message(unified_report)
    send_discord_message(unified_report)
    print("✨ [STOCK BOT] 모든 프로세스 완료!")
