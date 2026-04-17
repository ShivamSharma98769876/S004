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

**SuperTrendTrail** uses ``strategyType: "supertrend-trail"`` with ``superTrendTrail``
params; spot signal math in ``app.strategies.supertrend_trail``; short ATM execution in
``trades_service._get_live_candidates_supertrend_trail``.

**StochasticBNF** uses ``strategyType: "stochastic-bnf"`` with ``stochasticBnf`` params;
Bank Nifty spot EMA/ADX/Stoch RSI in ``app.strategies.stochastic_bnf``; short ATM in
``trades_service._get_live_candidates_stochastic_bnf``.

**PS/VS MTF** uses ``strategyType: "ps-vs-mtf"`` with ``psVsMtf`` params; one ``3minute``
index pull and in-memory 15m resample in ``app.strategies.ps_vs_mtf``; ATM in
``trades_service._get_live_candidates_ps_vs_mtf`` (see performance.mdc: no duplicate
interval fetches per refresh).

Avoid scattering hard-coded strategy strings; prefer rows in the catalog and
parameters in ``strategy_details_json`` / user overrides.
"""
