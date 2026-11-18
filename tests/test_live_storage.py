import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from mdc import live, config as C, hf_sync


class LiveStorageTests(unittest.TestCase):
    def test_failed_uploads_in_same_minute_keep_both_batches(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(C, "DATA", Path(folder)), \
                patch.object(C, "now_ist", return_value=dt.datetime(2026, 11, 19, 10)), \
                patch.object(hf_sync, "upload", return_value=False):
            sink = live.Sink({1: ("stock", "TEST")}, dt.date(2026, 11, 19))
            for volume in (100, 200):
                sink.add([{"instrument_token": 1, "last_price": 10., "volume_traded": volume}])
                sink.flush()
                sink.ship("morning_test")
            files = list(Path(folder).rglob("*.parquet"))
            self.assertEqual(len(files), 2)
            saved = pd.concat([pd.read_parquet(f) for f in files])
            self.assertEqual(sorted(saved.volume.tolist()), [100, 200])


if __name__ == "__main__":
    unittest.main()
