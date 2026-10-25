"""Parquet helpers. Every write is atomic (temp file + rename) so a crash never corrupts a dataset."""
import json
import os
import threading
from pathlib import Path
import pandas as pd

from .config import DATA

CATEGORY = {"INDEX": "index", "EQ": "stock", "FUT": "futures", "CE": "options", "PE": "options"}


def safe_name(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s.replace("&", "_AND_"))


def dataset_path(cat, source, interval, name):
    return DATA / cat / source / interval / f"{safe_name(name)}.parquet"


def write_atomic(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def read(path, columns=None):
    path = Path(path)
    return pd.read_parquet(path, columns=columns) if path.exists() else None


def append_dedup(path, new, key="ts", sort=None):
    """Merge new rows into the file, newest copy of a duplicate key wins, sorted by `sort` (default: key)."""
    if new is None or new.empty:
        return
    old = read(path)
    df = new if old is None else pd.concat([old, new], ignore_index=True)
    df = df.drop_duplicates(key, keep="last").sort_values(sort or key).reset_index(drop=True)
    write_atomic(df, path)


def ts_range(path):
    df = read(path, columns=["ts"])
    if df is None or df.empty:
        return None, None
    return df["ts"].min(), df["ts"].max()


class JsonState:
    """Small thread-safe JSON key/value file (progress bookkeeping)."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, default=str))
            os.replace(tmp, self.path)
