import os
import gc
import csv
import json
import logging
import smtplib
from datetime import datetime, timedelta, timezone, date as date_cls
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
SETTINGS_FILE = "settings.json"

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Google CSE uses the same API key as Gemini by default
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "") or GEMINI_API_KEY
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "620f073b5bf414784")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# Email configuration
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)
EMAIL_TO_DEFAULT = os.getenv("EMAIL_TO", "")  # comma-separated default recipients

# Available Gemini models (for autocomplete suggestions; manual input also allowed)
AVAILABLE_GEMINI_MODELS = [
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro (기본값)"},
    {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash (빠름)"},
    {"id": "gemini-2.0-flash-lite", "label": "Gemini 2.0 Flash Lite (최고속)"},
    {"id": "gemini-1.5-pro", "label": "Gemini 1.5 Pro"},
    {"id": "gemini-1.5-flash", "label": "Gemini 1.5 Flash"},
    {"id": "gemini-2.5-flash-preview-04-17", "label": "Gemini 2.5 Flash Preview"},
]
DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"

# Default tickers pre-loaded on first run
DEFAULT_TICKERS = [
    "IFX.DE", "NXPI", "STMPA.PA", "ON", "WOLF", "6723.T",
    "NVDA", "AMD", "ARM", "QCOM", "INTC", "AVGO", "MRVL", "MU",
    "000660.KS", "WDC", "SNDK", "285A.T", "2330.TW", "GFS", "0981.HK",
    "ASML.AS", "CIEN", "NOKIA.HE", "ERIC-B.ST", "CSCO", "068270.KS",
    "BIIB", "OGN", "MRNA", "PFE", "AMGN", "ROG.SW", "LLY", "NVO",
    "4523.T", "LONN.SW", "4901.T", "OXB.L", "2269.HK", "2359.HK",
    "BANB.SW", "PPGN.SW", "GEHC", "PHIA.AS", "SHL.DE",
    "PACB", "TEM", "GH", "ILMN", "GRAL",
    "JCI", "TT", "CARR", "LII", "VRT", "ELUX-B.ST", "WHR",
    "APTV", "AMV0.DE", "TSLA", "MBLY", "VOW.DE", "002594.SZ", "005380.KS",
    "RBLX", "U", "3659.T",
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "9988.HK", "6758.T", "373220.KS",
    "GLW", "6324.T",
    "005930.KS", "005935.KS", "028260.KS", "006400.KS", "018260.KS",
    "032830.KS", "009150.KS", "000810.KS", "029780.KS", "008770.KS",
    "012750.KS", "010140.KS", "016360.KS", "028050.KS", "030000.KS",
    "0126z0.KS", "207940.KS",
]

# Default Gemini prompt templates
# Available variables: {name}, {ticker}, {change_pct}, {trade_date}
# Additional for with_articles: {articles_text}, {sources_label}, {articles_count}
DEFAULT_PROMPT_WITH_ARTICLES = """You are a stock market analyst. The following news articles were collected for this stock on or around the analysis date.

Stock: {name} ({ticker})
Change: {change_pct}%
Date: {trade_date}

수집된 뉴스 기사 ({articles_count}건, 출처: {sources_label}):
{articles_text}

Instructions:
1. 출력은 한글로 해라.
2. 제공된 기사 내용만을 근거로 주가 변동 이유를 2문장 이내로 간결하게 정리해라. (명사형 종결)
3. 종목명이나 변동률은 출력하지 마라.
4. 뉴스 기사가 변동 원인과 무관하거나 불충분하면 "개별이슈 미발견"으로만 출력해라.
5. 추측하거나 자체 지식을 사용하지 마라. 오직 제공된 기사 내용만 활용해라.
6. 유효한 분석이 있는 경우에만 한글 분석 후 영어로 한 문장 요약 추가. "개별이슈 미발견"인 경우 영어 요약 생략."""

DEFAULT_PROMPT_WITHOUT_ARTICLES = "개별이슈 미발견."

# Memory optimization: reuse Gemini client
_gemini_client = None

# Batch settings
FETCH_BATCH_SIZE = 30  # Fetch tickers in batches
ANALYSIS_BATCH_SIZE = 3  # Analyze filtered stocks in batches

KST = timezone(timedelta(hours=9))


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


def apply_template(template, **kwargs):
    """Safe template variable substitution using {varname} placeholders."""
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def get_kst_today():
    """Return today's date in KST (UTC+9)."""
    return datetime.now(KST).date()


# ─── Settings Management ──────────────────────────────────────────────────────

def load_settings():
    """Load settings from JSON file."""
    defaults = {
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "email_recipients": [],
        "prompt_with_articles": DEFAULT_PROMPT_WITH_ARTICLES,
        "prompt_without_articles": DEFAULT_PROMPT_WITHOUT_ARTICLES,
        "custom_query": "",
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                defaults.update(data)
        except Exception as e:
            logger.error(f"Error reading settings: {e}")
    return defaults


def save_settings(settings):
    """Save settings to JSON file."""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error writing settings: {e}")


# ─── CSV Ticker Management ────────────────────────────────────────────────────

def load_tickers_from_csv():
    """Load tickers from CSV file.

    Auto-initializes with DEFAULT_TICKERS only on the very first run (file not found).
    If the file exists (even empty – user explicitly cleared), respects that state.
    """
    if not os.path.exists(TICKERS_CSV_FILE):
        # First run: file has never been created → seed with defaults
        tickers = list(DEFAULT_TICKERS)
        save_tickers_to_csv(tickers)
        logger.info(f"Initialized with {len(tickers)} default tickers.")
        return tickers

    tickers = []
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


@app.route("/api/tickers/reset-defaults", methods=["POST"])
def reset_tickers_to_defaults():
    """Reset tickers to the built-in default list."""
    save_tickers_to_csv(DEFAULT_TICKERS)
    return jsonify({"tickers": list(DEFAULT_TICKERS), "count": len(DEFAULT_TICKERS)})


@app.route("/api/tickers/upload", methods=["POST"])
def upload_tickers_csv():
    """Upload a CSV file to add tickers. First column = ticker symbol."""
    if "file" not in request.files:
        return jsonify({"error": "파일이 없습니다"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "파일을 선택해주세요"}), 400

    # Try UTF-8 first, then EUC-KR
    content = None
    for encoding in ("utf-8-sig", "utf-8", "euc-kr"):
        try:
            file.seek(0)
            content = file.read().decode(encoding)
            break
        except (UnicodeDecodeError, Exception):
            continue

    if content is None:
        return jsonify({"error": "파일 인코딩 오류 (UTF-8 또는 EUC-KR 지원)"}), 400

    # Parse: first column of each row
    SKIP_HEADERS = {"ticker", "symbol", "종목코드", "티커", "종목명", "name", "code"}
    new_tickers = []
    for line in content.splitlines():
        if not line.strip():
            continue
        parts = line.split(",")
        ticker = parts[0].strip().strip('"').strip("'").upper()
        if ticker and ticker.lower() not in SKIP_HEADERS:
            new_tickers.append(ticker)

    if not new_tickers:
        return jsonify({"error": "CSV에서 티커를 찾을 수 없습니다. 첫 번째 열에 티커 심볼을 입력해주세요."}), 400

    tickers = load_tickers_from_csv()
    added = []
    for t in new_tickers:
        if t not in tickers:
            tickers.append(t)
            added.append(t)

    save_tickers_to_csv(tickers)
    return jsonify({"tickers": tickers, "added": added, "added_count": len(added)})


# ─── Settings Routes ──────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    settings = load_settings()
    settings["available_models"] = AVAILABLE_GEMINI_MODELS
    settings["has_google_search"] = bool(GOOGLE_API_KEY and GOOGLE_CSE_ID)
    settings["has_email"] = bool(SMTP_USER and SMTP_PASSWORD)
    settings["news_sources"] = {
        "yahoo_finance": True,  # always available, no API key needed
        "newsapi": bool(NEWS_API_KEY),
        "google_cse": bool(GOOGLE_API_KEY and GOOGLE_CSE_ID),
    }
    settings["default_prompt_with_articles"] = DEFAULT_PROMPT_WITH_ARTICLES
    settings["default_prompt_without_articles"] = DEFAULT_PROMPT_WITHOUT_ARTICLES
    return jsonify(settings)


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.get_json()
    settings = load_settings()
    if "gemini_model" in data:
        settings["gemini_model"] = data["gemini_model"]
    if "prompt_with_articles" in data:
        settings["prompt_with_articles"] = data["prompt_with_articles"]
    if "prompt_without_articles" in data:
        settings["prompt_without_articles"] = data["prompt_without_articles"]
    if "custom_query" in data:
        settings["custom_query"] = data["custom_query"]
    save_settings(settings)
    return jsonify(settings)


# ─── Email Recipients Routes ──────────────────────────────────────────────────

@app.route("/api/email/recipients", methods=["GET"])
def get_email_recipients():
    settings = load_settings()
    default_list = [e.strip() for e in EMAIL_TO_DEFAULT.split(",") if e.strip()] if EMAIL_TO_DEFAULT else []
    extra_list = settings.get("email_recipients", [])
    return jsonify({
        "default_recipients": default_list,
        "extra_recipients": extra_list,
        "has_email_config": bool(SMTP_USER and SMTP_PASSWORD),
    })


@app.route("/api/email/recipients", methods=["POST"])
def add_email_recipient():
    data = request.get_json()
    email_addr = data.get("email", "").strip().lower()
    if not email_addr or "@" not in email_addr:
        return jsonify({"error": "유효한 이메일 주소를 입력해주세요"}), 400

    settings = load_settings()
    recipients = settings.get("email_recipients", [])
    if email_addr in recipients:
        return jsonify({"error": f"{email_addr} 는 이미 등록되어 있습니다"}), 400

    recipients.append(email_addr)
    settings["email_recipients"] = recipients
    save_settings(settings)
    return jsonify({"extra_recipients": recipients})


@app.route("/api/email/recipients/<path:email_addr>", methods=["DELETE"])
def delete_email_recipient(email_addr):
    settings = load_settings()
    recipients = settings.get("email_recipients", [])
    email_addr = email_addr.lower()
    if email_addr in recipients:
        recipients.remove(email_addr)
        settings["email_recipients"] = recipients
        save_settings(settings)
    return jsonify({"extra_recipients": recipients})


@app.route("/api/send-email", methods=["POST"])
def send_email_report():
    """Send analysis results via email."""
    if not SMTP_USER or not SMTP_PASSWORD:
        return jsonify({"error": "이메일 설정이 구성되지 않았습니다. SMTP_USER, SMTP_PASSWORD 환경변수를 설정해주세요."}), 400

    data = request.get_json()
    results = data.get("results", [])
    extra_to = data.get("extra_to", [])  # Additional one-time recipients from request

    if not results:
        return jsonify({"error": "전송할 분석 결과가 없습니다"}), 400

    # Build recipient list
    settings = load_settings()
    default_list = [e.strip() for e in EMAIL_TO_DEFAULT.split(",") if e.strip()] if EMAIL_TO_DEFAULT else []
    extra_list = settings.get("email_recipients", [])
    all_recipients = list(set(default_list + extra_list + extra_to))

    if not all_recipients:
        return jsonify({"error": "수신자 이메일이 없습니다. 수신자를 추가해주세요."}), 400

    # Build HTML email
    today = datetime.now(KST).strftime("%Y-%m-%d")
    subject = f"[Stock Analyzer] 주가 변동 분석 리포트 {today}"
    html_body = build_email_html(results, today)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM or SMTP_USER
        msg["To"] = ", ".join(all_recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, all_recipients, msg.as_string())

        return jsonify({"success": True, "sent_to": all_recipients, "count": len(all_recipients)})

    except smtplib.SMTPAuthenticationError:
        return jsonify({"error": "SMTP 인증 실패. 이메일/비밀번호를 확인해주세요."}), 400
    except Exception as e:
        logger.error(f"Email send error: {e}")
        return jsonify({"error": f"이메일 전송 실패: {str(e)}"}), 500


def build_email_html(results, date_str):
    """Build HTML content for email report."""
    rows = ""
    for r in results:
        sign = "+" if r["change_pct"] > 0 else ""
        color = "#10b981" if r["change_pct"] > 0 else "#ef4444"
        analysis_text = r.get("analysis", "").replace("\n", "<br>")
        news_badge = ""
        if r.get("articles_found"):
            news_badge = '<span style="background:#3b82f6;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:6px;">뉴스 참고</span>'

        # Build article links
        articles_html = ""
        if r.get("articles"):
            article_items = ""
            for a in r["articles"]:
                date_part = f'<span style="color:#5a6a7a;font-size:10px;margin-right:6px;">{a["date"]}</span>' if a.get("date") else ""
                if a.get("link"):
                    article_items += f'<li style="padding:3px 0;">{date_part}<a href="{a["link"]}" style="color:#7ab3e0;">{a["title"]}</a> <small style="color:#5a6a7a;">({a.get("source","")})</small></li>'
                else:
                    article_items += f'<li style="padding:3px 0;">{date_part}{a["title"]} <small style="color:#5a6a7a;">({a.get("source","")})</small></li>'
            articles_html = f'<ul style="margin:8px 0 0 0;padding-left:16px;font-size:12px;">{article_items}</ul>'

        rows += f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #2a3a4a;">
                <strong>{r['name']}</strong> ({r['ticker']})
                <span style="color:{color};font-weight:bold;margin-left:8px;">{sign}{r['change_pct']:.1f}%</span>
                {news_badge}
                <br><small style="color:#aaa;">{r.get('model_used', 'Gemini')}</small>
                <div style="margin-top:8px;color:#ccc;line-height:1.5;">{analysis_text}</div>
                {articles_html}
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="background:#0f1923;color:#e0e6ed;font-family:Arial,sans-serif;margin:0;padding:20px;">
        <div style="max-width:700px;margin:0 auto;">
            <h1 style="color:#3b82f6;margin-bottom:4px;">Stock Movement Analyzer</h1>
            <p style="color:#aaa;margin-top:0;">분석 날짜: {date_str}</p>
            <table style="width:100%;border-collapse:collapse;background:#1a2634;border-radius:8px;overflow:hidden;">
                {rows}
            </table>
            <p style="color:#555;font-size:12px;margin-top:16px;">This report was automatically generated by Stock Movement Analyzer.</p>
        </div>
    </body>
    </html>
    """


@app.route("/api/analyze/stream", methods=["GET"])
def analyze_stream():
    """Streaming analysis - sends results in batches via SSE."""
    tickers = load_tickers_from_csv()
    if not tickers:
        return jsonify({"error": "No tickers saved."}), 400

    settings = load_settings()
    model = request.args.get("model", settings.get("gemini_model", DEFAULT_GEMINI_MODEL))

    # Target date: from query param (KST) or today KST
    date_str = request.args.get("date", "")
    target_date = None
    if date_str:
        try:
            target_date = date_cls.fromisoformat(date_str)
        except ValueError:
            pass
    if target_date is None:
        target_date = get_kst_today()

    # Prompt templates from settings
    prompt_templates = {
        "with_articles": settings.get("prompt_with_articles") or DEFAULT_PROMPT_WITH_ARTICLES,
        "without_articles": settings.get("prompt_without_articles") or DEFAULT_PROMPT_WITHOUT_ARTICLES,
    }

    # Custom search query (from query param, fallback to saved setting)
    custom_query = request.args.get("custom_query", "").strip() or settings.get("custom_query", "")

    def generate():
        log_memory("STREAM START")

        # Phase 1: Fetch all stock data in batches
        all_stocks_slim = {}
        filtered_list = []

        total_batches = (len(tickers) + FETCH_BATCH_SIZE - 1) // FETCH_BATCH_SIZE

        for batch_idx in range(0, len(tickers), FETCH_BATCH_SIZE):
            batch = tickers[batch_idx:batch_idx + FETCH_BATCH_SIZE]
            batch_num = batch_idx // FETCH_BATCH_SIZE + 1

            yield f"data: {json.dumps({'type': 'progress', 'message': f'주가 수집 중... ({batch_num}/{total_batches})'})}\n\n"

            for ticker in batch:
                result = fetch_single_ticker(ticker, target_date=target_date)
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

        for batch_idx in range(0, len(filtered_list), ANALYSIS_BATCH_SIZE):
            batch = filtered_list[batch_idx:batch_idx + ANALYSIS_BATCH_SIZE]
            batch_num = batch_idx // ANALYSIS_BATCH_SIZE + 1

            log_memory(f"ANALYSIS BATCH {batch_num}")

            yield f"data: {json.dumps({'type': 'progress', 'message': f'분석 중... ({batch_num}/{total_analysis_batches})'})}\n\n"

            batch_results = []
            for ticker, info in batch:
                try:
                    # Step 1: Search news from all available sources
                    yield f"data: {json.dumps({'type': 'progress', 'message': f'뉴스 기사 검색 중... ({ticker})'})}\n\n"
                    articles = search_all_news_articles(
                        ticker,
                        info.get("name", ticker),
                        info.get("date", ""),
                        target_date=target_date,
                        custom_query=custom_query,
                    )

                    # Step 2: Analyze with Gemini
                    yield f"data: {json.dumps({'type': 'progress', 'message': f'Gemini 분석 중... ({ticker})'})}\n\n"
                    analysis = analyze_with_gemini(
                        ticker, info, articles=articles, model=model,
                        prompt_templates=prompt_templates,
                    )

                    article_sources = list(set(a.get("source", "") for a in articles if a.get("source")))
                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": analysis,
                        "articles_found": len(articles) > 0,
                        "articles_count": len(articles),
                        "articles_sources": article_sources,
                        "articles": [
                            {
                                "title": a["title"],
                                "link": a.get("link", ""),
                                "source": a.get("source", ""),
                                "date": a.get("date", ""),
                            }
                            for a in articles[:5]
                        ],
                        "model_used": model,
                    })

                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": f"분석 실패: {str(e)}",
                        "articles_found": False,
                        "articles_count": 0,
                        "articles_sources": [],
                        "articles": [],
                        "model_used": model,
                    })

                gc.collect()

            # Send batch results
            yield f"data: {json.dumps({'type': 'results', 'results': batch_results})}\n\n"

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


def fetch_single_ticker(ticker_symbol, target_date=None):
    """Fetch a single ticker's data from Yahoo Finance API.

    If target_date (date object, KST) is provided, returns data for that trading day.
    Otherwise returns the most recent day's data.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"

        if target_date:
            # Fetch a 14-day window ending at target_date+2 to account for weekends/holidays
            period1_dt = datetime.combine(
                target_date - timedelta(days=14), datetime.min.time()
            ).replace(tzinfo=timezone.utc)
            period2_dt = datetime.combine(
                target_date + timedelta(days=2), datetime.min.time()
            ).replace(tzinfo=timezone.utc)
            params = {
                "period1": int(period1_dt.timestamp()),
                "period2": int(period2_dt.timestamp()),
                "interval": "1d",
            }
        else:
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

        if target_date:
            # Filter to entries on or before target_date (compare in KST = UTC+9)
            filtered = []
            for ts, c in valid:
                kst_date = (datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(hours=9)).date()
                if kst_date <= target_date:
                    filtered.append((ts, c))
            if len(filtered) >= 2:
                valid = filtered

        if len(valid) < 2:
            return {
                "error": f"Insufficient data (rows={len(valid)})",
                "change_pct": 0,
                "name": ticker_symbol,
            }

        prev_close = valid[-2][1]
        last_close = valid[-1][1]
        change_pct = ((last_close - prev_close) / prev_close) * 100
        # Report date in KST
        last_date = (datetime.fromtimestamp(valid[-1][0], tz=timezone.utc) + timedelta(hours=9)).date()
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


def build_search_query(custom_query, company_name, ticker, fallback):
    """Return query string: apply custom_query template or use fallback."""
    if not custom_query:
        return fallback
    return custom_query.replace("{종목명}", company_name).replace("{name}", company_name).replace("{ticker}", ticker)


def search_news_articles_yahoo_finance(ticker, company_name):
    """Search news from Yahoo Finance (no API key required)."""
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        params = {
            "q": ticker,
            "lang": "en-US",
            "region": "US",
            "quotesCount": 0,
            "newsCount": 5,
        }
        resp = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        data = resp.json()
        articles = []
        for item in data.get("news", []):
            title = item.get("title", "")
            if title:
                # Convert providerPublishTime (Unix timestamp) to KST date string
                pub_ts = item.get("providerPublishTime", 0)
                pub_date = ""
                if pub_ts:
                    pub_date = (
                        datetime.fromtimestamp(pub_ts, tz=timezone.utc) + timedelta(hours=9)
                    ).strftime("%Y-%m-%d")
                articles.append({
                    "title": title,
                    "snippet": "",
                    "link": item.get("link", ""),
                    "source": "Yahoo Finance",
                    "date": pub_date,
                })
        logger.info(f"Yahoo Finance: found {len(articles)} articles for {ticker}")
        return articles

    except Exception as e:
        logger.error(f"Yahoo Finance news error for {ticker}: {e}")
        return []


def search_news_articles_newsapi(ticker, company_name, target_date=None, custom_query=None):
    """Search news from NewsAPI.org (requires NEWS_API_KEY).

    If target_date is provided, searches articles from target_date to target_date+1.
    """
    if not NEWS_API_KEY:
        return []
    try:
        query = build_search_query(custom_query, company_name, ticker, f'"{company_name}" OR "{ticker}" stock')
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 5,
            "apiKey": NEWS_API_KEY,
        }
        if target_date:
            params["from"] = str(target_date)
            params["to"] = str(target_date + timedelta(days=1))

        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"NewsAPI error {resp.status_code} for {ticker}")
            return []

        data = resp.json()
        articles = []
        for item in data.get("articles", []):
            title = item.get("title", "")
            if title and title != "[Removed]":
                published = item.get("publishedAt", "")
                articles.append({
                    "title": title,
                    "snippet": item.get("description", ""),
                    "link": item.get("url", ""),
                    "source": "NewsAPI",
                    "date": published[:10] if published else "",
                })
        logger.info(f"NewsAPI: found {len(articles)} articles for {ticker}")
        return articles

    except Exception as e:
        logger.error(f"NewsAPI error for {ticker}: {e}")
        return []


def search_news_articles_google(ticker, company_name, trade_date, target_date=None, custom_query=None):
    """Search news from Google Custom Search API (requires GOOGLE_API_KEY + GOOGLE_CSE_ID).

    If target_date is provided, restricts results to that date range (date to date+1).
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    try:
        query = build_search_query(custom_query, company_name, ticker, f'"{company_name}" OR "{ticker}" stock news')
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CSE_ID,
            "q": query,
            "num": 5,
        }
        if target_date:
            next_date = target_date + timedelta(days=1)
            params["sort"] = f"date:r:{target_date.strftime('%Y%m%d')}:{next_date.strftime('%Y%m%d')}"
        else:
            params["dateRestrict"] = "d3"

        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Google Search API error {resp.status_code} for {ticker}")
            return []

        data = resp.json()
        articles = []
        for item in data.get("items", []):
            title = item.get("title", "")
            if title:
                # Try to extract publish date from pagemap (multiple sources)
                pub_date = ""
                pagemap = item.get("pagemap", {})

                # 1. Try metatags first
                metatags = pagemap.get("metatags", [])
                if metatags:
                    raw_date = (
                        metatags[0].get("article:published_time", "") or
                        metatags[0].get("article:modified_time", "") or
                        metatags[0].get("og:updated_time", "") or
                        metatags[0].get("og:article:published_time", "") or
                        metatags[0].get("date", "") or
                        metatags[0].get("datePublished", "") or
                        metatags[0].get("pubdate", "")
                    )
                    if raw_date:
                        pub_date = raw_date[:10]

                # 2. Try newsarticle / article pagemap
                if not pub_date:
                    for key in ("newsarticle", "article"):
                        entries = pagemap.get(key, [])
                        if entries:
                            raw_date = entries[0].get("datepublished", "") or entries[0].get("datemodified", "")
                            if raw_date:
                                pub_date = raw_date[:10]
                                break

                # 3. Try to extract date from snippet (Google often prepends "MMM DD, YYYY — ")
                if not pub_date:
                    snippet = item.get("snippet", "")
                    import re as _re
                    m = _re.match(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", snippet)
                    if m:
                        try:
                            from datetime import datetime as _dt
                            pub_date = _dt.strptime(m.group(1), "%b %d, %Y").strftime("%Y-%m-%d")
                        except ValueError:
                            pass
                articles.append({
                    "title": title,
                    "snippet": item.get("snippet", ""),
                    "link": item.get("link", ""),
                    "source": "Google",
                    "date": pub_date,
                })
        logger.info(f"Google CSE: found {len(articles)} articles for {ticker}")
        return articles

    except Exception as e:
        logger.error(f"Google CSE error for {ticker}: {e}")
        return []


def search_all_news_articles(ticker, company_name, trade_date, target_date=None, custom_query=None):
    """Search news from all available sources and merge results.

    Priority: Yahoo Finance (always) → NewsAPI (if configured) → Google CSE (if configured)
    Returns up to 10 deduplicated articles.
    """
    all_articles = []

    # 1. Yahoo Finance — always available (uses ticker directly, not text query)
    all_articles.extend(search_news_articles_yahoo_finance(ticker, company_name))

    # 2. NewsAPI — optional
    if NEWS_API_KEY:
        all_articles.extend(search_news_articles_newsapi(ticker, company_name, target_date=target_date, custom_query=custom_query))

    # 3. Google CSE — optional
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        all_articles.extend(search_news_articles_google(ticker, company_name, trade_date, target_date=target_date, custom_query=custom_query))

    # Deduplicate by title
    seen_titles = set()
    unique = []
    for a in all_articles:
        t = a["title"].strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            unique.append(a)

    # Filter by date: keep only articles from target_date or later.
    # Articles with no date field are kept (date unknown).
    # If all articles are older than target_date, return empty → "개별이슈 미발견"
    if target_date:
        date_threshold = str(target_date)
        date_filtered = [
            a for a in unique
            if not a.get("date") or a["date"] >= date_threshold
        ]
        logger.info(
            f"Date filter ({date_threshold}): {len(unique)} → {len(date_filtered)} articles for {ticker}"
        )
        unique = date_filtered

    logger.info(f"Total unique articles for {ticker}: {len(unique)}")
    return unique[:10]


def analyze_with_gemini(ticker, stock_info, articles=None, model=None, prompt_templates=None):
    """Use Gemini to analyze stock price movement using customizable prompt templates.

    Template variables: {name}, {ticker}, {change_pct}, {trade_date}
    Additional for with_articles: {articles_text}, {sources_label}, {articles_count}
    """
    if not GEMINI_API_KEY:
        return "Gemini API key not configured."

    client = get_gemini_client()
    if not client:
        return "Gemini client initialization failed."

    if model is None:
        settings = load_settings()
        model = settings.get("gemini_model", DEFAULT_GEMINI_MODEL)

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]
    trade_date = stock_info.get("date", "")

    templates = prompt_templates or {}

    if not articles:
        # No articles from the analysis date → no speculation, return directly
        return "개별이슈 미발견."

    sources_used = list(set(a.get("source", "") for a in articles if a.get("source")))
    sources_label = ", ".join(sources_used) if sources_used else "외부 검색"
    articles_text = "\n".join([
        f"  [{i+1}] [{a.get('source', '')}] {a['title']}" +
        (f"\n      {a['snippet']}" if a.get('snippet') else "")
        for i, a in enumerate(articles[:8])
    ])
    tmpl = templates.get("with_articles") or DEFAULT_PROMPT_WITH_ARTICLES
    prompt = apply_template(
        tmpl,
        name=name,
        ticker=ticker,
        change_pct=f"{change_pct:+.1f}",
        trade_date=trade_date,
        articles_text=articles_text,
        sources_label=sources_label,
        articles_count=len(articles),
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return response.text
    except Exception as e:
        return f"분석 실패: {str(e)}"


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
