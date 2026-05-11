from __future__ import annotations

from datetime import timezone

from email.utils import parsedate_to_datetime

import xml.etree.ElementTree as ET

import httpx

from app.services.news_scanner_types import IMPACT_KEYWORDS, NewsFetchError, NewsItem

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
