"""GitHub Search API: recently created repos that already have a few stars."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from .. import http
from ..models import Item

log = logging.getLogger(__name__)
API = "https://api.github.com/search/repositories"


def fetch(cfg) -> tuple[list[Item], int]:
    conf = cfg.settings["github_search"]
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    delay = 2.2 if token else 6.5  # search rate limit: 30/min with a token, 10/min without
    since = (datetime.now(timezone.utc) - timedelta(days=conf["created_within_days"])).date()
    items, errors, first = [], 0, True
    for topic in cfg.topics:
        for kw in (topic.get("arxiv_keywords") or [])[: conf["keywords_per_topic"]]:
            if not first:
                time.sleep(delay)
            first = False
            q = f'"{kw}" in:name,description,readme created:>={since} stars:>={conf["min_stars"]}'
            try:
                repos = http.get(API, headers=headers, params={
                    "q": q, "sort": "stars", "order": "desc", "per_page": conf["max_per_query"],
                }).json()["items"]
            except Exception as e:
                errors += 1
                log.warning("github: a query failed: %s", http.describe(e))
                continue
            for r in repos:
                items.append(Item(
                    title=r["full_name"], url=r["html_url"], source="GitHub",
                    published_at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
                    snippet=f"{r.get('description') or ''} ({r['stargazers_count']} stars, {r.get('language') or '?'})",
                    type="repo", kind="github", skip_time_filter=True,
                ))
    return items, errors
