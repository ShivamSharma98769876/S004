"""Request logging with X-Request-ID for traceability."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("s004.api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed method=%s path=%s request_id=%s", request.method, request.url.path, rid)
            raise
        duration_s = time.perf_counter() - start
        duration_ms = duration_s * 1000
        response.headers["X-Request-ID"] = rid
        status = str(getattr(response, "status_code", 500))
        try:
            from app.metrics.prometheus_metrics import HTTP_LATENCY, HTTP_REQUESTS, path_group

            pg = path_group(request.url.path)
            HTTP_REQUESTS.labels(request.method, pg, status).inc()
            HTTP_LATENCY.labels(request.method, pg).observe(duration_s)
        except Exception:
            pass
        logger.info(
            "%s %s %s %.1fms request_id=%s",
            request.method,
            request.url.path,
            getattr(response, "status_code", "?"),
            duration_ms,
            rid,
        )
        return response
