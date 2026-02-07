import os
import gc
import csv
import json
import logging
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

# CSV file for ticker storage
TICKERS_CSV_FILE = "tickers.csv"

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Memory optimization: reuse Gemini client
_gemini_client = None

# Batch settings
FETCH_BATCH_SIZE = 30  # Fetch tickers in batches
ANALYSIS_BATCH_SIZE = 3  # Analyze filtered stocks in batches

# ─── Default Categories and Tickers ───────────────────────────────────────────

DEFAULT_CATEGORIES = {
    "반도체": ["IFX.DE", "NXPI", "STMPA.PA", "WOLF", "6723.T", "NVDA", "AMD", "ARM", "QCOM", "INTC", "AVGO", "MRVL", "MU", "000660.KS", "WDC", "SNDK", "285A.T", "2330.TW", "GFS", "0981.HK", "ASML.AS"],
    "네트워크": ["CIEN", "NOKIA.HE", "ERIC-B.ST", "CSCO"],
    "바이오": ["068270.KS", "BIIB", "OGN", "MRNA", "PFE", "AMGN", "ROG.SW", "LLY", "NVO", "4523.T", "LONN.SW", "4901.T", "OXB.L", "2269.HK", "2359.HK", "BANB.SW", "PPGN.SW"],
    "의료기기": ["GEHC", "PHIA.AS", "SHL.DE", "PACB", "TEM", "GH", "ILMN", "GRAL"],
    "공조": ["JCI", "TT", "CARR", "LII", "VRT"],
    "가전": ["ELUX-B.ST", "WHR"],
    "전장": ["APTV", "AMVOY", "TSLA", "MBLY", "VOW.DE", "2594.CH", "005380.KS"],
    "게임": ["RBLX", "U", "3659.T"],
    "기타": ["AAPL", "MSFT", "AMZN", "GOOGL", "META", "9988.HK", "6758.T", "373220.KS", "GLW", "6324.T"],
    "삼성": ["005930.KS", "028260.KS", "006400.KS", "018260.KS", "032830.KS", "009150.KS", "000810.KS", "209780.KS", "008770.KS", "012750.KS", "010140.KS", "016360.KS", "028050.KS", "030000.KS", "012620.KS", "207940.KS"],
}


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


def get_ticker_category(ticker):
    """Get the category for a given ticker."""
    ticker_upper = ticker.upper()
    for category, tickers in DEFAULT_CATEGORIES.items():
        if ticker_upper in [t.upper() for t in tickers]:
            return category
    return "기타"


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
    return render_template("index.html")


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


@app.route("/api/tickers/defaults", methods=["POST"])
def load_default_tickers():
    """Load all default tickers from categories."""
    all_tickers = []
    for category, tickers in DEFAULT_CATEGORIES.items():
        for t in tickers:
            t_upper = t.upper()
            if t_upper not in all_tickers:
                all_tickers.append(t_upper)

    save_tickers_to_csv(all_tickers)
    return jsonify({
        "tickers": all_tickers,
        "count": len(all_tickers),
        "categories": list(DEFAULT_CATEGORIES.keys()),
    })


@app.route("/api/categories", methods=["GET"])
def get_categories():
    """Get all default categories and their tickers."""
    return jsonify({"categories": DEFAULT_CATEGORIES})


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
                category = get_ticker_category(ticker)
                all_stocks_slim[ticker] = {
                    "name": result.get("name", ticker),
                    "change_pct": result.get("change_pct", 0),
                    "category": category,
                }
                if "error" in result:
                    all_stocks_slim[ticker]["error"] = result["error"]

                if abs(result.get("change_pct", 0)) >= 5.0:
                    filtered_list.append((ticker, result, category))

            gc.collect()

        # Calculate category stats
        category_stats = {}
        for ticker, info in all_stocks_slim.items():
            cat = info.get("category", "기타")
            if cat not in category_stats:
                category_stats[cat] = {"up": 0, "down": 0, "total_pct": 0, "count": 0}

            change = info.get("change_pct", 0)
            if change > 0:
                category_stats[cat]["up"] += 1
            elif change < 0:
                category_stats[cat]["down"] += 1
            category_stats[cat]["total_pct"] += change
            category_stats[cat]["count"] += 1

        # Calculate averages
        for cat, stats in category_stats.items():
            if stats["count"] > 0:
                stats["avg_pct"] = round(stats["total_pct"] / stats["count"], 2)
            else:
                stats["avg_pct"] = 0

        # Send all_stocks data with category stats
        yield f"data: {json.dumps({'type': 'stocks', 'all_stocks': all_stocks_slim, 'category_stats': category_stats})}\n\n"

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
            for ticker, info, category in batch:
                try:
                    result = analyze_with_gemini(ticker, info)

                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "category": category,
                        "analysis": result["analysis"],
                        "sources": result.get("sources", []),
                    })

                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "category": category,
                        "analysis": f"Analysis failed: {str(e)}",
                        "sources": [],
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
    """Use Gemini 2.5 Pro with Google Search grounding to analyze stock price movement."""
    if not GEMINI_API_KEY:
        return {"analysis": "Gemini API key not configured.", "sources": []}

    client = get_gemini_client()
    if not client:
        return {"analysis": "Gemini client initialization failed.", "sources": []}

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
        # Enable Google Search grounding
        from google.genai import types

        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )

        analysis_text = response.text

        # Extract grounding sources (up to 3)
        sources = []
        try:
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                logger.info(f"[GROUNDING] Candidate attrs: {dir(candidate)}")

                if hasattr(candidate, 'grounding_metadata') and candidate.grounding_metadata:
                    grounding = candidate.grounding_metadata
                    logger.info(f"[GROUNDING] Grounding metadata attrs: {dir(grounding)}")

                    # Try grounding_chunks
                    if hasattr(grounding, 'grounding_chunks') and grounding.grounding_chunks:
                        for chunk in grounding.grounding_chunks[:3]:
                            if hasattr(chunk, 'web') and chunk.web:
                                sources.append({
                                    "title": getattr(chunk.web, 'title', ''),
                                    "url": getattr(chunk.web, 'uri', ''),
                                })

                    # Try search_entry_point if no chunks
                    if not sources and hasattr(grounding, 'search_entry_point'):
                        sep = grounding.search_entry_point
                        if hasattr(sep, 'rendered_content'):
                            logger.info(f"[GROUNDING] Search entry point found")

                    # Try grounding_supports
                    if not sources and hasattr(grounding, 'grounding_supports'):
                        for support in grounding.grounding_supports[:3]:
                            if hasattr(support, 'grounding_chunk_indices'):
                                logger.info(f"[GROUNDING] Support chunk indices: {support.grounding_chunk_indices}")

                    # Try web_search_queries
                    if hasattr(grounding, 'web_search_queries'):
                        logger.info(f"[GROUNDING] Web search queries: {grounding.web_search_queries}")

        except Exception as e:
            logger.error(f"[GROUNDING] Error extracting sources: {e}")

        logger.info(f"[GROUNDING] Final sources count: {len(sources)}")
        return {"analysis": analysis_text, "sources": sources}
    except Exception as e:
        return {"analysis": f"Analysis failed: {str(e)}", "sources": []}


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
