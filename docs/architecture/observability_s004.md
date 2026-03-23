# Observability & risk controls (S004)

## Request tracing

- Every API response includes **`X-Request-ID`** (echoed from the client or generated).
- Access logs: **`s004.api`** logger — method, path, status, duration (ms), `request_id`.
- Configure log level via standard Python `logging` (default `INFO` in `main.py`).

## Health checks

| Endpoint | Purpose |
|----------|---------|
| `GET /api/health` | Liveness — always returns `{ status, service, version }`. |
| `GET /api/health?deep=true` | Readiness-style — runs `SELECT 1`; sets `database: ok` or `degraded` with error detail. |

Use `deep=true` from load balancers or Kubernetes readiness probes.

## Prometheus

- Scrape URL: **`GET /metrics`** (root path on the API process, not under `/api`).
- Counters / histograms: `s004_http_requests_total`, `s004_http_request_duration_seconds`.
- Docker Compose (`infra/docker-compose.yml`) includes Prometheus + Grafana; `infra/observability/prometheus.yml` targets `backend:8000` at `/metrics`.

Protect `/metrics` at the network edge in production (VPN / allowlist), or put the API behind a reverse proxy that strips public access to `/metrics`.

## Redis (optional)

- Set **`REDIS_URL`** (e.g. `redis://localhost:6379/0`). `infra/docker-compose.yml` already defines Redis and passes `REDIS_URL` to the backend.
- **`app/services/redis_client.py`**: JSON get/set/delete with TTL; no-op if `REDIS_URL` is unset.
- **Landing sentiment replay** (`GET /api/landing/sentiment-history`): when `REDIS_URL` is set, snapshots are stored as a **capped Redis list** per user (`RPUSH` + `LTRIM`, max 240) with TTL `SENTIMENT_HISTORY_REDIS_TTL_SEC` (default 48h). No Postgres tables. If Redis is unset, the API falls back to an in-process ring buffer.
- **Platform pause** settings are cached ~3s to reduce DB reads; cache invalidated on admin `PUT /api/admin/platform`.

## CI

- **`.github/workflows/ci.yml`**: backend `pytest`; frontend `npm ci` + `tsc` (non-blocking on type errors if the repo has pre-existing TS issues).

## Risk enforcement

| Layer | Behavior |
|-------|----------|
| **Platform pause** | Row `s004_platform_settings` (`trading_paused`). Blocks auto-execute and manual `execute_recommendation`. |
| **Daily P&L caps** | `max_loss_day` / `max_profit_day` from `s004_user_master_settings` vs **today’s realized P&L** (IST calendar day). |

API: `GET /api/dashboard/risk-status` — UI banner + admin **Platform risk** on User Management.

Admin: `GET/PUT /api/admin/platform` — `{ trading_paused, pause_reason }`.

## Database migration

Apply `backend/db/migrations/platform_risk_schema.sql` (included in `scripts/apply_db_schema.py` after core + seed).
