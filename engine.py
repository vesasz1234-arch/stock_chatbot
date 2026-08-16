import argparse
import json
import os
import re
import sys
import time
import warnings
import xml.etree.ElementTree as ET
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

DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_WEBHOOK_URL")
    or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"
)


def sanitize_market_text(text):
    if not text:
        return ""
    text = re.sub(r'(-\d+\.?\d*%)\s*상승', r'\1', text)
    text = re.sub(r'(\+\d+\.?\d*%)\s*하락', r'\1', text)
    return text


def clean_cjk_junk(text):
    if not text:
        return ""
    cleaned = re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
    return cleaned


def fetch_krx_market_summary():
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


def mine_yahoo_finance_rss():
    raw_news = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    rss_urls = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^IXIC,NVDA,AAPL,TSLA,CL=F&region=US&lang=en-US",
        "https://finance.yahoo.com/news/rssindex"
    ]

    for url in rss_urls:
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                for item in root.findall(".//item"):
                    title = item.find("title").text if item.find("title") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    if title and len(title) > 10:
                        raw_news.append({
                            "source": "Yahoo Finance (US)",
                            "title": title.strip(),
                            "summary": desc.strip()[:250] if desc else title.strip()
                        })
        except Exception as e:
            print(f"⚠️ Yahoo RSS 마이닝 예외: {e}")

    return raw_news


def mine_naver_finance_news():
    raw_news = []
    headers = {"User-Agent": "Mozilla/5.0"}
    urls = [
        "https://finance.naver.com/news/mainnews.naver",
        "https://finance.naver.com/news/news_list.naver?mode=LSS2D&office_id=018&section_id=101"
    ]

    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for article in soup.select(".articleList .articleSubject a, .newsList .articleSubject a"):
                    title = article.get_text().strip()
                    if title and len(title) > 8:
                        raw_news.append({
                            "source": "Naver Finance (KR)",
                            "title": title,
                            "summary": title
                        })
        except Exception as e:
            print(f"⚠️ 네이버 뉴스 마이닝 예외: {e}")

    return raw_news


def rank_and_filter_3star_news(all_news):
    high_impact_keywords = {
        "fomc": 10, "fed": 10, "rate": 10, "cpi": 10, "ppi": 10, "pce": 10, "inflation": 9, "yield": 8, "gdp": 9, "jobs": 9,
        "금리": 10, "연준": 10, "물가": 9, "인플레이션": 9, "환율": 9, "달러": 8, "실업률": 9, "고용": 9,
        "nvidia": 10, "semiconductor": 9, "ai": 9, "earnings": 10, "guidance": 10, "revenue": 9,
        "엔비디아": 10, "반도체": 9, "hbm": 10, "실적": 10, "가이던스": 10, "삼성전자": 9, "sk하이닉스": 10,
        "opec": 8, "oil": 8, "war": 8, "tariff": 9, "crude": 8,
        "유가": 8, "중동": 8, "관세": 9, "지정학": 8, "안전자산": 8
    }

    scored_news = []
    seen_titles = set()

    for item in all_news:
        title_lower = item["title"].lower()
        if title_lower in seen_titles:
            continue
        seen_titles.add(title_lower)

        score = 0
        for kw, weight in high_impact_keywords.items():
            if kw in title_lower or kw in item["summary"].lower():
                score += weight

        scored_news.append({
            "source": item["source"],
            "title": item["title"],
            "summary": item["summary"],
            "score": score
        })

    scored_news.sort(key=lambda x: x["score"], reverse=True)
    return scored_news[:5]


def fetch_market_intelligence():
    yahoo_news = mine_yahoo_finance_rss()
    naver_news = mine_naver_finance_news()
    all_mined_news = yahoo_news + naver_news

    top_3star_news = rank_and_filter_3star_news(all_mined_news)
    top_stocks, top_sectors = [], []
    headers = {"User-Agent": "Mozilla/5.0"}

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
                    if len(top_stocks) >= 8:
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
                    if len(top_sectors) >= 6:
                        break
    except Exception as e:
        print(f"주도 업종 수집 에러: {e}")

    return top_3star_news, top_stocks, top_sectors


# =========================================================
# 🤖 1순위 구글 Gemini AI 동적 모델 스캔(Dynamic Scanning) 가동
# =========================================================
def call_gemini_primary(prompt, system_instruction):
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY 미설정 - 다음 모델로 이관")
        return None

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        # 1. API 키에서 현재 활성화된 모델 목록 동적 스캔
        candidate_models = []
        try:
            all_models = list(client.models.list())
            for m in all_models:
                m_name = getattr(m, "name", "") or getattr(m, "model_id", "")
                clean_name = m_name.replace("models/", "")
                if "flash" in clean_name or "pro" in clean_name:
                    candidate_models.append(clean_name)
        except Exception as e:
            print(f"⚠️ Gemini 모델 동적 스캔 중 예외: {e}")

        # 기본 예비 후보군
        default_candidates = [
            "gemini-2.5-flash-lite",
            "gemini-3-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash-lite",
        ]
        for m_def in default_candidates:
            if m_def not in candidate_models:
                candidate_models.append(m_def)

        # 2. 스캔된 활성 모델 순차 실행
        for m_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=m_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.1,
                        max_output_tokens=3500
                    )
                )
                if response and response.text:
                    cleaned = sanitize_report_text(response.text)
                    cleaned = clean_cjk_junk(cleaned)
                    if len(cleaned) > 700:
                        print(f"✅ [SUCCESS] 구글 Gemini AI ({m_name}) 월가급 리포트 생성 완료! (길이: {len(cleaned)}자)")
                        return cleaned
            except Exception as e:
                continue
    except Exception as e:
        print(f"⚠️ Gemini Client 생성 실패: {e}")

    return None


def call_groq_ai(prompt, system_instruction):
    if not GROQ_API_KEY:
        return None

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
        "max_tokens": 3500
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=30)
        if res.status_code == 200:
            data = res.json()
            text = data["choices"][0]["message"]["content"]
            cleaned = sanitize_report_text(text)
            cleaned = clean_cjk_junk(cleaned)
            if len(cleaned) > 700:
                print("✅ [SUCCESS] Groq Llama-3.3-70B 월가급 리포트 생성 완료!")
                return cleaned
    except Exception:
        pass
    return None


def call_openrouter_ai(prompt, system_instruction):
    if not OPENROUTER_API_KEY:
        return None

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://github.com/vesasz1234-arch/stock_chatbot",
        "X-Title": "Stock Bot Automation",
        "Content-Type": "application/json"
    }

    target_models = [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "deepseek/deepseek-r1:free"
    ]

    for model_name in target_models:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 3500
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=35)
            if res.status_code == 200:
                data = res.json()
                text = data["choices"][0]["message"]["content"]
                cleaned = sanitize_report_text(text)
                cleaned = clean_cjk_junk(cleaned)
                if len(cleaned) > 700:
                    print(f"✅ [SUCCESS] OpenRouter ({model_name}) 월가급 리포트 생성 완료!")
                    return cleaned
        except Exception:
            pass
    return None


def call_ai_pipeline(prompt, krx_data, global_data, top_3star_news, top_stocks, top_sectors):
    time_context_instruction = (
        "너는 글로벌 투자은행 골드만삭스/블룸버그 수석 마켓 전략가이자 [STOCK BOT] 최고투자책임자(CIO)이다.\n"
        "너의 역할은 개장 전 밤사이 미증시 마감 결과, 핵심 경제지표 발표, 글로벌 주요 기업 실적, 마이닝된 별3개(★★★) 뉴스 이슈를 종합 분석하여 "
        "오늘 한국증시의 주도 예상 섹터와 대장주를 명시하고 깊이 있는 전술적 트레이딩 가이드를 제공하는 것이다."
        if is_morning
        else "너는 글로벌 투자은행 골드만삭스/블룸버그 수석 마켓 전략가이자 [STOCK BOT] 최고투자책임자(CIO)이다.\n"
        "너의 역할은 장 마감 후 한국증시 수급과 거래대금, 뉴스에 많이 노출된 핵심 테마/섹터를 정밀 분석하여 "
        "내일 시장 참여자들이 추적(Trace)해야 할 스마트머니 집중 섹터와 핵심 추적 종목 리스트를 제공하고 최고의 내일 대응 전략을 수립하는 것이다."
    )

    morning_structure_guide = (
        "[출력 작성 가이드 - 절대로 요약하여 단축하지 말고 깊이 있게 작성하라]\n"
        "[주의 사항: 종목 매핑 시 반드시 수집 데이터에 존재하는 실제 한국 KOSPI/KOSDAQ 상장 기업만 정확히 연결할 것. 비상장사 및 세부 자회사 언급 엄격 금지]\n\n"
        "### 2. 💡 [스탁봇 CIO 오늘의 전술적 핵심 한마디]\n"
        "• **개장 전 시장 경보 및 핵심 지침**: [어젯밤 미증시/지표/실적 변동에 따른 오늘 한국증시 트레이딩 방향성과 위험 관리 주의사항을 단정적으로 2~3문장 제시]\n\n"
        "---\n\n"
        "### 3. 📰 [3성급★★★] 마이닝 기반 글로벌 핵심 이슈 & 경제지표/실적 (Macro Impact Chain)\n"
        "• **이슈 1: [별3개 기사 1 제목]**\n"
        "  - **메커니즘 분석**: [상세 파급 경로 분석]\n"
        "  - **월가 기관 포지셔닝**: [헤지펀드 및 기관 대응]\n"
        "• **이슈 2: [별3개 기사 2 제목]**\n"
        "  - **메커니즘 분석**: [상세 파급 경로 분석]\n"
        "  - **국내 증시 파급 효과**: [수출/수입 기업 영향]\n\n"
        "---\n\n"
        "### 4. 🎯 [3성급★★★] 오늘 개장 전 주도 예상 섹터 & 대장주 Top 1, Top 2 (Capital Flow)\n"
        "• **주도 예상 섹터 1: [실제 수집된 주도 섹터명]**\n"
        "  - **대장주 1위**: [해당 섹터의 실제 대표 상장 종목명] (이유 및 촉매 명시)\n"
        "  - **대장주 2위**: [해당 섹터의 실제 대표 상장 종목명] (이유 및 촉매 명시)\n"
        "• **주도 예상 섹터 2: [실제 수집된 주도 섹터명]**\n"
        "  - **대장주 1위**: [해당 섹터의 실제 대표 상장 종목명] (이유 및 촉매 명시)\n"
        "  - **대장주 2위**: [해당 섹터의 실제 대표 상장 종목명] (이유 및 촉매 명시)\n\n"
        "---\n\n"
        "### 5. 🚀 [Goldman Sachs Strategist] 오늘 개장 전 전술적 자산 배분 대응 전략\n"
        "• **포트폴리오 리스크 관리**: [현금 비중(%) 및 구체적 진입/손절 액션 플랜 제시]"
    )

    evening_structure_guide = (
        "[출력 작성 가이드 - 절대로 요약하여 단축하지 말고 깊이 있게 작성하라]\n"
        "[주의 사항: 비상장 자회사 언급 엄격 금지. 반드시 한국 KOSPI/KOSDAQ 실제 상장 종목(S-Oil, HD현대, GS 등)만 언급할 것]\n\n"
        "### 2. 💡 [스탁봇 CIO 오늘의 마감 핵심 한마디 & 내일 추적 포인트]\n"
        "• **오늘 장 마감 요약 및 내일 추적 지침**: [오늘 수급 집중 원인과 내일 연장선상에서 추적해야 할 핵심 거래 포인트 2~3문장 제시]\n\n"
        "---\n\n"
        "### 3. 📰 [3성급★★★] 마이닝 기반 오늘 시장 핵심 이슈 & 뉴스 (Macro Impact Chain)\n"
        "• **이슈 1: [별3개 기사 1 제목]**\n"
        "  - **수급 및 거래대금 분석**: [오늘 자금 쏠림 원인]\n"
        "  - **파급 인과관계**: [산업 및 기업 실적 영향]\n"
        "• **이슈 2: [별3개 기사 2 제목]**\n"
        "  - **기관 및 외인 포지셔닝**: [스마트머니 매수 세력 의도]\n\n"
        "---\n\n"
        "### 4. 🏢 [3성급★★★] 오늘 스마트머니 집중 주도 섹터 & 핵심 관심 종목 (Trace List)\n"
        "• **주도 테마/섹터 1**: [오늘 수급+뉴스 집중 실제 섹터명]\n"
        "  - **내일 연속 추적 종목**: [실제 KOSPI/KOSDAQ 상장 종목명 2개 이상 및 수급 특징 상세 작성]\n"
        "• **주도 테마/섹터 2**: [오늘 수급+뉴스 집중 실제 섹터명]\n"
        "  - **내일 연속 추적 종목**: [실제 KOSPI/KOSDAQ 상장 종목명 2개 이상 및 수급 특징 상세 작성]\n\n"
        "---\n\n"
        "### 5. 🚀 [Goldman Sachs Strategist] 내일 대응 전술적 자산 배분 전략\n"
        "• **포트폴리오 리스크 관리**: [현금 비중(%) 및 내일 시초가 대응 수립]"
    )

    system_instruction = (
        f"{time_context_instruction}\n\n"
        "[절대 규칙 - 위반 시 무효]\n"
        "1. 절대로 단축하거나 1줄 요약하지 말 것. 월가 기관 보고서처럼 풍부한 세부 논리와 근거를 포함할 것.\n"
        "2. 금지 단어: '~를 의미한다', '~관련이 있습니다', '~영향을 미칠 수 있습니다', '~시사합니다', '~생각됩니다'.\n"
        "3. 어조: 단정적/명령적 어조('~로 판단됨', '~를 기록', '~가 가시화됨', '~를 강제함')만 사용할 것.\n"
        "4. 금지 문자: 중국어/일본어 한자, 가타카나.\n"
        "5. 종목 정밀성: 실제 상장되어 있는 국내 주식만 언급할 것(비상장사 언급 엄금).\n\n"
        f"{morning_structure_guide if is_morning else evening_structure_guide}"
    )

    # 1. 구글 Gemini AI (동적 모델 스캔) 우선 호출
    res = call_gemini_primary(prompt, system_instruction)
    if res:
        return res

    # 2. Groq Llama-3.3-70B 호출
    res = call_groq_ai(prompt, system_instruction)
    if res:
        return res

    # 3. OpenRouter 70B+ 선별 모델 호출
    res = call_openrouter_ai(prompt, system_instruction)
    if res:
        return res

    return None


def build_dynamic_rich_fallback(krx_data, global_data, top_3star_news, top_stocks, top_sectors):
    n1 = top_3star_news[0]["title"] if len(top_3star_news) > 0 else "미 연준 긴축 기조 및 매크로 변동성 주시"
    n2 = top_3star_news[1]["title"] if len(top_3star_news) > 1 else "실적 가시성 보유 우량주 중심 차별화 수급"

    stocks_formatted = (
        "\n".join([f" • **{s.split('(')[0].strip()}**: {s.split('(')[1].replace(')', '') if '(' in s else s}" for s in top_stocks[:5]])
        if top_stocks
        else " • **주요 주도주**: 실시간 거래대금 수급 추적 중"
    )
    sectors_formatted = ", ".join(top_sectors[:4]) if top_sectors else "반도체, 2차전지, 바이오, 방산"

    target_day = "오늘" if is_morning else "내일"

    return f"""### 2. 💡 [스탁봇 CIO {'오늘' if is_morning else '내일'}의 전술적 핵심 한마디]
• **시장 핵심 지침**: 미 매크로 금리 변동성과 주요 섹터 자금 이동이 가시화되는 시점임. 단기 추격 매수를 자제하고 거래대금 상위 수급주 중심으로 분할 진입 전략을 권장함.

---

### 3. 📰 [3성급★★★] 마이닝 기반 글로벌 & 국내 핵심 이슈 분석 (Macro Impact Chain)
- **이슈 1 [3성급★★★]: {n1}** ⚠️
  • **월가 분석 시각**: 미 금리 및 환율 변동성에 따라 고평가 기술주 전반의 Multiple 차별화 장세 진행 중.

- **이슈 2 [3성급★★★]: {n2}** 📊
  • **기관 전략가 시각**: 원/달러 환율 변동에 따라 수출주 및 원자재 수입주의 세부 수급 이탈입 연출.

---

### 4. 🏢 [3성급★★★] 핵심 기업 실적 & 시장 주도 섹터 (Capital Flow Analysis)
- **주도 강세 섹터**: {sectors_formatted}
- **거래대금 집중 핵심 종목 및 수급 특징**: 🧨
{stocks_formatted}

---

### 5. 🚀 [Goldman Sachs Strategist] {target_day}의 전술적 자산 배분 대응 전략
- **포트폴리오 리스크 관리**:
  1. **현금 비중 35% 엄수**: 하방 변동성 완화 시까지 추격 매수 금지 및 현금 비중 유지.
  2. **실적 퀄리티주 분할 접근**: AI 반도체 벨류체인 및 실적 확실성 보유 종목 중심의 분할 매수 전략 가동."""


def generate_unified_report(krx_data, global_data, top_3star_news, top_stocks, top_sectors):
    formatted_news = "\n".join([f"- [점수:{n['score']}점 | 출처:{n['source']}] {n['title']} (요약: {n['summary']})" for n in top_3star_news])

    prompt = f"""
    [현재 모드]: {mode_title}
    아래 마이닝된 [알고리즘 추출 별3개(★★★) 핵심 뉴스] 및 수집 데이터를 바탕으로 최고의 월가 기관급 리포트를 풍부한 분량으로 작성하라.

    [알고리즘 추출 별3개(★★★) 핵심 마이닝 기사 및 지표/실적]
    {formatted_news}

    [실시간 시장 수집 데이터 - 반드시 아래 실측 데이터 내의 상장 종목과 섹터만 사용할 것]
    - 국내 증시 현황 및 수급: {krx_data}
    - 해외 주요 매크로 지표: {global_data}
    - 실제 수집된 거래대금 상위 종목: {top_stocks}
    - 실제 수집된 주도 강세 섹터: {top_sectors}
    """
    
    # 1. 파이썬 실측 수치 기반 스코어카드 헤더(섹션 1) 생성
    kospi_info = krx_data.get("KOSPI", "정보 수집 중")
    kosdaq_info = krx_data.get("KOSDAQ", "정보 수집 중")
    supply_info = krx_data.get("KOSPI_수급", "외인/기관 동향 스캔 중")

    sp500 = global_data.get("S&P 500", "-")
    nasdaq = global_data.get("NASDAQ", "-")
    es = global_data.get("S&P500 선물", "-")
    nq = global_data.get("나스닥100 선물", "-")
    vix = global_data.get("VIX 변동성지수", "-")
    dxy = global_data.get("달러 인덱스", "-")
    us10y = global_data.get("미 10년물 국채금리", "-")
    usdkrw = global_data.get("원/달러 환율", "-")
    wti = global_data.get("WTI 유가", "-")
    gold = global_data.get("금 선물", "-")
    btc = global_data.get("비트코인", "-")

    macro_dashboard = f"""📈 **[STOCK BOT] {mode_title}**

---

### 1. 🌐 글로벌 매크로 & 국내 증시 스코어카드 (Macro Dashboard)
• **국내 증시 현황**: KOSPI **{kospi_info}** | KOSDAQ **{kosdaq_info}**
• **시장 수급 메커니즘**: {supply_info}
• **글로벌 벤치마크 지표**: S&P 500 **{sp500}**, NASDAQ **{nasdaq}**, 야간선물(S&P500 **{es}** / NQ **{nq}**), VIX **{vix}**, DXY **{dxy}**, 미 10년물 금리 **{us10y}**, 원/달러 환율 **{usdkrw}**, WTI 유가 **{wti}**, 금 선물 **{gold}**, 비트코인 **{btc}**

---
"""

    # 2. AI 파이프라인 생성 본문 호출
    llm_analysis = call_ai_pipeline(prompt, krx_data, global_data, top_3star_news, top_stocks, top_sectors)

    if llm_analysis and "### 2." in llm_analysis:
        analysis_part = llm_analysis.split("### 2.", 1)[1]
        final_report = macro_dashboard + "### 2." + analysis_part
    else:
        fallback_body = build_dynamic_rich_fallback(krx_data, global_data, top_3star_news, top_stocks, top_sectors)
        final_report = macro_dashboard + fallback_body

    return final_report


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
        print("⚠️ DISCORD_WEBHOOK_URL 미설정 스킵")
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
    top_3star_news, top_stocks, top_sectors = fetch_market_intelligence()

    print(f"⛏️ [마이닝 완료] 별3개(★★★) 핵심 뉴스 {len(top_3star_news)}건 추출")
    print("🤖 [STOCK BOT] 월가 기관급 AI 리포트 생성 중...")
    unified_report = generate_unified_report(krx_data, global_macro, top_3star_news, top_stocks, top_sectors)

    print("📲 메신저 송출 시작...")
    send_telegram_message(unified_report)
    send_discord_message(unified_report)
    print("✨ 모든 프로세스 완료!")