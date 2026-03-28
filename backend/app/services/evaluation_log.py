"""Human-readable log files for strategy recommendation runs (analysis / debugging).

Enable by setting S004_EVALUATION_LOG_DIR (e.g. logs/evaluation relative to cwd, or an absolute path).
Files: {dir}/{IST calendar date}/{strategy_id}__{version}.log — one snapshot per refresh (append).
Per-user copies: by_user/user_{id}.log
Snapshot timestamps in the file body are IST (Asia/Kolkata), matching the folder date.

Optional: S004_EVALUATION_LOG_MAX_CANDIDATES — if > 0, cap the slim candidate list passed into the event (used for short compact “in band” lines).

Long premium / verbose mode: chain diagnostics when present, counts, and failed-condition samples (no per-candidate scan-order dump).

Short premium (default): compact log — one ``Short delta gate:`` line (includes VIX → CE/PE ranges) and one line
per strike **in the active delta band** only. Set ``S004_EVALUATION_LOG_SHORT_FULL=1`` for the full short diagnostic blocks.
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
    cf_raw = None if diagnostic else c.get("confidence_score")
    cf_s = "—" if diagnostic else _fmt_confidence_slim(cf_raw)
    el = c.get("leg_signal_eligible") if diagnostic else c.get("signal_eligible")
    el_s = "YES" if el is True else "NO" if el is False else "—"
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

    # Line 1: align with "   1. SYMBOL | CE | side=BUY | ..."
    line1 = (
        f"   {i}. {sym} | {ot} | side={side} | {dist_s} | score={_dash(sc)} | "
        f"conf={cf_s} | eligible={el_s} | Δ={delta_s} | IVR={ivr_s} | "
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
    line3 = f"       failed: {fail}"
    return "\n".join((line1, line2, line3))


def _fmt_short_diagnostic_line(i: int, d: dict[str, Any]) -> str:
    """Short-premium diagnostic leg; same 3-line shape as long-premium leg blocks."""
    return _fmt_leg_evaluation_block(i, d, diagnostic=True)


def _short_eval_log_verbose() -> bool:
    """If true, use the long-form short-premium log (diagnostics + extra chain lines)."""
    return os.getenv("S004_EVALUATION_LOG_SHORT_FULL", "").strip().lower() in {"1", "true", "yes"}


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
        f"Trigger user:       {_dash(event.get('trigger_user_id'))}",
    ]
    su = event.get("subscribed_user_ids")
    if isinstance(su, list) and su:
        lines.append(f"Subscribed users:   {', '.join(str(x) for x in su)}")
    else:
        lines.append("Subscribed users:   —")

    lines.append(f"Chain fetch:        {'FAILED' if event.get('fetch_failed') else 'OK'}")
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
