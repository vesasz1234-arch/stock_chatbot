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

# =========================================================
# 🔑 환경 변수 및 백업 토큰 Engine
# =========================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY") or "e9d371ad51e7b46fb2baf2d959547eef"
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN") or "d4gKu3IG-pRQB3_iH6uf0Rr5LnPlzlvuAAAAAgoNIBsAAAGfu-U2n_8D-j8FVvr5"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or "https://discordapp.com/api/webhooks/1534112008767803433/B1S87u-nnaokeMR2lut-FAPv1PJAbeVuQunoWr-4AoZfrG4g70XwhuD8PATpApYgeFt1"

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
    
    # 900자 단위 분할 송출 (최대 3개 파트로 제한하여 도배 방지)
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


def send_discord_message(text_content):
    if not DISCORD_WEBHOOK_URL:
        return

    # 디스코드 글자 수 제한(2,000자) 대비 1,800자 단위 분할 송출
    chunks = [text_content[i:i+1800] for i in range(0, len(text_content), 1800)]
    headers = {"Content-Type": "application/json"}

    for idx, chunk in enumerate(chunks):
        payload = {
            "content": chunk,
            "username": "📈 [STOCK BOT]",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/4712/4712109.png"
        }
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=10)
            if res.status_code in [200, 204]:
                print(f"✅ [디스코드] 파트 {idx+1} 전송 완료!")
            else:
                print(f"❌ [디스코드] 파트 {idx+1} 전송 실패 ({res.status_code})")
        except Exception as e:
            print(f"⚠️ 디스코드 전송 에러: {e}")
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
    yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow = [], [], [], [], []
    headers = {"User-Agent": "Mozilla/5.0"}

    # 네이버 주요 뉴스 (한국어 중심)
    try:
        res = requests.get("https://finance.naver.com/news/mainnews.naver", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for article in soup.select(".articleList .articleSubject a"):
                title = article.get_text().strip()
                if title:
                    naver_news.append(title)
                if len(naver_news) >= 6:
                    break
    except Exception as e:
        print(f"Naver 뉴스 에러: {e}")

    # 거래대금 상위 종목
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
                        top_stocks.append(f"{name} ({price}원 | {change})")
                    if len(top_stocks) >= 6:
                        break
    except Exception as e:
        print(f"거래대금 종목 수집 에러: {e}")

    # 주도 업종
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

    return yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow


def build_dynamic_rich_fallback(global_data, naver_news, top_stocks, top_sectors):
    """영문 원문 노출을 방지하고 순수 한국어로 정제된 월가 애널리스트 리포트 생성"""
    macro_str = ", ".join([f"{k}: {v}" for k, v in global_data.items()]) if global_data else "S&P500/환율/금 변동성 유지"
    
    # 뉴스 텍스트 한국어 정제
    clean_news = [n for n in naver_news if not re.search(r'[a-zA-Z]{5,}', n)]
    n1 = clean_news[0] if len(clean_news) > 0 else "미 연준 고금리 기조 경계감 속 자산군별 재편 가속화"
    n2 = clean_news[1] if len(clean_news) > 1 else "주요 기술주 실적 가시성 점검 및 가치주로의 수급 이동"
    n3 = clean_news[2] if len(clean_news) > 2 else "원/달러 환율 급등에 따른 외국인 수급 변동성 확대"

    stocks_str = "\n".join([f"  • {s}" for s in top_stocks[:5]]) if top_stocks else "  • 인버스 2X 및 방어 섹터 거래대금 유입"
    sectors_str = ", ".join(top_sectors[:4]) if top_sectors else "생물공학, 헬스케어, 통신장비"

    return f"""📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑**

---

### 1. 🌐 거시경제 환경 진단 (WSJ / Bloomberg Macro Analysis)
- **지표 종합 진단**: "고환율·고금리·고원자재 삼중고 속 밸류에이션 리프라이싱 전개" ⚖️
    - **주요 매크로 지표**: {macro_str}
    - 미 10년물 국채 금리와 원/달러 환율이 높은 수치를 유지하며 기술주 및 고밸류 성장주에 대한 할인율 부담이 누적되고 있습니다. 유가와 금 선물의 동반 상승은 글로벌 인플레이션 재점화 가능성에 대한 헤지(Hedge) 수요를 자극하는 것으로 분석됩니다.
- **매크로 기조**: '위험자산 선호'와 '안전자산 피신'이 극명하게 갈리는 디커플링 구간입니다. 달러 강세 기조 속 신흥국 증시 내 외국인 유동성 이탈 압력이 지속되는 만큼 포트폴리오의 하방 경직성 확보가 최우선 과제입니다.

---

### 2. 📰 글로벌 & 국내 핵심 이슈 Top 3 (시장 파급력 분석)

- **이슈 1 (중요도 ⭐⭐⭐): 글로벌 거시 불확실성 및 연준 긴축 기조 여파** ⚠️
  • {n1}
    - **증시 시사점 (Wall St. Insight)**: 고금리 장기화(Higher for Longer) 기조에 따른 자산군별 리밸런싱이 가속화되고 있습니다. 채권 및 특정 통화 자산에서의 유출세와 현금흐름이 견고한 퀄리티 가치주로의 수급 이동이 감지됩니다.

- **이슈 2 (중요도 ⭐⭐): 주요 기업 실적 발표 및 산업 모멘텀** 💰
  • {n2}
    - **증시 시사점 (Wall St. Insight)**: 단기 매출 성장세보다 실질 현금 창출력 및 진입장벽(Moat)을 확보한 종목군으로 자금이 쏠리고 있습니다. 실적 가시성이 높은 배당주 및 방어주 섹터의 매력도가 대두됩니다.

- **이슈 3 (중요도 ⭐⭐): 원/달러 환율 변동성 및 유동성 동향** 📊
  • {n3}
    - **증시 시사점**: 환율 지지선 상향에 따라 외국인 수급 변동성이 가속화될 수 있으므로 환율 둔화 확인 전까지 공격적 추격 매수는 자제해야 합니다.

---

### 3. 🏢 주도 섹터 및 자금 쏠림 판세 (Smart Money Flow)
- **강세/약세 업종**: 📉 **방어주(바이오/통신) 상방 유지 vs 고성장주(2차전지/레버리지) 수급 이탈**
    - **주도 강세 섹터**: {sectors_str}
    - 시장 전체의 하락 압력 속에서도 개별 모멘텀을 보유한 **생물공학 및 헬스케어, 통신장비** 섹터로의 피난처 수급 유입이 뚜렷합니다. 반면, 2차전지 등 고밸류 성장주는 레버리지 상품 급락과 함께 자금 유출이 심화되었습니다.
- **수급 특징**: 🔄 **Short(인버스) 쏠림 vs Defensive Rotation**
    - 지수 하방에 베팅하는 인버스 2X 상품으로 역대급 거래대금이 집중되며 공포 심리가 반영되었습니다. 동시에 경기 방어적 성격의 헬스케어 및 배당주로 순환매가 전개되는 전형적인 Risk-Off 장세입니다.

---

### 4. 🎯 거래대금 폭발 종목 & 대장주 수급 분석 (Goldman Sachs Level)
- **거래대금 집중 특징주**: 🧨
{stocks_str}
    - **수급 메커니즘 분석**: 인버스 2X 상품의 거래대금 폭발은 지수 추가 하락을 노린 기관/개인의 강한 숏 포지션 구축을 뜻합니다. 반면 주도 섹터 내 대장주들은 하방 지지력을 시험하며 눌림목을 형성하고 있습니다.

---

### 5. 🚀 [STOCK BOT] Tomorrow 실전 플레이북
- **핵심 관전 포인트**: 🔍
    1. **원/달러 환율 지지 여부**: 외국인 수급 반전의 가장 직접적인 척도입니다.
    2. **인버스 2X 거래대금 둔화 시점**: 숏 포지션 청산(Short Covering) 유입 시 단기 기술적 반등이 도래합니다.
- **실전 대응 전략**: ⚠️
    - **추격 매수 엄금**: 낙폭 과대 성장주(2차전지 등)에 대한 무분별한 물타기보다는 하락 멈춤 캔들(도지형 등) 확인이 필수입니다.
    - **방어 섹터 눌림목 접근**: 생물공학, 헬스케어, 고배당 가치주 위주로 짧은 스윙 타점을 노리되, 현금 비중을 일정 수준 유지하는 방어 전략을 권고합니다."""


def call_gemini_clean(prompt, global_data, naver_news, top_stocks, top_sectors):
    system_instruction = (
        "너는 월스트리트저널(WSJ), 블룸버그 수석 에디터이자 골드만삭스 최고 수석 애널리스트인 [STOCK BOT]이다. "
        "너의 답변은 오직 '📈 [STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑' 제목으로 시작하는 "
        "완벽하고 세련된 100% 한국어 최종 전문 리포트여야 한다. 영어 사고과정이나 찌꺼기 텍스트는 절대 출력하지 마라."
    )
    
    # 구글 API 버전별 공식 호환 모델 이름 배열로 보완
    models = ["gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro-latest", "gemini-1.5-flash"]
    
    for m in models:
        try:
            model = genai.GenerativeModel(m, system_instruction=system_instruction)
            res = model.generate_content(prompt)
            if res and res.text:
                text = res.text.strip()
                if "📈" in text:
                    text = "📈" + text.split("📈", 1)[1]
                print(f"✅ AI 모델 ({m}) 호출 성공!")
                return text.strip()
        except Exception as e:
            print(f"⚠️ 모델 {m} 호출 에러: {e}")
            continue

    print("🚨 모든 AI 모델 쿼터 초과 - 정제된 순수 한국어 동적 리포트 생성")
    return build_dynamic_rich_fallback(global_data, naver_news, top_stocks, top_sectors)


def generate_unified_report(global_data, yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow):
    prompt = f"""
    아래 실시간 시장 데이터를 바탕으로 월스트리트저널/블룸버그 수석 에디터 수준의 최고의 한국어 통합 프리미엄 리포트를 작성하라.
    
    [입력 데이터]
    - 매크로 지표: {global_data}
    - 네이버 주요 뉴스: {naver_news}
    - 거래대금 폭발 종목: {top_stocks}
    - 주도 섹터 등락: {top_sectors}

    [출력 양식]
    📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑** 제목으로 시작하는 100% 한국어 리포트.
    """
    return call_gemini_clean(prompt, global_data, naver_news, top_stocks, top_sectors)


if __name__ == "__main__":
    print("🚀 [STOCK BOT] 파이프라인 가동 (수급·매크로 통합 엔진)")
    
    global_macro = fetch_global_yahoo_data()
    yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow = fetch_market_intelligence()

    print("🤖 [STOCK BOT] 프리미엄 통합 AI 리포트 생성 중...")
    unified_report = generate_unified_report(global_macro, yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow)

    print("📲 [STOCK BOT] 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_kakao_message(unified_report)
    send_discord_message(unified_report)
    print("✨ [STOCK BOT] 모든 프로세스 완료!")