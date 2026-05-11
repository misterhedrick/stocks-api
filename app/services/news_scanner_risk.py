from __future__ import annotations

from datetime import datetime, timezone

from typing import Any

from app.services.news_scanner_types import (
    HIGH_RISK_KEYWORDS,
    LOW_QUALITY_SOURCE_TERMS,
    LOW_QUALITY_TITLE_TERMS,
    MARKET_BLOCK_LOOKBACK,
    MEDIUM_RISK_KEYWORDS,
    TICKER_REVIEW_LOOKBACK,
    TRUSTED_NEWS_SOURCES,
)

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
