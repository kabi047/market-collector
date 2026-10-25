"""All settings in one place. Override any of them in .env."""
import os
import logging
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

IST = ZoneInfo("Asia/Kolkata")
DATA = Path(os.getenv("DATA_ROOT", ROOT / "data"))      # data/{index,stock,options,futures,others}
STATE = Path(os.getenv("STATE_ROOT", ROOT / "state"))   # tokens, universe, progress, logs
LOGS = STATE / "logs"
for p in (DATA, STATE, LOGS):
    p.mkdir(parents=True, exist_ok=True)

# --- Zerodha
API_KEY = os.getenv("ZERODHA_API_KEY", "")
API_SECRET = os.getenv("ZERODHA_API_SECRET", "")
USER_ID = os.getenv("ZERODHA_USER_ID", "")            # only needed for auto-login (cloud)
PASSWORD = os.getenv("ZERODHA_PASSWORD", "")
TOTP_SECRET = os.getenv("ZERODHA_TOTP_SECRET", "")

# --- remote storage (Hugging Face private dataset): lets a cloud runner receive the morning token and store live data
HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_REPO = os.getenv("HF_REPO", "")
TOKEN_FROM_HF = os.getenv("TOKEN_FROM_HF", "0") == "1"      # cloud runner: take the access token you pushed in the morning

# --- universe
EXTRA_SYMBOLS = [s.strip().upper() for s in os.getenv("EXTRA_SYMBOLS", "").split(",") if s.strip()]
INDEX_LISTS = ["nifty50", "niftynext50", "niftymidcap50"]    # Nifty 100 + Midcap 50 = top ~150 by free-float mcap
INDICES = [  # (exchange, tradingsymbol as in the Kite instrument dump)
    ("NSE", "NIFTY 50"), ("NSE", "NIFTY BANK"), ("NSE", "NIFTY NEXT 50"), ("NSE", "NIFTY 100"),
    ("NSE", "NIFTY MIDCAP 50"), ("NSE", "NIFTY MIDCAP 100"), ("NSE", "NIFTY 500"), ("NSE", "NIFTY FIN SERVICE"),
    ("NSE", "NIFTY IT"), ("NSE", "NIFTY AUTO"), ("NSE", "NIFTY PHARMA"), ("NSE", "NIFTY FMCG"),
    ("NSE", "NIFTY METAL"), ("NSE", "NIFTY ENERGY"), ("NSE", "NIFTY REALTY"), ("NSE", "NIFTY PSU BANK"),
    ("NSE", "INDIA VIX"), ("BSE", "SENSEX"), ("BSE", "BANKEX"),
]

# --- historical
EARLIEST = dt.date(int(os.getenv("HIST_EARLIEST_YEAR", 2000)), 1, 1)
HIST_REQ_PER_SEC = float(os.getenv("HIST_REQ_PER_SEC", 2.8))     # Zerodha limit is 3/s
CHUNK_DAYS = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100, "15minute": 200,
              "30minute": 200, "60minute": 400, "day": 2000}      # max range per request (Zerodha limits)
STOP_AFTER_EMPTY = {"minute": 4, "day": 1}                        # consecutive empty windows => no older data exists

# --- live
LIVE_START = dt.time(9, 0)      # pre-open starts 09:00
LIVE_END = dt.time(15, 45)     # F&O trades until 15:40 (since Aug 2026); equities' continuous session ends 15:15
LIVE_SHIP_MINUTES = int(os.getenv("LIVE_SHIP_MINUTES", 30))
LIVE_FLUSH_SECONDS = int(os.getenv("LIVE_FLUSH_SECONDS", 15))
LIVE_FUT_MONTHS = int(os.getenv("LIVE_FUT_MONTHS", 2))                  # current + next month futures
LIVE_OPT_UNDERLYINGS = [s.strip() for s in os.getenv("LIVE_OPT_UNDERLYINGS", "NIFTY,BANKNIFTY,FINNIFTY,SENSEX").split(",") if s.strip()]
LIVE_OPT_EXPIRIES = int(os.getenv("LIVE_OPT_EXPIRIES", 2))
LIVE_OPT_STRIKES = int(os.getenv("LIVE_OPT_STRIKES", 20))               # strikes each side of ATM
LIVE_STOCK_OPTIONS = os.getenv("LIVE_STOCK_OPTIONS", "0") == "1"        # stock options near ATM (adds ~3000+ instruments)

# --- fundamentals / reports
ANNUAL_REPORTS_PER_COMPANY = int(os.getenv("ANNUAL_REPORTS_PER_COMPANY", 3))

# --- phone alerts (optional): create a bot with @BotFather, put token + your chat id here
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def now_ist():
    return dt.datetime.now(IST).replace(tzinfo=None)


def setup_logging(name):
    log = logging.getLogger()
    if not log.handlers:
        log.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        fh = logging.FileHandler(LOGS / f"{now_ist():%Y-%m-%d}_{name}.log", encoding="utf-8")
        sh = logging.StreamHandler()
        for h in (fh, sh):
            h.setFormatter(fmt)
            log.addHandler(h)
    return logging.getLogger(name)


def notify(text):
    """Send a Telegram message if configured (silent no-op otherwise)."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": f"[collector] {text}"}, timeout=10)
    except Exception:
        pass
