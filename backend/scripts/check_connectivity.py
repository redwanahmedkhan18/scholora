#!/usr/bin/env python3
"""Safe Supabase/Upstash connectivity diagnostic for Render Shell or a One-Off Job.

Run from the backend directory:

    python scripts/check_connectivity.py
    python scripts/check_connectivity.py --json

It performs DNS resolution, TCP reachability, authentication and a harmless query/PING.
It never prints a password or a complete connection URL.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from app.core import connectivity  # noqa: E402
from app.core.config import settings  # noqa: E402


def _families(infos) -> list[str]:
    names = []
    for info in infos:
        name = "IPv6" if info[0] == socket.AF_INET6 else "IPv4" if info[0] == socket.AF_INET else str(info[0])
        if name not in names:
            names.append(name)
    return names


def _dns(host: str, port: int) -> Dict[str, Any]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}
    return {"ok": True, "families": _families(infos), "addresses": len(infos)}


def _tcp(host: str, port: int, timeout: float) -> Dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def _database_host_port() -> tuple[str, int]:
    url = make_url(connectivity.normalize_database_url(settings.database_url))
    return (url.host or "", int(url.port or 5432))


def _redis_host_port(raw: str) -> tuple[str, int]:
    parts = urlsplit(connectivity.normalize_redis_url(raw))
    return (parts.hostname or "", int(parts.port or 6379))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--timeout", type=float, default=5.0, help="TCP timeout in seconds")
    args = parser.parse_args()

    production = settings.is_production
    report: Dict[str, Any] = {
        "environment": settings.environment,
        "database": {},
        "cache": {},
        "celery_broker": {},
    }

    db_summary = connectivity.summarize_database_url(settings.database_url)
    report["database"]["summary"] = db_summary
    if db_summary.get("configured") and db_summary.get("driver", "").startswith("postgresql"):
        host, port = _database_host_port()
        report["database"]["dns"] = _dns(host, port)
        report["database"]["tcp"] = _tcp(host, port, args.timeout)
    report["database"]["check"] = connectivity.check_database()

    for label, raw in (
        ("cache", settings.redis_url),
        ("celery_broker", settings.celery_broker_url),
    ):
        summary = connectivity.summarize_redis_url(raw)
        report[label]["summary"] = summary
        if summary.get("configured"):
            try:
                host, port = _redis_host_port(raw)
                report[label]["dns"] = _dns(host, port)
                report[label]["tcp"] = _tcp(host, port, args.timeout)
            except Exception as exc:  # noqa: BLE001
                report[label]["dns"] = {"ok": False, "error": type(exc).__name__}
                report[label]["tcp"] = {"ok": False, "error": type(exc).__name__}
        report[label]["check"] = connectivity.check_redis_url(
            raw, label,
            required=bool((raw or "").strip().strip('"').strip("'").strip()) or production,
            timeout=args.timeout,
        )

    required_ok = (
        report["database"]["check"].get("ok", False)
        and report["cache"]["check"].get("ok", False)
        and report["celery_broker"]["check"].get("ok", False)
    )
    report["ready"] = bool(required_ok)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Scholara connectivity diagnostic")
        print(f"environment: {report['environment']}")
        for label in ("database", "cache", "celery_broker"):
            item = report[label]
            summary = item.get("summary", {})
            check = item.get("check", {})
            print(f"\n{label}:")
            print(f"  configured : {summary.get('configured', check.get('configured'))}")
            print(f"  host       : {summary.get('host', check.get('host', ''))}")
            print(f"  port       : {summary.get('port', check.get('port'))}")
            print(f"  driver/tls : {summary.get('driver') or summary.get('scheme', '')}"
                  f"{' (TLS)' if summary.get('tls') else ''}")
            if summary.get("sslmode"):
                print(f"  sslmode    : {summary['sslmode']}")
            if item.get("dns"):
                print(f"  dns        : ok={item['dns'].get('ok')} families={item['dns'].get('families')}")
            if item.get("tcp"):
                print(f"  tcp        : ok={item['tcp'].get('ok')} error={item['tcp'].get('error')}")
            print(f"  check      : ok={check.get('ok')} error={check.get('error')} "
                  f"latency_ms={check.get('latency_ms')}")
        print(f"\nREADY: {report['ready']}")

    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
