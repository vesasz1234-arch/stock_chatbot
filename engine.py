import argparse
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
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
# 🔑 환경 변수 및 토큰 설정
# =========================================================
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN")

DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_WEBHOOK_URL")
    or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"
)


def sanitize_market_text(text):
    """네이버 크롤링 수치 오류 정제 (-0.60%상승 -> -0.60%)"""
    if not text:
        return ""
    text = re.sub(r'(-\d+\.?\d*%)\s*상승', r'\1', text)
    text = re.sub(r'(\+\d+\.?\d*%)\s*하락', r'\1', text)
    return text


def clean_cjk_junk(text):
    """중국어/일본어 한자 및 가타카나 강제 삭제 필터"""
    if not text:
        return ""
    # 한자 및 가타카나/히라가나 제거
    cleaned = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
    return cleaned


def fetch_krx_market_summary():
    """국내 증시 핵심 지수(KOSPI, KOSDAQ) 및 실시간 외인/기관 수급 정밀 수집"""
    krx_data = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            now_val = soup.select_one("#now_value")
            change_val = soup.select_one("#change_value_and_rate")
            if now_val and change_val:
                c_text = change_val.get_text().strip().replace("\n", " ").replace("\t", "")
                c_text = sanitize_market_text(c_text)
                krx_data["KOSPI"] = f"{now_val.get_text().strip()} ({c_text})"
    except Exception as e:
        print(f"⚠️ KOSPI 수집 에러: {e}")

    try:
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSDAQ", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            now_val = soup.select_one("#now_value")
            change_val = soup.select_one("#change_value_and_rate")
            if now_val and change_val:
                c_text = change_val.get_text().strip().replace("\n", " ").replace("\t", "")
                c_text = sanitize_market_text(c_text)
                krx_data["KOSDAQ"] = f"{now_val.get_text().strip()} ({c_text})"
    except Exception as e:
        print(f"⚠️ KOSDAQ 수집 에러: {e}")

    try:
        res_sise = requests.get("https://finance.naver.com/sise/", headers=headers, timeout=5)
        if res_sise.status_code == 200:
            soup_sise = BeautifulSoup(res_sise.text, "html.parser")
            kospi_tab = soup_sise.select_one("#num2")
            if kospi_tab:
                items = [li.get_text().strip().replace("\n", "").replace("\t", "") for li in kospi_tab.select("li")]
                if items:
                    krx_data["KOSPI_수급"] = ", ".join(items)
    except Exception as e:
        print(f"⚠️ KRX 수급 수집 에러: {e}")

    return krx_data


def fetch_global_yahoo_data():
    """월가 헤지펀드 핵심 매크로 지표 정밀 수집 (VIX, DXY, 야간선물 포함)"""
    tickers = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "S&P500 선물": "ES=F",
        "나스닥100 선물": "NQ=F",
        "VIX 변동성지수": "^VIX",
        "달러 인덱스": "DX-Y.NYB",
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
    """뉴스, 특징주, 주도 섹터 수집"""
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


# =========================================================
# 🤖 OpenRouter API (무료 모델 검증 슬러그 라인업)
# =========================================================
def call_openrouter_ai(prompt, system_instruction):
    if not OPENROUTER_API_KEY:
        print("⚠️ OPENROUTER_API_KEY 없음 - OpenRouter 스킵")
        return None

    key_preview = f"{OPENROUTER_API_KEY[:8]}...{OPENROUTER_API_KEY[-4:]}" if len(OPENROUTER_API_KEY) > 12 else "INVALID_KEY"
    print(f"🚀 [OpenRouter AI Engine] 월가 추론 AI 가동 중... (Key: {key_preview})")

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    # OpenRouter 검증된 100% 무료 최상위 모델 슬러그
    models = [
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-r1:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "google/gemini-2.0-flash-exp:free"
    ]

    for model_name in models:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,  # 단정적이고 일관된 어조 유지
            "max_tokens": 3000
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                text = data["choices"][0]["message"]["content"]
                cleaned = sanitize_report_text(text)
                cleaned = clean_cjk_junk(cleaned)
                if len(cleaned) > 500:
                    print(f"✅ [SUCCESS] OpenRouter ({model_name}) 월가 리포트 생성 완료!")
                    return cleaned
            else:
                print(f"⚠️ OpenRouter {model_name} 오류 ({res.status_code}): {res.text[:120]}")
        except Exception as e:
            print(f"⚠️ OpenRouter {model_name} 예외 발생: {e}")
    return None


def call_groq_ai(prompt, system_instruction):
    if not GROQ_API_KEY:
        print("⚠️ GROQ_API_KEY 없음 - Groq 스킵")
        return None

    print("🚀 [Groq AI Engine] Llama-3.3-70B 월가 가드레일 가동 중...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 3000
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        if res.status_code == 200:
            data = res.json()
            text = data["choices"][0]["message"]["content"]
            cleaned = sanitize_report_text(text)
            cleaned = clean_cjk_junk(cleaned)
            if len(cleaned) > 500:
                print("✅ [SUCCESS] Groq Llama-3.3-70B 월가 리포트 생성 완료!")
                return cleaned
        else:
            print(f"⚠️ Groq API 오류 ({res.status_code}): {res.text[:120]}")
    except Exception as e:
        print(f"⚠️ Groq AI 예외: {e}")
    return None


def call_gemini_clean(prompt, krx_data, global_data, naver_news, top_stocks, top_sectors):
    system_instruction = (
        "너는 골드만삭스/블룸버그 수석 마켓 전략가이자 헤지펀드 최고투자책임자(CIO)인 [STOCK BOT]이다.\n"
        "너의 문장은 시장을 주도하는 월가 트레이더와 기관 투자가에게 직접 전달되는 최고급 마켓 인텔리전스다.\n\n"
        "[절대 금지 표현 - 위반 시 무효]\n"
        "1. 절대 쓰지 말아야 할 단어: '~관련이 있습니다', '~영향을 미칠 수 있습니다', '~시사합니다', '~고려합니다', '~생각됩니다'.\n"
        "2. 절대 쓰지 말아야 할 문자: 중국어/일본어 한자(漢字, 예: 影响, 需求, 圧力), 가타카나, 베트남어.\n"
        "3. 수치 부호 절대 준수: 마이너스 비율(-0.60%)에 '상승' 표기 절대 금지.\n\n"
        "[월가 금융 메커니즘 단정적 지침]\n"
        "1. 원/달러 환율 하락(원화 강세) = '수출 대형주 환차손 부담 가중 및 영업이익률 상방 제약, 원자재 수입 기업 원가 절감 수혜'. 단정적 어조로 기술하라.\n"
        "2. 미 10년물 국채금리 하락 = '할인율(Multiple) 상방 압력 완화에 따른 빅테크 Multiple Expansion 개시'.\n"
        "3. VIX 지수 및 DXY 변동 = 'CTA 헤지펀드 숏커버링 물량 집결 및 Volatility Control 펀드 포지션 재편'.\n"
        "4. 전술적 자산 배분 = '현금 비중 35% 준수, HBM/AI 반도체 벨류체인 눌림목 분할 매수, 선물 인버스 델타 헤징 30% 즉시 가동'. 단호한 명령조로 작성하라.\n\n"
        "[출력 규격 양식]\n"
        "📈 **[STOCK BOT] 블룸버그 프리미엄 마감 시황 브리핑**\n\n"
        "---\n\n"
        "### 1. 🌐 글로벌 매크로 & 국내 증시 스코어카드 (Macro Dashboard)\n"
        "• **국내 증시 현황**: KOSPI **[치]** | KOSDAQ **[치]**\n"
        "• **시장 수급 메커니즘**: [외인/기관 순매수 수치 기반 세력의 포지셔닝 단정 분석]\n"
        "• **글로벌 벤치마크 지표**: S&P 500 **[치]**, NASDAQ **[치]**, 야간선물(S&P500 **[치]** / NQ **[치]**), VIX **[치]**, DXY **[치]**, 미 10년물 금리 **[치]**, 원/달러 환율 **[치]**, WTI 유가 **[치]**, 금 선물 **[치]**, 비트코인 **[치]**\n\n"
        "---\n\n"
        "### 2. 📰 [3성급★★★] 글로벌 & 국내 핵심 이슈 분석 (Macro Impact Chain)\n"
        "• **이슈 1: [금리 및 성장주 Multiple Expansion]**\n"
        "  - **메커니즘 분석**: [국채금리와 빅테크 밸류에이션 간 정밀 단정 추론]\n"
        "• **이슈 2: [환율 변동성과 외인 파생 포지셔닝]**\n"
        "  - **월가 시각**: [환율 하락이 외국인 델타 포지션 및 수출 대형주 실적에 미치는 직격탄 분석]\n\n"
        "---\n\n"
        "### 3. 🏢 [3성급★★★] 핵심 기업 실적 & 주도 섹터 (Capital Flow Analysis)\n"
        "• **주도 강세 섹터**: [스마트머니 집결 섹터 정밀 매핑]\n"
        "• **거래대금 집중 종목 및 수급 특징**: [상위 종목 매수 세력의 의도 및 인버스/레버리지 메커니즘 추론]\n\n"
        "---\n\n"
        "### 4. 🎯 [3성급★★★] 원자재, 환율 & 자산시장 시사점 (Alternative Risk)\n"
        "• **금/유가 시사점**: [인플레이션 리프라이싱 및 실물 안전자산 쏠림 분석]\n\n"
        "---\n\n"
        "### 5. 🚀 [Goldman Sachs Strategist] 내일의 전술적 자산 배분 대응 전략\n"
        "• **포트폴리오 리스크 관리**: [구체적인 현금 비중(%) 및 액션 플랜 명령조 제시]"
    )

    # 1. OpenRouter 1순위 가동
    openrouter_res = call_openrouter_ai(prompt, system_instruction)
    if openrouter_res:
        return openrouter_res

    # 2. Groq 2순위 가동
    groq_res = call_groq_ai(prompt, system_instruction)
    if groq_res:
        return groq_res

    # 3. Gemini 3순위 가동
    if GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            for m_name in ["gemini-2.0-flash", "gemini-2.0-flash-lite"]:
                try:
                    response = client.models.generate_content(
                        model=m_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.1,
                        )
                    )
                    if response and response.text:
                        cleaned = sanitize_report_text(response.text)
                        cleaned = clean_cjk_junk(cleaned)
                        if len(cleaned) > 500 and ("📈" in cleaned or "[STOCK BOT]" in cleaned):
                            print(f"✅ [SUCCESS] 구글 Gemini AI ({m_name}) 리포트 생성 완료!")
                            return cleaned
                except Exception as e:
                    print(f"⚠️ Gemini {m_name} 오류: {str(e)[:100]}")
        except Exception as e:
            print(f"⚠️ Gemini Client 실패: {e}")

    # 4. 백업 템플릿
    print("🚨 모든 AI 모델 호출 불가 - 비상 템플릿 가동")
    return build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors)


def build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors):
    """최후의 백업 분석 엔진"""
    kospi_info = krx_data.get("KOSPI", "6,258.77 (-0.60%)")
    kosdaq_info = krx_data.get("KOSDAQ", "798.81 (-0.36%)")
    supply_info = krx_data.get("KOSPI_수급", "외인 순매도 전환, 기관 파생 헤지물량 출하")

    sp500 = global_data.get("S&P 500", "4,757.64 (+0.62%)")
    nasdaq = global_data.get("NASDAQ", "26,690.62 (+1.30%)")
    es = global_data.get("S&P500 선물", "4,765.25 (+0.15%)")
    nq = global_data.get("나스닥100 선물", "26,720.50 (+0.22%)")
    vix = global_data.get("VIX 변동성지수", "15.42 (-1.20%)")
    dxy = global_data.get("달러 인덱스", "104.12 (+0.08%)")
    us10y = global_data.get("미 10년물 국채금리", "4.66% (-0.21%)")
    usdkrw = global_data.get("원/달러 환율", "1,407.45원 (-0.96%)")
    wti = global_data.get("WTI 유가", "78.18달러 (+1.15%)")
    gold = global_data.get("금 선물", "4,340.7달러 (+2.33%)")
    btc = global_data.get("비트코인", "64,956.3달러 (+0.12%)")

    clean_news = [n for n in naver_news if not re.search(r"[a-zA-Z]{6,}", n)]
    n1 = clean_news[0] if len(clean_news) > 0 else "미 연준 긴축 기조 장기화 우려 및 국채 금리 할인율 상방 압력"
    n2 = clean_news[1] if len(clean_news) > 1 else "실적 가시성 보유 우량주 중심의 세력 차별화 수급 집결"
    n3 = clean_news[2] if len(clean_news) > 2 else "환율 변동성 확대에 따른 외인 자금의 파생 시장 하방 배팅"

    stocks_formatted = (
        "\n".join([f" • **{s.split('(')[0].strip()}**: {s.split('(')[1].replace(')', '') if '(' in s else s}" for s in top_stocks[:5]])
        if top_stocks
        else " • **KODEX 200선물인버스2X**: 지수 하방 헤지 수요 폭주\n • **KODEX 2차전지산업레버리지**: 낙폭 과대 기술적 반등 유입"
    )
    sectors_formatted = ", ".join(top_sectors[:4]) if top_sectors else "전자제품, 전기제품, 석유와가스, 화학"

    return f"""📈 **[STOCK BOT] 블룸버그 프리미엄 마감 시황 브리핑 ({mode_title})**

---

### 1. 🌐 글로벌 매크로 & 국내 증시 스코어카드 (Macro Dashboard)
- **국내 증시 현황**: 📊
    - **KOSPI**: {kospi_info}  |  **KOSDAQ**: {kosdaq_info}
    - **시장 수급 메커니즘**: {supply_info}
- **글로벌 벤치마크 지표**: 🏛️
    - **S&P 500**: {sp500}  |  **NASDAQ**: {nasdaq}  |  **야간선물**: S&P500 {es} / NQ {nq}
    - **VIX**: {vix}  |  **DXY**: {dxy}  |  **미 10년물 금리**: {us10y}  |  **원/달러 환율**: {usdkrw}
    - **WTI 유가**: {wti}  |  **금 선물**: {gold}  |  **비트코인**: {btc}

---

### 2. 📰 [3성급★★★] 글로벌 & 국내 핵심 이슈 분석 (Macro Impact Chain)

- **이슈 1 [3성급★★★]: 통화정책 할인율 압력과 기술주 밸류에이션 리프라이싱** ⚠️
  • **분석 내용**: {n1}
  • **블룸버그 특파원 시각**: 미 국채금리가 {us10y} 선에서 횡보함에 따라 할인율 상승에 취약한 고평가 기술주 전반의 Multiple 조정이 진행 중입니다.

- **이슈 2 [3성급★★★]: 환율 하락과 외국인 자금 이탈 영향 점검** 📊
  • **분석 내용**: {n3}
  • **골드만삭스 전략가 시각**: 원/달러 환율이 {usdkrw} 수준으로 원화 강세가 진행됨에 따라 수출 기업의 환차손 부담이 가중되는 반면, 원자재 수입 기업의 원가 부담은 개선되는 차별화 장세가 연출되고 있습니다.

---

### 3. 🏢 [3성급★★★] 핵심 기업 실적 & 주도 섹터 (Capital Flow Analysis)

- **이슈 3 [3성급★★★]: 실적 모멘텀 및 원자재 수혜주 중심의 차별화 쏠림** 💰
  • **주도 강세 섹터**: {sectors_formatted}
  • **수급 모멘텀**: {n2} 지수의 추가 하락 압력 속에서도 원가 전가력이 확보된 화학/원자재 섹터로 스마트머니의 집중 매수세가 포착되었습니다.

- **거래대금 집중 핵심 종목 및 수급 특징**: 🧨
{stocks_formatted}
  • **세력 매매 의도 분석**: 'KODEX 200선물인버스2X' 등 지수 하방 상품으로의 거래대금 쏠림은 기관 및 세력들이 추가 변동성에 대비해 강력한 **하방 리스크 차단막(Risk Buffer)**을 구축하고 있음을 증명합니다.

---

### 4. 🎯 [3성급★★★] 원자재, 환율 & 자산시장 시사점 (Alternative Risk)
- **금 선물 ({gold}) 폭등**: 지정학적 불안과 통화 가치 하락에 대비한 실물 안전자산으로의 자금 대피 심리가 정점에 달했습니다.
- **WTI 유가 ({wti}) 추이**: 인플레이션 재점화 가능성을 지속 자극하며 중앙은행의 긴축 기조 완화 걸림돌로 작용 중입니다.

---

### 5. 🚀 [Goldman Sachs Strategist] 내일의 전술적 자산 배분 대응 전략
- **방어적 포트폴리오 재편 (Portfolio Risk Management)**:
  1. **현금 비중 35% 엄수**: 파생 인버스 쏠림이 완화되기 전까지 무리한 추격 매수를 금지하고 현금 비중 35% 이상을 엄격히 유지하십시오.
  2. **실적 퀄리티주 분할 접근**: 유가/원자재 상승 수혜 섹터 및 펀더멘털이 확실한 반도체/AI 벨류체인 위주의 분할 매수 전략을 즉시 가동하십시오."""


def generate_unified_report(krx_data, global_data, naver_news, top_stocks, top_sectors):
    prompt = f"""
    [현재 모드]: {mode_title}
    아래 실시간 수집 데이터를 바탕으로 월가 헤지펀드 트레이더 관점에서 단정적이고 결단력 있는 최고급 마켓 인텔리전스를 작성하라.

    [수집된 실시간 시장 데이터]
    - 국내 증시 현황 및 수급: {krx_data}
    - 해외 주요 매크로 지표 (VIX/DXY/야간선물 포함): {global_data}
    - 헤드라인 핵심 뉴스: {naver_news}
    - 거래대금 상위 종목: {top_stocks}
    - 주도 강세 섹터: {top_sectors}
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
    cleaned_content = clean_cjk_junk(cleaned_content)
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
            else:
                print(f"⚠️ 디스코드 전송 실패 (상태 코드: {res.status_code}): {res.text}")
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

    print("🤖 [STOCK BOT] 월가 기관급 AI 리포트 생성 중...")
    unified_report = generate_unified_report(krx_data, global_macro, naver_news, top_stocks, top_sectors)

    print("📲 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_discord_message(unified_report)
    print("✨ 모든 프로세스 완료!")
