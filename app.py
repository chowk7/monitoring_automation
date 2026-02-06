import os
import gc
import json
import logging
import re
from datetime import datetime, timedelta
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

# In-memory ticker storage
saved_tickers = []

# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

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


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tickers", methods=["GET"])
def get_tickers():
    return jsonify({"tickers": saved_tickers})


@app.route("/api/tickers", methods=["POST"])
def add_ticker():
    data = request.get_json()
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400
    if ticker in saved_tickers:
        return jsonify({"error": f"{ticker} is already added"}), 400
    saved_tickers.append(ticker)
    return jsonify({"tickers": saved_tickers})


@app.route("/api/tickers/<ticker>", methods=["DELETE"])
def delete_ticker(ticker):
    ticker = ticker.upper()
    if ticker in saved_tickers:
        saved_tickers.remove(ticker)
    return jsonify({"tickers": saved_tickers})


@app.route("/api/analyze/stream", methods=["GET"])
def analyze_stream():
    """Streaming analysis - sends results in batches via SSE."""
    if not saved_tickers:
        return jsonify({"error": "No tickers saved."}), 400

    def generate():
        log_memory("STREAM START")

        # Phase 1: Fetch all stock data in batches
        all_stocks_slim = {}
        filtered_list = []

        total_batches = (len(saved_tickers) + FETCH_BATCH_SIZE - 1) // FETCH_BATCH_SIZE

        for batch_idx in range(0, len(saved_tickers), FETCH_BATCH_SIZE):
            batch = saved_tickers[batch_idx:batch_idx + FETCH_BATCH_SIZE]
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

        # Phase 2: Analyze filtered stocks in batches
        total_analysis_batches = (len(filtered_list) + ANALYSIS_BATCH_SIZE - 1) // ANALYSIS_BATCH_SIZE
        is_first_ticker = True

        for batch_idx in range(0, len(filtered_list), ANALYSIS_BATCH_SIZE):
            batch = filtered_list[batch_idx:batch_idx + ANALYSIS_BATCH_SIZE]
            batch_num = batch_idx // ANALYSIS_BATCH_SIZE + 1

            log_memory(f"ANALYSIS BATCH {batch_num}")

            yield f"data: {json.dumps({'type': 'progress', 'message': f'분석 중... ({batch_num}/{total_analysis_batches})'})}\n\n"

            batch_results = []
            for ticker, info in batch:
                try:
                    articles = search_news(ticker, info.get("name", ticker))

                    # Log all article URLs for the first ticker only
                    if is_first_ticker and articles:
                        logger.info(f"=== First ticker [{ticker}] article URLs ===")
                        for i, a in enumerate(articles, 1):
                            logger.info(f"  {i}. {a['link']}")
                        is_first_ticker = False

                    result = analyze_with_gemini(ticker, info, articles)
                    analysis = result["analysis"]
                    used_indices = result["used_indices"]

                    # Get articles that Gemini actually used
                    selected_articles = []
                    for idx in used_indices[:2]:
                        if 0 <= idx < len(articles):
                            a = articles[idx]
                            selected_articles.append({
                                "title": a["title"],
                                "link": a["link"],
                                "date": a.get("date", ""),
                            })

                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": analysis,
                        "articles": selected_articles,
                        "article_count": len(articles),
                    })

                    del articles
                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": f"Analysis failed: {str(e)}",
                        "articles": [],
                        "article_count": 0,
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


def search_news(ticker, company_name):
    """Search Google for news - fetches 20 articles (2 API calls)."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    today = datetime.now()
    yesterday = today - timedelta(days=1)
    if yesterday.weekday() == 6:
        yesterday = yesterday - timedelta(days=2)
    elif yesterday.weekday() == 5:
        yesterday = yesterday - timedelta(days=1)

    date_str = yesterday.strftime("%Y-%m-%d")
    query = f"news {company_name} stock after:{date_str} -quote"

    articles = []
    # Fetch 20 articles (2 requests of 10 each)
    for start_idx in [1, 11]:
        try:
            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CSE_ID,
                "q": query,
                "num": 10,
                "start": start_idx,
                "dateRestrict": "d1",
                "sort": "date",
            }
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    metatags = item.get("pagemap", {}).get("metatags", [{}])[0]
                    pub_date = (
                        metatags.get("article:published_time", "")
                        or metatags.get("og:updated_time", "")
                        or ""
                    )
                    if pub_date and "T" in pub_date:
                        pub_date = pub_date.split("T")[0]

                    articles.append({
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", "")[:100],
                        "link": item.get("link", ""),
                        "date": pub_date,
                    })
        except Exception:
            pass

    return articles[:20]


def analyze_with_gemini(ticker, stock_info, articles):
    """Use Gemini API to analyze articles. Returns dict with analysis and used article indices."""
    if not GEMINI_API_KEY:
        return {"analysis": "Gemini API key not configured.", "used_indices": []}

    if not articles:
        return {"analysis": "No news articles found for analysis.", "used_indices": []}

    client = get_gemini_client()
    if not client:
        return {"analysis": "Gemini client initialization failed.", "used_indices": []}

    # Use all 20 articles
    article_texts = [f"{i}. {a['title']}: {a.get('snippet', '')[:100]}" for i, a in enumerate(articles[:20], 1)]
    articles_block = "\n".join(article_texts)

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]

    prompt = f"""출력은 한글로해라. Based on the news, analyze stock price fluctuation cause.

Stock: {name} ({ticker}), Change: {change_pct:+.1f}%

News:
{articles_block}

Instructions:
1. One sentence summary (noun ending), max 3 sentences.
2. No stock names or rate of change.
3. If no issues: 개별이슈 미발견.
4. Example: 블랙웰 수요 증가로 AI 관련주 전반 상승
5. Add English one sentence after Korean.
6. At the end, add a line: USED_ARTICLES: X, Y (where X and Y are the article numbers you actually used for analysis, pick the 2 most relevant ones)
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        response_text = response.text

        # Parse used article indices from response
        used_indices = []
        analysis = response_text

        if "USED_ARTICLES:" in response_text:
            parts = response_text.split("USED_ARTICLES:")
            analysis = parts[0].strip()
            if len(parts) > 1:
                indices_str = parts[1].strip()
                # Extract numbers from the indices string
                numbers = re.findall(r'\d+', indices_str)
                used_indices = [int(n) - 1 for n in numbers[:2] if int(n) <= len(articles)]

        return {"analysis": analysis, "used_indices": used_indices}
    except Exception as e:
        return {"analysis": f"Analysis failed: {str(e)}", "used_indices": []}


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
