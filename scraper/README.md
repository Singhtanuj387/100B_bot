# 🕵️ Automated Funny Video Scraper (Phase 2)

A modular, production-ready video discovery and ingestion engine designed to source **24+ unique, high-engagement funny videos daily** from **Reddit** and **X (Twitter)** for automated YouTube Shorts and social publishing.

---

## 📁 Project Architecture

```
scraper/
  scrape.py            # CLI entry point & execution orchestrator
  config.yaml          # All operational parameters (targets, subreddits, accounts, filters)
  .env.example         # Template for Reddit API keys and credentials
  sources/
    __init__.py        # Plugin registry
    base.py            # Common abstract interface (VideoCandidate & BaseSource)
    reddit.py          # Reddit multi-layer scraper (PRAW -> RSS -> JSON)
    twitter.py         # X (Twitter) multi-layer scraper (yt-dlp -> Playwright)
  core/
    __init__.py        # Core package exports
    http.py            # Resilient HTTP client (UA rotation, rate limits, exponential backoff)
    db.py              # SQLite database (data/videos.db) tracking seen videos & history
    dedupe.py          # 3-level deduplication (ID/URL, SHA-256 hash, 3-frame perceptual hash)
    filters.py         # Quality, safety, duration, resolution & English language filters
    captions.py        # Punchy social caption generator & cleaner (max ~8 words)
    downloader.py      # Atomic parallel downloader with yt-dlp & FFmpeg audio/video merging
  data/
    raw/               # Downloaded media partitioned by date (e.g. data/raw/2026-11-12/)
    videos.db          # SQLite deduplication and history database
  logs/                # Scraper execution logs with automatic rotation
  tests/               # Pytest test suite for filters, captions, and deduplication
  README.md            # Full setup and operational guide
```

---

## ⚡ Key Features

- **Layered Sourcing**:
  - **Reddit**: Layer 1 (Official PRAW API via `.env`) $\rightarrow$ Layer 2 (Open RSS/Atom feeds) $\rightarrow$ Layer 3 (Public JSON listings).
  - **X (Twitter)**: Layer 1 (`yt-dlp` with optional cookies) $\rightarrow$ Layer 2 (Headless Playwright Chromium fallback).
- **3-Level Deduplication (Zero Reposts)**:
  1. **Post ID & URL**: Checks SQLite database before downloading.
  2. **File Content Hash**: SHA-256 binary hash verification.
  3. **Perceptual Frame Hashing**: Extracts 3 video keyframes (15%, 50%, 85%) via OpenCV and computes difference hashes (`dhash`). Catches identical clips reposted by different users or cross-posted between Reddit and X.
- **Audio-Video Stream Merging**: Reddit videos use DASH separate audio and video streams; the scraper automatically downloads and merges them with FFmpeg so audio is never lost.
- **Quality & Safety Filters**:
  - Duration: 5.0 to 60.0 seconds (configurable).
  - Minimum resolution: 480p.
  - Content safety: Blocks Reddit NSFW flag + keyword blocklist (gore, politics, tragedy, war, death).
  - Language: Verifies English or universal titles.
- **Punchy Social Captions**: Automatically converts verbose Reddit/X titles into clean captions (max ~8 words) stripping links, hashtags, usernames, and `[OC]` tags while preserving natural emojis.
- **Atomic Safety**: Downloads to temporary files first; moves to final location only after probing and verifying video integrity. Never leaves partial or broken files.

---

## 🚀 Setup Guide

### 1. Install Dependencies
```bash
cd scraper
pip install -r ../requirements.txt
pip install yt-dlp loguru praw imagehash opencv-python-headless playwright
playwright install chromium
```

### 2. Configure Reddit API Credentials (Layer 1)
Reddit's official API is free and provides reliable, unthrottled access:
1. Log in to your Reddit account.
2. Go to **[https://www.reddit.com/prefs/apps](https://www.reddit.com/prefs/apps)**.
3. Scroll down and click **"are you a developer? create an app..."** (or "create another app").
4. Fill in:
   - **Name**: `FunnyVideoScraper`
   - **Type**: Select **script**
   - **Description**: `Automated video scraper for personal YouTube channel`
   - **About url**: `http://localhost`
   - **Redirect uri**: `http://localhost:8080`
5. Click **Create app**.
6. Note your credentials:
   - **Client ID**: The string right under the app name (e.g. `14_characters_here`).
   - **Client Secret**: The `secret` string.
7. Copy `.env.example` to `.env` and fill in:
   ```env
   REDDIT_CLIENT_ID=your_client_id_here
   REDDIT_CLIENT_SECRET=your_client_secret_here
   REDDIT_USER_AGENT=python:funny_scraper:v1.0 (by /u/YOUR_USERNAME)
   ```

### 3. Configure X (Twitter) Cookies (Optional)
Because X requires authentication for video feeds:
1. Log in to [x.com](https://x.com) in Chrome or Firefox.
2. Install the browser extension **"Get cookies.txt LOCALLY"**.
3. Click the extension icon on x.com and click **Export**.
4. Save the file as `scraper/cookies.txt`.
5. The scraper will automatically pass this file to `yt-dlp` and Playwright.

---

## 🛠 Usage & CLI Commands

### 1. Dry Run (Test without downloading media)
Finds candidates, tests filters, ranks engagement, and prints candidate summary:
```bash
python scrape.py --target 10 --sources reddit,twitter --dry-run
```

### 2. Standard Production Run (Download 40 unique videos)
```bash
python scrape.py --target 40 --sources reddit,twitter
```

### 3. Single Source Execution
```bash
# Scrape only Reddit
python scrape.py --target 25 --sources reddit

# Scrape only X (Twitter)
python scrape.py --target 15 --sources twitter
```

### 4. Custom Date Partition
```bash
python scrape.py --target 40 --date 2026-11-15
```

---

## 📦 Output Structure

Downloaded videos are organized in date-stamped folders ready for Phase 1 (Video Editor) and Phase 3 (YouTube Uploader):

```
data/
  raw/
    2026-11-12/
      reddit_1xsrkbt/
        video.mp4      # Merged 1080p/720p video with synced AAC audio
        caption.txt    # Clean punchy caption (e.g. "Why he is the GOAT 😂")
        meta.json      # Full provenance: source, uploader, post_url, duration, hashes
      twitter_18529381/
        video.mp4
        caption.txt
        meta.json
  videos.db            # SQLite database tracking all historical downloads & seen IDs
```

### Sample `meta.json`
```json
{
  "source": "reddit",
  "post_id": "reddit_1xsrkbt",
  "post_url": "https://www.reddit.com/r/funny/comments/1xsrkbt/why_hes_the_goat/",
  "uploader": "TheMemerGuy",
  "original_title": "[OC] Why he is the GOAT! (watch till the end)",
  "caption": "Why he is the GOAT 😂",
  "score": 8500,
  "duration": 22.4,
  "resolution": "1080x1920",
  "file_hash": "edad2da9921c3547006fb86ff87986b3ac9a0d808c004c4805e6054879c7b5e2",
  "phashes": ["f888c453494d36c0", "63722673fb16430f", "b2c6d6ce48163d7c"],
  "scraped_at": "2026-11-12",
  "video_path": "C:\\Drive-D\\python tut\\100B\\scraper\\data\\raw\\2026-11-12\\reddit_1xsrkbt\\video.mp4",
  "caption_path": "C:\\Drive-D\\python tut\\100B\\scraper\\data\\raw\\2026-11-12\\reddit_1xsrkbt\\caption.txt"
}
```

---

## 🔗 Integration with Next Phases (Phase 3 Uploader / Agent)

The scraper can be imported directly as a Python library by subsequent phases:

```python
from scraper.scrape import get_videos_for_date

# Retrieve all ready videos for a specific day
videos = get_videos_for_date("2026-11-12")

for v in videos:
    print(f"Post ID   : {v['post_id']}")
    print(f"Caption   : {v['caption']}")
    print(f"Uploader  : {v['uploader']}")
    print(f"Credit URL: {v['post_url']}")
    print(f"File Path : {v['file_path']}")
    # Feed into video_editor.py or upload to YouTube
```

---

## ⚠️ Known Weak Points & Mitigation Guide

### 1. X (Twitter) Fragility & Anti-Scraping
- **Weak Point**: X frequently updates DOM structures, enforces strict rate limits, and displays "Sign in to X" interstitials to unauthenticated IP addresses.
- **Mitigation**:
  1. **Layered Fallback**: `yt-dlp` runs first. If it cannot resolve the tweet, Playwright headless Chromium is engaged.
  2. **Isolated Failures**: If X returns 429, login walls, or blocks requests, `sources/twitter.py` logs a clear warning, marks the source as throttled, and lets Reddit finish the quota. The pipeline never crashes.
  3. **Exported Cookies**: Supplying `cookies.txt` bypasses virtually all unauthenticated walls on both `yt-dlp` and Playwright.

### 2. Reddit Unauthenticated CDN Throttling
- **Weak Point**: Reddit's Fastly CDN returns `HTTP 403 / 429` on unauthenticated `.json` requests from data centers and certain residential IPs.
- **Mitigation**:
  1. **Official PRAW Credentials (Layer 1)**: Free Reddit script apps obtain an OAuth token with a generous quota of 60 requests/minute.
  2. **Circuit Breaker**: If unauthenticated RSS/JSON endpoints return 3 consecutive rate limits, the scraper breaks early and notifies the user instead of hanging or spamming.

### 3. Cross-Platform Reposts
- **Weak Point**: Viral clips are frequently downloaded from TikTok, re-uploaded to Reddit, and cross-posted to X under different titles and file formats.
- **Mitigation**:
  - The 3-frame perceptual difference hash (`imagehash.dhash` at 15%, 50%, and 85% duration) compares visual frame structure with a Hamming distance threshold $\le 6$. This catches reposts even if the video has been slightly trimmed, re-encoded, or re-compressed.

