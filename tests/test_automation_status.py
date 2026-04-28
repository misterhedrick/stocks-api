from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.db.models import JobRun, Strategy
from app.services.automation_status import get_automation_status


class FakeScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def __iter__(self):
        return iter(self.values)


class FakeAutomationStatusSession:
    def __init__(
        self,
        *,
        strategies: list[Strategy] | None = None,
        job_runs: list[JobRun | None] | None = None,
    ) -> None:
        self.strategies = strategies or []
        self.job_runs = job_runs or []

    def scalars(self, _: object) -> FakeScalarResult:
        return FakeScalarResult(self.strategies)

    def scalar(self, _: object) -> JobRun | None:
        if self.job_runs:
            return self.job_runs.pop(0)
        return None


def build_strategy() -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name="Momentum Scanner",
        description="Test strategy",
        is_active=True,
        config={
            "scanner": {
                "type": "percent_change",
                "symbols": ["spy", " QQQ "],
                "preview": {"enabled": True},
                "exit": {
                    "enabled": True,
                    "profit_target_percent": "30",
                    "submit": {
                        "enabled": True,
                        "allowed_sides": ["sell"],
                    },
                },
                "submit": {
                    "enabled": True,
                    "max_orders_per_cycle": 1,
                    "max_orders_per_trading_day": 3,
                    "allowed_sides": ["buy"],
                    "ignored_setting": "not returned",
                },
            }
        },
        created_at=now,
        updated_at=now,
    )


def build_job_run(job_name: str) -> JobRun:
    now = datetime.now(timezone.utc)
    return JobRun(
        id=uuid.uuid4(),
        job_name=job_name,
        status="succeeded",
        started_at=now,
        finished_at=now,
        details={"status": "ok"},
        error=None,
        created_at=now,
    )


class AutomationStatusTests(unittest.TestCase):
    def test_get_automation_status_summarizes_switches_strategies_and_jobs(self) -> None:
        db = FakeAutomationStatusSession(
            strategies=[build_strategy()],
            job_runs=[
                build_job_run("market_cycle"),
                build_job_run("scan_signals"),
                None,
            ],
        )

        with patch(
            "app.services.automation_status.settings.market_cycle_news_enabled",
            False,
        ):
            result = get_automation_status(db)

        self.assertTrue(result.switches.scan_enabled)
        self.assertTrue(result.switches.reconcile_enabled)
        self.assertFalse(result.switches.news_enabled)
        self.assertFalse(result.trading_automation_enabled)
        self.assertTrue(result.auto_submit_requires_paper)
        self.assertTrue(result.paper_mode)
        self.assertEqual(result.max_auto_orders_per_cycle, 1)
        self.assertEqual(result.max_auto_orders_per_day, 3)
        self.assertEqual(result.max_open_positions, 3)
        self.assertEqual(result.max_open_positions_per_symbol, 1)
        self.assertEqual(result.max_contracts_per_order, 1)
        self.assertEqual(str(result.max_estimated_premium_per_order), "250")
        self.assertEqual(len(result.active_strategies), 1)
        strategy = result.active_strategies[0]
        self.assertEqual(strategy.scanner_type, "percent_change")
        self.assertEqual(strategy.scanner_symbols, ["SPY", "QQQ"])
        self.assertTrue(strategy.preview_enabled)
        self.assertTrue(strategy.exit_enabled)
        self.assertTrue(strategy.submit_enabled)
        self.assertEqual(strategy.exit_limits["profit_target_percent"], "30")
        self.assertEqual(strategy.exit_limits["submit"]["allowed_sides"], ["sell"])
        self.assertEqual(strategy.submit_limits["max_orders_per_cycle"], 1)
        self.assertNotIn("ignored_setting", strategy.submit_limits)
        self.assertEqual(result.latest_job_runs["market_cycle"].job_name, "market_cycle")
        self.assertEqual(result.latest_job_runs["scan_signals"].job_name, "scan_signals")
        self.assertIsNone(result.latest_job_runs["reconcile_broker"])


if __name__ == "__main__":
    unittest.main()
