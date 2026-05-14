from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import OptionSelectionDiagnostic, OrderIntent, Signal, Strategy

from app.integrations.alpaca import AlpacaMarketDataClient, AlpacaTradingClient

from app.schemas.order_intents import OrderIntentPreviewCreate

from app.services.audit_logs import record_audit_log

from app.services.option_contracts import OptionContractNotFoundError, select_option_contract

from app.services.order_intent_helpers import (
    _build_quote_preview,
    _decimal_from_preview,
    _effective_max_estimated_notional,
    _effective_max_spread,
    _selection_preview,
    _validate_preview_quote_constraints,
)

from app.services.order_intent_types import OrderIntentPreviewError, SignalNotFoundError

logger = logging.getLogger("app.services.order_intents")

def preview_order_intent_from_signal(
    db: Session,
    payload: OrderIntentPreviewCreate,
    *,
    market_data_client: AlpacaMarketDataClient | None = None,
    trading_client: AlpacaTradingClient | None = None,
    deadline: float | None = None,
) -> OrderIntent:
    signal = db.get(Signal, payload.signal_id)
    if signal is None:
        raise SignalNotFoundError(f"Signal '{payload.signal_id}' was not found")

    option_symbol = payload.option_symbol
    selection = None
    max_estimated_notional = _effective_max_estimated_notional(payload)
    max_spread = _effective_max_spread(payload)
    if payload.contract_selection is not None:
        selection_payload = payload.contract_selection.model_copy(
            update={
                "side": payload.side,
                "data_feed": payload.data_feed,
                "max_estimated_notional": max_estimated_notional,
                "max_spread": max_spread,
            }
        )
        try:
            selection = select_option_contract(
                selection_payload,
                trading_client=trading_client,
                market_data_client=market_data_client,
                deadline=deadline,
            )
        except OptionContractNotFoundError as exc:
            _record_option_selection_diagnostic(
                db,
                signal=signal,
                selection_payload=selection_payload,
                diagnostics=exc.diagnostics,
                error=str(exc),
            )
            raise
        option_symbol = selection.selected_contract.symbol

    if option_symbol is None:
        raise OrderIntentPreviewError("Unable to determine an option symbol")

    client = market_data_client or AlpacaMarketDataClient.from_settings()
    latest_quote = client.get_latest_option_quote(
        option_symbol,
        feed=payload.data_feed,
    )
    quote_preview = _build_quote_preview(
        latest_quote,
        side=payload.side,
        quantity=payload.quantity,
        supplied_limit_price=payload.limit_price,
    )
    try:
        _validate_preview_quote_constraints(
            quote_preview,
            max_estimated_notional=max_estimated_notional,
            max_spread=max_spread,
            max_spread_percent=payload.max_spread_percent
            or (
                selection_payload.max_spread_percent
                if payload.contract_selection is not None
                else None
            ),
        )
    except OrderIntentPreviewError as exc:
        if payload.contract_selection is not None:
            _record_preview_quote_diagnostic(
                db,
                signal=signal,
                selection_payload=selection_payload,
                quote_preview=quote_preview,
                selection=selection,
                error=str(exc),
                max_estimated_notional=max_estimated_notional,
                max_spread=max_spread,
                max_spread_percent=selection_payload.max_spread_percent,
            )
        raise

    limit_price = payload.limit_price
    if payload.order_type == "limit" and limit_price is None:
        limit_price = _decimal_from_preview(quote_preview.get("suggested_limit_price"))
        if limit_price is None:
            exc = OrderIntentPreviewError(
                "Unable to derive a limit price from the latest option quote"
            )
            if payload.contract_selection is not None:
                _record_preview_quote_diagnostic(
                    db,
                    signal=signal,
                    selection_payload=selection_payload,
                    quote_preview=quote_preview,
                    selection=selection,
                    error=str(exc),
                    max_estimated_notional=max_estimated_notional,
                    max_spread=max_spread,
                    max_spread_percent=selection_payload.max_spread_percent,
                )
            raise exc

    order_intent = OrderIntent(
        strategy_id=signal.strategy_id,
        signal_id=signal.id,
        underlying_symbol=signal.underlying_symbol or signal.symbol,
        option_symbol=option_symbol,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        limit_price=limit_price,
        time_in_force=payload.time_in_force,
        status="previewed",
        rationale=payload.rationale or signal.rationale,
        preview={
            "source": "alpaca_latest_option_quote",
            "data_feed": payload.data_feed,
            "signal": {
                "id": str(signal.id),
                "strategy_id": str(signal.strategy_id)
                if signal.strategy_id is not None
                else None,
                "signal_type": signal.signal_type,
                "direction": signal.direction,
                "confidence": str(signal.confidence)
                if signal.confidence is not None
                else None,
                "status": signal.status,
            },
            "quote": quote_preview,
            "selection": _selection_preview(selection),
        },
    )
    signal.status = "previewed"

    db.add(order_intent)
    db.add(signal)
    db.flush()
    record_audit_log(
        db,
        event_type="order_intent.previewed",
        entity_type="order_intent",
        entity_id=order_intent.id,
        message="Order intent preview generated from signal",
        payload={
            "signal_id": str(signal.id),
            "strategy_id": str(signal.strategy_id)
            if signal.strategy_id is not None
            else None,
            "option_symbol": order_intent.option_symbol,
            "side": order_intent.side,
            "quantity": order_intent.quantity,
            "order_type": order_intent.order_type,
            "limit_price": str(order_intent.limit_price)
            if order_intent.limit_price is not None
            else None,
            "preview_source": order_intent.preview.get("source"),
        },
    )
    db.commit()
    db.refresh(order_intent)
    return order_intent

def _record_option_selection_diagnostic(
    db: Session,
    *,
    signal: Signal,
    selection_payload: OptionContractSelectionCreate,
    diagnostics: dict,
    error: str,
) -> None:
    strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
    scanner_config = strategy.config.get("scanner") if strategy is not None else None
    scanner_type = scanner_config.get("type") if isinstance(scanner_config, dict) else None
    summary = dict(diagnostics or {})
    summary["error"] = error
    summary["signal"] = {
        "id": str(signal.id),
        "status": signal.status,
        "signal_type": signal.signal_type,
        "direction": signal.direction,
        "confidence": str(signal.confidence) if signal.confidence is not None else None,
    }
    if strategy is not None:
        summary["strategy"] = {
            "id": str(strategy.id),
            "name": strategy.name,
        }
    summary["scanner_type"] = scanner_type
    summary["preview_profile"] = selection_payload.preview_profile
    top_candidates = summary.get("top_rejected_candidates")
    if isinstance(top_candidates, list):
        enriched_candidates = [
            _enrich_candidate_diagnostic(
                candidate,
                signal=signal,
                strategy=strategy,
                scanner_type=scanner_type,
                underlying_symbol=selection_payload.underlying_symbol,
            )
            for candidate in top_candidates
            if isinstance(candidate, dict)
        ]
        summary["top_rejected_candidates"] = enriched_candidates
        if enriched_candidates:
            logger.info(
                "Option contract selection rejected candidates for signal %s: %s",
                signal.id,
                enriched_candidates,
                extra={"option_selection_rejected_candidates": enriched_candidates},
            )

    diagnostic = OptionSelectionDiagnostic(
        signal_id=signal.id,
        strategy_id=signal.strategy_id,
        strategy_name=strategy.name if strategy is not None else None,
        underlying_symbol=selection_payload.underlying_symbol,
        scanner_type=scanner_type,
        preview_profile=selection_payload.preview_profile,
        candidate_count=int(summary.get("candidates_seen") or 0),
        reason_counts=summary.get("reason_counts") or {},
        summary=summary,
        market_context=signal.market_context or {},
    )
    db.add(diagnostic)
    record_audit_log(
        db,
        event_type="option_selection.failed",
        entity_type="signal",
        entity_id=signal.id,
        message="Option contract selection failed before order intent creation",
        payload=summary,
    )
    db.commit()


def _record_preview_quote_diagnostic(
    db: Session,
    *,
    signal: Signal,
    selection_payload: OptionContractSelectionCreate,
    quote_preview: dict[str, object],
    selection: object,
    error: str,
    max_estimated_notional: object,
    max_spread: object,
    max_spread_percent: object,
) -> None:
    strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
    scanner_config = strategy.config.get("scanner") if strategy is not None else None
    scanner_type = scanner_config.get("type") if isinstance(scanner_config, dict) else None
    reason_code = _preview_quote_reason_code(error)
    selected_contract = None
    if selection is not None and getattr(selection, "selected_contract", None) is not None:
        selected_contract = selection.selected_contract.model_dump(mode="json")
    summary = {
        "error": error,
        "scanner_type": scanner_type,
        "preview_profile": selection_payload.preview_profile,
        "underlying_symbol": selection_payload.underlying_symbol,
        "option_type": selection_payload.option_type,
        "side": selection_payload.side,
        "reason_counts": {reason_code: 1},
        "candidate_count": 1,
        "selected_contract": selected_contract,
        "quote": quote_preview,
        "limits": {
            "max_estimated_notional": str(max_estimated_notional)
            if max_estimated_notional is not None
            else None,
            "max_spread": str(max_spread) if max_spread is not None else None,
            "max_spread_percent": str(max_spread_percent)
            if max_spread_percent is not None
            else None,
        },
        "signal": {
            "id": str(signal.id),
            "status": signal.status,
            "signal_type": signal.signal_type,
            "direction": signal.direction,
            "confidence": str(signal.confidence) if signal.confidence is not None else None,
        },
    }
    if strategy is not None:
        summary["strategy"] = {
            "id": str(strategy.id),
            "name": strategy.name,
        }

    diagnostic = OptionSelectionDiagnostic(
        signal_id=signal.id,
        strategy_id=signal.strategy_id,
        strategy_name=strategy.name if strategy is not None else None,
        underlying_symbol=selection_payload.underlying_symbol,
        scanner_type=scanner_type,
        preview_profile=selection_payload.preview_profile,
        candidate_count=1,
        reason_counts={reason_code: 1},
        summary=summary,
        market_context=signal.market_context or {},
    )
    db.add(diagnostic)
    record_audit_log(
        db,
        event_type="option_selection.preview_quote_failed",
        entity_type="signal",
        entity_id=signal.id,
        message="Selected option contract failed preview quote constraints",
        payload=summary,
    )
    db.commit()


def _preview_quote_reason_code(error: str) -> str:
    normalized = error.lower()
    if "estimated notional" in normalized:
        return "estimated_notional_above_max"
    if "quote spread" in normalized:
        return "spread_too_wide"
    if "limit price" in normalized:
        return "missing_limit_price"
    return "preview_quote_constraint_failed"

def _enrich_candidate_diagnostic(
    candidate: dict[str, object],
    *,
    signal: Signal,
    strategy: Strategy | None,
    scanner_type: str | None,
    underlying_symbol: str,
) -> dict[str, object]:
    enriched = dict(candidate)
    enriched["signal_id"] = str(signal.id)
    enriched["underlying_symbol"] = underlying_symbol
    enriched["strategy"] = (
        {"id": str(strategy.id), "name": strategy.name, "scanner_type": scanner_type}
        if strategy is not None
        else None
    )
    return enriched
