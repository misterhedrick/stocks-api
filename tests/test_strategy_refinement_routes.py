from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.main import app

_AUTH = {"Authorization": f"Bearer {settings.admin_api_token}"}


class StrategyRefinementRouteTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_strategy_refinement_route_returns_summary(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        result = {
            "snapshot_count": 2,
            "summary": {"needs_option_filter_review": 1},
            "candidates": [{"scanner_type": "moving_average", "symbol": "SPY"}],
            "human_review_only": True,
            "auto_apply": False,
        }

        with patch(
            "app.api.routes.automation.build_strategy_refinement_summary",
            return_value=result,
        ) as service:
            response = client.get(
                "/api/v1/automation/strategy-refinement?days=5&min_closed_trade_cases=3&min_rejected_previews=7&min_no_signal_reasons=11&limit=25",
                headers=_AUTH,
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["auto_apply"])
        service.assert_called_once_with(
            db,
            days=5,
            min_closed_trade_cases=3,
            min_rejected_previews=7,
            min_no_signal_reasons=11,
            limit=25,
        )

    def test_strategy_tuning_decision_routes_create_and_update(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        decision_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        decision = SimpleNamespace(
            id=decision_id,
            strategy_id=None,
            scanner_type="moving_average",
            symbol="SPY",
            decision_type="tighten_spread_filter",
            status="approved",
            description="Review spread limits.",
            expected_effect="Fewer wide-spread fills.",
            proposed_config_patch={"preview": {"max_spread_percent": "25"}},
            evidence_snapshot_ids=["snapshot-1"],
            evidence_summary={"closed_trade_cases": 5},
            outcome_summary={},
            created_by="admin",
            created_at=now,
            updated_at=now,
        )

        with patch(
            "app.api.routes.automation.create_strategy_tuning_decision",
            return_value=SimpleNamespace(decision=decision),
        ) as creator:
            response = client.post(
                "/api/v1/automation/strategy-tuning-decisions",
                headers=_AUTH,
                json={
                    "scanner_type": "moving_average",
                    "symbol": "SPY",
                    "decision_type": "tighten_spread_filter",
                    "description": "Review spread limits.",
                    "expected_effect": "Fewer wide-spread fills.",
                    "proposed_config_patch": {"preview": {"max_spread_percent": "25"}},
                    "evidence_snapshot_ids": ["snapshot-1"],
                    "evidence_summary": {"closed_trade_cases": 5},
                    "created_by": "admin",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()["auto_apply"])
        creator.assert_called_once()

        decision.status = "applied"
        decision.outcome_summary = {"after_snapshot_count": 3}
        with patch(
            "app.api.routes.automation.update_strategy_tuning_decision",
            return_value=SimpleNamespace(decision=decision),
        ) as updater:
            response = client.patch(
                f"/api/v1/automation/strategy-tuning-decisions/{decision_id}",
                headers=_AUTH,
                json={"status": "applied", "outcome_summary": {"after_snapshot_count": 3}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "applied")
        updater.assert_called_once()

    def test_strategy_tuning_decisions_route_returns_list(self) -> None:
        db = object()

        def override_db() -> Iterator[object]:
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)

        with patch(
            "app.api.routes.automation.get_strategy_tuning_decisions",
            return_value=[{"id": str(uuid.uuid4()), "auto_apply": False}],
        ) as service:
            response = client.get(
                "/api/v1/automation/strategy-tuning-decisions?status=approved&scanner_type=moving_average&symbol=SPY&limit=5",
                headers=_AUTH,
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()[0]["auto_apply"])
        service.assert_called_once_with(
            db,
            status="approved",
            scanner_type="moving_average",
            symbol="SPY",
            limit=5,
        )


if __name__ == "__main__":
    unittest.main()
