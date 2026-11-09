"""Fixed RSS/Atom feeds from feeds.yaml: media, company blogs, tracked repos."""
from __future__ import annotations

import logging

from .. import http
from ..models import Item

log = logging.getLogger(__name__)


def fetch(cfg) -> tuple[list[Item], int]:
    items, errors = [], 0
    for kind in ("media", "company_blogs", "tracked_repos"):
        for feed_conf in cfg.feeds.get(kind) or []:
            try:
                feed = http.get_feed(feed_conf["url"])
            except Exception as e:
                errors += 1
                log.warning("rss: a feed failed: %s", http.describe(e))
                continue
            tracked = kind == "tracked_repos"
            for e in feed.entries:
                title = http.clean_text(e.get("title", ""), 300)
                if not title or not e.get("link"):
                    continue
                items.append(Item(
                    title=f"{feed_conf['name']}: {title}" if tracked else title,
                    url=e.link, source=feed_conf["name"],
                    published_at=http.entry_time(e),
                    snippet=http.clean_text(e.get("summary", "")),
                    type="repo" if tracked else "news",
                    kind=kind, pinned=tracked,
                ))
    return items, errors
