# Phase 3.5: Dynamic Discovery Stage & Adaptive Learning Implementation Plan (Revised)

This document contains the finalized, production-ready specification for the **Phase 3.5 Dynamic Discovery & Adaptive Learning System** for the LangGraph Curation Agent. All feedback points—including the two-step scraper, composite registry keys, archive cursor pagination, post-decision tracking, and careful title pre-screening—are fully incorporated.

---

## 1. Updated Architecture & LangGraph Pipeline Topology

```mermaid
flowchart TD
    START([START]) --> COLLECT[Fresh Context Collector<br/>No LLM: Top 25 from 5 Seeds + r/all + Stats]
    COLLECT --> DISCOVER[discover Node (Loop 1 Only)<br/>LLM: Suggests up to 10 Subreddits & 10 Queries]
    DISCOVER --> VERIFY[verify Node<br/>No LLM: Validates exists, not NSFW, video content, caps 5 trial/day]
    VERIFY --> SELECT[80/20 Bandit Source Selector<br/>80% Best Active by Yield, 20% Trial Sources]
    SELECT --> SCRAPE_LIST[Scrape Tool: --list-only<br/>Fetches candidate metadata: title, URL, source, listing]
    SCRAPE_LIST --> PRESCREEN[Title Pre-Screen Node<br/>Cheap Text LLM: Batched 30 titles/call; Drops ONLY obvious non-comedy]
    PRESCREEN --> DOWNLOAD[Parallel Downloader: --download-only<br/>Downloads ONLY surviving candidate videos via FFmpeg]
    DOWNLOAD --> CHEAP_FILTERS[Cheap Filters & Whisper VAD<br/>Resolution >= 480p, Duration 5-60s, English audio]
    CHEAP_FILTERS --> JUDGE[Calibrated Vision Judge<br/>Parallel workers, 1-10 Rubric, TPM-controlled]
    JUDGE --> DECIDE[Decide Node<br/>Auto-approve >=8.0, Auto-reject <5.0, Borderline 5.0-7.9]
    DECIDE --> BORDERLINE_RANK[Rank Borderline Node<br/>LLM Comparative Ranking for ties / review]
    BORDERLINE_RANK --> REVIEW[Human Review / Unattended Auto-Resolve]
    REVIEW --> BACKFILL[Backfill from Reserve Pool]
    BACKFILL --> UPDATE_REGISTRY[Update Source Registry<br/>Record final outcomes per source; update yield & cursor]
    UPDATE_REGISTRY --> CAPTION[Caption & Edit Nodes]
    CAPTION --> SCHEDULE[Schedule & Telegram Alert]
    SCHEDULE --> END_NODE([END])
```

---

## 2. Detailed Technical Specifications

### Requirement 1: Split Scraper into Two Distinct Steps
In [`scraper/scrape.py`](file:///C:/Drive-D/python%20tut/100B/scraper/scrape.py) and [`agent/tools/scraper_tool.py`](file:///C:/Drive-D/python%20tut/100B/agent/tools/scraper_tool.py):
1. **Step 1: Listing (`--list-only`)**:
   - Accepts sources, search queries, and listing modes.
   - Discovers candidate posts and performs initial URL/ID deduplication.
   - Outputs candidates as structured JSON to `data/candidates/<date>_candidates.json` containing: `post_id`, `title`, `post_url`, `video_url`, `source_type`, `source_name`, `listing_mode`, `score`, `upvote_ratio`, `num_comments`.
   - **Zero video files downloaded** in this step.
2. **Title Pre-Screen (Agent Step)**:
   - Evaluates the JSON candidate list in text batches.
   - Flags dropped titles with clear reasons (`news/politics`, `text meme`, `broadcast interview`).
3. **Step 2: Downloading (`--download-only`)**:
   - Takes the surviving vetted candidate IDs/objects.
   - Invokes the parallel FFmpeg HLS muxer using `cookies.txt` to download only high-potential videos.
   - Saves `meta.json` in each video folder with `source_type`, `source_name`, `listing_mode`, and original metadata for end-to-end auditability.

---

### Requirement 2: Registry Composite Key
In [`agent/core/db.py`](file:///C:/Drive-D/python%20tut/100B/agent/core/db.py):
* Table `sources` is uniquely constrained on `(source_type, name, listing_mode)`.
```sql
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,          -- 'subreddit', 'search_query', 'x_account'
    name TEXT NOT NULL,                 -- e.g. 'funny', 'fail compilation'
    listing_mode TEXT NOT NULL,         -- 'hot', 'top_day', 'top_week', 'top_month', 'top_year', 'top_all'
    status TEXT DEFAULT 'trial',        -- 'active', 'trial', 'paused', 'exhausted'
    found INTEGER DEFAULT 0,
    downloaded INTEGER DEFAULT 0,
    approved INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    yield_rate REAL DEFAULT 0.0,        -- approved / max(1, downloaded)
    pagination_cursor TEXT DEFAULT '',  -- Reddit pagination token ('after' parameter or page index)
    consecutive_dup_runs INTEGER DEFAULT 0, -- Count of runs yielding 100% duplicates
    last_used TEXT,
    added_by TEXT DEFAULT 'config',     -- 'config' or 'llm_discovery'
    reject_reasons_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, name, listing_mode)
);
```

---

### Requirement 3: Archive Listing Pagination & Exhaustion
For historical listings (`top_week`, `top_month`, `top_year`, `top_all`):
* `pagination_cursor` stores the Reddit `after` token (e.g. `t3_1x...`) or offset.
* When scraping an archive listing:
  1. The scraper passes the saved `after` cursor to continue where it left off yesterday instead of pulling the same top posts.
  2. Updates `pagination_cursor` with the new `after` token from the response.
  3. If a run yields zero new candidates (all duplicate IDs), `consecutive_dup_runs` increments.
  4. When `consecutive_dup_runs >= 2`, status transitions to `'exhausted'` to prevent wasting future request quota.

---

### Requirement 4: Post-Decision Registry Updates & Traceability
1. **Traceability in `meta.json`**:
   Every raw video folder [`scraper/data/raw/<date>/<post_id>/meta.json`](file:///C:/Drive-D/python%20tut/100B/scraper/data/raw) must contain:
   ```json
   {
     "post_id": "reddit_1xvewbj",
     "source_type": "subreddit",
     "source_name": "Unexpected",
     "listing_mode": "top_week",
     "title": "...",
     "score": 8500
   }
   ```
2. **Update Placement**:
   - The registry update runs **after all final decisions** (auto-approved, borderline ranked, human review/unattended auto-resolve, and backfill).
   - This guarantees that every final approval or rejection is mapped with 100% accuracy to the originating `(source_type, name, listing_mode)`.

---

### Requirement 5: Source Verification Without Brittle RSS Dependencies
In `agent/core/source_verifier.py`:
* Verification inspects the public subreddit metadata:
  1. Calls `https://www.reddit.com/r/{sub}/about.json` (or verified feed with feed-reader User-Agent).
  2. Confirms:
     - `over18` is `false` (Strict NSFW gate).
     - Subreddit exists and is public (`subscribers >= 10,000` to ensure sufficient video volume).
     - Contains recent activity within 72 hours.
  3. Sample checks the listing for real video embeds (`v.redd.it`).
* **Daily Trial Cap**: Strictly caps newly admitted trial sources at **5 per day**.
* **Commercial API Terms Note**: We preserve support for PRAW credentials in `scraper/.env`, while ensuring open-feed scraping operates cleanly as a resilient non-commercial research/curation fallback.

---

### Requirement 6: Conservative Title Pre-Screening
In `agent/llm/title_prescreen.py`:
* **Batched Text Prompt**: Batches up to 30 titles per call to minimize token usage.
* **Context Provided**: Includes `title`, `source_name`, `score`, and `upvote_ratio`.
* **Conservative Rule**:
  - **NEVER drop vague or contextual titles**: Titles like *"Maybe Maybe Maybe"*, *"An interesting title"*, *"Watch till the end"*, or *"Wait for it"* are common in top comedy subreddits and must be **kept**.
  - **ONLY drop clear non-comedy / high-risk categories**:
    - News / political debates / election coverage.
    - Text memes, Q&A discussions, advice requests.
    - Studio TV broadcasts, late-night talk shows, podcast interviews.
    - Serious injury, tragedy, violence.
* **Audit Logging**: Every dropped title and its drop reason are logged to `title_prescreen_drops.jsonl` for easy inspection.

---

### Requirement 7: Smoothed Promotion & Pause Rules
In [`agent/core/db.py`](file:///C:/Drive-D/python%20tut/100B/agent/core/db.py):
* **Promotion Rule (Trial $\to$ Active)**:
  - Requires at least **10 evaluated videos** (`downloaded >= 10`) to eliminate sample noise.
  - Requires smoothed yield rate:
    $$\text{Yield Rate} = \frac{\text{Approved}}{\text{Downloaded}} \ge 0.30$$
* **Pause Rule (Active/Trial $\to$ Paused)**:
  - Requires at least **15 evaluated videos** (`downloaded >= 15`).
  - Paused if:
    $$\text{Yield Rate} < 0.15$$

---

### Requirement 8: Token-Aware Rate Limiter (TPM Bottleneck)
In [`agent/llm/client.py`](file:///C:/Drive-D/python%20tut/100B/agent/llm/client.py):
* Acknowledges that with 4 keyframes per video (~1,200 input tokens per call), **16k TPM is reached at ~10-12 calls per minute**, well before 30 RPM.
* The rate limiter uses a dual sliding-window:
  - **TPM Window**: Caps rolling 60s input tokens at **13,600 TPM** (85% buffer under 16k).
  - **Worker Throughput**: With 4 parallel workers, throughput naturally stabilizes at **~5–8 videos per minute**.
  - If a thread's estimated tokens would breach 13.6k TPM, that worker sleeps until older tokens age out.

---

### Requirement 9: Verified Model Names in AI Studio
Based on direct `client.models.list()` query on your key:
* **Primary Models**:
  - `gemma-4-31b-it` (Gemma 4 31B — text & vision)
  - `gemini-3.8-flash` (Gemini 3.8 Flash — high-demand fallback)
* Both models are confirmed live and accessible.

---

### Requirement 10: Discovery vs. Plan Lifecycle
* **`discover` Node**: Runs **exactly once per day** during **Loop 1**.
* **Loops 2+ (Adaptive Re-planning)**:
  - Do **NOT** call the discovery LLM again (preserving your 1–3 daily discovery call cap).
  - Instead, the planner queries the `sources` registry in SQLite to pull the next best active sources or secondary listing modes (`top_week`, `top_month`).

---

### Requirement 11: Resilience Goal
* Pipeline resilience goal: **"All network, server, and model errors (such as 500/503 spikes or 429 bursts) are caught, retried with exponential backoff, and never crash the curation run."**

---

## 3. Step-by-Step Implementation Order

1. **Step 1: Scraper Split (`--list-only` and `--download-only`)**
   - Update `scraper/scrape.py` and `scraper/core/downloader.py` to support two-stage metadata listing and targeted downloading.
   - Update `meta.json` output to save `source_type`, `source_name`, and `listing_mode`.
2. **Step 2: Source Registry Schema with Composite Key & Cursors**
   - Update `agent/core/db.py` with `UNIQUE(source_type, name, listing_mode)` and pagination cursor columns.
   - Implement `seed_default_sources()`, `get_sources_for_selection()`, and smoothed `auto_promote_and_pause()`.
3. **Step 3: Fresh Context Collector & Source Verifier**
   - Build `agent/core/context_collector.py` (seed multireddit + yesterday's stats).
   - Build `agent/core/source_verifier.py` (NSFW check, exists check, cap 5 trial/day).
4. **Step 4: LLM Schemas & Prompts**
   - Add `DiscoveryResult`, `TitlePrescreenResult`, `BorderlineRankResult` in `agent/llm/schemas.py`.
   - Add calibrated 1–10 rubric and cautious title pre-screen prompt in `agent/llm/prompts.py`.
5. **Step 5: Title Pre-Screening & Comparative Borderline Ranker**
   - Build `agent/llm/title_prescreen.py`.
   - Update `agent/graph/nodes/judge.py` with parallel workers and comparative ranking.
6. **Step 6: LangGraph Node Integration**
   - Integrate `discover_node`, `verify_node`, and post-decision registry updates in `agent/graph/build_graph.py`.
7. **Step 7: Unit Testing & End-to-End Verification**
   - Write comprehensive unit tests with mocked LLM for all new components.
   - Verify all tests pass with zero unhandled exceptions.
