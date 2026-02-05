import os
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
import yfinance as yf
import requests
from google import genai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

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
            "all_stocks": stock_data,
            "message": "No stocks with +/- 5% or more change found."
        })

    # Step 3 & 4: For filtered stocks, search news and analyze with Gemini
    results = []
    for ticker, info in filtered.items():
        articles = search_news(ticker, info.get("name", ticker))
        analysis = analyze_with_gemini(ticker, info, articles)
        results.append({
            "ticker": ticker,
            "name": info.get("name", ticker),
            "change_pct": info["change_pct"],
            "prev_close": info.get("prev_close", 0),
            "close": info.get("close", 0),
            "analysis": analysis,
            "article_count": len(articles),
        })

    # Sort by absolute change descending
    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    return jsonify({
        "results": results,
        "all_stocks": stock_data,
    })


# ─── Core Functions ───────────────────────────────────────────────────────────

def fetch_stock_changes(tickers):
    """Fetch previous trading day's price change for each ticker."""
    stock_data = {}

    for ticker_symbol in tickers:
        try:
            ticker_obj = yf.Ticker(ticker_symbol)
            # Get last 5 days to ensure we have enough data
            hist = ticker_obj.history(period="5d")

            if hist.empty or len(hist) < 2:
                stock_data[ticker_symbol] = {
                    "error": "Insufficient data",
                    "change_pct": 0,
                    "name": ticker_symbol,
                }
                continue

            # Previous trading day = last row, day before = second to last
            prev_close = hist["Close"].iloc[-2]
            last_close = hist["Close"].iloc[-1]
            change_pct = ((last_close - prev_close) / prev_close) * 100

            # Try to get company name
            info = ticker_obj.info
            name = info.get("shortName", info.get("longName", ticker_symbol))

            stock_data[ticker_symbol] = {
                "name": name,
                "prev_close": round(float(prev_close), 2),
                "close": round(float(last_close), 2),
                "change_pct": round(float(change_pct), 2),
                "date": str(hist.index[-1].date()),
            }
        except Exception as e:
            stock_data[ticker_symbol] = {
                "error": str(e),
                "change_pct": 0,
                "name": ticker_symbol,
            }

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

    query = f"{company_name} ({ticker}) stock news {date_str}"

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
                    articles.append({
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                        "link": item.get("link", ""),
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

    prompt = f"""You are a financial analyst. Analyze the following news articles about {name} ({ticker})
and explain why the stock price {direction} by {abs(change_pct):.1f}% on the previous trading day.

Stock Information:
- Company: {name} ({ticker})
- Previous Close: {stock_info.get('prev_close', 'N/A')}
- Last Close: {stock_info.get('close', 'N/A')}
- Change: {change_pct:+.1f}%

News Articles:
{articles_block}

Instructions:
1. Identify the key factors that caused the stock price movement.
2. Summarize the analysis in 2-3 concise paragraphs in Korean.
3. Focus on the most impactful events/news that directly influenced the stock price.
4. Be specific about the cause-and-effect relationship.
5. Do NOT include the stock name or change percentage in your response - those will be displayed separately.
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
    app.run(debug=True, host="0.0.0.0", port=5000)
