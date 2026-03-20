# W01-S03: API and Event Conventions

## REST API conventions
- Base prefix: `/api`
- Resource naming: plural nouns (`/trades`, `/signals`, `/strategies`)
- Versioning: `/api/v1` for public contracts (internal endpoints may remain `/api/internal/...`)
- Methods:
  - `GET`: read
  - `POST`: create/action
  - `PUT/PATCH`: update
  - `DELETE`: remove/deactivate

## Request/response standards
- Request IDs: client may send `X-Request-ID`; server generates if absent.
- Correlation IDs: `X-Correlation-ID` propagated across services.
- Response envelope for errors:
  - `error.code`
  - `error.message`
  - `error.details` (optional)
  - `correlation_id`

## Idempotency
- Required for POST actions that can duplicate side effects:
  - `POST /trades/open`
  - `POST /strategies/{id}/enable`
- Header: `Idempotency-Key`
- Retention window: 24h

## Event naming
- Topic namespace: `s004.<domain>.<entity>.<event>`
- Examples:
  - `s004.market.tick.received`
  - `s004.analytics.strike_score.generated`
  - `s004.strategy.recommendation.created`
  - `s004.trade.order.placed`
  - `s004.trade.state.changed`

## Event payload contract
- Required fields:
  - `event_id` (uuid)
  - `event_name`
  - `event_version`
  - `occurred_at` (UTC ISO)
  - `correlation_id`
  - `producer`
  - `data` (domain object)

## Versioning strategy
- Backward-compatible changes: increment minor (`1.1`).
- Breaking changes: increment major (`2.0`) and dual-publish for migration window.

## Open implementation questions
- Use JSON schema registry from start or add when message broker volume grows?
- What is the guaranteed event delivery mode for v1 (at-least-once vs exactly-once)?
