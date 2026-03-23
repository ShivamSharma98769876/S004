# Implementation Structure Normalization

The project has been normalized into runtime-oriented folders:

- `backend/`
  - `app/services/` for implementation modules
  - `app/api/` for API contracts
  - `db/migrations/` for SQL migration artifacts
- `frontend/`
  - `src/app/marketplace/page.tsx` for marketplace UI scaffold
  - `src/lib/stream_manager.ts` for websocket stream manager
- `infra/`
  - `docker-compose.yml`
  - `docker/`, `k8s/`, and `observability/` deployment/ops assets
- `docs/`
  - architecture and release runbooks
  - `docs/architecture/observability_s004.md` — health checks, request IDs, platform risk / daily limits, Prometheus, Redis
  - `.github/workflows/ci.yml` — pytest + frontend typecheck

The folder `prd_v2_5_task_pack/` remains as planning/tracking source-of-truth for task status and execution history.
