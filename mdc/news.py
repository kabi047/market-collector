"""Company + market news from free RSS feeds, appended forever (deduplicated by link).

  data/others/news/<SYM>.parquet     Google News search for the company (last 7 days each run)
  data/others/news/_MARKET.parquet   general Indian market feeds
Run it daily (or several times a day); history accumulates."""
import logging
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import pandas as pd
import requests

from . import config as C
from . import universe as U
from .store import append_dedup, safe_name

log = logging.getLogger("news")
OUT = C.DATA / "others" / "news"
MARKET_FEEDS = {
    "economictimes_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "economictimes_stocks": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "moneycontrol_markets": "https://www.moneycontrol.com/rss/marketreports.xml",
    "moneycontrol_business": "https://www.moneycontrol.com/rss/business.xml",
    "livemint_markets": "https://www.livemint.com/rss/markets",
    "business_standard_markets": "https://www.business-standard.com/rss/markets-106.rss",
}


def parse_rss(xml_text, feed):
    rows = []
    for it in ET.fromstring(xml_text).iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()
        try:
            pub = parsedate_to_datetime(g("pubDate")).astimezone(C.IST).replace(tzinfo=None)
        except Exception:
            pub = pd.NaT
        src = it.find("source")
        rows.append(dict(published=pub, title=g("title"), link=g("link"), summary=re.sub("<[^>]+>", "", g("description"))[:2000],
                         source=src.text if src is not None else feed, feed=feed, fetched_at=C.now_ist()))
    return pd.DataFrame(rows)


def get(url):
    r = requests.get(url, headers={"User-Agent": C.BROWSER_UA}, timeout=30)
    r.raise_for_status()
    return r.text


def clean_name(name):
    return re.sub(r"\b(ltd|limited|corporation|corp|india|of)\b\.?", "", str(name), flags=re.I).replace("&", " ").strip()


def run(symbols=None):
    OUT.mkdir(parents=True, exist_ok=True)
    n_market = 0
    for feed, url in MARKET_FEEDS.items():
        try:
            df = parse_rss(get(url), feed)
            append_dedup(OUT / "_MARKET.parquet", df, key="link", sort="published")
            n_market += len(df)
        except Exception as e:
            log.warning("market feed %s: %s", feed, e)
    u = U.load()
    stocks = [s for s in u["stocks"] if not symbols or s["symbol"] in symbols]
    total = 0
    for i, s in enumerate(stocks, 1):
        q = f'"{clean_name(s["name"])}" OR "{s["symbol"]}" share when:7d'
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
        try:
            df = parse_rss(get(url), "google_news")
            df["symbol"] = s["symbol"]
            append_dedup(OUT / f"{safe_name(s['symbol'])}.parquet", df, key="link", sort="published")
            total += len(df)
        except Exception as e:
            log.warning("%s news: %s", s["symbol"], e)
        time.sleep(1.0)
    msg = f"news done: {total} company items, {n_market} market items fetched"
    log.info(msg)
    C.notify(msg)
