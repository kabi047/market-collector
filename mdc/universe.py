"""Which instruments we collect.
stocks  = Nifty 50 + Nifty Next 50 + Nifty Midcap 50 (= top ~150 NSE companies; all Sensex 30 stocks are inside)
          + EXTRA_SYMBOLS from .env.  Saved in state/universe.json - edit it by hand if you like.
indices = config.INDICES
F&O     = futures / options contracts resolved fresh each day from the instrument dump."""
import io
import json
import logging
import pandas as pd
import requests

from . import config as C

log = logging.getLogger("universe")
UNIVERSE_FILE = C.STATE / "universe.json"
INSTR_DIR = C.DATA / "others" / "zerodha_instruments"
SPOT_FOR = {"NIFTY": ("NSE", "NIFTY 50"), "BANKNIFTY": ("NSE", "NIFTY BANK"), "FINNIFTY": ("NSE", "NIFTY FIN SERVICE"),
            "MIDCPNIFTY": ("NSE", "NIFTY MID SELECT"), "SENSEX": ("BSE", "SENSEX"), "BANKEX": ("BSE", "BANKEX")}


def instruments(kite, refresh=False):
    """Full Kite instrument dump (all exchanges), cached once per day and kept as history."""
    INSTR_DIR.mkdir(parents=True, exist_ok=True)
    f = INSTR_DIR / f"instruments_{C.now_ist():%Y-%m-%d}.csv.gz"
    if f.exists() and not refresh:
        df = pd.read_csv(f, parse_dates=["expiry"])
    else:
        df = pd.DataFrame(kite.instruments())
        df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
        df.to_csv(f, index=False, compression="gzip")
        log.info("instrument dump saved: %s (%d rows)", f.name, len(df))
    df["tradingsymbol"] = df["tradingsymbol"].astype(str)
    df["name"] = df["name"].fillna("").astype(str)
    return df


def _index_list(name):
    for url in (f"https://archives.nseindia.com/content/indices/ind_{name}list.csv",
                f"https://niftyindices.com/IndexConstituent/ind_{name}list.csv"):
        try:
            r = requests.get(url, headers={"User-Agent": C.BROWSER_UA}, timeout=20, allow_redirects=False)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            return df
        except Exception as e:
            log.warning("could not fetch %s: %s", url, e)
    raise SystemExit(f"Could not download index list {name}. Check internet, or add symbols via EXTRA_SYMBOLS.")


def build(kite):
    inst = instruments(kite)
    nse_eq = inst[(inst.exchange == "NSE") & (inst.segment == "NSE") & (inst.instrument_type == "EQ")].set_index("tradingsymbol")
    rows, seen = [], set()
    for lst in C.INDEX_LISTS:
        for r in _index_list(lst).to_dict("records"):
            s = str(r.get("Symbol", "")).strip()
            if s and s not in seen:
                seen.add(s)
                rows.append(dict(symbol=s, name=r.get("Company Name", s), industry=r.get("Industry", ""),
                                 isin=r.get("ISIN Code", ""), list=lst))
    for s in C.EXTRA_SYMBOLS:
        if s not in seen:
            seen.add(s); rows.append(dict(symbol=s, name=s, industry="", isin="", list="extra"))
    stocks = []
    for r in rows:
        if r["symbol"] in nse_eq.index:
            r["token"] = int(nse_eq.loc[r["symbol"], "instrument_token"])
            stocks.append(r)
        else:
            log.warning("not found on NSE in Kite dump: %s", r["symbol"])
    idx = inst[inst.segment == "INDICES"]
    indices = []
    for exch, ts in C.INDICES:
        m = idx[(idx.exchange == exch) & (idx.tradingsymbol == ts)]
        if len(m):
            indices.append(dict(symbol=ts, exchange=exch, token=int(m.iloc[0].instrument_token)))
        else:
            log.warning("index not found in Kite dump: %s:%s", exch, ts)
    u = dict(built=str(C.now_ist().date()), stocks=stocks, indices=indices)
    UNIVERSE_FILE.write_text(json.dumps(u, indent=1, default=str))
    log.info("universe: %d stocks, %d indices -> %s", len(stocks), len(indices), UNIVERSE_FILE)
    return u


def load(kite=None):
    if UNIVERSE_FILE.exists():
        return json.loads(UNIVERSE_FILE.read_text())
    if kite is None:
        raise SystemExit("No universe yet. Run: python collect.py universe")
    return build(kite)


def futures(inst, underlyings, months=C.LIVE_FUT_MONTHS):
    """Nearest `months` futures contracts for each underlying (NFO + BFO)."""
    f = inst[inst.instrument_type.eq("FUT") & inst.exchange.isin(["NFO", "BFO"]) & inst.name.isin(underlyings)]
    f = f[f.expiry >= pd.Timestamp(C.now_ist().date())].sort_values("expiry")
    return f.groupby("name", group_keys=False).head(months)


def options_near_atm(kite, inst, underlying, expiries, strikes):
    """CE+PE contracts within `strikes` strikes of the current spot, nearest `expiries` expiries."""
    o = inst[inst.instrument_type.isin(["CE", "PE"]) & inst.exchange.isin(["NFO", "BFO"]) & (inst.name == underlying)]
    o = o[o.expiry >= pd.Timestamp(C.now_ist().date())]
    if o.empty:
        return o
    exps = sorted(o.expiry.unique())[:expiries]
    o = o[o.expiry.isin(exps)]
    key = "%s:%s" % SPOT_FOR[underlying] if underlying in SPOT_FOR else f"NSE:{underlying}"
    try:
        spot = kite.ltp([key])[key]["last_price"]
    except Exception as e:
        log.warning("spot for %s unavailable (%s); using median strike", underlying, e)
        spot = float(o.strike.median())
    out = []
    for _, g in o.groupby("expiry"):
        ks = sorted(g.strike.unique())
        i = min(range(len(ks)), key=lambda j: abs(ks[j] - spot))
        keep = ks[max(0, i - strikes): i + strikes + 1]
        out.append(g[g.strike.isin(keep)])
    return pd.concat(out)
