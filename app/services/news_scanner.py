from __future__ import annotations

from app.services.news_scanner_client import (
    RssNewsClient,
    _element_text,
    _impact_keywords,
    _parse_rss_items,
    _published_at,
)
from app.services.news_scanner_core import (
    _dedupe_items,
    _market_feed_urls,
    _news_symbol,
    _owned_symbols,
    _ticker_feed_url,
    scan_market_news,
)
from app.services.news_scanner_risk import (
    _blocking_reasons,
    _freshness,
    _ignored_items,
    _keyword_hits,
    _qualified_keyword_hits,
    _review_items,
    _risk_filter_reason,
    _risk_level_from_hits,
    _risk_reasons,
    _scored_item,
    _source_quality,
    assess_news_risk,
)
from app.services.news_scanner_types import (
    HIGH_RISK_KEYWORDS,
    IMPACT_KEYWORDS,
    LOW_QUALITY_SOURCE_TERMS,
    LOW_QUALITY_TITLE_TERMS,
    MARKET_BLOCK_LOOKBACK,
    MEDIUM_RISK_KEYWORDS,
    OPTION_SYMBOL_PATTERN,
    TICKER_REVIEW_LOOKBACK,
    TRUSTED_NEWS_SOURCES,
    NewsFetchError,
    NewsItem,
    NewsScanResult,
)
