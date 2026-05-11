from __future__ import annotations

import unittest
from collections.abc import Iterator
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.main import app

_AUTH = {"Authorization": f"Bearer {settings.admin_api_token}"}


class DailyPaperReviewRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_daily_paper_review_route_returns_service_result(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        service_result = {
            "review_date": "2026-05-11",
            "timezone": "America/New_York",
            "summary": {
                "job_runs": 3,
                "signals": 2,
                "order_intents": 1,
                "paper_review_snapshot_found": True,
            },
            "jobs": {},
            "signals": {},
            "previews": {},
            "orders": {},
            "fills": {},
            "option_selection_diagnostics": {},
            "trade_cases": {},
            "ai_reviews": {},
            "paper_review_snapshot": {"id": "snapshot-1"},
        }

        with patch(
            "app.api.routes.automation.build_daily_paper_review",
            return_value=service_result,
        ) as daily_review:
            response = client.get(
                "/api/v1/automation/daily-paper-review?date=2026-05-11&limit=250",
                headers=_AUTH,
            )

        assert response.status_code == 200
        assert response.json()["review_date"] == "2026-05-11"
        assert response.json()["summary"]["paper_review_snapshot_found"] is True
        daily_review.assert_called_once()
        _, kwargs = daily_review.call_args
        assert kwargs["review_date"].isoformat() == "2026-05-11"
        assert kwargs["limit"] == 250

    def test_daily_paper_review_route_requires_auth(self) -> None:
        client = TestClient(app)

        response = client.get("/api/v1/automation/daily-paper-review")

        assert response.status_code == 401


if __name__ == "__main__":
    unittest.main()
