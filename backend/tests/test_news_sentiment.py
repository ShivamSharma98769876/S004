"""Unit tests for RSS parse + lexicon scoring (no live network)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import news_sentiment as ns


def test_score_text_bullish() -> None:
    s = ns._score_text("NIFTY rally on strong gains and optimism")
    assert s > 0


def test_score_text_bearish() -> None:
    s = ns._score_text("Slump on crash fears and broad decline")
    assert s < 0


def test_parse_rss_items() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
    <item><title>First headline</title><link>https://a.test/1</link><pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate></item>
    <item><title>Second</title><link>https://a.test/2</link></item>
    </channel></rss>"""
    items = ns._parse_rss_items(xml, 10)
    assert len(items) == 2
    assert items[0]["title"] == "First headline"


@pytest.fixture(autouse=True)
def reset_news_cache() -> None:
    ns._cache_payload = None
    ns._cache_mono = 0.0
    yield
    ns._cache_payload = None
    ns._cache_mono = 0.0


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        xml = f"""<?xml version="1.0"?><rss><channel>
        <item><title>Rally on strong gains</title><link>https://t.test</link></item>
        <item><title>Neutral market update</title><link>https://t.test/2</link></item>
        </channel></rss>""".encode()
        return _FakeResponse(xml)


def test_compute_news_sentiment_snapshot_mocked() -> None:
    with patch.object(ns.httpx, "AsyncClient", _FakeAsyncClient):
        out = asyncio.run(ns.compute_news_sentiment_snapshot(force_refresh=True))
    assert out["headlineCount"] >= 1
    assert out["aggregateLabel"] in ("POSITIVE", "NEGATIVE", "NEUTRAL")
    assert out["methodologyVersion"] == "lexicon-rss-v1"
    assert isinstance(out["items"], list)
