"""Historical candles from Kite Connect, one parquet file per instrument per interval.

  data/stock/zerodha/minute/RELIANCE.parquet      1-minute candles, as far back as Zerodha has them (~2015+)
  data/stock/zerodha/day/RELIANCE.parquet         daily candles from 2000 (or listing)
  data/index/zerodha/{minute,day}/NIFTY_50.parquet
  data/futures/zerodha/day_continuous/RELIANCE.parquet   continuous front-month futures, with OI
  data/futures/zerodha/minute/RELIANCE26NOVFUT.parquet   live contracts, with OI
  data/options/zerodha/minute/NIFTY26N0324000CE.parquet  (written by 'eod' for contracts seen live)

Both minute and day are kept - nothing is overwritten. Re-running only fetches what is missing
(new candles at the end, and older history if a previous backfill was interrupted)."""
import logging
import threading
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import pandas as pd
from kiteconnect import exceptions as kex

from . import config as C
from . import universe as U
from .store import dataset_path, append_dedup, ts_range, JsonState

log = logging.getLogger("historical")
SOURCE = "zerodha"


@dataclass
class Job:
    cat: str            # index | stock | futures | options
    name: str           # file name (symbol / tradingsymbol)
    token: int
    interval: str       # minute | day | ...
    folder: str = ""    # sub folder, defaults to interval
    oi: bool = False
    continuous: bool = False
    stop_after_empty: int = 0

    def __post_init__(self):
        self.folder = self.folder or self.interval
        self.stop_after_empty = self.stop_after_empty or C.STOP_AFTER_EMPTY.get(self.interval, 2)

    @property
    def path(self):
        return dataset_path(self.cat, SOURCE, self.folder, self.name)

    @property
    def key(self):
        return f"{self.cat}/{self.folder}/{self.name}"


class Limiter:
    def __init__(self, per_sec):
        self.gap, self.next, self.lock = 1.0 / per_sec, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def to_frame(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={"date": "ts"})
    ts = pd.to_datetime(df["ts"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert(C.IST).dt.tz_localize(None)
    df["ts"] = ts
    return df


def fetch(kite, lim, job, start, end):
    """Candles for [start, end] (dates, inclusive). Retries on network / rate-limit errors."""
    a = dt.datetime.combine(start, dt.time(0, 0))
    b = dt.datetime.combine(end, dt.time(23, 59, 59))
    for attempt in range(8):
        lim.wait()
        try:
            return to_frame(kite.historical_data(job.token, a, b, job.interval, continuous=job.continuous, oi=job.oi))
        except kex.TokenException:
            raise
        except kex.InputException as e:
            log.warning("%s %s..%s rejected: %s", job.key, start, end, e)
            return pd.DataFrame()
        except Exception as e:                       # network, 429 too many requests, 5xx
            wait = min(60, 2 ** attempt)
            log.warning("%s %s..%s error (%s) retry in %ss", job.key, start, end, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"{job.key}: giving up on {start}..{end}")


def last_session_date():
    """Most recent weekday whose session is over (holidays just cost one extra request)."""
    now = C.now_ist()
    d = now.date() if now.time() >= C.LIVE_END else now.date() - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def run_job(kite, lim, state, job):
    chunk = C.CHUNK_DAYS[job.interval]
    today = C.now_ist().date()
    first, last = ts_range(job.path)
    if last is not None and last.date() >= last_session_date() and state.get(job.key, {}).get("backfill_done"):
        return 0                                   # already complete - skip without any request
    added = 0
    # 1) bring the end of the file up to date
    if last is not None:
        d = last.date()
        while d <= today:
            e = min(d + dt.timedelta(days=chunk - 1), today)
            df = fetch(kite, lim, job, d, e)
            append_dedup(job.path, df)
            added += len(df)
            d = e + dt.timedelta(days=1)
    # 2) walk backwards until history runs out (resumable)
    st = state.get(job.key, {})
    if not st.get("backfill_done"):
        if st.get("oldest"):
            cursor = dt.date.fromisoformat(st["oldest"]) - dt.timedelta(days=1)
        elif first is not None:
            cursor = first.date() - dt.timedelta(days=1)
        else:
            cursor = today
        empties, buf, n, oldest = 0, [], 0, st.get("oldest")
        while cursor >= C.EARLIEST and empties < job.stop_after_empty:
            start = max(cursor - dt.timedelta(days=chunk - 1), C.EARLIEST)
            df = fetch(kite, lim, job, start, cursor)
            if df.empty:
                empties += 1
            else:
                empties = 0
                buf.append(df)
            oldest, cursor, n = str(start), start - dt.timedelta(days=1), n + 1
            if n % 20 == 0:
                if buf:
                    new = pd.concat(buf); append_dedup(job.path, new); added += len(new); buf = []
                state.set(job.key, {"oldest": oldest, "backfill_done": False})
        if buf:
            new = pd.concat(buf); append_dedup(job.path, new); added += len(new)
        state.set(job.key, {"oldest": oldest, "backfill_done": True})
    return added


def build_jobs(kite, intervals=("day", "minute"), symbols=None, fno=True):
    u = U.load(kite)
    stocks = [s for s in u["stocks"] if not symbols or s["symbol"] in symbols]
    indices = [i for i in u["indices"] if not symbols or i["symbol"] in symbols]
    jobs = []
    for iv in intervals:                       # all day jobs first (fast), then minute
        jobs += [Job("index", i["symbol"], i["token"], iv) for i in indices]
        jobs += [Job("stock", s["symbol"], s["token"], iv) for s in stocks]
    if fno:
        inst = U.instruments(kite)
        unds = [s["symbol"] for s in stocks] + ([] if symbols else list(U.SPOT_FOR))
        fut = U.futures(inst, unds, months=3)
        front = fut.sort_values("expiry").groupby("name").head(1)
        jobs += [Job("futures", r.name, int(r.instrument_token), "day", folder="day_continuous", oi=True,
                     continuous=True) for r in front.itertuples()]
        if "minute" in intervals:
            jobs += [Job("futures", r.tradingsymbol, int(r.instrument_token), "minute", oi=True, stop_after_empty=1)
                     for r in fut.itertuples()]
    return jobs


def run(kite, jobs, workers=3):
    """Process jobs in parallel; the shared limiter keeps the total under Zerodha's 3 requests/second."""
    lim = Limiter(C.HIST_REQ_PER_SEC)
    state = JsonState(C.STATE / "historical_progress.json")
    log.info("%d datasets to update", len(jobs))
    t0, total, failed = time.time(), 0, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_job, kite, lim, state, j): j for j in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            j = futs[f]
            try:
                n = f.result()
                total += n
                log.info("[%d/%d] %-40s +%d rows", i, len(jobs), j.key, n)
            except kex.TokenException:
                for other in futs:
                    other.cancel()
                C.notify("historical: Zerodha token expired - stopped")
                raise SystemExit("Zerodha token expired/invalid. Log in again and re-run (it resumes).")
            except Exception as e:
                failed.append(j.key)
                log.error("[%d/%d] %s FAILED: %s", i, len(jobs), j.key, e)
    msg = f"historical done: +{total:,} rows in {(time.time()-t0)/60:.1f} min, {len(failed)} failed"
    log.info(msg + (f": {failed}" if failed else ""))
    C.notify(msg)
    return failed
