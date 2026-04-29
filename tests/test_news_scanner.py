from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import AuditLog, JobRun
from app.services.news_scanner import NewsItem, assess_news_risk, scan_market_news


class FakeScalarResult:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeNewsSession:
    def __init__(self, *, owned_symbols: list[str]) -> None:
        self.owned_symbols = owned_symbols
        self.added: list[object] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flush_count += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if isinstance(obj, JobRun) and getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    def scalars(self, _: object) -> FakeScalarResult:
        return FakeScalarResult(self.owned_symbols)


class FakeNewsClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def fetch(self, url: str, *, limit: int) -> list[NewsItem]:
        self.urls.append(url)
        if "SPY" in url:
            return [
                NewsItem(
                    title="SPY earnings volatility rises",
                    url="https://example.test/spy",
                    source="Example",
                    published_at=None,
                    impact_keywords=["earnings", "volatility"],
                )
            ][:limit]
        return [
            NewsItem(
                title="Federal Reserve rate decision moves market",
                url="https://example.test/fed",
                source="Example",
                published_at=None,
                impact_keywords=["federal reserve", "rate"],
            )
        ][:limit]


class NewsScannerTests(unittest.TestCase):
    def test_scan_market_news_records_market_and_owned_ticker_items(self) -> None:
        db = FakeNewsSession(owned_symbols=["SPY260429C00500000"])
        client = FakeNewsClient()

        result = scan_market_news(
            db,
            market_limit=3,
            ticker_limit=2,
            client=client,
        )

        self.assertEqual(result.job_run.status, "succeeded")
        self.assertEqual(result.owned_symbols, ["SPY"])
        self.assertEqual(result.sources_checked, 6)
        self.assertEqual(result.market_items[0]["impact_keywords"], ["federal reserve", "rate"])
        self.assertEqual(result.ticker_items["SPY"][0]["impact_keywords"], ["earnings", "volatility"])
        self.assertEqual(result.risk_assessment["market_risk_level"], "medium")
        self.assertEqual(result.risk_assessment["ticker_risks"]["SPY"]["risk_level"], "high")
        self.assertFalse(result.risk_assessment["should_block_new_entries"])
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "news_scan.succeeded")

    def test_assess_news_risk_ignores_stale_low_quality_ticker_noise(self) -> None:
        stale_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

        risk = assess_news_risk(
            market_items=[],
            ticker_items={
                "SPY": [
                    {
                        "title": "War ALWAYS REVEALS In The Stock Market - SPY QQQ Options ES NQ Swing & Day Trading",
                        "url": "https://example.test/stale",
                        "source": "Fathom Journal",
                        "published_at": stale_date,
                        "impact_keywords": ["war"],
                    },
                    {
                        "title": "SPY 260427 710.00P Stock Options Chain | Quotes & News",
                        "url": "https://example.test/chain",
                        "source": "Moomoo",
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "impact_keywords": ["volatility"],
                    },
                ]
            },
        )

        self.assertEqual(risk["version"], "news_risk_v2")
        self.assertFalse(risk["should_block_new_entries"])
        self.assertEqual(risk["ticker_risks"]["SPY"]["risk_level"], "low")
        self.assertEqual(risk["manual_review_symbols"], [])
        self.assertGreaterEqual(len(risk["ignored_items"]), 2)

    def test_assess_news_risk_blocks_only_on_multiple_fresh_market_high_risk_items(self) -> None:
        fresh_date = datetime.now(timezone.utc).isoformat()

        one_headline = assess_news_risk(
            market_items=[
                {
                    "title": "Oil volatility rises as market waits",
                    "url": "https://example.test/oil",
                    "source": "Yahoo Finance",
                    "published_at": fresh_date,
                    "impact_keywords": ["oil", "volatility"],
                }
            ],
            ticker_items={},
        )
        two_headlines = assess_news_risk(
            market_items=[
                {
                    "title": "Oil volatility rises as market waits",
                    "url": "https://example.test/oil",
                    "source": "Yahoo Finance",
                    "published_at": fresh_date,
                    "impact_keywords": ["oil", "volatility"],
                },
                {
                    "title": "Markets slide on tariff risk",
                    "url": "https://example.test/tariff",
                    "source": "CNBC",
                    "published_at": fresh_date,
                    "impact_keywords": ["tariff"],
                },
            ],
            ticker_items={},
        )

        self.assertFalse(one_headline["should_block_new_entries"])
        self.assertTrue(two_headlines["should_block_new_entries"])
        self.assertEqual(len(two_headlines["blocking_reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
