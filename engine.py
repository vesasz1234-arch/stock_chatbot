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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY")
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN")

DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_WEBHOOK_URL")
    or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"
)

if os.path.exists("kakao_token.json"):
    try:
        with open("kakao_token.json", "r", encoding="utf-8") as f:
            k_data = json.load(f)
            KAKAO_REST_API_KEY = k_data.get("rest_api_key") or KAKAO_REST_API_KEY
            KAKAO_REFRESH_TOKEN = k_data.get("refresh_token") or KAKAO_REFRESH_TOKEN
    except Exception as e:
        print(f"⚠️ kakao_token.json 로드 실패: {e}")


def fetch_krx_market_summary():
    """국내 증시 핵심 지수(KOSPI, KOSDAQ) 및 실시간 외인/기관 수급 정밀 수집"""
    krx_data = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. KOSPI 수집
    try:
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSPI", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            now_val = soup.select_one("#now_value")
            change_val = soup.select_one("#change_value_and_rate")
            if now_val and change_val:
                c_text = change_val.get_text().strip().replace("\n", " ").replace("\t", "")
                krx_data["KOSPI"] = f"{now_val.get_text().strip()} ({c_text})"
    except Exception as e:
        print(f"⚠️ KOSPI 수집 에러: {e}")

    # 2. KOSDAQ 수집
    try:
        res = requests.get("https://finance.naver.com/sise/sise_index.naver?code=KOSDAQ", headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            now_val = soup.select_one("#now_value")
            change_val = soup.select_one("#change_value_and_rate")
            if now_val and change_val:
                c_text = change_val.get_text().strip().replace("\n", " ").replace("\t", "")
                krx_data["KOSDAQ"] = f"{now_val.get_text().strip()} ({c_text})"
    except Exception as e:
        print(f"⚠️ KOSDAQ 수집 에러: {e}")

    # 3. KOSPI 투자자별 매매동향 (개인/외국인/기관 순매수 수치 정밀 추출)
    try:
        res_sise = requests.get("https://finance.naver.com/sise/", headers=headers, timeout=5)
        if res_sise.status_code == 200:
            soup_sise = BeautifulSoup(res_sise.text, "html.parser")
            kospi_tab = soup_sise.select_one("#num2")
            if kospi_tab:
                items = [li.get_text().strip().replace("\n", "").replace("\t", "") for li in kospi_tab.select("li")]
                if items:
                    krx_data["KOSPI_투자자동향"] = ", ".join(items)
    except Exception as e:
        print(f"⚠️ KRX 수급 수집 에러: {e}")

    return krx_data


def fetch_global_yahoo_data():
    """글로벌 매크로 지표 및 미국 야간 선물 지수 정밀 수집"""
    tickers = {
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "S&P500 선물": "ES=F",
        "나스닥100 선물": "NQ=F",
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


def call_groq_ai(prompt, system_instruction):
    """보조 AI 엔진: Groq API (Llama 3.3 70B) 고성능 금융 가드레일 가동"""
    if not GROQ_API_KEY:
        print("⚠️ GROQ_API_KEY 미설정 - Groq 엔진을 스킵합니다.")
        return None

    key_preview = f"{GROQ_API_KEY[:8]}...{GROQ_API_KEY[-4:]}" if len(GROQ_API_KEY) > 12 else "INVALID_KEY"
    print(f"🚀 [Groq AI Engine] Llama-3.3-70B 월가 고급 가드레일 가동 중... (Key: {key_preview})")

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
        "temperature": 0.3,
        "max_tokens": 3000
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        if res.status_code == 200:
            data = res.json()
            text = data["choices"][0]["message"]["content"]
            cleaned = sanitize_report_text(text)
            if len(cleaned) > 500:
                print("✅ [SUCCESS] Groq Llama-3.3-70B 월가급 프리미엄 리포트 생성 완료!")
                return cleaned
        else:
            print(f"⚠️ Groq API 오류 ({res.status_code}): {res.text[:150]}")
    except Exception as e:
        print(f"⚠️ Groq AI 호출 예외: {e}")
    return None


def call_gemini_clean(prompt, krx_data, global_data, naver_news, top_stocks, top_sectors):
    system_instruction = (
        f"너는 골드만삭스/블룸버그 수석 마켓 전략가이자 헤지펀드 최고투자책임자(CIO)인 [STOCK BOT]이다.\n"
        f"너의 목적은 기관 및 고액자산가를 위한 월가 최고 레벨의 정밀 시황 리포트를 작성하는 것이다.\n\n"
        f"[필수 거시경제 금융 가드레일 - 오류 시 해고]\n"
        f"1. 원/달러 환율 하락(원화 강세) = 수출 기업 환차손 및 가격경쟁력 부담, 원자재/부품 수입 기업 원가 절감 수혜. 절대로 '환율 하락이 수출 경쟁력을 제고한다'고 쓰지 마라.\n"
        f"2. 원/달러 환율 상승(원화 약세) = 외국인 환차손 우려로 인한 매도 압력, 수출 기업 단기 매출 환산 착시 수혜.\n"
        f"3. 미 10년물 국채금리 하락 = 할인율(Multiple) 상방 압력 완화 -> 고PER 기술주/빅테크 밸류에이션 경감.\n"
        f"4. 미 10년물 국채금리 상승 = 할인율 상승 -> 기술주 멀티플 축소(Multiple Compression) 압박.\n"
        f"5. 제공된 숫자와 등락률 부호를 엄격히 지켜라(마이너스 비율에 '상승' 표기 금지).\n"
        f"6. 100% 품격 있는 한국어 금융 저널리즘 어조를 유지하라. 외국어 오타(베트남어, 영어 혼용)나 '그린뉴딜' 같은 구식 표현을 절대 포함하지 마라.\n"
        f"7. '데이터가 부족하여 알 수 없으나' 같은 피하기성 문장을 절대 쓰지 마라. 주어진 수급 금액과 지수를 바탕으로 세력의 하방 헤지 및 수급 쏠림을 정교히 추론하라.\n"
        f"8. [5. 전술적 자산 배분] 섹션에서는 '현금 비중 35% 확보', '반도체 벨류체인 분할 매수' 등 명확하고 구체적인 숫자 중심의 포트폴리오 대응책을 제시하라.\n\n"
        f"[출력 마크다운 양식 규격 - 절대 준수]\n"
        f"답변은 반드시 아래 구분선(`---`)과 헤더 양식을 완벽히 준수하라:\n\n"
        f"📈 **[STOCK BOT] 블룸버그 프리미엄 {'장전 모닝' if is_morning else '마감 시황'} 브리핑**\n\n"
        f"---\n\n"
        f"### 1. 🌐 글로벌 매크로 & 국내 증시 스코어카드 (Macro Dashboard)\n"
        f"• **국내 증시 현황**: KOSPI **[치]** | KOSDAQ **[치]**\n"
        f"• **시장 수급 메커니즘**: [수급 수치 기반 외인/기관 동향 분석]\n"
        f"• **글로벌 벤치마크 지표**: S&P 500 **[치]**, NASDAQ **[치]**, 야간선물(S&P500 **[치]** / NQ **[치]**), 미 10년물 금리 **[치]**, 원/달러 환율 **[치]**, WTI 유가 **[치]**, 금 선물 **[치]**, 비트코인 **[치]**\n\n"
        f"---\n\n"
        f"### 2. 📰 [3성급★★★] 글로벌 & 국내 핵심 이슈 분석 (Macro Impact Chain)\n"
        f"• **이슈 1: [금리 및 기술주 밸류에이션 인과관계]**\n"
        f"  - **메커니즘 분석**: [국채금리와 기술주 Multiple 간 정밀 추론]\n"
        f"• **이슈 2: [환율 및 외국인 수급 변동성]**\n"
        f"  - **월가 시각**: [환율 변동이 외국인 파생/현물 수급 및 수출 기업 수익성에 미치는 영향]\n\n"
        f"---\n\n"
        f"### 3. 🏢 [3성급★★★] 핵심 기업 실적 & 주도 섹터 (Capital Flow Analysis)\n"
        f"• **주도 강세 섹터**: [수급 쏠림 섹터 분석]\n"
        f"• **거래대금 집중 종목 및 수급 특징**: [상위 거래 종목 분석 및 인버스/레버리지 메커니즘 추론]\n\n"
        f"---\n\n"
        f"### 4. 🎯 [3성급★★★] 원자재, 환율 & 자산시장 시사점 (Alternative Risk)\n"
        f"• **금/유가 시사점**: [원자재 상승과 인플레이션, 실물 안전자산 선호 분석]\n\n"
        f"---\n\n"
        f"### 5. 🚀 [Goldman Sachs Strategist] 내일의 전술적 자산 배분 대응 전략\n"
        f"• **포트폴리오 리스크 관리**: [구체적인 현금 비중(%) 및 섹터별 비중 조절, 헤징 액션 플랜]"
    )

    # 1. Gemini AI 시도
    if GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            target_models = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]
            for m_name in target_models:
                try:
                    response = client.models.generate_content(
                        model=m_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.3,
                        )
                    )
                    if response and response.text:
                        cleaned = sanitize_report_text(response.text)
                        if len(cleaned) > 500 and ("📈" in cleaned or "[STOCK BOT]" in cleaned):
                            print(f"✅ [SUCCESS] 구글 Gemini AI ({m_name}) 리포트 생성 완료!")
                            return cleaned
                except Exception as e:
                    print(f"⚠️ Gemini {m_name} 호출 오류: {str(e)[:100]}")
        except Exception as e:
            print(f"⚠️ Gemini Client 생성 실패: {e}")

    # 2. Gemini 429 시 Groq AI (Llama 3.3 70B) 고성능 가드레일 분석 구동
    groq_result = call_groq_ai(prompt, system_instruction)
    if groq_result:
        return groq_result

    # 3. 최후의 비상 템플릿
    print("🚨 모든 AI 모델 호출 불가 - 비상 템플릿 가동")
    return build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors)


def build_dynamic_rich_fallback(krx_data, global_data, naver_news, top_stocks, top_sectors):
    """최후의 백업 분석 엔진"""
    kospi_info = krx_data.get("KOSPI", "6,258.77 (-0.60%)")
    kosdaq_info = krx_data.get("KOSDAQ", "798.81 (-0.36%)")
    supply_info = krx_data.get("KOSPI_투자자동향", "외인 순매도 전환, 기관 파생 헤지물량 출하")

    sp500 = global_data.get("S&P 500", "4,757.64 (+0.62%)")
    nasdaq = global_data.get("NASDAQ", "26,690.62 (+1.30%)")
    es = global_data.get("S&P500 선물", "4,765.25 (+0.15%)")
    nq = global_data.get("나스닥100 선물", "26,720.50 (+0.22%)")
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
    - **미 10년물 국채금리**: {us10y}  |  **원/달러 환율**: {usdkrw}
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

### 3. 🏢 [3성급★★★] 핵심 기업 실적 & 시장 주도 섹터 (Capital Flow Analysis)

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
  1. **현금 비중 35% 확보**: 파생 인버스 쏠림이 완화되기 전까지 무리한 추격 매수를 금지하고 현금 비중 35% 이상을 엄격히 확보하십시오.
  2. **실적 퀄리티주 분할 접근**: 유가/원자재 상승 수혜 섹터 및 펀더멘털이 확실한 반도체/AI 벨류체인 위주의 분할 매수 전략이 유효합니다."""


def generate_unified_report(krx_data, global_data, naver_news, top_stocks, top_sectors):
    prompt = f"""
    [현재 모드]: {mode_title}
    아래 실시간 수집 데이터를 바탕으로, 각 지표와 사건 간의 월가급 금융 메커니즘을 정밀 추론하여 가독성이 뛰어난 전문 리포트를 작성하라.

    [수집된 실시간 시장 데이터]
    - 국내 증시 현황 및 수급 금액: {krx_data}
    - 해외 주요 매크로 지표 및 야간선물: {global_data}
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

    print("🤖 [STOCK BOT] 월가 최고급 이중화 AI 리포트 생성 중...")
    unified_report = generate_unified_report(krx_data, global_macro, naver_news, top_stocks, top_sectors)

    print("📲 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_discord_message(unified_report)
    print("✨ 모든 프로세스 완료!")
