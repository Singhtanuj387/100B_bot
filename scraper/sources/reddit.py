import os
import re
import time
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
        self.custom_sources = self.reddit_cfg.get("custom_sources") or config.get("custom_sources") or []

        delay_range = self.reddit_cfg.get("rate_limit_delay", [1.0, 2.0])
        self.rate_limiter = RateLimiter(min_delay=delay_range[0], max_delay=delay_range[1])
        self.http = HttpClient()

        self.praw_client: Optional[praw.Reddit] = None
        self._init_praw()

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
                eff.append({"type": "subreddit", "name": sub, "listing": l, "cursor": None})
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
        """Layer 1: Extract video candidates via official Reddit API (PRAW)."""
        candidates: List[VideoCandidate] = []
        sources = self._get_effective_sources()

        for src in sources:
            if len(candidates) >= target_count:
                break

            src_type = src.get("type", "subreddit")
            src_name = src.get("name", "funny")
            listing = src.get("listing", "hot")
            time_filter = listing.replace("top_", "") if listing.startswith("top_") else "all"

            self.rate_limiter.wait("reddit")
            try:
                if src_type == "search_query":
                    generators = [
                        self.praw_client.subreddit("all").search(
                            src_name, sort="top", time_filter=time_filter, limit=self.limit
                        )
                    ]
                else:
                    sub = self.praw_client.subreddit(src_name)
                    generators = []
                    if listing == "hot":
                        generators.append(sub.hot(limit=self.limit))
                    elif listing.startswith("top_") or listing == "top":
                        generators.append(sub.top(time_filter=time_filter, limit=self.limit))
                    elif listing == "rising":
                        generators.append(sub.rising(limit=self.limit))
                    else:
                        generators.append(sub.hot(limit=self.limit))

                for gen in generators:
                    for post in gen:
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
                        if media and "reddit_video" in media:
                            dur = float(media["reddit_video"].get("duration", 0))
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
                                "pagination_cursor": f"t3_{post.id}",
                            },
                        )
                        candidates.append(candidate)

            except Exception as e:
                logger.warning(f"Error scraping {src_type} '{src_name}' via PRAW: {e}")
                continue

        return candidates

    def _fetch_via_json(self, target_count: int, seen_ids: set) -> List[VideoCandidate]:
        """Layer 2: Extract video candidates from Reddit JSON endpoints with real engagement."""
        candidates: List[VideoCandidate] = []
        consecutive_errors = 0
        sources = self._get_effective_sources()

        for src in sources:
            if len(candidates) >= target_count or consecutive_errors >= 3:
                break

            src_type = src.get("type", "subreddit")
            src_name = src.get("name", "funny")
            listing = src.get("listing", "hot")
            cursor = src.get("cursor")
            time_filter = listing.replace("top_", "") if listing.startswith("top_") else "all"

            self.rate_limiter.wait("reddit")
            params = {"limit": self.limit}
            if cursor:
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
                continue

            consecutive_errors = 0
            try:
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                for child in children:
                    if len(candidates) >= target_count:
                        break

                    post = child.get("data", {})
                    post_id = post.get("id")
                    if not post_id or post_id in seen_ids:
                        continue

                    is_video = post.get("is_video", False)
                    domain = post.get("domain", "")
                    media = post.get("media") or {}
                    reddit_vid = media.get("reddit_video") or {}

                    crosspost_list = post.get("crosspost_parent_list") or []
                    if not reddit_vid and crosspost_list:
                        cp_media = crosspost_list[0].get("media") or {}
                        reddit_vid = cp_media.get("reddit_video") or {}
                        if crosspost_list[0].get("is_video"):
                            is_video = True

                    if not (is_video or "v.redd.it" in domain or reddit_vid):
                        continue

                    seen_ids.add(post_id)
                    permalink = post.get("permalink", "")
                    dur = float(reddit_vid.get("duration", 0)) if reddit_vid else None
                    fallback_url = reddit_vid.get("fallback_url") if reddit_vid else None

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
                            "pagination_cursor": f"t3_{post_id}",
                        },
                    )
                    candidates.append(candidate)

            except Exception as e:
                logger.warning(f"Error parsing Reddit JSON for {src_name}: {e}")
                continue

        if consecutive_errors >= 3:
            logger.info("Reddit JSON endpoints blocked/rate-limited; proceeding to RSS feed layer.")

        return candidates

    def _fetch_via_rss(self, target_count: int, seen_ids: set) -> List[VideoCandidate]:
        """
        Layer 3: Extract video candidates from Reddit Atom/RSS feeds.
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

        # Check if we can batch subreddits into multireddit chunks (only if custom_sources is not specified or subreddits share listing without cursor)
        batchable_subs: Dict[str, List[str]] = {}
        individual_sources: List[Dict[str, Any]] = []

        if not self.custom_sources:
            # Default mode: chunk subreddits by listing
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

            src_type = src.get("type", "subreddit")
            listing = src.get("listing", "hot")
            cursor = src.get("cursor")
            time_filter = listing.replace("top_", "") if listing.startswith("top_") else "all"

            self.rate_limiter.wait("reddit")

            if src_type == "search_query":
                q_enc = urllib.parse.quote_plus(src.get("name", ""))
                url = f"https://www.reddit.com/search.rss?q={q_enc}&sort=top&t={time_filter}"
                if cursor:
                    url += f"&after={cursor}"
                source_label = src.get("name", "")
            elif src_type == "multireddit":
                multi_slug = src.get("name", "")
                endpoint = "hot.rss"
                if listing.startswith("top_") or listing == "top":
                    endpoint = f"top.rss?t={time_filter}"
                elif listing == "rising":
                    endpoint = "rising.rss"
                url = f"https://www.reddit.com/r/{multi_slug}/{endpoint}"
                if cursor:
                    delimiter = "&" if "?" in endpoint else "?"
                    url += f"{delimiter}after={cursor}"
                source_label = multi_slug
            else:
                sub_name = src.get("name", "funny")
                endpoint = "hot.rss"
                if listing.startswith("top_") or listing == "top":
                    endpoint = f"top.rss?t={time_filter}"
                elif listing == "rising":
                    endpoint = "rising.rss"
                url = f"https://www.reddit.com/r/{sub_name}/{endpoint}"
                if cursor:
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
                continue

            consecutive_429 = 0
            try:
                root = ET.fromstring(resp.text)
                entries = root.findall("{http://www.w3.org/2005/Atom}entry")
                for entry_idx, entry in enumerate(entries):
                    if len(candidates) >= target_count:
                        break

                    link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                    post_url = link_elem.attrib.get("href", "") if link_elem is not None else ""

                    id_match = re.search(r"/comments/([a-zA-Z0-9]+)/", post_url)
                    if not id_match:
                        continue

                    post_id = id_match.group(1)
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
                            "pagination_cursor": f"t3_{post_id}",
                        },
                    ))

            except Exception as e:
                logger.warning(f"Error parsing RSS for {source_label}: {e}")
                continue

        if consecutive_429 >= 3:
            logger.info("Reddit unauthenticated rate limit reached. Pausing until next curation loop.")

        return candidates
