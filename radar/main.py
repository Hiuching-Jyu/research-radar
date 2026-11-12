"""Entry point: fetch -> time filter -> dedupe -> LLM filter -> push to Telegram -> record.

Usage:
  python -m radar.main --mode all --dry-run          # print only; nothing sent, no state written
  python -m radar.main --mode news                   # papers | news | all
  python -m radar.main --dry-run --skip-llm          # just check what each source returns
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from . import config, dedup, http, telegram
from .llm import Scorer
from .sources import SOURCES

log = logging.getLogger("radar")


def collect(cfg, mode: str, only: set[str] | None):
    items, report = [], []
    for name, (fetch, group) in SOURCES.items():
        if (mode != "all" and group != mode) or (only and name not in only):
            continue
        t0 = time.time()
        try:
            got, errors = fetch(cfg)
            status = "ok" if not errors else f"{errors} request(s) failed"
        except Exception as e:  # one broken source must not take down the others
            log.warning("source %s failed: %s", name, http.describe(e))
            got, status = [], f"failed: {http.describe(e)}"
        items += got
        report.append((name, len(got), status, time.time() - t0))
    return items, report


def load_dotenv() -> None:
    """Local runs read secrets from .env in the project root (.env is git-ignored)."""
    path = config.ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Research Radar")
    ap.add_argument("--mode", choices=["papers", "news", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="print only: no Telegram message, no state written")
    ap.add_argument("--skip-llm", action="store_true", help="skip the LLM; only report fetch and dedupe counts")
    ap.add_argument("--sources", help="only run these sources, comma separated, e.g. arxiv,rss")
    ap.add_argument("--show-all", action="store_true", help="with --dry-run, also list items below the threshold")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")

    load_dotenv()
    cfg = config.load()
    http.configure(cfg.settings)
    s = cfg.settings
    priority = s["source_priority"]

    # 1. fetch
    items, report = collect(cfg, args.mode, set(args.sources.split(",")) if args.sources else None)
    print("\n== sources ==")
    for name, n, status, secs in report:
        print(f"  {name:<12} {n:>5} items  {status}  ({secs:.0f}s)")

    # 2. time filter
    now = datetime.now(timezone.utc)
    fresh = [
        i for i in items
        if i.skip_time_filter
        or (i.published_at and i.published_at >= now - timedelta(hours=cfg.lookback(i.type)))
    ]
    # 3. dedupe (within this run + already sent + already scored)
    state = dedup.State(s["dedup"]["sent_retention_days"])
    thr = s["dedup"]["title_similarity"]
    unique = dedup.dedupe(fresh, thr, priority)
    candidates = [i for i in unique if not state.is_sent(i, thr) and not state.is_seen(i)]
    print(f"\nfetched {len(items)} -> recent {len(fresh)} -> unique {len(unique)} -> new {len(candidates)}")

    cap = s["llm"]["max_candidates"]
    if len(candidates) > cap:
        log.warning("%d candidates exceed the cap of %d; scoring the newest %d, the rest wait for the next run",
                    len(candidates), cap, cap)
        candidates.sort(key=lambda i: (not i.pinned, -(i.published_at or now).timestamp()))
        candidates = candidates[:cap]

    if args.skip_llm:
        for i in candidates[:40]:
            print(f"  [{i.type}] {i.source}: {i.title[:90]}")
        return 0

    # 4. LLM scoring
    scorer = Scorer(cfg) if candidates else None
    scored = scorer.score(candidates) if scorer else []
    if candidates and not scored:
        log.error("every LLM batch failed; nothing pushed (check the API key, model name and network)")
        if not args.dry_run:
            try:  # tell the reader instead of going silent (e.g. the LLM account needs a top-up)
                telegram.send([telegram.failure_message(scorer.provider, scorer.model, scorer.last_error)])
            except Exception as e:
                log.error("could not send the failure alert: %s", e)
        return 1
    threshold = s["relevance_threshold"]
    for i in scored:
        if i.pinned:  # tracked repos are always pushed
            i.relevance = max(i.relevance, threshold)
            i.summary = i.summary or re.sub(r"\s+", " ", i.snippet)[:80]
    passed = [i for i in scored if i.relevance >= threshold and i.summary]
    passed = dedup.dedupe(passed, s["dedup"]["merge_similarity"], priority, use_event=True)
    ranked = scorer.curate(passed) if scorer else []       # merge same-event reports + rank
    ranked.sort(key=lambda i: not i.pinned)                # tracked repos first (stable sort)
    n_feat, n_more = s["digest"]["featured"], s["digest"]["more"]
    featured, more = ranked[:n_feat], ranked[n_feat:n_feat + n_more]
    print(f"scored {len(scored)} -> {len(passed)} at or above {threshold} -> {len(ranked)} after merging"
          f" -> {len(featured)} featured + {len(more)} more")

    # 5. push
    if featured:
        messages = telegram.build_messages(featured, more, cfg)
    elif s.get("notify_when_empty"):
        messages = [telegram.empty_message(cfg)]
    else:
        messages = []

    if args.dry_run:
        print(f"\n== dry run: {len(messages)} Telegram message(s) would be sent ==")
        for m in messages:
            print("-" * 60)
            print(m)
        if args.show_all:
            print("\n== below the threshold ==")
            for i in sorted(scored, key=lambda i: -i.relevance):
                if i.relevance < threshold:
                    print(f"  {i.relevance:>2} [{i.type}] {i.source}: {i.title[:90]}")
        return 0

    if messages:
        telegram.send(messages)
        log.info("pushed %d message(s): %d featured + %d more", len(messages), len(featured), len(more))
    # 6. record (only after a successful send)
    state.mark_seen(scored)
    state.mark_sent(featured + more)
    state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
