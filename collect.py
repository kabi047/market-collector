"""Market data collector - one entry point.

  python collect.py login [--auto]          daily Zerodha login (token valid until ~06:00 next day)
  python collect.py universe                build / refresh the instrument list (top ~150 NSE + indices)
  python collect.py historical              all candles: day from 2000, 1-minute as far back as Zerodha has
  python collect.py live                    record the live market 09:00-15:45 (ticks + 5-level depth)
  python collect.py eod                     after the close: today's F&O minute candles + historical top-up
  python collect.py fundamentals            financials, filings, results, shareholding, annual reports
  python collect.py news                    company + market news
  python collect.py check                   verify the stored/pulled token works
  python collect.py status                  what has been collected so far

Add --auto to any Zerodha command to log in automatically when the token is stale (cloud server)."""
import argparse
import json
import sys

from mdc import config as C


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("login"); p.add_argument("--auto", action="store_true")
    p = sub.add_parser("universe"); p.add_argument("--auto", action="store_true")
    p = sub.add_parser("historical")
    p.add_argument("--intervals", nargs="+", default=["day", "minute"], choices=list(C.CHUNK_DAYS))
    p.add_argument("--symbols", nargs="+", help="limit to these symbols (default: whole universe)")
    p.add_argument("--no-fno", action="store_true", help="skip futures (continuous day + live contracts)")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--auto", action="store_true")
    p = sub.add_parser("live"); p.add_argument("--auto", action="store_true")
    p.add_argument("--end", help="stop at HH:MM IST (default 15:45)")
    p.add_argument("--tag", default="", help="label for this run, e.g. A or B (cloud jobs)")
    p.add_argument("--upload", action="store_true", help="send 30-minute chunks to Hugging Face (cloud mode)")
    sub.add_parser("check")
    p = sub.add_parser("eod"); p.add_argument("--auto", action="store_true")
    p = sub.add_parser("fundamentals")
    p.add_argument("--symbols", nargs="+")
    p.add_argument("--reports", type=int, default=C.ANNUAL_REPORTS_PER_COMPANY, help="annual report PDFs per company (0 = none)")
    p.add_argument("--skip-yahoo", action="store_true"); p.add_argument("--skip-nse", action="store_true")
    p = sub.add_parser("news"); p.add_argument("--symbols", nargs="+")
    sub.add_parser("status")
    a = ap.parse_args()
    log = C.setup_logging(a.cmd)

    try:
        if a.cmd == "login":
            from mdc import kite_auth
            kite_auth.login_auto() if a.auto else kite_auth.login_manual()
        elif a.cmd == "universe":
            from mdc import kite_auth, universe
            kite, _ = kite_auth.get_kite(a.auto)
            universe.build(kite)
        elif a.cmd == "historical":
            from mdc import kite_auth, historical
            kite, _ = kite_auth.get_kite(a.auto)
            jobs = historical.build_jobs(kite, a.intervals, a.symbols, fno=not a.no_fno)
            historical.run(kite, jobs, a.workers)
        elif a.cmd == "live":
            from mdc import kite_auth, live
            kite, token = kite_auth.get_kite(a.auto)
            live.run(kite, token, a.end, a.tag, a.upload)
        elif a.cmd == "eod":
            from mdc import kite_auth, historical
            kite, _ = kite_auth.get_kite(a.auto)
            jobs = []
            f = C.STATE / f"live_subscriptions_{C.now_ist().date()}.json"
            if not f.exists():          # recorded on the cloud runner: fetch today's contract list
                from mdc import hf_sync
                if hf_sync.enabled():
                    try:
                        hf_sync.download(f"state/{f.name}", f)
                    except Exception as e:
                        log.warning("no cloud subscription list for today (%s)", e)
            if f.exists():   # minute candles + OI for every F&O contract recorded today (they vanish after expiry)
                for tok, (cat, name) in json.loads(f.read_text()).items():
                    if cat in ("futures", "options"):
                        jobs.append(historical.Job(cat, name, int(tok), "minute", oi=True, stop_after_empty=1))
            seen = {j.key for j in jobs}
            jobs += [j for j in historical.build_jobs(kite) if j.key not in seen]
            historical.run(kite, jobs)
        elif a.cmd == "fundamentals":
            from mdc import fundamentals
            fundamentals.run(a.symbols, a.reports, a.skip_yahoo, a.skip_nse)
        elif a.cmd == "news":
            from mdc import news
            news.run(a.symbols)
        elif a.cmd == "check":
            from mdc import kite_auth
            kite, _ = kite_auth.get_kite()
            print("Token OK for Zerodha user:", kite.profile()["user_id"])
        elif a.cmd == "status":
            status()
    except SystemExit:
        raise
    except Exception as e:
        log.exception("failed")
        C.notify(f"{a.cmd} FAILED: {e}")
        sys.exit(1)


def status():
    import pandas as pd
    from mdc.kite_auth import token_valid
    print(f"data folder : {C.DATA}")
    print(f"zerodha token valid today: {token_valid()}")
    for cat in ("index", "stock", "futures", "options"):
        for d in sorted((C.DATA / cat / "zerodha").glob("*")) if (C.DATA / cat / "zerodha").exists() else []:
            files = list(d.glob("*.parquet"))
            if not files:
                continue
            lo = hi = None
            rows = 0
            for f in files:
                t = pd.read_parquet(f, columns=["ts"])["ts"]
                rows += len(t)
                lo = t.min() if lo is None else min(lo, t.min())
                hi = t.max() if hi is None else max(hi, t.max())
            print(f"{cat:8s} {d.name:15s} {len(files):5d} files {rows:>13,} rows  {lo} -> {hi}")
        live = C.DATA / cat / "zerodha_live"
        if live.exists():
            days = sorted(p.name for p in live.iterdir() if p.is_dir())
            if days:
                print(f"{cat:8s} {'live ticks':15s} {len(days):5d} days  {days[0]} -> {days[-1]}")
    for sub in ("fundamentals", "corporate", "reports", "news"):
        p = C.DATA / "others" / sub
        if p.exists():
            print(f"others   {sub:15s} {sum(1 for _ in p.rglob('*') if _.is_file()):5d} files")


if __name__ == "__main__":
    main()
