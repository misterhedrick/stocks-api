from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.models import AuditLog, JobRun
from app.services.news_scanner import NewsItem, scan_market_news


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
        self.assertEqual(result.sources_checked, 2)
        self.assertEqual(result.market_items[0]["impact_keywords"], ["federal reserve", "rate"])
        self.assertEqual(result.ticker_items["SPY"][0]["impact_keywords"], ["earnings", "volatility"])
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "news_scan.succeeded")


if __name__ == "__main__":
    unittest.main()
