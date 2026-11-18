import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from mdc import cloud_capture as capture


class CaptureTests(unittest.TestCase):
    def test_windows_and_weekends(self):
        self.assertFalse(capture.eligible("morning", dt.datetime(2026, 11, 19, 8, 14)))
        self.assertTrue(capture.eligible("morning", dt.datetime(2026, 11, 19, 8, 15)))
        self.assertFalse(capture.eligible("morning", dt.datetime(2026, 11, 19, 14)))
        self.assertTrue(capture.eligible("afternoon", dt.datetime(2026, 11, 19, 13, 40)))
        self.assertFalse(capture.eligible("afternoon", dt.datetime(2026, 11, 19, 15, 45)))
        self.assertFalse(capture.eligible("morning", dt.datetime(2026, 11, 21, 9)))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)
        for obj, fields in [
            (capture.C, {"STATE": self.state, "now_ist": MagicMock(return_value=dt.datetime(2026, 11, 19, 9))}),
            (capture.hf_sync, {"download": MagicMock(side_effect=FileNotFoundError), "upload": MagicMock(return_value=True)}),
            (capture.kite_auth, {"cloud_login": MagicMock(), "get_kite": MagicMock(return_value=(MagicMock(), "test"))}),
            (capture.universe, {"build": MagicMock(return_value={"stocks": [{"list": "nifty50", "symbol": str(i)} for i in range(50)]})}),
            (capture.live, {"run": MagicMock()}),
        ]:
            p = patch.multiple(obj, **fields)
            p.start()
            self.addCleanup(p.stop)

    def test_completed_slot_does_not_login_again(self):
        def download(remote, dest):
            dest.write_text(json.dumps({"status": "complete"}))
        capture.hf_sync.download.side_effect = download
        capture.run("morning")
        capture.kite_auth.cloud_login.assert_not_called()

    def test_failed_recording_is_retryable(self):
        capture.live.run.side_effect = RuntimeError("feed stalled")
        with self.assertRaises(RuntimeError):
            capture.run("morning")
        capture.hf_sync.upload.assert_not_called()
        self.assertFalse(list(self.state.glob("capture_*.json")))

    def test_completed_recording_publishes_marker(self):
        capture.run("morning")
        capture.kite_auth.cloud_login.assert_called_once()
        capture.hf_sync.upload.assert_called_once()
        self.assertEqual(json.loads(next(self.state.glob("*.json")).read_text())["status"], "complete")

    def test_missing_constituents_prevents_incomplete_recording(self):
        capture.universe.build.return_value = {"stocks": []}
        with self.assertRaises(RuntimeError):
            capture.run("morning")
        capture.live.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
