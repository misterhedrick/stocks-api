from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
import re
import urllib.parse
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

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
    risk_assessment: dict[str, Any]
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
HIGH_RISK_KEYWORDS = {
    "war",
    "tariff",
    "lawsuit",
    "sec",
    "fda",
    "downgrade",
    "volatility",
    "recession",
    "oil",
}
TRUSTED_NEWS_SOURCES = {
    "associated press",
    "barron's",
    "bloomberg",
    "cnbc",
    "investopedia",
    "marketwatch",
    "reuters",
    "the wall street journal",
    "yahoo finance",
}
LOW_QUALITY_SOURCE_TERMS = {
    "fathom",
    "moomoo",
}
LOW_QUALITY_TITLE_TERMS = {
    "options chain",
    "quotes & news",
    "swing & day trading",
}
MARKET_BLOCK_LOOKBACK = timedelta(hours=72)
TICKER_REVIEW_LOOKBACK = timedelta(hours=72)
MEDIUM_RISK_KEYWORDS = {
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
    "upgrade",
    "merger",
    "acquisition",
    "yields",
}
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


def assess_news_risk(
    *,
    market_items: list[dict[str, Any]],
    ticker_items: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    scored_market_items = [
        _scored_item(item, scope="market")
        for item in market_items
    ]
    market_keyword_hits = _qualified_keyword_hits(scored_market_items)
    ticker_risks: dict[str, dict[str, Any]] = {}
    high_risk_symbols: list[str] = []
    medium_risk_symbols: list[str] = []

    for symbol, items in ticker_items.items():
        scored_items = [
            _scored_item(item, scope="ticker")
            for item in items
        ]
        hits = _qualified_keyword_hits(scored_items)
        level = _risk_level_from_hits(hits)
        ticker_risks[symbol] = {
            "risk_level": level,
            "impact_keywords": sorted(hits),
            "reasons": _risk_reasons(symbol, scored_items, hits),
            "review_items": _review_items(scored_items),
        }
        if level == "high":
            high_risk_symbols.append(symbol)
        elif level == "medium":
            medium_risk_symbols.append(symbol)

    market_risk_level = _risk_level_from_hits(market_keyword_hits)
    blocking_reasons = _blocking_reasons(scored_market_items, market_keyword_hits)
    should_block_new_entries = bool(blocking_reasons)
    manual_review_symbols = sorted(set(high_risk_symbols + medium_risk_symbols))

    return {
        "version": "news_risk_v2",
        "market_risk_level": market_risk_level,
        "market_impact_keywords": sorted(market_keyword_hits),
        "should_block_new_entries": should_block_new_entries,
        "blocking_reasons": blocking_reasons,
        "manual_review_symbols": manual_review_symbols,
        "ticker_risks": ticker_risks,
        "reasons": _risk_reasons("market", scored_market_items, market_keyword_hits),
        "ignored_items": _ignored_items(scored_market_items)
        + [
            ignored
            for symbol, items in ticker_items.items()
            for ignored in _ignored_items(
                [_scored_item(item, scope="ticker") for item in items],
                label=symbol,
            )
        ],
    }


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


def _keyword_hits(items: list[dict[str, Any]]) -> set[str]:
    hits: set[str] = set()
    for item in items:
        keywords = item.get("impact_keywords")
        if not isinstance(keywords, list):
            continue
        hits.update(
            keyword
            for keyword in keywords
            if isinstance(keyword, str) and keyword.strip()
        )
    return hits


def _qualified_keyword_hits(items: list[dict[str, Any]]) -> set[str]:
    hits: set[str] = set()
    for item in items:
        if not item.get("risk_qualified"):
            continue
        hits.update(
            keyword
            for keyword in item.get("impact_keywords", [])
            if isinstance(keyword, str) and keyword.strip()
        )
    return hits


def _risk_level_from_hits(hits: set[str]) -> str:
    if hits & HIGH_RISK_KEYWORDS:
        return "high"
    if hits & MEDIUM_RISK_KEYWORDS:
        return "medium"
    return "low"


def _risk_reasons(
    label: str,
    items: list[dict[str, Any]],
    hits: set[str],
) -> list[str]:
    if not hits:
        return []
    reasons: list[str] = []
    for item in items:
        keywords = item.get("impact_keywords")
        if not isinstance(keywords, list) or not set(keywords) & hits:
            continue
        title = item.get("title")
        if isinstance(title, str) and title.strip():
            reasons.append(f"{label}: {title.strip()}")
        if len(reasons) >= 5:
            break
    return reasons


def _blocking_reasons(
    scored_market_items: list[dict[str, Any]],
    market_keyword_hits: set[str],
) -> list[str]:
    high_risk_hits = market_keyword_hits & HIGH_RISK_KEYWORDS
    if not high_risk_hits:
        return []

    high_risk_items = [
        item
        for item in scored_market_items
        if item.get("risk_qualified")
        and set(item.get("impact_keywords", [])) & high_risk_hits
    ]
    trusted_high_risk_items = [
        item
        for item in high_risk_items
        if item.get("source_quality") == "trusted"
    ]

    if len(trusted_high_risk_items) >= 2:
        return _risk_reasons("market", trusted_high_risk_items, high_risk_hits)
    if len(high_risk_items) >= 3:
        return _risk_reasons("market", high_risk_items, high_risk_hits)
    return []


def _review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": item.get("title"),
            "source": item.get("source"),
            "published_at": item.get("published_at"),
            "impact_keywords": item.get("impact_keywords", []),
            "source_quality": item.get("source_quality"),
            "freshness": item.get("freshness"),
        }
        for item in items
        if item.get("risk_qualified")
    ][:5]


def _ignored_items(
    items: list[dict[str, Any]],
    *,
    label: str = "market",
) -> list[dict[str, Any]]:
    ignored = []
    for item in items:
        if item.get("risk_qualified"):
            continue
        reason = item.get("risk_filter_reason")
        if not reason:
            continue
        ignored.append(
            {
                "scope": label,
                "title": item.get("title"),
                "source": item.get("source"),
                "reason": reason,
            }
        )
    return ignored[:10]


def _scored_item(item: dict[str, Any], *, scope: str) -> dict[str, Any]:
    scored = dict(item)
    title = str(scored.get("title") or "")
    source = str(scored.get("source") or "")
    source_quality = _source_quality(source)
    freshness = _freshness(scored.get("published_at"), scope=scope)
    filter_reason = _risk_filter_reason(
        title=title,
        source=source,
        source_quality=source_quality,
        freshness=freshness,
    )
    scored["source_quality"] = source_quality
    scored["freshness"] = freshness
    scored["risk_qualified"] = filter_reason is None
    scored["risk_filter_reason"] = filter_reason
    return scored


def _source_quality(source: str) -> str:
    lower_source = source.strip().lower()
    if not lower_source:
        return "unknown"
    if lower_source in TRUSTED_NEWS_SOURCES:
        return "trusted"
    if any(term in lower_source for term in LOW_QUALITY_SOURCE_TERMS):
        return "low"
    return "standard"


def _freshness(published_at: object, *, scope: str) -> str:
    if not isinstance(published_at, str) or not published_at.strip():
        return "unknown"
    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    lookback = MARKET_BLOCK_LOOKBACK if scope == "market" else TICKER_REVIEW_LOOKBACK
    return "fresh" if age <= lookback else "stale"


def _risk_filter_reason(
    *,
    title: str,
    source: str,
    source_quality: str,
    freshness: str,
) -> str | None:
    lower_title = title.lower()
    if source_quality == "low":
        return f"low-quality source: {source}"
    if any(term in lower_title for term in LOW_QUALITY_TITLE_TERMS):
        return "low-quality ticker/news result"
    if freshness == "stale":
        return "stale headline"
    return None


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
