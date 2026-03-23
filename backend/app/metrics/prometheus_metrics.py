"""Prometheus counters/histograms — low-cardinality path groups."""

from __future__ import annotations

import re

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Path group: collapse numeric IDs to keep cardinality bounded
_PATH_NUM = re.compile(r"/\d+")


def path_group(path: str) -> str:
    p = path.split("?")[0] or "/"
    p = _PATH_NUM.sub("/*", p)
    if len(p) > 64:
        p = p[:64]
    return p or "/"


HTTP_REQUESTS = Counter(
    "s004_http_requests_total",
    "Total HTTP requests",
    ["method", "path_group", "status"],
)

HTTP_LATENCY = Histogram(
    "s004_http_request_duration_seconds",
    "Request latency seconds",
    ["method", "path_group"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")),
)


def metrics_payload() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
