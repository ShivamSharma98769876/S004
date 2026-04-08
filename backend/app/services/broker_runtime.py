"""Broker-agnostic market-data and execution runtime."""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from app.services import broker_accounts as ba
from app.services.kite_broker import OrderResult, place_nfo_market_order
from app.services.option_greeks import compute_greeks
from app.services.option_chain_zerodha import (
    fetch_indices_spot_sync,
    fetch_index_candles_sync,
    fetch_option_chain_sync,
    get_expiries_for_analytics,
    verify_kite_session_sync,
)

_FYERS_LEG_HISTORY_MAX = 48
_FYERS_LEG_HISTORY: dict[str, deque[tuple[float, float]]] = {}


class MarketDataProvider(Protocol):
    broker_code: str

    async def session_ok(self) -> bool: ...

    async def indices(self) -> dict[str, Any]: ...

    async def expiries(self, instrument: str) -> tuple[list[str], str]: ...

    async def index_candles(
        self,
        instrument: str,
        interval: str = "5minute",
        days_back: int = 5,
    ) -> list[dict[str, Any]]: ...

    async def option_chain(
        self,
        instrument: str,
        expiry: str,
        strikes_up: int,
        strikes_down: int,
        require_live: bool,
    ) -> dict[str, Any]: ...


class ExecutionProvider(Protocol):
    broker_code: str

    async def place_entry(self, symbol: str, side: str, quantity: int, expected_price: float) -> OrderResult: ...

    async def place_exit(self, symbol: str, side: str, quantity: int) -> OrderResult: ...


@dataclass
class ResolvedBrokerContext:
    broker_code: str | None
    source: str
    market_data: MarketDataProvider | None
    execution: ExecutionProvider | None
    active_broker: str | None
    is_admin: bool


class ZerodhaProvider(MarketDataProvider, ExecutionProvider):
    broker_code = ba.BROKER_ZERODHA

    def __init__(self, kite: Any):
        self.kite = kite

    async def session_ok(self) -> bool:
        return bool(self.kite and await asyncio.to_thread(verify_kite_session_sync, self.kite))

    async def indices(self) -> dict[str, Any]:
        return await asyncio.to_thread(fetch_indices_spot_sync, self.kite)

    async def expiries(self, instrument: str) -> tuple[list[str], str]:
        ok = await self.session_ok()
        kite_for_list = self.kite if ok else None
        return get_expiries_for_analytics(kite_for_list, instrument)

    async def index_candles(
        self,
        instrument: str,
        interval: str = "5minute",
        days_back: int = 5,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(fetch_index_candles_sync, self.kite, instrument, interval, days_back)

    async def option_chain(
        self,
        instrument: str,
        expiry: str,
        strikes_up: int,
        strikes_down: int,
        require_live: bool,
    ) -> dict[str, Any]:
        if require_live and not await self.session_ok():
            raise RuntimeError("Live Zerodha session required for option chain.")
        kite = self.kite if await self.session_ok() else None
        return await asyncio.to_thread(
            fetch_option_chain_sync,
            kite,
            instrument,
            expiry,
            strikes_up,
            strikes_down,
        )

    async def place_entry(self, symbol: str, side: str, quantity: int, expected_price: float) -> OrderResult:
        _ = expected_price
        txn = "BUY" if str(side or "BUY").upper() == "BUY" else "SELL"
        return place_nfo_market_order(self.kite, symbol, txn, quantity)

    async def place_exit(self, symbol: str, side: str, quantity: int) -> OrderResult:
        orig = str(side or "BUY").upper()
        txn = "SELL" if orig == "BUY" else "BUY"
        return place_nfo_market_order(self.kite, symbol, txn, quantity)


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pick(obj: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in obj and obj.get(k) is not None:
            return obj.get(k)
    return None


def _extract_opt_fields(opt: dict[str, Any]) -> dict[str, Any]:
    """Normalize FYERS option row fields across multiple payload shapes."""
    v = opt.get("v") if isinstance(opt.get("v"), dict) else {}
    g = opt.get("greeks") if isinstance(opt.get("greeks"), dict) else {}
    g2 = opt.get("optionGreeks") if isinstance(opt.get("optionGreeks"), dict) else {}
    g3 = opt.get("option_greeks") if isinstance(opt.get("option_greeks"), dict) else {}
    md = opt.get("market_data") if isinstance(opt.get("market_data"), dict) else {}
    ltp = _float(_pick(opt, "ltp", "last_price", "lastPrice", "lp"))
    if ltp == 0:
        ltp = _float(_pick(v, "lp", "ltp", "last_price"))
    if ltp == 0:
        ltp = _float(_pick(md, "lp", "ltp", "last_price"))
    # Prefer percentage fields; ltpch in some payloads is absolute points.
    chp = _float(_pick(opt, "change_pct", "chp", "ltp_change_pct", "changePerc"))
    if chp == 0:
        chp = _float(_pick(v, "chp", "change_pct", "changePerc"))
    if chp == 0:
        chp = _float(_pick(md, "chp", "change_pct", "changePerc"))
    if chp == 0:
        abs_chg = _float(_pick(opt, "ltpch", "change", "ltp_change"))
        if abs_chg == 0:
            abs_chg = _float(_pick(v, "ltpch", "change"))
        if abs_chg != 0 and ltp > 0:
            prev = ltp - abs_chg
            if prev > 0:
                chp = (abs_chg / prev) * 100.0
    oi = _float(_pick(opt, "oi", "open_interest", "openInterest"))
    if oi == 0:
        oi = _float(_pick(v, "oi", "open_interest"))
    if oi == 0:
        oi = _float(_pick(md, "oi", "open_interest", "openInterest"))
    oi_chg_abs = _float(
        _pick(
            opt,
            "oiChg",
            "oi_change",
            "oiChange",
            "open_interest_change",
            "openInterestChange",
            "oich",
        )
    )
    if oi_chg_abs == 0:
        oi_chg_abs = _float(_pick(v, "oiChg", "oi_change", "oiChange", "open_interest_change", "oich"))
    if oi_chg_abs == 0:
        oi_chg_abs = _float(_pick(md, "oiChg", "oi_change", "oiChange", "open_interest_change", "oich"))
    oi_chg_pct_val = _pick(
        opt,
        "oiChgPct",
        "oi_change_pct",
        "oi_change_perc",
        "oichangepct",
        "oiChangePct",
    )
    if oi_chg_pct_val is None:
        oi_chg_pct_val = _pick(v, "oi_change_pct", "oi_change_perc", "oichangepct", "oiChgPct", "oiChangePct")
    if oi_chg_pct_val is None:
        oi_chg_pct_val = _pick(md, "oi_change_pct", "oi_change_perc", "oichangepct", "oiChgPct", "oiChangePct")
    oi_chg_pct = _float(oi_chg_pct_val, default=float("nan"))
    if oi_chg_pct != oi_chg_pct and oi_chg_abs != 0 and oi > 0:
        prev_oi = oi - oi_chg_abs
        if prev_oi > 0:
            oi_chg_pct = (oi_chg_abs / prev_oi) * 100.0
    vol = _float(_pick(opt, "volume", "vol", "traded_volume", "tradedVolume"))
    if vol == 0:
        vol = _float(_pick(v, "volume", "vol"))
    if vol == 0:
        vol = _float(_pick(md, "volume", "vol", "traded_volume", "tradedVolume"))
    iv = _float(_pick(opt, "iv", "implied_volatility", "impliedVolatility"))
    if iv == 0:
        iv = _float(_pick(g, "iv", "impliedVolatility"))
    if iv == 0:
        iv = _float(_pick(g2, "iv", "impliedVolatility"))
    if iv == 0:
        iv = _float(_pick(g3, "iv", "impliedVolatility"))
    delta = _float(_pick(opt, "delta"))
    if delta == 0:
        delta = _float(_pick(g, "delta"))
    if delta == 0:
        delta = _float(_pick(g2, "delta"))
    if delta == 0:
        delta = _float(_pick(g3, "delta"))
    theta = _float(_pick(opt, "theta"))
    if theta == 0:
        theta = _float(_pick(g, "theta"))
    if theta == 0:
        theta = _float(_pick(g2, "theta"))
    if theta == 0:
        theta = _float(_pick(g3, "theta"))
    return {
        "ltp": ltp,
        "ltpChg": chp,
        "oi": oi,
        "oiChgPct": (None if oi_chg_pct != oi_chg_pct else oi_chg_pct),
        "volume": vol,
        "iv": iv,
        "delta": delta,
        "theta": theta,
    }


def _fyers_underlying(instrument: str) -> str:
    inst = str(instrument or "").upper().strip()
    if inst == "NIFTY":
        return "NSE:NIFTY50-INDEX"
    if inst == "BANKNIFTY":
        return "NSE:NIFTYBANK-INDEX"
    if inst == "FINNIFTY":
        return "NSE:FINNIFTY-INDEX"
    if inst == "SENSEX":
        return "BSE:SENSEX-INDEX"
    return "NSE:NIFTY50-INDEX"


def _fyers_resolution(interval: str) -> str:
    iv = str(interval or "5minute").strip().lower()
    if iv == "minute":
        return "1"
    if iv.endswith("minute"):
        m = iv.replace("minute", "").strip()
        if m.isdigit():
            return m
    if iv in {"day", "1day"}:
        return "D"
    return "5"


def _fyers_symbol_from_compact(symbol: str) -> str:
    s = str(symbol or "").strip().upper().replace(" ", "")
    if ":" in s:
        return s
    return f"NSE:{s}"


def _extract_fyers_data(payload: dict[str, Any]) -> dict[str, Any]:
    """FYERS responses vary by SDK version; normalize top-level data envelope."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _normalize_expiry_label(raw: Any) -> str | None:
    """Normalize FYERS expiry values to DDMMMYYYY used by analytics UI."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Epoch seconds / milliseconds
    if s.isdigit():
        try:
            ts = int(s)
            if ts > 10_000_000_000:  # ms
                ts //= 1000
            return datetime.utcfromtimestamp(ts).strftime("%d%b%Y").upper()
        except Exception:
            return s
    # ISO yyyy-mm-dd
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d%b%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d%b%Y").upper()
        except Exception:
            pass
    return s.upper()


def _ema(vals: list[float], period: int) -> float:
    if not vals:
        return 0.0
    p = max(1, int(period))
    k = 2.0 / (p + 1.0)
    out = float(vals[0])
    for v in vals[1:]:
        out = float(v) * k + out * (1.0 - k)
    return out


def _rsi(vals: list[float], period: int = 14) -> float:
    if len(vals) < 3:
        return 50.0
    p = max(1, min(int(period), len(vals) - 1))
    diffs = [vals[i] - vals[i - 1] for i in range(len(vals) - p, len(vals))]
    gains = [d for d in diffs if d > 0]
    losses = [-d for d in diffs if d < 0]
    avg_gain = (sum(gains) / p) if gains else 0.0
    avg_loss = (sum(losses) / p) if losses else 0.0
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _fyers_hist_key(instrument: str, expiry: str, strike: float, side: str) -> str:
    return f"{str(instrument).upper()}|{str(expiry).upper()}|{int(round(float(strike)))}|{str(side).upper()}"


def _fyers_leg_indicator_pack(instrument: str, expiry: str, strike: float, side: str, ltp: float, volume: float) -> dict[str, Any]:
    k = _fyers_hist_key(instrument, expiry, strike, side)
    bucket = _FYERS_LEG_HISTORY.get(k)
    if bucket is None:
        bucket = deque(maxlen=_FYERS_LEG_HISTORY_MAX)
        _FYERS_LEG_HISTORY[k] = bucket
    bucket.append((float(ltp), max(0.0, float(volume))))
    ltps = [float(x[0]) for x in bucket]
    vols = [float(x[1]) for x in bucket]
    ema9 = _ema(ltps[-30:], 9)
    ema21 = _ema(ltps[-30:], 21)
    rsi = _rsi(ltps[-30:], 14)
    vol_sum = sum(vols)
    if vol_sum > 0:
        vwap = sum(p * v for p, v in bucket) / vol_sum
    else:
        vwap = statistics.mean(ltps) if ltps else float(ltp)
    avg_volume = statistics.mean(vols[:-1]) if len(vols) > 1 else (vols[-1] if vols else 0.0)
    volume_spike_ratio = (vols[-1] / avg_volume) if avg_volume > 0 and vols else 0.0
    return {
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 2),
        "vwap": round(vwap, 2),
        "avgVolume": round(float(avg_volume), 2),
        "volumeSpikeRatio": round(float(volume_spike_ratio), 2),
    }


def _parse_expiry_to_date(raw: Any) -> date | None:
    n = _normalize_expiry_label(raw)
    if not n:
        return None
    try:
        return datetime.strptime(n, "%d%b%Y").date()
    except Exception:
        return None


def _add_ivr_to_rows(rows: list[dict[str, Any]]) -> None:
    all_ivs: list[float] = []
    for row in rows:
        for leg_key in ("call", "put"):
            leg = row.get(leg_key) or {}
            iv = _float(leg.get("iv"))
            if iv > 0:
                all_ivs.append(iv)
    if not all_ivs:
        return
    min_iv = min(all_ivs)
    max_iv = max(all_ivs)
    iv_range = max(max_iv - min_iv, 1e-6)
    for row in rows:
        for leg_key in ("call", "put"):
            leg = row.get(leg_key) or {}
            iv = _float(leg.get("iv"))
            leg["ivr"] = round((iv - min_iv) / iv_range * 100.0, 2) if iv > 0 else None


class FyersProvider(MarketDataProvider, ExecutionProvider):
    broker_code = ba.BROKER_FYERS

    def __init__(self, fy: Any):
        self.fy = fy

    async def session_ok(self) -> bool:
        if not self.fy:
            return False
        try:
            resp = await asyncio.to_thread(self.fy.get_profile)
            return bool(resp)
        except Exception:
            return False

    async def indices(self) -> dict[str, Any]:
        syms = "NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:FINNIFTY-INDEX,BSE:SENSEX-INDEX"
        q = await asyncio.to_thread(self.fy.quotes, {"symbols": syms})
        d = (q or {}).get("d") or (q or {}).get("data") or []
        out: dict[str, dict[str, Any]] = {
            "NIFTY": {"spot": 0.0, "spotChgPct": 0.0},
            "BANKNIFTY": {"spot": 0.0, "spotChgPct": 0.0},
            "FINNIFTY": {"spot": 0.0, "spotChgPct": 0.0},
            "SENSEX": {"spot": 0.0, "spotChgPct": 0.0},
        }
        for row in d if isinstance(d, list) else []:
            n = row.get("n") or row.get("symbol") or ""
            v = row.get("v") or {}
            if "NIFTY50" in str(n):
                out["NIFTY"] = {"spot": _float(v.get("lp")), "spotChgPct": _float(v.get("chp"))}
            elif "NIFTYBANK" in str(n):
                out["BANKNIFTY"] = {"spot": _float(v.get("lp")), "spotChgPct": _float(v.get("chp"))}
            elif "FINNIFTY" in str(n):
                out["FINNIFTY"] = {"spot": _float(v.get("lp")), "spotChgPct": _float(v.get("chp"))}
            elif "SENSEX" in str(n):
                out["SENSEX"] = {"spot": _float(v.get("lp")), "spotChgPct": _float(v.get("chp"))}
        return out

    async def expiries(self, instrument: str) -> tuple[list[str], str]:
        under = _fyers_underlying(instrument)
        oc = await asyncio.to_thread(
            self.fy.optionchain,
            {"symbol": under, "strikecount": 1, "timestamp": ""},
        )
        data = _extract_fyers_data(oc or {})
        ex = data.get("expiryData") or data.get("expiryDates") or []
        expiries: list[str] = []
        for e in ex if isinstance(ex, list) else []:
            if isinstance(e, dict):
                if e.get("date"):
                    v = _normalize_expiry_label(e.get("date"))
                    if v:
                        expiries.append(v)
                    continue
                if e.get("expiry"):
                    v = _normalize_expiry_label(e.get("expiry"))
                    if v:
                        expiries.append(v)
                    continue
            if isinstance(e, (str, int, float)):
                v = _normalize_expiry_label(e)
                if v:
                    expiries.append(v)
        # Normalize to chronological order; API order can be monthly-first.
        unique = {x for x in expiries if str(x).strip()}
        dated: list[tuple[date, str]] = []
        for lbl in unique:
            try:
                d = datetime.strptime(str(lbl).strip().upper(), "%d%b%Y").date()
            except ValueError:
                continue
            dated.append((d, str(lbl).strip().upper()))
        dated.sort(key=lambda x: x[0])
        expiries = [lbl for _, lbl in dated]
        if expiries:
            nearest_dte = (dated[0][0] - date.today()).days
            # FYERS optionchain can occasionally return only monthly series for index underlyings.
            # If nearest expiry is unusually far, fall back to weekly estimator so strategy default
            # remains immediate expiry unless explicitly configured otherwise.
            if nearest_dte > 10:
                fallback, fb_src = get_expiries_for_analytics(None, instrument)
                if fallback:
                    return fallback, f"{fb_src}_fyers_fallback"
        return expiries, "fyers_optionchain"

    async def index_candles(
        self,
        instrument: str,
        interval: str = "5minute",
        days_back: int = 5,
    ) -> list[dict[str, Any]]:
        now = int(time.time())
        start = int((datetime.utcnow() - timedelta(days=max(1, int(days_back)))).timestamp())
        payload = {
            "symbol": _fyers_underlying(instrument),
            "resolution": _fyers_resolution(interval),
            "date_format": "0",
            "range_from": str(start),
            "range_to": str(now),
            "cont_flag": "1",
        }
        res = await asyncio.to_thread(self.fy.history, payload)
        data = _extract_fyers_data(res or {})
        candles = data.get("candles") or (res or {}).get("candles") or []
        out: list[dict[str, Any]] = []
        for c in candles if isinstance(candles, list) else []:
            if not isinstance(c, list) or len(c) < 6:
                continue
            ts = c[0]
            try:
                ts_i = int(float(ts))
                t_iso = datetime.utcfromtimestamp(ts_i).isoformat() + "Z"
            except Exception:
                t_iso = str(ts)
            out.append(
                {
                    "open": _float(c[1]),
                    "high": _float(c[2]),
                    "low": _float(c[3]),
                    "close": _float(c[4]),
                    "volume": _float(c[5]),
                    "time": t_iso,
                }
            )
        return out

    async def option_chain(
        self,
        instrument: str,
        expiry: str,
        strikes_up: int,
        strikes_down: int,
        require_live: bool,
    ) -> dict[str, Any]:
        _ = (strikes_up, strikes_down)
        if require_live and not await self.session_ok():
            raise RuntimeError("Live FYERS session required for option chain.")
        under = _fyers_underlying(instrument)
        expiry_date = ""
        try:
            expiry_date = datetime.strptime(expiry, "%d%b%Y").strftime("%Y-%m-%d")
        except Exception:
            # FYERS also accepts epoch or blank depending on account/product; fallback below handles both.
            expiry_date = str(expiry or "").strip()
        oc = await asyncio.to_thread(self.fy.optionchain, {"symbol": under, "strikecount": 20, "timestamp": expiry_date})
        data = _extract_fyers_data(oc or {})
        options = (
            data.get("optionsChain")
            or data.get("optionChain")
            or data.get("options_chain")
            or data.get("option_chain")
            or data.get("chain")
            or data.get("options")
            or []
        )
        if not options:
            # Fallback: nearest chain without timestamp.
            oc2 = await asyncio.to_thread(self.fy.optionchain, {"symbol": under, "strikecount": 20, "timestamp": ""})
            data = _extract_fyers_data(oc2 or {})
            options = (
                data.get("optionsChain")
                or data.get("optionChain")
                or data.get("options_chain")
                or data.get("option_chain")
                or data.get("chain")
                or data.get("options")
                or []
            )
        underlying = (
            data.get("underlyingValue")
            or data.get("underlying_value")
            or data.get("underLyingValue")
            or data.get("ltp")
            or data.get("spot")
            or (data.get("underlying") or {}).get("ltp")
            or (data.get("underlying") or {}).get("value")
            or 0
        )
        spot = _float(underlying)
        spot_chg_pct = _float(
            data.get("underlyingChangePercent")
            or data.get("underlying_change_percent")
            or data.get("underlyingChp")
            or 0.0
        )
        if spot <= 0:
            try:
                idx = await self.indices()
                x = idx.get(str(instrument).upper()) or {}
                spot = _float(x.get("spot"))
                spot_chg_pct = _float(x.get("spotChgPct"))
            except Exception:
                pass
        expiry_dt = _parse_expiry_to_date(expiry_date or expiry)
        chain_map: dict[float, dict[str, Any]] = {}
        for opt in options if isinstance(options, list) else []:
            strike = _float(opt.get("strike_price") or opt.get("strikePrice") or opt.get("strike"))
            if strike <= 0:
                continue
            side = str(opt.get("option_type") or opt.get("optionType") or opt.get("type") or "").upper()
            if side not in {"CE", "PE"}:
                symbol = str(
                    opt.get("symbol")
                    or opt.get("name")
                    or opt.get("trading_symbol")
                    or opt.get("tradingsymbol")
                    or ""
                ).upper()
                side = "CE" if "CE" in symbol else "PE" if "PE" in symbol else ""
            if side not in {"CE", "PE"}:
                continue
            bucket = chain_map.setdefault(
                strike,
                {
                    "strike": strike,
                    "call": {
                        "buildup": "—",
                        "oiChgPct": None,
                        "theta": 0.0,
                        "delta": 0.0,
                        "iv": 0.0,
                        "ivr": None,
                        "volume": "0",
                        "oi": "0",
                        "ltpChg": 0.0,
                        "ltp": 0.0,
                    },
                    "put": {
                        "pcr": 0.0,
                        "ltp": 0.0,
                        "ltpChg": 0.0,
                        "oi": "0",
                        "oiChgPct": None,
                        "volume": "0",
                        "iv": 0.0,
                        "ivr": None,
                        "delta": 0.0,
                        "theta": 0.0,
                        "buildup": "—",
                    },
                },
            )
            leg = bucket["call"] if side == "CE" else bucket["put"]
            f = _extract_opt_fields(opt)
            leg["ltp"] = f["ltp"]
            leg["ltpChg"] = f["ltpChg"]
            leg["oi"] = str(int(_float(f["oi"])))
            leg["oiChgPct"] = f["oiChgPct"]
            leg["volume"] = str(int(_float(f["volume"])))
            leg["iv"] = f["iv"]
            leg["delta"] = f["delta"]
            leg["theta"] = f["theta"]
            ind = _fyers_leg_indicator_pack(instrument, str(expiry_date or expiry), strike, side, f["ltp"], f["volume"])
            leg["ema9"] = ind["ema9"]
            leg["ema21"] = ind["ema21"]
            leg["rsi"] = ind["rsi"]
            leg["vwap"] = ind["vwap"]
            leg["avgVolume"] = ind["avgVolume"]
            leg["volumeSpikeRatio"] = ind["volumeSpikeRatio"]
            # Approximate buildup when OI/price delta is available.
            oi_chg = f["oiChgPct"]
            px_chg = f["ltpChg"]
            if isinstance(oi_chg, float):
                if oi_chg > 0 and px_chg > 0:
                    leg["buildup"] = "Long Buildup"
                elif oi_chg > 0 and px_chg < 0:
                    leg["buildup"] = "Short Buildup"
                elif oi_chg < 0 and px_chg > 0:
                    leg["buildup"] = "Short Covering"
                elif oi_chg < 0 and px_chg < 0:
                    leg["buildup"] = "Long Unwinding"
        rows = sorted(chain_map.values(), key=lambda x: x["strike"])
        if not rows:
            msg = str((oc or {}).get("message") or (oc or {}).get("s") or "No option chain rows returned by FYERS")
            raise RuntimeError(msg)
        # Fill missing greeks/IV using BS-model fallback, then compute IVR chain percentile.
        if expiry_dt is not None and spot > 0:
            for row in rows:
                strike = _float(row.get("strike"))
                if strike <= 0:
                    continue
                for leg_key, opt_type in (("call", "CE"), ("put", "PE")):
                    leg = row.get(leg_key) or {}
                    delta = _float(leg.get("delta"))
                    theta = _float(leg.get("theta"))
                    iv = _float(leg.get("iv"))
                    if (abs(delta) < 1e-9 and abs(theta) < 1e-9) or iv <= 0:
                        ltp = _float(leg.get("ltp"))
                        if ltp > 0:
                            d_calc, t_calc, iv_calc = compute_greeks(spot, strike, expiry_dt, ltp, opt_type)
                            if abs(delta) < 1e-9 and d_calc != 0:
                                leg["delta"] = d_calc
                            if abs(theta) < 1e-9 and t_calc != 0:
                                leg["theta"] = t_calc
                            if iv <= 0 and iv_calc > 0:
                                leg["iv"] = iv_calc
        _add_ivr_to_rows(rows)
        total_ce = sum(_float(r["call"]["oi"]) for r in rows)
        total_pe = sum(_float(r["put"]["oi"]) for r in rows)
        pcr = (total_pe / total_ce) if total_ce > 0 else 0.0
        for r in rows:
            ce = _float(r["call"]["oi"])
            pe = _float(r["put"]["oi"])
            r["put"]["pcr"] = (pe / ce) if ce > 0 else 0.0
        vix_val: float | None = None
        try:
            vq = await asyncio.to_thread(self.fy.quotes, {"symbols": "NSE:INDIAVIX-INDEX"})
            vd = (vq or {}).get("d") or (vq or {}).get("data") or []
            if isinstance(vd, list) and vd:
                vv = vd[0].get("v") if isinstance(vd[0], dict) else {}
                if isinstance(vv, dict):
                    vix = _float(vv.get("lp"))
                    if vix > 0:
                        vix_val = vix
        except Exception:
            vix_val = None
        return {
            "spot": spot,
            "spotChgPct": spot_chg_pct,
            "vix": vix_val,
            "synFuture": (spot if spot > 0 else None),
            "pcr": pcr,
            "pcrVol": 0.0,
            "updated": datetime.utcnow().isoformat() + "Z",
            "chain": rows,
            "from_cache": False,
            "using_live_broker": True,
        }

    async def place_entry(self, symbol: str, side: str, quantity: int, expected_price: float) -> OrderResult:
        _ = expected_price
        try:
            payload = {
                "symbol": _fyers_symbol_from_compact(symbol),
                "qty": int(quantity),
                "type": 2,
                "side": 1 if str(side or "BUY").upper() == "BUY" else -1,
                "productType": "INTRADAY",
                "limitPrice": 0,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
            }
            resp = await asyncio.to_thread(self.fy.place_order, payload)
            oid = str((resp or {}).get("id") or (resp or {}).get("order_id") or "")
            if oid:
                return OrderResult(True, oid, None, None, None)
            msg = str((resp or {}).get("message") or "FYERS order placement failed")
            return OrderResult(False, None, None, "ORDER_FAILED", msg)
        except Exception as exc:
            e = str(exc)
            u = e.upper()
            if any(x in u for x in ("TOKEN", "SESSION", "UNAUTHORIZED", "AUTH")):
                return OrderResult(False, None, None, "TOKEN_EXPIRED", e)
            if any(x in u for x in ("MARGIN", "INSUFFICIENT")):
                return OrderResult(False, None, None, "INSUFFICIENT_MARGIN", e)
            return OrderResult(False, None, None, "ORDER_FAILED", e)

    async def place_exit(self, symbol: str, side: str, quantity: int) -> OrderResult:
        txn = "SELL" if str(side or "BUY").upper() == "BUY" else "BUY"
        return await self.place_entry(symbol, txn, quantity, 0.0)


async def _resolve_user_provider(user_id: int) -> tuple[MarketDataProvider | None, str | None]:
    active = await ba.get_active_broker_code(user_id)
    if active == ba.BROKER_FYERS:
        fy = await ba.user_fyers_client(user_id)
        if fy:
            return FyersProvider(fy), ba.BROKER_FYERS
    if active == ba.BROKER_ZERODHA:
        kite = await ba.user_zerodha_kite(user_id, env_fallback=False)
        if kite:
            return ZerodhaProvider(kite), ba.BROKER_ZERODHA
    # Fallback within user's own vault only (when active code missing/incomplete).
    kite = await ba.user_zerodha_kite(user_id, env_fallback=False)
    if kite:
        return ZerodhaProvider(kite), ba.BROKER_ZERODHA
    fy = await ba.user_fyers_client(user_id)
    if fy:
        return FyersProvider(fy), ba.BROKER_FYERS
    return None, active


async def _resolve_platform_shared_provider() -> tuple[MarketDataProvider | None, str | None]:
    st = await ba.get_platform_shared_status()
    code = str(st.get("brokerCode") or "").strip().lower()
    if code == ba.BROKER_ZERODHA:
        kite = await ba.platform_shared_zerodha_kite()
        return (ZerodhaProvider(kite), code) if kite else (None, code or None)
    if code == ba.BROKER_FYERS:
        fy = await ba.platform_shared_fyers_client()
        return (FyersProvider(fy), code) if fy else (None, code or None)
    return None, code or None


async def resolve_broker_context(user_id: int, *, mode: str) -> ResolvedBrokerContext:
    role = (await ba.get_user_role(user_id)) or ""
    is_admin = role.upper() == "ADMIN"
    active = await ba.get_active_broker_code(user_id)

    own_provider, own_code = await _resolve_user_provider(user_id)
    if own_provider:
        source = "user_fyers" if own_code == ba.BROKER_FYERS else "user_zerodha"
        return ResolvedBrokerContext(
            broker_code=own_code,
            source=source,
            market_data=own_provider,
            execution=own_provider,
            active_broker=active,
            is_admin=is_admin,
        )

    # Shared provider is only for paper on non-admin users.
    if str(mode or "").upper() == "PAPER" and not is_admin:
        shared_provider, shared_code = await _resolve_platform_shared_provider()
        if shared_provider:
            return ResolvedBrokerContext(
                broker_code=shared_code,
                source="platform_shared",
                market_data=shared_provider,
                execution=shared_provider,
                active_broker=active,
                is_admin=is_admin,
            )
        if await ba.platform_shared_slot_configured():
            return ResolvedBrokerContext(
                broker_code=shared_code,
                source="platform_only_unavailable",
                market_data=None,
                execution=None,
                active_broker=active,
                is_admin=is_admin,
            )

    return ResolvedBrokerContext(
        broker_code=None,
        source="none",
        market_data=None,
        execution=None,
        active_broker=active,
        is_admin=is_admin,
    )
