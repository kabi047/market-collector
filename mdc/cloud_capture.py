"""Restartable recording sessions started by either external cron or GitHub."""
import json
import logging
import os

from . import config as C, hf_sync, kite_auth, universe, live

log = logging.getLogger("capture")
WINDOWS = {"morning": ("08:15", "14:00"), "afternoon": ("13:40", "15:45"),
           "server": ("08:15", "15:45")}


def eligible(slot, now):
    start, end = WINDOWS[slot]
    return now.weekday() < 5 and start <= now.strftime("%H:%M") < end


def run(slot):
    now = C.now_ist()
    if slot != "probe" and not eligible(slot, now):
        print(f"{slot}: outside recording window ({now.isoformat()}); skipped")
        return
    marker = C.STATE / f"capture_{now.date()}_{slot}.json"
    remote = f"state/{marker.name}"
    if slot != "probe":
        try:
            hf_sync.download(remote, marker)
            if json.loads(marker.read_text())["status"] == "complete":
                print(f"{slot}: already completed today; skipped")
                return
        except Exception:
            pass  # First attempt, or previous attempt did not finish.
    # Refresh here: recording no longer depends on a separate scheduled login.
    kite_auth.cloud_login()
    kite, token = kite_auth.get_kite()
    u = universe.build(kite)
    stocks = [s for s in u["stocks"] if s["list"] == "nifty50"]
    if len(stocks) != 50:
        raise RuntimeError(f"Only {len(stocks)} Nifty 50 constituents resolved")
    if slot == "probe":
        quotes = kite.quote(["NSE:" + s["symbol"] for s in stocks])
        if len(quotes) != 50 or not all(
                len(q.get("depth", {}).get(side, [])) == 5
                for q in quotes.values() for side in ("buy", "sell")):
            raise RuntimeError("Nifty 50 quotes or five-level depth are incomplete")
    else:
        tag = f"{slot}_{os.getenv('GITHUB_RUN_ID', str(os.getpid()))}"
        live.run(kite, token, WINDOWS[slot][1], tag, upload=True)
    marker.write_text(json.dumps({"status": "complete", "slot": slot,
                                  "finished_at": C.now_ist().isoformat(),
                                  "nifty_companies": len(stocks)}))
    if not hf_sync.upload(marker, remote):
        raise RuntimeError("Could not save capture completion status")
    print(f"{slot}: completed and saved to private storage")
