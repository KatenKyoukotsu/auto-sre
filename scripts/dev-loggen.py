#!/usr/bin/env python3
"""Генератор синтетических логов для локальной проверки auto-sre.

Режимы:
  --backfill          залить 6ч истории (базовая линия: 2-6 ошибок на 15-минутное окно)
  --live              жить вечно, подливать INFO + редкие ошибки
  --burst SERVICE     вбросить ~60 ошибок в текущее окно (спайк для детектора)

Ингест: POST /insert/jsonline, стрим задаётся полем app (_stream_fields=app).
"""
import argparse
import json
import random
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

VL_URL = "http://localhost:9428"
SERVICES = ["gateway", "billing", "auth", "notifications"]

ERROR_TEMPLATES = [
    "error: connection refused to upstream {upstream}",
    "error: request timeout after {ms}ms",
    "DatabaseError: deadlock detected on table orders",
    "exception in worker thread: pool exhausted",
    "fatal: cannot acquire lock on resource {res}",
    "panic: runtime index out of range in handler",
    "traceback (most recent call last): payment flow crashed",
    "error: 502 bad gateway from {upstream}",
    "RedisConnectionException: unable to reach cache node",
]

INFO_TEMPLATES = [
    "request completed status=200 path=/api/v1/items duration={ms}ms",
    "healthcheck ok",
    "cache hit ratio {ratio} session refreshed",
    "user login ok user_id={uid}",
    "scheduled job finished items_processed={n}",
    "request completed status=201 path=/api/v1/orders duration={ms}ms",
]

UPSTREAMS = ["payments-svc", "inventory-svc", "user-svc"]


def ingest(docs: list[dict]) -> int:
    if not docs:
        return 0
    body = "\n".join(json.dumps(d, ensure_ascii=False) for d in docs).encode()
    req = urllib.request.Request(
        f"{VL_URL}/insert/jsonline?_time_field=_time&_msg_field=_msg&_stream_fields=app",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-ndjson"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    return len(docs)


def make_doc(service: str, ts: datetime, is_error: bool) -> dict:
    if is_error:
        msg = random.choice(ERROR_TEMPLATES).format(
            upstream=random.choice(UPSTREAMS), ms=random.randint(1000, 9000),
            res=f"/var/lock/{service}.lock",
        )
        level = "ERROR"
    else:
        msg = random.choice(INFO_TEMPLATES).format(
            ms=random.randint(5, 400), ratio=round(random.random(), 2),
            uid=random.randint(100, 999), n=random.randint(1, 500),
        )
        level = "INFO"
    return {
        "_time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_msg": msg,
        "app": service,
        "level": level,
        "env": "combat-local",
    }


def backfill(hours: float):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    window = timedelta(minutes=15)
    total = 0
    cursor = start
    while cursor < now:
        win_end = min(cursor + window, now)
        for service in SERVICES:
            docs = []
            # базовая линия ошибок: 2-6 на окно
            n_err = random.randint(2, 6)
            for _ in range(n_err):
                ts = cursor + timedelta(seconds=random.uniform(0, (win_end - cursor).total_seconds()))
                docs.append(make_doc(service, ts, is_error=True))
            # информационный шум
            for _ in range(random.randint(25, 60)):
                ts = cursor + timedelta(seconds=random.uniform(0, (win_end - cursor).total_seconds()))
                docs.append(make_doc(service, ts, is_error=False))
            docs.sort(key=lambda d: d["_time"])
            total += ingest(docs)
        cursor += window
    print(f"backfill: {total} логов за {hours}ч ({len(SERVICES)} сервисов)")


def live(interval: float):
    print(f"live: пишу каждые {interval}s, Ctrl+C для остановки")
    while True:
        docs = []
        for service in SERVICES:
            for _ in range(random.randint(1, 4)):
                docs.append(make_doc(service, datetime.now(timezone.utc), is_error=False))
            # редкая ошибка, чтобы базовая линия не вымирала (~1 на 5 мин на сервис)
            if random.random() < interval / 300:
                docs.append(make_doc(service, datetime.now(timezone.utc), is_error=True))
        random.shuffle(docs)
        ingest(docs)
        time.sleep(interval)


def burst(service: str, count: int, spread_min: float):
    if service not in SERVICES:
        sys.exit(f"неизвестный сервис {service!r}, доступно: {', '.join(SERVICES)}")
    now = datetime.now(timezone.utc)
    docs = []
    for _ in range(count):
        ts = now - timedelta(minutes=random.uniform(0, spread_min))
        docs.append(make_doc(service, ts, is_error=True))
    docs.sort(key=lambda d: d["_time"])
    n = ingest(docs)
    print(f"burst: {n} ошибок в '{service}' за последние {spread_min} мин")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--burst", metavar="SERVICE")
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--spread", type=float, default=12)
    args = ap.parse_args()

    if args.backfill:
        backfill(args.hours)
    if args.burst:
        burst(args.burst, args.count, args.spread)
    if args.live:
        try:
            live(args.interval)
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
