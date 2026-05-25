from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from app.db.models import OrderIntent, Signal, Strategy
from app.services.entry_quality import (
    entry_preview_delay_reason,
    evaluate_entry_quality,
)


class FakeEntryQualitySession:
    def __init__(self, *, exit_intents: list[OrderIntent] | None = None) -> None:
        self.exit_intents = exit_intents or []

    def scalars(self, _: object) -> list[OrderIntent]:
        return self.exit_intents

    def scalar(self, _: object) -> int:
        return 0


def build_strategy(scanner_type: str) -> Strategy:
    now = datetime.now(timezone.utc)
    return Strategy(
        id=uuid.uuid4(),
        name=f"{scanner_type} strategy",
        description="Test",
        is_active=True,
        config={"scanner": {"type": scanner_type}},
        created_at=now,
        updated_at=now,
    )


def build_signal(
    strategy: Strategy,
    *,
    confidence: str = "0.6500",
    direction: str = "bullish",
    market_context: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> Signal:
    now = created_at or datetime.now(timezone.utc)
    return Signal(
        id=uuid.uuid4(),
        strategy_id=strategy.id,
        symbol="SPY",
        underlying_symbol="SPY",
        signal_type="test_signal",
        direction=direction,
        confidence=Decimal(confidence),
        rationale="test",
        market_context=market_context or {},
        status="new",
        preview_attempts=0,
        created_at=now,
        updated_at=now,
    )


def build_order_intent(signal: Signal, *, spread_percent: str = "8") -> OrderIntent:
    return OrderIntent(
        id=uuid.uuid4(),
        strategy_id=signal.strategy_id,
        signal_id=signal.id,
        underlying_symbol=signal.underlying_symbol or signal.symbol,
        option_symbol="SPY260417C00500000",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.25"),
        time_in_force="day",
        status="previewed",
        preview={
            "quote": {
                "bid": "1.20",
                "ask": "1.30",
                "spread_percent": spread_percent,
            },
            "selection": {
                "selected_contract": {
                    "open_interest": 500,
                    "dte": 7,
                }
            },
        },
    )


class EntryQualityTests(unittest.TestCase):
    def test_delays_fast_scanner_until_one_bar_confirms(self) -> None:
        strategy = build_strategy("vwap_reclaim")
        signal = build_signal(
            strategy,
            market_context={"timeframe": "5Min"},
            created_at=datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc),
        )

        reason = entry_preview_delay_reason(
            signal,
            strategy,
            now=datetime(2026, 5, 20, 14, 3, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(reason)
        self.assertIn("confirmation pending", reason or "")

    def test_allows_fast_scanner_after_one_bar(self) -> None:
        strategy = build_strategy("vwap_reclaim")
        signal = build_signal(
            strategy,
            market_context={"timeframe": "5Min"},
            created_at=datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc),
        )

        reason = entry_preview_delay_reason(
            signal,
            strategy,
            now=datetime(2026, 5, 20, 14, 5, tzinfo=timezone.utc),
        )

        self.assertIsNone(reason)

    def test_blocks_market_regime_as_standalone_entry(self) -> None:
        strategy = build_strategy("market_regime_filter")
        signal = build_signal(strategy, confidence="0.9000")
        decision = evaluate_entry_quality(
            FakeEntryQualitySession(),
            order_intent=build_order_intent(signal),
            strategy=strategy,
            signal=signal,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("signal-only", "; ".join(decision.reasons))

    def test_blocks_marginal_momentum_signal(self) -> None:
        strategy = build_strategy("momentum_rate_of_change")
        signal = build_signal(
            strategy,
            confidence="0.7000",
            direction="bearish",
            market_context={"percent_change": "-0.3513"},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        decision = evaluate_entry_quality(
            FakeEntryQualitySession(),
            order_intent=build_order_intent(signal),
            strategy=strategy,
            signal=signal,
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(
            any("too close to threshold" in reason for reason in decision.reasons)
        )

    def test_allows_strong_momentum_with_good_option(self) -> None:
        strategy = build_strategy("momentum_rate_of_change")
        signal = build_signal(
            strategy,
            confidence="0.7000",
            direction="bearish",
            market_context={"percent_change": "-1.00"},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        decision = evaluate_entry_quality(
            FakeEntryQualitySession(),
            order_intent=build_order_intent(signal),
            strategy=strategy,
            signal=signal,
        )

        self.assertTrue(decision.allowed, decision.reasons)

    def test_blocks_wide_option_spread(self) -> None:
        strategy = build_strategy("momentum_rate_of_change")
        signal = build_signal(
            strategy,
            confidence="0.7000",
            direction="bearish",
            market_context={"percent_change": "-0.80"},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        decision = evaluate_entry_quality(
            FakeEntryQualitySession(),
            order_intent=build_order_intent(signal, spread_percent="40"),
            strategy=strategy,
            signal=signal,
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(
            any("spread percent" in reason for reason in decision.reasons)
        )

    def test_blocks_recent_intraday_stop_loss_before_trade_cases_exist(self) -> None:
        strategy = build_strategy("momentum_rate_of_change")
        signal = build_signal(
            strategy,
            confidence="0.9000",
            direction="bearish",
            market_context={"percent_change": "-1.20"},
            created_at=datetime(2026, 5, 22, 15, 10, tzinfo=timezone.utc),
        )
        exit_intent = OrderIntent(
            id=uuid.uuid4(),
            strategy_id=strategy.id,
            signal_id=None,
            underlying_symbol="SPY",
            option_symbol="SPY260522P00500000",
            side="sell",
            quantity=1,
            order_type="limit",
            limit_price=Decimal("1.10"),
            time_in_force="day",
            status="filled",
            rationale="Exit SPY260522P00500000: stop_loss_percent triggered at -12%",
            preview={
                "source": "position_exit_evaluator",
                "trigger_reason": "stop_loss_percent triggered at -12%",
            },
            created_at=datetime(2026, 5, 22, 15, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 22, 15, 0, tzinfo=timezone.utc),
        )

        decision = evaluate_entry_quality(
            FakeEntryQualitySession(exit_intents=[exit_intent]),
            order_intent=build_order_intent(signal),
            strategy=strategy,
            signal=signal,
            now=datetime(2026, 5, 22, 15, 30, tzinfo=timezone.utc),
        )

        self.assertFalse(decision.allowed)
        self.assertIn("stop-loss cooldown", "; ".join(decision.reasons))


if __name__ == "__main__":
    unittest.main()
