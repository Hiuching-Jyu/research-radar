"""Hacker News via the Algolia search API: recent stories whose title matches a query."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .. import http
from ..models import Item

log = logging.getLogger(__name__)
API = "https://hn.algolia.com/api/v1/search_by_date"


def fetch(cfg) -> tuple[list[Item], int]:
    conf = cfg.settings["hackernews"]
    since = int(time.time()) - conf["lookback_hours"] * 3600
    items, errors = [], 0
    for topic in cfg.topics:
        for q in topic.get("queries_en") or []:
            try:
                hits = http.get(API, params={
                    "query": q, "tags": "story", "hitsPerPage": 20,
                    "restrictSearchableAttributes": "title",  # body matches are too noisy
                    "numericFilters": f"created_at_i>{since}",
                }).json()["hits"]
            except Exception as e:
                errors += 1
                log.warning("hackernews: a query failed: %s", http.describe(e))
                continue
            for h in hits:
                if not h.get("title") or (h.get("points") or 0) < conf["min_points"]:
                    continue
                items.append(Item(
                    title=h["title"],
                    url=h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                    source="Hacker News",
                    published_at=datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
                    snippet=http.clean_text(h.get("story_text") or f"(search query: {q})"),
                    type="news", kind="hackernews", skip_time_filter=True,
                ))
            time.sleep(0.3)
    return items, errors
