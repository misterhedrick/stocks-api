from __future__ import annotations

import argparse
import json
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.models import Strategy
from app.db.session import SessionLocal
from app.integrations.alpaca import AlpacaMarketDataClient
from app.services.audit_logs import record_audit_log
from app.services.strategy_templates import build_moving_average_strategy_payload
from app.services.strategy_templates import build_momentum_rate_of_change_strategy_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List and tune preview-first strategy configs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List strategy scanner summaries.")
    list_parser.add_argument("--active-only", action="store_true")

    seed_parser = subparsers.add_parser(
        "seed-moving-average",
        help="Create or update one preview-first moving-average strategy.",
    )
    seed_parser.add_argument("--name")
    seed_parser.add_argument("--symbol", default="SPY")
    seed_parser.add_argument("--option-type", choices=["call", "put"], default="call")
    seed_parser.add_argument(
        "--trigger",
        choices=["bullish_cross", "bearish_cross", "bullish_trend", "bearish_trend"],
        default="bullish_trend",
    )
    seed_parser.add_argument("--short-window", type=int, default=5)
    seed_parser.add_argument("--long-window", type=int, default=20)
    seed_parser.add_argument("--lookback-minutes", type=int, default=1440)
    seed_parser.add_argument("--timeframe", default="5Min")
    seed_parser.add_argument("--min-change-percent", default="0.10")
    seed_parser.add_argument("--confidence", default="0.6200")
    seed_parser.add_argument(
        "--target-strike",
        help="Override target strike. Defaults to latest IEX midpoint rounded to whole dollars.",
    )
    seed_parser.add_argument(
        "--sample-price",
        help="Use this underlying price instead of Alpaca market data.",
    )
    seed_parser.add_argument("--dry-run", action="store_true")

    momentum_parser = subparsers.add_parser(
        "seed-momentum-rate-of-change",
        help="Create or update one preview-first momentum rate-of-change strategy.",
    )
    momentum_parser.add_argument("--name")
    momentum_parser.add_argument("--symbol", default="SPY")
    momentum_parser.add_argument("--option-type", choices=["call", "put"], default="call")
    momentum_parser.add_argument("--direction", choices=["bullish", "bearish"], default="bullish")
    momentum_parser.add_argument("--timeframe", default="1Min")
    momentum_parser.add_argument("--lookback-minutes", type=int, default=30)
    momentum_parser.add_argument("--change-above-percent", default="0.25")
    momentum_parser.add_argument("--change-below-percent", default="-0.25")
    momentum_parser.add_argument("--short-average-type", default="ema")
    momentum_parser.add_argument("--short-average-window", type=int, default=9)
    momentum_parser.add_argument("--max-extension-percent")
    momentum_parser.add_argument("--confidence", default="0.6500")
    momentum_parser.add_argument("--target-strike")
    momentum_parser.add_argument("--sample-price")
    momentum_parser.add_argument("--dry-run", action="store_true")

    patch_parser = subparsers.add_parser(
        "patch-scanner",
        help="Merge a JSON object into an existing strategy scanner config.",
    )
    patch_parser.add_argument("--name", required=True)
    patch_parser.add_argument(
        "--scanner-json",
        default=None,
        help="JSON object to deep-merge into config.scanner.",
    )
    patch_parser.add_argument("--short-window", type=int)
    patch_parser.add_argument("--long-window", type=int)
    patch_parser.add_argument("--lookback-minutes", type=int)
    patch_parser.add_argument("--timeframe")
    patch_parser.add_argument(
        "--trigger",
        choices=["bullish_cross", "bearish_cross", "bullish_trend", "bearish_trend"],
    )
    patch_parser.add_argument("--dry-run", action="store_true")

    submit_parser = subparsers.add_parser(
        "set-submit",
        help="Enable or disable scanner auto-submit metadata. Runtime volume is controlled by env.",
    )
    submit_parser.add_argument("--name", required=True)
    submit_group = submit_parser.add_mutually_exclusive_group(required=True)
    submit_group.add_argument("--enable", action="store_true")
    submit_group.add_argument("--disable", action="store_true")
    submit_parser.add_argument("--max-orders-per-cycle", type=int, default=100)
    submit_parser.add_argument("--max-contracts-per-order", type=int, default=1)
    submit_parser.add_argument("--max-contracts-per-cycle", type=int, default=100)
    submit_parser.add_argument("--max-notional-per-order", default="5000.00")
    submit_parser.add_argument("--max-open-contracts-per-symbol", type=int, default=100)
    submit_parser.add_argument("--max-open-contracts-per-strategy", type=int, default=100)
    submit_parser.add_argument("--max-orders-per-trading-day", type=int, default=500)
    submit_parser.add_argument("--trading-day-timezone", default="America/New_York")
    submit_parser.add_argument("--trade-window-timezone", default="America/New_York")
    submit_parser.add_argument("--trade-window-start", default="10:00")
    submit_parser.add_argument("--trade-window-end", default="16:00")
    submit_parser.add_argument(
        "--allowed-side",
        action="append",
        dest="allowed_sides",
        choices=["buy", "sell"],
        default=None,
        help="Allowed order side. May be passed more than once.",
    )
    submit_parser.add_argument("--dry-run", action="store_true")

    batch_parser = subparsers.add_parser(
        "apply-2026-05-18-strategy-type-batch",
        help="Apply the 2026-05-18 evidence tuning batch by scanner type.",
    )
    batch_parser.add_argument("--dry-run", action="store_true")

    quality_batch_parser = subparsers.add_parser(
        "apply-2026-05-29-entry-quality-batch",
        help="Apply the 2026-05-29 entry-selection tuning batch by scanner type.",
    )
    quality_batch_parser.add_argument("--dry-run", action="store_true")

    fresh_paper_batch_parser = subparsers.add_parser(
        "apply-2026-06-11-fresh-paper-tuning-batch",
        help="Apply the 2026-06-11 fresh-paper evidence tuning batch by scanner type.",
    )
    fresh_paper_batch_parser.add_argument("--dry-run", action="store_true")

    risk_breakout_batch_parser = subparsers.add_parser(
        "apply-2026-06-17-risk-breakout-quality-batch",
        help="Apply the 2026-06-17 exit-risk and breakout-quality tuning batch.",
    )
    risk_breakout_batch_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    with SessionLocal() as db:
        if args.command == "list":
            summaries = list_strategy_summaries(db, active_only=args.active_only)
            print(json.dumps(summaries, indent=2, sort_keys=True, default=str))
            return

        if args.command == "seed-moving-average":
            payload = moving_average_payload_from_args(args)
            if args.dry_run:
                print(json.dumps(payload, indent=2, sort_keys=True))
                return
            created = upsert_strategy(db, payload, source="strategy_tuning_script")
            db.commit()
            action = "created" if created else "updated"
            print(f"Moving-average strategy {action}: {payload['name']}")
            return

        if args.command == "seed-momentum-rate-of-change":
            payload = momentum_rate_of_change_payload_from_args(args)
            if args.dry_run:
                print(json.dumps(payload, indent=2, sort_keys=True))
                return
            created = upsert_strategy(db, payload, source="strategy_tuning_script")
            db.commit()
            action = "created" if created else "updated"
            print(f"Momentum rate-of-change strategy {action}: {payload['name']}")
            return

        if args.command == "patch-scanner":
            scanner_patch = scanner_patch_from_args(args)
            strategy = patch_strategy_scanner(
                db,
                name=args.name,
                scanner_patch=scanner_patch,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(_strategy_summary(strategy), indent=2, sort_keys=True, default=str))
                return
            db.commit()
            print(f"Patched scanner config for: {strategy.name}")
            return

        if args.command == "set-submit":
            submit_config = submit_config_from_args(args)
            strategy = set_strategy_submit_config(
                db,
                name=args.name,
                submit_config=submit_config,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(_strategy_summary(strategy), indent=2, sort_keys=True, default=str))
                return
            db.commit()
            state = "enabled" if submit_config["enabled"] else "disabled"
            print(f"Scanner submit {state} for: {strategy.name}")
            return

        if args.command == "apply-2026-05-18-strategy-type-batch":
            results = apply_strategy_type_batch(
                db,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(results, indent=2, sort_keys=True, default=str))
                return
            db.commit()
            print(json.dumps(results, indent=2, sort_keys=True, default=str))
            return

        if args.command == "apply-2026-05-29-entry-quality-batch":
            results = apply_entry_quality_batch_2026_05_29(
                db,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(results, indent=2, sort_keys=True, default=str))
                return
            db.commit()
            print(json.dumps(results, indent=2, sort_keys=True, default=str))
            return

        if args.command == "apply-2026-06-11-fresh-paper-tuning-batch":
            results = apply_fresh_paper_tuning_batch_2026_06_11(
                db,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(results, indent=2, sort_keys=True, default=str))
                return
            db.commit()
            print(json.dumps(results, indent=2, sort_keys=True, default=str))
            return

        if args.command == "apply-2026-06-17-risk-breakout-quality-batch":
            results = apply_risk_breakout_quality_batch_2026_06_17(
                db,
                dry_run=args.dry_run,
            )
            if args.dry_run:
                print(json.dumps(results, indent=2, sort_keys=True, default=str))
                return
            db.commit()
            print(json.dumps(results, indent=2, sort_keys=True, default=str))
            return


def list_strategy_summaries(
    db: Session,
    *,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    statement = select(Strategy).order_by(Strategy.name.asc())
    if active_only:
        statement = statement.where(Strategy.is_active == True)  # noqa: E712
    return [_strategy_summary(strategy) for strategy in db.scalars(statement)]


def moving_average_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    symbol = args.symbol.strip().upper()
    target_strike = (
        Decimal(args.target_strike)
        if args.target_strike is not None
        else _whole_dollar(_price_for_symbol(symbol, sample_price=args.sample_price))
    )
    return build_moving_average_strategy_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=args.name,
        option_type=args.option_type,
        trigger=args.trigger,
        short_window=args.short_window,
        long_window=args.long_window,
        lookback_minutes=args.lookback_minutes,
        timeframe=args.timeframe,
        min_change_percent=args.min_change_percent,
        confidence=args.confidence,
    )


def scanner_patch_from_args(args: argparse.Namespace) -> dict[str, Any]:
    scanner_patch = (
        _json_object(args.scanner_json)
        if args.scanner_json is not None
        else {}
    )
    optional_fields = {
        "short_window": args.short_window,
        "long_window": args.long_window,
        "lookback_minutes": args.lookback_minutes,
        "timeframe": args.timeframe,
        "trigger": args.trigger,
    }
    for key, value in optional_fields.items():
        if value is not None:
            scanner_patch[key] = value
    if not scanner_patch:
        raise RuntimeError(
            "provide --scanner-json or at least one scanner patch flag"
        )
    return scanner_patch


def submit_config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "enabled": bool(args.enable),
        "max_orders_per_cycle": args.max_orders_per_cycle,
        "max_contracts_per_order": args.max_contracts_per_order,
        "max_contracts_per_cycle": args.max_contracts_per_cycle,
        "max_notional_per_order": _money_string(args.max_notional_per_order),
        "max_open_contracts_per_symbol": args.max_open_contracts_per_symbol,
        "max_open_contracts_per_strategy": args.max_open_contracts_per_strategy,
        "max_orders_per_trading_day": args.max_orders_per_trading_day,
        "trading_day_timezone": args.trading_day_timezone,
        "trade_windows": [
            {
                "timezone": args.trade_window_timezone,
                "start": args.trade_window_start,
                "end": args.trade_window_end,
            }
        ],
        "allowed_sides": args.allowed_sides or ["buy"],
    }


def momentum_rate_of_change_payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    symbol = args.symbol.strip().upper()
    target_strike = (
        Decimal(args.target_strike)
        if args.target_strike is not None
        else _whole_dollar(_price_for_symbol(symbol, sample_price=args.sample_price))
    )
    return build_momentum_rate_of_change_strategy_payload(
        symbol=symbol,
        target_strike=target_strike,
        name=args.name,
        option_type=args.option_type,
        direction=args.direction,
        timeframe=args.timeframe,
        lookback_minutes=args.lookback_minutes,
        change_above_percent=args.change_above_percent,
        change_below_percent=args.change_below_percent,
        short_average_type=args.short_average_type,
        short_average_window=args.short_average_window,
        max_extension_percent=getattr(args, "max_extension_percent", None) or "1.00",
        confidence=args.confidence,
    )


def upsert_strategy(db: Session, payload: dict[str, Any], *, source: str) -> bool:
    existing = db.scalar(select(Strategy).where(Strategy.name == payload["name"]))
    created = existing is None
    strategy = Strategy(**payload) if created else existing
    if not created:
        strategy.description = payload["description"]
        strategy.is_active = payload["is_active"]
        strategy.config = payload["config"]

    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.created" if created else "strategy.updated",
            entity_type="strategy",
            entity_id=strategy.id,
            message=(
                "Strategy created by tuning script"
                if created
                else "Strategy updated by tuning script"
            ),
            payload={"source": source, "strategy": _strategy_audit_payload(strategy)},
        )
    except SQLAlchemyError:
        db.rollback()
        raise
    return created


def patch_strategy_scanner(
    db: Session,
    *,
    name: str,
    scanner_patch: dict[str, Any],
    dry_run: bool = False,
) -> Strategy:
    strategy = db.scalar(select(Strategy).where(Strategy.name == name))
    if strategy is None:
        raise RuntimeError(f"Strategy '{name}' was not found")

    config = deepcopy(strategy.config) if isinstance(strategy.config, dict) else {}
    scanner_config = config.get("scanner")
    if not isinstance(scanner_config, dict):
        scanner_config = {}
    config["scanner"] = _deep_merge(scanner_config, scanner_patch)

    if dry_run:
        return Strategy(
            id=strategy.id,
            name=strategy.name,
            description=strategy.description,
            is_active=strategy.is_active,
            config=config,
            created_at=strategy.created_at,
            updated_at=strategy.updated_at,
        )

    strategy.config = config
    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.updated",
            entity_type="strategy",
            entity_id=strategy.id,
            message="Strategy scanner patched by tuning script",
            payload={
                "source": "strategy_tuning_script",
                "scanner_patch": scanner_patch,
                "strategy": _strategy_audit_payload(strategy),
            },
        )
    except SQLAlchemyError:
        db.rollback()
        raise
    return strategy


def set_strategy_submit_config(
    db: Session,
    *,
    name: str,
    submit_config: dict[str, Any],
    dry_run: bool = False,
) -> Strategy:
    strategy = db.scalar(select(Strategy).where(Strategy.name == name))
    if strategy is None:
        raise RuntimeError(f"Strategy '{name}' was not found")

    config = deepcopy(strategy.config) if isinstance(strategy.config, dict) else {}
    scanner_config = config.get("scanner")
    if not isinstance(scanner_config, dict):
        scanner_config = {}
    scanner_config["submit"] = deepcopy(submit_config)
    config["scanner"] = scanner_config

    if dry_run:
        return Strategy(
            id=strategy.id,
            name=strategy.name,
            description=strategy.description,
            is_active=strategy.is_active,
            config=config,
            created_at=strategy.created_at,
            updated_at=strategy.updated_at,
        )

    strategy.config = config
    try:
        db.add(strategy)
        db.flush()
        record_audit_log(
            db,
            event_type="strategy.updated",
            entity_type="strategy",
            entity_id=strategy.id,
            message="Strategy scanner submit controls updated by tuning script",
            payload={
                "source": "strategy_tuning_script",
                "submit_config": submit_config,
                "strategy": _strategy_audit_payload(strategy),
            },
        )
    except SQLAlchemyError:
        db.rollback()
        raise
    return strategy


STRATEGY_TYPE_BATCH_2026_05_18: dict[str, dict[str, Any]] = {
    "support_resistance": {
        "min_touches": 3,
        "level_tolerance_percent": "0.15",
        "max_distance_percent": "0.75",
        "dedupe_minutes": 120,
    },
    "momentum_rate_of_change": {
        "lookback_minutes": 45,
        "change_above_percent": "0.50",
        "change_below_percent": "-0.50",
        "max_extension_percent": "1.25",
        "dedupe_minutes": 120,
        "exit": {
            "stop_loss_percent": "15",
            "stop_loss_min_dollars": "10",
        },
    },
    "moving_average": {
        "min_change_percent": "0.15",
        "min_average_separation_percent": "0.05",
        "max_price_distance_percent": "0.75",
        "dedupe_minutes": 360,
    },
    "mean_reversion": {
        "bollinger_stddev": "2.25",
        "max_distance_to_middle_percent": "1.50",
        "dedupe_minutes": 120,
        "exit": {
            "stop_loss_percent": "15",
            "stop_loss_min_dollars": "10",
        },
    },
    "rsi_reversal": {
        "oversold_level": "30",
        "overbought_level": "70",
        "trend_average_type": "ema",
        "trend_average_window": 20,
        "reject_trend_conflict": True,
        "dedupe_minutes": 120,
    },
    "volume_confirmed_breakout": {
        "min_relative_volume": "1.50",
        "breakout_buffer_percent": "0.10",
        "max_breakout_distance_percent": "2.00",
        "dedupe_minutes": 120,
    },
}


ENTRY_QUALITY_BATCH_2026_05_29: dict[str, dict[str, Any]] = {
    "moving_average": {
        "trigger": "crossover",
    },
    "relative_strength": {
        "min_edge_percent": "1.50",
    },
    "breakout_price_threshold": {
        "breakout_buffer_percent": "0.15",
    },
    "momentum_rate_of_change": {
        "timeframe": "5Min",
    },
}


FRESH_PAPER_TUNING_BATCH_2026_06_11: dict[str, dict[str, Any]] = {
    "mean_reversion": {
        "bollinger_stddev": "2.50",
        "max_distance_to_middle_percent": "0.75",
    },
    "momentum_rate_of_change": {
        "change_above_percent": "0.75",
        "change_below_percent": "-0.75",
        "max_extension_percent": "1.00",
    },
    "support_resistance": {
        "max_distance_percent": "0.35",
    },
    "time_series_momentum": {
        "min_trend_percent": "2.00",
    },
}


RISK_BREAKOUT_QUALITY_BATCH_2026_06_17: dict[str, dict[str, Any]] = {
    "mean_reversion": {
        "exit": {
            "stop_loss_percent": "8",
            "max_hold_hours": 4,
        },
    },
    "volatility_squeeze": {
        "breakout_buffer_percent": "0.20",
        "max_breakout_distance_percent": "1.50",
    },
    "breakout_price_threshold": {
        "breakout_buffer_percent": "0.20",
        "max_breakout_distance_percent": "1.25",
    },
}


def apply_strategy_type_batch(
    db: Session,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    strategies = list(
        db.scalars(select(Strategy).where(Strategy.is_active == True))  # noqa: E712
    )
    results: list[dict[str, Any]] = []
    for strategy in strategies:
        scanner = (
            strategy.config.get("scanner")
            if isinstance(strategy.config, dict)
            and isinstance(strategy.config.get("scanner"), dict)
            else None
        )
        if scanner is None:
            continue
        scanner_type = scanner.get("type")
        if not isinstance(scanner_type, str):
            continue
        patch = STRATEGY_TYPE_BATCH_2026_05_18.get(scanner_type)
        if patch is None:
            results.append(
                {
                    "name": strategy.name,
                    "scanner_type": scanner_type,
                    "status": "watch",
                    "reason": "No scanner-config patch in this evidence batch.",
                }
            )
            continue

        before = deepcopy(scanner)
        patched = patch_strategy_scanner(
            db,
            name=strategy.name,
            scanner_patch=patch,
            dry_run=dry_run,
        )
        after_config = (
            patched.config.get("scanner")
            if isinstance(patched.config, dict)
            and isinstance(patched.config.get("scanner"), dict)
            else {}
        )
        results.append(
            {
                "name": strategy.name,
                "scanner_type": scanner_type,
                "status": "would_update" if dry_run else "updated",
                "patch": patch,
                "changed": _changed_scanner_keys(before, after_config, patch),
            }
        )
    return results


def apply_entry_quality_batch_2026_05_29(
    db: Session,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    return _apply_scanner_type_batch(
        db,
        batch=ENTRY_QUALITY_BATCH_2026_05_29,
        dry_run=dry_run,
    )


def apply_fresh_paper_tuning_batch_2026_06_11(
    db: Session,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    return _apply_scanner_type_batch(
        db,
        batch=FRESH_PAPER_TUNING_BATCH_2026_06_11,
        dry_run=dry_run,
    )


def apply_risk_breakout_quality_batch_2026_06_17(
    db: Session,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    return _apply_scanner_type_batch(
        db,
        batch=RISK_BREAKOUT_QUALITY_BATCH_2026_06_17,
        dry_run=dry_run,
    )


def _apply_scanner_type_batch(
    db: Session,
    *,
    batch: dict[str, dict[str, Any]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    strategies = list(
        db.scalars(select(Strategy).where(Strategy.is_active == True))  # noqa: E712
    )
    results: list[dict[str, Any]] = []
    for strategy in strategies:
        scanner = (
            strategy.config.get("scanner")
            if isinstance(strategy.config, dict)
            and isinstance(strategy.config.get("scanner"), dict)
            else None
        )
        if scanner is None:
            continue
        scanner_type = scanner.get("type")
        if not isinstance(scanner_type, str):
            continue
        patch = batch.get(scanner_type)
        if patch is None:
            results.append(
                {
                    "name": strategy.name,
                    "scanner_type": scanner_type,
                    "status": "watch",
                    "reason": "No scanner-config patch in this evidence batch.",
                }
            )
            continue

        before = deepcopy(scanner)
        patched = patch_strategy_scanner(
            db,
            name=strategy.name,
            scanner_patch=patch,
            dry_run=dry_run,
        )
        after_config = (
            patched.config.get("scanner")
            if isinstance(patched.config, dict)
            and isinstance(patched.config.get("scanner"), dict)
            else {}
        )
        results.append(
            {
                "name": strategy.name,
                "scanner_type": scanner_type,
                "status": "would_update" if dry_run else "updated",
                "patch": patch,
                "changed": _changed_scanner_keys(before, after_config, patch),
            }
        )
    return results


def _changed_scanner_keys(
    before: dict[str, Any],
    after: dict[str, Any],
    patch: dict[str, Any],
    *,
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for key, patch_value in patch.items():
        path = f"{prefix}.{key}" if prefix else key
        before_value = before.get(key)
        after_value = after.get(key)
        if isinstance(patch_value, dict):
            nested_before = before_value if isinstance(before_value, dict) else {}
            nested_after = after_value if isinstance(after_value, dict) else {}
            changes.update(
                _changed_scanner_keys(
                    nested_before,
                    nested_after,
                    patch_value,
                    prefix=path,
                )
            )
        elif before_value != after_value:
            changes[path] = {"from": before_value, "to": after_value}
    return changes


def _strategy_summary(strategy: Strategy) -> dict[str, Any]:
    config = strategy.config if isinstance(strategy.config, dict) else {}
    scanner = config.get("scanner") if isinstance(config.get("scanner"), dict) else {}
    preview = scanner.get("preview") if isinstance(scanner.get("preview"), dict) else {}
    submit = scanner.get("submit") if isinstance(scanner.get("submit"), dict) else {}
    exit_config = scanner.get("exit") if isinstance(scanner.get("exit"), dict) else {}
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "is_active": strategy.is_active,
        "scanner_type": scanner.get("type"),
        "scanner_symbols": scanner.get("symbols", []),
        "preview_enabled": preview.get("enabled") is True,
        "submit_enabled": submit.get("enabled") is True,
        "exit_enabled": exit_config.get("enabled") is True,
        "scanner": scanner,
        "updated_at": strategy.updated_at,
    }


def _strategy_audit_payload(strategy: Strategy) -> dict[str, Any]:
    return {
        "name": strategy.name,
        "description": strategy.description,
        "is_active": strategy.is_active,
        "config": strategy.config,
    }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _json_object(raw_json: str) -> dict[str, Any]:
    value = json.loads(raw_json)
    if not isinstance(value, dict):
        raise RuntimeError("scanner JSON must be an object")
    return value


def _money_string(value: str | int | Decimal) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _price_for_symbol(symbol: str, *, sample_price: str | None) -> Decimal:
    if sample_price is not None:
        return Decimal(sample_price)

    client = AlpacaMarketDataClient.from_settings()
    latest_quote = client.get_latest_stock_quotes([symbol], feed="iex").get(symbol)
    if latest_quote is None:
        raise RuntimeError(f"No latest stock quote returned for {symbol}")

    bid_price = _usable_quote_price(latest_quote.quote.bid_price)
    ask_price = _usable_quote_price(latest_quote.quote.ask_price)
    if bid_price is not None and ask_price is not None:
        return (bid_price + ask_price) / Decimal("2")
    if ask_price is not None:
        return ask_price
    if bid_price is not None:
        return bid_price
    raise RuntimeError(f"Latest stock quote for {symbol} had no bid or ask")


def _usable_quote_price(value: Decimal | None) -> Decimal | None:
    if value is None or value <= Decimal("0"):
        return None
    return value


def _whole_dollar(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


if __name__ == "__main__":
    main()
