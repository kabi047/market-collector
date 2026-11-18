"""Always-on VM worker. The VM clock starts recording; no external cron is needed."""
import logging
import subprocess
import sys
import time

from mdc import config as C
from mdc.cloud_capture import eligible


def main():
    log = C.setup_logging("worker")
    completed = None
    while True:
        now = C.now_ist()
        if completed != now.date() and eligible("server", now):
            result = subprocess.run([sys.executable, "collect.py", "cloud-capture", "--slot", "server"], cwd=C.ROOT)
            if result.returncode == 0:
                completed = now.date()
                log.info("Today's capture completed")
            else:
                log.error("Recorder failed (%s); retrying in one minute", result.returncode)
        time.sleep(60)


if __name__ == "__main__":
    main()
