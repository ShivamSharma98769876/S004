# W01-S04: Cross-Service Observability Contract

## Objective
Ensure request tracing, diagnostics, and operational visibility are consistent across all services.

## Logging standard (JSON)
Required fields:
- `timestamp_utc`
- `level`
- `service`
- `environment`
- `message`
- `correlation_id`
- `request_id` (if HTTP)
- `user_id` (if authenticated context exists)
- `event_name` (for async handlers)
- `error_code` (for failures)
- `latency_ms` (for measured operations)

## Tracing
- Use W3C Trace Context (`traceparent`, `tracestate`).
- Propagate trace headers in:
  - HTTP downstream calls
  - message-bus events
  - websocket-originated server actions (via correlation mapping)

## Metrics
Minimum required metrics:
- HTTP:
  - request_count (labels: path, method, status)
  - request_latency_ms (histogram)
- Background workers:
  - job_duration_ms
  - job_failures_total
- Market data:
  - tick_ingest_latency_ms
  - reconnect_count
- Execution:
  - order_placement_latency_ms
  - order_reject_total
- WebSocket:
  - connected_clients
  - message_publish_latency_ms

## Error handling model
- Every error response includes:
  - `error.code`
  - `error.message`
  - `correlation_id`
- Every caught exception log includes stack trace and contextual domain identifiers.

## Alerting baseline
- P1:
  - feed disconnected > 30s
  - execution error rate > 5% in 5m
- P2:
  - websocket publish latency p95 > 500ms
  - DB query latency p95 > 250ms

## Open implementation questions
- Should debug-level logs be fully disabled in production or sampled?
- Where to store long-term logs: cloud logging only or searchable SIEM mirror?
