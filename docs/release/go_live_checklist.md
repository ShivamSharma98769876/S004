# W09 Go-Live Checklist

## Schema and data
- [ ] Run `migration_bundle.sql` on staging and production
- [ ] Verify `s004_schema_versions` has `prd-v2.5-final-bundle`
- [ ] Validate indexes exist for high-frequency tables

## Application deploy
- [ ] Build/push backend and frontend images
- [ ] Apply Kubernetes manifests (`backend`, `frontend`, `hpa`)
- [ ] Confirm health probes green after rollout

## Configuration
- [ ] Set production secrets (DB, Redis, broker keys, Telegram)
- [ ] Verify websocket auth secrets and token validation
- [ ] Validate rate limits and risk thresholds

## Observability
- [ ] Prometheus scrape targets healthy
- [ ] Grafana dashboard imported and rendering
- [ ] Alert rules loaded and test alerts triggered

## Validation
- [ ] Run smoke tests
- [ ] Run peak and soak tests per load plan
- [ ] Validate reconciliation and alert escalation paths

## Rollback plan
- [ ] Previous image tags documented
- [ ] Rollback command tested in staging
- [ ] DB rollback/forward scripts prepared

## Sign-off
- [ ] Engineering lead approval
- [ ] Product owner approval
- [ ] On-call handoff completed

