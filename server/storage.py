import json
import os
import sys
from pathlib import Path

DATA_FILE = Path(__file__).parent / "history.json"
MAX_SIZE_MB = 500  # Purge oldest data if file exceeds this


def load_series() -> dict[str, list[dict]]:
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return {}


def save_series(series: dict[str, list[dict]]):
    """Persist to disk. If file exceeds MAX_SIZE_MB, trim the oldest 25% of points."""
    cleaned = {}
    for name, points in series.items():
        cleaned[name] = list(points)
    try:
        raw = json.dumps(cleaned)
        size_mb = len(raw.encode("utf-8")) / (1024 * 1024)
        if size_mb > MAX_SIZE_MB:
            # Remove oldest 25% of points from each server
            for name in list(cleaned.keys()):
                n = len(cleaned[name])
                if n > 100:
                    cleaned[name] = cleaned[name][int(n * 0.25):]
            raw = json.dumps(cleaned)
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(raw)
    except Exception as e:
        print(f"[checker] Failed to save history: {e}", flush=True)
