"""Live market recorder (Kite websocket, FULL mode = LTP, volume, OHLC, OI, 5-level market depth, timestamps).

Runs 09:00 -> 15:45 IST. Every tick is kept. Output:
  data/<cat>/zerodha_live/<YYYY-MM-DD>/_parts/part_*.parquet   written every few seconds while running
  data/<cat>/zerodha_live/<YYYY-MM-DD>/<SYMBOL>.parquet        one file per instrument, built at the close
<cat> = stock | index | futures | options.

Note: Zerodha streams snapshots (up to ~1 per second per instrument when something changes), not every
exchange trade. This is the fastest feed Kite Connect offers."""
import json
import logging
import threading
import time
import datetime as dt
from collections import defaultdict
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from kiteconnect import KiteTicker

from . import config as C
from . import universe as U
from .store import safe_name

log = logging.getLogger("live")
MAX_PER_SOCKET = 3000          # Zerodha: 3000 instruments per websocket, 3 sockets per API key
LIVE_DIR = "zerodha_live"


def subscriptions(kite):
    """token -> (category, name) for everything we record today."""
    inst = U.instruments(kite, refresh=True)
    u = U.load(kite)
    meta = {}
    for i in u["indices"]:
        meta[i["token"]] = ("index", i["symbol"])
    for s in u["stocks"]:
        meta[s["token"]] = ("stock", s["symbol"])
    unds = [s["symbol"] for s in u["stocks"]] + list(U.SPOT_FOR)
    for r in U.futures(inst, unds).itertuples():
        meta[int(r.instrument_token)] = ("futures", r.tradingsymbol)
    opt_unds = list(C.LIVE_OPT_UNDERLYINGS) + ([s["symbol"] for s in u["stocks"]] if C.LIVE_STOCK_OPTIONS else [])
    for und in opt_unds:
        stock = und not in C.LIVE_OPT_UNDERLYINGS
        o = U.options_near_atm(kite, inst, und, 1 if stock else C.LIVE_OPT_EXPIRIES, 5 if stock else C.LIVE_OPT_STRIKES)
        for r in o.itertuples():
            meta[int(r.instrument_token)] = ("options", r.tradingsymbol)
    return meta


def flatten(recv_ns, t, meta):
    tok = t["instrument_token"]
    cat, name = meta.get(tok, ("others", str(tok)))
    o = t.get("ohlc") or {}
    r = {"recv_ns": recv_ns, "exch_ts": t.get("exchange_timestamp"), "ltt": t.get("last_trade_time"),
         "cat": cat, "symbol": name, "token": tok, "ltp": t.get("last_price"),
         "ltq": t.get("last_traded_quantity"), "atp": t.get("average_traded_price"), "volume": t.get("volume_traded"),
         "tbq": t.get("total_buy_quantity"), "tsq": t.get("total_sell_quantity"),
         "open": o.get("open"), "high": o.get("high"), "low": o.get("low"), "prev_close": o.get("close"),
         "change_pct": t.get("change"), "oi": t.get("oi"), "oi_day_high": t.get("oi_day_high"),
         "oi_day_low": t.get("oi_day_low")}
    depth = t.get("depth")
    for side, p in (("buy", "b"), ("sell", "s")):
        lv = depth[side] if depth else []
        for i in range(5):
            d = lv[i] if i < len(lv) else {}
            r[f"{p}{i+1}p"], r[f"{p}{i+1}q"], r[f"{p}{i+1}o"] = d.get("price"), d.get("quantity"), d.get("orders")
    return r


class Sink:
    """Collects raw ticks from the socket thread; a flush turns them into parquet part files."""

    def __init__(self, meta, day):
        self.meta, self.day = meta, day
        self.buf, self.lock = [], threading.Lock()
        self.count = defaultdict(int)
        self.seen = set()
        self.last_tick = 0.0
        self.part = 0

    def add(self, ticks):
        now = time.time_ns()
        with self.lock:
            self.buf.append((now, ticks))
        self.last_tick = time.time()

    def dir(self, cat):
        return C.DATA / cat / LIVE_DIR / str(self.day)

    def flush(self):
        with self.lock:
            batch, self.buf = self.buf, []
        if not batch:
            return 0
        rows = [flatten(ns, t, self.meta) for ns, ticks in batch for t in ticks]
        df = pd.DataFrame(rows)
        df["recv_ts"] = pd.to_datetime(df.pop("recv_ns"), unit="ns", utc=True).dt.tz_convert(C.IST).dt.tz_localize(None)
        for col in ("exch_ts", "ltt"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        self.part += 1
        stamp = f"{C.now_ist():%H%M%S}_{self.part:05d}"
        for cat, g in df.groupby("cat"):
            d = self.dir(cat) / "_parts"
            d.mkdir(parents=True, exist_ok=True)
            g.drop(columns="cat").to_parquet(d / f"part_{stamp}.parquet", index=False, compression="zstd")
            self.count[cat] += len(g)
        self.seen.update(df["token"].unique().tolist())
        return len(df)

    def ship(self, tag):
        """Cloud mode: merge the part files of each category into one chunk file, upload it, delete it locally.
        Chunks that fail to upload stay on disk and are retried next time."""
        from . import hf_sync
        stamp = f"{C.now_ist():%H%M}"
        for cat in ("index", "stock", "futures", "options", "others"):
            pdir = self.dir(cat) / "_parts"
            parts = sorted(pdir.glob("part_*.parquet")) if pdir.exists() else []
            if parts:
                tbl = pa.concat_tables([pq.read_table(p) for p in parts], promote_options="permissive")
                tbl = tbl.sort_by([("symbol", "ascending"), ("recv_ts", "ascending")])
                out = self.dir(cat) / f"{tag or 'run'}_{stamp}.parquet"
                tmp = out.with_suffix(".parquet.tmp")
                pq.write_table(tbl, tmp, compression="zstd")
                tmp.replace(out)
                for p in parts:
                    p.unlink()
                pdir.rmdir()
            for f in sorted(self.dir(cat).glob("*_[0-9][0-9][0-9][0-9].parquet")) if self.dir(cat).exists() else []:
                rel = f"data/{cat}/{LIVE_DIR}/{self.day}/{f.name}"
                if hf_sync.upload(f, rel):
                    log.info("uploaded %s (%.1f MB)", rel, f.stat().st_size / 1e6)
                    f.unlink()

    def compact(self):
        """Merge part files into one parquet per instrument; parts are removed only after success."""
        for cat in ("index", "stock", "futures", "options", "others"):
            pdir = self.dir(cat) / "_parts"
            parts = sorted(pdir.glob("part_*.parquet")) if pdir.exists() else []
            if not parts:
                continue
            try:
                tbl = pa.concat_tables([pq.read_table(p) for p in parts], promote_options="permissive")
                tbl = tbl.sort_by([("symbol", "ascending"), ("recv_ts", "ascending")])
                syms = pc.unique(tbl["symbol"]).to_pylist()
                for s in syms:
                    sub = tbl.filter(pc.equal(tbl["symbol"], s))
                    pq.write_table(sub, self.dir(cat) / f"{safe_name(s)}.parquet", compression="zstd")
                for p in parts:
                    p.unlink()
                pdir.rmdir()
                log.info("compacted %s: %d rows, %d instruments", cat, tbl.num_rows, len(syms))
            except Exception as e:
                log.error("compaction of %s failed (%s) - part files kept in %s", cat, e, pdir)


def run(kite, access_token, end_at=None, tag="", upload=False):
    """end_at: 'HH:MM' override. tag: label for this run (cloud jobs A/B). upload: ship chunks to Hugging Face."""
    now = C.now_ist()
    day = now.date()
    end = dt.datetime.combine(day, dt.time.fromisoformat(end_at) if end_at else C.LIVE_END)
    if now >= end:
        raise SystemExit("Market session is over for today.")
    if now.weekday() >= 5:
        raise SystemExit("Weekend - nothing to record.")
    meta = subscriptions(kite)
    tokens = list(meta)
    if not tokens or len(tokens) > 3 * MAX_PER_SOCKET:
        raise RuntimeError(f"Unsupported subscription count: {len(tokens)}")
    subs_file = C.STATE / f"live_subscriptions_{day}.json"
    subs_file.write_text(json.dumps({str(k): v for k, v in meta.items()}))
    if upload:
        from . import hf_sync
        hf_sync.upload(subs_file, f"state/{subs_file.name}")
    by_cat = defaultdict(int)
    for c, _ in meta.values():
        by_cat[c] += 1
    log.info("subscribing %d instruments: %s", len(tokens), dict(by_cat))

    start = dt.datetime.combine(day, C.LIVE_START) - dt.timedelta(minutes=1)
    if C.now_ist() < start:
        log.info("waiting for %s", start.time())
        while C.now_ist() < start:
            time.sleep(5)

    sink = Sink(meta, day)
    sockets = []
    for n, i in enumerate(range(0, len(tokens), MAX_PER_SOCKET)):
        chunk = tokens[i:i + MAX_PER_SOCKET]
        kws = KiteTicker(C.API_KEY, access_token, reconnect=True, reconnect_max_tries=300, reconnect_max_delay=30)

        def on_connect(ws, resp, chunk=chunk, n=n):
            ws.subscribe(chunk)
            ws.set_mode(ws.MODE_FULL, chunk)
            log.info("socket %d connected, %d instruments", n, len(chunk))

        kws.on_ticks = lambda ws, ticks: sink.add(ticks)
        kws.on_connect = on_connect
        kws.on_close = lambda ws, code, reason, n=n: log.warning("socket %d closed: %s %s", n, code, reason)
        kws.on_error = lambda ws, code, reason, n=n: log.error("socket %d error: %s %s", n, code, reason)
        kws.on_reconnect = lambda ws, attempt, n=n: log.warning("socket %d reconnecting (%d)", n, attempt)
        kws.on_noreconnect = lambda ws, n=n: C.notify(f"live: socket {n} gave up reconnecting")
        if n == 0:
            kws.connect(threaded=True)
        else:
            from twisted.internet import reactor
            reactor.callFromThread(kws.connect)
        sockets.append(kws)
    C.notify(f"live started: {len(tokens)} instruments")

    started, last_report, last_ship, warned = time.time(), time.time(), time.time(), False
    try:
        while C.now_ist() < end:
            time.sleep(C.LIVE_FLUSH_SECONDS)
            sink.flush()
            t = C.now_ist()
            if t.time() >= dt.time(9, 20) and time.time() - started > 180 and sum(sink.count.values()) == 0:
                raise RuntimeError("No ticks after 09:20; holiday or feed failure. Retry required.")
            if dt.time(9, 20) <= t.time() <= dt.time(15, 30) and sink.last_tick and time.time() - sink.last_tick > 300:
                raise RuntimeError("Live feed stalled for five minutes; restart required")
            if dt.time(9, 15) <= t.time() <= dt.time(15, 30) and time.time() - sink.last_tick > 120 and not warned:
                C.notify("live: no ticks for 2 minutes during market hours")
                warned = True
            elif time.time() - sink.last_tick < 10:
                warned = False
            if upload and time.time() - last_ship > C.LIVE_SHIP_MINUTES * 60:
                last_ship = time.time()
                try:
                    sink.ship(tag)
                except Exception as e:
                    log.error("ship failed (%s) - will retry", e)
            if time.time() - last_report > 300:
                last_report = time.time()
                log.info("ticks so far %s | instruments seen %d/%d", dict(sink.count), len(sink.seen), len(tokens))
    finally:
        for k in sockets:
            try:
                k.close()
            except Exception:
                pass
        time.sleep(2)
        sink.flush()
        if upload:
            sink.ship(tag)
            pending = [p for cat in ("index", "stock", "futures", "options", "others")
                       for p in sink.dir(cat).rglob("*.parquet")]
            if pending:
                raise RuntimeError(f"{len(pending)} live files were not uploaded; retained for recovery")
        else:
            sink.compact()
        if not sum(sink.count.values()):
            raise RuntimeError("Recording ended without any ticks; session is incomplete")
        msg = f"live done {day} {tag}: {sum(sink.count.values()):,} ticks, {len(sink.seen)}/{len(tokens)} instruments"
        log.info(msg)
        C.notify(msg)
