from __future__ import annotations

import unittest
from collections.abc import Iterator
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.main import app

_AUTH = {"Authorization": f"Bearer {settings.admin_api_token}"}


class OptionSelectionDiagnosticsRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_option_selection_diagnostics_summary_route_returns_service_result(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        service_result = {
            "review_date": "2026-05-11",
            "timezone": "America/New_York",
            "total": 2,
            "reason_counts": {"spread_too_wide": 3},
            "by_symbol": {"SPY": {"diagnostic_count": 2}},
            "groups": [],
        }

        with patch(
            "app.api.routes.automation_diagnostics.build_option_selection_diagnostics_summary",
            return_value=service_result,
        ) as diagnostics_summary:
            response = client.get(
                "/api/v1/automation/option-selection-diagnostics/summary?date=2026-05-11&limit=250",
                headers=_AUTH,
            )

        assert response.status_code == 200
        assert response.json()["review_date"] == "2026-05-11"
        assert response.json()["reason_counts"]["spread_too_wide"] == 3
        diagnostics_summary.assert_called_once()
        _, kwargs = diagnostics_summary.call_args
        assert kwargs["review_date"].isoformat() == "2026-05-11"
        assert kwargs["limit"] == 250

    def test_option_selection_diagnostics_summary_route_requires_auth(self) -> None:
        client = TestClient(app)

        response = client.get("/api/v1/automation/option-selection-diagnostics/summary")

        assert response.status_code == 401


if __name__ == "__main__":
    unittest.main()
