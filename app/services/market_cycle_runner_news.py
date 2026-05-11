from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services.market_cycle_helpers import (
    _disabled_step,
    _elapsed_seconds,
    _phase_budget_exceeded,
    _timeout_step,
)

logger = logging.getLogger("app.services.market_cycle_runner")


def _run_news_phase(
    db: Session,
    *,
    news_enabled: bool,
    cycle_started: float,
    phase_timeout: int,
    timings: dict[str, float],
    scan_market_news_fn: Callable[[Session], Any],
) -> tuple[dict[str, Any] | None, bool]:
    if news_enabled and not _phase_budget_exceeded(cycle_started, phase_timeout):
        step_started = perf_counter()
        logger.info("market_cycle phase=news starting")
        news_result = scan_market_news_fn(db)
        elapsed = _elapsed_seconds(step_started)
        timings["news_seconds"] = elapsed
        news = {
            "job_run_id": str(news_result.job_run.id),
            "market_items": news_result.market_items,
            "ticker_items": news_result.ticker_items,
            "owned_symbols": news_result.owned_symbols,
            "risk_assessment": news_result.risk_assessment,
            "sources_checked": news_result.sources_checked,
            "errors": news_result.errors,
        }
        risk_assessment = news_result.risk_assessment
        blocks_entries = (
            isinstance(risk_assessment, dict)
            and risk_assessment.get("should_block_new_entries") is True
        )
        logger.info(
            "market_cycle phase=news done: elapsed=%.3fs sources=%d blocks_entries=%s errors=%d",
            elapsed,
            news_result.sources_checked,
            blocks_entries,
            len(news_result.errors),
        )
        return news, blocks_entries

    timings["news_seconds"] = 0.0
    if news_enabled:
        logger.warning(
            "market_cycle phase=news skipped: runtime budget reached at %.3fs (limit=%ds)",
            _elapsed_seconds(cycle_started),
            phase_timeout,
        )
        return _timeout_step("news", phase_timeout), False

    return _disabled_step("news"), False
