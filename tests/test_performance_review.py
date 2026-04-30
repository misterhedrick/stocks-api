from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from app.services.performance_review import get_paper_performance_review


class FakePerformanceSession:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def execute(self, _: object) -> list[tuple]:
        return self.rows


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
        "moving_average_setup" if signal_id is not None else None,
        "bullish" if signal_id is not None else None,
        Decimal("0.6400") if signal_id is not None else None,
        "signal rationale" if signal_id is not None else None,
        {"trigger": "bullish_cross"} if signal_id is not None else {},
    )


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


if __name__ == "__main__":
    unittest.main()
