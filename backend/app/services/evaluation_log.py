"""Human-readable log files for strategy recommendation runs (analysis / debugging).

Enable by setting S004_EVALUATION_LOG_DIR (e.g. logs/evaluation relative to cwd, or an absolute path).
Files: {dir}/{IST calendar date}/{strategy_id}__{version}.log — one snapshot per refresh (append).
Per-user copies: by_user/user_{id}.log
Snapshot timestamps in the file body are IST (Asia/Kolkata), matching the folder date.

Optional: S004_EVALUATION_LOG_MAX_CANDIDATES — if > 0, cap the slim candidate list passed into the event (used for short compact “in band” lines).

Long premium / verbose mode: chain diagnostics when present, counts, and failed-condition samples (no per-candidate scan-order dump).

Short premium (default): compact log — one ``Short delta gate:`` line (includes VIX → CE/PE ranges) and one line
per strike **in the active delta band** only. Set ``S004_EVALUATION_LOG_SHORT_FULL=1`` for the full short diagnostic blocks.
Per-leg diagnostics use ``leg_elig`` (chain ``signalEligible``) vs ``trade_elig`` (all gates including regime / liquidity)
and show ``conf`` (same formula as recommendation confidence).

Long premium (e.g. TrendSnap Momentum): by default the log does **not** list each scanned strike — only chain summary (if any),
counts, score thresholds, and a few ``failed_conditions`` samples. Set ``S004_EVALUATION_LOG_LONG_CANDIDATES=1`` to append a
full per-candidate block (symbol, strike, Greeks, indicators, eligibility, failed reasons) for analysis. Keep
``S004_EVALUATION_LOG_MAX_CANDIDATES=0`` (default) so the list is not truncated.

Spot-led strategies (e.g. ``stochastic-bnf``, ``supertrend-trail``): ``chain_snapshot`` is often empty in the event; ``Chain fetch: OK``
means the refresh did not raise. **SuperTrendTrail** attaches ``scanned_candidates`` (every chain strike on the active CE/PE side) so
the snapshot shows **Scanned strikes (spot-led, …)** when ``S004_EVALUATION_LOG_LONG_CANDIDATES`` is off, and full per-leg blocks when it is on.
Other spot-led types may still show only counts until they pass a similar scan list.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")
_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_SEP = "=" * 80

# Strategy types that build recommendations from spot/index logic first; evaluation log often has no chain_snapshot.
_SPOT_LED_STRATEGY_TYPES: frozenset[str] = frozenset(
    {"stochastic-bnf", "supertrend-trail", "trendpulse-z", "ps-vs-mtf"}
)
_CANDIDATES_DETAIL_SUPPRESS: frozenset[tuple[str, str]] = frozenset(
    {
        ("strat-nifty-ivr-trend-short", "1.1.0"),
        ("strat-nifty-ivr-trend-short", "1.2.0"),
    }
)


def _candidates_detail_title(event: dict[str, Any]) -> str:
    pi = str(event.get("position_intent") or "").strip().lower()
    if pi == "short_premium":
        return "Scanned candidates (detail, short premium):"
    return "Scanned candidates (detail, long premium):"


def _suppress_candidates_detail(event: dict[str, Any]) -> bool:
    sid = str(event.get("strategy_id") or "").strip().lower()
    ver = str(event.get("strategy_version") or "").strip().lower()
    return (sid, ver) in _CANDIDATES_DETAIL_SUPPRESS


def _empty_candidates_detail_line(event: dict[str, Any]) -> str:
    if event.get("fetch_failed"):
        return "  (none — refresh failed; see Error message above.)"
    st = str(event.get("strategy_type") or "").strip().lower()
    if st in _SPOT_LED_STRATEGY_TYPES:
        return (
            "  (none — no recommendation row this refresh (spot-led path: signal, time/VWAP filters, "
            "expiry/ATM leg, OI/volume/IVR, or LTP). Chain summary is often omitted here; check server logs / Observability.)"
        )
    cs = event.get("chain_snapshot")
    if not isinstance(cs, dict) or not cs:
        return "  (none — no candidates; chain summary was not attached to this snapshot.)"
    return "  (none — no candidates this refresh (scan empty or no legs passed gates).)"


def execution_intent_side_note(score_params: dict[str, Any]) -> str | None:
    """One line when effective execution intent implies BUY/SELL differently from the position-intent chain path.

    Mirrors ``trades_service`` logic: chain uses ``position_intent`` (long vs short premium scan); row ``side`` uses
    ``execution_action_intent`` defaulting to position intent.
    """
    pi = str(score_params.get("position_intent") or "long_premium").strip().lower()
    if pi not in ("long_premium", "short_premium"):
        pi = "long_premium"
    raw = score_params.get("execution_action_intent")
    ex_eff = str(raw if raw not in (None, "") else pi).strip().lower()
    if ex_eff not in ("long_premium", "short_premium"):
        ex_eff = pi
    chain_short = pi == "short_premium"
    action_short = ex_eff == "short_premium"
    if action_short == chain_short:
        return None
    side_word = "SELL" if action_short else "BUY"
    chain_word = "short" if chain_short else "long"
    return (
        f"Note: execution intent ({ex_eff}) differs from position intent ({pi}): "
        f"recommendation side={side_word}; chain/scoring uses {chain_word}-premium rules."
    )


def _log_dir() -> Path | None:
    raw = os.getenv("S004_EVALUATION_LOG_DIR", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def _safe_segment(s: str) -> str:
    t = _SAFE.sub("_", (s or "").strip())
    return t[:200] if len(t) > 200 else t


def _dash(v: Any) -> str:
    if v is None or v == "":
        return "—"
    return str(v)


def _format_ts_ist_display(raw: Any) -> str:
    """Pretty IST wall time for log headers (event ts_ist is timezone-aware ISO)."""
    s = str(raw or "").strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_IST)
        else:
            dt = dt.astimezone(_IST)
        return dt.strftime("%Y-%m-%d %H:%M:%S IST")
    except ValueError:
        return s


def _fmt_optf(v: Any, decimals: int) -> str | None:
    if isinstance(v, (int, float)):
        return f"{float(v):.{decimals}f}"
    return None


_FAILED_MAX_LEN = 800


def _fmt_confidence_slim(cf: Any) -> str:
    if cf is None or cf == "":
        return "—"
    if isinstance(cf, (int, float)):
        return f"{float(cf):.1f}"
    return str(cf)


def _fmt_leg_evaluation_block(i: int, c: dict[str, Any], *, diagnostic: bool) -> str:
    """
    Human-readable 3-line block (summary / E9–RSI / failed), matching Trades-style logs.
    ``diagnostic=True``: short-premium chain_snapshot rows (side=SELL, score from leg_score, failed=blockers).
    """
    sym = _dash(c.get("symbol"))
    ot = _dash(c.get("option_type"))
    side = "SELL" if diagnostic else _dash(c.get("side"))
    dist = c.get("distance_to_atm")
    if isinstance(dist, (int, float)):
        di = int(dist)
        if di == 0:
            dist_s = "steps_from_ATM=0"
        elif di > 0:
            dist_s = f"steps_from_ATM=+{di}"
        else:
            dist_s = f"steps_from_ATM={di}"
    else:
        dist_s = f"steps_from_ATM={_dash(dist)}"
    sc = c.get("leg_score") if diagnostic else c.get("score")
    if diagnostic:
        cf_raw = c.get("confidence_score")
        cf_s = _fmt_confidence_slim(cf_raw) if cf_raw is not None and cf_raw != "" else "—"
        leg_el = c.get("leg_signal_eligible")
        leg_s = "YES" if leg_el is True else "NO" if leg_el is False else "—"
        te = c.get("trade_eligible")
        if te is None:
            blk = str(c.get("blockers") or "").strip()
            te_bool = blk in ("", "—")
        else:
            te_bool = bool(te)
        trade_s = "YES" if te_bool else "NO"
        elig_field = f"leg_elig={leg_s} | trade_elig={trade_s}"
    else:
        cf_raw = c.get("confidence_score")
        cf_s = _fmt_confidence_slim(cf_raw) if cf_raw is not None and cf_raw != "" else "—"
        el = c.get("signal_eligible")
        elig_field = f"eligible={'YES' if el is True else 'NO' if el is False else '—'}"
    stk_v = c.get("strike")
    if isinstance(stk_v, (int, float)):
        strike_s = str(int(stk_v))
    else:
        strike_s = "—"
    delta = c.get("delta")
    delta_s = f"{float(delta):.4f}" if isinstance(delta, (int, float)) else "—"
    ivr = c.get("ivr")
    ivr_s = f"{float(ivr):.1f}" if isinstance(ivr, (int, float)) else "—"
    oi = c.get("oi")
    volr = c.get("volume_spike_ratio")
    volr_s = f"{float(volr):.2f}" if isinstance(volr, (int, float)) else "—"
    ltp = c.get("ltp") if diagnostic else c.get("entry_price")
    ltp_s = f"{float(ltp):.2f}" if isinstance(ltp, (int, float)) else "—"
    fail_key = "blockers" if diagnostic else "failed_conditions"
    fail = str(c.get(fail_key) or "—").replace("\n", " ").strip()
    if len(fail) > _FAILED_MAX_LEN:
        fail = fail[: _FAILED_MAX_LEN - 3] + "..."

    # Line 1: align with "   1. SYMBOL | CE | strike=… | side=BUY | ..."
    line1 = (
        f"   {i}. {sym} | {ot} | strike={strike_s} | side={side} | {dist_s} | score={_dash(sc)} | "
        f"conf={cf_s} | {elig_field} | Δ={delta_s} | IVR={ivr_s} | "
        f"OI={_dash(oi)} | vol×={volr_s} | LTP={ltp_s}"
    )
    ind_parts: list[str] = []
    for label, key, dec in (
        ("E9", "ema9", 2),
        ("E21", "ema21", 2),
        ("VWAP", "vwap", 2),
        ("RSI", "rsi", 2),
    ):
        part = _fmt_optf(c.get(key), dec)
        if part:
            ind_parts.append(f"{label}={part}")
    line2 = f"       {'  '.join(ind_parts)}" if ind_parts else "       E9=—  E21=—  VWAP=—  RSI=—"
    if " | " in fail:
        parts = [p.strip() for p in fail.split("|") if str(p).strip()]
        line3 = f"       failed: {parts[0] if parts else fail}"
        if len(parts) > 1:
            line4 = f"       detail: {' | '.join(parts[1:])}"
            return "\n".join((line1, line2, line3, line4))
    line3 = f"       failed: {fail}"
    return "\n".join((line1, line2, line3))


def _fmt_short_diagnostic_line(i: int, d: dict[str, Any]) -> str:
    """Short-premium diagnostic leg; same 3-line shape as long-premium leg blocks."""
    return _fmt_leg_evaluation_block(i, d, diagnostic=True)


def _short_eval_log_verbose() -> bool:
    """If true, use the long-form short-premium log (diagnostics + extra chain lines)."""
    return os.getenv("S004_EVALUATION_LOG_SHORT_FULL", "").strip().lower() in {"1", "true", "yes"}


def _long_eval_log_candidates() -> bool:
    """If true, long_premium snapshots include per-scanned-candidate leg blocks (for post-hoc analysis)."""
    return os.getenv("S004_EVALUATION_LOG_LONG_CANDIDATES", "").strip().lower() in {"1", "true", "yes"}


def _row_in_short_delta_band(row: dict[str, Any], cs: dict[str, Any]) -> bool:
    """True if leg delta is inside active CE/PE corners from chain snapshot."""
    try:
        ce_lo = float(cs["short_delta_ce_lo"])
        ce_hi = float(cs["short_delta_ce_hi"])
        pe_lo = float(cs["short_delta_pe_lo"])
        pe_hi = float(cs["short_delta_pe_hi"])
    except (KeyError, TypeError, ValueError):
        return True
    ot = str(row.get("option_type") or "").strip().upper()
    d = row.get("delta")
    if not isinstance(d, (int, float)) or ot not in ("CE", "PE"):
        return False
    df = float(d)
    if ot == "CE":
        return ce_lo - 1e-9 <= df <= ce_hi + 1e-9
    return pe_lo - 1e-9 <= df <= pe_hi + 1e-9


def _fmt_spot_led_scan_compact_line(i: int, c: dict[str, Any]) -> str:
    """One-line strike row for spot-led strategies when long candidate logging is off."""
    sym = _dash(c.get("symbol"))
    ot = _dash(c.get("option_type"))
    stk_v = c.get("strike")
    strike_s = str(int(stk_v)) if isinstance(stk_v, (int, float)) else "—"
    dist = c.get("distance_to_atm")
    if dist is None:
        dist_s = "—"
    else:
        try:
            dist_s = str(int(dist))
        except (TypeError, ValueError):
            dist_s = _dash(dist)
    ep = c.get("entry_price")
    ltp_s = f"{float(ep):.2f}" if isinstance(ep, (int, float)) else "—"
    el = c.get("signal_eligible")
    el_s = "YES" if el is True else "NO" if el is False else "—"
    fc = str(c.get("failed_conditions") or "—").replace("\n", " ").strip()
    if len(fc) > 120:
        fc = fc[:117] + "..."
    line = (
        f"   {i}. {sym} | {ot} | strike={strike_s} | dATM={dist_s} | OI={_dash(c.get('oi'))} | "
        f"vol={_dash(c.get('volume'))} | LTP={ltp_s} | elig={el_s} | {fc}"
    )
    e5_s = _fmt_optf(c.get("ema5"), 2)
    e15_s = _fmt_optf(c.get("ema15"), 2)
    e50_s = _fmt_optf(c.get("ema50"), 2)
    adx_s = _fmt_optf(c.get("adx"), 2)
    k_s = _fmt_optf(c.get("stoch_k"), 2)
    d_s = _fmt_optf(c.get("stoch_d"), 2)
    vw_s = _fmt_optf(c.get("spot_vwap"), 2) or _fmt_optf(c.get("vwap"), 2)
    if any(v is not None for v in (e5_s, e15_s, e50_s, adx_s, k_s, d_s, vw_s)):
        return (
            line
            + f"\n       indicators: EMA5={e5_s or '—'} EMA15={e15_s or '—'} EMA50={e50_s or '—'} "
            f"VWAP={vw_s or '—'} ADX={adx_s or '—'} K={k_s or '—'} D={d_s or '—'}"
        )
    ps3_s = _fmt_optf(c.get("ps3"), 3)
    vs3_s = _fmt_optf(c.get("vs3"), 3)
    rsi3_ps = _fmt_optf(c.get("rsi3"), 2)
    ps15_s = _fmt_optf(c.get("ps15"), 3)
    vs15_s = _fmt_optf(c.get("vs15"), 3)
    rsi15_ps = _fmt_optf(c.get("rsi15"), 2)
    adx15_ps = _fmt_optf(c.get("adx15"), 2)
    ratr_s = _fmt_optf(c.get("r_atr"), 3)
    if any(
        v is not None
        for v in (ps3_s, vs3_s, rsi3_ps, ps15_s, vs15_s, rsi15_ps, adx15_ps, ratr_s)
    ):
        return (
            line
            + f"\n       indicators: 3m PS={ps3_s or '—'} VS={vs3_s or '—'} RSI={rsi3_ps or '—'} | "
            f"15m PS={ps15_s or '—'} VS={vs15_s or '—'} RSI={rsi15_ps or '—'} | "
            f"ADX15={adx15_ps or '—'} rATR={ratr_s or '—'}"
        )
    return line


def _fmt_spot_led_scan_row(i: int, c: dict[str, Any], *, strategy_type: str) -> str:
    """
    Spot-led scan rows.

    SuperTrendTrail frequently uses "signal:... | close=... | ema10=... | st=..." style failed_conditions.
    For readability, render the same multi-line failed/detail shape as normal candidate blocks.
    """
    st = str(strategy_type or "").strip().lower()
    if st == "supertrend-trail":
        return _fmt_leg_evaluation_block(i, c, diagnostic=False)
    return _fmt_spot_led_scan_compact_line(i, c)


def _fmt_short_strike_one_line(i: int, row: dict[str, Any], *, slim_candidate: bool) -> str:
    """Single-line strike row for compact short-premium logs."""
    sym = _dash(row.get("symbol"))
    ot = _dash(row.get("option_type"))
    strike_v = row.get("strike")
    if isinstance(strike_v, (int, float)):
        strike_s = str(int(strike_v))
    else:
        strike_s = "—"
    dist = row.get("distance_to_atm")
    if isinstance(dist, (int, float)):
        di = int(dist)
        dist_s = str(di) if di <= 0 else f"+{di}"
    else:
        dist_s = _dash(dist)
    d = row.get("delta")
    delta_s = f"{float(d):.4f}" if isinstance(d, (int, float)) else "—"
    ivr = row.get("ivr")
    ivr_s = f"{float(ivr):.1f}" if isinstance(ivr, (int, float)) else "—"
    ltp = row.get("entry_price") if slim_candidate else row.get("ltp")
    ltp_s = f"{float(ltp):.2f}" if isinstance(ltp, (int, float)) else "—"
    if slim_candidate:
        el = row.get("signal_eligible")
        el_s = "YES" if el is True else "NO" if el is False else "—"
        return (
            f"   {i}. {sym} | {ot} | strike={strike_s} | dATM={dist_s} | Δ={delta_s} | "
            f"IVR={ivr_s} | LTP={ltp_s} | elig={el_s}"
        )
    el = row.get("leg_signal_eligible")
    el_s = "YES" if el is True else "NO" if el is False else "—"
    return (
        f"   {i}. {sym} | {ot} | strike={strike_s} | dATM={dist_s} | Δ={delta_s} | "
        f"IVR={ivr_s} | LTP={ltp_s} | elig={el_s}"
    )


def _format_evaluation_event_short_compact(event: dict[str, Any]) -> str:
    """Minimal short-premium snapshot: delta gate line + in-band strikes only (one line each)."""
    lines: list[str] = [
        _SEP,
        "Recommendation evaluation snapshot (short premium, compact)",
        f"Time (IST):         {_format_ts_ist_display(event.get('ts_ist'))}",
        f"Strategy:           {_dash(event.get('strategy_id'))}  @  {_dash(event.get('strategy_version'))}",
    ]
    _esn = event.get("execution_side_note")
    if isinstance(_esn, str) and _esn.strip():
        lines.append(_esn.strip())
    if event.get("fetch_failed"):
        lines.append("Chain fetch:        FAILED")
        if event.get("error"):
            lines.append(f"Error:              {_dash(event.get('error'))}")
        lines.extend(["", _SEP, ""])
        return "\n".join(lines) + "\n"

    cs = event.get("chain_snapshot")
    if not isinstance(cs, dict):
        cs = {}
    ok = "OK"
    exp = _dash(cs.get("option_expiry"))
    nrow = _dash(cs.get("chain_rows"))
    dte = _dash(cs.get("calendar_dte_ist"))
    lines.append(f"Chain:              {ok} | expiry {exp} | rows {nrow} | DTE {dte}")
    if cs.get("short_premium_delta_abs"):
        lines.append(f"Short delta gate:   {_dash(cs.get('short_premium_delta_abs'))}")

    diag = cs.get("short_leg_diagnostics")
    in_band: list[dict[str, Any]] = []
    if isinstance(diag, list):
        for r in diag:
            if isinstance(r, dict) and _row_in_short_delta_band(r, cs):
                in_band.append(r)
    lines.append(f"Strikes in band ({len(in_band)}):")
    if in_band:
        for j, row in enumerate(in_band, start=1):
            lines.append(_fmt_short_strike_one_line(j, row, slim_candidate=False))
    else:
        lines.append("   (none — no legs in active CE/PE delta range in this scan)")

    lines.append("")
    lines.append(
        f"Persisted {_dash(event.get('candidate_count'))} | scanned {_dash(event.get('scanned_candidate_count'))} | "
        f"eligible {_dash(event.get('eligible_count'))}"
    )
    cands = event.get("candidates") or []
    slim_in: list[dict[str, Any]] = []
    if isinstance(cands, list):
        for c in cands:
            if isinstance(c, dict) and _row_in_short_delta_band(c, cs):
                slim_in.append(c)
    lines.append("")
    lines.append(f"Candidates in band ({len(slim_in)}):")
    if slim_in:
        for j, c in enumerate(slim_in, start=1):
            lines.append(_fmt_short_strike_one_line(j, c, slim_candidate=True))
    else:
        lines.append("   (none)")
    lines.extend(["", _SEP, ""])
    return "\n".join(lines) + "\n"


def format_evaluation_event_text(event: dict[str, Any]) -> str:
    """Render one evaluation snapshot for a .log file (used by tests and append)."""
    pi = str(event.get("position_intent") or "").strip().lower()
    cs0 = event.get("chain_snapshot")
    if (
        pi == "short_premium"
        and isinstance(cs0, dict)
        and cs0.get("short_premium_delta_abs")
        and not _short_eval_log_verbose()
    ):
        return _format_evaluation_event_short_compact(event)

    lines: list[str] = [
        _SEP,
        "Recommendation evaluation snapshot",
        f"Time (IST):         {_format_ts_ist_display(event.get('ts_ist'))}",
        f"Timestamp (ISO IST): {_dash(event.get('ts_ist'))}",
        _SEP,
        f"Strategy:           {_dash(event.get('strategy_id'))}  @  {_dash(event.get('strategy_version'))}",
        f"Strategy type:      {_dash(event.get('strategy_type'))}",
        f"Position intent:    {_dash(event.get('position_intent'))}",
    ]
    _esn = event.get("execution_side_note")
    if isinstance(_esn, str) and _esn.strip():
        lines.append(_esn.strip())
    lines.append(f"Trigger user:       {_dash(event.get('trigger_user_id'))}")
    su = event.get("subscribed_user_ids")
    if isinstance(su, list) and su:
        lines.append(f"Subscribed users:   {', '.join(str(x) for x in su)}")
    else:
        lines.append("Subscribed users:   —")

    lines.append(f"Chain fetch:        {'FAILED' if event.get('fetch_failed') else 'OK'}")
    spot_state = event.get("spot_state")
    if isinstance(spot_state, dict) and spot_state:
        kind = str(spot_state.get("kind") or event.get("strategy_type") or "").strip().lower()
        if kind == "stochastic-bnf":
            trend_s = _dash(spot_state.get("trend"))
            reason_s = _dash(spot_state.get("reason"))
            close_s = _fmt_optf(spot_state.get("close"), 2) or "—"
            vwap_s = _fmt_optf(spot_state.get("vwap"), 2) or "—"
            e5_s = _fmt_optf(spot_state.get("ema5"), 2) or "—"
            e15_s = _fmt_optf(spot_state.get("ema15"), 2) or "—"
            e50_s = _fmt_optf(spot_state.get("ema50"), 2) or "—"
            adx_s = _fmt_optf(spot_state.get("adx"), 2) or "—"
            adx_thr_s = _fmt_optf(spot_state.get("adx_threshold"), 2) or "—"
            k_s = _fmt_optf(spot_state.get("stoch_k"), 2) or "—"
            d_s = _fmt_optf(spot_state.get("stoch_d"), 2) or "—"
            ob_s = _fmt_optf(spot_state.get("overbought"), 2) or "—"
            os_s = _fmt_optf(spot_state.get("oversold"), 2) or "—"
            lines.append(
                f"Spot StochasticBNF: trend={trend_s} | reason={reason_s} | close={close_s} | VWAP={vwap_s}"
            )
            lines.append(
                f"Indicators:         EMA5={e5_s} | EMA15={e15_s} | EMA50={e50_s} | ADX={adx_s}>{adx_thr_s} | "
                f"StochK={k_s} | StochD={d_s} | OB/OS={ob_s}/{os_s}"
            )
            lines.append(
                "Filters:            "
                f"stochConfirmation={_dash(spot_state.get('stoch_confirmation'))} | "
                f"vwapFilter={_dash(spot_state.get('vwap_filter'))} | "
                f"timeFilter={_dash(spot_state.get('time_filter'))} "
                f"({_dash(spot_state.get('time_filter_start'))}-{_dash(spot_state.get('time_filter_end'))}) | "
                f"usePullbackEntry={_dash(spot_state.get('use_pullback_entry'))}"
            )
        elif kind == "ps-vs-mtf":
            trend_s = _dash(spot_state.get("trend"))
            reason_s = _dash(spot_state.get("reason"))
            dir_s = _dash(spot_state.get("direction"))
            conv_s = _fmt_confidence_slim(spot_state.get("conviction"))
            ok_s = "YES" if spot_state.get("signal_ok") is True else "NO" if spot_state.get("signal_ok") is False else "—"
            m = spot_state.get("metrics") if isinstance(spot_state.get("metrics"), dict) else {}
            ps3_s = _fmt_optf(m.get("ps3"), 3) or "—"
            vs3_s = _fmt_optf(m.get("vs3"), 3) or "—"
            r3_s = _fmt_optf(m.get("rsi3"), 2) or "—"
            ps15_s = _fmt_optf(m.get("ps15"), 3) or "—"
            vs15_s = _fmt_optf(m.get("vs15"), 3) or "—"
            r15_s = _fmt_optf(m.get("rsi15"), 2) or "—"
            adx_s = _fmt_optf(m.get("adx15"), 2) or "—"
            ratr_s = _fmt_optf(m.get("r_atr"), 3) or "—"
            lines.append(
                f"Spot PS/VS MTF:     trend={trend_s} | direction={dir_s} | reason={reason_s} | "
                f"signal_ok={ok_s} | conviction={conv_s}%"
            )
            lines.append(
                f"Indicators (3m/15m): PS3={ps3_s} VS3={vs3_s} RSI3={r3_s} | "
                f"PS15={ps15_s} VS15={vs15_s} RSI15={r15_s} | ADX15={adx_s} | rATR={ratr_s}"
            )
            lines.append(
                "Config gates:       "
                f"minConviction≥{_fmt_optf(spot_state.get('minConvictionPct'), 1) or '—'}% | "
                f"RSI15∈[{_fmt_optf(spot_state.get('rsiBandLow'), 0) or '—'}-{_fmt_optf(spot_state.get('rsiBandHigh'), 0) or '—'}] | "
                f"ADX≥{_fmt_optf(spot_state.get('adxMin'), 1) or '—'} | "
                f"rATR∈[{_fmt_optf(spot_state.get('atrRangeMin'), 2) or '—'}-{_fmt_optf(spot_state.get('atrRangeMax'), 2) or '—'}] | "
                f"strict15m={_dash(spot_state.get('strict15m'))} | "
                f"chart={_dash(spot_state.get('chart_interval_kite'))}"
            )
        else:
            sd = spot_state.get("st_direction")
            try:
                sd_i = int(sd) if isinstance(sd, (int, float, str)) and str(sd).strip() != "" else None
            except ValueError:
                sd_i = None
            if sd_i in (1, -1):
                regime = "BULLISH" if sd_i == 1 else "BEARISH"
                close_s = _fmt_optf(spot_state.get("close"), 2) or "—"
                e10_s = _fmt_optf(spot_state.get("ema10"), 2) or "—"
                e20_s = _fmt_optf(spot_state.get("ema20"), 2) or "—"
                stu_s = _fmt_optf(spot_state.get("supertrend_upper"), 2) or "—"
                stl_s = _fmt_optf(spot_state.get("supertrend_lower"), 2) or "—"
                lines.append(
                    f"Spot SuperTrend:    {regime} (dir={sd_i:+d}) | close={close_s} | ema10={e10_s} | ema20={e20_s} | "
                    f"STu={stu_s} | STl={stl_s}"
                )
    cs = event.get("chain_snapshot")
    if isinstance(cs, dict) and cs:
        lines.append(f"Option expiry:      {_dash(cs.get('option_expiry'))}")
        lines.append(f"Chain rows:         {_dash(cs.get('chain_rows'))}")
        lines.append(f"Calendar DTE (IST): {_dash(cs.get('calendar_dte_ist'))}")
        if cs.get("reason"):
            lines.append(f"Chain note:         {_dash(cs.get('reason'))}")
        if cs.get("short_premium_delta_abs"):
            lines.append(f"Short delta gate:   {_dash(cs.get('short_premium_delta_abs'))}")
        if cs.get("india_vix") is not None and cs.get("india_vix") != "":
            lines.append(f"India VIX (quote):  {_dash(cs.get('india_vix'))}")
        if cs.get("short_premium_strike_select"):
            lines.append(f"Short strike select: {_dash(cs.get('short_premium_strike_select'))}")
        if cs.get("chain_strikes_each_side") is not None and cs.get("chain_strikes_each_side") != "":
            lines.append(f"Chain ±strikes/side: {_dash(cs.get('chain_strikes_each_side'))}")
        if cs.get("short_premium_ce_datm"):
            lines.append(f"Short CE dATM:      {_dash(cs.get('short_premium_ce_datm'))}  (asymmetric mode only)")
        if cs.get("short_premium_pe_datm"):
            lines.append(f"Short PE dATM:      {_dash(cs.get('short_premium_pe_datm'))}  (asymmetric mode only)")
        diag = cs.get("short_leg_diagnostics")
        if isinstance(diag, list) and diag:
            lines.append("")
            lines.append(
                "Short premium — per-leg diagnostics (chain scan; use blockers to tune gates):"
            )
            for j, row in enumerate(diag, start=1):
                if isinstance(row, dict):
                    lines.append(_fmt_short_diagnostic_line(j, row))
                else:
                    lines.append(f"  {j:3}. {row!r}")
    elif not event.get("fetch_failed"):
        stt = str(event.get("strategy_type") or "").strip().lower()
        if stt in _SPOT_LED_STRATEGY_TYPES:
            lines.append(
                "Chain snapshot:     — (not attached for this strategy type; OK means no Python exception on refresh.)"
            )
    err = event.get("error")
    if err:
        lines.append(f"Error message:      {err}")
    lines.extend(
        [
            "",
            f"Persisted rows:     {_dash(event.get('candidate_count'))}  (recommendations written for this refresh)",
            f"Scanned candidates: {_dash(event.get('scanned_candidate_count'))}",
            f"Eligible (persist): {_dash(event.get('eligible_count'))}",
            "",
            f"Score threshold:    {_dash(event.get('score_threshold'))}    "
            f"Score max: {_dash(event.get('score_max'))}",
            f"Auto-trade score:   {_dash(event.get('auto_trade_score_threshold'))}  "
            f"(min score for auto-execute)",
            f"EMA cross in score: {_dash(event.get('include_ema_crossover_in_score'))}    "
            f"Strict bullish: {_dash(event.get('strict_bullish_comparisons'))}",
            f"RSI band:           {_dash(event.get('rsi_min'))} – {_dash(event.get('rsi_max'))}",
            f"Volume min ratio:   {_dash(event.get('volume_min_ratio'))}",
            f"ADX min threshold:  {_dash(event.get('adx_min_threshold'))}",
            f"Top symbol:         {_dash(event.get('top_symbol'))}",
            "",
            "Failed conditions (sample, from persisted rows):",
        ]
    )
    samples = event.get("failed_conditions_sample") or []
    if isinstance(samples, list) and samples:
        for s in samples:
            lines.append(f"  • {s}")
    else:
        lines.append("  (none)")

    stt_spot = str(event.get("strategy_type") or "").strip().lower()
    cands_spot = event.get("candidates") or []
    if (
        stt_spot in _SPOT_LED_STRATEGY_TYPES
        and isinstance(cands_spot, list)
        and len(cands_spot) > 0
        and not event.get("fetch_failed")
        and not _long_eval_log_candidates()
    ):
        max_sl = int(os.getenv("S004_EVALUATION_LOG_SPOT_LED_MAX_STRIKES", "60") or "60")
        lines.append("")
        lines.append("Scanned strikes (spot-led, one line per chain leg on the active side):")
        show = cands_spot[:max_sl] if max_sl > 0 else cands_spot
        for j, c in enumerate(show, start=1):
            if isinstance(c, dict):
                lines.append(_fmt_spot_led_scan_row(j, c, strategy_type=stt_spot))
            else:
                lines.append(f"   {j}. {c!r}")
        if max_sl > 0 and len(cands_spot) > max_sl:
            lines.append(
                f"   … ({len(cands_spot) - max_sl} more rows; set S004_EVALUATION_LOG_SPOT_LED_MAX_STRIKES "
                "or S004_EVALUATION_LOG_LONG_CANDIDATES=1 for full detail)"
            )

    if not _long_eval_log_candidates():
        cands_hint = event.get("candidates") or []
        if (
            isinstance(cands_hint, list)
            and len(cands_hint) > 0
            and stt_spot not in _SPOT_LED_STRATEGY_TYPES
        ):
            lines.append("")
            lines.append(
                "Per-strike candidate list is omitted (log size; S004_EVALUATION_LOG_LONG_CANDIDATES≠1). "
                "Set S004_EVALUATION_LOG_LONG_CANDIDATES=1 to list scanned candidates; "
                "keep S004_EVALUATION_LOG_MAX_CANDIDATES=0 so the list is not capped."
            )

    if _long_eval_log_candidates() and not _suppress_candidates_detail(event):
        cands = event.get("candidates") or []
        trunc = event.get("candidates_truncated")
        lines.append("")
        lines.append(_candidates_detail_title(event))
        if trunc:
            lines.append("  (list truncated — set S004_EVALUATION_LOG_MAX_CANDIDATES=0 for full scan in log)")
        if isinstance(cands, list) and cands:
            for j, c in enumerate(cands, start=1):
                if isinstance(c, dict):
                    lines.append(_fmt_leg_evaluation_block(j, c, diagnostic=False))
                else:
                    lines.append(f"   {j}. {c!r}")
        else:
            lines.append(_empty_candidates_detail_line(event))

    lines.extend(["", _SEP, ""])
    return "\n".join(lines) + "\n"


def append_evaluation_event(event: dict[str, Any], *, user_ids: list[int] | None = None) -> None:
    """Append one human-readable snapshot per strategy file; optionally duplicate to by_user/user_{id}.log.

    If S004_EVALUATION_LOG_DIR is unset, no-op.
    """
    base = _log_dir()
    if base is None:
        return
    try:
        sid = str(event.get("strategy_id") or "unknown")
        ver = str(event.get("strategy_version") or "unknown")
        day = datetime.now(_IST).strftime("%Y-%m-%d")
        subdir = base / day
        subdir.mkdir(parents=True, exist_ok=True)
        fname = f"{_safe_segment(sid)}__{_safe_segment(ver)}.log"
        path = subdir / fname
        text = format_evaluation_event_text(event)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        if user_ids:
            by_user = subdir / "by_user"
            by_user.mkdir(parents=True, exist_ok=True)
            seen: set[int] = set()
            for uid in user_ids:
                try:
                    u = int(uid)
                except (TypeError, ValueError):
                    continue
                if u in seen:
                    continue
                seen.add(u)
                upath = by_user / f"user_{u}.log"
                with open(upath, "a", encoding="utf-8") as f:
                    f.write(text)
    except OSError as exc:
        _logger.warning("evaluation_log write failed: %s", exc)
