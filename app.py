import os
import gc
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
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

# Memory optimization settings
BATCH_SIZE = 20  # Fetch tickers in batches of 20


def log_memory(label=""):
    """Log current memory usage."""
    if HAS_PSUTIL:
        process = psutil.Process()
        mem = process.memory_info()
        logger.info(f"[MEM {label}] RSS: {mem.rss / 1024 / 1024:.1f}MB, VMS: {mem.vms / 1024 / 1024:.1f}MB")


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


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Main analysis pipeline - memory optimized for 512MB."""
    if not saved_tickers:
        return jsonify({"error": "No tickers saved. Please add tickers first."}), 400

    log_memory("START")

    # Step 1: Fetch stock data in batches (sequential, not parallel)
    all_stocks_slim = {}
    filtered_list = []

    for i in range(0, len(saved_tickers), BATCH_SIZE):
        batch = saved_tickers[i:i + BATCH_SIZE]
        log_memory(f"BATCH {i // BATCH_SIZE + 1}")

        for ticker in batch:
            result = fetch_single_ticker(ticker)
            # Immediately slim for response
            all_stocks_slim[ticker] = {
                "name": result.get("name", ticker),
                "change_pct": result.get("change_pct", 0),
            }
            if "error" in result:
                all_stocks_slim[ticker]["error"] = result["error"]

            # Check if filtered
            if abs(result.get("change_pct", 0)) >= 5.0:
                filtered_list.append((ticker, result))

        gc.collect()

    log_memory("AFTER FETCH")

    if not filtered_list:
        return jsonify({
            "results": [],
            "all_stocks": all_stocks_slim,
            "message": "No stocks with +/- 5% or more change found."
        })

    # Sort by absolute change
    filtered_list.sort(key=lambda x: abs(x[1].get("change_pct", 0)), reverse=True)

    logger.info(f"Processing {len(filtered_list)} filtered stocks")

    # Step 2: Process filtered stocks ONE BY ONE
    results = []
    for idx, (ticker, info) in enumerate(filtered_list):
        log_memory(f"STOCK {idx + 1}/{len(filtered_list)}")

        try:
            # Fetch articles
            articles = search_news(ticker, info.get("name", ticker))

            # Analyze
            analysis = analyze_with_gemini(ticker, info, articles)

            # Keep only top 2 unique articles
            top_articles = get_top_articles(articles, limit=2)

            results.append({
                "ticker": ticker,
                "name": info.get("name", ticker),
                "change_pct": info["change_pct"],
                "analysis": analysis,
                "articles": top_articles,
                "article_count": len(articles),
            })

            # Aggressive cleanup
            del articles
            del analysis
        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}")
            results.append({
                "ticker": ticker,
                "name": info.get("name", ticker),
                "change_pct": info["change_pct"],
                "analysis": f"Analysis failed: {str(e)}",
                "articles": [],
                "article_count": 0,
            })

        gc.collect()

    log_memory("DONE")

    return jsonify({
        "results": results,
        "all_stocks": all_stocks_slim,
    })


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
    """Search Google for news - limited to 10 articles."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    today = datetime.now()
    yesterday = today - timedelta(days=1)
    if yesterday.weekday() == 6:
        yesterday = yesterday - timedelta(days=2)
    elif yesterday.weekday() == 5:
        yesterday = yesterday - timedelta(days=1)

    date_str = yesterday.strftime("%Y-%m-%d")
    query = f"{company_name} Stock Price after:{date_str}"

    articles = []
    try:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "num": 10,
            "start": 1,
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
                    "snippet": item.get("snippet", "")[:100],  # Limit snippet
                    "link": item.get("link", ""),
                    "date": pub_date,
                })
    except Exception:
        pass

    return articles[:10]


def get_top_articles(articles, limit=2):
    """Get top unique articles by title."""
    if not articles:
        return []

    seen = set()
    unique = []
    for a in articles:
        key = a["title"].lower()[:40]
        if key not in seen:
            seen.add(key)
            unique.append({
                "title": a["title"],
                "link": a["link"],
                "date": a.get("date", ""),
            })
            if len(unique) >= limit:
                break
    return unique


def analyze_with_gemini(ticker, stock_info, articles):
    """Use Gemini API to analyze articles."""
    if not GEMINI_API_KEY:
        return "Gemini API key not configured."

    if not articles:
        return "No news articles found for analysis."

    client = get_gemini_client()
    if not client:
        return "Gemini client initialization failed."

    # Compact prompt with title + snippet (100 chars)
    article_texts = [f"{i}. {a['title']}: {a.get('snippet', '')[:100]}" for i, a in enumerate(articles[:5], 1)]
    articles_block = "\n".join(article_texts)

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]

    prompt = f"""출력은 한글로해라. Based on the news, analyze stock price fluctuation cause.

Stock: {name} ({ticker}), Change: {change_pct:+.1f}%

News:
{articles_block}

1. One sentence summary (noun ending), max 3 sentences.
2. No stock names or rate of change.
3. If no issues: 개별이슈 미발견.
4. Example: 블랙웰 수요 증가로 AI 관련주 전반 상승
5. Add English one sentence after Korean.
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"Analysis failed: {str(e)}"


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
