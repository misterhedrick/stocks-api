from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import JobRun, OrderIntent, Signal, Strategy
from app.schemas.options import OptionContractSelectionCreate
from app.schemas.order_intents import OrderIntentPreviewCreate
from app.services.audit_logs import record_audit_log
from app.services.broker_reconciliation import reconcile_broker_state
from app.services.order_intents import preview_order_intent_from_signal, submit_order_intent
from app.services.signal_scanner import scan_signals


@dataclass(slots=True)
class MarketCycleResult:
    job_run: JobRun
    scan_enabled: bool
    reconcile_enabled: bool
    preview_enabled: bool
    submit_enabled: bool
    scan: dict[str, Any] | None
    reconcile: dict[str, Any] | None
    preview: dict[str, Any] | None
    submit: dict[str, Any] | None


def run_market_cycle(
    db: Session,
    *,
    scan_limit: int = 100,
    order_limit: int = 100,
    fill_page_size: int = 100,
) -> MarketCycleResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="market_cycle",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    scan_enabled = settings.market_cycle_scan_enabled
    reconcile_enabled = settings.market_cycle_reconcile_enabled
    preview_enabled = settings.market_cycle_preview_enabled
    submit_enabled = settings.market_cycle_submit_enabled

    scan = None
    reconcile = None
    preview = _disabled_step("preview")
    submit = _disabled_step("submit")

    try:
        created_signal_ids: list[uuid.UUID] = []
        if scan_enabled:
            scan_result = scan_signals(db, limit=scan_limit)
            created_signal_ids = scan_result.created_signal_ids
            scan = {
                "job_run_id": str(scan_result.job_run.id),
                "strategies_seen": scan_result.strategies_seen,
                "strategies_scanned": scan_result.strategies_scanned,
                "signals_created": scan_result.signals_created,
                "signals_skipped": scan_result.signals_skipped,
                "errors": scan_result.errors,
                "created_signal_ids": [
                    str(signal_id) for signal_id in scan_result.created_signal_ids
                ],
            }
        else:
            scan = _disabled_step("scan")

        if preview_enabled:
            preview = _preview_created_signals(db, created_signal_ids)

        if submit_enabled:
            submit = _submit_previewed_order_intents(
                db,
                _order_intent_ids_from_preview(preview),
            )

        if reconcile_enabled:
            reconciliation_result = reconcile_broker_state(
                db,
                order_limit=order_limit,
                fill_page_size=fill_page_size,
            )
            reconcile = {
                "job_run_id": str(reconciliation_result.job_run.id),
                "orders_seen": reconciliation_result.orders_seen,
                "orders_created": reconciliation_result.orders_created,
                "orders_updated": reconciliation_result.orders_updated,
                "fills_seen": reconciliation_result.fills_seen,
                "fills_created": reconciliation_result.fills_created,
                "positions_seen": reconciliation_result.positions_seen,
                "position_snapshots_created": reconciliation_result.position_snapshots_created,
            }
        else:
            reconcile = _disabled_step("reconcile")

        details = {
            "scan_enabled": scan_enabled,
            "reconcile_enabled": reconcile_enabled,
            "preview_enabled": preview_enabled,
            "submit_enabled": submit_enabled,
            "scan": scan,
            "reconcile": reconcile,
            "preview": preview,
            "submit": submit,
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None
        db.add(job_run)
        record_audit_log(
            db,
            event_type="market_cycle.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Market cycle succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return MarketCycleResult(job_run=job_run, **details)
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="market_cycle.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="Market cycle failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise


def _disabled_step(step_name: str) -> dict[str, Any]:
    return {"status": "disabled", "step": step_name}


def _not_implemented_step(step_name: str) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "step": step_name,
        "message": f"{step_name} automation is not implemented yet",
    }


def _preview_created_signals(
    db: Session,
    signal_ids: list[uuid.UUID],
) -> dict[str, Any]:
    previews_created = 0
    previews_skipped = 0
    errors: list[str] = []
    order_intent_ids: list[str] = []

    for signal_id in signal_ids:
        signal = db.get(Signal, signal_id)
        if signal is None:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}' was not found")
            continue

        strategy = db.get(Strategy, signal.strategy_id) if signal.strategy_id else None
        if strategy is None:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}' has no strategy")
            continue

        try:
            payload = _preview_payload_for_signal(signal, strategy)
        except ValueError as exc:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}': {exc}")
            continue

        try:
            order_intent = preview_order_intent_from_signal(db, payload)
        except Exception as exc:
            previews_skipped += 1
            errors.append(f"Signal '{signal_id}': {exc.__class__.__name__}: {exc}")
            continue

        previews_created += 1
        order_intent_ids.append(str(order_intent.id))

    return {
        "status": "completed",
        "signals_seen": len(signal_ids),
        "previews_created": previews_created,
        "previews_skipped": previews_skipped,
        "errors": errors,
        "order_intent_ids": order_intent_ids,
    }


def _submit_previewed_order_intents(
    db: Session,
    order_intent_ids: list[uuid.UUID],
) -> dict[str, Any]:
    submitted = 0
    rejected = 0
    skipped = 0
    errors: list[str] = []
    broker_order_ids: list[str] = []

    orders_submitted_by_strategy: dict[uuid.UUID, int] = {}
    for order_intent_id in order_intent_ids:
        order_intent = db.get(OrderIntent, order_intent_id)
        if order_intent is None:
            skipped += 1
            errors.append(f"Order intent '{order_intent_id}' was not found")
            continue

        strategy = db.get(Strategy, order_intent.strategy_id) if order_intent.strategy_id else None
        if strategy is None:
            skipped += 1
            errors.append(f"Order intent '{order_intent_id}' has no strategy")
            continue

        try:
            submit_config = _submit_config_for_strategy(strategy)
            _validate_submit_limits(
                order_intent,
                submit_config,
                orders_submitted_by_strategy.get(strategy.id, 0),
            )
        except ValueError as exc:
            skipped += 1
            errors.append(f"Order intent '{order_intent_id}': {exc}")
            continue

        try:
            _, broker_order = submit_order_intent(db, order_intent.id)
        except Exception as exc:
            rejected += 1
            errors.append(f"Order intent '{order_intent_id}': {exc.__class__.__name__}: {exc}")
            continue

        submitted += 1
        orders_submitted_by_strategy[strategy.id] = (
            orders_submitted_by_strategy.get(strategy.id, 0) + 1
        )
        broker_order_ids.append(str(broker_order.id))

    return {
        "status": "completed",
        "order_intents_seen": len(order_intent_ids),
        "submitted": submitted,
        "rejected": rejected,
        "skipped": skipped,
        "errors": errors,
        "broker_order_ids": broker_order_ids,
    }


def _order_intent_ids_from_preview(preview: dict[str, Any] | None) -> list[uuid.UUID]:
    if not isinstance(preview, dict):
        return []

    order_intent_ids = []
    for value in preview.get("order_intent_ids", []):
        try:
            order_intent_ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return order_intent_ids


def _preview_payload_for_signal(
    signal: Signal,
    strategy: Strategy,
) -> OrderIntentPreviewCreate:
    preview_config = _preview_config_for_strategy(strategy)

    option_symbol = preview_config.get("option_symbol")
    contract_selection = None
    if option_symbol is None:
        contract_selection = _contract_selection_for_signal(signal, preview_config)

    return OrderIntentPreviewCreate(
        signal_id=signal.id,
        option_symbol=option_symbol if isinstance(option_symbol, str) else None,
        contract_selection=contract_selection,
        side=_string_config(preview_config, "side", default="buy"),
        quantity=_int_config(preview_config, "quantity", default=1),
        order_type=_string_config(preview_config, "order_type", default="limit"),
        limit_price=preview_config.get("limit_price"),
        time_in_force=_string_config(preview_config, "time_in_force", default="day"),
        rationale=preview_config.get("rationale")
        if isinstance(preview_config.get("rationale"), str)
        else signal.rationale,
        data_feed=_string_config(preview_config, "data_feed", default="indicative"),
    )


def _preview_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for auto-preview")

    preview_config = scanner_config.get("preview")
    if not isinstance(preview_config, dict):
        raise ValueError("scanner.preview config is required")
    if preview_config.get("enabled") is not True:
        raise ValueError("scanner.preview.enabled must be true")
    return preview_config


def _submit_config_for_strategy(strategy: Strategy) -> dict[str, Any]:
    scanner_config = strategy.config.get("scanner")
    if not isinstance(scanner_config, dict):
        raise ValueError("strategy scanner config is required for auto-submit")

    submit_config = scanner_config.get("submit")
    if not isinstance(submit_config, dict):
        raise ValueError("scanner.submit config is required")
    if submit_config.get("enabled") is not True:
        raise ValueError("scanner.submit.enabled must be true")
    return submit_config


def _validate_submit_limits(
    order_intent: OrderIntent,
    submit_config: dict[str, Any],
    submitted_for_strategy: int,
) -> None:
    max_orders_per_cycle = _int_config(
        submit_config,
        "max_orders_per_cycle",
        default=1,
        label_prefix="scanner.submit",
    )
    if submitted_for_strategy >= max_orders_per_cycle:
        raise ValueError("scanner.submit.max_orders_per_cycle reached")

    max_contracts_per_order = _int_config(
        submit_config,
        "max_contracts_per_order",
        default=1,
        label_prefix="scanner.submit",
    )
    if order_intent.quantity > max_contracts_per_order:
        raise ValueError("order quantity exceeds scanner.submit.max_contracts_per_order")

    allowed_sides = submit_config.get("allowed_sides", ["buy"])
    if not isinstance(allowed_sides, list) or not allowed_sides:
        raise ValueError("scanner.submit.allowed_sides must be a non-empty list")
    clean_allowed_sides = {
        value.strip().lower()
        for value in allowed_sides
        if isinstance(value, str) and value.strip()
    }
    if order_intent.side.lower() not in clean_allowed_sides:
        raise ValueError("order side is not allowed by scanner.submit.allowed_sides")


def _contract_selection_for_signal(
    signal: Signal,
    preview_config: dict[str, Any],
) -> OptionContractSelectionCreate:
    underlying_symbol = preview_config.get("underlying_symbol")
    if not isinstance(underlying_symbol, str) or not underlying_symbol.strip():
        underlying_symbol = signal.underlying_symbol or signal.symbol

    return OptionContractSelectionCreate(
        underlying_symbol=underlying_symbol,
        option_type=_string_config(preview_config, "option_type"),
        side=_string_config(preview_config, "side", default="buy"),
        expiration_date=preview_config.get("expiration_date"),
        expiration_date_gte=preview_config.get("expiration_date_gte"),
        expiration_date_lte=preview_config.get("expiration_date_lte"),
        target_strike=preview_config.get("target_strike"),
        underlying_price=preview_config.get("underlying_price"),
        data_feed=_string_config(preview_config, "data_feed", default="indicative"),
        limit=_int_config(preview_config, "limit", default=100),
    )


def _string_config(
    config: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
) -> str:
    value = config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"scanner.preview.{key} must be a non-empty string")
    return value.strip()


def _int_config(
    config: dict[str, Any],
    key: str,
    *,
    default: int,
    label_prefix: str = "scanner.preview",
) -> int:
    value = config.get(key, default)
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label_prefix}.{key} must be an integer") from exc
    if int_value <= 0:
        raise ValueError(f"{label_prefix}.{key} must be greater than 0")
    return int_value
