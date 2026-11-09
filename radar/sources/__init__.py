"""Source registry. To add a source: create a module that implements
fetch(cfg) -> (items, number_of_failed_requests) and register it here."""
from . import arxiv_src, github_search, google_news, hackernews, rss

# name -> (fetch function, run mode it belongs to: papers / news)
SOURCES = {
    "arxiv": (arxiv_src.fetch, "papers"),
    "google_news": (google_news.fetch, "news"),
    "rss": (rss.fetch, "news"),
    "hackernews": (hackernews.fetch, "news"),
    "github": (github_search.fetch, "news"),
}
