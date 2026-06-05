"""Statistics calculations for server history data."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta


def uptime_pct(points: list[dict], hours: int = 168) -> float:
    """Calculate fleet uptime percentage over the last N hours."""
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    recent = [p for p in points if p["ts"] >= cutoff and p.get("fleet") is not None]
    if not recent:
        return 0.0
    active = sum(1 for p in recent if p["fleet"] is True)
    return round(active / len(recent) * 100, 1)


def last_active(points: list[dict]) -> str:
    """Human-readable time since last fleet=active state."""
    active_points = [p for p in points if p.get("fleet") is True]
    if not active_points:
        return "Never"
    last_ts = max(p["ts"] for p in active_points)
    delta = datetime.now(timezone.utc) - datetime.fromtimestamp(last_ts, tz=timezone.utc)
    if delta < timedelta(minutes=1):
        return "Just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    return f"{delta.days}d ago"


def heatmap(points: list[dict], days: int = 14) -> dict:
    """Build a 24h x 7d heatmap: hour_of_day -> day_of_week -> % active.
    Returns a dict suitable for Chart.js matrix heatmap or grid rendering."""
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    recent = [p for p in points if p["ts"] >= cutoff and p.get("fleet") is not None]

    # Bucket: (hour, dow) -> [bool, ...]
    buckets: dict[tuple[int, int], list[bool]] = defaultdict(list)
    for p in recent:
        dt = datetime.fromtimestamp(p["ts"], tz=timezone.utc)
        key = (dt.hour, dt.weekday())
        buckets[key].append(p["fleet"] is True)

    result = {}
    for (hour, dow), vals in buckets.items():
        pct = round(sum(1 for v in vals if v) / len(vals) * 100)
        result[f"{hour}_{dow}"] = {"hour": hour, "day": dow, "pct": pct, "samples": len(vals)}
    return result


def schedule_summary(points: list[dict], days: int = 14) -> str:
    """Generate a human-readable schedule summary based on last N days of data."""
    hm = heatmap(points, days=days)
    if not hm:
        return "Insufficient data"

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # For each day, find the "best" contiguous block where uptime > 50%
    day_ranges: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for key, cell in hm.items():
        if cell["pct"] >= 50:
            day_ranges[cell["day"]].append(cell["hour"])

    lines = []
    for dow in range(7):
        hours = sorted(day_ranges.get(dow, []))
        if not hours:
            continue
        # Merge contiguous hours
        ranges = []
        start = hours[0]
        end = hours[0]
        for h in hours[1:]:
            if h == end + 1:
                end = h
            else:
                ranges.append((start, end))
                start = h
                end = h
        ranges.append((start, end))

        parts = []
        for s, e in ranges:
            if s == e:
                parts.append(f"{s:02d}:00")
            else:
                parts.append(f"{s:02d}:00–{e:02d}:00")
        if parts:
            lines.append(f"{day_names[dow]}: {', '.join(parts)}")

    if not lines:
        return "No predictable uptime pattern"

    return "\n".join(lines)
