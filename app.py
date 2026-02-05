import os
import gc
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

# Memory optimization: reuse Gemini client
_gemini_client = None


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

    # Step 1: Fetch previous day's stock price changes
    stock_data = fetch_stock_changes(saved_tickers)

    # Immediately slim stock_data for response
    all_stocks_slim = _slim_stock_data(stock_data)

    # Step 2: Filter stocks with >= +/-5% change
    filtered_tickers = [
        (ticker, info)
        for ticker, info in stock_data.items()
        if abs(info.get("change_pct", 0)) >= 5.0
    ]

    # Free up memory - only keep filtered data
    del stock_data
    gc.collect()

    if not filtered_tickers:
        return jsonify({
            "results": [],
            "all_stocks": all_stocks_slim,
            "message": "No stocks with +/- 5% or more change found."
        })

    # Step 3 & 4: Process filtered stocks one by one to minimize memory
    results = []
    for ticker, info in filtered_tickers:
        # Fetch articles (limited to 10)
        articles = search_news(ticker, info.get("name", ticker))

        # Analyze with Gemini
        analysis = analyze_with_gemini(ticker, info, articles)

        # Keep only top 2 unique articles for display
        top_articles = _get_top_articles(articles, limit=2)

        results.append({
            "ticker": ticker,
            "name": info.get("name", ticker),
            "change_pct": info["change_pct"],
            "analysis": analysis,
            "articles": top_articles,
            "article_count": len(articles),
        })

        # Clear article data immediately after processing
        del articles
        gc.collect()

    # Sort by absolute change descending
    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    return jsonify({
        "results": results,
        "all_stocks": all_stocks_slim,
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


def _get_top_articles(articles, limit=2):
    """Get top unique articles, removing duplicates by similar titles."""
    if not articles:
        return []

    seen_titles = set()
    unique = []

    for a in articles:
        # Normalize title for comparison
        title_key = a["title"].lower()[:50]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique.append({
                "title": a["title"],
                "link": a["link"],
                "date": a.get("date", ""),
            })
            if len(unique) >= limit:
                break

    return unique


# ─── Core Functions ───────────────────────────────────────────────────────────

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def _fetch_single_ticker(ticker_symbol):
    """Fetch a single ticker's data from Yahoo Finance API."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        params = {"range": "5d", "interval": "1d"}
        resp = requests.get(
            url, params=params, headers=YAHOO_HEADERS, timeout=10
        )

        if resp.status_code != 200:
            return ticker_symbol, {
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
            return ticker_symbol, {
                "error": f"Insufficient data (rows={len(valid)})",
                "change_pct": 0,
                "name": ticker_symbol,
            }

        prev_close = valid[-2][1]
        last_close = valid[-1][1]
        change_pct = ((last_close - prev_close) / prev_close) * 100
        last_date = datetime.fromtimestamp(valid[-1][0]).date()

        name = meta.get("shortName", meta.get("longName", ticker_symbol))

        return ticker_symbol, {
            "name": name,
            "change_pct": round(float(change_pct), 2),
            "date": str(last_date),
        }
    except Exception as e:
        return ticker_symbol, {
            "error": str(e),
            "change_pct": 0,
            "name": ticker_symbol,
        }


def fetch_stock_changes(tickers):
    """Fetch stock data with reduced parallelism for memory efficiency."""
    stock_data = {}
    # Reduced workers: 5 instead of 10
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_single_ticker, t): t for t in tickers}
        for future in as_completed(futures):
            ticker_symbol, result = future.result()
            stock_data[ticker_symbol] = result
    return stock_data


def search_news(ticker, company_name):
    """Search Google for recent news - limited to 10 articles."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    today = datetime.now()
    yesterday = today - timedelta(days=1)
    if yesterday.weekday() == 6:  # Sunday
        yesterday = yesterday - timedelta(days=2)
    elif yesterday.weekday() == 5:  # Saturday
        yesterday = yesterday - timedelta(days=1)

    date_str = yesterday.strftime("%Y-%m-%d")
    query = f"{company_name} Stock Price after:{date_str}"

    articles = []
    try:
        # Only 1 request (10 articles) instead of 2 (20 articles)
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
                    or metatags.get("datePublished", "")
                    or ""
                )
                if pub_date and "T" in pub_date:
                    pub_date = pub_date.split("T")[0]

                articles.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                    "date": pub_date,
                })
    except Exception:
        pass

    return articles[:10]


def analyze_with_gemini(ticker, stock_info, articles):
    """Use Gemini API to analyze articles."""
    if not GEMINI_API_KEY:
        return "Gemini API key not configured."

    if not articles:
        return "No news articles found for analysis."

    client = get_gemini_client()
    if not client:
        return "Gemini client initialization failed."

    # Build compact prompt with limited articles
    article_texts = []
    for i, article in enumerate(articles[:10], 1):
        article_texts.append(f"{i}. {article['title']}: {article['snippet'][:100]}")
    articles_block = "\n".join(article_texts)

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]

    prompt = f"""출력은 한글로해라. Based on the provided news, analyze the cause of the stock price fluctuation.

Stock: {name} ({ticker}), Change: {change_pct:+.1f}%

News:
{articles_block}

1. Summarize the analysis in one sentence(noun ending) within 3 sentences.
2. Do not include stock names or rate of change.
3. Unless there are any issues, write it as follows: 개별이슈 미발견.
4. Example: 블랙웰 수요 증가로 TSMC에 생산주문을 확대했다는 소식으로 AI 관련주 전반 상승
5. After Korean, add newline and one English sentence summary.
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
