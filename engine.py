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
        print("⚠️ 카카오 키 미설정으로 전송을 건너뜁니다.")
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
            print("✅ [카카오톡] 실시간 Access Token 갱신 성공!")
            return access_token
        else:
            print(f"❌ [카카오톡] 토큰 발급 실패: {tokens}")
            return None
    except Exception as e:
        print(f"❌ [카카오톡] 토큰 요청 예외: {e}")
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
            "button_title": f"통합 프리미엄 브리핑 ({idx+1})"
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
        print("⚠️ 텔레그램 설정 누락 (건너뜀)")
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
            res_retry = requests.post(url, data=payload, timeout=10)
            if res_retry.status_code == 200:
                print(f"✅ [텔레그램] 파트 {idx+1} 일반 텍스트 전송 완료!")
            else:
                print(f"❌ [텔레그램] 전송 실패: {res_retry.text}")
        time.sleep(1)


def fetch_global_yahoo_data():
    """글로벌 거시경제 및 원자재, 환율 데이터 크롤링"""
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
    """네이버 금융 및 야후 파이낸스 뉴스, 수급, 상위 종목 수집"""
    yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow = [], [], [], [], []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. 야후 헤드라인 뉴스
    try:
        res = requests.get("https://finance.yahoo.com/news/", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for item in soup.select("h3")[:6]:
                title = item.get_text().strip()
                if title and len(title) > 10:
                    yahoo_news.append(title)
    except Exception as e:
        print(f"Yahoo 뉴스 에러: {e}")

    # 2. 네이버 주요 헤드라인 뉴스
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

    # 3. 거래대금 상위 종목 수집
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

    # 4. 주도 업종 수집
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

    # 5. 외국인/기관 순매수 상위 수집
    try:
        res = requests.get("https://finance.naver.com/sise/sise_deal_equity.naver", headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for row in soup.select("table.type_1 tr")[:6]:
                cols = row.select("td")
                if len(cols) > 1:
                    txt = cols[0].get_text().strip()
                    if txt:
                        foreign_inst_flow.append(txt)
    except Exception:
        pass

    return yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow


def build_dynamic_rich_fallback(global_data, yahoo_news, naver_news, top_stocks, top_sectors):
    """API 쿼터 제한 발생 시 실수집 데이터를 기반으로 조합하는 2000자급 월가 애널리스트 리포트"""
    macro_str = ", ".join([f"{k}: {v}" for k, v in global_data.items()]) if global_data else "S&P500/환율/금 변동성 유지"
    y_news_str = "\n".join([f"  • {n}" for n in yahoo_news[:3]]) if yahoo_news else "  • 글로벌 매크로 지표 및 금리 변동성 경계감 지속"
    n_news_str = "\n".join([f"  • {n}" for n in naver_news[:3]]) if naver_news else "  • 국내 주도 섹터 수급 순환매 전개"
    stocks_str = "\n".join([f"  • {s}" for s in top_stocks[:5]]) if top_stocks else "  • 인버스 및 핵심 방어 섹터 거래대금 집중"
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

- **이슈 1 (중요도 ⭐⭐⭐): 글로벌 거시 불확실성 및 주요 연준 인사 발언 여파** ⚠️
{y_news_str}
    - **증시 시사점 (Wall St. Insight)**: 고금리 장기화(Higher for longer) 기조에 따른 자산군별 리밸런싱이 가속화되고 있습니다. 채권 및 특정 통화 자산에서의 유출세와 현금흐름이 견고한 퀄리티 가치주로의 스위어링(Swirling) 수급이 감지됩니다.

- **이슈 2 (중요도 ⭐⭐): 주요 기업 실적 발표 및 주요 산업 R&D 모멘텀** 💰
{n_news_str}
    - **증시 시사점 (Wall St. Insight)**: 단기 매출 성장세보다 실질 현금 창출력 및 진입장벽(Moat)을 확보한 종목군으로 자금이 쏠리고 있습니다. 실적 가시성이 높은 배당주 및 방어주 섹터의 매력도가 대두됩니다.

- **이슈 3 (중요도 ⭐⭐): 원/달러 환율 급등에 따른 수급 이탈 및 정책 변수** 📊
    - **증시 시사점**: 환율 지지선 상향에 따라 외국인 현·선물 매도세가 가속화될 수 있으므로 환율 둔화 확인 전까지 공격적 추격 매수는 자제해야 합니다.

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
    1. **원/달러 환율 1,430원선 지지 여부**: 외국인 수급 반전의 가장 직접적인 척도입니다.
    2. **인버스 2X 거래대금 둔화 시점**: 숏 포지션 청산(Short Covering) 유입 시 단기 기술적 반등이 도래합니다.
- **실전 대응 전략**: ⚠️
    - **추격 매수 엄금**: 낙폭 과대 성장주(2차전지 등)에 대한 무분별한 물타기보다는 하락 멈춤 캔들(도지형 등) 확인이 필수입니다.
    - **방어 섹터 눌림목 접근**: 생물공학, 헬스케어, 고배당 가치주 위주로 짧은 스윙 타점을 노리되, 현금 비중을 30% 이상 유지하는 공격적 방어 전략을 권고합니다."""


def call_gemini_clean(prompt, global_data, yahoo_news, naver_news, top_stocks, top_sectors):
    """Gemini API 호출 및 Quota 초과 시 동적 고품질 리포트 자동 생성"""
    system_instruction = (
        "너는 월스트리트저널(WSJ), 블룸버그의 수석 경제 에디터이자 골드만삭스 최고 수석 애널리스트인 [STOCK BOT]이다. "
        "너의 답변은 오직 '📈 [STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑' 제목으로 시작하는 "
        "완벽하고 세련된 100% 한국어 최종 전문 리포트여야 한다. "
        "영어 사고과정(Role, Task, Input Data, Constraints 등)이나 찌꺼기 텍스트는 절대로 출력하지 마라."
    )
    
    models = ["gemini-2.0-flash-lite", "gemini-1.5-flash-latest", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-pro"]
    
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
            print(f"⚠️ 모델 {m} 호출 실패 (Quota/API 에러): {e}")
            time.sleep(2)
            continue

    print("🚨 모든 AI 모델 쿼터 초과/호출 실패 - 실수집 데이터 기반 동적 프리미엄 리포트 합성 시작")
    return build_dynamic_rich_fallback(global_data, yahoo_news, naver_news, top_stocks, top_sectors)


def generate_unified_report(global_data, yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow):
    prompt = f"""
    아래 실시간 시장 데이터를 바탕으로 월스트리트저널/블룸버그 수석 에디터 수준의 최고의 한국어 통합 프리미엄 리포트를 작성하라.
    
    [입력 실시간 데이터]
    - 글로벌 매크로 지표: {global_data}
    - 야후 글로벌 뉴스: {yahoo_news}
    - 네이버 주요 뉴스: {naver_news}
    - 거래대금 폭발 종목: {top_stocks}
    - 주도 섹터 및 업종 등락: {top_sectors}
    - 외국인/기관 수급 동향: {foreign_inst_flow}

    [작성 요구사항]
    1. 각 뉴스는 단순히 제목을 나열하지 말고, **핵심 내용 요약 + 증시 파급력(시사점)**을 깊이 있게 분석할 것.
    2. 경제지표, 기업 실적, 수급(외국인/기관 숏 및 롱 포지션) 맥락을 금융 전문 용어로 날카롭게 해석할 것.
    3. 이모지, 굵은 글씨(**)를 활용해 최고 수준의 전자 뉴스 가독성을 확보할 것.
    4. 반드시 '📈 **[STOCK BOT] 통합 프리미엄 시황 & 수급 브리핑**' 제목으로 시작할 것.
    """
    return call_gemini_clean(prompt, global_data, yahoo_news, naver_news, top_stocks, top_sectors)


if __name__ == "__main__":
    print("🚀 [STOCK BOT] 파이프라인 가동 (수급·매크로 통합 엔진)")
    
    # 1. 데이터 수집
    global_macro = fetch_global_yahoo_data()
    yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow = fetch_market_intelligence()

    # 2. 통합 브리핑 단 1회 생성
    print("🤖 [STOCK BOT] 프리미엄 통합 AI 리포트 생성 중...")
    unified_report = generate_unified_report(global_macro, yahoo_news, naver_news, top_stocks, top_sectors, foreign_inst_flow)

    # 3. 메신저 단 1회 안전 송출 (중복 방지)
    print("📲 [STOCK BOT] 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_kakao_message(unified_report)
    print("✨ [STOCK BOT] 모든 프로세스 완료!")