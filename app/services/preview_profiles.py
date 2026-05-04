from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class PreviewProfileLimits:
    profile: str | None
    max_estimated_notional: Decimal | None
    max_spread: Decimal | None
    max_spread_percent: Decimal | None
    min_open_interest: int | None


def profile_for_strategy_config(strategy_config: dict[str, Any] | None) -> str | None:
    if not isinstance(strategy_config, dict):
        return None
    scanner = strategy_config.get("scanner")
    if not isinstance(scanner, dict):
        return None
    preview = scanner.get("preview")
    if isinstance(preview, dict):
        profile = preview.get("profile") or preview.get("preview_profile")
        if isinstance(profile, str) and profile.strip():
            return profile.strip()
    scanner_type = scanner.get("type")
    if isinstance(scanner_type, str) and scanner_type.strip():
        return scanner_type.strip()
    return None


def resolve_preview_profile_limits(
    profile: str | None,
    *,
    max_estimated_notional: Decimal | None,
    max_spread: Decimal | None,
    max_spread_percent: Decimal | None,
    min_open_interest: int | Decimal | None,
) -> PreviewProfileLimits:
    if not settings.paper_strategy_preview_profiles_enabled:
        return PreviewProfileLimits(
            profile=profile,
            max_estimated_notional=max_estimated_notional,
            max_spread=max_spread,
            max_spread_percent=max_spread_percent,
            min_open_interest=_int_or_none(min_open_interest),
        )

    return PreviewProfileLimits(
        profile=profile,
        max_estimated_notional=settings.preview_profile_decimal(
            profile,
            "MAX_ESTIMATED_NOTIONAL",
            max_estimated_notional or settings.paper_strategy_max_estimated_notional,
        ),
        max_spread=settings.preview_profile_decimal(
            profile,
            "MAX_SPREAD",
            max_spread or settings.paper_strategy_max_spread,
        ),
        max_spread_percent=settings.preview_profile_decimal(
            profile,
            "MAX_SPREAD_PERCENT",
            max_spread_percent or settings.paper_strategy_max_spread_percent,
        ),
        min_open_interest=settings.preview_profile_int(
            profile,
            "MIN_OPEN_INTEREST",
            _int_or_none(min_open_interest) or settings.paper_strategy_min_open_interest,
        ),
    )


def _int_or_none(value: int | Decimal | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
