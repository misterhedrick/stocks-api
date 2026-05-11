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

    def test_phase1_readiness_route_returns_service_result(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        service_result = {
            "ready": True,
            "mode": "paper_auto_submit",
            "blockers": [],
            "warnings": [],
            "safety": {"paper_mode": True},
            "risk_caps": {"max_auto_orders_per_cycle": 5},
            "active_strategy_count": 2,
            "latest_jobs": {},
            "latest_paper_review_snapshot": None,
            "recent_trade_case_count": 3,
        }

        with patch(
            "app.api.routes.automation_diagnostics.build_phase1_readiness",
            return_value=service_result,
        ) as phase1_readiness:
            response = client.get("/api/v1/automation/phase-1-readiness", headers=_AUTH)

        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["mode"] == "paper_auto_submit"
        assert response.json()["active_strategy_count"] == 2
        phase1_readiness.assert_called_once()

    def test_phase1_readiness_route_requires_auth(self) -> None:
        client = TestClient(app)

        response = client.get("/api/v1/automation/phase-1-readiness")

        assert response.status_code == 401

    def test_retention_report_route_returns_service_result(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        service_result = {
            "generated_at": "2026-05-11T12:00:00+00:00",
            "mode": "report_only",
            "cutoffs": {
                "job_runs_before": "2026-04-11T12:00:00+00:00",
                "audit_logs_before": "2026-03-12T12:00:00+00:00",
                "rejected_signals_before": "2026-04-11T12:00:00+00:00",
                "option_diagnostics_before": "2026-03-12T12:00:00+00:00",
            },
            "eligible_counts": {
                "successful_job_runs": 10,
                "audit_logs": 250,
                "option_selection_diagnostics": 500,
                "rejected_signals_without_order_intents": 75,
            },
            "always_preserved": ["broker_orders", "fills", "trade_cases"],
        }

        with patch(
            "app.api.routes.automation_diagnostics.build_retention_report",
            return_value=service_result,
        ) as retention_report:
            response = client.get("/api/v1/automation/retention-report", headers=_AUTH)

        assert response.status_code == 200
        assert response.json()["mode"] == "report_only"
        assert response.json()["eligible_counts"]["successful_job_runs"] == 10
        assert response.json()["eligible_counts"]["audit_logs"] == 250
        retention_report.assert_called_once()

    def test_retention_report_route_requires_auth(self) -> None:
        client = TestClient(app)

        response = client.get("/api/v1/automation/retention-report")

        assert response.status_code == 401


if __name__ == "__main__":
    unittest.main()
