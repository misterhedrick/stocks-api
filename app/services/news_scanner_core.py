from __future__ import annotations

import logging

from dataclasses import asdict

from datetime import datetime, timezone

from typing import Any

import urllib.parse

from sqlalchemy import and_, func, select

from sqlalchemy.orm import Session

from app.core.config import settings

from app.db.models import JobRun, PositionSnapshot

from app.services.audit_logs import record_audit_log

from app.services.news_scanner_client import RssNewsClient

from app.services.news_scanner_risk import assess_news_risk

from app.services.news_scanner_types import NewsFetchError, NewsScanResult, OPTION_SYMBOL_PATTERN

logger = logging.getLogger("app.services.news_scanner")

def scan_market_news(
    db: Session,
    *,
    market_limit: int = 10,
    ticker_limit: int = 5,
    client: RssNewsClient | None = None,
) -> NewsScanResult:
    started_at = datetime.now(timezone.utc)
    job_run = JobRun(
        job_name="news_scan",
        status="running",
        started_at=started_at,
        details={},
    )
    db.add(job_run)
    db.flush()

    try:
        news_client = client or RssNewsClient(
            timeout_seconds=settings.news_request_timeout_seconds
        )
        errors: list[str] = []
        sources_checked = 0
        market_items: list[dict[str, Any]] = []

        for feed_url in _market_feed_urls():
            sources_checked += 1
            try:
                market_items.extend(
                    asdict(item)
                    for item in news_client.fetch(feed_url, limit=market_limit)
                )
            except NewsFetchError as exc:
                logger.warning("Market news feed fetch failed (%s): %s", feed_url, exc)
                errors.append(str(exc))

        owned_symbols = _owned_symbols(db)
        ticker_items: dict[str, list[dict[str, Any]]] = {}
        for symbol in owned_symbols:
            sources_checked += 1
            try:
                ticker_items[symbol] = [
                    asdict(item)
                    for item in news_client.fetch(
                        _ticker_feed_url(symbol),
                        limit=ticker_limit,
                    )
                ]
            except NewsFetchError as exc:
                logger.warning("Ticker news feed fetch failed (%s): %s", symbol, exc)
                errors.append(f"{symbol}: {exc}")
                ticker_items[symbol] = []

        deduped_market_items = _dedupe_items(market_items, limit=market_limit)
        deduped_ticker_items = {
                symbol: _dedupe_items(items, limit=ticker_limit)
                for symbol, items in ticker_items.items()
            }
        risk_assessment = assess_news_risk(
            market_items=deduped_market_items,
            ticker_items=deduped_ticker_items,
        )

        details = {
            "market_items": deduped_market_items,
            "ticker_items": deduped_ticker_items,
            "owned_symbols": owned_symbols,
            "risk_assessment": risk_assessment,
            "sources_checked": sources_checked,
            "errors": errors,
        }
        job_run.status = "succeeded"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = details
        job_run.error = None
        db.add(job_run)
        record_audit_log(
            db,
            event_type="news_scan.succeeded",
            entity_type="job_run",
            entity_id=job_run.id,
            message="News scan succeeded",
            payload=details,
        )
        db.commit()
        db.refresh(job_run)

        return NewsScanResult(job_run=job_run, **details)
    except Exception as exc:
        db.rollback()
        job_run.status = "failed"
        job_run.finished_at = datetime.now(timezone.utc)
        job_run.details = {}
        job_run.error = f"{exc.__class__.__name__}: {exc}"
        db.add(job_run)
        record_audit_log(
            db,
            event_type="news_scan.failed",
            entity_type="job_run",
            entity_id=job_run.id,
            message="News scan failed",
            payload={"error": job_run.error},
        )
        db.commit()
        db.refresh(job_run)
        raise

def _market_feed_urls() -> list[str]:
    return [
        url.strip()
        for url in settings.news_market_rss_feeds.split(",")
        if url.strip()
    ]

def _ticker_feed_url(symbol: str) -> str:
    quoted_symbol = urllib.parse.quote(symbol, safe="")
    return settings.news_ticker_rss_template.format(symbol=quoted_symbol)

def _owned_symbols(db: Session) -> list[str]:
    latest_captured_at = (
        select(
            PositionSnapshot.symbol.label("symbol"),
            func.max(PositionSnapshot.captured_at).label("captured_at"),
        )
        .group_by(PositionSnapshot.symbol)
        .subquery()
    )
    statement = (
        select(PositionSnapshot.symbol)
        .join(
            latest_captured_at,
            and_(
                PositionSnapshot.symbol == latest_captured_at.c.symbol,
                PositionSnapshot.captured_at == latest_captured_at.c.captured_at,
            ),
        )
        .where(PositionSnapshot.quantity > 0)
        .order_by(PositionSnapshot.symbol.asc())
    )
    owned_symbols = {
        _news_symbol(str(symbol).upper())
        for symbol in db.scalars(statement)
    }
    return sorted(symbol for symbol in owned_symbols if symbol)

def _dedupe_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or item.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped

def _news_symbol(symbol: str) -> str:
    match = OPTION_SYMBOL_PATTERN.match(symbol)
    if match is not None:
        return match.group(1)
    return symbol
