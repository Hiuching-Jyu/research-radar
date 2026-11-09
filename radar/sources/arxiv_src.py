"""arXiv API: one query per topic (categories AND keywords), newest first."""
from __future__ import annotations

import logging
import re
import time

from .. import http
from ..models import Item

log = logging.getLogger(__name__)
API = "https://export.arxiv.org/api/query"


def fetch(cfg) -> tuple[list[Item], int]:
    conf = cfg.settings["arxiv"]
    if not cfg.arxiv_categories:
        raise ValueError("arxiv_categories is not set in topics.yaml")
    cats = " OR ".join(f"cat:{c}" for c in cfg.arxiv_categories)
    items, errors = [], 0
    for i, topic in enumerate(cfg.topics):
        kws = topic.get("arxiv_keywords") or []
        if not kws:
            continue
        if i:
            time.sleep(3)  # arXiv asks for >= 3 s between requests
        query = f"({cats}) AND ({' OR '.join(f'all:\"{k}\"' for k in kws)})"
        try:
            feed = http.get_feed(API, params={
                "search_query": query, "sortBy": "submittedDate",
                "sortOrder": "descending", "max_results": conf["max_per_topic"],
            })
        except Exception as e:
            errors += 1
            log.warning("arxiv: topic #%d failed: %s", i + 1, http.describe(e))
            continue
        for e in feed.entries:
            items.append(Item(
                title=http.clean_text(e.title, 300),
                url=re.sub(r"v\d+$", "", e.id).replace("http://", "https://"),
                source="arXiv",
                published_at=http.entry_time(e),
                snippet=http.clean_text(e.get("summary", ""), 700),
                type="paper", kind="arxiv",
            ))
    return items, errors
