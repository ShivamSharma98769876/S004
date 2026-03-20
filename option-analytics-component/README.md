# Option Analytics Component Backup

This folder is the backup package for the Option Analytics component from this project.

## Source files (current project)

Copy these files as a unit into your target project:

- `backend/app/api/routes_analytics.py`
- `backend/app/services/option_chain_zerodha.py`
- `backend/app/services/option_greeks.py`
- `frontend/src/app/analytics/page.tsx`

## Required backend dependencies

The component expects these backend modules/tables/settings to exist:

- `app.core.config.settings` with:
  - `ZERODHA_API_KEY`
  - `OPTION_CHAIN_REFRESH_SECONDS`
  - expiry config accessors (`get_expiry_config`)
- DB model:
  - `S004UserBrokerAccount`
  - `S004Trade` (for summary endpoint)
- DB session:
  - `get_session`
- Optional DB retry helper:
  - `execute_with_retry`

## Required routes to expose

The frontend analytics page expects:

- `GET /api/analytics/config`
- `GET /api/analytics/expiries`
- `GET /api/analytics/indices`
- `GET /api/analytics/option-chain`

## Frontend notes

- The page uses `NEXT_PUBLIC_API_URL` (falls back to `http://localhost:8000`).
- Ensure MUI is installed in the target project.

## Operational notes

- Zerodha connection must be present in `s004_user_broker_accounts` with status `CONNECTED`.
- If option expiries are empty, bootstrap NFO/BFO instruments cache first.
- Kite API/network instability can return 429/502; keep retry/cache handling.

