import os
import gc
import re
import csv
import io
import json
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone, date as date_cls
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, jsonify, Response, send_file
import requests
from google import genai
from dotenv import load_dotenv

import fundamentals

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


def gcs_upload_json(obj, blob_name):
    """Upload a JSON-serializable object directly to GCS (no local temp file)."""
    _, bucket = _gcs_bucket()
    if bucket is None:
        return False
    try:
        blob = bucket.blob(blob_name)
        blob.upload_from_string(json.dumps(obj, ensure_ascii=False), content_type="application/json")
        logger.info(f"GCS: uploaded JSON -> gs://{GCS_BUCKET_NAME}/{blob_name}")
        return True
    except Exception as e:
        logger.error(f"GCS JSON upload error ({blob_name}): {e}")
        return False


def gcs_download_json(blob_name):
    """Download and parse a JSON blob from GCS. Returns dict or None."""
    _, bucket = _gcs_bucket()
    if bucket is None:
        return None
    try:
        blob = bucket.blob(blob_name)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception as e:
        logger.error(f"GCS JSON download error ({blob_name}): {e}")
        return None


def gcs_list_blob_names(prefix):
    """List blob names under a given prefix. Returns [] on failure."""
    client, bucket = _gcs_bucket()
    if bucket is None:
        return []
    try:
        return [b.name for b in client.list_blobs(GCS_BUCKET_NAME, prefix=prefix)]
    except Exception as e:
        logger.error(f"GCS list_blobs error (prefix={prefix}): {e}")
        return []


RESULTS_POINTER_BLOB = "results/_pointer.json"
RESULTS_DATE_BLOB_RE = re.compile(r"^results/(\d{4}-\d{2}-\d{2})\.json$")


def save_results_to_gcs(date_str, payload, source="scheduled"):
    """Persist one run's full result payload to GCS as the new default,
    keyed by date. No-ops (with a warning) if GCS isn't configured — never
    raises, so callers (scheduled run, manual save endpoint) stay safe."""
    if not GCS_BUCKET_NAME:
        logger.warning("save_results_to_gcs: GCS_BUCKET_NAME not configured, skipping")
        return False
    record = dict(payload)
    record["date"] = date_str
    record["saved_at"] = datetime.now(KST).isoformat()
    record["source"] = source
    ok = gcs_upload_json(record, f"results/{date_str}.json")
    if ok:
        gcs_upload_json(
            {"current_date": date_str, "updated_at": record["saved_at"], "source": source},
            RESULTS_POINTER_BLOB,
        )
    return ok


def load_results_from_gcs(date_str):
    """Load one saved date's result payload. Returns dict or None."""
    if not GCS_BUCKET_NAME:
        return None
    return gcs_download_json(f"results/{date_str}.json")


def load_latest_results_from_gcs():
    """Load whatever the current default-pointer date's result is.
    Returns dict or None if nothing has been saved yet."""
    if not GCS_BUCKET_NAME:
        return None
    pointer = gcs_download_json(RESULTS_POINTER_BLOB)
    if not pointer or not pointer.get("current_date"):
        return None
    return load_results_from_gcs(pointer["current_date"])


def list_saved_result_dates():
    """Return saved result dates, most recent first."""
    if not GCS_BUCKET_NAME:
        return []
    names = gcs_list_blob_names("results/")
    dates = []
    for name in names:
        m = RESULTS_DATE_BLOB_RE.match(name)
        if m:
            dates.append(m.group(1))
    return sorted(dates, reverse=True)


def apply_fundamentals_overrides(date_str, overrides):
    """Merge {ticker: {field: value}} overrides (from an uploaded Excel/CSV,
    parsed client-side) into whichever fundamentals data currently represents
    date_str. Returns (updated_fundamentals_dict, error_message_or_None).

    Two targets, matching where date_str's data currently lives:
    - A live/just-completed manual run still in `_manual_run_cache`: merge in
      memory only — the existing "저장" button persists it to GCS later.
    - An already-saved GCS blob (historical date): patch and re-upload it
      immediately, since there's no separate "save" step for that case.
      Deliberately does NOT touch the default-pointer blob — editing a past
      date's fundamentals shouldn't make it the new homepage default.
    """
    cached = _manual_run_cache.get(date_str)
    if cached is not None:
        target = cached
        persist_now = False
    else:
        if not GCS_BUCKET_NAME:
            return None, "GCS_BUCKET_NAME 환경변수가 설정되지 않아 과거 저장본을 불러올 수 없습니다"
        target = load_results_from_gcs(date_str)
        if target is None:
            return None, "해당 날짜에 저장된 결과가 없습니다. 먼저 분석을 실행하거나 날짜를 확인해주세요."
        persist_now = True

    fundamentals_dict = target.setdefault("fundamentals", {})
    for ticker, fields in overrides.items():
        rec = fundamentals_dict.setdefault(
            ticker, {key: None for key, _, _, _ in fundamentals.EXCEL_COLUMNS}
        )
        rec.update(fields)

    if persist_now:
        target["date"] = date_str
        target["saved_at"] = datetime.now(KST).isoformat()
        gcs_upload_json(target, f"results/{date_str}.json")

    return target["fundamentals"], None


def startup_sync_from_gcs():
    """On app start, pull settings.json and tickers.csv from GCS when configured.

    Never overwrite a user's saved ticker list just because it differs from
    DEFAULT_TICKERS. DEFAULT_TICKERS are only a first-run bootstrap.
    """
    if not GCS_BUCKET_NAME:
        return
    logger.info(f"GCS: syncing config from bucket '{GCS_BUCKET_NAME}'...")
    gcs_download("settings.json", SETTINGS_FILE)
    gcs_download("tickers.csv", TICKERS_CSV_FILE)

    logger.info("GCS: startup sync complete")


# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID", "620f073b5bf414784")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# Email configuration.  The Cloud Run deployment for this service was set up
# with an "EMAIL_*" naming convention (EMAIL_SMTP_SERVER, EMAIL_SENDER,
# EMAIL_PASSWORD, ...) instead of the SMTP_* names documented in
# .env.example, so accept either — SMTP_* wins if both are set.
SMTP_HOST = os.getenv("SMTP_HOST") or os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT") or os.getenv("EMAIL_SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER") or os.getenv("EMAIL_SENDER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM") or os.getenv("EMAIL_SENDER") or SMTP_USER
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
    {"ticker": "BIIB",       "category": "바이오", "name": "Biogen"},
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

# In-process cache of the most recent manual "분석 실행" run, keyed by date_str.
# Populated incrementally by analyze_stream() as it streams (GET, unaffected by
# the corporate network's large-POST-body block). The "결과 저장"/"이메일 전송"
# buttons then only need to POST the date, not the full result blob, avoiding
# the same 403 block documented in this repo's history for POST bodies.
# Safe as a single process-wide dict: gunicorn runs --workers 1 --threads 1.
_manual_run_cache = {}

# Batch settings
FETCH_BATCH_SIZE = 30  # Fetch tickers in batches
ANALYSIS_BATCH_SIZE = 3  # Analyze filtered stocks in batches
FUNDAMENTALS_BATCH_SIZE = 10  # Fetch fundamentals (KRX/DART/FMP) in batches

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


def get_kst_yesterday():
    """Return yesterday's date in KST (UTC+9)."""
    return (datetime.now(KST) - timedelta(days=1)).date()


# ─── Settings Management ──────────────────────────────────────────────────────

def load_settings():
    """Load settings from JSON file."""
    defaults = {
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "email_recipients": [],
        "prompt_with_articles": DEFAULT_PROMPT_WITH_ARTICLES,
        "prompt_without_articles": DEFAULT_PROMPT_WITHOUT_ARTICLES,
        "custom_query": "",
        # News source-specific search queries
        "query_yahoo": "{name} {ticker} stock",
        "query_newsapi": "{name} stock OR {ticker}",
        "query_google": "{name} stock {ticker}",
        "query_naver": "{name} 주가",
        # Global market indices queries
        "query_market_us": "US stock market Dow Jones Nasdaq",
        "query_market_korea": "코스피 코스닥 주식시장",
        "query_market_china": "China Shanghai stock market",
        "query_market_hongkong": "Hong Kong Hang Seng stock market",
        "query_market_japan": "Japan Nikkei stock market",
        "query_market_europe": "European stock market FTSE DAX",
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
        if GCS_BUCKET_NAME:
            gcs_upload(SETTINGS_FILE, "settings.json")
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
            run_scheduled_analysis,
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
    if GCS_BUCKET_NAME:
        gcs_upload(TICKERS_CSV_FILE, "tickers.csv")


def _normalize_ticker_item(item):
    return {
        "ticker": (item.get("ticker", "") or "").strip().upper(),
        "category": (item.get("category", "") or "").strip(),
        "name": (item.get("name", "") or "").strip(),
    }


def persist_and_reload_tickers(ticker_list):
    """Persist tickers and verify the reloaded server-side list matches."""
    normalized_expected = [_normalize_ticker_item(item) for item in ticker_list]
    save_tickers_to_csv(normalized_expected)
    reloaded = [_normalize_ticker_item(item) for item in load_tickers_from_csv()]
    if reloaded != normalized_expected:
        raise IOError("Ticker persistence verification failed")
    return reloaded


# ─── GCS Startup Sync ─────────────────────────────────────────────────────────
# Note: _apply_schedule is called after run_scheduled_analysis is defined (at end of file)
startup_sync_from_gcs()

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
    try:
        saved_tickers = persist_and_reload_tickers(ticker_list)
    except Exception as e:
        logger.error(f"Failed to persist ticker {ticker}: {e}")
        return jsonify({"error": "Ticker 저장에 실패했습니다"}), 500
    return jsonify({"tickers": saved_tickers})


@app.route("/api/tickers/<ticker>", methods=["DELETE"])
def delete_ticker(ticker):
    ticker = ticker.upper()
    ticker_list = load_tickers_from_csv()
    ticker_list = [t for t in ticker_list if t["ticker"] != ticker]
    try:
        saved_tickers = persist_and_reload_tickers(ticker_list)
    except Exception as e:
        logger.error(f"Failed to delete ticker {ticker}: {e}")
        return jsonify({"error": "Ticker 삭제에 실패했습니다"}), 500
    return jsonify({"tickers": saved_tickers})


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

    try:
        saved_tickers = persist_and_reload_tickers(ticker_list)
    except Exception as e:
        logger.error(f"Failed to persist bulk tickers: {e}")
        return jsonify({"error": "Ticker 일괄 저장에 실패했습니다"}), 500
    return jsonify({"tickers": saved_tickers, "added": added, "added_count": len(added)})


@app.route("/api/tickers/clear", methods=["DELETE"])
def clear_tickers():
    """Clear all tickers."""
    try:
        saved_tickers = persist_and_reload_tickers([])
    except Exception as e:
        logger.error(f"Failed to clear tickers: {e}")
        return jsonify({"error": "전체 삭제에 실패했습니다"}), 500
    return jsonify({"tickers": saved_tickers, "message": "All tickers cleared"})


@app.route("/api/tickers/reset-defaults", methods=["POST"])
def reset_tickers_to_defaults():
    """Reset tickers to the built-in default list."""
    try:
        saved_tickers = persist_and_reload_tickers(DEFAULT_TICKERS)
    except Exception as e:
        logger.error(f"Failed to reset default tickers: {e}")
        return jsonify({"error": "디폴트 종목 초기화에 실패했습니다"}), 500
    return jsonify({"tickers": saved_tickers, "count": len(saved_tickers)})


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

    try:
        saved_tickers = persist_and_reload_tickers(ticker_list)
    except Exception as e:
        logger.error(f"Failed to persist uploaded tickers CSV: {e}")
        return jsonify({"error": "CSV 종목 저장에 실패했습니다"}), 500
    return jsonify({"tickers": saved_tickers, "added": added, "added_count": len(added)})


# ─── Settings Routes ──────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    settings = load_settings()
    # 프론트엔드는 이 두 값을 쓰지 않는다 — webhook_token은 /api/webhook/info
    # 전용 엔드포인트로만 노출해야 하고, gemini_api_key는 코드 어디에서도 더
    # 이상 읽지 않는 legacy 필드라 그대로 두면 실제 키 값이 유출된다.
    settings.pop("webhook_token", None)
    settings.pop("gemini_api_key", None)
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
    # News source-specific query templates
    for key in ["query_yahoo", "query_newsapi", "query_google", "query_naver"]:
        if key in data:
            settings[key] = str(data[key])[:500]
    # Global market indices queries
    for key in ["query_market_us", "query_market_korea", "query_market_china", "query_market_hongkong", "query_market_japan", "query_market_europe"]:
        if key in data:
            settings[key] = str(data[key])[:500]
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
    """Send analysis results via email.

    요청 본문은 {"date": ..., "extra_to": [...]} 처럼 작아야 한다 — 실제 분석
    데이터는 analyze_stream()이 채워둔 _manual_run_cache에서 가져온다. (전체
    결과를 다시 POST로 보내면 사내 네트워크의 큰 POST 본문 차단(403)에 걸린다.)
    """
    data = request.get_json(force=True, silent=True) or {}
    date_str = data.get("date", "")
    extra_to = data.get("extra_to", [])  # Additional one-time recipients from request

    cached = _manual_run_cache.get(date_str)
    if not cached:
        return jsonify({"error": "전송할 캐시된 분석 결과가 없습니다. 분석을 다시 실행해주세요."}), 400

    results = cached.get("results") or []
    market_data = cached.get("market_data")
    all_stocks = cached.get("all_stocks")
    category_stats = cached.get("category_stats")
    current_tickers = load_tickers_from_csv()

    if not results:
        return jsonify({"error": "전송할 분석 결과가 없습니다"}), 400

    # Build recipient list
    settings = load_settings()
    extra_list = settings.get("email_recipients", [])
    all_recipients = list(set(DEFAULT_EMAIL_RECIPIENTS + extra_list + extra_to))

    if not all_recipients:
        return jsonify({"error": "수신자 이메일이 없습니다. 수신자를 추가해주세요."}), 400

    # Build HTML email.  Use the cached run's own date, not the server's
    # current date — otherwise emailing a historical analysis mislabels it
    # as today.
    m, d = date_str.split("-")[1], date_str.split("-")[2]
    subject = f"[{int(m)}/{int(d)}일 종가기준] 모니터링 업체 현황"
    html_body = build_email_html(
        results,
        date_str,
        market_data=market_data,
        all_stocks=all_stocks,
        category_stats=category_stats,
        ticker_objects=current_tickers,
    )

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


@app.route("/api/email-preview", methods=["POST"])
def get_email_preview():
    """Get plain text email preview for copying."""
    data = request.get_json()
    results = data.get("results", [])
    market_data = data.get("market_data", None)
    date_str = data.get("date", datetime.now(KST).strftime("%Y-%m-%d"))
    current_tickers = data.get("current_tickers", None)
    text = build_email_text(results, date_str, market_data=market_data, ticker_objects=current_tickers)
    return jsonify({"text": text})


def sort_results_for_email(results, ticker_objects=None):
    """Sort analysis results by category order, then ticker registration order."""
    if not results:
        return []

    ticker_objects = ticker_objects or load_tickers_from_csv()
    ticker_order = {}
    ticker_category = {}
    category_order = {}

    for idx, item in enumerate(ticker_objects):
        ticker = (item.get("ticker", "") or "").strip().upper()
        category = (item.get("category", "") or "").strip() or "기타"
        if not ticker:
            continue
        ticker_order[ticker] = idx
        ticker_category[ticker] = category
        if category not in category_order:
            category_order[category] = len(category_order)

    decorated = []
    for idx, result in enumerate(results):
        ticker = (result.get("ticker", "") or "").strip().upper()
        category = (result.get("category", "") or "").strip() or ticker_category.get(ticker, "기타")
        decorated.append((
            category_order.get(category, 9999),
            ticker_order.get(ticker, 9999),
            idx,
            {**result, "category": category},
        ))

    decorated.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in decorated]


def strip_english_and_refs(text):
    """Remove REFS:[...] tags and English-dominant lines, keeping Korean-only
    lines. Mirrors static/js/app.js's stripEnglishAndRefs (used there for the
    이메일 복붙용 결과 preview) so the actual sent emails match."""
    if not text:
        return ""
    cleaned = re.sub(r"REFS:\[[^\]]*\]", "", text).strip()
    korean_lines = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue
        ascii_count = len(re.findall(r"[A-Za-z]", line))
        total_chars = len(re.sub(r"\s", "", line))
        if total_chars > 0 and ascii_count / total_chars > 0.3:
            continue
        korean_lines.append(line)
    return " ".join(korean_lines).strip()


def build_email_html(results, date_str, market_data=None, all_stocks=None, category_stats=None, ticker_objects=None):
    """Build HTML email in 맑은고딕 10.5pt with 시장/개별회사 sections."""
    from datetime import date as date_cls

    INDEX_DISPLAY_NAMES = {
        "^DJI":       "Dow",
        "^IXIC":      "Nasdaq",
        "^GSPC":      "S\u0026P 500",
        "^KS11":      "코스피",
        "^KQ11":      "코스닥",
        "000001.SS":  "中상해",
        "^HSI":       "홍콩항셍",
        "^N225":      "日니케이",
        "^FTSE":      "英FTSE",
        "^FCHI":      "CAC",
        "^GDAXI":     "獨DAX",
    }

    REGION_GROUPS = [
        ("미\u00a0\u00a0국", ["미국"]),
        ("아시아",           ["한국", "중국", "홍콩", "일본"]),
        ("유\u00a0\u00a0럽", ["영국", "프랑스", "독일"]),
    ]

    def fmt_chg(v, decimals=2):
        if v < 0:
            return f'<span style="color:#cc0000;">△{abs(v):.{decimals}f}%</span>'
        else:
            return f'<span style="color:#0066cc;">{v:.{decimals}f}%</span>'

    def fmt_chg_text(v, decimals=2):
        """텍스트용 등락률 포맷"""
        if v < 0:
            return f'△{abs(v):.{decimals}f}%'
        else:
            return f'{v:.{decimals}f}%'

    try:
        d = date_cls.fromisoformat(date_str)
        date_label = f"{d.month:02d}/{d.day:02d}"
    except Exception:
        date_label = date_str

    font_style = "font-family:'맑은고딕',Malgun Gothic,Arial,sans-serif;font-size:10.5pt;"
    sorted_results = sort_results_for_email(results, ticker_objects=ticker_objects)

    # Build 시장 section
    market_lines = ""
    if market_data:
        index_by_ticker = {m["ticker"]: m for m in market_data.get("indices", []) if not m.get("error")}
        for region_label, regions in REGION_GROUPS:
            tickers_in_group = [
                idx["ticker"] for idx in MARKET_INDICES if idx["region"] in regions
            ]
            items = []
            for ticker in tickers_in_group:
                if ticker in index_by_ticker:
                    m = index_by_ticker[ticker]
                    display = INDEX_DISPLAY_NAMES.get(ticker, m.get("name", ticker))
                    if m.get("is_closed"):
                        items.append(f"{display} (휴장)")
                    else:
                        items.append(f"{display} ({fmt_chg(m['change_pct'], 2)})")
            if items:
                market_lines += (
                    f'<p style="{font_style}margin:2px 0;">'
                    f'\u00a0\u00a0- {region_label} :\u00a0\u00a0'
                    + ",\u00a0".join(items)
                    + "</p>"
                )

    # Build 개별회사 section (2열 테이블 형식)
    company_rows = ""
    for r in sorted_results:
        analysis_text = strip_english_and_refs(r.get("analysis", ""))
        chg_html = fmt_chg(r["change_pct"], 1)
        company_rows += (
            f'<tr>'
            f'<td style="border:none;vertical-align:top;white-space:nowrap;padding:2px 8px 2px 0;color:#333;">- {r["name"]} ({chg_html}) :</td>'
            f'<td style="border:none;vertical-align:top;padding:2px 0;color:#333;">{analysis_text}</td>'
            f'</tr>'
        )
    company_table = f'<table style="border-collapse:collapse;border:none;{font_style}width:100%;">{company_rows}</table>' if company_rows else ""

    market_section = ""
    if market_lines:
        market_section = (
            f'<p style="{font_style}margin:6px 0;"><b><u>시\u00a0\u00a0장</u></b></p>'
            + market_lines
            + '<br>'
        )

    body = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="background:#ffffff;color:#111111;{font_style}margin:0;padding:20px;">
<p style="{font_style}margin:4px 0;">안녕하십니까,</p>
<p style="{font_style}margin:4px 0;">{date_label}일 종가기준 모니터링 업체 현황 송부드립니다.</p>
<br>
{market_section}<p style="{font_style}margin:6px 0;"><b><u>개별회사</u></b></p>
{company_table}
</body>
</html>"""
    return body


def build_email_text(results, date_str, market_data=None, ticker_objects=None):
    """Build plain text for email copy —的市场/개별회사 format."""
    from datetime import date as date_cls

    INDEX_DISPLAY_NAMES = {
        "^DJI":       "Dow",
        "^IXIC":      "Nasdaq",
        "^GSPC":      "S&P 500",
        "^KS11":      "코스피",
        "^KQ11":      "코스닥",
        "000001.SS":  "中상해",
        "^HSI":       "홍콩항셍",
        "^N225":      "日니케이",
        "^FTSE":      "英FTSE",
        "^FCHI":      "CAC",
        "^GDAXI":     "獨DAX",
    }

    REGION_GROUPS = [
        ("미\u00a0\u00a0국", ["미국"]),
        ("아시아",           ["한국", "중국", "홍콩", "일본"]),
        ("유\u00a0\u00a0럽", ["영국", "프랑스", "독일"]),
    ]

    def fmt_chg(v):
        if v < 0:
            return f"△{abs(v):.2f}%"
        else:
            return f"{v:.2f}%"

    try:
        d = date_cls.fromisoformat(date_str)
        date_label = f"{d.month:02d}/{d.day:02d}"
    except Exception:
        date_label = date_str

    sorted_results = sort_results_for_email(results, ticker_objects=ticker_objects)

    lines = []
    lines.append("안녕하십니까,")
    lines.append(f"{date_label}일 종가기준 모니터링 업체 현황 송부드립니다.")
    lines.append("")

    # 시장 section
    if market_data:
        index_by_ticker = {m["ticker"]: m for m in market_data.get("indices", []) if not m.get("error")}
        market_parts = []
        for region_label, regions in REGION_GROUPS:
            tickers_in_group = [
                idx["ticker"] for idx in MARKET_INDICES if idx["region"] in regions
            ]
            items = []
            for ticker in tickers_in_group:
                if ticker in index_by_ticker:
                    m = index_by_ticker[ticker]
                    display = INDEX_DISPLAY_NAMES.get(ticker, m.get("name", ticker))
                    if m.get("is_closed"):
                        items.append(f"{display} (휴장)")
                    else:
                        items.append(f"{display} ({fmt_chg(m['change_pct'])})")
            if items:
                market_parts.append(f"  - {region_label} :  {', '.join(items)}")
        
        if market_parts:
            lines.append("시  장")
            lines.extend(market_parts)
            lines.append("")

    # 개별회사 section
    if sorted_results:
        lines.append("개별회사")
        for r in sorted_results:
            analysis_text = strip_english_and_refs(r.get("analysis", ""))
            lines.append(f"- {r['name']} ({fmt_chg(r['change_pct'])}): {analysis_text}")

    return "\n".join(lines)


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


def ticker_region(ticker):
    """티커 접미사 → 해당 시장 region. 매핑 안 되면(미국/유럽 등) None."""
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")):
        return "한국"
    if t.endswith(".T"):
        return "일본"
    if t.endswith(".TW"):
        return "대만"
    if t.endswith(".HK"):
        return "홍콩"
    if t.endswith((".SS", ".SZ")):
        return "중국"
    return None


@app.route("/api/analyze/stream", methods=["GET"])
def analyze_stream():
    """Streaming analysis - sends results in batches via SSE."""
    ticker_objects = load_tickers_from_csv()
    if not ticker_objects:
        return jsonify({"error": "No tickers saved."}), 400

    requested_tickers = [
        ticker.strip().upper()
        for ticker in request.args.get("tickers", "").split(",")
        if ticker.strip()
    ]
    if requested_tickers:
        requested_ticker_set = set(requested_tickers)
        ticker_objects = [
            ticker_obj for ticker_obj in ticker_objects
            if ticker_obj["ticker"].upper() in requested_ticker_set
        ]
        if not ticker_objects:
            return jsonify({"error": "Requested tickers not found."}), 400

    tickers = [t["ticker"] for t in ticker_objects]
    ticker_meta = {t["ticker"]: t for t in ticker_objects}
    skip_market = request.args.get("skip_market", "").lower() in {"1", "true", "yes"}

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

    # Custom search query (from query param, fallback to saved setting)
    custom_query = request.args.get("custom_query", "").strip() or settings.get("custom_query", "")

    cache_date_str = str(target_date)
    if not skip_market:
        # First batch of a fresh run for this date — reset the server-side cache.
        _manual_run_cache[cache_date_str] = {
            "market_data": None,
            "all_stocks": {},
            "category_stats": {},
            "results": [],
            "fundamentals": {},
            "change_threshold": change_threshold,
            "model_used": model,
        }
    else:
        _manual_run_cache.setdefault(cache_date_str, {
            "market_data": None,
            "all_stocks": {},
            "category_stats": {},
            "results": [],
            "fundamentals": {},
            "change_threshold": change_threshold,
            "model_used": model,
        })

    def generate():
        log_memory("STREAM START")

        # Phase 0: Fetch global market indices + news + Gemini analysis
        if not skip_market:
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
            _manual_run_cache[cache_date_str]["market_data"] = {
                "indices": indices_data, "analysis": market_analysis, "date": trade_date_for_market,
            }
            # 휴장 시장 목록 — 나중 배치(skip_market=True)에서도 쓸 수 있게 캐시에 저장
            _manual_run_cache[cache_date_str]["closed_markets"] = [
                idx["region"] for idx in indices_data if idx.get("is_closed")
            ]
            gc.collect()

        closed_markets = set(_manual_run_cache.get(cache_date_str, {}).get("closed_markets", []))

        # Phase 1: Fetch all stock data in batches
        all_stocks_slim = {}
        filtered_list = []

        total_batches = (len(tickers) + FETCH_BATCH_SIZE - 1) // FETCH_BATCH_SIZE

        for batch_idx in range(0, len(tickers), FETCH_BATCH_SIZE):
            batch = tickers[batch_idx:batch_idx + FETCH_BATCH_SIZE]
            batch_num = batch_idx // FETCH_BATCH_SIZE + 1

            yield f"data: {json.dumps({'type': 'progress', 'message': f'주가 수집 중... ({batch_num}/{total_batches})'})}\n\n"

            for ticker in batch:
                tkr_region = ticker_region(ticker)
                is_closed_market = tkr_region is not None and tkr_region in closed_markets
                result = fetch_single_ticker(ticker, target_date=target_date)
                meta = ticker_meta.get(ticker, {})
                err_msg = result.get("error", "")
                is_market_closed = is_closed_market or result.get("is_closed", False) or ("Insufficient data" in err_msg if err_msg else False)
                all_stocks_slim[ticker] = {
                    "name": result.get("name") or meta.get("name") or ticker,
                    "change_pct": result.get("change_pct", 0),
                    "category": meta.get("category", ""),
                }
                if err_msg:
                    all_stocks_slim[ticker]["error"] = err_msg
                if is_market_closed:
                    all_stocks_slim[ticker]["is_closed"] = True

                if not is_market_closed and abs(result.get("change_pct", 0)) >= change_threshold:
                    filtered_list.append((ticker, result))

            gc.collect()

        # Compute category averages
        category_stats = {}
        for tkr, info in all_stocks_slim.items():
            cat = info.get("category") or "기타"
            if cat not in category_stats:
                category_stats[cat] = {"total": 0.0, "count": 0, "tickers": []}
            category_stats[cat]["tickers"].append(tkr)
            if not info.get("error") and not info.get("is_closed"):
                category_stats[cat]["total"] += info["change_pct"]
                category_stats[cat]["count"] += 1
        for cat in category_stats:
            s = category_stats[cat]
            s["avg"] = round(s["total"] / s["count"], 2) if s["count"] else 0
            del s["total"]

        # Send all_stocks data and category stats
        yield f"data: {json.dumps({'type': 'stocks', 'all_stocks': all_stocks_slim, 'category_stats': category_stats})}\n\n"

        # Accumulate into the server-side cache across batched requests, then
        # recompute category stats from the FULL accumulated all_stocks (not
        # just this batch) so the cached snapshot reflects the whole run.
        cached_all_stocks = _manual_run_cache[cache_date_str]["all_stocks"]
        cached_all_stocks.update(all_stocks_slim)
        cached_category_stats = {}
        for tkr, info in cached_all_stocks.items():
            cat = info.get("category") or "기타"
            if cat not in cached_category_stats:
                cached_category_stats[cat] = {"total": 0.0, "count": 0, "tickers": []}
            cached_category_stats[cat]["tickers"].append(tkr)
            if not info.get("error") and not info.get("is_closed"):
                cached_category_stats[cat]["total"] += info["change_pct"]
                cached_category_stats[cat]["count"] += 1
        for cat in cached_category_stats:
            s = cached_category_stats[cat]
            s["avg"] = round(s["total"] / s["count"], 2) if s["count"] else 0
            del s["total"]
        _manual_run_cache[cache_date_str]["category_stats"] = cached_category_stats

        log_memory("AFTER FETCH")

        # Phase 1.5: Fetch fundamentals (market cap, EV/EBITDA, revenue, etc.)
        # for ALL tracked tickers, regardless of the change-% filter — this
        # data feeds the Excel export and is independent of Gemini analysis.
        fundamentals_data = {}
        total_fund_batches = (len(ticker_objects) + FUNDAMENTALS_BATCH_SIZE - 1) // FUNDAMENTALS_BATCH_SIZE
        for batch_idx in range(0, len(ticker_objects), FUNDAMENTALS_BATCH_SIZE):
            batch = ticker_objects[batch_idx:batch_idx + FUNDAMENTALS_BATCH_SIZE]
            batch_num = batch_idx // FUNDAMENTALS_BATCH_SIZE + 1
            yield f"data: {json.dumps({'type': 'progress', 'message': f'펀더멘털 데이터 수집 중... ({batch_num}/{total_fund_batches})'})}\n\n"
            for t in batch:
                fundamentals_data[t["ticker"]] = fundamentals.fetch_fundamentals_for_ticker(
                    t["ticker"], t, target_date=target_date
                )
            gc.collect()
        yield f"data: {json.dumps({'type': 'fundamentals', 'fundamentals': fundamentals_data})}\n\n"
        _manual_run_cache[cache_date_str]["fundamentals"].update(fundamentals_data)

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
                meta = ticker_meta.get(ticker, {})
                try:
                    # Step 1: Search news from all available sources
                    yield f"data: {json.dumps({'type': 'progress', 'message': f'뉴스 기사 검색 중... ({ticker})'})}\n\n"
                    articles = search_all_news_articles(
                        ticker,
                        info.get("name", ticker),
                        info.get("date", ""),
                        target_date=target_date,
                        custom_query=custom_query,
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
                        "name": meta.get("name") or info.get("name", ticker),
                        "category": meta.get("category", ""),
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
                        "name": meta.get("name") or info.get("name", ticker),
                        "category": meta.get("category", ""),
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
            _manual_run_cache[cache_date_str]["results"].extend(batch_results)

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

    Simplified: Only fetch target_date and previous day data, no timezone conversion.
    If target_date data is missing → market is closed (holiday/weekend).
    """
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"

        if target_date:
            # Fetch: (target - 14 days) ~ (target + 1 day) — wide range for long holidays
            period1_dt = datetime.combine(
                target_date - timedelta(days=14), datetime.min.time()
            ).replace(tzinfo=timezone.utc)
            period2_dt = datetime.combine(
                target_date + timedelta(days=1), datetime.min.time()
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

        # Build (date, close) pairs from UTC timestamps, no timezone conversion
        # Dates are in UTC, but we treat them as market calendar dates
        date_close = {}
        for ts, c in zip(timestamps, closes):
            if c is not None:
                # Use UTC date directly as market date
                dt = datetime.utcfromtimestamp(ts)
                date_close[dt.date()] = c

        sorted_dates = sorted(date_close.keys())
        name = meta.get("shortName", meta.get("longName", ticker_symbol))

        if not target_date:
            # No target date → return latest data
            if len(sorted_dates) < 2:
                return {
                    "error": f"Insufficient data",
                    "change_pct": 0,
                    "name": name,
                }
            prev_close = date_close[sorted_dates[-2]]
            last_close = date_close[sorted_dates[-1]]
            change_pct = ((last_close - prev_close) / prev_close) * 100
            return {
                "name": name,
                "change_pct": round(float(change_pct), 2),
                "date": str(sorted_dates[-1]),
                "is_closed": False,
            }

        # Check if target_date data exists
        if target_date not in date_close:
            # Check if meta.regularMarketPrice can fill the gap (market was trading but quote not timestamped yet)
            market_price = meta.get("regularMarketPrice")
            market_time = meta.get("regularMarketTime")
            if market_price and market_time:
                # Check if regularMarketTime is from target_date (KST)
                market_dt_utc = datetime.utcfromtimestamp(market_time)
                market_date = market_dt_utc.date()
                # If market date matches target_date, use regularMarketPrice as today's close
                if market_date == target_date and sorted_dates:
                    prev_close = date_close[sorted_dates[-1]]
                    last_close = float(market_price)
                    change_pct = ((last_close - prev_close) / prev_close) * 100
                    return {
                        "name": name,
                        "change_pct": round(float(change_pct), 2),
                        "date": str(target_date),
                        "is_closed": False,
                    }
            # Target date data not available → market is closed
            return {
                "name": name,
                "change_pct": 0,
                "date": str(sorted_dates[-1]) if sorted_dates else "",
                "is_closed": True,
            }

        # Find previous day data
        target_idx = sorted_dates.index(target_date)
        if target_idx == 0 or len(sorted_dates) < 2:
            return {
                "name": name,
                "change_pct": 0,
                "date": str(target_date),
                "is_closed": False,
                "error": "No previous day data",
            }

        prev_date = sorted_dates[target_idx - 1]
        prev_close = date_close[prev_date]
        last_close = date_close[target_date]
        change_pct = ((last_close - prev_close) / prev_close) * 100

        return {
            "name": name,
            "change_pct": round(float(change_pct), 2),
            "date": str(target_date),
            "is_closed": False,
        }
    except Exception as e:
        return {
            "error": str(e),
            "change_pct": 0,
            "name": ticker_symbol,
        }


def fetch_all_market_indices(target_date=None):
    """Fetch all global market indices data.
    
    Simplified: Uses fetch_single_ticker which handles:
    - 2-day API range (target-1, target+1)
    - No timezone conversion
    - is_closed if target_date data missing
    """
    results = []
    for idx in MARKET_INDICES:
        data = fetch_single_ticker(idx["ticker"], target_date=target_date)
        results.append({
            "ticker": idx["ticker"],
            "name": idx["name"],
            "region": idx["region"],
            "change_pct": data.get("change_pct", 0),
            "date": data.get("date", ""),
            "error": data.get("error", ""),
            "is_closed": data.get("is_closed", False),
        })
    return results


def search_market_news_for_region(region, trade_date, target_date=None):
    """Search news articles for a global market region."""
    cfg = MARKET_NEWS_REGIONS.get(region)
    if not cfg:
        return []
    ticker = cfg["ticker"]
    
    # Get region-specific query from settings
    settings = load_settings()
    query_key = f"query_market_{region.lower().replace(' ', '_')}"
    query = settings.get(query_key) or cfg["query"]
    
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
1. 아래 순서대로 각 지역을 반드시 구분선(ㅡ)으로 구분하여 출력:
   ㅡ US 미국
   [한국어 분석 1-2문장]
   [English analysis 1-2 sentences]

   ㅡ 한국
   [한국어 분석 1-2문장]
   [English analysis 1-2 sentences]

   ㅡ 중국/홍콩
   [한국어 분석 1-2문장]
   [English analysis 1-2 sentences]

   ㅡ 일본
   [한국어 분석 1-2문장]
   [English analysis 1-2 sentences]

   ㅡ 유럽
   [한국어 분석 1-2문장]
   [English analysis 1-2 sentences]

2. 뉴스 근거가 없는 지역은 "정보 없음 / No data"로 표시.
3. 추측하거나 자체 지식을 사용하지 마라. 오직 제공된 기사 내용만 활용.
4. 마지막에 구분선 후 전체 시장 분위기를 1문장으로 요약:
   ㅡ 전체 요약
   [한국어]
   [English]"""

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


def search_news_articles_newsapi(ticker, company_name, target_date=None, custom_query=None, settings=None):
    """Search news from NewsAPI.org (requires NEWS_API_KEY).

    If target_date is provided, searches articles from target_date to target_date+1.
    """
    if not NEWS_API_KEY:
        return []
    try:
        # Use source-specific query template from settings, fallback to custom_query or default
        query_template = (settings or {}).get("query_newsapi") or custom_query or f'"{company_name}" OR "{ticker}" stock'
        query = build_search_query(query_template, company_name, ticker, f'"{company_name}" OR "{ticker}" stock')
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


def search_news_articles_google(ticker, company_name, trade_date, target_date=None, custom_query=None, settings=None):
    """Search news from Google Custom Search API (requires GOOGLE_API_KEY + GOOGLE_CSE_ID).

    If target_date is provided, restricts results to that date range (date to date+1).
    """
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return []

    try:
        # Use source-specific query template from settings, fallback to custom_query or default
        query_template = (settings or {}).get("query_google") or custom_query or f'"{company_name}" OR "{ticker}" stock news'
        query = build_search_query(query_template, company_name, ticker, f'"{company_name}" OR "{ticker}" stock news')
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


def search_news_articles_naver(company_name, ticker="", target_date=None, custom_query=None, settings=None):
    """Search Korean news from Naver Search API (requires NAVER_CLIENT_ID + NAVER_CLIENT_SECRET).

    Returns articles sorted by publish date (newest first).
    Naver is especially useful for Korean stocks and market indices (코스피/코스닥).
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    try:
        # Use source-specific query template from settings, fallback to custom_query or default
        query_template = (settings or {}).get("query_naver") or custom_query or company_name
        query = build_search_query(query_template, company_name, ticker, company_name)
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


def search_all_news_articles(ticker, company_name, trade_date, target_date=None, custom_query=None, gmail_articles=None):
    """Search news from all available sources and merge results.

    Priority: Yahoo Finance (always) → NewsAPI → Google CSE → Naver (Korean news) → Gmail memos
    gmail_articles: pre-fetched memo list (cached); if None, fetches from IMAP directly.
    Returns up to 10 deduplicated articles.
    """
    all_articles = []
    settings = load_settings()

    # 1. Yahoo Finance — always available (uses ticker directly, not text query)
    if settings.get("yahoo_finance_enabled", True):
        all_articles.extend(search_news_articles_yahoo_finance(ticker, company_name))

    # 2. NewsAPI — optional
    if NEWS_API_KEY and settings.get("newsapi_enabled", True):
        all_articles.extend(search_news_articles_newsapi(ticker, company_name, target_date=target_date, custom_query=custom_query, settings=settings))

    # 3. Google CSE — optional
    if GOOGLE_API_KEY and GOOGLE_CSE_ID and settings.get("google_cse_enabled", True):
        all_articles.extend(search_news_articles_google(ticker, company_name, trade_date, target_date=target_date, custom_query=custom_query, settings=settings))

    # 4. Naver — optional (Korean news, especially useful for KS/KQ tickers and Korean market indices)
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET and settings.get("naver_enabled", True):
        all_articles.extend(search_news_articles_naver(company_name, ticker=ticker, target_date=target_date, custom_query=custom_query, settings=settings))

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
            target_date = get_kst_yesterday()

        settings = load_settings()
        model = settings.get("gemini_model", DEFAULT_GEMINI_MODEL)
        change_threshold = float(settings.get("change_threshold", 5.0))
        custom_query = settings.get("custom_query", "")
        prompt_templates = {
            "with_articles": settings.get("prompt_with_articles") or DEFAULT_PROMPT_WITH_ARTICLES,
            "without_articles": settings.get("prompt_without_articles") or DEFAULT_PROMPT_WITHOUT_ARTICLES,
        }

        # Phase 0: 글로벌 지수
        logger.info("Fetching market indices...")
        indices_data = fetch_all_market_indices(target_date)
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

        # 휴장 시장 목록을 시장 지수 데이터에서 구축
        closed_markets = set()
        for idx in indices_data:
            if idx.get("is_closed"):
                closed_markets.add(idx["region"])

        # Phase 1: 종목 데이터 수집 + 필터링
        logger.info("Fetching ticker data...")
        ticker_objects = load_tickers_from_csv()
        ticker_meta = {t["ticker"]: t for t in ticker_objects}
        all_stocks = {}
        filtered_list = []
        # 휴장 시장 목록 (error에 "Insufficient data"가 포함된 경우 휴장으로 간주)
        for t in ticker_objects:
            tkr = t["ticker"]
            tkr_region = ticker_region(tkr)
            # 해당 시장이 휴장 목록에 있으면跳过
            is_closed_market = tkr_region is not None and tkr_region in closed_markets
            result = fetch_single_ticker(tkr, target_date=target_date)
            err_msg = result.get("error", "")
            is_market_closed = is_closed_market or result.get("is_closed", False) or ("Insufficient data" in err_msg if err_msg else False)
            all_stocks[tkr] = {
                "name": result.get("name") or t.get("name") or tkr,
                "change_pct": result.get("change_pct", 0),
                "category": t.get("category", ""),
            }
            if err_msg:
                all_stocks[tkr]["error"] = err_msg
            if is_market_closed:
                all_stocks[tkr]["is_closed"] = True
            # 휴장이 아닌 종목을 대상으로 필터링
            if not is_market_closed and abs(result.get("change_pct", 0)) >= change_threshold:
                filtered_list.append((tkr, result))
        # Sort by ticker registration order (matches 분석 실행/UI ordering)
        ticker_order = {t["ticker"]: i for i, t in enumerate(ticker_objects)}
        filtered_list.sort(key=lambda x: ticker_order.get(x[0], 9999))
        # 카테고리별 평균 등락률 계산
        category_stats = {}
        for tkr, info in all_stocks.items():
            cat = info.get("category") or "기타"
            if cat not in category_stats:
                category_stats[cat] = {"total": 0.0, "count": 0}
            if not info.get("error") and not info.get("is_closed"):
                category_stats[cat]["total"] += info["change_pct"]
                category_stats[cat]["count"] += 1
        for cat in category_stats:
            s = category_stats[cat]
            s["avg"] = round(s["total"] / s["count"], 2) if s["count"] else 0
            del s["total"]
        logger.info(f"Filtered {len(filtered_list)} tickers above threshold {change_threshold}%")
        gc.collect()

        # Phase 1.5: 펀더멘털 데이터 수집 (전체 종목 대상, 필터링 무관 — 엑셀 다운로드용)
        logger.info("Fetching fundamentals data...")
        fundamentals_data = {}
        for batch_idx in range(0, len(ticker_objects), FUNDAMENTALS_BATCH_SIZE):
            batch = ticker_objects[batch_idx:batch_idx + FUNDAMENTALS_BATCH_SIZE]
            for t in batch:
                fundamentals_data[t["ticker"]] = fundamentals.fetch_fundamentals_for_ticker(
                    t["ticker"], t, target_date=target_date
                )
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
                    target_date=target_date, custom_query=custom_query,
                    gmail_articles=_gmail_cache,
                )
                result = analyze_with_gemini(ticker, info, articles=articles, model=model, prompt_templates=prompt_templates)
                meta = ticker_meta.get(ticker, {})
                results.append({
                    "ticker": ticker,
                    "name": meta.get("name") or info.get("name", ticker),
                    "category": meta.get("category", ""),
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

        # Phase 3: 이메일 전송.  SMTP 실패(자격증명 만료 등)가 나도 아래 Phase 4
        # GCS 저장은 반드시 실행되어야 하므로, 이 블록만 따로 감싼다 — 그렇지
        # 않으면 예외가 함수 전체를 중단시켜 분석 결과가 통째로 유실된다.
        date_str = str(target_date)
        all_recipients = list(set(DEFAULT_EMAIL_RECIPIENTS + settings.get("email_recipients", [])))
        sent = False
        email_error = None
        if not all_recipients:
            logger.warning("Scheduled analysis: no recipients configured, skipping email")
        else:
            try:
                html_body = build_email_html(
                    results,
                    date_str,
                    market_data=market_data,
                    all_stocks=all_stocks,
                    category_stats=category_stats,
                    ticker_objects=ticker_objects,
                )
                m2, d2 = date_str.split("-")[1], date_str.split("-")[2]
                subject = f"[{int(m2)}/{int(d2)}일 종가기준] 모니터링 업체 현황"
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = EMAIL_FROM
                msg["To"] = ", ".join(all_recipients)
                msg.attach(MIMEText(html_body, "html", "utf-8"))
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                    server.ehlo()
                    server.starttls()
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    server.sendmail(SMTP_USER, all_recipients, msg.as_string())
                sent = True
                logger.info(f"Scheduled analysis complete: {len(results)} results, email sent to {all_recipients}")
            except Exception as e:
                email_error = str(e)
                logger.error(f"Scheduled analysis: email send failed: {e}", exc_info=True)

        # Phase 4: GCS에 결과 저장 (이메일 성공 여부와 무관하게 항상 실행 — 홈페이지 기본값이 됨)
        save_results_to_gcs(date_str, {
            "market_data": market_data,
            "all_stocks": all_stocks,
            "category_stats": category_stats,
            "results": results,
            "fundamentals": fundamentals_data,
            "change_threshold": change_threshold,
            "model_used": model,
        }, source="scheduled")

        return {
            "status": "done", "sent": sent,
            "reason": email_error if email_error else (None if sent else "no recipients"),
            "recipients": all_recipients if sent else [],
            "results_count": len(results), "date": date_str,
        }

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


@app.route("/api/google-cse/test", methods=["GET"])
def google_cse_test():
    """Google CSE 직접 호출 테스트 — 설정상 활성화 여부가 아니라 실제 검색
    호출이 성공하는지, 실패한다면 구글이 뭐라고 하는지(권한/할당량/CSE 설정
    오류) 확인하기 위한 진단용. search_news_articles_google()은 실패 시
    조용히 빈 리스트만 반환하도록 되어 있어 실제 원인을 알 수 없다."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return jsonify({"ok": False, "error": "GOOGLE_API_KEY 또는 GOOGLE_CSE_ID가 설정되지 않았습니다."}), 400
    query = request.args.get("q", "삼성전자 주가")
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": 5},
            timeout=10,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return jsonify({
            "ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "query": query,
            "cx": GOOGLE_CSE_ID,
            "result_count": len(data.get("items", [])),
            "titles": [item.get("title") for item in data.get("items", [])],
            "google_error": data.get("error"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


# ─── Saved Results (date-indexed GCS store) ───────────────────────────────────

@app.route("/api/results/latest", methods=["GET"])
def results_latest():
    """홈페이지 기본값 — 가장 최근 저장(포인터가 가리키는) 결과."""
    if not GCS_BUCKET_NAME:
        return jsonify({"available": False, "reason": "GCS not configured"})
    data = load_latest_results_from_gcs()
    if not data:
        return jsonify({"available": False})
    data["available"] = True
    return jsonify(data)


@app.route("/api/results/dates", methods=["GET"])
def results_dates():
    """저장된 날짜 목록 (최신순)."""
    return jsonify({"dates": list_saved_result_dates()})


@app.route("/api/results/ticker-history", methods=["GET"])
def results_ticker_history():
    """특정 종목의 등락율/등락 원인을 기간 내 저장된 날짜별로 조회."""
    ticker = request.args.get("ticker", "").strip().upper()
    start_str = request.args.get("start", "")
    end_str = request.args.get("end", "")

    if not ticker:
        return jsonify({"error": "종목(ticker)을 지정해주세요"}), 400
    try:
        date_cls.fromisoformat(start_str)
        date_cls.fromisoformat(end_str)
    except ValueError:
        return jsonify({"error": "잘못된 날짜 형식입니다 (YYYY-MM-DD)"}), 400
    if start_str > end_str:
        return jsonify({"error": "시작일이 종료일보다 늦을 수 없습니다"}), 400

    all_dates = list_saved_result_dates()
    in_range = sorted((d for d in all_dates if start_str <= d <= end_str), reverse=True)

    rows = []
    for date_str in in_range:
        data = load_results_from_gcs(date_str)
        if not data:
            continue
        stock_info = (data.get("all_stocks") or {}).get(ticker)
        if not stock_info:
            continue
        analysis_entry = next(
            (r for r in (data.get("results") or []) if r.get("ticker") == ticker), None
        )
        rows.append({
            "date": date_str,
            "name": stock_info.get("name", ticker),
            "change_pct": stock_info.get("change_pct"),
            "error": stock_info.get("error"),
            "analyzed": analysis_entry is not None,
            "analysis": analysis_entry.get("analysis") if analysis_entry else None,
        })

    return jsonify({"ticker": ticker, "rows": rows})


@app.route("/api/results/<date_str>", methods=["GET"])
def results_by_date(date_str):
    """특정 날짜의 저장된 결과 조회 (읽기 전용)."""
    try:
        date_cls.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "잘못된 날짜 형식입니다 (YYYY-MM-DD)"}), 400
    data = load_results_from_gcs(date_str)
    if not data:
        return jsonify({"error": f"{date_str} 저장된 결과가 없습니다"}), 404
    data["available"] = True
    return jsonify(data)


@app.route("/api/results/save", methods=["POST"])
def results_save():
    """수동 실행("분석 실행") 결과를 새 기본값으로 저장.

    요청 본문은 날짜값만 담은 작은 JSON({"date": ...})이어야 한다 — 실제 분석
    데이터는 analyze_stream()이 스트리밍하면서 이미 _manual_run_cache에 쌓아둔
    것을 그대로 사용한다. (전체 결과를 다시 POST로 보내면 사내 네트워크의 큰
    POST 본문 차단(403)에 걸리는 문제가 있어 이렇게 구조를 바꿨다.)
    """
    if not GCS_BUCKET_NAME:
        return jsonify({"error": "GCS_BUCKET_NAME 환경변수가 설정되지 않았습니다"}), 400
    if not HAS_GCS:
        return jsonify({"error": "google-cloud-storage 패키지가 설치되지 않았습니다"}), 500

    body = request.get_json(force=True, silent=True) or {}
    date_str = body.get("date") or str(get_kst_today())
    try:
        date_cls.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "잘못된 날짜 형식입니다 (YYYY-MM-DD)"}), 400

    payload = _manual_run_cache.get(date_str)
    if not payload:
        return jsonify({"error": "저장할 캐시된 분석 결과가 없습니다. 분석을 다시 실행해주세요."}), 400

    ok = save_results_to_gcs(date_str, payload, source="manual")
    if not ok:
        return jsonify({"error": "GCS 저장에 실패했습니다"}), 500
    return jsonify({"success": True, "date": date_str})


@app.route("/api/results/fundamentals-override", methods=["POST"])
def results_fundamentals_override():
    """엑셀/CSV 업로드로 종목별 펀더멘털 값을 수동 보정.

    파일 자체는 절대 서버로 전송하지 않는다 — 브라우저에서 파싱한 뒤 티커
    소수개씩 청크로 나눠 이 라우트를 여러 번 호출한다(본문은
    {"date": ..., "overrides": {ticker: {field: value}}} 수준으로 작게 유지 —
    큰 POST 본문이 사내 네트워크에서 차단되는 문제를 피하기 위함).
    """
    body = request.get_json(force=True, silent=True) or {}
    date_str = body.get("date", "")
    overrides = body.get("overrides") or {}
    try:
        date_cls.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "잘못된 날짜 형식입니다 (YYYY-MM-DD)"}), 400
    if not overrides:
        return jsonify({"error": "적용할 값이 없습니다"}), 400

    updated, err = apply_fundamentals_overrides(date_str, overrides)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"success": True, "updated_count": len(overrides)})


@app.route("/api/export/excel", methods=["GET"])
def export_excel():
    """저장된 결과의 펀더멘털 데이터를 엑셀(.xlsx)로 다운로드."""
    date_str = request.args.get("date", "")
    if not date_str:
        pointer = gcs_download_json(RESULTS_POINTER_BLOB) if GCS_BUCKET_NAME else None
        date_str = pointer.get("current_date") if pointer else None
        if not date_str:
            return jsonify({"error": "저장된 결과가 없습니다"}), 400
    try:
        date_cls.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "잘못된 날짜 형식입니다 (YYYY-MM-DD)"}), 400

    # 방금 실행했지만 아직 "저장"하지 않은 결과도 다운로드할 수 있어야 하므로
    # 먼저 인메모리 캐시를 확인하고, 없으면 GCS에 저장된 과거 결과를 본다.
    data = _manual_run_cache.get(date_str) or load_results_from_gcs(date_str)
    if not data:
        return jsonify({"error": f"{date_str} 저장된 결과가 없습니다"}), 404
    fundamentals_data = data.get("fundamentals")
    if not fundamentals_data:
        return jsonify({"error": f"{date_str} 결과에는 펀더멘털 데이터가 없습니다 (이 기능 추가 이전 결과)"}), 400

    ticker_objects = load_tickers_from_csv()
    wb = fundamentals.build_fundamentals_workbook(fundamentals_data, ticker_objects)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"fundamentals_{date_str}.xlsx",
    )


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

    # 날짜 파라미터 (없으면 어제 KST)
    date_str = request.args.get("date", "") or (request.get_json(silent=True) or {}).get("date", "")
    target_date = None
    if date_str:
        try:
            target_date = date_cls.fromisoformat(date_str)
        except ValueError:
            pass
    else:
        target_date = get_kst_yesterday()

    result = run_scheduled_analysis(target_date=target_date)
    return jsonify(result)


# ─── Scheduler Startup ────────────────────────────────────────────────────────
# Start scheduler after all functions are defined
_apply_schedule(load_settings())

# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
