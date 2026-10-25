"""Daily Zerodha login. Access tokens expire every morning (~06:00 IST).

manual: open the login URL, log in, paste the redirect URL back.
auto:   user id + password + TOTP secret from .env (for the cloud server, no human needed).
        Uses Kite's web login endpoints, which Zerodha does not officially support - may break if they change."""
import json
import datetime as dt
from urllib.parse import urlparse, parse_qs
import requests
import pyotp
from kiteconnect import KiteConnect

from . import config as C

TOKEN_FILE = C.STATE / "access_token.json"


def _save(session):
    TOKEN_FILE.write_text(json.dumps({"access_token": session["access_token"], "user_id": session.get("user_id"),
                                      "login_at": C.now_ist().isoformat()}))


def _need_keys():
    if not (C.API_KEY and C.API_SECRET):
        raise SystemExit("Set ZERODHA_API_KEY and ZERODHA_API_SECRET in .env first.")


def _request_token(text):
    text = text.strip()
    if "request_token=" in text:
        return parse_qs(urlparse(text).query)["request_token"][0]
    return text


def login_manual():
    _need_keys()
    kite = KiteConnect(api_key=C.API_KEY)
    print("1) Open this URL and log in:\n   " + kite.login_url())
    print("2) After login the browser goes to your redirect URL (it may show an error page - that is fine).")
    rt = _request_token(input("3) Paste that full URL (or just the request_token) here: "))
    s = kite.generate_session(rt, api_secret=C.API_SECRET)
    _save(s)
    print(f"Logged in as {s.get('user_id')}. Token saved to {TOKEN_FILE}")
    from . import hf_sync
    if hf_sync.enabled():
        print("Token sent to the cloud collector." if hf_sync.push_token() else "WARNING: could not send token to the cloud.")


def login_auto():
    _need_keys()
    if not (C.USER_ID and C.PASSWORD and C.TOTP_SECRET):
        raise SystemExit("Auto-login needs ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET in .env")
    http = requests.Session()
    http.headers["User-Agent"] = C.BROWSER_UA
    r = http.post("https://kite.zerodha.com/api/login", data={"user_id": C.USER_ID, "password": C.PASSWORD}, timeout=20)
    r.raise_for_status()
    req_id = r.json()["data"]["request_id"]
    r = http.post("https://kite.zerodha.com/api/twofa", timeout=20,
                  data={"user_id": C.USER_ID, "request_id": req_id, "twofa_value": pyotp.TOTP(C.TOTP_SECRET).now(),
                        "twofa_type": "totp", "skip_totp": "true"})
    r.raise_for_status()
    url = f"https://kite.zerodha.com/connect/login?api_key={C.API_KEY}&v=3"
    rt = None
    for _ in range(10):                      # follow redirects by hand until one carries request_token
        if "request_token=" in url:
            rt = _request_token(url); break
        try:
            r = http.get(url, allow_redirects=False, timeout=20)
        except requests.exceptions.ConnectionError as e:     # redirect URL host may not exist; token is in the URL
            m = str(e)
            if "request_token=" in m:
                rt = m.split("request_token=")[1].split("&")[0].split(" ")[0]
            break
        loc = r.headers.get("Location")
        if not loc:
            break
        url = loc if loc.startswith("http") else "https://kite.zerodha.com" + loc
    if not rt:
        raise SystemExit("Auto-login failed: no request_token. Has the app been authorised once manually? "
                         "Run 'python collect.py login' once, then retry.")
    s = KiteConnect(api_key=C.API_KEY).generate_session(rt, api_secret=C.API_SECRET)
    _save(s)
    print(f"Auto-login OK ({s.get('user_id')})")


def token_valid():
    if not TOKEN_FILE.exists():
        return False
    t = json.loads(TOKEN_FILE.read_text())
    login_at = dt.datetime.fromisoformat(t["login_at"])
    now = C.now_ist()
    cutoff = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < cutoff:
        cutoff -= dt.timedelta(days=1)
    return login_at >= cutoff


def get_kite(auto=False):
    """Logged-in KiteConnect. With auto=True, logs in by itself when the token is stale.
    On a cloud runner (TOKEN_FROM_HF=1) the token you pushed from your laptop in the morning is used."""
    if not C.API_KEY:
        raise SystemExit("Set ZERODHA_API_KEY.")
    if C.TOKEN_FROM_HF:
        from . import hf_sync
        hf_sync.pull_token()
        if not token_valid():
            raise SystemExit("The access token on Hugging Face is stale. On your laptop run: python collect.py login")
    if not token_valid():
        if auto:
            login_auto()
        else:
            raise SystemExit("Zerodha token missing/expired. Run:  python collect.py login   (or login --auto)")
    t = json.loads(TOKEN_FILE.read_text())
    kite = KiteConnect(api_key=C.API_KEY, timeout=20)
    kite.set_access_token(t["access_token"])
    return kite, t["access_token"]
