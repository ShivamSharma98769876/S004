"""Human-readable log files for strategy recommendation runs (analysis / debugging).

Enable by setting S004_EVALUATION_LOG_DIR (e.g. logs/evaluation relative to cwd, or an absolute path).
Files: {dir}/{IST calendar date}/{strategy_id}__{version}.log — one snapshot per refresh (append).
Per-user copies: by_user/user_{id}.log
Snapshot timestamps in the file body are IST (Asia/Kolkata), matching the folder date.

Optional: S004_EVALUATION_LOG_MAX_CANDIDATES — if > 0, cap candidate lines and note truncation.
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


def _fmt_candidate_line(i: int, c: dict[str, Any]) -> str:
    sym = _dash(c.get("symbol"))
    ot = _dash(c.get("option_type"))
    side = _dash(c.get("side"))
    dist = c.get("distance_to_atm")
    dist_s = (
        f"steps_from_ATM={int(dist):+d}" if isinstance(dist, (int, float)) else _dash(dist)
    )
    sc = c.get("score")
    cf = c.get("confidence_score")
    el = c.get("signal_eligible")
    el_s = "YES" if el is True else "NO" if el is False else "—"
    delta = c.get("delta")
    delta_s = f"{float(delta):.4f}" if isinstance(delta, (int, float)) else "—"
    ivr = c.get("ivr")
    ivr_s = f"{float(ivr):.1f}" if isinstance(ivr, (int, float)) else "—"
    oi = c.get("oi")
    volr = c.get("volume_spike_ratio")
    volr_s = f"{float(volr):.2f}" if isinstance(volr, (int, float)) else "—"
    ltp = c.get("entry_price")
    ltp_s = f"{float(ltp):.2f}" if isinstance(ltp, (int, float)) else "—"
    fail = str(c.get("failed_conditions") or "—")[:120]
    head = (
        f"  {i:3}. {sym} | {ot} | side={side} | {dist_s} | score={_dash(sc)} | "
        f"conf={_dash(cf)} | eligible={el_s} | Δ={delta_s} | IVR={ivr_s} | "
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
    ind_line = f"\n       {'  '.join(ind_parts)}" if ind_parts else ""
    return head + ind_line + f"\n       failed: {fail}"


def format_evaluation_event_text(event: dict[str, Any]) -> str:
    """Render one evaluation snapshot for a .log file (used by tests and append)."""
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
    lines.extend(["", "Candidates (slim, scan order):"])
    cands = event.get("candidates") or []
    if isinstance(cands, list) and cands:
        for i, c in enumerate(cands, start=1):
            if isinstance(c, dict):
                lines.append(_fmt_candidate_line(i, c))
            else:
                lines.append(f"  {i:3}. {c!r}")
    else:
        lines.append("  (none)")
    lines.extend(
        [
            "",
            f"Candidates truncated: {event.get('candidates_truncated', False)}",
            _SEP,
            "",
        ]
    )
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
