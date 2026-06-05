import asyncio
import os
import sys
import threading
import traceback
from collections import defaultdict
from datetime import datetime, timezone

import requests
from flask import Flask, render_template, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from checker import REGIONS, UNSTABLE_SERVERS, check_all_regions
from storage import load_series, save_series
from stats import uptime_pct, last_active, heatmap, schedule_summary

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per minute"],
    storage_uri="memory://",
)

_host_location = "Unknown"
try:
    r = requests.get("http://ip-api.com/json/?fields=regionName", timeout=5)
    if r.ok:
        _host_location = r.json().get("regionName", "Unknown")
except Exception:
    pass

_series = defaultdict(list)
_loaded = load_series()
for name in UNSTABLE_SERVERS:
    if name in _loaded:
        _series[name] = _loaded[name]

_latest = {}
_prev_fleet = {}  # track previous state for notification detection
_checker_running = False
_last_error = None
_last_check_time = None


def _run_checker_loop():
    global _latest, _series, _prev_fleet, _checker_running, _last_error, _last_check_time
    loop_obj = asyncio.new_event_loop()
    asyncio.set_event_loop(loop_obj)

    async def loop():
        global _latest, _series, _prev_fleet, _last_error, _last_check_time
        print("[checker] Started, waiting 5s before first check...", flush=True)
        await asyncio.sleep(5)
        while True:
            try:
                results = await check_all_regions()
                # Track state changes for notifications
                for name in UNSTABLE_SERVERS:
                    old = _prev_fleet.get(name)
                    new = results.get(name)
                    new_state = new.fleet_active if new else None
                    if old is not None and old != new_state:
                        print(f"[notify] {name}: {old} -> {new_state}", flush=True)
                    _prev_fleet[name] = new_state
                _latest = results
                _last_check_time = datetime.now(timezone.utc).isoformat()
                now_ts = datetime.now(timezone.utc).timestamp()
                for name in UNSTABLE_SERVERS:
                    s = results.get(name)
                    if s:
                        _series[name].append({"ts": now_ts, "fleet": s.fleet_active, "icmp": s.icmp_ms})
                save_series(dict(_series))
            except Exception as e:
                _last_error = f"{e}\n{traceback.format_exc()}"
                print(f"[checker] ERROR: {_last_error}", flush=True)
            await asyncio.sleep(30)

    try:
        _checker_running = True
        loop_obj.run_until_complete(loop())
    except Exception as e:
        _checker_running = False
        _last_error = f"Thread died: {e}\n{traceback.format_exc()}"
        print(_last_error, flush=True)


def _ensure_checker():
    t = threading.Thread(target=_run_checker_loop, daemon=True, name="checker")
    t.start()


_ensure_checker()


@app.route("/")
def index():
    regions_dict = {
        name: {"service_host": info.service_host, "ping_host": info.ping_host, "stable": info.stable, "group": info.group}
        for name, info in REGIONS.items()
    }
    return render_template("index.html", regions=regions_dict, host_location=_host_location)


@app.route("/api/status")
def api_status():
    result = {}
    for name, info in REGIONS.items():
        status = _latest.get(name)
        result[name] = {
            "group": info.group,
            "stable": info.stable,
            "icmp_ms": status.icmp_ms if status else None,
            "fleet_active": status.fleet_active if status else None,
            "last_check_iso": status.last_check.isoformat() if status and status.last_check else None,
        }
    return jsonify(result)


@app.route("/api/notifications")
def api_notifications():
    """Return a list of servers whose fleet status changed since last check."""
    changes = []
    for name in UNSTABLE_SERVERS:
        prev = _prev_fleet.get(name)
        curr = None
        s = _latest.get(name)
        if s:
            curr = s.fleet_active
        if prev is not None and prev != curr:
            changes.append({"name": name, "previous": prev, "current": curr})
    return jsonify(changes)


@app.route("/api/stats")
def api_stats():
    """Return uptime %, last active, heatmap, and schedule for each unstable server."""
    hours = request.args.get("hours", 168, type=int)
    result = {}
    for name in UNSTABLE_SERVERS:
        points = _series.get(name, [])
        result[name] = {
            "uptime_pct": uptime_pct(points, hours=hours),
            "last_active": last_active(points),
            "schedule": schedule_summary(points),
            "heatmap": heatmap(points),
        }
    return jsonify(result)


@app.route("/api/health")
def api_health():
    sample = {}
    for name in UNSTABLE_SERVERS:
        s = _latest.get(name)
        sample[name] = {"icmp_ms": s.icmp_ms if s else None, "fleet_active": s.fleet_active if s else None}
    fsize = os.path.getsize(__import__("storage").DATA_FILE) if __import__("storage").DATA_FILE.exists() else 0
    return jsonify({
        "checker_running": _checker_running,
        "last_check_time": _last_check_time,
        "last_error": _last_error,
        "latest_count": len(_latest),
        "series_sizes": {name: len(_series.get(name, [])) for name in UNSTABLE_SERVERS},
        "history_file_kb": round(fsize / 1024, 1),
        "sample": sample,
    })


@app.route("/api/history")
def api_history():
    hours = request.args.get("hours", 168, type=int)
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    result = {}
    for name in UNSTABLE_SERVERS:
        points = [p for p in _series.get(name, []) if p["ts"] >= cutoff]
        result[name] = points
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
