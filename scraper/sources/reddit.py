import os
import re
import time
import sqlite3
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger
import praw
from dotenv import load_dotenv

from sources.base import BaseSource, VideoCandidate
from core.http import HttpClient, RateLimiter

# Load .env from current working directory, scraper folder, or project root
for env_path in [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env", Path(__file__).resolve().parents[2] / ".env"]:
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)
load_dotenv()


class RedditSource(BaseSource):
    """
    Reddit video scraper with three resilient layers:
      Layer 1: Official Reddit API via PRAW (if .env credentials present)
      Layer 2: Public JSON listing endpoints (.json) - extracts real engagement
      Layer 3: Reddit RSS / Atom feeds (.rss) - resilient open fallback
    """

    name: str = "reddit"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.reddit_cfg = config.get("reddit", {})
        self.subreddits = self.reddit_cfg.get("subreddits", ["funny", "Unexpected", "holdmybeer"])
        self.listings = self.reddit_cfg.get("listings", ["hot", "top_day", "rising"])
        self.limit = self.reddit_cfg.get("limit_per_sub", 25)
        self.max_pages_per_source = int(self.reddit_cfg.get("max_pages_per_source_per_run", 3))
        self.custom_sources = self.reddit_cfg.get("custom_sources") or config.get("custom_sources") or []

        # Locate registry database path for cursor persistence
        reg_path = config.get("registry", {}).get("db_path") or config.get("registry_db_path")
        if not reg_path:
            for cand_p in [
                Path.cwd() / "agent" / "data" / "agent.db",
                Path(__file__).resolve().parent.parent.parent / "agent" / "data" / "agent.db",
                Path(__file__).resolve().parents[2] / "agent" / "data" / "agent.db",
                Path(__file__).resolve().parent.parent / "data" / "agent.db",
                Path("agent/data/agent.db"),
                Path("../agent/data/agent.db"),
            ]:
                if cand_p.is_file():
                    reg_path = str(cand_p.resolve())
                    break
        self.registry_db_path = str(reg_path) if reg_path else None

        delay_range = self.reddit_cfg.get("rate_limit_delay", [1.0, 2.0])
        self.rate_limiter = RateLimiter(min_delay=delay_range[0], max_delay=delay_range[1])
        self.http = HttpClient()

        self.praw_client: Optional[praw.Reddit] = None
        self._init_praw()

    def _get_cursor(self, source_type: str, name: str, listing_mode: str, initial_cursor: Optional[str] = None) -> Optional[str]:
        """Never use a cursor for hot listings (always starts from top). Reads from registry if not provided."""
        if listing_mode == "hot":
            return None
        if initial_cursor:
            return initial_cursor
        if not self.registry_db_path or not Path(self.registry_db_path).is_file():
            return None
        try:
            conn = sqlite3.connect(self.registry_db_path, timeout=5.0)
            cur = conn.execute("""
                SELECT pagination_cursor FROM sources
                WHERE source_type = ? AND name = ? AND listing_mode = ?
            """, (source_type, name, listing_mode))
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                return str(row[0])
        except Exception as e:
            logger.debug(f"Error reading cursor from registry for {name}/{listing_mode}: {e}")
        return None

    def _save_cursor(self, source_type: str, name: str, listing_mode: str, cursor: Optional[str]) -> None:
        """Persist pagination cursor back to registry table."""
        if listing_mode == "hot" or not cursor or not self.registry_db_path:
            return
        try:
            if Path(self.registry_db_path).is_file():
                conn = sqlite3.connect(self.registry_db_path, timeout=5.0)
                with conn:
                    conn.execute("""
                        UPDATE sources
                        SET pagination_cursor = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE source_type = ? AND name = ? AND listing_mode = ?
                    """, (cursor, source_type, name, listing_mode))
                conn.close()
                logger.info(f"Saved pagination cursor '{cursor}' for ({source_type}, {name}, {listing_mode}) to registry.")
        except Exception as e:
            logger.debug(f"Error saving cursor to registry: {e}")

    def _mark_exhausted(self, source_type: str, name: str, listing_mode: str) -> None:
        """Mark source exhausted in registry after 3 empty pages in a row."""
        if not self.registry_db_path or not Path(self.registry_db_path).is_file():
            return
        try:
            conn = sqlite3.connect(self.registry_db_path, timeout=5.0)
            with conn:
                conn.execute("""
                    UPDATE sources
                    SET status = 'exhausted', updated_at = CURRENT_TIMESTAMP
                    WHERE source_type = ? AND name = ? AND listing_mode = ?
                """, (source_type, name, listing_mode))
            conn.close()
            logger.info(f"Source ({source_type}, {name}, {listing_mode}) marked 'exhausted' after 3 consecutive empty pages.")
        except Exception as e:
            logger.debug(f"Error marking source exhausted: {e}")

    def _init_praw(self) -> None:
        client_id = (os.getenv("REDDIT_CLIENT_ID") or "").strip()
        client_secret = (os.getenv("REDDIT_CLIENT_SECRET") or "").strip()
        user_agent = (os.getenv("REDDIT_USER_AGENT") or "").strip() or "python:funny_video_curator:v1.0"

        # Check for non-empty credentials that are not template placeholders
        is_placeholder = any(
            ph in client_id.lower() or ph in client_secret.lower()
            for ph in ["your_reddit", "client_id_here", "client_secret_here"]
        )

        if client_id and client_secret and not is_placeholder:
            try:
                self.praw_client = praw.Reddit(
                    client_id=client_id,
                    client_secret=client_secret,
                    user_agent=user_agent,
                    username=os.getenv("REDDIT_USERNAME") or None,
                    password=os.getenv("REDDIT_PASSWORD") or None,
                )
                logger.info("Reddit Layer 1 initialized (Official PRAW API).")
            except Exception as e:
                logger.warning(f"Could not initialize PRAW: {e}. Falling back to open feeds.")
                self.praw_client = None
        else:
            logger.info("No Reddit API keys found in .env; using open feeds fallback (JSON -> RSS).")
            self.praw_client = None

    def _get_effective_sources(self) -> List[Dict[str, Any]]:
        """Return list of source specs (subreddits or search queries) to query."""
        if self.custom_sources:
            return self.custom_sources
        eff = []
        for sub in self.subreddits:
            for l in self.listings:
                eff.append({"type": "subreddit", "source_type": "subreddit", "name": sub, "listing": l, "listing_mode": l, "cursor": None})
        return eff

    def fetch_candidates(self, target_count: int) -> List[VideoCandidate]:
        candidates: List[VideoCandidate] = []
        seen_ids = set()

        # Layer 1: Official Reddit API via PRAW (if configured)
        if self.praw_client:
            try:
                logger.info(f"Fetching Reddit candidates via PRAW (target={target_count})...")
                praw_candidates = self._fetch_via_praw(target_count, seen_ids)
                candidates.extend(praw_candidates)
                if len(candidates) >= target_count:
                    return candidates
                logger.info(
                    f"PRAW returned {len(candidates)} candidates. Augmenting with JSON listings..."
                )
            except Exception as e:
                logger.warning(f"PRAW fetch encountered error: {e}. Falling back to JSON endpoints.")

        # Layer 2: Public JSON listing endpoints (.json)
        if len(candidates) < target_count:
            needed = target_count - len(candidates)
            logger.info(f"Fetching Reddit candidates via JSON endpoints (target={needed})...")
            try:
                json_candidates = self._fetch_via_json(needed, seen_ids)
                candidates.extend(json_candidates)
            except Exception as e:
                logger.warning(f"Reddit JSON fetch error: {e}. Falling back to RSS.")

        # Layer 3: RSS / Atom Feeds (.rss) - fallback if JSON fails or rate limits
        if len(candidates) < target_count:
            needed = target_count - len(candidates)
            logger.info(f"Augmenting Reddit candidates via RSS feeds (target={needed})...")
            try:
                rss_candidates = self._fetch_via_rss(needed, seen_ids)
                candidates.extend(rss_candidates)
            except Exception as e:
                logger.warning(f"Reddit RSS fetch error: {e}")

        return candidates

    def _fetch_via_praw(self, target_count: int, seen_ids: set) -> List[VideoCandidate]:
        """Layer 1: Extract video candidates via official Reddit API (PRAW) with cursor pagination."""
        candidates: List[VideoCandidate] = []
        sources = self._get_effective_sources()

        for src in sources:
            if len(candidates) >= target_count:
                break

            src_type = src.get("source_type") or src.get("type") or "subreddit"
            src_name = src.get("name", "funny")
            listing = src.get("listing_mode") or src.get("listing") or "hot"
            time_filter = listing.replace("top_", "") if listing.startswith("top_") else "all"

            # hot listings must not use a cursor; archive listings and search queries use it
            uses_cursor = (listing in ["top_week", "top_month", "top_year", "top_all"] or src_type == "search_query") and listing != "hot"
            cursor = self._get_cursor(src_type, src_name, listing, src.get("cursor") or src.get("pagination_cursor")) if uses_cursor else None
            max_pages = self.max_pages_per_source if uses_cursor else 1
            consecutive_empty = 0

            for page in range(max_pages):
                if len(candidates) >= target_count:
                    break

                page_label = f"{page + 1}/{max_pages}" if uses_cursor else "1/1"
                logger.info(f"[PRAW:{src_type}:{src_name}:{listing}] Fetching page {page_label} (cursor={cursor or 'start'})...")

                self.rate_limiter.wait("reddit")
                page_limit = 100 if uses_cursor else self.limit
                praw_kwargs: Dict[str, Any] = {"limit": page_limit}
                if uses_cursor and cursor:
                    praw_kwargs["params"] = {"after": cursor}

                page_new_count = 0
                last_fullname = None

                try:
                    if src_type == "search_query":
                        generators = [
                            self.praw_client.subreddit("all").search(
                                src_name, sort="top", time_filter=time_filter, **praw_kwargs
                            )
                        ]
                    else:
                        sub = self.praw_client.subreddit(src_name)
                        generators = []
                        if listing == "hot":
                            generators.append(sub.hot(limit=self.limit))
                        elif listing.startswith("top_") or listing == "top":
                            generators.append(sub.top(time_filter=time_filter, **praw_kwargs))
                        elif listing == "rising":
                            generators.append(sub.rising(limit=self.limit))
                        else:
                            generators.append(sub.hot(limit=self.limit))

                    for gen in generators:
                        for post in gen:
                            fullname = getattr(post, "name", f"t3_{post.id}")
                            last_fullname = fullname

                            if len(candidates) >= target_count:
                                break
                            if post.id in seen_ids:
                                continue

                            is_video = getattr(post, "is_video", False)
                            domain = getattr(post, "domain", "")
                            media = getattr(post, "media", None)

                            # Check for crossposted video
                            crosspost_list = getattr(post, "crosspost_parent_list", None)
                            if not (is_video or "v.redd.it" in domain or (media and "reddit_video" in media)) and crosspost_list:
                                cp = crosspost_list[0]
                                if cp.get("is_video") or "v.redd.it" in cp.get("domain", "") or (cp.get("media") and "reddit_video" in cp.get("media", {})):
                                    is_video = True
                                    media = cp.get("media")

                            if not (is_video or "v.redd.it" in domain or (media and "reddit_video" in media)):
                                continue

                            seen_ids.add(post.id)
                            dur = None
                            fallback_url = None
                            if media and isinstance(media, dict) and "reddit_video" in media:
                                raw_d = media["reddit_video"].get("duration")
                                if raw_d is not None and float(raw_d) > 0:
                                    dur = float(raw_d)
                                fallback_url = media["reddit_video"].get("fallback_url")

                            post_url = f"https://www.reddit.com{post.permalink}"
                            video_url = post_url
                            post_link_url = getattr(post, "url", "")
                            if post_link_url.startswith("https://v.redd.it/"):
                                video_url = post_link_url

                            candidate = VideoCandidate(
                                source="reddit",
                                post_id=f"reddit_{post.id}",
                                post_url=post_url,
                                title=post.title,
                                uploader=str(post.author) if post.author else "unknown",
                                score=int(post.score),
                                upvote_ratio=float(getattr(post, "upvote_ratio", 0.95)),
                                num_comments=int(post.num_comments),
                                created_utc=float(post.created_utc),
                                video_url=video_url,
                                is_nsfw=bool(post.over_18),
                                duration=dur,
                                extra={
                                    "source_type": src_type,
                                    "source_name": src_name,
                                    "listing_mode": listing,
                                    "subreddit": src_name if src_type == "subreddit" else "all",
                                    "source_feed": "praw",
                                    "fallback_url": fallback_url,
                                    "pagination_cursor": fullname if listing != "hot" else None,
                                },
                            )
                            candidates.append(candidate)
                            page_new_count += 1

                    if uses_cursor and last_fullname:
                        self._save_cursor(src_type, src_name, listing, last_fullname)
                        cursor = last_fullname

                    logger.info(
                        f"[PRAW:{src_type}:{src_name}:{listing}] Page {page + 1}: found {page_new_count} candidates "
                        f"({len(candidates)}/{target_count} total). Next cursor: {last_fullname or 'none'}"
                    )

                    if page_new_count == 0:
                        consecutive_empty += 1
                        if consecutive_empty >= 3 and uses_cursor:
                            self._mark_exhausted(src_type, src_name, listing)
                            break
                        if not last_fullname:
                            break
                    else:
                        consecutive_empty = 0

                    if not uses_cursor or not last_fullname:
                        break

                except Exception as e:
                    logger.warning(f"Error scraping {src_type} '{src_name}' via PRAW: {e}")
                    break

        return candidates

    def _fetch_via_json(self, target_count: int, seen_ids: set) -> List[VideoCandidate]:
        """Layer 2: Extract video candidates from Reddit JSON endpoints with cursor pagination."""
        candidates: List[VideoCandidate] = []
        consecutive_errors = 0
        sources = self._get_effective_sources()

        for src in sources:
            if len(candidates) >= target_count or consecutive_errors >= 3:
                break

            src_type = src.get("source_type") or src.get("type") or "subreddit"
            src_name = src.get("name", "funny")
            listing = src.get("listing_mode") or src.get("listing") or "hot"
            time_filter = listing.replace("top_", "") if listing.startswith("top_") else "all"

            # hot listings must not use a cursor; archive listings and search queries use it
            uses_cursor = (listing in ["top_week", "top_month", "top_year", "top_all"] or src_type == "search_query") and listing != "hot"
            cursor = self._get_cursor(src_type, src_name, listing, src.get("cursor") or src.get("pagination_cursor")) if uses_cursor else None
            max_pages = self.max_pages_per_source if uses_cursor else 1
            consecutive_empty = 0

            for page in range(max_pages):
                if len(candidates) >= target_count:
                    break

                page_label = f"{page + 1}/{max_pages}" if uses_cursor else "1/1"
                logger.info(f"[JSON:{src_type}:{src_name}:{listing}] Fetching page {page_label} (cursor={cursor or 'start'})...")

                self.rate_limiter.wait("reddit")
                page_limit = 100 if uses_cursor else self.limit
                params: Dict[str, Any] = {"limit": page_limit}
                if uses_cursor and cursor:
                    params["after"] = cursor

                if src_type == "search_query":
                    url = "https://www.reddit.com/search.json"
                    params["q"] = src_name
                    params["sort"] = "top"
                    params["t"] = time_filter
                else:
                    endpoint = "hot.json"
                    if listing.startswith("top_") or listing == "top":
                        endpoint = "top.json"
                        params["t"] = time_filter
                    elif listing == "rising":
                        endpoint = "rising.json"
                    url = f"https://www.reddit.com/r/{src_name}/{endpoint}"

                headers = {"Accept": "application/json"}
                ua = os.getenv("REDDIT_USER_AGENT")
                if ua and "YOUR_USERNAME" not in ua:
                    headers["User-Agent"] = ua

                resp = self.http.get(url, params=params, headers=headers)
                if not resp or resp.status_code != 200:
                    consecutive_errors += 1
                    if resp and resp.status_code == 429:
                        raw_reset = resp.headers.get("x-ratelimit-reset")
                        try:
                            reset_wait = float(raw_reset) + 1.0 if raw_reset else 32.0
                            reset_wait = min(reset_wait, 35.0)
                        except (ValueError, TypeError):
                            reset_wait = 32.0
                        logger.info(f"Reddit 429 rate limit on {src_name}. Backing off {reset_wait:.0f}s...")
                        time.sleep(reset_wait)
                    break

                consecutive_errors = 0
                try:
                    data = resp.json()
                    children = data.get("data", {}).get("children", [])
                    after_val = data.get("data", {}).get("after")

                    page_new_count = 0
                    last_fullname = None

                    for child in children:
                        post = child.get("data", {})
                        post_id = post.get("id")
                        fullname = post.get("name") or f"t3_{post_id}"
                        last_fullname = fullname

                        if len(candidates) >= target_count:
                            break
                        if not post_id or post_id in seen_ids:
                            continue

                        is_video = post.get("is_video", False)
                        domain = post.get("domain", "")
                        media = post.get("media") or {}
                        reddit_vid = media.get("reddit_video") if isinstance(media, dict) else {}

                        crosspost_list = post.get("crosspost_parent_list") or []
                        if not reddit_vid and crosspost_list and isinstance(crosspost_list, list) and len(crosspost_list) > 0:
                            cp = crosspost_list[0]
                            if isinstance(cp, dict):
                                cp_media = cp.get("media") or {}
                                if isinstance(cp_media, dict) and "reddit_video" in cp_media:
                                    reddit_vid = cp_media.get("reddit_video") or {}
                                if cp.get("is_video"):
                                    is_video = True

                        if not (is_video or "v.redd.it" in domain or reddit_vid):
                            continue

                        seen_ids.add(post_id)
                        permalink = post.get("permalink", "")
                        dur = None
                        fallback_url = None
                        if reddit_vid and isinstance(reddit_vid, dict):
                            raw_dur = reddit_vid.get("duration")
                            if raw_dur is not None and float(raw_dur) > 0:
                                dur = float(raw_dur)
                            fallback_url = reddit_vid.get("fallback_url")

                        post_url = f"https://www.reddit.com{permalink}"
                        video_url = post_url
                        url_field = post.get("url", "")
                        if url_field.startswith("https://v.redd.it/"):
                            video_url = url_field

                        candidate = VideoCandidate(
                            source="reddit",
                            post_id=f"reddit_{post_id}",
                            post_url=post_url,
                            title=post.get("title", ""),
                            uploader=post.get("author", "unknown"),
                            score=int(post.get("score", 0)),
                            upvote_ratio=float(post.get("upvote_ratio", 0.95)),
                            num_comments=int(post.get("num_comments", 0)),
                            created_utc=float(post.get("created_utc", 0)),
                            video_url=video_url,
                            is_nsfw=bool(post.get("over_18", False)),
                            duration=dur,
                            extra={
                                "source_type": src_type,
                                "source_name": src_name,
                                "listing_mode": listing,
                                "subreddit": post.get("subreddit", src_name),
                                "listing": listing,
                                "source_feed": "json",
                                "fallback_url": fallback_url,
                                "pagination_cursor": fullname if listing != "hot" else None,
                            },
                        )
                        candidates.append(candidate)
                        page_new_count += 1

                    next_cursor = after_val or last_fullname
                    if uses_cursor and next_cursor:
                        self._save_cursor(src_type, src_name, listing, next_cursor)
                        cursor = next_cursor

                    logger.info(
                        f"[JSON:{src_type}:{src_name}:{listing}] Page {page + 1}: found {page_new_count} candidates "
                        f"({len(candidates)}/{target_count} total). Next cursor: {next_cursor or 'none'}"
                    )

                    if page_new_count == 0 or not children:
                        consecutive_empty += 1
                        if consecutive_empty >= 3 and uses_cursor:
                            self._mark_exhausted(src_type, src_name, listing)
                            break
                        if not next_cursor:
                            break
                    else:
                        consecutive_empty = 0

                    if not uses_cursor or not next_cursor:
                        break

                except Exception as e:
                    logger.warning(f"Error parsing Reddit JSON for {src_name}: {e}")
                    break

        if consecutive_errors >= 3:
            logger.info("Reddit JSON endpoints blocked/rate-limited; proceeding to RSS feed layer.")

        return candidates

    def _fetch_via_rss(self, target_count: int, seen_ids: set) -> List[VideoCandidate]:
        """
        Layer 3: Extract video candidates from Reddit Atom/RSS feeds with cursor pagination.
        Supports:
          - Multireddits (/r/sub1+sub2+sub3/hot.rss) for fast bulk fetches
          - Single subreddits with time windows and pagination cursors
          - Reddit search queries (/search.rss?q=...&sort=top&t=...)
        """
        candidates: List[VideoCandidate] = []
        consecutive_429 = 0

        listing_baselines = {
            "top_day": {"base_upvotes": 15000, "decay": 0.12},
            "top_week": {"base_upvotes": 20000, "decay": 0.12},
            "top_month": {"base_upvotes": 25000, "decay": 0.12},
            "top_year": {"base_upvotes": 35000, "decay": 0.12},
            "top_all": {"base_upvotes": 50000, "decay": 0.12},
            "hot": {"base_upvotes": 8000, "decay": 0.15},
            "rising": {"base_upvotes": 2500, "decay": 0.10},
        }

        sources = self._get_effective_sources()
        feed_headers = {"User-Agent": "MorningstarVideoCurator/1.0 (feed reader; non-commercial)"}

        individual_sources: List[Dict[str, Any]] = []
        if not self.custom_sources:
            chunk_size = 6
            sub_chunks = [self.subreddits[i : i + chunk_size] for i in range(0, len(self.subreddits), chunk_size)]
            for chunk in sub_chunks:
                for listing in self.listings:
                    individual_sources.append({
                        "type": "multireddit",
                        "names": chunk,
                        "name": "+".join(chunk),
                        "listing": listing,
                        "cursor": None,
                    })
        else:
            for s in sources:
                individual_sources.append(s)

        for src in individual_sources:
            if len(candidates) >= target_count:
                break

            src_type = src.get("source_type") or src.get("type") or "subreddit"
            src_name = src.get("name", "funny")
            listing = src.get("listing_mode") or src.get("listing") or "hot"
            time_filter = listing.replace("top_", "") if listing.startswith("top_") else "all"

            # hot listings must not use a cursor; archive listings and search queries use it
            uses_cursor = (listing in ["top_week", "top_month", "top_year", "top_all"] or src_type == "search_query") and listing != "hot"
            cursor = self._get_cursor(src_type, src_name, listing, src.get("cursor") or src.get("pagination_cursor")) if uses_cursor else None
            max_pages = self.max_pages_per_source if uses_cursor else 1
            consecutive_empty = 0

            for page in range(max_pages):
                if len(candidates) >= target_count:
                    break

                page_label = f"{page + 1}/{max_pages}" if uses_cursor else "1/1"
                source_label = src_name
                logger.info(f"[RSS:{src_type}:{src_name}:{listing}] Fetching page {page_label} (cursor={cursor or 'start'})...")

                self.rate_limiter.wait("reddit")

                if src_type == "search_query":
                    q_enc = urllib.parse.quote_plus(src.get("name", ""))
                    url = f"https://www.reddit.com/search.rss?q={q_enc}&sort=top&t={time_filter}&limit=100"
                    if uses_cursor and cursor:
                        url += f"&after={cursor}"
                    source_label = src.get("name", "")
                elif src_type == "multireddit":
                    multi_slug = src.get("name", "")
                    endpoint = "hot.rss"
                    if listing.startswith("top_") or listing == "top":
                        endpoint = f"top.rss?t={time_filter}&limit=100"
                    elif listing == "rising":
                        endpoint = "rising.rss?limit=100"
                    url = f"https://www.reddit.com/r/{multi_slug}/{endpoint}"
                    if uses_cursor and cursor:
                        delimiter = "&" if "?" in endpoint else "?"
                        url += f"{delimiter}after={cursor}"
                    source_label = multi_slug
                else:
                    sub_name = src.get("name", "funny")
                    endpoint = "hot.rss"
                    if listing.startswith("top_") or listing == "top":
                        endpoint = f"top.rss?t={time_filter}&limit=100"
                    elif listing == "rising":
                        endpoint = "rising.rss?limit=100"
                    url = f"https://www.reddit.com/r/{sub_name}/{endpoint}"
                    if uses_cursor and cursor:
                        delimiter = "&" if "?" in endpoint else "?"
                        url += f"{delimiter}after={cursor}"
                    source_label = sub_name

                resp = self.http.get(url, headers=feed_headers)
                if not resp or resp.status_code != 200:
                    consecutive_429 += 1
                    if resp and resp.status_code == 429:
                        raw_reset = resp.headers.get("x-ratelimit-reset")
                        try:
                            reset_wait = float(raw_reset) + 1.0 if raw_reset else 32.0
                            reset_wait = min(reset_wait, 35.0)
                        except (ValueError, TypeError):
                            reset_wait = 32.0
                        logger.info(
                            f"Reddit rate limit on {source_label} ({reset_wait:.0f}s reset). Waiting before next request..."
                        )
                        time.sleep(reset_wait)
                    break

                consecutive_429 = 0
                try:
                    root = ET.fromstring(resp.text)
                    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
                    page_new_count = 0
                    last_fullname = None

                    for entry_idx, entry in enumerate(entries):
                        link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                        post_url = link_elem.attrib.get("href", "") if link_elem is not None else ""

                        id_match = re.search(r"/comments/([a-zA-Z0-9]+)/", post_url)
                        if not id_match:
                            continue

                        post_id = id_match.group(1)
                        fullname = f"t3_{post_id}"
                        last_fullname = fullname

                        if len(candidates) >= target_count:
                            break
                        if post_id in seen_ids:
                            continue

                        sub_match = re.search(r"/r/([a-zA-Z0-9_]+)/", post_url)
                        entry_sub = sub_match.group(1) if sub_match else (src.get("name", "unknown").split("+")[0])

                        content_elem = entry.find("{http://www.w3.org/2005/Atom}content")
                        content_text = content_elem.text if content_elem is not None else ""

                        v_match = re.search(r"https://v\.redd\.it/[a-zA-Z0-9]+", content_text)
                        ext_vid_match = re.search(
                            r'href=["\'](https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|streamable\.com/|redgifs\.com/watch/|tiktok\.com/)[^"\'\s>]+)["\']',
                            content_text,
                        )
                        if not v_match and not ext_vid_match:
                            continue

                        seen_ids.add(post_id)
                        video_url = v_match.group(0) if v_match else ext_vid_match.group(1)

                        title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                        title = title_elem.text if title_elem is not None else f"Video from r/{entry_sub}"

                        author_elem = entry.find("{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name")
                        author = author_elem.text.replace("/u/", "") if author_elem is not None else "unknown"

                        baseline = listing_baselines.get(listing, listing_baselines["hot"])
                        decay = 1.0 + (baseline["decay"] * entry_idx)
                        est_score = max(50, int(baseline["base_upvotes"] / decay))
                        est_comments = max(10, int(est_score * 0.04))
                        est_ratio = round(max(0.75, 0.96 - (0.008 * entry_idx)), 2)

                        candidates.append(VideoCandidate(
                            source="reddit",
                            post_id=f"reddit_{post_id}",
                            post_url=post_url,
                            title=title,
                            uploader=author,
                            score=est_score,
                            upvote_ratio=est_ratio,
                            num_comments=est_comments,
                            created_utc=time.time(),
                            video_url=video_url,
                            is_nsfw=False,
                            duration=None,
                            extra={
                                "source_type": src_type if src_type != "multireddit" else "subreddit",
                                "source_name": src.get("name", entry_sub),
                                "listing_mode": listing,
                                "subreddit": entry_sub,
                                "source_feed": "search_rss" if src_type == "search_query" else "multireddit_rss",
                                "listing": listing,
                                "feed_rank": entry_idx,
                                "is_estimated_score": True,
                                "pagination_cursor": fullname if listing != "hot" else None,
                            },
                        ))
                        page_new_count += 1

                    if uses_cursor and last_fullname:
                        self._save_cursor(src_type, src_name, listing, last_fullname)
                        cursor = last_fullname

                    logger.info(
                        f"[RSS:{src_type}:{source_label}:{listing}] Page {page + 1}: found {page_new_count} candidates "
                        f"({len(candidates)}/{target_count} total). Next cursor: {last_fullname or 'none'}"
                    )

                    if page_new_count == 0 or not entries:
                        consecutive_empty += 1
                        if consecutive_empty >= 3 and uses_cursor:
                            self._mark_exhausted(src_type, src_name, listing)
                            break
                        if not last_fullname:
                            break
                    else:
                        consecutive_empty = 0

                    if not uses_cursor or not last_fullname:
                        break

                except Exception as e:
                    logger.warning(f"Error parsing RSS for {source_label}: {e}")
                    break

        if consecutive_429 >= 3:
            logger.info("Reddit unauthenticated rate limit reached. Pausing until next curation loop.")

        return candidates
