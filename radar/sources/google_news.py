"""Google News RSS: one search feed per query, in the English and Chinese editions."""
from __future__ import annotations

import logging
import math
import time

from .. import http
from ..models import Item

log = logging.getLogger(__name__)
URL = "https://news.google.com/rss/search"
LOCALES = {
    "queries_en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "queries_zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
}


def fetch(cfg) -> tuple[list[Item], int]:
    delay = cfg.settings["http"].get("google_news_delay", 1.5)
    days = max(1, math.ceil(cfg.lookback("news") / 24))
    items, errors, first = [], 0, True
    for topic in cfg.topics:
        for field, locale in LOCALES.items():
            for q in topic.get(field) or []:
                if not first:
                    time.sleep(delay)
                first = False
                try:
                    feed = http.get_feed(URL, params={"q": f"{q} when:{days}d", **locale})
                except Exception as e:
                    errors += 1
                    log.warning("google_news: a query failed: %s", http.describe(e))
                    continue
                for e in feed.entries:
                    publisher = (e.get("source") or {}).get("title", "")
                    title = e.title
                    if publisher and title.endswith(f" - {publisher}"):
                        title = title[: -len(publisher) - 3]
                    items.append(Item(
                        title=title.strip(), url=e.link,
                        source=publisher or "Google News",
                        published_at=http.entry_time(e),
                        snippet=f"(search query: {q})",
                        type="news", kind="google_news",
                    ))
    return items, errors
