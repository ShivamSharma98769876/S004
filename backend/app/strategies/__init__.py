"""Strategy onboarding model (extension points).

New strategies scale through three layers — keep ``strategy_id`` + ``version``
stable across them:

1. **Catalog** — ``s004_strategy_catalog`` (admin marketplace API / migrations).
2. **Per-user** — ``s004_user_strategy_settings`` when a user enables a strategy.
3. **Signals & execution** — recommendation generation and trade paths in
   ``app.services`` (e.g. ``trades_service``), keyed by the same ids.

**TrendPulse Z** uses ``strategyType: "trendpulse-z"`` with a ``trendPulseZ`` params
object; core signal math in ``app.services.trendpulse_z``; Phase-3 profiles / session /
breadth gates in ``app.services.trendpulse_phase3``.

Avoid scattering hard-coded strategy strings; prefer rows in the catalog and
parameters in ``strategy_details_json`` / user overrides.
"""
