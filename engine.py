import json
import os
import re
import time
import warnings
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import google.generativeai as genai
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

# =========================================================
# ⏰ 타임존 (한국 표준시 KST) 및 장전/장후 모드 판별 Engine
# =========================================================
KST = timezone(timedelta(hours=9))
now_kst = datetime.now(KST)
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

TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or "8612239847:AAFLgGhtJm8cOS9-eaW4wsSsQO2-9bWW0Qw"
)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or "-1004358276766"

KAKAO_REST_API_KEY = (
    os.environ.get("KAKAO_REST_API_KEY") or "e9d371ad51e7b46fb2baf2d959547eef"
)
KAKAO_REFRESH_TOKEN = (
    os.environ.get("KAKAO_REFRESH_TOKEN")
    or "d4gKu3IG-pRQB3_iH6uf0Rr5LnPlzlvuAAAAAgoNIBsAAAGfu-U2n_8D-j8FVvr5"
)

DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_WEBHOOK_URL")
    or "https://discordapp.com/api/webhooks/1534114852082155574/ggvSBAoyDs1JbPwW7V8hEWTRVX-5MCTzduMiqv0mxKEp5hLoZOsZ1TXDRzo8-cNdE6bW"
)

if os.path.exists("kakao_token.json"):
  try:
    with open("kakao_token.json", "r", encoding="utf-8") as f:
      k_data = json.load(f)
      KAKAO_REST_API_KEY = k_data.get("rest_api_key") or KAKAO_REST_API_KEY
      KAKAO_REFRESH_TOKEN = (
          k_data.get("refresh_token") or KAKAO_REFRESH_TOKEN
      )
  except Exception as e:
    print(f"⚠️ kakao_token.json 로드 실패: {e}")

if GEMINI_API_KEY:
  genai.configure(api_key=GEMINI_API_KEY)


def fetch_krx_market_summary():
  """국내 증시 핵심 지수(KOSPI, KOSDAQ) 및 정확한 수급 정밀 수집"""
  krx_data = {}
  headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

  try:
    for name, code in [("KOSPI", "KOSI"), ("KOSDAQ", "KOSQ")]:
      res = requests.get(
          f"https://finance.naver.com/sise/sise_index.naver?code={code}",
          headers=headers,
          timeout=5,
      )
      if res.status_code == 200:
        soup = BeautifulSoup(res.text, "html.parser")
        now_val = soup.select_one("#now_value")
        change_val = soup.select_one("#change_value_and_rate")
        if now_val and change_val:
          c_text = (
              change_val.get_text()
              .strip()
              .replace("\n", " ")
              .replace("\t", "")
          )
          krx_data[name] = f"{now_val.get_text().strip()} ({c_text})"
  except Exception as e:
    print(f"⚠️ KRX 지수 수집 에러: {e}")

  try:
    res_sise = requests.get(
        "https://finance.naver.com/sise/", headers=headers, timeout=5
    )
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
    res = requests.get(
        "https://finance.naver.com/news/mainnews.naver",
        headers=headers,
        timeout=10,
    )
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
    res = requests.get(
        "https://finance.naver.com/sise/sise_quant.naver?sosok=0",
        headers=headers,
        timeout=10,
    )
    if res.status_code == 200:
      soup = BeautifulSoup(res.text, "html.parser")
      for row in soup.select("table.type_2 tr"):
        cols = row.select("td")
        if len(cols) > 5:
          name = cols[1].get_text().strip()
          price = cols[2].get_text().strip()
          change = (
              cols[4].get_text().strip().replace("\n", "").replace("\t", "")
          )
          if name and name not in ["종목명", ""]:
            top_stocks.append(f"{name} ({price}원 | {change})")
          if len(top_stocks) >= 6:
            break
  except Exception as e:
    print(f"거래대금 종목 수집 에러: {e}")

  try:
    res = requests.get(
        "https://finance.naver.com/sise/sise_group.naver?type=upjong",
        headers=headers,
        timeout=10,
    )
    if res.status_code == 200:
      soup = BeautifulSoup(res.text, "html.parser")
      for row in soup.select("table.type_1 tr"):
        cols = row.select("td")
        if len(cols) > 2:
          sec_name = cols[0].get_text().strip()
          sec_change = (
              cols[2].get_text().strip().replace("\n", "").replace("\t", "")
          )
          if sec_name:
            top_sectors.append(f"{sec_name} ({sec_change})")
          if len(top_sectors) >= 5:
            break
  except Exception as e:
    print(f"주도 업종 수집 에러: {e}")

  return naver_news, top_stocks, top_sectors


def call_gemini_clean(
    prompt, krx_data, global_data, naver_news, top_stocks, top_sectors
):
  system_instruction = (
      f"너는 블룸버그(Bloomberg) 수석 특파원이자 골드만삭스 수석 글로벌 마켓 전략가인 [STOCK BOT]이다.\n"
      f"너는 단순 수급이나 ETF 시세를 나열하는 자가 아니다. 시장의 거시 경제 흐름, 기업 실적, 헤드라인 뉴스의 파급력을 깊이 있게 분석하라.\n"
      f"답변은 반드시 '📈 **[STOCK BOT] 블룸버그 프리미엄 마감 시황 브리핑**'으로 시작해야 한다.\n"
      f"제공된 [입력 데이터]에 없는 코스피/코스닥 지수 수치를 왜곡하거나 지어내지 마라(할루시네이션 절대 금지).\n"
      f"반드시 3성급(⭐⭐⭐) 핵심 이슈, 3성급 주요 기업 실적/모멘텀, 3성급 경제지표 분석을 명확히 구분하여 월가 최고 수준의 격조 높은 한국어로 작성하라."
  )

  # ⚡ 1순위로 즉시 성공했던 정규 모델(gemini-flash-latest)을 맨 앞에 배치
  target_models = [
      "models/gemini-flash-latest",
      "models/gemini-2.0-flash",
      "models/gemini-1.5-flash",
  ]

  for m_name in target_models:
    try:
      model = genai.GenerativeModel(
          model_name=m_name, system_instruction=system_instruction
      )
      res = model.generate_content(prompt)
      if res and res.text:
        cleaned = sanitize_report_text(res.text)
        if len(cleaned) > 300 and (
            "📈" in cleaned or "[STOCK BOT]" in cleaned
        ):
          print(f"✅ 정규 AI 모델 ({m_name}) 호출 및 생성 성공!")
          return cleaned
    except Exception as e:
      print(f"⚠️ 모델 {m_name} 호출 에러: {e}")
      time.sleep(1)
      continue

  print("🚨 모든 정규 AI 쿼터 초과 - 100% 동적 프리미엄 리포트 백업 엔진 가동")
  return build_dynamic_rich_fallback(
      krx_data, global_data, naver_news, top_stocks, top_sectors
  )


def build_dynamic_rich_fallback(
    krx_data, global_data, naver_news, top_stocks, top_sectors
):
  """블룸버그 특파원 스타일 100% 동적 프리미엄 백업 보고서 엔진"""
  kospi_info = krx_data.get("KOSPI", "KOSPI 지수 집계 중")
  kosdaq_info = krx_data.get("KOSDAQ", "KOSDAQ 지수 집계 중")
  supply_info = krx_data.get("KOSPI_투자자동향", "외인/기관 동향 분석 중")

  macro_str = (
      ", ".join([f"{k}: {v}" for k, v in global_data.items()])
      if global_data
      else "글로벌 매크로 지표 변동성 유지"
  )

  clean_news = [n for n in naver_news if not re.search(r"[a-zA-Z]{6,}", n)]
  n1 = (
      clean_news[0]
      if len(clean_news) > 0
      else "미 연준 통화정책 향방 및 글로벌 국채 금리 변동성 상존"
  )
  n2 = (
      clean_news[1]
      if len(clean_news) > 1
      else "국내 핵심 주도 섹터 3분기 실적 전망 및 수급 집중"
  )
  n3 = (
      clean_news[2]
      if len(clean_news) > 2
      else "원/달러 환율 추이 및 지정학적 리스크에 따른 자금 이탈 체크"
  )

  stocks_str = (
      "\n".join([f" • {s}" for s in top_stocks[:5]])
      if top_stocks
      else " • 거래대금 상위 주도주 집계 중"
  )
  sectors_str = (
      ", ".join(top_sectors[:4]) if top_sectors else "핵심 업종 순환매 진행"
  )

  return f"""📈 **[STOCK BOT] 블룸버그 프리미엄 마감 시황 브리핑 ({mode_title})**

---

### 1. 🌐 글로벌 매크로 & 국내 증시 스코어카드 (Bloomberg Macro Brief)
- **국내 증시 마감 스코어**: 📊
    - **KOSPI**: {kospi_info}
    - **KOSDAQ**: {kosdaq_info}
    - **시장 투자자 수급**: {supply_info}
- **글로벌 핵심 경제 지표**: 🏛️
    - {macro_str}
- **매크로 총평**: 미 국채 금리와 원/달러 환율 흐름이 증시 할인율 및 외국인 수급 방향성을 결정짓는 핵심 분수령으로 작용하고 있습니다.

---

### 2. 📰 ⭐3성급(⭐⭐⭐) 글로벌 & 국내 핵심 이슈 분석 (Market Impact)

- **이슈 1 (중요도 ⭐⭐⭐): 통화정책 & 매크로 환경 재편** ⚠️
  • {n1}
    - **블룸버그 분석 (Bloomberg Insight)**: 미 연준의 금리 경로 전망에 따라 글로벌 위험자산 선호 심리가 재편되고 있으며, 현금 창출력이 우수한 퀄리티 우량주로 자금이 집결 중입니다.

- **이슈 2 (중요도 ⭐⭐⭐): 글로벌 수급 이탈 및 환율 변동성** 📊
  • {n3}
    - **블룸버그 분석 (Bloomberg Insight)**: 환율 상방 압력 해소 여부가 외국인 현·선물 순매수 복귀의 선행 지표가 될 것입니다.

---

### 3. 🏢 ⭐3성급(⭐⭐⭐) 핵심 기업 실적 & 시장 주도 섹터 (Corporate Earnings & Movers)

- **이슈 3 (중요도 ⭐⭐⭐): 실적 가시성 보유 주도 업종 쏠림** 💰
  • {n2}
    - **주도 강세 섹터**: {sectors_str}
    - **실전 모멘텀**: 3분기 실적 가시성이 입증된 핵심 섹터로 스마트머니의 차별화된 유입이 관찰됩니다.

- **거래대금 집중 핵심 종목**: 🧨
{stocks_str}
    - **기업/수급 분석**: 대장주 중심의 거래대금 유입이 지속되고 있으며, 실적 모멘텀이 유효한 주도주의 눌림목 지지력이 확인되고 있습니다.

---

### 4. 🎯 ⭐3성급(⭐⭐⭐) 원자재, 환율 & 자산시장 시사점 (Economic Indicators)
- **환율 & 금리**: 원/달러 환율과 국채 금리의 안정이 가치 평가(Valuation) 부담을 완화시키고 있습니다.
- **원자재 & 대체 자산**: 유가 및 금 선물의 흐름은 인플레이션 재점화 및 리스크 헤지 수요를 명확히 반영하고 있습니다.

---

### 5. 🚀 [STOCK BOT] 월가 전략 가이드 & 내일의 대응 전략
- **전술적 자산 배분 (Asset Allocation)**:
    1. **주도주 눌림목 분할 접근**: 거래대금이 터진 상위 섹터 내 실적 우량주 위주의 분할 매수 전략.
    2. **리스크 관리**: 환율 변동성 구간을 활용한 적정 현금 비중 유지 권고."""


def generate_unified_report(
    krx_data, global_data, naver_news, top_stocks, top_sectors
):
  prompt = f"""
    [현재 모드]: {mode_title}
    아래 수집된 실제 데이터 기반으로 블룸버그 수석 기자의 관점에서 깊이 있는 최고급 마감 시황 리포트를 작성하라.

    [실제 수집 데이터]
    - 국내 증시 지수 및 수급: {krx_data}
    - 해외 매크로 지표: {global_data}
    - 헤드라인 뉴스: {naver_news}
    - 거래대금 상위 핵심 종목: {top_stocks}
    - 주도 섹터: {top_sectors}

    [작성 가이드라인 - 블룸버그 퀄리티]
    1. 단순히 ETF 수치나 단기 수급 점수만 나열하지 말고, **뉴스, 매크로 지표, 기업 실적의 파급력**을 심층 분석하라.
    2. 아래 3가지 ⭐3성급 섹션을 반드시 명확히 포함하여 작성할 것:
       - **⭐3성급(⭐⭐⭐) 글로벌 & 국내 핵심 이슈 분석**
       - **⭐3성급(⭐⭐⭐) 핵심 기업 실적 & 시장 주도 섹터**
       - **⭐3성급(⭐⭐⭐) 원자재, 환율 & 자산시장 시사점**
    3. 제공된 코스피/코스닥 지수 수치를 절대로 왜곡하거나 임의로 지어내지 말 것.
    4. 격조 높은 전문 금융 어조와 이모티콘(📈, 🌐, 📰, 🏢, 🎯, 🚀)을 조합할 것.
    """
  return call_gemini_clean(
      prompt, krx_data, global_data, naver_news, top_stocks, top_sectors
  )


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
    return
  cleaned_content = sanitize_report_text(text_content)
  chunks = split_text_smartly(cleaned_content, max_length=1700)
  headers = {"Content-Type": "application/json"}

  for idx, chunk in enumerate(chunks):
    payload = {
        "content": chunk,
        "username": f"📈 [STOCK BOT] ({'장전' if is_morning else '장후'})",
        "avatar_url": (
            "https://cdn-icons-png.flaticon.com/512/4712/4712109.png"
        ),
    }
    try:
      res = requests.post(
          DISCORD_WEBHOOK_URL,
          data=json.dumps(payload),
          headers=headers,
          timeout=10,
      )
      if res.status_code in [200, 204]:
        print(f"✅ [디스코드 스탁봇] 파트 {idx+1} 전송 완료!")
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

  for idx, chunk in enumerate(chunks):
    payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
    res = requests.post(url, data=payload, timeout=10)
    if res.status_code != 200:
      payload.pop("parse_mode", None)
      requests.post(url, data=payload, timeout=10)
    time.sleep(1)


def send_kakao_message(text_content):
  access_token = get_kakao_access_token()
  if not access_token:
    return
  url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
  headers = {"Authorization": f"Bearer {access_token}"}
  chunks = [text_content[i : i + 850] for i in range(0, len(text_content), 850)][
      :3
  ]

  for idx, chunk in enumerate(chunks):
    template_object = {
        "object_type": "text",
        "text": chunk,
        "link": {
            "web_url": "https://finance.naver.com",
            "mobile_web_url": "https://finance.naver.com",
        },
        "button_title": f"통합 시황 브리핑 ({idx+1})",
    }
    data = {"template_object": json.dumps(template_object)}
    requests.post(url, headers=headers, data=data, timeout=10)
    time.sleep(1)


def get_kakao_access_token():
  if not KAKAO_REST_API_KEY or not KAKAO_REFRESH_TOKEN:
    return None
  url = "https://kauth.kakao.com/oauth/token"
  data = {
      "grant_type": "refresh_token",
      "client_id": KAKAO_REST_API_KEY,
      "refresh_token": KAKAO_REFRESH_TOKEN,
  }
  try:
    response = requests.post(url, data=data, timeout=10)
    tokens = response.json()
    return tokens.get("access_token")
  except Exception:
    return None


if __name__ == "__main__":
  print(f"🚀 [STOCK BOT] 파이프라인 가동 ({mode_title})")

  krx_data = fetch_krx_market_summary()
  global_macro = fetch_global_yahoo_data()
  naver_news, top_stocks, top_sectors = fetch_market_intelligence()

  print("🤖 [STOCK BOT] 프리미엄 통합 AI 리포트 생성 중...")
  unified_report = generate_unified_report(
      krx_data, global_macro, naver_news, top_stocks, top_sectors
  )

  print("📲 [STOCK BOT] 메신저 송출 시작...")
  send_telegram_message(unified_report)
  send_kakao_message(unified_report)
  send_discord_message(unified_report)
  print("✨ [STOCK BOT] 모든 프로세스 완료!")
