import os
import gc
import csv
import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask, render_template, request, jsonify, Response
import requests
from google import genai
from dotenv import load_dotenv

# Memory monitoring
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CSV file for ticker storage
TICKERS_CSV_FILE = "tickers.csv"

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# SMTP Configuration
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# Comma-separated default recipient list, e.g. "a@example.com,b@example.com"
_raw_default  = os.getenv("DEFAULT_RECIPIENTS", "")
DEFAULT_RECIPIENTS = [e.strip() for e in _raw_default.split(",") if e.strip()]

# Naver Search API
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# Memory optimization: reuse Gemini client
_gemini_client = None

# Batch settings
FETCH_BATCH_SIZE = 30  # Fetch tickers in batches
ANALYSIS_BATCH_SIZE = 3  # Analyze filtered stocks in batches


def log_memory(label=""):
    """Log current memory usage."""
    if HAS_PSUTIL:
        process = psutil.Process()
        mem = process.memory_info()
        logger.info(f"[MEM {label}] RSS: {mem.rss / 1024 / 1024:.1f}MB")


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


# ─── CSV Ticker Management ────────────────────────────────────────────────────

def load_tickers_from_csv():
    """Load tickers from CSV file."""
    tickers = []
    if os.path.exists(TICKERS_CSV_FILE):
        try:
            with open(TICKERS_CSV_FILE, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].strip():
                        tickers.append(row[0].strip().upper())
        except Exception as e:
            logger.error(f"Error reading CSV: {e}")
    return tickers


def save_tickers_to_csv(tickers):
    """Save tickers to CSV file."""
    try:
        with open(TICKERS_CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            for ticker in tickers:
                writer.writerow([ticker])
    except Exception as e:
        logger.error(f"Error writing CSV: {e}")


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", default_recipients=DEFAULT_RECIPIENTS)


@app.route("/api/tickers", methods=["GET"])
def get_tickers():
    tickers = load_tickers_from_csv()
    return jsonify({"tickers": tickers})


@app.route("/api/tickers", methods=["POST"])
def add_ticker():
    data = request.get_json()
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400

    tickers = load_tickers_from_csv()
    if ticker in tickers:
        return jsonify({"error": f"{ticker} is already added"}), 400

    tickers.append(ticker)
    save_tickers_to_csv(tickers)
    return jsonify({"tickers": tickers})


@app.route("/api/tickers/<ticker>", methods=["DELETE"])
def delete_ticker(ticker):
    ticker = ticker.upper()
    tickers = load_tickers_from_csv()
    if ticker in tickers:
        tickers.remove(ticker)
        save_tickers_to_csv(tickers)
    return jsonify({"tickers": tickers})


@app.route("/api/tickers/bulk", methods=["POST"])
def bulk_add_tickers():
    """Add multiple tickers at once (from CSV upload or text input)."""
    data = request.get_json()
    new_tickers = data.get("tickers", [])

    if not new_tickers:
        return jsonify({"error": "No tickers provided"}), 400

    tickers = load_tickers_from_csv()
    added = []
    for t in new_tickers:
        t = t.strip().upper()
        if t and t not in tickers:
            tickers.append(t)
            added.append(t)

    save_tickers_to_csv(tickers)
    return jsonify({"tickers": tickers, "added": added, "added_count": len(added)})


@app.route("/api/tickers/clear", methods=["DELETE"])
def clear_tickers():
    """Clear all tickers."""
    save_tickers_to_csv([])
    return jsonify({"tickers": [], "message": "All tickers cleared"})


@app.route("/api/analyze/stream", methods=["GET"])
def analyze_stream():
    """Streaming analysis - sends results in batches via SSE."""
    tickers = load_tickers_from_csv()
    if not tickers:
        return jsonify({"error": "No tickers saved."}), 400

    def generate():
        log_memory("STREAM START")

        # Phase 1: Fetch all stock data in batches
        all_stocks_slim = {}
        filtered_list = []

        total_batches = (len(tickers) + FETCH_BATCH_SIZE - 1) // FETCH_BATCH_SIZE

        for batch_idx in range(0, len(tickers), FETCH_BATCH_SIZE):
            batch = tickers[batch_idx:batch_idx + FETCH_BATCH_SIZE]
            batch_num = batch_idx // FETCH_BATCH_SIZE + 1

            # Send progress
            yield f"data: {json.dumps({'type': 'progress', 'message': f'주가 수집 중... ({batch_num}/{total_batches})'})}\n\n"

            for ticker in batch:
                result = fetch_single_ticker(ticker)
                all_stocks_slim[ticker] = {
                    "name": result.get("name", ticker),
                    "change_pct": result.get("change_pct", 0),
                }
                if "error" in result:
                    all_stocks_slim[ticker]["error"] = result["error"]

                if abs(result.get("change_pct", 0)) >= 5.0:
                    filtered_list.append((ticker, result))

            gc.collect()

        # Send all_stocks data
        yield f"data: {json.dumps({'type': 'stocks', 'all_stocks': all_stocks_slim})}\n\n"

        log_memory("AFTER FETCH")

        if not filtered_list:
            yield f"data: {json.dumps({'type': 'done', 'message': 'No stocks with +/- 5% change found.'})}\n\n"
            return

        # Sort filtered list
        filtered_list.sort(key=lambda x: abs(x[1].get("change_pct", 0)), reverse=True)

        yield f"data: {json.dumps({'type': 'progress', 'message': f'{len(filtered_list)}개 종목 분석 시작...'})}\n\n"

        # Phase 2: Analyze filtered stocks in batches using Gemini
        total_analysis_batches = (len(filtered_list) + ANALYSIS_BATCH_SIZE - 1) // ANALYSIS_BATCH_SIZE

        for batch_idx in range(0, len(filtered_list), ANALYSIS_BATCH_SIZE):
            batch = filtered_list[batch_idx:batch_idx + ANALYSIS_BATCH_SIZE]
            batch_num = batch_idx // ANALYSIS_BATCH_SIZE + 1

            log_memory(f"ANALYSIS BATCH {batch_num}")

            yield f"data: {json.dumps({'type': 'progress', 'message': f'Gemini 분석 중... ({batch_num}/{total_analysis_batches})'})}\n\n"

            batch_results = []
            for ticker, info in batch:
                try:
                    analysis = analyze_with_gemini(ticker, info)

                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": analysis,
                    })

                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": f"Analysis failed: {str(e)}",
                    })

                gc.collect()

            # Send batch results
            yield f"data: {json.dumps({'type': 'results', 'results': batch_results})}\n\n"

            # Clear batch data
            del batch_results
            gc.collect()

        log_memory("STREAM DONE")
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


# Legacy endpoint (for compatibility)
@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Legacy non-streaming endpoint."""
    return jsonify({"error": "Please use streaming endpoint /api/analyze/stream"}), 400


# ─── Core Functions ───────────────────────────────────────────────────────────

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch_single_ticker(ticker_symbol):
    """Fetch a single ticker's data from Yahoo Finance API."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        params = {"range": "5d", "interval": "1d"}
        resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=10)

        if resp.status_code != 200:
            return {
                "error": f"Yahoo API returned {resp.status_code}",
                "change_pct": 0,
                "name": ticker_symbol,
            }

        data = resp.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        closes = result["indicators"]["quote"][0]["close"]
        timestamps = result["timestamp"]

        valid = [(ts, c) for ts, c in zip(timestamps, closes) if c is not None]

        if len(valid) < 2:
            return {
                "error": f"Insufficient data (rows={len(valid)})",
                "change_pct": 0,
                "name": ticker_symbol,
            }

        prev_close = valid[-2][1]
        last_close = valid[-1][1]
        change_pct = ((last_close - prev_close) / prev_close) * 100
        last_date = datetime.fromtimestamp(valid[-1][0]).date()
        name = meta.get("shortName", meta.get("longName", ticker_symbol))

        return {
            "name": name,
            "change_pct": round(float(change_pct), 2),
            "date": str(last_date),
        }
    except Exception as e:
        return {
            "error": str(e),
            "change_pct": 0,
            "name": ticker_symbol,
        }


def analyze_with_gemini(ticker, stock_info):
    """Use Gemini 2.5 Pro to analyze stock price movement."""
    if not GEMINI_API_KEY:
        return "Gemini API key not configured."

    client = get_gemini_client()
    if not client:
        return "Gemini client initialization failed."

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]
    trade_date = stock_info.get("date", "")

    prompt = f"""You are a stock market analyst. Analyze the following stock's price movement and explain the likely cause.

Stock: {name} ({ticker})
Change: {change_pct:+.1f}%
Date: {trade_date}

Instructions:
1. 출력은 한글로 해라.
2. 한글로 1-2문장으로 간결하게 원인을 분석해라. (명사형 종결)
3. 종목명이나 변동률은 출력하지 마라.
4. 개별 이슈가 없으면: "개별이슈 미발견. 시장 전반적인 흐름에 따른 변동으로 추정."
5. 예시: "AI 반도체 수요 증가에 대한 기대감으로 상승" 또는 "실적 발표 후 가이던스 하향으로 하락"
6. 한글 분석 후 영어로 한 문장 요약 추가.
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"Analysis failed: {str(e)}"


# ─── Global Indices ───────────────────────────────────────────────────────────

GLOBAL_INDICES = {
    "미국": [
        {"symbol": "^DJI",  "name": "다우존스 (DOW)"},
        {"symbol": "^IXIC", "name": "나스닥 (NASDAQ)"},
        {"symbol": "^GSPC", "name": "S&P 500"},
        {"symbol": "^SOX",  "name": "필라델피아 반도체 (SOX)"},
    ],
    "아시아": [
        {"symbol": "^KS11",     "name": "KOSPI"},
        {"symbol": "^KQ11",     "name": "KOSDAQ"},
        {"symbol": "000001.SS", "name": "상해종합 (Shanghai)"},
        {"symbol": "^HSI",      "name": "항셍 (Hang Seng)"},
        {"symbol": "^N225",     "name": "니케이 (Nikkei 225)"},
    ],
    "유럽": [
        {"symbol": "^FTSE",  "name": "영국 FTSE 100"},
        {"symbol": "^FCHI",  "name": "프랑스 CAC 40"},
        {"symbol": "^GDAXI", "name": "독일 DAX"},
    ],
}


def fetch_index_data(symbol, name, target_date=None):
    """Fetch a single index's price data from Yahoo Finance.

    target_date: 'YYYY-MM-DD' string.  When given, find the candle whose
    exchange-local date matches target_date and calculate change vs the
    previous trading day.  When None, use the most recent candle.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        # Fetch enough history to cover a target date + prior trading day.
        # For a specific date we use a 10-day window; otherwise 5d suffices.
        params = {"range": "10d" if target_date else "5d", "interval": "1d"}
        resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=10)

        if resp.status_code != 200:
            return {"symbol": symbol, "name": name, "error": f"HTTP {resp.status_code}"}

        data = resp.json()
        result = data["chart"]["result"][0]
        meta   = result["meta"]
        closes = result["indicators"]["quote"][0]["close"]
        timestamps = result["timestamp"]

        # Resolve exchange timezone
        tz_name = meta.get("exchangeTimezoneName", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = timezone.utc

        # Build list of (local_date_str, ts, close) for valid candles
        valid = []
        for ts, c in zip(timestamps, closes):
            if c is None:
                continue
            local_date = datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")
            valid.append((local_date, ts, c))

        if len(valid) < 2:
            return {"symbol": symbol, "name": name, "error": "데이터 부족"}

        if target_date:
            # Find the index of the candle matching target_date (exchange local)
            target_idx = next(
                (i for i, (d, _, _) in enumerate(valid) if d == target_date), None
            )
            if target_idx is None:
                return {"symbol": symbol, "name": name,
                        "error": f"현지 {target_date} 거래 데이터 없음 (휴장일 가능성)"}
            if target_idx == 0:
                return {"symbol": symbol, "name": name,
                        "error": f"현지 {target_date} 이전 거래일 데이터 부족"}
            last_date, _, last_close   = valid[target_idx]
            _,         _, prev_close   = valid[target_idx - 1]
        else:
            last_date, _, last_close = valid[-1]
            _,         _, prev_close = valid[-2]

        change = last_close - prev_close
        change_pct = (change / prev_close) * 100

        return {
            "symbol": symbol,
            "name": name,
            "value": round(float(last_close), 2),
            "change": round(float(change), 2),
            "change_pct": round(float(change_pct), 2),
            "date": last_date,
            "timezone": tz_name,
        }
    except Exception as e:
        return {"symbol": symbol, "name": name, "error": str(e)}


NAVER_NEWS_QUERIES = {
    "미국": ["미국증시", "뉴욕증시", "나스닥", "다우존스"],
    "아시아": ["코스피", "코스닥", "일본증시", "중국증시"],
    "유럽": ["유럽증시", "영국증시"],
}


def fetch_naver_news(region, display=5):
    """Fetch recent news headlines from Naver Search API for a given region."""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []

    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    headlines = []
    queries = NAVER_NEWS_QUERIES.get(region, [])

    for query in queries[:2]:  # 쿼리 2개씩, 각 display개
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers=headers,
                params={"query": query, "display": display, "sort": "date"},
                timeout=5,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                for item in items:
                    title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                    headlines.append(title)
        except Exception as e:
            logger.warning(f"Naver news fetch failed ({query}): {e}")

    return headlines


def get_indices_news_summary(indices_data):
    """Use Gemini to generate news & market analysis for global indices."""
    if not GEMINI_API_KEY:
        return "Gemini API key가 설정되지 않았습니다."

    client = get_gemini_client()
    if not client:
        return "Gemini 클라이언트 초기화 실패."

    context_lines = []
    # Collect representative local date per region (from first valid index)
    region_dates = {}
    for region, indices in indices_data.items():
        context_lines.append(f"\n[{region}]")
        for idx in indices:
            if "error" not in idx:
                sign = "+" if idx["change_pct"] >= 0 else ""
                context_lines.append(
                    f"  - {idx['name']}: {idx['value']:,.2f} ({sign}{idx['change_pct']:.2f}%) [{idx['date']} 기준]"
                )
                if region not in region_dates:
                    region_dates[region] = idx["date"]
            else:
                context_lines.append(f"  - {idx['name']}: 데이터 오류")

    context = "\n".join(context_lines)
    date_info = " | ".join(f"{r}: {d}" for r, d in region_dates.items())
    today = date_info if date_info else datetime.now().strftime("%Y-%m-%d")

    # Fetch real headlines from Naver News API per region
    news_lines = []
    for region in indices_data:
        headlines = fetch_naver_news(region)
        if headlines:
            news_lines.append(f"\n[{region} 실시간 뉴스 헤드라인]")
            for h in headlines:
                news_lines.append(f"  - {h}")
    news_context = "\n".join(news_lines) if news_lines else "  (네이버 뉴스 API 미설정)"

    prompt = f"""당신은 글로벌 금융 시장 전문 애널리스트입니다.
각 지역별 현지 기준 날짜: {today}

아래는 주요 글로벌 주가 지수 현황입니다 (각 수치는 해당 거래소의 현지 날짜 기준):
{context}

아래는 네이버에서 수집한 각 지역 실시간 뉴스 헤드라인입니다. 분석 시 반드시 참고하세요:
{news_context}

위 지수 데이터와 실제 뉴스 헤드라인을 바탕으로 각 지역별 주요 이슈를 한글로 정리해주세요.

출력 형식 (마크다운):
## 미국 시장
- 핵심 이슈 2-3가지 (뉴스 헤드라인 근거 포함)

## 아시아 시장
- 핵심 이슈 2-3가지

## 유럽 시장
- 핵심 이슈 2-3가지

## 종합 시장 분위기
1-2문장으로 전체 시장 분위기 요약.

각 이슈는 실제 뉴스 근거를 포함하고 간결하게 작성하세요.
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"뉴스 분석 실패: {str(e)}"


@app.route("/api/indices/stream", methods=["GET"])
def indices_stream():
    """SSE endpoint: fetch global index prices then Gemini news summary."""
    target_date = request.args.get("date") or None  # 'YYYY-MM-DD' or None

    def generate():
        label = f"{target_date} 현지 기준 " if target_date else ""
        yield f"data: {json.dumps({'type': 'progress', 'message': f'{label}글로벌 지수 데이터 수집 중...'})}\n\n"

        all_indices_data = {}
        for region, indices in GLOBAL_INDICES.items():
            region_data = []
            for idx in indices:
                result = fetch_index_data(idx["symbol"], idx["name"], target_date)
                region_data.append(result)
            all_indices_data[region] = region_data

        yield f"data: {json.dumps({'type': 'indices', 'data': all_indices_data})}\n\n"

        yield f"data: {json.dumps({'type': 'progress', 'message': 'Gemini로 뉴스 & 시장 분석 중...'})}\n\n"

        news_summary = get_indices_news_summary(all_indices_data)
        yield f"data: {json.dumps({'type': 'news', 'summary': news_summary})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


# ─── Email ────────────────────────────────────────────────────────────────────

def build_email_html(indices_data, news_summary, report_date):
    """Build HTML email body from indices data and news summary."""

    def change_color(pct):
        if pct > 0:
            return "#22c55e"
        if pct < 0:
            return "#ef4444"
        return "#94a3b8"

    region_icons = {"미국": "🇺🇸", "아시아": "🌏", "유럽": "🇪🇺"}

    rows_html = ""
    for region, indices in indices_data.items():
        icon = region_icons.get(region, "")
        rows_html += f"""
        <tr><td colspan="3" style="padding:12px 16px 6px;font-size:0.8rem;font-weight:700;
            color:#94a3b8;background:#0f1923;border-bottom:1px solid #2a3a4a;">
            {icon} {region}
        </td></tr>"""
        for idx in indices:
            if "error" in idx:
                rows_html += f"""
                <tr><td style="padding:8px 16px;color:#94a3b8;">{idx['name']}</td>
                <td colspan="2" style="color:#ef4444;font-size:0.8rem;">데이터 오류</td></tr>"""
                continue
            sign = "+" if idx["change_pct"] >= 0 else ""
            color = change_color(idx["change_pct"])
            val = f"{idx['value']:,.2f}"
            rows_html += f"""
            <tr style="border-bottom:1px solid #1e2f3f;">
                <td style="padding:8px 16px;color:#e0e6ed;font-size:0.88rem;">{idx['name']}</td>
                <td style="padding:8px 16px;color:#e0e6ed;font-weight:700;font-size:0.88rem;
                    text-align:right;">{val}</td>
                <td style="padding:8px 16px;color:{color};font-weight:700;font-size:0.88rem;
                    text-align:right;">{sign}{idx['change_pct']:.2f}%</td>
            </tr>"""

    # Convert markdown news to simple HTML paragraphs
    news_html = ""
    for line in news_summary.split("\n"):
        t = line.strip()
        if not t:
            continue
        if t.startswith("## "):
            news_html += f'<h3 style="color:#818cf8;font-size:0.95rem;margin:16px 0 6px;padding-left:10px;border-left:3px solid #6366f1;">{t[3:]}</h3>'
        elif t.startswith("- "):
            news_html += f'<p style="color:#94a3b8;font-size:0.88rem;margin:4px 0 4px 14px;">• {t[2:]}</p>'
        else:
            news_html += f'<p style="color:#b0c4de;font-size:0.88rem;margin:6px 0;">{t}</p>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0f1923;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:640px;margin:32px auto;background:#1a2634;border:1px solid #2a3a4a;border-radius:12px;overflow:hidden;">
    <div style="padding:24px 28px;background:#0f1923;border-bottom:1px solid #2a3a4a;">
      <h1 style="margin:0;color:#fff;font-size:1.2rem;">글로벌 주요 지수 리포트</h1>
      <p style="margin:4px 0 0;color:#5a6a7a;font-size:0.82rem;">{report_date} 기준</p>
    </div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
      <thead>
        <tr style="background:#253547;">
          <th style="padding:8px 16px;text-align:left;color:#7a8a9e;font-size:0.78rem;font-weight:600;">지수</th>
          <th style="padding:8px 16px;text-align:right;color:#7a8a9e;font-size:0.78rem;font-weight:600;">현재가</th>
          <th style="padding:8px 16px;text-align:right;color:#7a8a9e;font-size:0.78rem;font-weight:600;">등락률</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div style="padding:20px 28px;border-top:1px solid #2a3a4a;">
      <h2 style="color:#c8d6e5;font-size:0.95rem;margin:0 0 12px;">뉴스 &amp; 시장 분석
        <span style="font-size:0.7rem;background:rgba(99,102,241,0.2);color:#818cf8;
          padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:500;">Gemini 2.5 Pro</span>
      </h2>
      {news_html}
    </div>
    <div style="padding:14px 28px;background:#0f1923;border-top:1px solid #2a3a4a;
        text-align:center;color:#3a4a5a;font-size:0.75rem;">
      Stock Movement Analyzer · 자동 발송 리포트
    </div>
  </div>
</body></html>"""


@app.route("/api/send-email", methods=["POST"])
def send_email():
    """Send indices report email."""
    if not SMTP_USER or not SMTP_PASSWORD:
        return jsonify({"error": "SMTP 설정이 없습니다. 환경변수(SMTP_USER, SMTP_PASSWORD)를 확인하세요."}), 400

    data = request.get_json()
    to_emails    = data.get("to_emails", [])
    indices_data = data.get("indices_data", {})
    news_summary = data.get("news_summary", "")
    report_date  = data.get("report_date", datetime.now().strftime("%Y-%m-%d"))

    if not to_emails:
        return jsonify({"error": "수신자를 추가해주세요."}), 400

    try:
        html_body = build_email_html(indices_data, news_summary, report_date)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[주식 리포트] 글로벌 주요 지수 현황 ({report_date})"
        msg["From"]    = SMTP_USER
        msg["To"]      = ", ".join(to_emails)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_emails, msg.as_string())

        count = len(to_emails)
        label = to_emails[0] if count == 1 else f"{to_emails[0]} 외 {count - 1}명"
        logger.info(f"Email sent to {to_emails}")
        return jsonify({"message": f"{label}에게 발송 완료!"})

    except smtplib.SMTPAuthenticationError:
        return jsonify({"error": "SMTP 인증 실패. 계정/비밀번호를 확인하세요."}), 500
    except smtplib.SMTPException as e:
        return jsonify({"error": f"SMTP 오류: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return jsonify({"error": f"발송 실패: {str(e)}"}), 500


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
