from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Item:
    title: str
    url: str
    source: str                 # display name, e.g. "arXiv" or a publisher
    published_at: datetime | None
    snippet: str
    type: str                   # paper / news / repo
    kind: str                   # source category, matches settings.source_priority
    pinned: bool = False        # explicitly tracked repos: always pushed
    skip_time_filter: bool = False
    # filled in by the LLM
    relevance: int = 0
    topic: str = ""
    summary: str = ""
    event: str = ""
