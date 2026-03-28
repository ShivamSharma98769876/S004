"""RSS headline fetch + lightweight lexicon sentiment for landing context (display-first; trade wiring later)."""

from __future__ import annotations

import asyncio
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")

# Default mix: India markets + global business/markets + commodities/energy (FII/crude/macro → India).
# Override entirely with env NEWS_RSS_URLS (comma-separated).
_DEFAULT_FEEDS = (
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/business.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.theguardian.com/business/rss",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://oilprice.com/rss/main",
    "https://www.investing.com/rss/news_285.rss",
)

# If all primary feeds fail or return no items, try these (unless NEWS_RSS_URLS is set).
_FALLBACK_FEEDS = (
    "https://www.ft.com/markets?format=rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
}

_POS = frozenset(
    """
    rally surge gain gains bullish growth beat upgrade optimism strong rebound recovery
    high record profit profits rise rising jumped jump soar soared green up upside
    expansion hiring acquisition wins win breakthrough supportive ease easing stimulus
    """.split()
)
_NEG = frozenset(
    """
    slump crash fear downgrade loss losses bearish concern decline weak selloff sell-off
    fall falling drop dropped plunge plunged red down downside recession layoff debt
    default crisis warning warns warn cut cuts tariff sanctions strike inflation shock
    """.split()
)

_cache_lock = asyncio.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_mono: float = 0.0


def news_sentiment_failure_payload(exc: BaseException, *, ttl: float | None = None) -> dict[str, Any]:
    """Safe payload when RSS fetch/scoring fails so the landing API still returns 200."""
    try:
        cache_ttl = int(ttl if ttl is not None else _cache_ttl_sec())
    except Exception:
        cache_ttl = 480
    return {
        "aggregateLabel": "NEUTRAL",
        "aggregateScore": 0.0,
        "headlineCount": 0,
        "items": [],
        "feedsQueried": _feed_urls(),
        "feedErrors": [f"{type(exc).__name__}: {exc}"],
        "cached": False,
        "cacheTtlSec": cache_ttl,
        "methodologyVersion": "lexicon-rss-v2",
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _child_text(parent: ET.Element, name: str) -> str:
    for ch in parent:
        if _local_tag(ch.tag) == name:
            t = (ch.text or "").strip()
            if t:
                return t
    return ""


def _parse_rss_items(xml_bytes: bytes, max_items: int) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out: list[dict[str, str]] = []
    for el in root.iter():
        if _local_tag(el.tag) != "item":
            continue
        title = _child_text(el, "title")
        link = _child_text(el, "link")
        pub = _child_text(el, "pubDate") or _child_text(el, "published")
        if not title:
            continue
        out.append({"title": title, "link": link, "pubDate": pub})
        if len(out) >= max_items:
            break
    return out


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z\-']+", text.lower())


def _score_text(text: str) -> float:
    toks = _tokenize(text)
    if not toks:
        return 0.0
    pos = sum(1 for t in toks if t in _POS)
    neg = sum(1 for t in toks if t in _NEG)
    # small bounded signal in [-1, 1]
    raw = (pos - neg) / max(len(toks), 8)
    return max(-1.0, min(1.0, raw * 4.0))


def _label_from_score(avg: float) -> str:
    if avg > 0.12:
        return "POSITIVE"
    if avg < -0.12:
        return "NEGATIVE"
    return "NEUTRAL"


def _iso_from_pub(pub: str) -> str | None:
    if not pub:
        return None
    try:
        dt = parsedate_to_datetime(pub.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _feed_urls() -> list[str]:
    raw = (os.environ.get("NEWS_RSS_URLS") or "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    return list(_DEFAULT_FEEDS)


def _cache_ttl_sec() -> float:
    try:
        return max(60.0, float(os.environ.get("NEWS_SENTIMENT_CACHE_SEC") or "480"))
    except ValueError:
        return 480.0


def _max_items_total() -> int:
    try:
        return max(4, min(40, int(os.environ.get("NEWS_SENTIMENT_MAX_ITEMS") or "18")))
    except ValueError:
        return 18


async def _fetch_one_feed(client: httpx.AsyncClient, url: str, per_feed: int) -> list[dict[str, str]]:
    r = await client.get(url, follow_redirects=True)
    r.raise_for_status()
    return _parse_rss_items(r.content, per_feed)


async def compute_news_sentiment_snapshot(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    Returns camelCase JSON-friendly dict for API.
    methodologyVersion bumps when scoring rules change.
    """
    global _cache_payload, _cache_mono
    ttl = _cache_ttl_sec()
    now_m = time.monotonic()
    async with _cache_lock:
        if (
            not force_refresh
            and _cache_payload is not None
            and (now_m - _cache_mono) < ttl
        ):
            return dict(_cache_payload)

    max_total = _max_items_total()
    urls = _feed_urls()
    per_feed = max(2, max_total // max(1, len(urls)))

    items_raw: list[dict[str, str]] = []
    feed_errors: list[str] = []
    timeout = httpx.Timeout(12.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, headers=_HTTP_HEADERS) as client:
        for url in urls:
            try:
                chunk = await _fetch_one_feed(client, url, per_feed)
                for it in chunk:
                    it["_feed"] = url
                items_raw.extend(chunk)
            except Exception as ex:  # noqa: BLE001 — aggregate errors for UI
                msg = f"{type(ex).__name__}: {ex}"
                feed_errors.append(f"{url[:48]}… {msg}" if len(url) > 48 else f"{url}: {msg}")

        # Built-in fallbacks when defaults yield nothing (e.g. regional blocking).
        if not items_raw and not (os.environ.get("NEWS_RSS_URLS") or "").strip():
            for url in _FALLBACK_FEEDS:
                if url in urls:
                    continue
                try:
                    chunk = await _fetch_one_feed(client, url, per_feed)
                    for it in chunk:
                        it["_feed"] = url
                    items_raw.extend(chunk)
                    if items_raw:
                        break
                except Exception as ex:  # noqa: BLE001
                    msg = f"{type(ex).__name__}: {ex}"
                    feed_errors.append(f"fallback {url[:40]}… {msg}" if len(url) > 40 else f"fallback {url}: {msg}")

    # de-dupe by title
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for it in items_raw:
        key = (it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(it)
        if len(deduped) >= max_total:
            break

    scored: list[dict[str, Any]] = []
    scores: list[float] = []
    for it in deduped:
        title = it.get("title") or ""
        s = _score_text(title)
        scores.append(s)
        scored.append(
            {
                "title": title[:220],
                "link": (it.get("link") or "")[:500] or None,
                "publishedAt": _iso_from_pub(it.get("pubDate") or ""),
                "itemSentiment": _label_from_score(s),
                "itemScore": round(s, 4),
            }
        )

    avg = sum(scores) / len(scores) if scores else 0.0
    label = _label_from_score(avg)
    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    payload: dict[str, Any] = {
        "aggregateLabel": label,
        "aggregateScore": round(avg, 4),
        "headlineCount": len(scored),
        "items": scored[:max_total],
        "feedsQueried": urls,
        "feedErrors": feed_errors[:5],
        "cached": False,
        "cacheTtlSec": int(ttl),
        "methodologyVersion": "lexicon-rss-v2",
        "updatedAt": updated_at,
    }

    async with _cache_lock:
        _cache_payload = dict(payload)
        _cache_payload["cached"] = True
        _cache_mono = time.monotonic()

    payload["cached"] = False
    return payload
