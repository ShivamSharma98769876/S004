# S004 PRD v2.5 Execution Order Playbook

This is the single runbook for production-readiness execution using generated W09 artifacts.

## 0) Preconditions

- Docker Desktop running
- Python 3.11+ installed
- `psql` client available
- Optional for K8s stage: `kubectl` configured to target cluster

Use this root path:

```powershell
cd "C:\Users\SharmaS8\OneDrive - Unisys\Shivam Imp Documents-2024 June\PythonProgram\S004-DynamicOptionBuy"
```

---

## 1) Apply schema in exact order

### 1.1 Start only Postgres first

```powershell
cd ".\prd_v2_5_task_pack\implementation\W09"
docker compose up -d postgres
```

### 1.2 Apply SQL artifacts (explicit sequence)

> Use your real DB credentials/host if not local.

```powershell
$env:PGPASSWORD="changeme"

psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f "..\W03\analytics_schema.sql"
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f "..\W04\ranking_schema.sql"
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f "..\W05\recommendation_schema.sql"
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f "..\W06\execution_schema.sql"
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f "..\W07\alert_audit_schema.sql"
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f "..\W08\strategy_catalog_schema.sql"
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -f ".\migration_bundle.sql"
```

### 1.3 Validate version marker

```powershell
psql -h 127.0.0.1 -p 5432 -U shivams -d tradingpro -c "SELECT version_tag, applied_at FROM s004_schema_versions ORDER BY applied_at DESC LIMIT 5;"
```

---

## 2) Bring up complete local stack

```powershell
cd ".\prd_v2_5_task_pack\implementation\W09"
docker compose up -d
docker compose ps
```

Expected services:
- `s004-postgres`
- `s004-redis`
- `s004-backend`
- `s004-frontend`
- `s004-prometheus`
- `s004-grafana`

---

## 3) Validate application health

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health"
```

Expected:
- `{ "status": "ok" }`

Frontend:
- Open [http://localhost:3000](http://localhost:3000)

---

## 4) Validate observability stack

### 4.1 Prometheus

- Open [http://localhost:9090](http://localhost:9090)
- Go to **Status -> Targets**
- Confirm backend/frontend scrape targets are `UP`

### 4.2 Grafana

- Open [http://localhost:3001](http://localhost:3001)
- Login default: `admin / admin` (change immediately)
- Import dashboard from:
  - `.\observability\grafana-dashboard.json`

### 4.3 Alert rules

- Ensure rules file exists:
  - `.\observability\alert-rules.yml`
- If using external rule loader, apply per your Prometheus setup.

---

## 5) Run pre-go-live checks

### 5.1 Follow checklist

Use:
- `.\release\go_live_checklist.md`

Mark each checkbox after evidence capture.

### 5.2 Execute load/soak sequence

Use:
- `.\release\load_test_plan.md`

Minimum execution sequence:
1. Smoke load
2. Peak session
3. 4-hour soak

Capture metrics:
- HTTP latency p50/p95/p99
- WS publish p95
- error rate
- feed reconnects
- DB latency

---

## 6) Optional Kubernetes baseline apply

> Run this only in a configured K8s context.

```powershell
kubectl apply -f ".\k8s\backend-deployment.yaml"
kubectl apply -f ".\k8s\frontend-deployment.yaml"
kubectl apply -f ".\k8s\hpa-backend.yaml"
kubectl get pods
kubectl get svc
kubectl get hpa
```

---

## 7) Final sign-off gate

All must be true before go-live:
- Schema applied and validated
- Core services healthy
- Prometheus targets UP
- Grafana dashboard active
- Alert path tested
- Load + soak pass against PRD thresholds
- `go_live_checklist.md` signed by Eng + Product

---

## 8) Rollback quick commands

### Docker local rollback

```powershell
cd ".\prd_v2_5_task_pack\implementation\W09"
docker compose down
docker compose up -d
```

### K8s rollback (if deployed)

```powershell
kubectl rollout undo deployment/s004-backend
kubectl rollout undo deployment/s004-frontend
kubectl rollout status deployment/s004-backend
kubectl rollout status deployment/s004-frontend
```

