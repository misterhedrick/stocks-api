from __future__ import annotations

from dataclasses import dataclass

from datetime import timedelta

from typing import Any

import re

from app.db.models import JobRun

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
