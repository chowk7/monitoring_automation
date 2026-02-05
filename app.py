import os
import json
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify
import requests
from google import genai
from dotenv import load_dotenv

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
    """Main analysis pipeline."""
    if not saved_tickers:
        return jsonify({"error": "No tickers saved. Please add tickers first."}), 400

    # Step 1: Fetch previous day's stock price changes
    stock_data = fetch_stock_changes(saved_tickers)

    # Step 2: Filter stocks with >= +/-5% change
    filtered = {
        ticker: info
        for ticker, info in stock_data.items()
        if abs(info["change_pct"]) >= 5.0
    }

    if not filtered:
        return jsonify({
            "results": [],
            "all_stocks": _slim_stock_data(stock_data),
            "message": "No stocks with +/- 5% or more change found."
        })

    # Step 3 & 4: For filtered stocks, search news and analyze with Gemini
    results = []
    for ticker, info in filtered.items():
        articles = search_news(ticker, info.get("name", ticker))
        analysis = analyze_with_gemini(ticker, info, articles)
        # Strip snippet from articles to reduce response size
        articles_slim = [
            {"title": a["title"], "link": a["link"], "date": a.get("date", "")}
            for a in articles
        ]
        results.append({
            "ticker": ticker,
            "name": info.get("name", ticker),
            "change_pct": info["change_pct"],
            "prev_close": info.get("prev_close", 0),
            "close": info.get("close", 0),
            "analysis": analysis,
            "articles": articles_slim,
            "article_count": len(articles),
        })

    # Sort by absolute change descending
    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    return jsonify({
        "results": results,
        "all_stocks": _slim_stock_data(stock_data),
    })


def _slim_stock_data(stock_data):
    """Return only fields needed by the frontend overview."""
    slim = {}
    for ticker, info in stock_data.items():
        slim[ticker] = {
            "name": info.get("name", ticker),
            "change_pct": info.get("change_pct", 0),
        }
        if "error" in info:
            slim[ticker]["error"] = info["error"]
    return slim


# ─── Core Functions ───────────────────────────────────────────────────────────

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _fetch_single_ticker(ticker_symbol):
    """Fetch a single ticker's data from Yahoo Finance API."""
    try:
        logger.info(f"Fetching data for {ticker_symbol}...")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        params = {"range": "5d", "interval": "1d"}
        resp = requests.get(
            url, params=params, headers=YAHOO_HEADERS, timeout=10
        )

        if resp.status_code != 200:
            msg = f"Yahoo API returned {resp.status_code}"
            logger.warning(f"{ticker_symbol}: {msg}")
            return ticker_symbol, {
                "error": msg,
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
            msg = f"Insufficient data (rows={len(valid)})"
            logger.warning(f"{ticker_symbol}: {msg}")
            return ticker_symbol, {
                "error": msg,
                "change_pct": 0,
                "name": ticker_symbol,
            }

        prev_close = valid[-2][1]
        last_close = valid[-1][1]
        change_pct = ((last_close - prev_close) / prev_close) * 100
        last_date = datetime.fromtimestamp(valid[-1][0]).date()

        name = meta.get("shortName", meta.get("longName", ticker_symbol))

        logger.info(f"{ticker_symbol} ({name}): {change_pct:+.2f}%")
        return ticker_symbol, {
            "name": name,
            "prev_close": round(float(prev_close), 2),
            "close": round(float(last_close), 2),
            "change_pct": round(float(change_pct), 2),
            "date": str(last_date),
        }
    except Exception as e:
        logger.error(f"{ticker_symbol} failed: {e}", exc_info=True)
        return ticker_symbol, {
            "error": str(e),
            "change_pct": 0,
            "name": ticker_symbol,
        }


def fetch_stock_changes(tickers):
    """Fetch previous trading day's price change for each ticker via Yahoo Finance API (parallel)."""
    stock_data = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_single_ticker, t): t for t in tickers}
        for future in as_completed(futures):
            ticker_symbol, result = future.result()
            stock_data[ticker_symbol] = result
    return stock_data


def search_news(ticker, company_name):
    """Search Google for recent news articles about the stock."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    # Calculate date range (previous trading day)
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    # For weekends, go back further
    if yesterday.weekday() == 6:  # Sunday
        yesterday = yesterday - timedelta(days=2)
    elif yesterday.weekday() == 5:  # Saturday
        yesterday = yesterday - timedelta(days=1)

    date_str = yesterday.strftime("%Y-%m-%d")

    query = f"{company_name} Stock Price after:{date_str}"

    articles = []
    # Google Custom Search API returns max 10 results per request
    for start_index in [1, 11]:
        try:
            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CSE_ID,
                "q": query,
                "num": 10,
                "start": start_index,
                "dateRestrict": "d1",  # Last 1 day
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
                    # Extract date from pagemap or snippet metadata
                    pub_date = ""
                    metatags = (
                        item.get("pagemap", {})
                        .get("metatags", [{}])[0]
                    )
                    pub_date = (
                        metatags.get("article:published_time", "")
                        or metatags.get("og:updated_time", "")
                        or metatags.get("datePublished", "")
                        or item.get("snippet", "")[:10]
                    )
                    # Trim to date portion if ISO format
                    if pub_date and "T" in pub_date:
                        pub_date = pub_date.split("T")[0]

                    articles.append({
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                        "link": item.get("link", ""),
                        "date": pub_date,
                    })
        except Exception:
            continue

    return articles[:20]


def analyze_with_gemini(ticker, stock_info, articles):
    """Use Gemini API to analyze articles and summarize the cause of price movement."""
    if not GEMINI_API_KEY:
        return "Gemini API key not configured. Please set GEMINI_API_KEY in .env file."

    if not articles:
        return "No news articles found for analysis."

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Build article summaries for the prompt
    article_texts = []
    for i, article in enumerate(articles, 1):
        article_texts.append(
            f"{i}. Title: {article['title']}\n   Summary: {article['snippet']}\n   URL: {article['link']}"
        )
    articles_block = "\n\n".join(article_texts)

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]
    direction = "rose" if change_pct > 0 else "fell"

    prompt = f"""출력은 한글로해라. Based on the provided news, analyze the cause of the stock price fluctuation.

Stock Information:
- Company: {name} ({ticker})
- Previous Close: {stock_info.get('prev_close', 'N/A')}
- Last Close: {stock_info.get('close', 'N/A')}
- Change: {change_pct:+.1f}%

News Articles:
{articles_block}

1. Summarize the analysis in one sentence(noun ending) within 3 sentences.
2. Do not include stock names or rate of change.
3. Unless there are any issues, write it as follows: 개별이슈 미발견.
4. Example output: 블랙웰 수요 증가로 TSMC에 생산주문을 확대했다는 소식으로 AI 관련주 전반 상승
5. After the Korean summary, add a newline and write an English summary in one sentence.
6. English example: Increased Blackwell demand and expanded production orders to TSMC boosted AI-related stocks.
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
