"""Shared HTTP helpers: timeout, User-Agent, feed parsing."""
from __future__ import annotations

import calendar
import html
import re
from datetime import datetime, timezone

import feedparser
import requests

_session = requests.Session()
_timeout = 20


def configure(settings: dict) -> None:
    global _timeout
    http = settings.get("http", {})
    _timeout = http.get("timeout", 20)
    _session.headers["User-Agent"] = http.get("user_agent", "research-radar/0.1")


def user_agent() -> str:
    return _session.headers["User-Agent"]


def get(url: str, *, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    resp = _session.get(url, params=params, headers=headers, timeout=_timeout)
    resp.raise_for_status()
    return resp


def get_feed(url: str, *, params: dict | None = None):
    feed = feedparser.parse(get(url, params=params).content)
    if feed.bozo and not feed.entries:
        raise ValueError("could not parse feed")
    return feed


def entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def clean_text(text: str, limit: int = 600) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def describe(e: Exception) -> str:
    """Short description of an error with no URL or query in it
    (Actions logs are public on a public repo)."""
    resp = getattr(e, "response", None)
    if resp is not None:
        return f"HTTP {resp.status_code}"
    return type(e).__name__
