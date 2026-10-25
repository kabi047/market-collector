"""Company data from free public sources (Zerodha's API has no fundamentals/news).

  data/others/fundamentals/<SYM>/   Yahoo Finance: profile/ratios snapshot per day, quarterly + annual income
                                    statement, balance sheet, cash flow (merged over time, so history grows),
                                    dividends, splits, holders, analyst estimates, earnings dates
  data/others/corporate/<SYM>/      NSE: announcements/filings, quarterly + annual result filings,
                                    corporate actions, board meetings, shareholding pattern, quote snapshot
  data/others/reports/<SYM>/        NSE annual report PDFs (latest ANNUAL_REPORTS_PER_COMPANY)
Every source is best effort: a failing endpoint is logged and skipped, never stops the run."""
import json
import logging
import time
import warnings
from pathlib import Path
import pandas as pd
import requests

from . import config as C
from . import universe as U
from .store import safe_name

log = logging.getLogger("fundamentals")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", module="yfinance")
OUT = C.DATA / "others"


def _merge_statement(new, path):
    """Rows = line items, columns = period end dates. New values win; old periods are kept."""
    if new is None or getattr(new, "empty", True):
        return
    new = new.copy()
    new.columns = [str(pd.Timestamp(c).date()) if not isinstance(c, str) else c for c in new.columns]
    if path.exists():
        old = pd.read_csv(path, index_col=0)
        new = new.combine_first(old)
    new = new[sorted(new.columns, reverse=True)]
    new.to_csv(path)


def _save_df(df, path):
    if df is not None and not getattr(df, "empty", True):
        df.to_csv(path)


def yahoo(symbol):
    import yfinance as yf
    d = OUT / "fundamentals" / safe_name(symbol)
    d.mkdir(parents=True, exist_ok=True)
    t = yf.Ticker(f"{symbol}.NS")
    today = C.now_ist().date()
    done = []

    def attempt(label, fn):
        try:
            fn()
            done.append(label)
        except Exception as e:
            log.debug("%s yahoo %s: %s", symbol, label, e)

    def info():
        (d / "info").mkdir(exist_ok=True)
        (d / "info" / f"{today}.json").write_text(json.dumps(t.info, indent=1, default=str))

    attempt("info", info)
    for label, attr in [("income_quarterly", "quarterly_income_stmt"), ("income_annual", "income_stmt"),
                        ("balance_quarterly", "quarterly_balance_sheet"), ("balance_annual", "balance_sheet"),
                        ("cashflow_quarterly", "quarterly_cashflow"), ("cashflow_annual", "cashflow")]:
        attempt(label, lambda attr=attr, label=label: _merge_statement(getattr(t, attr), d / f"{label}.csv"))
    for label, attr in [("dividends", "dividends"), ("splits", "splits"), ("major_holders", "major_holders"),
                        ("institutional_holders", "institutional_holders"), ("mutualfund_holders", "mutualfund_holders"),
                        ("insider_transactions", "insider_transactions"), ("recommendations", "recommendations"),
                        ("upgrades_downgrades", "upgrades_downgrades"), ("earnings_dates", "earnings_dates"),
                        ("earnings_estimate", "earnings_estimate"), ("revenue_estimate", "revenue_estimate"),
                        ("eps_trend", "eps_trend")]:
        attempt(label, lambda attr=attr, label=label: _save_df(getattr(t, attr), d / f"{label}.csv"))
    return done


class NSE:
    BASE = "https://www.nseindia.com"

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": C.BROWSER_UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                               "Referer": self.BASE + "/"})
        self._cookies()

    def _cookies(self):
        try:
            self.s.get(self.BASE, timeout=20)
            self.s.get(self.BASE + "/companies-listing/corporate-filings-announcements", timeout=20)
        except Exception as e:
            log.warning("NSE homepage: %s", e)

    def get(self, path, **params):
        for i in range(3):
            time.sleep(0.6)
            try:
                r = self.s.get(f"{self.BASE}/api/{path}", params=params, timeout=30)
                if r.status_code in (401, 403):
                    self._cookies(); continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if i == 2:
                    raise
                log.debug("NSE %s retry: %s", path, e)
                time.sleep(2)


def _merge_json_list(path, items):
    items = items if isinstance(items, list) else (items.get("data") if isinstance(items, dict) and isinstance(items.get("data"), list) else None)
    if items is None:
        return False
    old = json.loads(path.read_text()) if path.exists() else []
    seen = {json.dumps(x, sort_keys=True, default=str) for x in old}
    old += [x for x in items if json.dumps(x, sort_keys=True, default=str) not in seen]
    path.write_text(json.dumps(old, indent=1, default=str))
    return True


def nse_company(nse, symbol, reports=C.ANNUAL_REPORTS_PER_COMPANY):
    d = OUT / "corporate" / safe_name(symbol)
    d.mkdir(parents=True, exist_ok=True)
    done = []
    lists = {
        "announcements": ("corporate-announcements", dict(index="equities", symbol=symbol)),
        "results_quarterly": ("corporates-financial-results", dict(index="equities", symbol=symbol, period="Quarterly")),
        "results_annual": ("corporates-financial-results", dict(index="equities", symbol=symbol, period="Annual")),
        "corporate_actions": ("corporates-corporateActions", dict(index="equities", symbol=symbol)),
        "board_meetings": ("corporate-board-meetings", dict(index="equities", symbol=symbol)),
        "shareholding": ("corporate-share-holdings-master", dict(index="equities", symbol=symbol)),
        "annual_reports": ("annual-reports", dict(index="equities", symbol=symbol)),
    }
    for label, (path, params) in lists.items():
        try:
            if _merge_json_list(d / f"{label}.json", nse.get(path, **params)):
                done.append(label)
        except Exception as e:
            log.debug("%s NSE %s: %s", symbol, label, e)
    try:
        snap = {"quote": nse.get("quote-equity", symbol=symbol),
                "trade_info": nse.get("quote-equity", symbol=symbol, section="trade_info")}
        (d / "quote").mkdir(exist_ok=True)
        (d / "quote" / f"{C.now_ist().date()}.json").write_text(json.dumps(snap, indent=1, default=str))
        done.append("quote")
    except Exception as e:
        log.debug("%s NSE quote: %s", symbol, e)
    if reports and (d / "annual_reports.json").exists():
        done += _download_reports(nse, symbol, json.loads((d / "annual_reports.json").read_text()), reports)
    return done


def _download_reports(nse, symbol, items, n):
    out = OUT / "reports" / safe_name(symbol)
    got = []
    items = sorted([x for x in items if isinstance(x, dict) and x.get("fileName")],
                   key=lambda x: str(x.get("toYr", "")), reverse=True)[:n]
    for x in items:
        url = x["fileName"]
        f = out / f"AR_{x.get('fromYr', '')}-{x.get('toYr', '')}{Path(url).suffix or '.pdf'}"
        if f.exists():
            continue
        try:
            out.mkdir(parents=True, exist_ok=True)
            r = nse.s.get(url, timeout=120)
            r.raise_for_status()
            f.write_bytes(r.content)
            got.append(f.name)
        except Exception as e:
            log.debug("%s report %s: %s", symbol, url, e)
    return got


def run(symbols=None, reports=C.ANNUAL_REPORTS_PER_COMPANY, skip_yahoo=False, skip_nse=False):
    u = U.load()
    stocks = [s for s in u["stocks"] if not symbols or s["symbol"] in symbols]
    nse = None if skip_nse else NSE()
    ok = 0
    today = C.now_ist().date()
    for i, s in enumerate(stocks, 1):
        sym = s["symbol"]
        if (OUT / "fundamentals" / safe_name(sym) / "info" / f"{today}.json").exists() and \
                (OUT / "corporate" / safe_name(sym) / "quote" / f"{today}.json").exists():
            ok += 1
            log.info("[%d/%d] %-12s already collected today - skipped", i, len(stocks), sym)
            continue
        y = [] if skip_yahoo else yahoo(sym)
        n = [] if skip_nse else nse_company(nse, sym, reports)
        ok += bool(y or n)
        log.info("[%d/%d] %-12s yahoo:%d nse:%d %s", i, len(stocks), sym, len(y), len(n),
                 "" if (y or n) else "(nothing - blocked?)")
    msg = f"fundamentals done: {ok}/{len(stocks)} companies with data"
    log.info(msg)
    C.notify(msg)
