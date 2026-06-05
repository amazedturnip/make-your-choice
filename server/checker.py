import asyncio
import re
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Only unstable servers matter for fleet tracking
UNSTABLE_SERVERS = ["Europe (London)", "US East (Ohio)", "Canada (Central)"]


@dataclass
class RegionInfo:
    service_host: str
    ping_host: str
    stable: bool
    group: str


@dataclass
class RegionStatus:
    icmp_ms: Optional[float] = None
    fleet_active: Optional[bool] = None
    last_check: Optional[datetime] = None


def _group(name: str) -> str:
    if name.startswith("Europe"):
        return "Europe"
    if name.startswith("US") or name.startswith("Canada") or name.startswith("South America"):
        return "Americas"
    if "Sydney" in name:
        return "Oceania"
    if "China" in name:
        return "China"
    return "Asia"


_RAW = [
    ("Europe (London)", "gamelift.eu-west-2.amazonaws.com", "gamelift-ping.eu-west-2.api.aws", False),
    ("Europe (Ireland)", "gamelift.eu-west-1.amazonaws.com", "gamelift-ping.eu-west-1.api.aws", True),
    ("Europe (Frankfurt am Main)", "gamelift.eu-central-1.amazonaws.com", "gamelift-ping.eu-central-1.api.aws", True),
    ("US East (N. Virginia)", "gamelift.us-east-1.amazonaws.com", "gamelift-ping.us-east-1.api.aws", True),
    ("US East (Ohio)", "gamelift.us-east-2.amazonaws.com", "gamelift-ping.us-east-2.api.aws", False),
    ("US West (N. California)", "gamelift.us-west-1.amazonaws.com", "gamelift-ping.us-west-1.api.aws", True),
    ("US West (Oregon)", "gamelift.us-west-2.amazonaws.com", "gamelift-ping.us-west-2.api.aws", True),
    ("Canada (Central)", "gamelift.ca-central-1.amazonaws.com", "gamelift-ping.ca-central-1.api.aws", False),
    ("South America (São Paulo)", "gamelift.sa-east-1.amazonaws.com", "gamelift-ping.sa-east-1.api.aws", True),
    ("Asia Pacific (Tokyo)", "gamelift.ap-northeast-1.amazonaws.com", "gamelift-ping.ap-northeast-1.api.aws", True),
    ("Asia Pacific (Seoul)", "gamelift.ap-northeast-2.amazonaws.com", "gamelift-ping.ap-northeast-2.api.aws", True),
    ("Asia Pacific (Mumbai)", "gamelift.ap-south-1.amazonaws.com", "gamelift-ping.ap-south-1.api.aws", True),
    ("Asia Pacific (Singapore)", "gamelift.ap-southeast-1.amazonaws.com", "gamelift-ping.ap-southeast-1.api.aws", True),
    ("Asia Pacific (Hong Kong)", "ec2.ap-east-1.amazonaws.com", "gamelift-ping.ap-east-1.api.aws", True),
    ("Asia Pacific (Sydney)", "gamelift.ap-southeast-2.amazonaws.com", "gamelift-ping.ap-southeast-2.api.aws", True),
]

REGIONS: dict[str, RegionInfo] = {}
for name, srv, ping, stable in _RAW:
    REGIONS[name] = RegionInfo(service_host=srv, ping_host=ping, stable=stable, group=_group(name))


def tcp_ping(host: str, port: int = 443, timeout: float = 2.0) -> Optional[float]:
    try:
        start = time.monotonic()
        sock = socket.create_connection((host, port), timeout=timeout)
        elapsed = (time.monotonic() - start) * 1000
        sock.close()
        return elapsed
    except Exception:
        return None


def icmp_ping(host: str, timeout: float = 2.0) -> Optional[float]:
    try:
        cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 1)
        if result.returncode != 0:
            return None
        m = re.search(r"time[=<](\d+\.?\d*)\s*ms", result.stdout)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def ping_host(host: str, timeout: float = 2.0) -> Optional[float]:
    result = tcp_ping(host, timeout=timeout)
    if result is not None:
        return result
    return icmp_ping(host, timeout=timeout)


def udp_ping(host: str, port: int = 443, timeout: float = 2.0) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        packet = bytearray(12)
        packet[0:4] = b"GLPL"
        timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF
        struct.pack_into("!Q", packet, 4, timestamp_ms)
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        if not addr:
            return False
        sock.sendto(bytes(packet), (addr[0][4][0], port))
        try:
            data, _ = sock.recvfrom(32)
            return len(data) >= 12 and data[0:4] == b"GLPL"
        except socket.timeout:
            return False
        finally:
            sock.close()
    except Exception:
        return False


async def _run_async(func, *args, **kwargs):
    return await asyncio.get_event_loop().run_in_executor(None, lambda: func(*args, **kwargs))


async def check_all_regions() -> dict[str, RegionStatus]:
    results: dict[str, RegionStatus] = {}

    async def check_single(name: str, info: RegionInfo) -> RegionStatus:
        status = RegionStatus()
        status.icmp_ms = await _run_async(ping_host, info.service_host)
        if not info.stable:
            status.fleet_active = await _run_async(udp_ping, info.ping_host)
        status.last_check = datetime.now(timezone.utc)
        return status

    tasks = {name: check_single(name, info) for name, info in REGIONS.items()}
    for name, task in tasks.items():
        results[name] = await task
    return results
