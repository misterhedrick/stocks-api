from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from app.services.performance_review import get_paper_performance_review


class FakePerformanceSession:
    def __init__(
        self,
        rows: list[tuple],
        *,
        signal_rows: list[tuple] | None = None,
        diagnostic_rows: list[tuple] | None = None,
        job_run_rows: list[tuple] | None = None,
        strategy_rows: list[tuple] | None = None,
    ) -> None:
        self.result_sets = [
            rows,
            signal_rows or [],
            diagnostic_rows or [],
            strategy_rows or [],
            job_run_rows or [],
        ]

    def execute(self, _: object) -> list[tuple]:
        if not self.result_sets:
            return []
        return self.result_sets.pop(0)


def fill_row(
    *,
    filled_at: datetime,
    symbol: str = "SPY260501C00500000",
    side: str,
    quantity: str,
    price: str,
    strategy_id: uuid.UUID | None,
    strategy_name: str | None,
) -> tuple:
    return (
        uuid.uuid4(),
        filled_at,
        symbol,
        side,
        Decimal(quantity),
        Decimal(price),
        strategy_id,
        strategy_name,
        uuid.uuid4(),
    )


def rich_fill_row(
    *,
    filled_at: datetime,
    side: str,
    quantity: str,
    price: str,
    strategy_id: uuid.UUID,
    strategy_name: str,
    order_intent_id: uuid.UUID,
    signal_id: uuid.UUID | None,
) -> tuple:
    return (
        uuid.uuid4(),
        filled_at,
        "SPY260501C00500000",
        side,
        Decimal(quantity),
        Decimal(price),
        strategy_id,
        strategy_name,
        order_intent_id,
        side,
        "test rationale",
        {"quote": {"midpoint": "1.00"}},
        signal_id,
        "SPY" if signal_id is not None else None,
        "moving_average_setup" if signal_id is not None else None,
        "bullish" if signal_id is not None else None,
        Decimal("0.6400") if signal_id is not None else None,
        "signal rationale" if signal_id is not None else None,
        {"trigger": "bullish_cross", "strategy_type": "moving_average"}
        if signal_id is not None
        else {},
    )


def signal_row(
    *,
    signal_id: uuid.UUID,
    created_at: datetime,
    strategy_id: uuid.UUID,
    strategy_name: str,
    scanner_type: str,
    symbol: str = "SPY",
    status: str = "new",
    preview_attempts: int = 0,
    error_code: str | None = None,
    rejection_reasons: dict[str, int] | None = None,
) -> tuple:
    return (
        signal_id,
        created_at,
        strategy_id,
        strategy_name,
        {"scanner": {"type": scanner_type}},
        symbol,
        symbol,
        "moving_average_setup",
        "bullish",
        status,
        preview_attempts,
        error_code,
        rejection_reasons,
        {"strategy_type": scanner_type},
    )


def diagnostic_row(
    *,
    signal_id: uuid.UUID,
    created_at: datetime,
    strategy_id: uuid.UUID,
    strategy_name: str,
    scanner_type: str,
    symbol: str = "SPY",
    reason_counts: dict[str, int] | None = None,
) -> tuple:
    return (
        uuid.uuid4(),
        created_at,
        signal_id,
        strategy_id,
        strategy_name,
        symbol,
        scanner_type,
        scanner_type,
        100,
        reason_counts or {},
    )


def strategy_row(*, strategy_name: str, scanner_type: str) -> tuple:
    return (strategy_name, {"scanner": {"type": scanner_type}})


def job_run_row(*, no_signal_reasons: list[str]) -> tuple:
    return ("market_entry_cycle", {"scan": {"no_signal_reasons": no_signal_reasons}})


class PerformanceReviewTests(unittest.TestCase):
    def test_get_paper_performance_review_matches_fifo_round_trips(self) -> None:
        strategy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        db = FakePerformanceSession(
            [
                fill_row(
                    filled_at=now,
                    side="buy",
                    quantity="2",
                    price="1.00",
                    strategy_id=strategy_id,
                    strategy_name="Confirmed Trend",
                ),
                fill_row(
                    filled_at=now + timedelta(minutes=20),
                    side="sell",
                    quantity="1",
                    price="1.35",
                    strategy_id=strategy_id,
                    strategy_name="Confirmed Trend",
                ),
            ]
        )

        result = get_paper_performance_review(db)

        self.assertEqual(result.fills_seen, 2)
        self.assertEqual(result.matched_round_trips, 1)
        self.assertEqual(result.totals["realized_pnl"], "35")
        self.assertEqual(result.totals["winning_trades"], 1)
        self.assertEqual(result.totals["win_rate_percent"], "100")
        self.assertEqual(result.by_strategy[0]["strategy_name"], "Confirmed Trend")
        self.assertEqual(result.by_strategy[0]["realized_pnl"], "35")
        self.assertEqual(result.by_symbol[0]["symbol"], "SPY260501C00500000")
        self.assertEqual(result.by_symbol[0]["realized_pnl"], "35")
        self.assertEqual(
            result.by_symbol[0]["strategy_names"],
            ["Confirmed Trend"],
        )
        self.assertEqual(result.open_positions[0]["open_quantity"], "1")
        self.assertEqual(result.open_positions[0]["cost_basis"], "100")
        self.assertEqual(result.signal_summary["signals_seen"], 0)
        self.assertEqual(result.no_signal_summary["reasons_seen"], 0)
        self.assertEqual(result.option_selection_diagnostics["diagnostics_seen"], 0)
        self.assertEqual(result.rejected_preview_outcomes, [])

    def test_get_paper_performance_review_handles_losses(self) -> None:
        strategy_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        db = FakePerformanceSession(
            [
                fill_row(
                    filled_at=now,
                    side="buy",
                    quantity="1",
                    price="2.00",
                    strategy_id=strategy_id,
                    strategy_name="Momentum",
                ),
                fill_row(
                    filled_at=now + timedelta(minutes=10),
                    side="sell",
                    quantity="1",
                    price="1.50",
                    strategy_id=strategy_id,
                    strategy_name="Momentum",
                ),
            ]
        )

        result = get_paper_performance_review(db)

        self.assertEqual(result.totals["realized_pnl"], "-50")
        self.assertEqual(result.totals["losing_trades"], 1)
        self.assertEqual(result.totals["average_loss"], "-50")
        self.assertEqual(result.by_symbol[0]["losing_trades"], 1)
        self.assertEqual(result.open_positions, [])

    def test_get_paper_performance_review_reports_learning_context_and_unmatched_fills(self) -> None:
        strategy_id = uuid.uuid4()
        entry_order_intent_id = uuid.uuid4()
        exit_order_intent_id = uuid.uuid4()
        signal_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        db = FakePerformanceSession(
            [
                rich_fill_row(
                    filled_at=now,
                    side="buy",
                    quantity="1",
                    price="1.00",
                    strategy_id=strategy_id,
                    strategy_name="Context Strategy",
                    order_intent_id=entry_order_intent_id,
                    signal_id=signal_id,
                ),
                rich_fill_row(
                    filled_at=now + timedelta(minutes=5),
                    side="sell",
                    quantity="1",
                    price="1.20",
                    strategy_id=strategy_id,
                    strategy_name="Context Strategy",
                    order_intent_id=exit_order_intent_id,
                    signal_id=None,
                ),
                rich_fill_row(
                    filled_at=now + timedelta(minutes=10),
                    side="sell_short",
                    quantity="1",
                    price="0.90",
                    strategy_id=strategy_id,
                    strategy_name="Context Strategy",
                    order_intent_id=uuid.uuid4(),
                    signal_id=None,
                ),
            ]
        )

        result = get_paper_performance_review(db)

        self.assertEqual(result.matched_round_trips, 1)
        round_trip = result.recent_round_trips[0]
        self.assertEqual(
            round_trip["entry_context"]["signal"]["market_context"]["trigger"],
            "bullish_cross",
        )
        self.assertEqual(
            round_trip["entry_context"]["order_intent"]["preview"]["quote"]["midpoint"],
            "1.00",
        )
        self.assertEqual(len(result.unmatched_closing_fills), 1)
        self.assertEqual(
            result.unmatched_closing_fills[0]["reason"],
            "sell_short fill is not matched as a long-option exit",
        )

    def test_get_paper_performance_review_reports_signal_and_rejection_context(self) -> None:
        strategy_id = uuid.uuid4()
        entry_order_intent_id = uuid.uuid4()
        signal_id = uuid.uuid4()
        rejected_signal_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        db = FakePerformanceSession(
            [
                rich_fill_row(
                    filled_at=now + timedelta(minutes=10),
                    side="buy",
                    quantity="1",
                    price="1.00",
                    strategy_id=strategy_id,
                    strategy_name="Moving Average",
                    order_intent_id=entry_order_intent_id,
                    signal_id=signal_id,
                ),
                rich_fill_row(
                    filled_at=now + timedelta(minutes=30),
                    side="sell",
                    quantity="1",
                    price="1.30",
                    strategy_id=strategy_id,
                    strategy_name="Moving Average",
                    order_intent_id=uuid.uuid4(),
                    signal_id=None,
                ),
            ],
            signal_rows=[
                signal_row(
                    signal_id=rejected_signal_id,
                    created_at=now,
                    strategy_id=strategy_id,
                    strategy_name="Moving Average",
                    scanner_type="moving_average",
                    status="preview_rejected",
                    preview_attempts=3,
                    error_code="OptionContractNotFoundError",
                    rejection_reasons={"low_open_interest": 2},
                ),
                signal_row(
                    signal_id=signal_id,
                    created_at=now + timedelta(minutes=5),
                    strategy_id=strategy_id,
                    strategy_name="Moving Average",
                    scanner_type="moving_average",
                    status="submitted",
                ),
            ],
            diagnostic_rows=[
                diagnostic_row(
                    signal_id=rejected_signal_id,
                    created_at=now,
                    strategy_id=strategy_id,
                    strategy_name="Moving Average",
                    scanner_type="moving_average",
                    reason_counts={"low_open_interest": 2, "wide_spread": 1},
                )
            ],
            strategy_rows=[
                strategy_row(
                    strategy_name="Moving Average",
                    scanner_type="moving_average",
                )
            ],
            job_run_rows=[
                job_run_row(
                    no_signal_reasons=[
                        "Moving Average.SPY: moving average evaluator produced no signal",
                        "Moving Average.QQQ: no usable bars for moving average evaluator",
                        "Moving Average.AAPL: scanner does not include symbol AAPL",
                    ]
                )
            ],
        )

        result = get_paper_performance_review(db)

        self.assertEqual(result.signal_summary["signals_seen"], 2)
        self.assertEqual(result.signal_summary["by_status"]["preview_rejected"], 1)
        self.assertEqual(
            result.signal_summary["preview_rejection_reasons"]["low_open_interest"],
            2,
        )
        self.assertEqual(result.option_selection_diagnostics["diagnostics_seen"], 1)
        self.assertEqual(result.no_signal_summary["reasons_seen"], 2)
        self.assertNotIn(
            "scanner does not include symbol AAPL",
            result.no_signal_summary["top_reasons"],
        )
        self.assertEqual(
            result.no_signal_summary["by_scanner_type"][0]["scanner_type"],
            "moving_average",
        )
        self.assertEqual(
            result.no_signal_summary["by_scanner_type"][0]["reasons"][
                "moving average evaluator produced no signal"
            ],
            1,
        )
        self.assertEqual(
            result.option_selection_diagnostics["reason_counts"]["wide_spread"],
            1,
        )
        comparison = result.rejected_preview_outcomes[0]
        self.assertEqual(comparison["scanner_type"], "moving_average")
        self.assertEqual(comparison["symbol"], "SPY")
        self.assertEqual(comparison["rejected_signals"], 1)
        self.assertEqual(comparison["later_matched_round_trips"], 1)
        self.assertEqual(comparison["later_realized_pnl"], "30")
        self.assertEqual(comparison["later_win_rate_percent"], "100")


if __name__ == "__main__":
    unittest.main()
