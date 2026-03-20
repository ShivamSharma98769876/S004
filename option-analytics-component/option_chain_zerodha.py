"""
Fetch live option chain from Zerodha Kite using connected broker credentials.

Computes Greeks (delta, theta, IV%) via Black-Scholes. BUILDUP from previous vs current OI/LTP snapshot.
"""

from __future__ import annotations

import logging
import pickle
import time
from calendar import monthrange
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from kiteconnect import KiteConnect

from app.core.config import get_settings
from app.services.option_greeks import compute_greeks

logger = logging.getLogger(__name__)

# Last N snapshots per (instrument, expiry) for OI change % and buildup. Newest at end.
MAX_SNAPSHOTS = 5
_snapshots: dict[tuple[str, str], deque] = {}

# NFO (NSE F&O) and BFO (BSE F&O) instruments cache (Kite dump once/day; avoid rate limits)
_NFO_INSTRUMENTS_CACHE: dict[str, Any] = {}  # "cached_at" (float), "data" (list)
_BFO_INSTRUMENTS_CACHE: dict[str, Any] = {}
_CACHE_TTL_SEC = 3600  # 1 hour
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_NFO_CACHE_FILE = _DATA_DIR / "nfo_instruments.pkl"
_BFO_CACHE_FILE = _DATA_DIR / "bfo_instruments.pkl"

# In-memory cache for BUILDUP: (instrument, expiry_str) -> { strike: { "call": {oi, ltp}, "put": {oi, ltp} } }
_buildup_cache: dict[tuple[str, str], dict[float, dict[str, dict[str, float]]]] = {}

BUILDUP_LABELS = ("Long Buildup", "Short Buildup", "Short Covering", "Long Unwinding")

# Segments to refresh in background (must match frontend instrument options).
OPTION_CHAIN_SEGMENTS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX")


def push_snapshot(instrument: str, expiry_str: str, snapshot: dict[str, Any]) -> None:
    """Append a snapshot for (instrument, expiry); keep at most MAX_SNAPSHOTS."""
    key = (instrument.strip().upper(), expiry_str.strip())
    if key not in _snapshots:
        _snapshots[key] = deque(maxlen=MAX_SNAPSHOTS)
    _snapshots[key].append(snapshot)


def get_snapshots(instrument: str, expiry_str: str) -> list[dict[str, Any]]:
    """Return up to MAX_SNAPSHOTS snapshots for (instrument, expiry), newest last."""
    key = (instrument.strip().upper(), expiry_str.strip())
    d = _snapshots.get(key)
    if not d:
        return []
    return list(d)


def _oi_change_pct(prev_oi: float, curr_oi: float) -> float:
    """Return (curr - prev) / prev * 100 when prev > 0, else 0."""
    if prev_oi is None or prev_oi <= 0:
        return 0.0
    return round((curr_oi - prev_oi) / prev_oi * 100, 2)


def _exchange_for_instrument(instrument: str) -> str:
    """Return Kite exchange: BFO for SENSEX (BSE), NFO for NSE indices."""
    return "BFO" if instrument.strip().upper() == "SENSEX" else "NFO"


def _load_instruments_from_cache(instrument: str) -> list:
    """Load NFO or BFO instruments from memory/file cache (no Kite call). SENSEX uses BFO (BSE)."""
    exchange = _exchange_for_instrument(instrument)
    cache = _BFO_INSTRUMENTS_CACHE if exchange == "BFO" else _NFO_INSTRUMENTS_CACHE
    cache_file = _BFO_CACHE_FILE if exchange == "BFO" else _NFO_CACHE_FILE
    instruments_all: list = []
    now = time.monotonic()
    if (now - cache.get("cached_at", 0)) < _CACHE_TTL_SEC:
        instruments_all = cache.get("data") or []
    if not instruments_all and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                instruments_all = pickle.load(f)
            if instruments_all:
                cache["data"] = instruments_all
                cache["cached_at"] = now
        except Exception:
            pass
    return instruments_all


def find_option_token(
    instrument: str,
    expiry_date: date,
    strike: float,
    option_type: str,
) -> Optional[int]:
    """
    Resolve Kite instrument_token for a single option (CE/PE) using cached NFO/BFO instruments.

    - instrument: underlying like "NIFTY", "BANKNIFTY"
    - expiry_date: date object for expiry
    - strike: strike price
    - option_type: "CE" or "PE"
    """
    exchange = _exchange_for_instrument(instrument)
    instruments_all = _load_instruments_from_cache(instrument)
    if not instruments_all:
        return None

    # Kite instruments use native types; expiry may be date or datetime.
    for r in instruments_all:
        if r.get("instrument_type") != option_type:
            continue
        ts = r.get("tradingsymbol", "")
        if not _match_instrument(instrument, ts):
            continue
        if float(r.get("strike", 0) or 0) != float(strike):
            continue
        ex = r.get("expiry")
        if not ex:
            continue
        if hasattr(ex, "date"):
            ex_date = ex.date()
        elif hasattr(ex, "year"):
            ex_date = ex
        else:
            try:
                ex_date = datetime.strptime(str(ex)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
        if ex_date != expiry_date:
            continue
        token = r.get("instrument_token")
        if token is None:
            continue

        logger.debug(
            "Resolved token for %s %s %s %s: %s",
            exchange,
            instrument,
            strike,
            option_type,
            token,
        )
        return int(token)

    return None


def _load_nfo_instruments_from_cache() -> list:
    """Load NFO instruments from memory or file cache (no Kite call). For NSE segments only."""
    instruments_all: list = []
    now = time.monotonic()
    if (now - _NFO_INSTRUMENTS_CACHE.get("cached_at", 0)) < _CACHE_TTL_SEC:
        instruments_all = _NFO_INSTRUMENTS_CACHE.get("data") or []
    if not instruments_all and _NFO_CACHE_FILE.exists():
        try:
            with open(_NFO_CACHE_FILE, "rb") as f:
                instruments_all = pickle.load(f)
        except Exception:
            pass
    return instruments_all


def _collect_expiry_dates(instruments_all: list, instrument: str) -> set[date]:
    """Collect unique expiry dates for instrument from NFO list (CE/PE only)."""
    expiries: set[date] = set()
    for r in instruments_all:
        if r.get("instrument_type") not in ("CE", "PE"):
            continue
        if not _match_instrument(instrument, r.get("tradingsymbol", "")):
            continue
        ex = r.get("expiry")
        if ex is None:
            continue
        if hasattr(ex, "year"):
            expiries.add(ex)
        else:
            try:
                expiries.add(datetime.strptime(str(ex)[:10], "%Y-%m-%d").date())
            except Exception:
                pass
    return expiries


def _filter_expiries_by_segment_config(
    sorted_dates: list[date], instrument: str
) -> list[date]:
    """
    Filter expiry dates by segment config: weekday (NIFTY/BANKNIFTY/FINNIFTY Tue, SENSEX Thu)
    and type (weekly = all such weekdays, monthly = last weekday of each month only).
    Uses settings EXPIRY_<INSTRUMENT>_WEEKDAY and EXPIRY_<INSTRUMENT>_TYPE.
    """
    if not sorted_dates:
        return []
    settings = get_settings()
    config = settings.get_expiry_config(instrument.strip().upper())
    weekday = int(config.get("weekday", 1))
    expiry_type = (config.get("type") or "weekly").lower()

    # Keep only dates matching the segment's expiry weekday (Python: Mon=0, Tue=1, ..., Sun=6)
    by_weekday = [d for d in sorted_dates if d.weekday() == weekday]
    if not by_weekday:
        return sorted_dates  # fallback: no filtering if no match (e.g. config mismatch)

    if expiry_type == "weekly":
        return sorted(by_weekday)

    # monthly: keep only last occurrence of that weekday in each (year, month)
    last_of_month: set[date] = set()
    for d in by_weekday:
        _, last_day = monthrange(d.year, d.month)
        # last occurrence of weekday in this month: find the last calendar day that is this weekday
        for day in range(last_day, 0, -1):
            candidate = date(d.year, d.month, day)
            if candidate.weekday() == weekday:
                if candidate == d:
                    last_of_month.add(d)
                break
    return sorted(last_of_month)


def get_expiries_for_instrument(instrument: str) -> list[str]:
    """
    Return sorted list of expiries for the instrument as DDMMMYYYY (e.g. 10MAR2026),
    filtered by segment config: NIFTY/FINNIFTY weekly Tuesday, BANKNIFTY last Tuesday of month,
    SENSEX weekly Thursday. NIFTY/BANKNIFTY/FINNIFTY use NFO (NSE); SENSEX uses BFO (BSE).
    Returns empty list if cache is empty or no options found.
    """
    instruments_all = _load_instruments_from_cache(instrument)
    if not instruments_all:
        return []
    expiries = _collect_expiry_dates(instruments_all, instrument.strip().upper())
    if not expiries:
        return []
    sorted_dates = sorted(expiries)
    filtered = _filter_expiries_by_segment_config(sorted_dates, instrument)
    return [d.strftime("%d%b%Y").upper() for d in filtered]


def get_default_expiry_for_instrument(instrument: str) -> Optional[str]:
    """
    Return nearest expiry for the instrument as DDMMMYYYY, respecting segment config.
    NIFTY/BANKNIFTY/FINNIFTY use NFO (NSE); SENSEX uses BFO (BSE).
    Returns None if cache is empty or no options found.
    """
    expiries_str = get_expiries_for_instrument(instrument)
    if not expiries_str:
        return None
    return expiries_str[0]

# Spot symbols for quote (Kite format). SENSEX is BSE; options use BFO (BSE F&O).
SPOT_SYMBOLS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FINANCIAL SERVICES",
    "SENSEX": "BSE:SENSEX",
}

# Keys for the NSE MARKET strip (frontend shows these three).
INDICES_STRIP_KEYS = ("NIFTY", "BANKNIFTY", "SENSEX")


def fetch_indices_spot_sync(kite: KiteConnect) -> dict[str, dict[str, Any]]:
    """
    Fetch spot and % change for NIFTY, BANKNIFTY, SENSEX in one Kite quote call.
    Returns e.g. {"NIFTY": {"spot": 24500, "spotChgPct": 0.5}, "BANKNIFTY": {...}, "SENSEX": {...}}.
    """
    symbols = [SPOT_SYMBOLS[k] for k in INDICES_STRIP_KEYS if k in SPOT_SYMBOLS]
    if not symbols:
        return {}
    try:
        q = kite.quote(symbols)
    except Exception as e:
        logger.warning("fetch_indices_spot_sync: Kite quote failed: %s", e)
        return {}
    data = q.get("data") if isinstance(q, dict) and "data" in q else (q if isinstance(q, dict) else {})
    result: dict[str, dict[str, Any]] = {}
    for key in INDICES_STRIP_KEYS:
        spot_key = SPOT_SYMBOLS.get(key)
        if not spot_key or spot_key not in data:
            result[key] = {"spot": 0, "spotChgPct": 0}
            continue
        d = data[spot_key]
        spot = float(d.get("last_price", 0) or 0)
        o = d.get("ohlc") or {}
        prev = float(o.get("close") or o.get("open") or spot or 1)
        spot_chg_pct = round((spot - prev) / prev * 100, 2) if prev and prev != 0 else 0.0
        result[key] = {"spot": spot, "spotChgPct": spot_chg_pct}
    return result


def _parse_expiry_frontend(expiry_str: str) -> date:
    """Parse frontend expiry like '10MAR2026' -> date(2026, 3, 10)."""
    from datetime import datetime
    d = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y")
    return d.date()


def _match_instrument(instrument: str, tradingsymbol: str) -> bool:
    """True if tradingsymbol belongs to the given underlying (NIFTY, BANKNIFTY, etc.)."""
    return tradingsymbol.startswith(instrument)


def bootstrap_nfo_instruments_cache(kite: KiteConnect) -> bool:
    """
    Fetch NFO (NSE F&O) instruments from Kite and save to file cache. Call once when not rate limited.
    Returns True on success.
    """
    try:
        instruments_all = kite.instruments("NFO")
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_NFO_CACHE_FILE, "wb") as f:
            pickle.dump(instruments_all, f)
        _NFO_INSTRUMENTS_CACHE["data"] = instruments_all
        _NFO_INSTRUMENTS_CACHE["cached_at"] = time.monotonic()
        logger.info("bootstrap_nfo_instruments_cache: saved %s instruments to %s", len(instruments_all), _NFO_CACHE_FILE)
        return True
    except Exception as e:
        logger.warning("bootstrap_nfo_instruments_cache failed: %s", e)
        return False


def bootstrap_bfo_instruments_cache(kite: KiteConnect) -> bool:
    """
    Fetch BFO (BSE F&O) instruments from Kite and save to file cache. Required for SENSEX option chain.
    Call once when not rate limited. Returns True on success.
    """
    try:
        instruments_all = kite.instruments("BFO")
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_BFO_CACHE_FILE, "wb") as f:
            pickle.dump(instruments_all, f)
        _BFO_INSTRUMENTS_CACHE["data"] = instruments_all
        _BFO_INSTRUMENTS_CACHE["cached_at"] = time.monotonic()
        logger.info("bootstrap_bfo_instruments_cache: saved %s instruments to %s", len(instruments_all), _BFO_CACHE_FILE)
        return True
    except Exception as e:
        logger.warning("bootstrap_bfo_instruments_cache failed: %s", e)
        return False


def fetch_option_chain_sync(
    kite: KiteConnect,
    instrument: str,
    expiry_str: str,
    strikes_up: int = 10,
    strikes_down: int = 10,
) -> dict[str, Any]:
    """
    Synchronous fetch: get instruments from NFO (NSE) or BFO (BSE for SENSEX), filter by instrument and expiry, then quote.
    Returns only strikes in range [ATM - strikes_down, ATM + strikes_up] (configurable).
    """
    expiry_date = _parse_expiry_frontend(expiry_str)
    exchange = _exchange_for_instrument(instrument)  # "NFO" or "BFO" (SENSEX = BSE)
    cache = _BFO_INSTRUMENTS_CACHE if exchange == "BFO" else _NFO_INSTRUMENTS_CACHE
    cache_file = _BFO_CACHE_FILE if exchange == "BFO" else _NFO_CACHE_FILE
    prefix = exchange + ":"  # "NFO:" or "BFO:"

    # Use cached instruments to avoid rate limit (Kite: 3 req/s; instruments dump is daily)
    now = time.monotonic()
    instruments_all: list = []

    if (now - cache.get("cached_at", 0)) < _CACHE_TTL_SEC and cache.get("data"):
        instruments_all = cache.get("data") or []
    else:
        if cache_file.exists():
            try:
                with open(cache_file, "rb") as f:
                    instruments_all = pickle.load(f)
                if instruments_all:
                    cache["data"] = instruments_all
                    cache["cached_at"] = now
                    logger.debug("option_chain: loaded %s instruments from file (%s)", exchange, len(instruments_all))
            except Exception as e:
                logger.warning("option_chain: %s file cache read failed: %s", exchange, e)

        if not instruments_all:
            try:
                instruments_all = kite.instruments(exchange)
                cache["data"] = instruments_all
                cache["cached_at"] = now
                _DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "wb") as f:
                    pickle.dump(instruments_all, f)
                logger.info("option_chain: cached %s instruments (%s) to memory and file", exchange, len(instruments_all))
            except Exception as e:
                if cache_file.exists():
                    try:
                        with open(cache_file, "rb") as f:
                            instruments_all = pickle.load(f)
                        logger.warning("option_chain: Kite failed (%s), using %s file cache", e, exchange)
                    except Exception:
                        raise
                elif cache.get("data"):
                    instruments_all = cache["data"]
                    logger.warning("option_chain: Kite failed (%s), using stale %s cache", e, exchange)
                else:
                    raise

    # Filter: CE/PE only, same underlying, same expiry (Kite may return expiry as date or string)
    def _expiry_match(r_expiry) -> bool:
        if r_expiry is None:
            return False
        if hasattr(r_expiry, "year"):
            return r_expiry == expiry_date
        return str(r_expiry) == str(expiry_date)

    options = [
        r for r in instruments_all
        if r.get("instrument_type") in ("CE", "PE")
        and _match_instrument(instrument, r.get("tradingsymbol", ""))
        and _expiry_match(r.get("expiry"))
    ]
    if not options:
        logger.info("option_chain: no options for %s expiry %s (date=%s)", instrument, expiry_str, expiry_date)
        return {
            "spot": 0.0,
            "spotChgPct": 0.0,
            "vix": None,
            "synFuture": None,
            "pcr": 0.0,
            "pcrVol": 0.0,
            "updated": None,
            "chain": [],
            "error": f"No options found for {instrument} expiry {expiry_str}",
        }

    # Build list of NFO:tradingsymbol or BFO:tradingsymbol for quote (max 250 per request)
    symbols_quote = list(dict.fromkeys(prefix + r["tradingsymbol"] for r in options))
    # Add spot
    spot_key = SPOT_SYMBOLS.get(instrument)
    if spot_key:
        symbols_quote.insert(0, spot_key)

    quotes: dict = {}
    batch = 250
    for i in range(0, len(symbols_quote), batch):
        if i > 0:
            time.sleep(0.4)  # Kite limit ~3 req/s; avoid burst
        chunk = symbols_quote[i : i + batch]
        try:
            q = kite.quote(chunk)
            if isinstance(q, dict) and "data" in q:
                quotes.update(q["data"])
            elif isinstance(q, dict):
                quotes.update(q)
        except Exception as e:
            logger.warning("Kite quote chunk failed: %s", e)

    spot = 0.0
    spot_chg_pct = 0.0
    if spot_key and spot_key in quotes:
        d = quotes[spot_key]
        spot = float(d.get("last_price", 0) or 0)
        o = d.get("ohlc") or {}
        prev = float(o.get("close") or o.get("open") or spot or 1)
        if prev and prev != 0:
            spot_chg_pct = round((spot - prev) / prev * 100, 2)

    def _ltp_chg(q: Optional[dict]) -> float:
        if not q:
            return 0.0
        lp = float(q.get("last_price") or 0)
        o = q.get("ohlc") or {}
        close = float(o.get("close") or o.get("open") or lp or 1)
        if not close:
            return 0.0
        return round((lp - close) / close * 100, 2)

    by_strike: dict[float, dict[str, Any]] = {}
    for r in options:
        strike = float(r.get("strike", 0))
        lot_size = int(r.get("lot_size") or 1)
        if strike not in by_strike:
            by_strike[strike] = {"ce": None, "pe": None}
        key = prefix + r["tradingsymbol"]
        q = quotes.get(key) or {}
        # Kite quote returns OI as "oi" (F&O); fallback to "open_interest"
        oi_val = float(q.get("oi") or q.get("open_interest") or 0)
        typ = r.get("instrument_type")
        rec = {
            "tradingsymbol": r["tradingsymbol"],
            "lot_size": lot_size,
            "last_price": float(q.get("last_price") or 0),
            "volume": int(q.get("volume") or 0),
            "open_interest": oi_val,
            "ohlc": q.get("ohlc") or {},
        }
        if typ == "CE":
            by_strike[strike]["ce"] = rec
        elif typ == "PE":
            by_strike[strike]["pe"] = rec

    strikes_sorted = sorted(by_strike.keys())
    # Spot may not be available yet; compute ATM from first quote or use mid of strikes
    spot_for_atm = spot if spot > 0 else (strikes_sorted[len(strikes_sorted) // 2] if strikes_sorted else 0)
    # ATM = strike closest to spot; NIFTY step 50, BANKNIFTY 100, SENSEX 100
    step = 50 if instrument == "NIFTY" else 100
    atm_strike = round(spot_for_atm / step) * step if spot_for_atm else (strikes_sorted[len(strikes_sorted) // 2] if strikes_sorted else 0)
    # Index of ATM in sorted list (closest strike)
    try:
        atm_idx = min(range(len(strikes_sorted)), key=lambda i: abs(strikes_sorted[i] - spot_for_atm))
    except (ValueError, TypeError):
        atm_idx = len(strikes_sorted) // 2
    # Slice: strikes_down below ATM, ATM, strikes_up above ATM (configurable)
    start = max(0, atm_idx - strikes_down)
    end = min(len(strikes_sorted), atm_idx + strikes_up + 1)
    strikes_filtered = strikes_sorted[start:end]

    total_call_oi = 0.0
    total_put_oi = 0.0
    cache_key = (instrument, expiry_str)
    next_snapshot: dict[float, dict[str, dict[str, float]]] = {}
    # Previous snapshot from store for OI change % and buildup
    stored = get_snapshots(instrument, expiry_str)
    prev_stored = stored[-1] if stored else {}
    prev_by_strike = prev_stored.get("by_strike") or {}

    def _buildup(curr_oi: float, curr_ltp: float, prev_oi: float, prev_ltp: float) -> str:
        if prev_oi is None or prev_ltp is None or (prev_oi == 0 and prev_ltp == 0):
            return "—"
        oi_up = curr_oi > prev_oi
        price_up = curr_ltp > prev_ltp
        if oi_up and price_up:
            return BUILDUP_LABELS[0]   # Long Buildup
        if oi_up and not price_up:
            return BUILDUP_LABELS[1]   # Short Buildup
        if not oi_up and price_up:
            return BUILDUP_LABELS[2]   # Short Covering
        return BUILDUP_LABELS[3]       # Long Unwinding

    chain = []
    for strike in strikes_filtered:
        ce = by_strike[strike]["ce"]
        pe = by_strike[strike]["pe"]
        call_oi_val = (ce["open_interest"] or 0) * ce["lot_size"] if ce else 0
        put_oi_val = (pe["open_interest"] or 0) * pe["lot_size"] if pe else 0
        total_call_oi += call_oi_val
        total_put_oi += put_oi_val

        call_ltp = (ce["last_price"] if ce else 0.0)
        put_ltp = (pe["last_price"] if pe else 0.0)
        call_vol = (ce["volume"] if ce else 0) or 0
        put_vol = (pe["volume"] if pe else 0) or 0
        pcr_strike = (put_oi_val / call_oi_val) if call_oi_val else 0.0

        # Greeks from Black-Scholes (spot, strike, expiry, LTP)
        call_delta_val, call_theta_val, call_iv_val = 0.0, 0.0, 0.0
        put_delta_val, put_theta_val, put_iv_val = 0.0, 0.0, 0.0
        if spot > 0:
            try:
                call_delta_val, call_theta_val, call_iv_val = compute_greeks(
                    spot, strike, expiry_date, call_ltp, "CE"
                )
                put_delta_val, put_theta_val, put_iv_val = compute_greeks(
                    spot, strike, expiry_date, put_ltp, "PE"
                )
            except Exception as e:
                logger.debug("greeks for strike %s: %s", strike, e)

        prev_c = prev_by_strike.get(strike, {}).get("call", {})
        prev_p = prev_by_strike.get(strike, {}).get("put", {})
        call_buildup = _buildup(call_oi_val, call_ltp, prev_c.get("oi", 0) or 0, prev_c.get("ltp", 0) or 0)
        put_buildup = _buildup(put_oi_val, put_ltp, prev_p.get("oi", 0) or 0, prev_p.get("ltp", 0) or 0)
        next_snapshot[strike] = {
            "call": {"oi": call_oi_val, "ltp": call_ltp},
            "put": {"oi": put_oi_val, "ltp": put_ltp},
        }

        # OI change % from last stored snapshot
        prev_call_oi = prev_by_strike.get(strike, {}).get("call", {}).get("oi", 0) or 0
        prev_put_oi = prev_by_strike.get(strike, {}).get("put", {}).get("oi", 0) or 0
        call_oi_chg_pct = _oi_change_pct(prev_call_oi, call_oi_val)
        put_oi_chg_pct = _oi_change_pct(prev_put_oi, put_oi_val)

        chain.append({
            "strike": strike,
            "call": {
                "buildup": call_buildup,
                "oiChgPct": call_oi_chg_pct,
                "theta": call_theta_val,
                "delta": call_delta_val,
                "iv": call_iv_val,
                "volume": str(call_vol),
                "oi": str(int(call_oi_val)),
                "ltpChg": _ltp_chg(ce),
                "ltp": call_ltp,
            },
            "put": {
                "pcr": round(pcr_strike, 2) if call_oi_val else 0,
                "ltp": put_ltp,
                "ltpChg": _ltp_chg(pe),
                "oi": str(int(put_oi_val)),
                "oiChgPct": put_oi_chg_pct,
                "volume": str(put_vol),
                "iv": put_iv_val,
                "delta": put_delta_val,
                "theta": put_theta_val,
                "buildup": put_buildup,
            },
        })

    # Push this run as latest snapshot (for next OI change % and buildup)
    push_snapshot(instrument, expiry_str, {
        "updated": datetime.utcnow().isoformat() + "Z",
        "spot": spot,
        "by_strike": next_snapshot,
    })
    _buildup_cache[cache_key] = next_snapshot
    pcr = (total_put_oi / total_call_oi) if total_call_oi else 0.0
    return {
        "spot": spot,
        "spotChgPct": spot_chg_pct,
        "vix": None,
        "synFuture": None,
        "pcr": round(pcr, 2),
        "pcrVol": 0.0,
        "updated": datetime.utcnow().isoformat() + "Z",
        "chain": chain,
    }
