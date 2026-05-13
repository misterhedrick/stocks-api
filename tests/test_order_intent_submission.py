from __future__ import annotations

import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.order_intents import create_order_intent, preview_order_intent
from app.db.models import AuditLog, BrokerOrder, OptionSelectionDiagnostic, OrderIntent, Signal, Strategy
from app.integrations.alpaca import (
    AlpacaLatestOptionQuote,
    AlpacaOrderCancellation,
    AlpacaOptionContract,
    AlpacaOptionContractsPage,
    AlpacaOrderRejectedError,
    AlpacaOrderSubmission,
    AlpacaOptionQuote,
    AlpacaSubmittedOrder,
)
from app.schemas.order_intents import OrderIntentCreate, OrderIntentPreviewCreate
from app.schemas.options import OptionContractSelectionCreate
from app.services.order_intents import (
    BrokerOrderNotFoundError,
    OrderIntentPreviewError,
    SignalNotFoundError,
    cancel_order_intent,
    preview_order_intent_from_signal,
    submit_order_intent,
)
from app.services.option_contracts import OptionContractNotFoundError


class FakeSession:
    def __init__(
        self,
        order_intent: OrderIntent | None,
        signal: Signal | None = None,
        strategy: Strategy | None = None,
        broker_order: BrokerOrder | None = None,
    ) -> None:
        self.order_intent = order_intent
        self.signal = signal
        self.strategy = strategy
        self.broker_order = broker_order
        self.added: list[object] = []
        self.commit_count = 0
        self.flush_count = 0

    def get(self, model: type, record_id: uuid.UUID) -> object | None:
        if model is OrderIntent:
            if self.order_intent is None or self.order_intent.id != record_id:
                return None
            return self.order_intent
        if model is Signal:
            if self.signal is None or self.signal.id != record_id:
                return None
            return self.signal
        if model is Strategy:
            if self.strategy is None or self.strategy.id != record_id:
                return None
            return self.strategy
        return None

    def scalar(self, _: object) -> object | None:
        return self.broker_order

    def add(self, obj: object) -> None:
        self.added.append(obj)
        if isinstance(obj, OrderIntent):
            self.order_intent = obj
        if isinstance(obj, BrokerOrder):
            self.broker_order = obj

    def commit(self) -> None:
        self.commit_count += 1

    def flush(self) -> None:
        self.flush_count += 1
        if self.order_intent is not None and getattr(self.order_intent, "id", None) is None:
            self.order_intent.id = uuid.uuid4()

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


class SuccessfulTradingClient:
    def submit_order_intent(self, order_intent: OrderIntent) -> AlpacaOrderSubmission:
        return AlpacaOrderSubmission(
            order=AlpacaSubmittedOrder.model_validate(
                {
                    "id": "alpaca-order-123",
                    "client_order_id": str(order_intent.id),
                    "symbol": order_intent.option_symbol,
                    "qty": str(order_intent.quantity),
                    "side": order_intent.side,
                    "type": order_intent.order_type,
                    "limit_price": str(order_intent.limit_price),
                    "status": "new",
                    "submitted_at": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "id": "alpaca-order-123",
                "client_order_id": str(order_intent.id),
                "symbol": order_intent.option_symbol,
                "qty": str(order_intent.quantity),
                "side": order_intent.side,
                "type": order_intent.order_type,
                "limit_price": str(order_intent.limit_price),
                "status": "new",
                "submitted_at": "2026-04-23T16:00:00Z",
            },
        )


class RejectedTradingClient:
    def submit_order_intent(self, _: OrderIntent) -> AlpacaOrderSubmission:
        raise AlpacaOrderRejectedError(
            "insufficient options buying power",
            status_code=403,
        )


class SuccessfulCancelTradingClient:
    def cancel_order(self, alpaca_order_id: str) -> AlpacaOrderCancellation:
        return AlpacaOrderCancellation(
            alpaca_order_id=alpaca_order_id,
            raw_response={"status_code": 204},
        )


class SuccessfulMarketDataClient:
    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(
                {
                    "bp": "1.20",
                    "bs": "10",
                    "ap": "1.30",
                    "as": "12",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "1.20",
                "bs": "10",
                "ap": "1.30",
                "as": "12",
                "t": "2026-04-23T16:00:00Z",
            },
        )


class ExpensiveMarketDataClient:
    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(
                {
                    "bp": "209.14",
                    "bs": "3",
                    "ap": "212.47",
                    "as": "2",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "209.14",
                "bs": "3",
                "ap": "212.47",
                "as": "2",
                "t": "2026-04-23T16:00:00Z",
            },
        )


class WideQuoteMarketDataClient:
    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(
                {
                    "bp": "1.00",
                    "bs": "10",
                    "ap": "1.80",
                    "as": "12",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "1.00",
                "bs": "10",
                "ap": "1.80",
                "as": "12",
                "t": "2026-04-23T16:00:00Z",
            },
        )


class TightThenEmptyMarketDataClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_latest_option_quote(
        self,
        symbol: str,
        *,
        feed: str,
    ) -> AlpacaLatestOptionQuote:
        self.calls += 1
        if self.calls == 1:
            return SuccessfulMarketDataClient().get_latest_option_quote(symbol, feed=feed)
        return AlpacaLatestOptionQuote(
            symbol=symbol,
            quote=AlpacaOptionQuote.model_validate(
                {
                    "bp": "0",
                    "bs": "0",
                    "ap": "0",
                    "as": "0",
                    "t": "2026-04-23T16:00:00Z",
                }
            ),
            raw_response={
                "bp": "0",
                "bs": "0",
                "ap": "0",
                "as": "0",
                "t": "2026-04-23T16:00:00Z",
            },
        )


class SuccessfulOptionContractTradingClient:
    def list_option_contracts(self, **_: object) -> AlpacaOptionContractsPage:
        return AlpacaOptionContractsPage(
            contracts=[
                AlpacaOptionContract.model_validate(
                    {
                        "id": "contract-1",
                        "symbol": "SPY260417C00500000",
                        "name": "SPY Apr 17 2026 500 Call",
                        "status": "active",
                        "tradable": True,
                        "expiration_date": "2026-04-17",
                        "root_symbol": "SPY",
                        "underlying_symbol": "SPY",
                        "type": "call",
                        "style": "american",
                        "strike_price": "500",
                        "size": "100",
                        "open_interest": "500",
                    }
                )
            ],
            raw_response={"option_contracts": []},
            page_token=None,
            limit=100,
        )


def build_previewed_order_intent() -> OrderIntent:
    return OrderIntent(
        id=uuid.uuid4(),
        underlying_symbol="SPY",
        option_symbol="SPY260417C00500000",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.25"),
        time_in_force="day",
        status="previewed",
        preview={"source": "test"},
    )


def build_submitted_order_intent() -> OrderIntent:
    return OrderIntent(
        id=uuid.uuid4(),
        underlying_symbol="SPY",
        option_symbol="SPY260417C00500000",
        side="buy",
        quantity=1,
        order_type="limit",
        limit_price=Decimal("1.25"),
        time_in_force="day",
        status="new",
        preview={"source": "test"},
    )


def build_broker_order(order_intent: OrderIntent) -> BrokerOrder:
    return BrokerOrder(
        id=uuid.uuid4(),
        order_intent_id=order_intent.id,
        alpaca_order_id="alpaca-order-123",
        symbol=order_intent.option_symbol,
        side=order_intent.side,
        quantity=Decimal("1"),
        order_type=order_intent.order_type,
        limit_price=order_intent.limit_price,
        status="new",
        raw_response={"id": "alpaca-order-123", "status": "new"},
    )


def build_signal() -> Signal:
    return Signal(
        id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        symbol="SPY",
        underlying_symbol="SPY",
        signal_type="breakout",
        direction="bullish",
        confidence=Decimal("0.7500"),
        rationale="Opening range breakout",
        market_context={"price": "512.34"},
        status="new",
    )


def build_strategy(strategy_id: uuid.UUID | None = None) -> Strategy:
    return Strategy(
        id=strategy_id or uuid.uuid4(),
        name="Opening Range Breakout",
        description="Test strategy",
        is_active=True,
        config={"underlying": "SPY"},
    )


class OrderIntentSubmissionTests(unittest.TestCase):
    def test_create_order_intent_records_audit_log(self) -> None:
        db = FakeSession(None)
        payload = OrderIntentCreate(
            underlying_symbol="SPY",
            option_symbol="SPY260417C00500000",
            side="buy",
            quantity=1,
            order_type="limit",
            limit_price=Decimal("1.25"),
            time_in_force="day",
        )

        order_intent = create_order_intent(payload, db)

        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(order_intent.underlying_symbol, "SPY")
        self.assertEqual(audit_logs[-1].event_type, "order_intent.created")
        self.assertEqual(audit_logs[-1].entity_type, "order_intent")
        self.assertEqual(audit_logs[-1].payload["option_symbol"], "SPY260417C00500000")
        self.assertEqual(db.commit_count, 1)

    def test_create_order_intent_accepts_matching_strategy_and_signal(self) -> None:
        signal = build_signal()
        strategy = build_strategy(signal.strategy_id)
        db = FakeSession(None, signal=signal, strategy=strategy)

        order_intent = create_order_intent(
            OrderIntentCreate(
                strategy_id=strategy.id,
                signal_id=signal.id,
                underlying_symbol="SPY",
                option_symbol="SPY260417C00500000",
                side="buy",
                quantity=1,
                order_type="limit",
                limit_price=Decimal("1.25"),
                time_in_force="day",
            ),
            db,
        )

        self.assertEqual(order_intent.strategy_id, strategy.id)
        self.assertEqual(order_intent.signal_id, signal.id)
        self.assertEqual(db.commit_count, 1)

    def test_create_order_intent_requires_existing_strategy(self) -> None:
        db = FakeSession(None)

        with self.assertRaises(HTTPException) as context:
            create_order_intent(
                OrderIntentCreate(
                    strategy_id=uuid.uuid4(),
                    underlying_symbol="SPY",
                    option_symbol="SPY260417C00500000",
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    limit_price=Decimal("1.25"),
                    time_in_force="day",
                ),
                db,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(db.commit_count, 0)

    def test_create_order_intent_requires_existing_signal(self) -> None:
        db = FakeSession(None)

        with self.assertRaises(HTTPException) as context:
            create_order_intent(
                OrderIntentCreate(
                    signal_id=uuid.uuid4(),
                    underlying_symbol="SPY",
                    option_symbol="SPY260417C00500000",
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    limit_price=Decimal("1.25"),
                    time_in_force="day",
                ),
                db,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(db.commit_count, 0)

    def test_create_order_intent_rejects_strategy_signal_mismatch(self) -> None:
        signal = build_signal()
        strategy = build_strategy()
        db = FakeSession(None, signal=signal, strategy=strategy)

        with self.assertRaises(HTTPException) as context:
            create_order_intent(
                OrderIntentCreate(
                    strategy_id=strategy.id,
                    signal_id=signal.id,
                    underlying_symbol="SPY",
                    option_symbol="SPY260417C00500000",
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    limit_price=Decimal("1.25"),
                    time_in_force="day",
                ),
                db,
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(db.commit_count, 0)

    def test_preview_order_intent_from_signal_creates_preview_with_quote_context(self) -> None:
        signal = build_signal()
        db = FakeSession(None, signal)

        order_intent = preview_order_intent_from_signal(
            db,
            OrderIntentPreviewCreate(
                signal_id=signal.id,
                option_symbol="SPY260417C00500000",
                side="buy",
                quantity=1,
                order_type="limit",
                time_in_force="day",
            ),
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(order_intent.status, "previewed")
        self.assertEqual(signal.status, "previewed")
        self.assertEqual(order_intent.strategy_id, signal.strategy_id)
        self.assertEqual(order_intent.signal_id, signal.id)
        self.assertEqual(order_intent.limit_price, Decimal("1.25"))
        self.assertEqual(order_intent.preview["quote"]["bid_price"], "1.20")
        self.assertEqual(order_intent.preview["quote"]["ask_price"], "1.30")
        self.assertEqual(order_intent.preview["quote"]["estimated_notional"], "130.00")
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "order_intent.previewed")

    def test_preview_order_intent_from_signal_can_select_contract(self) -> None:
        signal = build_signal()
        strategy = Strategy(
            id=signal.strategy_id,
            name="Breakout Strategy",
            config={"scanner": {"type": "breakout_price_threshold"}},
        )
        db = FakeSession(None, signal, strategy)

        order_intent = preview_order_intent_from_signal(
            db,
            OrderIntentPreviewCreate(
                signal_id=signal.id,
                contract_selection=OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                    target_strike=Decimal("500"),
                ),
                side="buy",
                quantity=1,
                order_type="limit",
                time_in_force="day",
            ),
            trading_client=SuccessfulOptionContractTradingClient(),
            market_data_client=SuccessfulMarketDataClient(),
        )

        self.assertEqual(order_intent.option_symbol, "SPY260417C00500000")
        self.assertEqual(order_intent.limit_price, Decimal("1.25"))
        self.assertEqual(
            order_intent.preview["selection"]["selected_contract"]["symbol"],
            "SPY260417C00500000",
        )
        self.assertEqual(order_intent.preview["selection"]["quote"]["midpoint"], "1.25")
        self.assertEqual(db.commit_count, 1)
        diagnostics = [item for item in db.added if isinstance(item, OptionSelectionDiagnostic)]
        self.assertEqual(diagnostics, [])

    def test_preview_selection_failure_records_diagnostic_without_order_intent(self) -> None:
        signal = build_signal()
        strategy = Strategy(
            id=signal.strategy_id,
            name="Breakout Strategy",
            config={"scanner": {"type": "breakout_price_threshold"}},
        )
        db = FakeSession(None, signal, strategy)

        with self.assertRaises(OptionContractNotFoundError):
            preview_order_intent_from_signal(
                db,
                OrderIntentPreviewCreate(
                    signal_id=signal.id,
                    contract_selection=OptionContractSelectionCreate(
                        underlying_symbol="SPY",
                        option_type="call",
                        target_strike=Decimal("500"),
                        max_spread=Decimal("0.10"),
                        preview_profile="breakout_price_threshold",
                    ),
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    time_in_force="day",
                ),
                trading_client=SuccessfulOptionContractTradingClient(),
                market_data_client=WideQuoteMarketDataClient(),
            )

        diagnostics = [item for item in db.added if isinstance(item, OptionSelectionDiagnostic)]
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(order_intents, [])
        self.assertEqual(diagnostics[0].signal_id, signal.id)
        self.assertEqual(diagnostics[0].strategy_id, signal.strategy_id)
        self.assertEqual(diagnostics[0].strategy_name, "Breakout Strategy")
        self.assertEqual(diagnostics[0].underlying_symbol, "SPY")
        self.assertEqual(diagnostics[0].scanner_type, "breakout_price_threshold")
        self.assertEqual(diagnostics[0].preview_profile, "breakout_price_threshold")
        self.assertEqual(diagnostics[0].reason_counts["spread_too_wide"], 1)
        self.assertEqual(audit_logs[-1].event_type, "option_selection.failed")
        self.assertEqual(db.commit_count, 1)

    def test_preview_order_intent_from_signal_requires_existing_signal(self) -> None:
        db = FakeSession(None)

        with self.assertRaises(SignalNotFoundError):
            preview_order_intent_from_signal(
                db,
                OrderIntentPreviewCreate(
                    signal_id=uuid.uuid4(),
                    option_symbol="SPY260417C00500000",
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    time_in_force="day",
                ),
                market_data_client=SuccessfulMarketDataClient(),
            )

    def test_preview_order_intent_blocks_default_expensive_notional(self) -> None:
        signal = build_signal()
        db = FakeSession(None, signal)

        with self.assertRaises(OrderIntentPreviewError):
            preview_order_intent_from_signal(
                db,
                OrderIntentPreviewCreate(
                    signal_id=signal.id,
                    option_symbol="SPY260417C00500000",
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    time_in_force="day",
                ),
                market_data_client=ExpensiveMarketDataClient(),
            )

        self.assertEqual(db.commit_count, 0)

    def test_preview_selected_contract_quote_failure_records_diagnostic(self) -> None:
        signal = build_signal()
        strategy = Strategy(
            id=signal.strategy_id,
            name="Breakout Strategy",
            config={"scanner": {"type": "breakout_price_threshold"}},
        )
        db = FakeSession(None, signal, strategy)

        with self.assertRaises(OrderIntentPreviewError):
            preview_order_intent_from_signal(
                db,
                OrderIntentPreviewCreate(
                    signal_id=signal.id,
                    contract_selection=OptionContractSelectionCreate(
                        underlying_symbol="SPY",
                        option_type="call",
                        target_strike=Decimal("500"),
                        preview_profile="breakout_price_threshold",
                    ),
                    side="buy",
                    quantity=1,
                    order_type="limit",
                    time_in_force="day",
                ),
                trading_client=SuccessfulOptionContractTradingClient(),
                market_data_client=TightThenEmptyMarketDataClient(),
            )

        diagnostics = [item for item in db.added if isinstance(item, OptionSelectionDiagnostic)]
        order_intents = [item for item in db.added if isinstance(item, OrderIntent)]
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(order_intents, [])
        self.assertEqual(diagnostics[0].signal_id, signal.id)
        self.assertEqual(diagnostics[0].scanner_type, "breakout_price_threshold")
        self.assertEqual(diagnostics[0].preview_profile, "breakout_price_threshold")
        self.assertEqual(diagnostics[0].reason_counts["missing_limit_price"], 1)
        self.assertEqual(
            diagnostics[0].summary["selected_contract"]["symbol"],
            "SPY260417C00500000",
        )
        self.assertEqual(audit_logs[-1].event_type, "option_selection.preview_quote_failed")
        self.assertEqual(db.commit_count, 1)

    def test_preview_order_intent_schema_requires_one_contract_source(self) -> None:
        signal_id = uuid.uuid4()

        with self.assertRaises(ValidationError):
            OrderIntentPreviewCreate(
                signal_id=signal_id,
                side="buy",
                quantity=1,
                order_type="limit",
                time_in_force="day",
            )

        with self.assertRaises(ValidationError):
            OrderIntentPreviewCreate(
                signal_id=signal_id,
                option_symbol="SPY260417C00500000",
                contract_selection=OptionContractSelectionCreate(
                    underlying_symbol="SPY",
                    option_type="call",
                ),
                side="buy",
                quantity=1,
                order_type="limit",
                time_in_force="day",
            )

    def test_preview_order_intent_route_maps_contract_selection_not_found(self) -> None:
        with self.assertRaises(HTTPException) as context:
            with patch(
                "app.api.routes.order_intents.preview_order_intent_from_signal",
                side_effect=OptionContractNotFoundError("No contracts"),
            ):
                preview_order_intent(
                    OrderIntentPreviewCreate(
                        signal_id=uuid.uuid4(),
                        contract_selection=OptionContractSelectionCreate(
                            underlying_symbol="SPY",
                            option_type="call",
                        ),
                        side="buy",
                        quantity=1,
                        order_type="limit",
                        time_in_force="day",
                    ),
                    FakeSession(None),
                )

        self.assertEqual(context.exception.status_code, 404)

    def test_submit_previewed_order_intent_creates_broker_order(self) -> None:
        order_intent = build_previewed_order_intent()
        db = FakeSession(order_intent)

        updated_order_intent, broker_order = submit_order_intent(
            db,
            order_intent.id,
            trading_client=SuccessfulTradingClient(),
        )

        self.assertEqual(updated_order_intent.status, "new")
        self.assertEqual(updated_order_intent.submitted_at.isoformat(), "2026-04-23T16:00:00+00:00")
        self.assertEqual(broker_order.alpaca_order_id, "alpaca-order-123")
        self.assertEqual(broker_order.status, "new")
        self.assertEqual(broker_order.symbol, order_intent.option_symbol)
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "order_intent.submitted")
        self.assertEqual(audit_logs[-1].payload["alpaca_order_id"], "alpaca-order-123")

    def test_broker_rejection_marks_order_intent_rejected(self) -> None:
        order_intent = build_previewed_order_intent()
        db = FakeSession(order_intent)

        with self.assertRaises(AlpacaOrderRejectedError):
            submit_order_intent(
                db,
                order_intent.id,
                trading_client=RejectedTradingClient(),
            )

        self.assertEqual(order_intent.status, "rejected")
        self.assertEqual(order_intent.rejection_reason, "insufficient options buying power")
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "order_intent.rejected")
        self.assertEqual(audit_logs[-1].payload["rejection_reason"], "insufficient options buying power")

    def test_cancel_order_intent_requests_broker_cancel(self) -> None:
        order_intent = build_submitted_order_intent()
        broker_order = build_broker_order(order_intent)
        order_intent.broker_orders = [broker_order]
        db = FakeSession(order_intent, broker_order=broker_order)

        updated_order_intent, updated_broker_order = cancel_order_intent(
            db,
            order_intent.id,
            trading_client=SuccessfulCancelTradingClient(),
        )

        self.assertEqual(updated_order_intent.status, "cancel_requested")
        self.assertEqual(updated_broker_order.status, "cancel_requested")
        self.assertEqual(
            updated_broker_order.raw_response["cancel_response"]["status_code"],
            204,
        )
        self.assertEqual(db.commit_count, 1)
        audit_logs = [item for item in db.added if isinstance(item, AuditLog)]
        self.assertEqual(audit_logs[-1].event_type, "order_intent.cancel_requested")
        self.assertEqual(audit_logs[-1].payload["alpaca_order_id"], "alpaca-order-123")

    def test_cancel_order_intent_requires_broker_order(self) -> None:
        order_intent = build_submitted_order_intent()
        db = FakeSession(order_intent)

        with self.assertRaises(BrokerOrderNotFoundError):
            cancel_order_intent(
                db,
                order_intent.id,
                trading_client=SuccessfulCancelTradingClient(),
            )

        self.assertEqual(db.commit_count, 0)

    def test_order_intent_create_matches_supported_options_rules(self) -> None:
        valid_payload = OrderIntentCreate(
            underlying_symbol="SPY",
            option_symbol="SPY260417C00500000",
            side="buy",
            quantity=1,
            order_type="limit",
            limit_price=Decimal("1.25"),
            time_in_force="day",
        )
        self.assertEqual(valid_payload.time_in_force, "day")

        with self.assertRaises(ValidationError):
            OrderIntentCreate(
                underlying_symbol="SPY",
                option_symbol="SPY260417C00500000",
                side="buy",
                quantity=1,
                order_type="limit",
                limit_price=Decimal("1.25"),
                time_in_force="gtc",
            )

        with self.assertRaises(ValidationError):
            OrderIntentCreate(
                underlying_symbol="SPY",
                option_symbol="SPY260417C00500000",
                side="buy",
                quantity=1,
                order_type="market",
                limit_price=Decimal("1.25"),
                time_in_force="day",
            )


if __name__ == "__main__":
    unittest.main()
