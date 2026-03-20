# W09-S05 Load and Soak Test Plan

## Objectives
- Validate PRD performance targets before release:
  - option data latency < 2s
  - signal generation < 1s
  - trade execution path < 300ms (internal)
  - websocket updates < 200ms

## Test phases
1. Smoke load
   - 50 concurrent users
   - baseline API and websocket functionality
2. Peak session
   - 500 concurrent websocket clients
   - burst recommendation/execution actions
3. Soak test
   - 4-hour sustained run
   - monitor memory, queue lag, reconnection behavior

## Metrics capture
- request latency p50/p95/p99
- websocket publish latency p95
- error rates by endpoint/channel
- order pipeline latency distribution
- db query latency and lock waits

## Pass/fail criteria
- all PRD latency targets met for p95
- no critical memory leaks over soak duration
- no unrecovered feed disconnect > 30s

## Tooling
- k6 for HTTP/WebSocket load
- Prometheus + Grafana dashboards
- structured log correlation checks

