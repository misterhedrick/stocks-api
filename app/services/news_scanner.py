from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import JobRun, PositionSnapshot
from app.services.audit_logs import record_audit_log


@dataclass(slots=True)
class NewsItem:
    title: str
    url: str | None
    source: str | None
    published_at: str | None
    impact_keywords: list[str]


@dataclass(slots=True)
class NewsScanResult:
    job_run: JobRun
    market_items: list[dict[str, Any]]
    ticker_items: dict[str, list[dict[str, Any]]]
    owned_symbols: list[str]
    sources_checked: int
    errors: list[str]


IMPACT_KEYWORDS = (
    "fed",
    "federal reserve",
    "rate",
    "rates",
    "inflation",
    "cpi",
    "ppi",
    "jobs report",
    "unemployment",
    "gdp",
    "earnings",
    "guidance",
    "downgrade",
    "upgrade",
    "lawsuit",
    "sec",
    "fda",
    "merger",
    "acquisition",
    "tariff",
    "war",
    "oil",
    "yields",
    "volatility",
)
OPTION_SYMBOL_PATTERN = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")


class NewsFetchError(RuntimeError):
    pass


class RssNewsClient:
    def __init__(self, *, timeout_seconds: int) -> None:
        self._timeout_seconds = timeout_seconds

    def fetch(self, url: str, *, limit: int) -> list[NewsItem]:
        try:
            with httpx.Client(timeout=self._timeout_seconds, follow_redirects=True) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise NewsFetchError(f"Unable to fetch news feed: {exc}") from exc

        if response.is_error:
            raise NewsFetchError(
                f"News feed returned HTTP {response.status_code}: {url}"
            )

        return _parse_rss_items(response.text, limit=limit)


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
                errors.append(f"{symbol}: {exc}")
                ticker_items[symbol] = []

        details = {
            "market_items": _dedupe_items(market_items, limit=market_limit),
            "ticker_items": {
                symbol: _dedupe_items(items, limit=ticker_limit)
                for symbol, items in ticker_items.items()
            },
            "owned_symbols": owned_symbols,
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


def _parse_rss_items(xml_text: str, *, limit: int) -> list[NewsItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise NewsFetchError("News feed returned invalid XML") from exc

    items: list[NewsItem] = []
    for item in root.findall(".//item"):
        title = _element_text(item, "title")
        if not title:
            continue
        url = _element_text(item, "link")
        source = _element_text(item, "source")
        published_at = _published_at(_element_text(item, "pubDate"))
        items.append(
            NewsItem(
                title=title,
                url=url,
                source=source,
                published_at=published_at,
                impact_keywords=_impact_keywords(title),
            )
        )
        if len(items) >= limit:
            break
    return items


def _element_text(item: ET.Element, tag_name: str) -> str | None:
    element = item.find(tag_name)
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None


def _published_at(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _impact_keywords(title: str) -> list[str]:
    lower_title = title.lower()
    return [keyword for keyword in IMPACT_KEYWORDS if keyword in lower_title]


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
