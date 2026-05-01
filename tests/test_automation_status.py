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
        details={
            "preview": {
                "status": "blocked",
                "signals_seen": 1,
                "previews_created": 0,
                "previews_skipped": 1,
            },
            "submit": {
                "status": "completed",
                "order_intents_seen": 0,
                "submitted": 0,
                "skipped": 0,
                "rejected": 0,
            },
            "news": {
                "risk_assessment": {
                    "market_risk_level": "high",
                    "should_block_new_entries": True,
                    "blocking_reasons": ["market: tariff risk"],
                    "manual_review_symbols": ["SPY"],
                }
            },
        },
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

        with patch.multiple(
            "app.services.automation_status.settings",
            market_cycle_preview_enabled=True,
            market_cycle_news_enabled=False,
            max_auto_orders_per_cycle=100,
            max_auto_orders_per_day=500,
            max_open_positions=500,
            max_open_positions_per_symbol=100,
            max_contracts_per_order=1,
            max_estimated_premium_per_order="2500",
        ):
            result = get_automation_status(db)

        self.assertTrue(result.switches.scan_enabled)
        self.assertTrue(result.switches.reconcile_enabled)
        self.assertFalse(result.switches.news_enabled)
        self.assertFalse(result.trading_automation_enabled)
        self.assertTrue(result.auto_submit_requires_paper)
        self.assertTrue(result.paper_mode)
        self.assertEqual(result.max_auto_orders_per_cycle, 100)
        self.assertEqual(result.max_auto_orders_per_day, 500)
        self.assertEqual(result.max_open_positions, 500)
        self.assertEqual(result.max_open_positions_per_symbol, 100)
        self.assertEqual(result.max_contracts_per_order, 1)
        self.assertEqual(str(result.max_estimated_premium_per_order), "2500")
        self.assertEqual(result.operational_summary["effective_mode"], "previewing")
        self.assertNotIn(
            "news risk gate is blocking new entry previews",
            result.operational_summary["blockers"],
        )
        self.assertFalse(
            result.operational_summary["news_gate"]["should_block_new_entries"]
        )
        self.assertEqual(
            result.operational_summary["news_gate"]["blocking_reasons"],
            ["market: tariff risk"],
        )
        self.assertEqual(result.operational_summary["last_preview"]["signals_seen"], 1)
        readiness = result.operational_summary["paper_trading_readiness"]
        self.assertTrue(readiness["ready_after_switches"])
        self.assertFalse(readiness["ready_to_auto_submit_now"])
        self.assertEqual(readiness["submit_ready_strategies"], ["Momentum Scanner"])
        self.assertIn(
            "TRADING_AUTOMATION_ENABLED must be true to auto-submit paper orders",
            readiness["warnings"],
        )
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

    def test_get_automation_status_blocks_on_news_risk_when_news_enabled(self) -> None:
        db = FakeAutomationStatusSession(
            strategies=[build_strategy()],
            job_runs=[
                build_job_run("market_cycle"),
                None,
                None,
            ],
        )

        with patch.multiple(
            "app.services.automation_status.settings",
            market_cycle_preview_enabled=True,
            market_cycle_news_enabled=True,
            market_cycle_submit_enabled=True,
            trading_automation_enabled=True,
        ):
            result = get_automation_status(db)

        self.assertIn(
            "news risk gate is blocking new entry previews",
            result.operational_summary["blockers"],
        )
        self.assertTrue(
            result.operational_summary["news_gate"]["should_block_new_entries"]
        )
        self.assertFalse(
            result.operational_summary["paper_trading_readiness"][
                "ready_to_auto_submit_now"
            ]
        )


if __name__ == "__main__":
    unittest.main()
