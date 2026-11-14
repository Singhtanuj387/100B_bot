import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger
import yt_dlp

from sources.base import BaseSource, VideoCandidate
from core.http import RateLimiter


def parse_metric_text(text: Optional[str]) -> Optional[int]:
    """
    Parse human-readable metrics like '14.2K', '1.5M', '1,420', '500' into integers.
    Works with aria-labels like '1,420 Likes. Like' or innerText like '14.2K'.
    """
    if not text:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*([KkMm])?", text)
    if not m:
        return None
    num_str = m.group(1).replace(",", "")
    try:
        val = float(num_str)
        suffix = (m.group(2) or "").upper()
        if suffix == "K":
            val *= 1000
        elif suffix == "M":
            val *= 1000000
        return int(val)
    except ValueError:
        return None


def parse_netscape_cookies(cookies_path: Path) -> List[Dict[str, Any]]:
    """Parse standard Netscape / curl format cookies.txt into Playwright cookie dicts."""
    cookies = []
    if not cookies_path.is_file():
        return cookies

    try:
        with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain, flag, path, secure, expiration, name, value = parts[:7]
                    cookie_dict = {
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                        "secure": secure.lower() == "true",
                    }
                    try:
                        exp = float(expiration)
                        if exp > 0:
                            cookie_dict["expires"] = exp
                    except ValueError:
                        pass
                    cookies.append(cookie_dict)
    except Exception as e:
        logger.warning(f"Error parsing cookies file {cookies_path}: {e}")

    return cookies


def add_cookies_to_playwright(context: Any, cookies_file: Optional[str]) -> int:
    """Load cookies from cookies.txt into a Playwright BrowserContext."""
    if not cookies_file:
        return 0
    path = Path(cookies_file)
    if not path.is_file():
        return 0

    cookies = parse_netscape_cookies(path)
    loaded = 0
    for c in cookies:
        try:
            context.add_cookies([c])
            loaded += 1
        except Exception:
            # Retry with stripped leading dot if domain had .x.com
            try:
                c_mod = dict(c)
                if c_mod.get("domain", "").startswith("."):
                    c_mod["domain"] = c_mod["domain"][1:]
                context.add_cookies([c_mod])
                loaded += 1
            except Exception:
                pass

    if loaded > 0:
        logger.info(f"Loaded {loaded} cookies into Playwright from {cookies_file}")
    else:
        logger.warning(f"No valid cookies could be loaded from {cookies_file}")
    return loaded


class TwitterSource(BaseSource):
    """
    X (Twitter) Video Source plugin (EXPERIMENTAL).
    Twitter/X aggressively blocks unauthenticated scraping:
      - Profile and search pages frequently require login credentials or cookies.
      - Playwright context now loads cookies.txt when provided.
      - Metrics are extracted from DOM when available rather than using flat mock values.
      - search_queries are utilized if account feeds do not yield enough clips.
    """

    name: str = "twitter"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.twitter_cfg = config.get("twitter", {})
        self.accounts = self.twitter_cfg.get("accounts", ["Cheerfulvids", "DailyLoud", "OutOfContext"])
        self.search_queries = self.twitter_cfg.get("search_queries", [])
        raw_cookies = self.twitter_cfg.get("cookies_file", "cookies.txt")
        scraper_dir = Path(__file__).parent.parent.resolve()
        project_root = scraper_dir.parent.resolve()
        resolved_cookies = None
        if raw_cookies:
            if os.path.isabs(raw_cookies) and os.path.isfile(raw_cookies):
                resolved_cookies = raw_cookies
            else:
                for cand in [
                    scraper_dir / raw_cookies,
                    project_root / raw_cookies,
                    Path.cwd() / raw_cookies,
                ]:
                    if cand.is_file():
                        resolved_cookies = str(cand.resolve())
                        break
                if not resolved_cookies:
                    resolved_cookies = str((scraper_dir / raw_cookies).resolve())
        self.cookies_file = resolved_cookies
        self.use_playwright = self.twitter_cfg.get("use_playwright_fallback", True)
        self.headless = self.twitter_cfg.get("headless", True)

        delay_range = self.twitter_cfg.get("rate_limit_delay", [2.0, 4.0])
        self.rate_limiter = RateLimiter(min_delay=delay_range[0], max_delay=delay_range[1])

        logger.info("X (Twitter) source initialized [EXPERIMENTAL]. (Ensure cookies.txt is provided if login is required).")

    def fetch_candidates(self, target_count: int) -> List[VideoCandidate]:
        candidates: List[VideoCandidate] = []
        seen_ids = set()

        logger.info(f"Fetching X (Twitter) candidates (target={target_count})...")

        # Layer 1: Attempt yt-dlp extraction from account feeds
        for account in self.accounts:
            if len(candidates) >= target_count:
                break

            self.rate_limiter.wait("twitter")
            try:
                acc_candidates = self._fetch_account_via_ytdlp(account, max_items=10, seen_ids=seen_ids)
                candidates.extend(acc_candidates)
                if len(candidates) >= target_count:
                    break
            except Exception as e:
                logger.warning(f"yt-dlp extraction failed for @{account}: {e}")

        # Layer 2: Playwright fallback (accounts + search queries) if needed
        if len(candidates) < target_count and self.use_playwright:
            needed = target_count - len(candidates)
            logger.info(f"Attempting Playwright headless fallback for X (Twitter) (needed={needed})...")
            try:
                pw_candidates = self._fetch_via_playwright(needed, seen_ids)
                candidates.extend(pw_candidates)
            except Exception as e:
                logger.warning(f"Playwright fallback encountered error: {e}. Skipping X safely.")

        logger.info(f"Collected {len(candidates)} candidates from X (Twitter).")
        return candidates

    def _fetch_account_via_ytdlp(self, account: str, max_items: int, seen_ids: set) -> List[VideoCandidate]:
        """Query account feed using yt-dlp extractor."""
        url = f"https://x.com/{account}"
        ydl_opts = {
            "extract_flat": "in_playlist",
            "playlistend": max_items,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
        }

        if self.cookies_file and os.path.isfile(self.cookies_file):
            ydl_opts["cookiefile"] = str(Path(self.cookies_file).resolve())

        candidates = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                err_str = str(e).lower()
                if "rate-limit" in err_str or "429" in err_str:
                    logger.warning(f"X (Twitter) rate limit encountered on @{account}. Backing off.")
                elif "login" in err_str or "auth" in err_str:
                    logger.warning(f"X (Twitter) requested login for @{account}. Export cookies.txt to authenticate.")
                else:
                    logger.warning(f"Could not extract @{account} via yt-dlp: {e}")
                return []

            if not info or "entries" not in info:
                return []

            for entry in info["entries"]:
                if not entry:
                    continue
                post_id = entry.get("id")
                if not post_id or post_id in seen_ids:
                    continue

                seen_ids.add(post_id)
                webpage_url = entry.get("webpage_url") or entry.get("url") or f"https://x.com/{account}/status/{post_id}"
                title = entry.get("title") or entry.get("description") or f"Viral video from @{account}"

                like_count = entry.get("like_count")
                comment_count = entry.get("comment_count")
                is_est = False
                if like_count is None:
                    like_count = 1000
                    is_est = True
                if comment_count is None:
                    comment_count = max(10, int(like_count * 0.05))

                candidates.append(VideoCandidate(
                    source="twitter",
                    post_id=f"twitter_{post_id}",
                    post_url=webpage_url,
                    title=title,
                    uploader=account,
                    score=int(like_count),
                    num_comments=int(comment_count),
                    duration=entry.get("duration"),
                    video_url=webpage_url,
                    is_nsfw=False,
                    extra={"account": account, "source_feed": "twitter", "is_estimated_score": is_est},
                ))

        return candidates

    def _extract_candidates_from_page(
        self, page: Any, account_or_query: str, target_count: int, seen_ids: set, is_search: bool = False
    ) -> List[VideoCandidate]:
        """Extract video tweet candidates from the currently loaded Playwright page."""
        candidates = []
        tweets = page.locator("article[data-testid='tweet']").all()

        for tweet in tweets:
            if len(candidates) >= target_count:
                break

            has_video = tweet.locator("video").count() > 0
            if not has_video:
                continue

            # Extract tweet link
            link_elem = tweet.locator("a[href*='/status/']").first
            href = link_elem.get_attribute("href") if link_elem.count() > 0 else ""
            if not href:
                continue

            match = re.search(r"/status/(\d+)", href)
            if not match:
                continue

            post_id = match.group(1)
            if post_id in seen_ids:
                continue

            # Extract author/uploader
            uploader = account_or_query
            author_match = re.search(r"^/([^/]+)/status/", href)
            if author_match:
                uploader = author_match.group(1)

            # Extract text
            text_elem = tweet.locator("div[data-testid='tweetText']").first
            text = text_elem.inner_text() if text_elem.count() > 0 else f"Video from @{uploader}"

            # Extract engagement metrics from DOM
            likes = None
            comments = None
            try:
                like_btn = tweet.locator("[data-testid='like'], [data-testid='unlike']").first
                if like_btn.count() > 0:
                    aria = like_btn.get_attribute("aria-label") or ""
                    likes = parse_metric_text(aria) or parse_metric_text(like_btn.inner_text())

                reply_btn = tweet.locator("[data-testid='reply']").first
                if reply_btn.count() > 0:
                    aria = reply_btn.get_attribute("aria-label") or ""
                    comments = parse_metric_text(aria) or parse_metric_text(reply_btn.inner_text())
            except Exception:
                pass

            is_est = False
            if likes is None:
                likes = 1200
                is_est = True
            if comments is None:
                comments = max(10, int(likes * 0.05))

            seen_ids.add(post_id)
            full_url = f"https://x.com{href}" if href.startswith("/") else href

            candidates.append(VideoCandidate(
                source="twitter",
                post_id=f"twitter_{post_id}",
                post_url=full_url,
                title=text,
                uploader=uploader,
                score=int(likes),
                num_comments=int(comments),
                video_url=full_url,
                is_nsfw=False,
                extra={
                    "account": uploader,
                    "extracted_by": "playwright",
                    "source_feed": "search" if is_search else "account",
                    "is_estimated_score": is_est,
                },
            ))

        return candidates

    def _fetch_via_playwright(self, target_count: int, seen_ids: set) -> List[VideoCandidate]:
        """
        Headless Playwright browser fallback.
        Loads cookies.txt if available, scrapes accounts, and falls back to search_queries.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("playwright package not installed. Skipping Playwright fallback.")
            return []

        candidates = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            )

            # Load exported cookies.txt if provided
            add_cookies_to_playwright(context, self.cookies_file)
            page = context.new_page()

            # 1. Scrape Accounts
            for account in self.accounts:
                if len(candidates) >= target_count:
                    break

                self.rate_limiter.wait("twitter")
                url = f"https://x.com/{account}"
                logger.info(f"Navigating to {url} with Playwright...")

                try:
                    page.goto(url, timeout=20000, wait_until="domcontentloaded")
                    time.sleep(2.0)

                    # Check for login modal or challenge
                    if page.locator("text=Sign in to X").is_visible(timeout=2000):
                        logger.warning(f"X (Twitter) presented a Sign-In wall on @{account}. Export cookies.txt to authenticate.")
                        continue

                    page_cands = self._extract_candidates_from_page(
                        page, account, target_count - len(candidates), seen_ids, is_search=False
                    )
                    candidates.extend(page_cands)

                except Exception as e:
                    logger.warning(f"Playwright navigation error for @{account}: {e}")
                    continue

            # 2. Scrape search_queries if accounts were insufficient or login-walled
            if len(candidates) < target_count and self.search_queries:
                for query in self.search_queries:
                    if len(candidates) >= target_count:
                        break

                    self.rate_limiter.wait("twitter")
                    encoded = urllib.parse.quote(query)
                    search_url = f"https://x.com/search?q={encoded}&f=top"
                    logger.info(f"Searching X via Playwright: {query}...")

                    try:
                        page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
                        time.sleep(2.0)

                        if page.locator("text=Sign in to X").is_visible(timeout=2000):
                            logger.warning("X (Twitter) search requires login. Export cookies.txt to authenticate.")
                            break

                        search_cands = self._extract_candidates_from_page(
                            page, query, target_count - len(candidates), seen_ids, is_search=True
                        )
                        candidates.extend(search_cands)

                    except Exception as e:
                        logger.warning(f"Playwright search error for '{query}': {e}")
                        continue

            browser.close()

        return candidates
