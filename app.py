import os
import gc
import re
import csv
import json
import logging
import secrets
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

# GCP Cloud Storage
try:
    from google.cloud import storage as gcs_storage
    HAS_GCS = True
except ImportError:
    HAS_GCS = False

# APScheduler — background scheduler for daily auto-analysis
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz as _pytz
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CSV file for ticker storage
TICKERS_CSV_FILE = "tickers.csv"
SETTINGS_FILE = "settings.json"

# GCP Cloud Storage – set GCS_BUCKET_NAME env var to enable persistence
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")


def _gcs_bucket():
    """Return (client, bucket) or (None, None) if GCS is unavailable."""
    if not HAS_GCS or not GCS_BUCKET_NAME:
        return None, None
    try:
        client = gcs_storage.Client()
        return client, client.bucket(GCS_BUCKET_NAME)
    except Exception as e:
        logger.error(f"GCS client error: {e}")
        return None, None


def gcs_download(blob_name, dest_path):
    """Download blob_name from GCS to dest_path. Returns True on success."""
    _, bucket = _gcs_bucket()
    if bucket is None:
        return False
    try:
        blob = bucket.blob(blob_name)
        if not blob.exists():
            logger.info(f"GCS: {blob_name} not found in bucket")
            return False
        blob.download_to_filename(dest_path)
        logger.info(f"GCS: downloaded gs://{GCS_BUCKET_NAME}/{blob_name} -> {dest_path}")
        return True
    except Exception as e:
        logger.error(f"GCS download error ({blob_name}): {e}")
        return False


def gcs_upload(src_path, blob_name):
    """Upload src_path to GCS as blob_name. Returns True on success."""
    _, bucket = _gcs_bucket()
    if bucket is None:
        return False
    try:
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(src_path)
        logger.info(f"GCS: uploaded {src_path} -> gs://{GCS_BUCKET_NAME}/{blob_name}")
        return True
    except Exception as e:
        logger.error(f"GCS upload error ({blob_name}): {e}")
        return False


def startup_sync_from_gcs():
    """On app start, pull settings.json and tickers.csv from GCS (if configured).
    If GCS tickers differ from DEFAULT_TICKERS, push DEFAULT_TICKERS to GCS.
    """
    if not GCS_BUCKET_NAME:
        return
    logger.info(f"GCS: syncing config from bucket '{GCS_BUCKET_NAME}'...")
    gcs_download("settings.json", SETTINGS_FILE)
    gcs_download("tickers.csv", TICKERS_CSV_FILE)

    # If the GCS ticker set differs from DEFAULT_TICKERS, update GCS automatically.
    # This keeps GCS in sync whenever DEFAULT_TICKERS is changed in code.
    if os.path.exists(TICKERS_CSV_FILE):
        gcs_ticker_set = set()
        try:
            import csv as _csv
            with open(TICKERS_CSV_FILE, "r", newline="", encoding="utf-8") as _f:
                for row in _csv.reader(_f):
                    if row and row[0].strip():
                        gcs_ticker_set.add(row[0].strip().upper())
        except Exception:
            pass
        default_ticker_set = {t["ticker"].upper() for t in DEFAULT_TICKERS}
        if gcs_ticker_set != default_ticker_set:
            save_tickers_to_csv(DEFAULT_TICKERS)
            if gcs_upload(TICKERS_CSV_FILE, "tickers.csv"):
                logger.info(f"GCS: tickers.csv updated ({len(DEFAULT_TICKERS)} tickers)")

    logger.info("GCS: startup sync complete")


# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Google CSE uses the same API key as Gemini by default
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "") or GEMINI_API_KEY
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "620f073b5bf414784")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# Email configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "lub2sky@gmail.com"
SMTP_PASSWORD = "rasmdixgbznopxel"
EMAIL_FROM = "lub2sky@gmail.com"
EMAIL_TO_DEFAULT = os.getenv("EMAIL_TO", "")  # comma-separated default recipients (env)
DEFAULT_EMAIL_RECIPIENTS = list(dict.fromkeys(
    [e.strip() for e in EMAIL_TO_DEFAULT.split(",") if e.strip()]
    + ["yunseong.cho@samsung.com"]
))  # hardcoded defaults merged with env

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

# Default tickers pre-loaded on first run (list of dicts: ticker, category, name)
DEFAULT_TICKERS = [
    # 반도체
    {"ticker": "NVDA",       "category": "반도체", "name": "nVidia"},
    {"ticker": "AMD",        "category": "반도체", "name": "AMD"},
    {"ticker": "ARM",        "category": "반도체", "name": "Arm"},
    {"ticker": "QCOM",       "category": "반도체", "name": "Qualcomm"},
    {"ticker": "INTC",       "category": "반도체", "name": "Intel"},
    {"ticker": "AVGO",       "category": "반도체", "name": "Broadcom"},
    {"ticker": "MRVL",       "category": "반도체", "name": "Marvell"},
    {"ticker": "MU",         "category": "반도체", "name": "Micron"},
    {"ticker": "000660.KS",  "category": "반도체", "name": "하이닉스"},
    {"ticker": "WDC",        "category": "반도체", "name": "Western Digital"},
    {"ticker": "SNDK",       "category": "반도체", "name": "Sandisk"},
    {"ticker": "285A.T",     "category": "반도체", "name": "Kioxia"},
    {"ticker": "2330.TW",    "category": "반도체", "name": "TSMC"},
    {"ticker": "GFS",        "category": "반도체", "name": "Globalfoundries"},
    {"ticker": "ASML.AS",    "category": "반도체", "name": "ASML"},
    {"ticker": "IFX.DE",     "category": "반도체", "name": "Infineon"},
    {"ticker": "NXPI",       "category": "반도체", "name": "NXP"},
    {"ticker": "STMPA.PA",   "category": "반도체", "name": "STMicro"},
    {"ticker": "6723.T",     "category": "반도체", "name": "Renesas"},
    # 네트워크
    {"ticker": "CIEN",       "category": "네트워크", "name": "Ciena"},
    {"ticker": "NOKIA.HE",   "category": "네트워크", "name": "Nokia"},
    {"ticker": "ERIC-B.ST",  "category": "네트워크", "name": "Ericsson"},
    {"ticker": "CSCO",       "category": "네트워크", "name": "Cisco"},
    # 바이오
    {"ticker": "068270.KS",  "category": "바이오", "name": "셀트리온"},
    {"ticker": "BIIB",       "category": "바이오", "name": "BG"},
    {"ticker": "OGN",        "category": "바이오", "name": "Organon"},
    {"ticker": "MRNA",       "category": "바이오", "name": "Moderna"},
    {"ticker": "PFE",        "category": "바이오", "name": "Pfizer"},
    {"ticker": "AMGN",       "category": "바이오", "name": "Amgen"},
    {"ticker": "ROG.SW",     "category": "바이오", "name": "Roche"},
    {"ticker": "LLY",        "category": "바이오", "name": "Eli Lilly"},
    {"ticker": "NVO",        "category": "바이오", "name": "Novo Nordisk"},
    {"ticker": "LONN.SW",    "category": "바이오", "name": "Lonza"},
    {"ticker": "4901.T",     "category": "바이오", "name": "Fujifilm"},
    {"ticker": "2269.HK",    "category": "바이오", "name": "Wuxi Biologics"},
    {"ticker": "2359.HK",    "category": "바이오", "name": "Wuxi AppTec"},
    {"ticker": "BANB.SW",    "category": "바이오", "name": "Bachem"},
    {"ticker": "PPGN.SW",    "category": "바이오", "name": "PolyPeptide"},
    # 의료기기
    {"ticker": "GEHC",       "category": "의료기기", "name": "GE 헬스케어"},
    {"ticker": "PHIA.AS",    "category": "의료기기", "name": "Philips"},
    {"ticker": "SHL.DE",     "category": "의료기기", "name": "Siemens 헬시니어"},
    {"ticker": "TEM",        "category": "의료기기", "name": "Tempus AI"},
    {"ticker": "GH",         "category": "의료기기", "name": "Guardant Health"},
    {"ticker": "ILMN",       "category": "의료기기", "name": "Illumina"},
    {"ticker": "GRAL",       "category": "의료기기", "name": "Grail"},
    # PC
    {"ticker": "HPQ",        "category": "PC", "name": "HP"},
    {"ticker": "HPE",        "category": "PC", "name": "HPE"},
    {"ticker": "0992.HK",    "category": "PC", "name": "Lenovo"},
    {"ticker": "DELL",       "category": "PC", "name": "Dell"},
    {"ticker": "6724.T",     "category": "PC", "name": "Epson"},
    # 공조
    {"ticker": "JCI",        "category": "공조", "name": "Johnson Control"},
    {"ticker": "TT",         "category": "공조", "name": "Trane"},
    {"ticker": "CARR",       "category": "공조", "name": "Carrier"},
    {"ticker": "LII",        "category": "공조", "name": "Lennox"},
    {"ticker": "MOD",        "category": "공조", "name": "Modine"},
    {"ticker": "VRT",        "category": "공조", "name": "Vertiv"},
    # 가전
    {"ticker": "ELUX-B.ST",  "category": "가전", "name": "Elux"},
    # 전장
    {"ticker": "APTV",       "category": "전장", "name": "Aptiv"},
    {"ticker": "TSLA",       "category": "전장", "name": "Tesla"},
    {"ticker": "MBLY",       "category": "전장", "name": "Mobileye"},
    {"ticker": "002594.SZ",  "category": "전장", "name": "BYD"},
    {"ticker": "005380.KS",  "category": "전장", "name": "Hyundai Motor"},
    # 게임
    {"ticker": "RBLX",       "category": "게임", "name": "Roblox"},
    {"ticker": "U",          "category": "게임", "name": "Unity"},
    {"ticker": "3659.T",     "category": "게임", "name": "넥슨"},
    # 기타
    {"ticker": "AAPL",       "category": "기타", "name": "Apple"},
    {"ticker": "MSFT",       "category": "기타", "name": "Microsoft"},
    {"ticker": "AMZN",       "category": "기타", "name": "Amazon"},
    {"ticker": "GOOGL",      "category": "기타", "name": "Google"},
    {"ticker": "META",       "category": "기타", "name": "Meta"},
    {"ticker": "9988.HK",    "category": "기타", "name": "Alibaba"},
    {"ticker": "6758.T",     "category": "기타", "name": "Sony"},
    {"ticker": "GLW",        "category": "기타", "name": "Corning"},
    {"ticker": "6324.T",     "category": "기타", "name": "Harmonic Drive"},
    # 삼성
    {"ticker": "005930.KS",  "category": "삼성", "name": "전자"},
    {"ticker": "028260.KS",  "category": "삼성", "name": "물산"},
    {"ticker": "032830.KS",  "category": "삼성", "name": "생명"},
    {"ticker": "006400.KS",  "category": "삼성", "name": "SDI"},
    {"ticker": "018260.KS",  "category": "삼성", "name": "SDS"},
    {"ticker": "009150.KS",  "category": "삼성", "name": "전기"},
    {"ticker": "012750.KS",  "category": "삼성", "name": "에스원"},
    {"ticker": "010140.KS",  "category": "삼성", "name": "중공업"},
    {"ticker": "030000.KS",  "category": "삼성", "name": "제일기획"},
    {"ticker": "0126Z0.KS",  "category": "삼성", "name": "에피스홀딩스"},
    {"ticker": "207940.KS",  "category": "삼성", "name": "로직스"},
    {"ticker": "000810.KS",  "category": "삼성", "name": "삼성화재"},
    {"ticker": "029780.KS",  "category": "삼성", "name": "삼성카드"},
    {"ticker": "008770.KS",  "category": "삼성", "name": "호텔신라"},
    {"ticker": "016360.KS",  "category": "삼성", "name": "삼성증권"},
    {"ticker": "028050.KS",  "category": "삼성", "name": "삼성엔지니어링"},
]

# Global market indices
MARKET_INDICES = [
    {"ticker": "^DJI",      "name": "다우존스",  "region": "미국",   "tz_hours":  0},
    {"ticker": "^IXIC",     "name": "나스닥",    "region": "미국",   "tz_hours":  0},
    {"ticker": "^GSPC",     "name": "S&P 500",   "region": "미국",   "tz_hours":  0},
    {"ticker": "^KS11",     "name": "코스피",    "region": "한국",   "tz_hours":  9},
    {"ticker": "^KQ11",     "name": "코스닥",    "region": "한국",   "tz_hours":  9},
    {"ticker": "000001.SS", "name": "상해종합",  "region": "중국",   "tz_hours":  8},
    {"ticker": "^HSI",      "name": "항셍",      "region": "홍콩",   "tz_hours":  8},
    {"ticker": "^N225",     "name": "닛케이225", "region": "일본",   "tz_hours":  9},
    {"ticker": "^FTSE",     "name": "FTSE100",   "region": "영국",   "tz_hours":  0},
    {"ticker": "^FCHI",     "name": "CAC40",     "region": "프랑스", "tz_hours":  1},
    {"ticker": "^GDAXI",    "name": "DAX",       "region": "독일",   "tz_hours":  1},
]

# Email market group layout (ticker → display label, grouped by region)
EMAIL_MARKET_GROUPS = [
    {
        "label": "미  국",
        "indices": [
            ("^DJI",      "Dow"),
            ("^IXIC",     "Nasdaq"),
            ("^GSPC",     "S&P 500"),
        ],
    },
    {
        "label": "아시아",
        "indices": [
            ("^KS11",     "韓코스피"),
            ("^KQ11",     "코스닥"),
            ("000001.SS", "中상해"),
            ("^HSI",      "홍콩항셍"),
            ("^N225",     "日니케이"),
        ],
    },
    {
        "label": "유  럽",
        "indices": [
            ("^FTSE",  "英FTSE"),
            ("^FCHI",  "CAC"),
            ("^GDAXI", "獨DAX"),
        ],
    },
]

# Region → ticker mapping for market news search
MARKET_NEWS_REGIONS = {
    "미국":  {"ticker": "^DJI",      "query": "US stock market Dow Jones Nasdaq S&P 500"},
    "한국":  {"ticker": "^KS11",     "query": "KOSPI KOSDAQ 코스피 코스닥 주식시장"},
    "중국":  {"ticker": "000001.SS", "query": "China Shanghai stock market"},
    "홍콩":  {"ticker": "^HSI",      "query": "Hong Kong Hang Seng stock market"},
    "일본":  {"ticker": "^N225",     "query": "Japan Nikkei 225 stock market"},
    "유럽":  {"ticker": "^FTSE",     "query": "European stock market FTSE DAX CAC"},
}

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
2. 제공된 기사 내용만을 근거로 주가 변동 이유를 4문장 이내로 간결하게 정리해라. (명사형 종결)
3. 종목명이나 변동률은 출력하지 마라.
4. 뉴스 기사가 변동 원인과 무관하거나 불충분하면 "개별이슈 미발견"으로만 출력해라.
5. 추측하거나 자체 지식을 사용하지 마라. 오직 제공된 기사 내용만 활용해라.
6. 유효한 분석이 있는 경우에만 한글 분석 후 영어로 한 문장 요약 추가. "개별이슈 미발견"인 경우 영어 요약 생략.
7. 응답 맨 끝에 분석에 가장 관련도 높은 기사 번호를 최대 3개만 골라 반드시 `REFS:[1,3]` 형식으로 출력해라. 주가 변동과 무관한 기사는 포함하지 마라. 분석이 "개별이슈 미발견"인 경우 `REFS:[]` 출력."""

DEFAULT_PROMPT_WITHOUT_ARTICLES = "개별이슈 미발견."

# Memory optimization: reuse Gemini client
_gemini_client = None

# Scheduler singleton
_scheduler = None

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
        "newsapi_query": "",
        "google_cse_query": "",
        "naver_query": "",
        "change_threshold": 5.0,
        "gmail_read_enabled": False,
        "gmail_subject_filter": "",
        "gmail_max_emails": 3,
        "yahoo_finance_enabled": True,
        "newsapi_enabled": True,
        "google_cse_enabled": True,
        "naver_enabled": True,
        "auto_schedule_enabled": False,
        "auto_schedule_time": "09:00",
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


# ─── Scheduler Management ─────────────────────────────────────────────────────

def _apply_schedule(settings):
    """설정에 따라 백그라운드 스케줄러를 활성화/비활성화한다. 멱등 함수."""
    global _scheduler
    if not HAS_SCHEDULER:
        logger.warning("APScheduler not installed; auto-schedule unavailable")
        return

    enabled = settings.get("auto_schedule_enabled", False)
    time_str = settings.get("auto_schedule_time", "09:00")

    try:
        hour, minute = [int(x) for x in time_str.split(":")]
    except (ValueError, AttributeError):
        logger.error(f"Invalid auto_schedule_time: {time_str!r}; defaulting to 09:00")
        hour, minute = 9, 0

    kst = _pytz.timezone("Asia/Seoul")

    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=kst)
        _scheduler.start()
        logger.info("APScheduler started")

    if _scheduler.get_job("daily_analysis"):
        _scheduler.remove_job("daily_analysis")

    if enabled:
        _scheduler.add_job(
            lambda: run_scheduled_analysis(),
            CronTrigger(hour=hour, minute=minute, timezone=kst),
            id="daily_analysis",
            name="Daily stock analysis",
            replace_existing=True,
            misfire_grace_time=600,
        )
        logger.info(f"Auto-schedule enabled: daily at {hour:02d}:{minute:02d} KST")
    else:
        logger.info("Auto-schedule disabled")


def _get_next_run_time():
    """다음 실행 시각을 KST 문자열로 반환. 없으면 None."""
    if not HAS_SCHEDULER or _scheduler is None:
        return None
    job = _scheduler.get_job("daily_analysis")
    if job and job.next_run_time:
        kst = _pytz.timezone("Asia/Seoul")
        return job.next_run_time.astimezone(kst).strftime("%Y-%m-%d %H:%M KST")
    return None


# ─── CSV Ticker Management ────────────────────────────────────────────────────

def load_tickers_from_csv():
    """Load tickers from CSV file. Returns list of dicts: {ticker, category, name}.

    Auto-initializes with DEFAULT_TICKERS only on the very first run (file not found).
    Supports both new 3-column format (ticker,category,name) and legacy 1-column format.
    """
    if not os.path.exists(TICKERS_CSV_FILE):
        save_tickers_to_csv(DEFAULT_TICKERS)
        logger.info(f"Initialized with {len(DEFAULT_TICKERS)} default tickers.")
        return list(DEFAULT_TICKERS)

    ticker_list = []
    try:
        with open(TICKERS_CSV_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or not row[0].strip():
                    continue
                ticker = row[0].strip().upper()
                category = row[1].strip() if len(row) > 1 else ""
                name = row[2].strip() if len(row) > 2 else ""
                ticker_list.append({"ticker": ticker, "category": category, "name": name})
    except Exception as e:
        logger.error(f"Error reading CSV: {e}")
    return ticker_list


def save_tickers_to_csv(ticker_list):
    """Save list of ticker dicts ({ticker, category, name}) to CSV file."""
    try:
        with open(TICKERS_CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            for item in ticker_list:
                if isinstance(item, dict):
                    writer.writerow([
                        item.get("ticker", ""),
                        item.get("category", ""),
                        item.get("name", ""),
                    ])
                else:
                    writer.writerow([str(item), "", ""])
    except Exception as e:
        logger.error(f"Error writing CSV: {e}")


# ─── GCS Startup Sync ─────────────────────────────────────────────────────────
try:
    startup_sync_from_gcs()
except Exception as _e:
    logger.error(f"Startup GCS sync failed (non-fatal): {_e}")

try:
    _apply_schedule(load_settings())
except Exception as _e:
    logger.error(f"Startup scheduler init failed (non-fatal): {_e}")

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/tickers", methods=["GET"])
def get_tickers():
    ticker_list = load_tickers_from_csv()
    return jsonify({"tickers": ticker_list})


@app.route("/api/tickers", methods=["POST"])
def add_ticker():
    data = request.get_json()
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "Ticker is required"}), 400

    category = data.get("category", "").strip()
    name = data.get("name", "").strip()

    ticker_list = load_tickers_from_csv()
    existing = [t["ticker"] for t in ticker_list]
    if ticker in existing:
        return jsonify({"error": f"{ticker} is already added"}), 400

    ticker_list.append({"ticker": ticker, "category": category, "name": name})
    save_tickers_to_csv(ticker_list)
    return jsonify({"tickers": ticker_list})


@app.route("/api/tickers/<ticker>", methods=["DELETE"])
def delete_ticker(ticker):
    ticker = ticker.upper()
    ticker_list = load_tickers_from_csv()
    ticker_list = [t for t in ticker_list if t["ticker"] != ticker]
    save_tickers_to_csv(ticker_list)
    return jsonify({"tickers": ticker_list})


@app.route("/api/tickers/bulk", methods=["POST"])
def bulk_add_tickers():
    """Add multiple tickers at once. Accepts list of strings or dicts."""
    data = request.get_json()
    new_items = data.get("tickers", [])

    if not new_items:
        return jsonify({"error": "No tickers provided"}), 400

    ticker_list = load_tickers_from_csv()
    existing = {t["ticker"] for t in ticker_list}
    added = []
    for item in new_items:
        if isinstance(item, dict):
            t = item.get("ticker", "").strip().upper()
            cat = item.get("category", "").strip()
            nm = item.get("name", "").strip()
        else:
            t = str(item).strip().upper()
            cat = ""
            nm = ""
        if t and t not in existing:
            ticker_list.append({"ticker": t, "category": cat, "name": nm})
            existing.add(t)
            added.append(t)

    save_tickers_to_csv(ticker_list)
    return jsonify({"tickers": ticker_list, "added": added, "added_count": len(added)})


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

    # Parse CSV – detect format by header row
    SKIP_HEADERS = {"ticker", "symbol", "yahoo finance ticker", "종목코드", "티커", "종목명", "name", "code", "category"}
    lines = [l for l in content.splitlines() if l.strip()]
    if not lines:
        return jsonify({"error": "CSV에서 티커를 찾을 수 없습니다."}), 400

    # Detect if header row exists and determine column layout
    first_parts = [p.strip().strip('"').strip("'").lower() for p in lines[0].split(",")]
    has_header = first_parts[0] in SKIP_HEADERS
    # Detect Category,Ticker,Name layout (col0=category, col1=ticker)
    is_cat_ticker_name = (
        has_header and len(first_parts) >= 2 and
        first_parts[0] in {"category", "카테고리"} and
        first_parts[1] in {"ticker", "yahoo finance ticker", "종목코드", "티커", "symbol"}
    )

    new_items = []
    for line in (lines[1:] if has_header else lines):
        parts = [p.strip().strip('"').strip("'") for p in line.split(",")]
        if is_cat_ticker_name:
            cat = parts[0] if len(parts) > 0 else ""
            ticker = parts[1].upper() if len(parts) > 1 else ""
            nm = parts[2] if len(parts) > 2 else ""
        else:
            ticker = parts[0].upper() if parts else ""
            cat = ""
            nm = parts[1] if len(parts) > 1 else ""
        if ticker and ticker.lower() not in SKIP_HEADERS:
            new_items.append({"ticker": ticker, "category": cat, "name": nm})

    if not new_items:
        return jsonify({"error": "CSV에서 티커를 찾을 수 없습니다."}), 400

    ticker_list = load_tickers_from_csv()
    existing = {t["ticker"] for t in ticker_list}
    added = []
    for item in new_items:
        if item["ticker"] not in existing:
            ticker_list.append(item)
            existing.add(item["ticker"])
            added.append(item["ticker"])

    save_tickers_to_csv(ticker_list)
    return jsonify({"tickers": ticker_list, "added": added, "added_count": len(added)})


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
        "naver": bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET),
        "gmail_read": bool(SMTP_USER and SMTP_PASSWORD and settings.get("gmail_read_enabled") and settings.get("gmail_subject_filter", "").strip()),
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
    if "newsapi_query" in data:
        settings["newsapi_query"] = data["newsapi_query"]
    if "google_cse_query" in data:
        settings["google_cse_query"] = data["google_cse_query"]
    if "naver_query" in data:
        settings["naver_query"] = data["naver_query"]
    if "change_threshold" in data:
        try:
            val = float(data["change_threshold"])
            if 0 < val <= 100:
                settings["change_threshold"] = val
        except (TypeError, ValueError):
            pass
    if "gmail_read_enabled" in data:
        settings["gmail_read_enabled"] = bool(data["gmail_read_enabled"])
    if "gmail_subject_filter" in data:
        settings["gmail_subject_filter"] = str(data["gmail_subject_filter"])[:200]
    if "gmail_max_emails" in data:
        try:
            val = int(data["gmail_max_emails"])
            if 1 <= val <= 10:
                settings["gmail_max_emails"] = val
        except (TypeError, ValueError):
            pass
    for key in ["yahoo_finance_enabled", "newsapi_enabled", "google_cse_enabled", "naver_enabled"]:
        if key in data:
            settings[key] = bool(data[key])
    if "auto_schedule_enabled" in data:
        settings["auto_schedule_enabled"] = bool(data["auto_schedule_enabled"])
    if "auto_schedule_time" in data:
        raw_time = str(data["auto_schedule_time"]).strip()
        if re.match(r"^\d{2}:\d{2}$", raw_time):
            h, m = int(raw_time[:2]), int(raw_time[3:])
            if 0 <= h <= 23 and 0 <= m <= 59:
                settings["auto_schedule_time"] = raw_time
    save_settings(settings)
    _apply_schedule(settings)
    return jsonify(settings)


# ─── Schedule Status Route ────────────────────────────────────────────────────

@app.route("/api/schedule/status", methods=["GET"])
def schedule_status():
    """현재 스케줄러 상태와 다음 실행 시각을 반환한다."""
    settings = load_settings()
    return jsonify({
        "enabled": settings.get("auto_schedule_enabled", False),
        "time": settings.get("auto_schedule_time", "09:00"),
        "next_run": _get_next_run_time(),
        "scheduler_available": HAS_SCHEDULER,
    })


# ─── Gmail IMAP Test Route ─────────────────────────────────────────────────────

@app.route("/api/gmail/test", methods=["GET"])
def gmail_test():
    """Test Gmail IMAP reading. Returns the raw parsed emails for diagnostic purposes."""
    subject_filter = request.args.get("subject", "").strip()
    max_emails = min(int(request.args.get("max", 5)), 10)

    if not SMTP_USER or not SMTP_PASSWORD:
        return jsonify({"ok": False, "error": "SMTP_USER 또는 SMTP_PASSWORD가 설정되지 않았습니다."}), 400
    if not subject_filter:
        # Try with settings subject filter
        settings = load_settings()
        subject_filter = settings.get("gmail_subject_filter", "").strip()
    if not subject_filter:
        return jsonify({"ok": False, "error": "제목 필터를 입력하거나 설정에서 저장해 주세요."}), 400

    articles = read_gmail_by_subject(subject_filter, max_emails=max_emails)
    return jsonify({
        "ok": True,
        "account": SMTP_USER,
        "subject_filter": subject_filter,
        "count": len(articles),
        "articles": articles,
    })


# ─── Email Recipients Routes ──────────────────────────────────────────────────

@app.route("/api/email/recipients", methods=["GET"])
def get_email_recipients():
    settings = load_settings()
    extra_list = settings.get("email_recipients", [])
    return jsonify({
        "default_recipients": DEFAULT_EMAIL_RECIPIENTS,
        "extra_recipients": extra_list,
        "has_email_config": True,
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
    data = request.get_json()
    results = data.get("results", [])
    extra_to = data.get("extra_to", [])  # Additional one-time recipients from request
    market_data = data.get("market_data", None)
    all_stocks = data.get("all_stocks", None)
    category_stats = data.get("category_stats", None)

    if not results:
        return jsonify({"error": "전송할 분석 결과가 없습니다"}), 400

    # Build recipient list
    settings = load_settings()
    extra_list = settings.get("email_recipients", [])
    all_recipients = list(set(DEFAULT_EMAIL_RECIPIENTS + extra_list + extra_to))

    if not all_recipients:
        return jsonify({"error": "수신자 이메일이 없습니다. 수신자를 추가해주세요."}), 400

    # Build HTML email
    today = datetime.now(KST).strftime("%Y-%m-%d")
    subject = f"[Stock Analyzer] 주가 변동 분석 리포트 {today}"
    summary_html  = build_email_summary_html(results, today, market_data=market_data)
    detailed_html = build_email_html(results, today, market_data=market_data, all_stocks=all_stocks, category_stats=category_stats)

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM or SMTP_USER
        msg["To"] = ", ".join(all_recipients)
        msg.attach(MIMEText(summary_html, "html", "utf-8"))
        att = MIMEText(detailed_html, "html", "utf-8")
        att.add_header("Content-Disposition", "attachment", filename=f"analysis_{today}.html")
        msg.attach(att)

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


def build_email_html(results, date_str, market_data=None, all_stocks=None, category_stats=None):
    """Build HTML content for email report."""
    # ── 색 헬퍼 (흰 바탕 기준) ──────────────────────────────────────
    def chg_color(v):
        return "#c0392b" if v < 0 else "#27ae60" if v > 0 else "#555"

    # ── 글로벌 시장 지수 섹션 ─────────────────────────────────────────
    market_section = ""
    if market_data and market_data.get("indices"):
        idx_rows = ""
        for idx in market_data["indices"]:
            chg = idx.get("change_pct", 0)
            color = chg_color(chg)
            sign = "+" if chg > 0 else ""
            err_note = ' <small style="color:#999;">(오류)</small>' if idx.get("error") else ""
            idx_rows += (
                f'<tr>'
                f'<td style="padding:6px 12px;color:#333;">{idx["name"]}'
                f'<small style="color:#888;margin-left:4px;">({idx["region"]})</small></td>'
                f'<td style="padding:6px 12px;color:{color};font-weight:bold;">{sign}{chg:.2f}%{err_note}</td>'
                f'</tr>'
            )
        analysis_html = (market_data.get("analysis") or "").replace("\n", "<br>")
        market_section = f"""
        <h2 style="color:#1a56db;margin-top:24px;margin-bottom:8px;">글로벌 시장 지수</h2>
        <table style="width:100%;border-collapse:collapse;background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;overflow:hidden;margin-bottom:12px;">
            {idx_rows}
        </table>
        <div style="background:#f0f4ff;padding:12px;border-radius:6px;color:#333;line-height:1.6;margin-bottom:20px;border:1px solid #c7d7fc;">
            <strong style="color:#1a56db;">시장 분석</strong><br>{analysis_html}
        </div>
        """

    # ── 카테고리별 등락률 섹션 ────────────────────────────────────────
    category_section = ""
    if category_stats:
        sorted_cats = sorted(category_stats.items(), key=lambda x: x[1].get("avg", 0), reverse=True)
        cat_rows = ""
        for cat, stat in sorted_cats:
            avg = stat.get("avg", 0)
            color = chg_color(avg)
            sign = "+" if avg > 0 else ""
            count = stat.get("count", 0)
            cat_rows += (
                f'<tr>'
                f'<td style="padding:6px 12px;color:#333;font-weight:500;">{cat}</td>'
                f'<td style="padding:6px 12px;color:{color};font-weight:bold;">{sign}{avg:.2f}%</td>'
                f'<td style="padding:6px 12px;color:#666;font-size:12px;">{count}개 종목 평균</td>'
                f'</tr>'
            )
        category_section = f"""
        <h2 style="color:#1a56db;margin-top:24px;margin-bottom:8px;">카테고리별 등락률</h2>
        <table style="width:100%;border-collapse:collapse;background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;overflow:hidden;margin-bottom:20px;">
            <thead>
                <tr style="background:#e9ecef;">
                    <th style="padding:8px 12px;text-align:left;color:#495057;font-size:13px;">카테고리</th>
                    <th style="padding:8px 12px;text-align:left;color:#495057;font-size:13px;">평균 등락률</th>
                    <th style="padding:8px 12px;text-align:left;color:#495057;font-size:13px;">비고</th>
                </tr>
            </thead>
            <tbody>{cat_rows}</tbody>
        </table>
        """

    # ── 전체 종목 등락률 섹션 ─────────────────────────────────────────
    all_stocks_section = ""
    if all_stocks:
        # 카테고리별로 묶기
        by_cat = {}
        for ticker, info in all_stocks.items():
            cat = info.get("category") or "기타"
            by_cat.setdefault(cat, []).append((ticker, info))
        # 카테고리 순서: category_stats의 avg 순
        cat_order = sorted(by_cat.keys(), key=lambda c: (category_stats or {}).get(c, {}).get("avg", 0), reverse=True)

        all_rows = ""
        for cat in cat_order:
            items = by_cat[cat]
            items.sort(key=lambda x: x[1].get("change_pct", 0), reverse=True)
            # 카테고리 헤더 행
            all_rows += (
                f'<tr style="background:#e9ecef;">'
                f'<td colspan="3" style="padding:6px 12px;font-weight:bold;color:#343a40;font-size:13px;">{cat}</td>'
                f'</tr>'
            )
            for ticker, info in items:
                chg = info.get("change_pct", 0)
                color = chg_color(chg)
                sign = "+" if chg > 0 else ""
                err_note = ' <small style="color:#999;">(오류)</small>' if info.get("error") else ""
                all_rows += (
                    f'<tr style="border-bottom:1px solid #f0f0f0;">'
                    f'<td style="padding:5px 12px;color:#555;font-size:12px;">{ticker}</td>'
                    f'<td style="padding:5px 12px;color:#333;font-size:12px;">{info.get("name","")}</td>'
                    f'<td style="padding:5px 12px;color:{color};font-weight:bold;font-size:12px;">{sign}{chg:.2f}%{err_note}</td>'
                    f'</tr>'
                )

        all_stocks_section = f"""
        <h2 style="color:#1a56db;margin-top:24px;margin-bottom:8px;">전체 종목 등락률</h2>
        <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #dee2e6;border-radius:6px;overflow:hidden;margin-bottom:20px;">
            <thead>
                <tr style="background:#e9ecef;">
                    <th style="padding:8px 12px;text-align:left;color:#495057;font-size:12px;">티커</th>
                    <th style="padding:8px 12px;text-align:left;color:#495057;font-size:12px;">종목명</th>
                    <th style="padding:8px 12px;text-align:left;color:#495057;font-size:12px;">등락률</th>
                </tr>
            </thead>
            <tbody>{all_rows}</tbody>
        </table>
        """

    # ── 분석 결과 섹션 ────────────────────────────────────────────────
    rows = ""
    for r in results:
        sign = "+" if r["change_pct"] > 0 else ""
        color = chg_color(r["change_pct"])
        analysis_text = r.get("analysis", "").replace("\n", "<br>")
        news_badge = ""
        if r.get("articles_found"):
            news_badge = '<span style="background:#1a56db;color:#fff;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:6px;">뉴스 참고</span>'

        articles_html = ""
        if r.get("articles"):
            article_items = ""
            for a in r["articles"]:
                date_part = f'<span style="color:#999;font-size:10px;margin-right:6px;">{a["date"]}</span>' if a.get("date") else ""
                if a.get("link"):
                    article_items += f'<li style="padding:3px 0;">{date_part}<a href="{a["link"]}" style="color:#1a56db;">{a["title"]}</a> <small style="color:#999;">({a.get("source","")})</small></li>'
                else:
                    article_items += f'<li style="padding:3px 0;">{date_part}{a["title"]} <small style="color:#999;">({a.get("source","")})</small></li>'
            articles_html = f'<ul style="margin:8px 0 0 0;padding-left:16px;font-size:12px;color:#333;">{article_items}</ul>'

        rows += f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #e9ecef;">
                <strong style="color:#111;">{r['name']}</strong> <span style="color:#666;">({r['ticker']})</span>
                <span style="color:{color};font-weight:bold;margin-left:8px;">{sign}{r['change_pct']:.1f}%</span>
                {news_badge}
                <br><small style="color:#888;">{r.get('model_used', 'Gemini')}</small>
                <div style="margin-top:8px;color:#333;line-height:1.6;">{analysis_text}</div>
                {articles_html}
            </td>
        </tr>
        """

    analysis_section = ""
    if rows:
        analysis_section = f"""
        <h2 style="color:#1a56db;margin-bottom:8px;">변동 종목 분석</h2>
        <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #dee2e6;border-radius:6px;overflow:hidden;">
            {rows}
        </table>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="background:#ffffff;color:#111111;font-family:Arial,sans-serif;margin:0;padding:20px;">
        <div style="max-width:760px;margin:0 auto;">
            <h1 style="color:#1a56db;margin-bottom:4px;">Stock Movement Analyzer</h1>
            <p style="color:#555;margin-top:0;">분석 날짜: {date_str}</p>
            {market_section}
            {category_section}
            {all_stocks_section}
            {analysis_section}
            <p style="color:#aaa;font-size:12px;margin-top:16px;">This report was automatically generated by Stock Movement Analyzer.</p>
        </div>
    </body>
    </html>
    """


def build_email_summary_html(results, date_str, market_data=None):
    """Build concise Korean-style email body (맑은고딕 10.5pt).

    Positive changes shown in blue without a sign.
    Negative changes shown in red with △ instead of minus.
    Email body = brief summary; detailed HTML is sent as attachment.
    """
    FONT = "'맑은고딕','Malgun Gothic',sans-serif"
    SIZE = "10.5pt"
    BLUE = "#1a56db"
    RED  = "#c0392b"

    def fmt_chg(v, decimals=2):
        if v > 0:
            return f'<span style="color:{BLUE};">{v:.{decimals}f}%</span>'
        elif v < 0:
            return f'<span style="color:{RED};">△{abs(v):.{decimals}f}%</span>'
        return f'<span style="color:#555;">0.{"0"*decimals}%</span>'

    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        date_label = f"{dt.month:02d}/{dt.day:02d}"
    except Exception:
        date_label = date_str

    # Market groups
    market_html = ""
    if market_data and market_data.get("indices"):
        idx_map = {idx["ticker"]: idx for idx in market_data["indices"]}
        group_rows = []
        for group in EMAIL_MARKET_GROUPS:
            parts = []
            for ticker, label in group["indices"]:
                idx = idx_map.get(ticker)
                if idx:
                    parts.append(f"{label} ({fmt_chg(idx.get('change_pct', 0))})")
            if parts:
                group_rows.append(
                    f'<tr valign="top">'
                    f'<td style="padding:2px 12px 2px 0;white-space:nowrap;">{group["label"]} :&nbsp;</td>'
                    f'<td style="padding:2px 0;">{",&nbsp;&nbsp;".join(parts)}</td>'
                    f'</tr>'
                )
        if group_rows:
            market_html = (
                '<p style="margin:12px 0 4px 0;"><strong>시  장</strong></p>'
                '<table style="border:none;border-collapse:collapse;">'
                + "".join(group_rows)
                + "</table>"
            )

    # Individual companies
    def first_line(text):
        if not text:
            return "분석 결과 없음"
        for line in text.split("\n"):
            line = line.strip()
            if line:
                return line
        return text.strip()

    company_rows = []
    for r in results:
        v = r.get("change_pct", 0)
        name = r.get("name") or r.get("ticker", "")
        analysis = first_line(r.get("analysis", ""))
        company_rows.append(
            f'<tr valign="top">'
            f'<td style="padding:2px 12px 2px 0;white-space:nowrap;">{name} ({fmt_chg(v, decimals=1)})</td>'
            f'<td style="padding:2px 0;">: {analysis}</td>'
            f'</tr>'
        )

    companies_html = ""
    if company_rows:
        companies_html = (
            '<p style="margin:16px 0 4px 0;"><strong>개별회사</strong></p>'
            '<table style="border:none;border-collapse:collapse;">'
            + "".join(company_rows)
            + "</table>"
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:{FONT};font-size:{SIZE};color:#111;margin:0;padding:20px;line-height:1.6;">
<p style="margin:0 0 2px 0;">안녕하십니까,</p>
<p style="margin:0 0 16px 0;">{date_label}일 종가기준 모니터링 업체 현황 송부드립니다.</p>
{market_html}
{companies_html}
</body>
</html>"""


@app.route("/api/market-indices", methods=["GET"])
def get_market_indices():
    """Fetch global market indices and analyze with news."""
    date_str = request.args.get("date", "")
    model = request.args.get("model", "")

    target_date = None
    if date_str:
        try:
            target_date = date_cls.fromisoformat(date_str)
        except ValueError:
            pass
    if target_date is None:
        target_date = get_kst_today()

    if not model:
        settings = load_settings()
        model = settings.get("gemini_model", DEFAULT_GEMINI_MODEL)

    indices_data = fetch_all_market_indices(target_date)
    trade_date = next((idx["date"] for idx in indices_data if idx.get("date")), str(target_date))
    news_date_kst = str(target_date)  # News uses KST (user-selected date)

    articles_by_region = {}
    for region in MARKET_NEWS_REGIONS:
        articles = search_market_news_for_region(region, news_date_kst, target_date)
        if articles:
            articles_by_region[region] = articles

    analysis = analyze_market_indices_with_gemini(indices_data, articles_by_region, model=model)

    return jsonify({
        "indices": indices_data,
        "analysis": analysis,
        "date": trade_date,
        "articles_by_region": {
            region: [{"title": a["title"], "link": a.get("link", ""), "source": a.get("source", ""), "date": a.get("date", "")} for a in arts[:5]]
            for region, arts in articles_by_region.items()
        },
    })


@app.route("/api/analyze/stream", methods=["GET"])
def analyze_stream():
    """Streaming analysis - sends results in batches via SSE."""
    ticker_objects = load_tickers_from_csv()
    if not ticker_objects:
        return jsonify({"error": "No tickers saved."}), 400
    tickers = [t["ticker"] for t in ticker_objects]
    ticker_meta = {t["ticker"]: t for t in ticker_objects}

    settings = load_settings()
    model = request.args.get("model", settings.get("gemini_model", DEFAULT_GEMINI_MODEL))
    change_threshold = float(request.args.get("threshold", settings.get("change_threshold", 5.0)))

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

    # Per-source search queries (URL param overrides saved setting; fallback to legacy custom_query)
    _legacy = settings.get("custom_query", "")
    source_queries = {
        "newsapi":     request.args.get("newsapi_query", "").strip()     or settings.get("newsapi_query",     "") or _legacy,
        "google_cse":  request.args.get("google_cse_query", "").strip()  or settings.get("google_cse_query",  "") or _legacy,
        "naver":       request.args.get("naver_query", "").strip()       or settings.get("naver_query",       "") or _legacy,
    }

    def generate():
        log_memory("STREAM START")

        # Phase 0: Fetch global market indices + news + Gemini analysis
        yield f"data: {json.dumps({'type': 'progress', 'message': '글로벌 지수 수집 중...'})}\n\n"
        indices_data = fetch_all_market_indices(target_date)
        trade_date_for_market = next((idx["date"] for idx in indices_data if idx.get("date")), str(target_date))
        # News date uses KST (user-selected date) — the earliest timezone, so
        # articles for all regions are most likely to be available.
        news_date_kst = str(target_date)

        yield f"data: {json.dumps({'type': 'progress', 'message': '글로벌 지수 뉴스 검색 중...'})}\n\n"
        articles_by_region = {}
        for region in MARKET_NEWS_REGIONS:
            arts = search_market_news_for_region(region, news_date_kst, target_date)
            if arts:
                articles_by_region[region] = arts

        yield f"data: {json.dumps({'type': 'progress', 'message': '글로벌 지수 Gemini 분석 중...'})}\n\n"
        market_analysis = analyze_market_indices_with_gemini(indices_data, articles_by_region, model=model)

        articles_by_region_slim = {
            region: [{"title": a["title"], "link": a.get("link", ""), "source": a.get("source", ""), "date": a.get("date", "")} for a in arts[:5]]
            for region, arts in articles_by_region.items()
        }
        yield f"data: {json.dumps({'type': 'market_indices', 'indices': indices_data, 'analysis': market_analysis, 'date': trade_date_for_market, 'articles_by_region': articles_by_region_slim})}\n\n"
        gc.collect()

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
                meta = ticker_meta.get(ticker, {})
                all_stocks_slim[ticker] = {
                    "name": result.get("name") or meta.get("name") or ticker,
                    "change_pct": result.get("change_pct", 0),
                    "category": meta.get("category", ""),
                }
                if "error" in result:
                    all_stocks_slim[ticker]["error"] = result["error"]

                if abs(result.get("change_pct", 0)) >= change_threshold:
                    filtered_list.append((ticker, result))

            gc.collect()

        # Compute category averages
        category_stats = {}
        for tkr, info in all_stocks_slim.items():
            cat = info.get("category") or "기타"
            if cat not in category_stats:
                category_stats[cat] = {"total": 0.0, "count": 0, "tickers": []}
            category_stats[cat]["tickers"].append(tkr)
            if not info.get("error"):
                category_stats[cat]["total"] += info["change_pct"]
                category_stats[cat]["count"] += 1
        for cat in category_stats:
            s = category_stats[cat]
            s["avg"] = round(s["total"] / s["count"], 2) if s["count"] else 0
            del s["total"]

        # Send all_stocks data and category stats
        yield f"data: {json.dumps({'type': 'stocks', 'all_stocks': all_stocks_slim, 'category_stats': category_stats})}\n\n"

        log_memory("AFTER FETCH")

        if not filtered_list:
            yield f"data: {json.dumps({'type': 'done', 'message': f'변동률 {change_threshold:g}% 이상인 종목이 없습니다.'})}\n\n"
            return

        # Sort filtered list by ticker registration order (matches user-defined output order)
        ticker_order = {t["ticker"]: i for i, t in enumerate(ticker_objects)}
        filtered_list.sort(key=lambda x: ticker_order.get(x[0], 9999))

        yield f"data: {json.dumps({'type': 'progress', 'message': f'{len(filtered_list)}개 종목 분석 시작...'})}\n\n"

        # Phase 2: Analyze filtered stocks in batches
        total_analysis_batches = (len(filtered_list) + ANALYSIS_BATCH_SIZE - 1) // ANALYSIS_BATCH_SIZE

        # Fetch Gmail memos once before the per-stock loop (avoids repeated IMAP connections)
        _gmail_cache = None
        _gs = load_settings()
        if _gs.get("gmail_read_enabled") and _gs.get("gmail_subject_filter", "").strip():
            _gmail_cache = read_gmail_by_subject(
                _gs["gmail_subject_filter"].strip(),
                max_emails=int(_gs.get("gmail_max_emails", 3)),
            )

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
                        source_queries=source_queries,
                        gmail_articles=_gmail_cache,
                    )

                    # Step 2: Analyze with Gemini
                    yield f"data: {json.dumps({'type': 'progress', 'message': f'Gemini 분석 중... ({ticker})'})}\n\n"
                    result = analyze_with_gemini(
                        ticker, info, articles=articles, model=model,
                        prompt_templates=prompt_templates,
                    )

                    article_sources = list(set(a.get("source", "") for a in articles if a.get("source")))
                    used_articles = result["used_articles"]
                    batch_results.append({
                        "ticker": ticker,
                        "name": info.get("name", ticker),
                        "change_pct": info["change_pct"],
                        "analysis": result["analysis"],
                        "articles_found": len(articles) > 0,
                        "articles_count": len(articles),
                        "articles_sources": article_sources,
                        "articles": [
                            {
                                "title": a["title"],
                                "link": a.get("link", ""),
                                "source": a.get("source", ""),
                                "date": a.get("date", ""),
                                "snippet": a.get("snippet", ""),
                            }
                            for a in used_articles
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


def ticker_tz_hours(ticker):
    """Return UTC offset (hours) for a ticker based on its exchange suffix."""
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")) or t in ("^KS11", "^KQ11"):
        return 9   # KST
    if t.endswith(".T") or t == "^N225":
        return 9   # JST
    if t.endswith((".SS", ".SZ")) or t == "000001.SS":
        return 8   # CST
    if t.endswith(".HK") or t == "^HSI":
        return 8   # HKT
    return 0       # UTC (US, Europe 등)


def fetch_single_ticker(ticker_symbol, target_date=None, tz_hours=None):
    """Fetch a single ticker's data from Yahoo Finance API.

    tz_hours: UTC offset of the exchange (e.g. 9 for KST/JST, 8 for CST/HKT).
    Bar dates are evaluated in local time so 3/16 always means 3/16 locally.
    If None, auto-detected from ticker suffix.
    """
    if tz_hours is None:
        tz_hours = ticker_tz_hours(ticker_symbol)
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

        # Deduplicate: keep the last bar per UTC date
        # (Yahoo Finance sometimes returns 2 bars for the same day, e.g. open + close snapshot)
        seen: dict = {}
        for ts, c in valid:
            seen[datetime.utcfromtimestamp(ts).date()] = (ts, c)
        valid = sorted(seen.values())

        if target_date:
            filtered = [(ts, c) for ts, c in valid
                        if datetime.utcfromtimestamp(ts).date() <= target_date]
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
        last_date = datetime.utcfromtimestamp(valid[-1][0]).date()
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


def fetch_all_market_indices(target_date=None):
    """Fetch all global market indices data."""
    results = []
    for idx in MARKET_INDICES:
        data = fetch_single_ticker(idx["ticker"], target_date=target_date, tz_hours=idx.get("tz_hours", 0))
        results.append({
            "ticker": idx["ticker"],
            "name": idx["name"],
            "region": idx["region"],
            "change_pct": data.get("change_pct", 0),
            "date": data.get("date", ""),
            "error": data.get("error", ""),
        })
    return results


def search_market_news_for_region(region, trade_date, target_date=None):
    """Search news articles for a global market region."""
    cfg = MARKET_NEWS_REGIONS.get(region)
    if not cfg:
        return []
    ticker = cfg["ticker"]
    query = cfg["query"]
    try:
        return search_all_news_articles(
            ticker, query, trade_date,
            target_date=target_date,
            custom_query=query,
        )
    except Exception:
        return []


def analyze_market_indices_with_gemini(indices_data, articles_by_region, model=None):
    """Analyze global market index movements using Gemini and collected news."""
    if not GEMINI_API_KEY:
        return "Gemini API key not configured."
    client = get_gemini_client()
    if not client:
        return "Gemini client initialization failed."
    if model is None:
        settings = load_settings()
        model = settings.get("gemini_model", DEFAULT_GEMINI_MODEL)

    trade_date = next((idx["date"] for idx in indices_data if idx.get("date")), "")
    indices_text = "\n".join(
        f"  {idx['name']} ({idx['region']}): {'+' if idx['change_pct'] > 0 else ''}{idx['change_pct']:.2f}%"
        + (f"  [오류]" if idx.get("error") else "")
        for idx in indices_data
    )

    articles_text_parts = []
    for region, articles in articles_by_region.items():
        if articles:
            articles_text_parts.append(f"\n[{region} 뉴스]")
            for i, a in enumerate(articles[:5]):
                line = f"  [{i+1}] [{a.get('source', '')}] {a['title']}"
                if a.get("snippet"):
                    line += f"\n      {a['snippet']}"
                articles_text_parts.append(line)
    articles_text = "\n".join(articles_text_parts) if articles_text_parts else "수집된 뉴스 없음"

    prompt = f"""You are a global financial market analyst.

날짜: {trade_date}

주요 글로벌 지수 등락:
{indices_text}

수집된 뉴스 기사:
{articles_text}

Instructions:
1. 아래 순서대로 각 지역을 반드시 빈 줄로 구분하여 출력해라:
   1) 미국  2) 한국  3) 중국/홍콩  4) 일본  5) 유럽
2. 각 지역 형식 (반드시 이 형식 준수):
   🇺🇸 미국: <한글 분석 1-2문장>
   🇺🇸 US: <English translation>

   🇰🇷 한국: <한글 분석>
   🇰🇷 Korea: <English>

   🇨🇳 중국/홍콩: <한글 분석>
   🇨🇳 China/HK: <English>

   🇯🇵 일본: <한글 분석>
   🇯🇵 Japan: <English>

   🇪🇺 유럽: <한글 분석>
   🇪🇺 Europe: <English>
3. 뉴스 근거가 없는 지역은 해당 지역 아래에 "정보 없음 / No data"로 표시해라.
4. 추측하거나 자체 지식을 사용하지 마라. 오직 제공된 기사 내용만 활용해라.
5. 마지막에 빈 줄 후 전체 시장 분위기를 1문장으로 요약 (한글/영문):
   📊 요약: <한글>
   📊 Summary: <English>"""

    try:
        response = client.models.generate_content(model=model, contents=prompt)
        return response.text
    except Exception as e:
        return f"분석 실패: {str(e)}"


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


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def search_news_articles_naver(company_name, ticker="", target_date=None, custom_query=None):
    """Search Korean news from Naver Search API (requires NAVER_CLIENT_ID + NAVER_CLIENT_SECRET).

    Returns articles sorted by publish date (newest first).
    Naver is especially useful for Korean stocks and market indices (코스피/코스닥).
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    try:
        query = build_search_query(custom_query, company_name, ticker, company_name)
        url = "https://openapi.naver.com/v1/search/news.json"
        params = {"query": query, "display": 5, "sort": "date"}
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Naver Search API error {resp.status_code} for '{query}'")
            return []

        from email.utils import parsedate as _parsedate
        articles = []
        for item in resp.json().get("items", []):
            title = _HTML_TAG_RE.sub("", item.get("title", "")).strip()
            if not title:
                continue
            snippet = _HTML_TAG_RE.sub("", item.get("description", "")).strip()
            link = item.get("originallink") or item.get("link", "")
            pub_date = ""
            raw_date = item.get("pubDate", "")
            if raw_date:
                try:
                    parsed = _parsedate(raw_date)
                    if parsed:
                        pub_date = f"{parsed[0]:04d}-{parsed[1]:02d}-{parsed[2]:02d}"
                except Exception:
                    pass
            articles.append({
                "title": title,
                "snippet": snippet,
                "link": link,
                "source": "Naver",
                "date": pub_date,
            })
        logger.info(f"Naver: found {len(articles)} articles for '{query}'")
        return articles
    except Exception as e:
        logger.error(f"Naver Search error for '{company_name}': {e}")
        return []


def _filter_gmail_memos_for_ticker(memos, ticker, company_name=""):
    """Return memos that mention the ticker or company name.
    If none match, return all memos (general memo fallback)."""
    if not memos:
        return []
    ticker_up = ticker.upper()
    name_words = [w.upper() for w in company_name.split() if len(w) >= 4]

    def matches(memo):
        text = f"{memo.get('title', '')} {memo.get('body', memo.get('snippet', ''))}".upper()
        if ticker_up in text:
            return True
        return any(w in text for w in name_words)

    matched = [m for m in memos if matches(m)]
    return matched if matched else memos  # fallback: no match → include all


def read_gmail_by_subject(subject_filter, max_emails=3):
    """Read emails from the registered Gmail account (SMTP_USER) via IMAP.

    Returns articles list (same dict format as other news sources) for emails
    whose subject contains subject_filter. Requires IMAP to be enabled in Gmail settings.
    """
    if not SMTP_USER or not SMTP_PASSWORD or not subject_filter:
        return []
    import imaplib
    import email as _email
    from email.header import decode_header as _decode_header

    def _decode_str(value):
        """Decode MIME-encoded header string."""
        if value is None:
            return ""
        parts = _decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return "".join(decoded)

    def _extract_text(msg):
        """Extract plain text body from email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if ct == "text/plain" and "attachment" not in cd:
                    charset = part.get_content_charset() or "utf-8"
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                    break
        else:
            ct = msg.get_content_type()
            if ct == "text/plain":
                charset = msg.get_content_charset() or "utf-8"
                body = msg.get_payload(decode=True).decode(charset, errors="replace")
            elif ct == "text/html":
                charset = msg.get_content_charset() or "utf-8"
                raw = msg.get_payload(decode=True).decode(charset, errors="replace")
                body = _HTML_TAG_RE.sub(" ", raw).strip()
        return body.strip()

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap.login(SMTP_USER, SMTP_PASSWORD)
        imap.select("INBOX")

        # Search for emails matching subject filter
        _, data = imap.search(None, f'SUBJECT "{subject_filter}"')
        uids = data[0].split()
        if not uids:
            imap.logout()
            logger.info(f"Gmail IMAP: no emails found with subject '{subject_filter}'")
            return []

        # Take the most recent max_emails
        recent_uids = uids[-max_emails:][::-1]
        articles = []
        for uid in recent_uids:
            _, msg_data = imap.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = _email.message_from_bytes(raw)
            subject = _decode_str(msg.get("Subject", "")).strip()
            date_str = msg.get("Date", "")
            pub_date = ""
            if date_str:
                try:
                    from email.utils import parsedate as _pd
                    parsed = _pd(date_str)
                    if parsed:
                        pub_date = f"{parsed[0]:04d}-{parsed[1]:02d}-{parsed[2]:02d}"
                except Exception:
                    pass
            body = _extract_text(msg)
            # Use up to 3000 chars of the body as snippet (preserving line breaks for Gemini context)
            snippet = body[:3000].strip() if body else ""
            if subject or snippet:
                articles.append({
                    "title": subject or f"Gmail 메모 ({pub_date})",
                    "snippet": snippet,
                    "body": body,  # full body for test/diagnostic endpoint
                    "link": "",
                    "source": "Gmail 메모",
                    "date": pub_date,
                })

        imap.logout()
        logger.info(f"Gmail IMAP: found {len(articles)} emails with subject '{subject_filter}'")
        return articles

    except imaplib.IMAP4.error as e:
        logger.error(f"Gmail IMAP auth/search error: {e}")
        return []
    except Exception as e:
        logger.error(f"Gmail IMAP error: {e}")
        return []


def search_all_news_articles(ticker, company_name, trade_date, target_date=None, source_queries=None, gmail_articles=None):
    """Search news from all available sources and merge results.

    Priority: Yahoo Finance (always) → NewsAPI → Google CSE → Naver (Korean news) → Gmail memos
    source_queries: dict with per-source query templates, e.g.
        {"newsapi": "...", "google_cse": "...", "naver": "..."}
    gmail_articles: pre-fetched memo list (cached); if None, fetches from IMAP directly.
    Returns up to 10 deduplicated articles.
    """
    all_articles = []
    settings = load_settings()
    sq = source_queries or {}

    # 1. Yahoo Finance — always available (uses ticker directly, not text query)
    if settings.get("yahoo_finance_enabled", True):
        all_articles.extend(search_news_articles_yahoo_finance(ticker, company_name))

    # 2. NewsAPI — optional
    if NEWS_API_KEY and settings.get("newsapi_enabled", True):
        all_articles.extend(search_news_articles_newsapi(ticker, company_name, target_date=target_date, custom_query=sq.get("newsapi", "")))

    # 3. Google CSE — optional
    if GOOGLE_API_KEY and GOOGLE_CSE_ID and settings.get("google_cse_enabled", True):
        all_articles.extend(search_news_articles_google(ticker, company_name, trade_date, target_date=target_date, custom_query=sq.get("google_cse", "")))

    # 4. Naver — optional (Korean news, especially useful for KS/KQ tickers and Korean market indices)
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET and settings.get("naver_enabled", True):
        all_articles.extend(search_news_articles_naver(company_name, ticker=ticker, target_date=target_date, custom_query=sq.get("naver", "")))

    # 5. Gmail 메모 — optional (user's own memos sent to registered Gmail account)
    if gmail_articles is not None:
        # Use pre-cached memos, filtered to this ticker/company
        all_articles.extend(_filter_gmail_memos_for_ticker(gmail_articles, ticker, company_name))
    else:
        # Fallback: fetch directly from IMAP (used when no cache provided, e.g. market index search)
        if settings.get("gmail_read_enabled") and settings.get("gmail_subject_filter", "").strip():
            max_emails = int(settings.get("gmail_max_emails", 3))
            all_articles.extend(read_gmail_by_subject(settings["gmail_subject_filter"].strip(), max_emails=max_emails))

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
    return unique


def analyze_with_gemini(ticker, stock_info, articles=None, model=None, prompt_templates=None):
    """Use Gemini to analyze stock price movement using customizable prompt templates.

    Template variables: {name}, {ticker}, {change_pct}, {trade_date}
    Additional for with_articles: {articles_text}, {sources_label}, {articles_count}
    """
    if not GEMINI_API_KEY:
        return {"analysis": "Gemini API key not configured.", "used_articles": []}

    client = get_gemini_client()
    if not client:
        return {"analysis": "Gemini client initialization failed.", "used_articles": []}

    if model is None:
        settings = load_settings()
        model = settings.get("gemini_model", DEFAULT_GEMINI_MODEL)

    name = stock_info.get("name", ticker)
    change_pct = stock_info["change_pct"]
    trade_date = stock_info.get("date", "")

    templates = prompt_templates or {}

    if not articles:
        # No articles from the analysis date → no speculation, return directly
        return {"analysis": "개별이슈 미발견.", "used_articles": []}

    sources_used = list(set(a.get("source", "") for a in articles if a.get("source")))
    sources_label = ", ".join(sources_used) if sources_used else "외부 검색"
    articles_text = "\n".join([
        f"  [{i+1}] [{a.get('source', '')}] {a['title']}" +
        (f"\n      {a['snippet']}" if a.get('snippet') else "")
        for i, a in enumerate(articles)
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
        text = response.text.strip()
        # Parse REFS:[...] tag that Gemini outputs to identify relevant articles
        refs_match = re.search(r'REFS:\[([^\]]*)\]', text)
        used_articles = articles  # fallback: show all if tag absent
        if refs_match:
            raw = refs_match.group(1)
            idxs = [int(x.strip()) - 1 for x in raw.split(',') if x.strip().isdigit()]
            valid = [articles[i] for i in idxs if 0 <= i < len(articles)]
            used_articles = valid[:3]  # top 3 most relevant articles
            text = text[:refs_match.start()].strip()
        return {"analysis": text, "used_articles": used_articles}
    except Exception as e:
        return {"analysis": f"분석 실패: {str(e)}", "used_articles": []}


# ─── Scheduled / Webhook Analysis ────────────────────────────────────────────

def run_scheduled_analysis(target_date=None):
    """비스트리밍 전체 분석 실행 후 이메일 발송. 웹훅에서 호출."""
    try:
        logger.info("Scheduled analysis started")
        if target_date is None:
            target_date = get_kst_today()

        settings = load_settings()
        model = settings.get("gemini_model", DEFAULT_GEMINI_MODEL)
        change_threshold = float(settings.get("change_threshold", 5.0))
        _legacy_q = settings.get("custom_query", "")
        source_queries = {
            "newsapi":    settings.get("newsapi_query",    "") or _legacy_q,
            "google_cse": settings.get("google_cse_query", "") or _legacy_q,
            "naver":      settings.get("naver_query",      "") or _legacy_q,
        }
        prompt_templates = {
            "with_articles": settings.get("prompt_with_articles") or DEFAULT_PROMPT_WITH_ARTICLES,
            "without_articles": settings.get("prompt_without_articles") or DEFAULT_PROMPT_WITHOUT_ARTICLES,
        }

        # Phase 0: 글로벌 지수
        logger.info("Fetching market indices...")
        indices_data = fetch_all_market_indices(target_date=None)  # always use Yahoo's latest available data
        trade_date_str = next((i["date"] for i in indices_data if i.get("date")), str(target_date))
        # News date uses KST (target_date) — earliest timezone, so articles are most likely available
        news_date_kst = str(target_date)
        articles_by_region = {}
        for region in MARKET_NEWS_REGIONS:
            arts = search_market_news_for_region(region, news_date_kst, target_date)
            if arts:
                articles_by_region[region] = arts
        market_analysis = analyze_market_indices_with_gemini(indices_data, articles_by_region, model=model)
        market_data = {"indices": indices_data, "analysis": market_analysis, "date": trade_date_str}
        gc.collect()

        # Phase 1: 종목 데이터 수집 + 필터링
        logger.info("Fetching ticker data...")
        ticker_objects = load_tickers_from_csv()
        ticker_meta = {t["ticker"]: t for t in ticker_objects}
        all_stocks = {}
        filtered_list = []
        for t in ticker_objects:
            result = fetch_single_ticker(t["ticker"], target_date=target_date)
            all_stocks[t["ticker"]] = {
                "name": result.get("name") or t.get("name") or t["ticker"],
                "change_pct": result.get("change_pct", 0),
                "category": t.get("category", ""),
            }
            if "error" in result:
                all_stocks[t["ticker"]]["error"] = result["error"]
            if abs(result.get("change_pct", 0)) >= change_threshold:
                filtered_list.append((t["ticker"], result))
        filtered_list.sort(key=lambda x: abs(x[1].get("change_pct", 0)), reverse=True)
        # 카테고리별 평균 등락률 계산
        category_stats = {}
        for tkr, info in all_stocks.items():
            cat = info.get("category") or "기타"
            if cat not in category_stats:
                category_stats[cat] = {"total": 0.0, "count": 0}
            if not info.get("error"):
                category_stats[cat]["total"] += info["change_pct"]
                category_stats[cat]["count"] += 1
        for cat in category_stats:
            s = category_stats[cat]
            s["avg"] = round(s["total"] / s["count"], 2) if s["count"] else 0
            del s["total"]
        logger.info(f"Filtered {len(filtered_list)} tickers above threshold {change_threshold}%")
        gc.collect()

        # Phase 2: 종목별 뉴스 + Gemini 분석
        # Fetch Gmail memos once before the per-stock loop (avoids repeated IMAP connections)
        _gmail_cache = None
        _gs = load_settings()
        if _gs.get("gmail_read_enabled") and _gs.get("gmail_subject_filter", "").strip():
            _gmail_cache = read_gmail_by_subject(
                _gs["gmail_subject_filter"].strip(),
                max_emails=int(_gs.get("gmail_max_emails", 3)),
            )

        results = []
        for ticker, info in filtered_list:
            try:
                articles = search_all_news_articles(
                    ticker, info.get("name", ticker), info.get("date", ""),
                    target_date=target_date, source_queries=source_queries,
                    gmail_articles=_gmail_cache,
                )
                result = analyze_with_gemini(ticker, info, articles=articles, model=model, prompt_templates=prompt_templates)
                meta = ticker_meta.get(ticker, {})
                results.append({
                    "ticker": ticker,
                    "name": info.get("name", ticker) or meta.get("name", ticker),
                    "change_pct": info["change_pct"],
                    "analysis": result["analysis"],
                    "articles_found": len(articles) > 0,
                    "articles_count": len(articles),
                    "articles_sources": list(set(a.get("source", "") for a in articles if a.get("source"))),
                    "articles": [{"title": a["title"], "link": a.get("link", ""), "source": a.get("source", ""), "date": a.get("date", ""), "snippet": a.get("snippet", "")} for a in result["used_articles"]],
                    "model_used": model,
                })
            except Exception as e:
                logger.error(f"Scheduled analysis error for {ticker}: {e}")
            gc.collect()

        # Phase 3: 이메일 전송
        date_str = str(target_date)
        summary_html  = build_email_summary_html(results, date_str, market_data=market_data)
        detailed_html = build_email_html(results, date_str, market_data=market_data, all_stocks=all_stocks, category_stats=category_stats)
        all_recipients = list(set(DEFAULT_EMAIL_RECIPIENTS + settings.get("email_recipients", [])))
        if not all_recipients:
            logger.warning("Scheduled analysis: no recipients configured, skipping email")
            return {"status": "done", "sent": False, "reason": "no recipients", "results_count": len(results)}

        subject = f"[Stock Analyzer] 주가 변동 분석 리포트 {date_str}"
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = ", ".join(all_recipients)
        msg.attach(MIMEText(summary_html, "html", "utf-8"))
        att = MIMEText(detailed_html, "html", "utf-8")
        att.add_header("Content-Disposition", "attachment", filename=f"analysis_{date_str}.html")
        msg.attach(att)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, all_recipients, msg.as_string())

        logger.info(f"Scheduled analysis complete: {len(results)} results, email sent to {all_recipients}")
        return {"status": "done", "sent": True, "recipients": all_recipients, "results_count": len(results), "date": date_str}

    except Exception as e:
        logger.error(f"Scheduled analysis failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def _get_or_create_webhook_token():
    """settings.json에 웹훅 토큰이 없으면 생성해서 저장."""
    settings = load_settings()
    token = settings.get("webhook_token", "")
    if not token:
        token = secrets.token_urlsafe(32)
        settings["webhook_token"] = token
        save_settings(settings)
    return token


@app.route("/api/gcs/status", methods=["GET"])
def gcs_status():
    """GCS 연동 상태 확인."""
    return jsonify({
        "configured": bool(HAS_GCS and GCS_BUCKET_NAME),
        "bucket": GCS_BUCKET_NAME or None,
    })


@app.route("/api/gcs/save", methods=["POST"])
def gcs_save():
    """현재 settings.json + tickers.csv를 GCS에 업로드."""
    if not GCS_BUCKET_NAME:
        return jsonify({"error": "GCS_BUCKET_NAME 환경변수가 설정되지 않았습니다"}), 400
    if not HAS_GCS:
        return jsonify({"error": "google-cloud-storage 패키지가 설치되지 않았습니다"}), 500

    results = {}
    if os.path.exists(SETTINGS_FILE):
        results["settings.json"] = gcs_upload(SETTINGS_FILE, "settings.json")
    else:
        results["settings.json"] = False

    if os.path.exists(TICKERS_CSV_FILE):
        results["tickers.csv"] = gcs_upload(TICKERS_CSV_FILE, "tickers.csv")
    else:
        results["tickers.csv"] = False

    success = all(results.values())
    return jsonify({"success": success, "uploaded": results, "bucket": GCS_BUCKET_NAME}), (200 if success else 500)


@app.route("/api/webhook/info", methods=["GET"])
def webhook_info():
    """웹훅 토큰 및 사용 방법 안내."""
    token = _get_or_create_webhook_token()
    return jsonify({
        "token": token,
        "endpoint": "/api/webhook/run-analysis",
        "method": "POST",
        "header": f"X-Webhook-Token: {token}",
        "note": "Google Cloud Scheduler: POST to <your-url>/api/webhook/run-analysis with header X-Webhook-Token",
    })


@app.route("/api/webhook/token/regenerate", methods=["POST"])
def regenerate_webhook_token():
    """토큰 재생성."""
    settings = load_settings()
    settings["webhook_token"] = secrets.token_urlsafe(32)
    save_settings(settings)
    return jsonify({"token": settings["webhook_token"]})


@app.route("/api/webhook/run-analysis", methods=["POST"])
def webhook_run_analysis():
    """외부 스케줄러(Google Cloud Scheduler 등)에서 호출하는 웹훅 엔드포인트."""
    # 토큰 검증
    expected_token = _get_or_create_webhook_token()
    provided_token = request.headers.get("X-Webhook-Token", "")
    if not provided_token or provided_token != expected_token:
        return jsonify({"error": "Unauthorized"}), 401

    # 날짜 파라미터 (없으면 오늘 KST)
    date_str = request.args.get("date", "") or (request.get_json(silent=True) or {}).get("date", "")
    target_date = None
    if date_str:
        try:
            target_date = date_cls.fromisoformat(date_str)
        except ValueError:
            pass

    result = run_scheduled_analysis(target_date=target_date)
    return jsonify(result)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
