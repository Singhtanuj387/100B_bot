import sqlite3
import json
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
from loguru import logger


class AgentDatabase:
    """
    SQLite persistence layer for Phase 3 Curation Agent.
    Manages:
      - LLM judgement cache (keyed by media file hash)
      - Agent decisions and reason logging (for tuning the judge)
      - LLM call logs (tokens, costs, latency)
      - Reserve pool (backfill leftover approved videos across days)
      - Schedule entries (hourly slots for Phase 4 YouTube Uploader)
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_tables(self) -> None:
        """Create necessary agent tables if they do not exist."""
        with self._get_connection() as conn:
            # 1. LLM Vision Judge Cache
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_judge_cache (
                    file_hash TEXT PRIMARY KEY,
                    funny_score REAL NOT NULL,
                    is_english_or_no_speech INTEGER NOT NULL,
                    is_safe_for_ads INTEGER NOT NULL,
                    has_visible_watermark INTEGER NOT NULL,
                    reason TEXT,
                    cached_at TEXT NOT NULL
                );
            """)

            # 2. Agent Decision Logging (Tuning History)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id TEXT NOT NULL,
                    file_hash TEXT,
                    hybrid_score REAL,
                    status TEXT NOT NULL,
                    decision_reason TEXT,
                    target_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # 3. LLM API Usage & Cost Log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL,
                    latency_s REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

            # 4. Reserve Pool (Surplus approved clips for backfill)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reserve_pool (
                    post_id TEXT PRIMARY KEY,
                    video_path TEXT NOT NULL,
                    caption TEXT,
                    title TEXT,
                    description TEXT,
                    uploader TEXT,
                    post_url TEXT,
                    score REAL,
                    duration REAL,
                    resolution TEXT,
                    status TEXT DEFAULT 'available',
                    added_at TEXT NOT NULL,
                    used_at TEXT
                );
            """)

            # 5. Daily Schedule Table for Phase 4
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_date TEXT NOT NULL,
                    slot_hour INTEGER NOT NULL,
                    slot_time TEXT NOT NULL,
                    post_id TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    title TEXT,
                    description TEXT,
                    caption TEXT,
                    source_url TEXT,
                    uploader TEXT,
                    status TEXT DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    UNIQUE(target_date, slot_hour)
                );
            """)

            # 6. Phase 3.5: Dynamic Source Registry
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    listing_mode TEXT NOT NULL DEFAULT 'hot',
                    status TEXT NOT NULL DEFAULT 'active',
                    found_count INTEGER DEFAULT 0,
                    downloaded_count INTEGER DEFAULT 0,
                    approved_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    yield_rate REAL DEFAULT 0.0,
                    consecutive_empty_runs INTEGER DEFAULT 0,
                    pagination_cursor TEXT DEFAULT NULL,
                    reject_reasons_json TEXT DEFAULT '{}',
                    added_by TEXT DEFAULT 'config',
                    last_used_date TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_type, name, listing_mode)
                );
            """)

            # 7. Phase 3.5: Daily Discovery Logs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    suggested_by_llm_json TEXT NOT NULL,
                    verified_json TEXT NOT NULL,
                    rejected_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 8. Pending Judge Retries (Videos whose judge call failed or timed out)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS judge_retries (
                    post_id TEXT PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    video_path TEXT,
                    meta_json TEXT,
                    failure_reason TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL
                );
            """)

    # --- LLM Judge Cache ---
    def get_cached_judge(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached Claude Vision evaluation for a video file hash."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM llm_judge_cache WHERE file_hash = ?", (file_hash,))
            row = cur.fetchone()
            if row:
                return {
                    "funny_score": row["funny_score"],
                    "is_english_or_no_speech": bool(row["is_english_or_no_speech"]),
                    "is_safe_for_ads": bool(row["is_safe_for_ads"]),
                    "has_visible_watermark": bool(row["has_visible_watermark"]),
                    "reason": row["reason"],
                    "cached": True,
                }
        return None

    def set_cached_judge(
        self,
        file_hash: str,
        funny_score: float,
        is_english_or_no_speech: bool,
        is_safe_for_ads: bool,
        has_visible_watermark: bool,
        reason: str,
    ) -> None:
        """Cache Claude Vision evaluation."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO llm_judge_cache (
                    file_hash, funny_score, is_english_or_no_speech,
                    is_safe_for_ads, has_visible_watermark, reason, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                file_hash, funny_score,
                1 if is_english_or_no_speech else 0,
                1 if is_safe_for_ads else 0,
                1 if has_visible_watermark else 0,
                reason, now
            ))

    # --- Decision Logging ---
    def record_decision(
        self,
        post_id: str,
        file_hash: str,
        hybrid_score: float,
        status: str,
        decision_reason: str,
        target_date: str,
    ) -> None:
        """Log video acceptance/rejection decision to tuning table."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO agent_decisions (
                    post_id, file_hash, hybrid_score, status, decision_reason, target_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (post_id, file_hash, hybrid_score, status, decision_reason, target_date, now))

    # --- Judge Failure & Retry Management ---
    def record_judge_failed(self, candidate: Dict[str, Any]) -> None:
        """
        Record a failed judge evaluation for retry in the next run.
        Also unmarks the post from scraper/data/videos.db so pre-download
        ID deduplication does not permanently block it.
        """
        post_id = candidate.get("post_id", "unknown")
        source = candidate.get("source", "unknown")
        title = candidate.get("title", "")
        video_path = str(candidate.get("video_path") or "")
        reason = candidate.get("judge_reason") or "Judge call error"
        now = datetime.now().isoformat()

        # Serialize candidate dictionary (stripping non-serializable objects)
        clean_cand = {}
        for k, v in candidate.items():
            if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                clean_cand[k] = v
        meta_json_str = json.dumps(clean_cand)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO judge_retries (
                    post_id, source, title, video_path, meta_json, failure_reason, retry_count, created_at, last_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    retry_count = judge_retries.retry_count + 1,
                    failure_reason = excluded.failure_reason,
                    last_attempt_at = excluded.last_attempt_at;
            """, (post_id, source, title, video_path, meta_json_str, reason, now, now))

        # Unmark in scraper videos.db if present so deduplication doesn't block re-scrape
        try:
            scraper_db_path = Path(__file__).resolve().parent.parent.parent / "scraper" / "data" / "videos.db"
            if scraper_db_path.is_file():
                with sqlite3.connect(str(scraper_db_path), timeout=5.0) as s_conn:
                    s_conn.execute("DELETE FROM videos WHERE post_id = ?", (post_id,))
        except Exception as e:
            logger.debug(f"Could not unmark {post_id} in scraper videos.db: {e}")

    def pop_pending_judge_retries(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """
        Fetch candidate clips that failed judging previously and are eligible for retry.
        Verifies that video media file still exists on disk.
        """
        now = datetime.now().isoformat()
        retrying = []
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT post_id, video_path, meta_json, retry_count
                FROM judge_retries
                WHERE retry_count < ?
                ORDER BY created_at ASC
            """, (max_retries,))
            rows = cur.fetchall()

            for r in rows:
                p_id = r["post_id"]
                vid_path = Path(r["video_path"]) if r["video_path"] else None
                if vid_path and vid_path.is_file():
                    try:
                        cand_dict = json.loads(r["meta_json"])
                        cand_dict["is_retry"] = True
                        cand_dict["retry_count"] = r["retry_count"] + 1
                        # Reset failure flags for the new evaluation
                        cand_dict.pop("judge_failed", None)
                        if cand_dict.get("status") == "judge_failed":
                            cand_dict["status"] = "pending_judge"
                        retrying.append(cand_dict)
                    except Exception:
                        pass

            if retrying:
                conn.execute("""
                    UPDATE judge_retries
                    SET retry_count = retry_count + 1, last_attempt_at = ?
                    WHERE post_id IN ({})
                """.format(",".join("?" for _ in retrying)), [now] + [c["post_id"] for c in retrying])

        return retrying

    def clear_judge_retry(self, post_id: str) -> None:
        """Clear a candidate from judge_retries once a final decision is reached."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM judge_retries WHERE post_id = ?", (post_id,))

    # --- LLM API Logging ---
    def log_llm_call(
        self,
        task_type: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_s: float,
    ) -> None:
        """Log token count and dollar spend."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO llm_logs (
                    task_type, model, input_tokens, output_tokens, cost_usd, latency_s, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (task_type, model, input_tokens, output_tokens, cost_usd, latency_s, now))

    # --- Reserve Pool Management ---
    def add_to_reserve_pool(self, video_data: Dict[str, Any]) -> None:
        """Add surplus approved video to reserve pool for future days."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO reserve_pool (
                    post_id, video_path, caption, title, description,
                    uploader, post_url, score, duration, resolution, status, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?)
            """, (
                video_data["post_id"],
                video_data["video_path"],
                video_data.get("caption"),
                video_data.get("title"),
                video_data.get("description"),
                video_data.get("uploader"),
                video_data.get("post_url"),
                video_data.get("score"),
                video_data.get("duration"),
                video_data.get("resolution"),
                now,
            ))

    def fetch_reserve_pool_candidates(self, limit: int) -> List[Dict[str, Any]]:
        """Retrieve available reserve videos to backfill shortage."""
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT * FROM reserve_pool
                WHERE status = 'available'
                ORDER BY score DESC, added_at ASC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def mark_reserve_used(self, post_ids: List[str]) -> None:
        """Mark reserved videos as used so they are not scheduled twice."""
        if not post_ids:
            return
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            placeholders = ",".join(["?"] * len(post_ids))
            conn.execute(f"""
                UPDATE reserve_pool
                SET status = 'used', used_at = ?
                WHERE post_id IN ({placeholders})
            """, [now] + post_ids)

    def mark_reserve_missing(self, post_ids: List[str]) -> None:
        """Mark reserved videos as missing if their files no longer exist on disk."""
        if not post_ids:
            return
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            placeholders = ",".join(["?"] * len(post_ids))
            conn.execute(f"""
                UPDATE reserve_pool
                SET status = 'missing', used_at = ?
                WHERE post_id IN ({placeholders})
            """, [now] + post_ids)

    # --- Schedule Management ---
    def save_schedule_slots(self, target_date: str, slots: List[Dict[str, Any]]) -> None:
        """Save final 24-hour schedule to SQLite database."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            for s in slots:
                conn.execute("""
                    INSERT OR REPLACE INTO schedule (
                        target_date, slot_hour, slot_time, post_id, video_path,
                        title, description, caption, source_url, uploader, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """, (
                    target_date,
                    s["slot_hour"],
                    s["slot_time"],
                    s["post_id"],
                    s["video_path"],
                    s.get("title", ""),
                    s.get("description", ""),
                    s.get("caption", ""),
                    s.get("source_url", ""),
                    s.get("uploader", ""),
                    now,
                ))

    # --- Phase 3.5: Dynamic Source Registry Management ---
    def seed_default_sources(self, subreddits: Optional[List[str]] = None) -> None:
        """Seed default active subreddits and multi-window archival streams into sources table if empty."""
        default_subs = subreddits or [
            "funny",
            "Unexpected",
            "holdmybeer",
            "Whatcouldgowrong",
            "instant_regret",
            "oddlysatisfying",
            "AnimalsBeingDerps",
            "ContagiousLaughter",
            "Catculations",
            "IdiotsInCars",
            "therewasanattempt",
            "MadeMeSmile",
        ]
        default_listings = ["hot", "top_week", "top_month", "top_year"]
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            for sub in default_subs:
                for l in default_listings:
                    conn.execute("""
                        INSERT OR IGNORE INTO sources (
                            source_type, name, listing_mode, status, added_by, created_at, updated_at
                        ) VALUES ('subreddit', ?, ?, 'active', 'config', ?, ?)
                    """, (sub, l, now, now))

    def get_sources_for_selection(
        self,
        total_budget: int = 10,
        active_ratio: float = 0.8,
        trial_ratio: float = 0.2,
    ) -> List[Dict[str, Any]]:
        """Select ~80% active sources (ranked by yield) and ~20% trial sources for current fetch."""
        self.seed_default_sources()
        num_active = max(1, int(round(total_budget * active_ratio)))
        num_trial = max(1, total_budget - num_active)
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            # Auto-reset exhausted archive sources older than 7 days back to active
            conn.execute("""
                UPDATE sources
                SET status = 'active', consecutive_empty_runs = 0, pagination_cursor = NULL, updated_at = ?
                WHERE status = 'exhausted' AND (julianday(?) - julianday(updated_at)) >= 7.0
            """, (now, now))

            cur = conn.execute("""
                SELECT * FROM sources
                WHERE status = 'active'
                ORDER BY yield_rate DESC, approved_count DESC, id ASC
                LIMIT ?
            """, (num_active,))
            active_rows = [dict(r) for r in cur.fetchall()]

            cur = conn.execute("""
                SELECT * FROM sources
                WHERE status = 'trial'
                ORDER BY last_used_date ASC NULLS FIRST, id ASC
                LIMIT ?
            """, (num_trial,))
            trial_rows = [dict(r) for r in cur.fetchall()]

            if len(trial_rows) < num_trial:
                needed = num_trial - len(trial_rows)
                placeholders = ",".join([str(r["id"]) for r in active_rows]) if active_rows else "0"
                cur = conn.execute(f"""
                    SELECT * FROM sources
                    WHERE status = 'active' AND id NOT IN ({placeholders})
                    ORDER BY yield_rate DESC, id ASC
                    LIMIT ?
                """, (needed,))
                active_rows.extend([dict(r) for r in cur.fetchall()])

            selected = active_rows + trial_rows
            return selected[:total_budget]

    def get_deeper_archive_sources(self, loop_count: int = 2, limit: int = 6) -> List[Dict[str, Any]]:
        """
        Phase 3.6: Fetch deeper pagination pages and alternative time windows
        (top_month, top_year, top_all) for active sources before requesting new sources.
        """
        self.seed_default_sources()
        target_listings = ["top_month", "top_year"] if loop_count == 2 else ["top_year", "top_all", "top_month"]
        placeholders = ",".join(["?"] * len(target_listings))

        with self._get_connection() as conn:
            # First, check active sources in target archive time windows (prioritizing existing cursors)
            cur = conn.execute(f"""
                SELECT * FROM sources
                WHERE status = 'active' AND listing_mode IN ({placeholders})
                ORDER BY (pagination_cursor IS NOT NULL) DESC, yield_rate DESC, id ASC
                LIMIT ?
            """, target_listings + [limit])
            rows = [dict(r) for r in cur.fetchall()]

            if len(rows) < limit:
                # Supplement with active sources that have saved cursors
                existing_ids = [str(r["id"]) for r in rows] or ["0"]
                id_placeholders = ",".join(existing_ids)
                cur = conn.execute(f"""
                    SELECT * FROM sources
                    WHERE status = 'active' AND id NOT IN ({id_placeholders}) AND listing_mode != 'hot'
                    ORDER BY (pagination_cursor IS NOT NULL) DESC, yield_rate DESC
                    LIMIT ?
                """, (limit - len(rows),))
                rows.extend([dict(r) for r in cur.fetchall()])

            return rows[:limit]

    def add_trial_sources(
        self,
        sources_list: List[Dict[str, Any]],
        run_date: str,
        max_daily_trials: int = 5,
    ) -> int:
        """Add newly discovered and verified trial sources, capped at 5 per day."""
        now = datetime.now().isoformat()
        added_count = 0
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT COUNT(*) as count FROM sources
                WHERE added_by = 'llm' AND date(created_at) = date(?)
            """, (now,))
            today_count = cur.fetchone()["count"]
            remaining_quota = max(0, max_daily_trials - today_count)

            for s in sources_list:
                if added_count >= remaining_quota:
                    break
                s_type = s.get("source_type", "subreddit")
                s_name = s.get("name", "").strip()
                s_mode = s.get("listing_mode", "hot")
                if not s_name:
                    continue

                try:
                    cur = conn.execute("""
                        INSERT INTO sources (
                            source_type, name, listing_mode, status, added_by, created_at, updated_at
                        ) VALUES (?, ?, ?, 'trial', 'llm', ?, ?)
                    """, (s_type, s_name, s_mode, now, now))
                    if cur.rowcount > 0:
                        added_count += 1
                except sqlite3.IntegrityError:
                    pass

        return added_count

    def update_source_metrics(
        self,
        source_type: str,
        name: str,
        listing_mode: str,
        found: int = 0,
        downloaded: int = 0,
        approved: int = 0,
        rejected: int = 0,
        reject_reasons: Optional[Dict[str, int]] = None,
        cursor: Optional[str] = None,
        run_date: Optional[str] = None,
    ) -> None:
        """
        Update source yield stats after final decision.
        Promotes trial sources (>=10 downloads, yield >= 0.30).
        Pauses underperforming sources (>=15 downloads, yield < 0.15).
        Marks exhausted archive sources (>=3 consecutive empty runs).
        """
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT * FROM sources
                WHERE source_type = ? AND name = ? AND listing_mode = ?
            """, (source_type, name, listing_mode))
            row = cur.fetchone()

            if not row:
                conn.execute("""
                    INSERT INTO sources (
                        source_type, name, listing_mode, status, found_count, downloaded_count,
                        approved_count, rejected_count, yield_rate, added_by, created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, 0.0, 'auto', ?, ?)
                """, (source_type, name, listing_mode, found, downloaded, approved, rejected, now, now))
                cur = conn.execute("""
                    SELECT * FROM sources
                    WHERE source_type = ? AND name = ? AND listing_mode = ?
                """, (source_type, name, listing_mode))
                row = cur.fetchone()

            new_found = row["found_count"] + found
            new_downloaded = row["downloaded_count"] + downloaded
            new_approved = row["approved_count"] + approved
            new_rejected = row["rejected_count"] + rejected

            if new_downloaded > 0:
                smoothed_yield = round((new_approved + 1.0) / (new_downloaded + 3.0), 4)
            else:
                smoothed_yield = 0.0

            current_reasons = {}
            if row["reject_reasons_json"]:
                try:
                    current_reasons = json.loads(row["reject_reasons_json"])
                except Exception:
                    current_reasons = {}
            if reject_reasons:
                for k, v in reject_reasons.items():
                    current_reasons[k] = current_reasons.get(k, 0) + v

            if found == 0:
                new_empty = row["consecutive_empty_runs"] + 1
            else:
                new_empty = 0

            status = row["status"]
            if new_empty >= 3 and listing_mode in ["top_week", "top_month", "top_year", "top_all"]:
                status = "exhausted"
            elif status == "trial" and new_downloaded >= 10:
                if smoothed_yield >= 0.30:
                    status = "active"
                    logger.info(f"Promoted source ({source_type}, {name}, {listing_mode}) to ACTIVE (yield={smoothed_yield:.2f})")
                elif smoothed_yield < 0.15:
                    status = "paused"
                    logger.info(f"Paused trial source ({source_type}, {name}, {listing_mode}) due to low yield ({smoothed_yield:.2f})")
            elif status == "active" and new_downloaded >= 15:
                if smoothed_yield < 0.15:
                    status = "paused"
                    logger.info(f"Paused active source ({source_type}, {name}, {listing_mode}) due to low yield ({smoothed_yield:.2f})")

            new_cursor = cursor or row["pagination_cursor"]
            if listing_mode == "hot":
                new_cursor = None
            conn.execute("""
                UPDATE sources
                SET found_count = ?, downloaded_count = ?, approved_count = ?, rejected_count = ?,
                    yield_rate = ?, consecutive_empty_runs = ?, pagination_cursor = ?,
                    reject_reasons_json = ?, status = ?, last_used_date = ?, updated_at = ?
                WHERE id = ?
            """, (
                new_found, new_downloaded, new_approved, new_rejected,
                smoothed_yield, new_empty, new_cursor,
                json.dumps(current_reasons), status, run_date or now, now,
                row["id"]
            ))

    def log_discovery(
        self,
        run_date: str,
        suggested: List[Dict[str, Any]],
        verified: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
    ) -> None:
        """Record daily LLM discovery outcome."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO discovery_logs (run_date, suggested_by_llm_json, verified_json, rejected_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                run_date,
                json.dumps(suggested),
                json.dumps(verified),
                json.dumps(rejected),
                now,
            ))

    def get_performance_summary(self) -> Dict[str, Any]:
        """Aggregate stats for fresh context collector."""
        self.seed_default_sources()
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM sources WHERE status != 'exhausted' ORDER BY yield_rate DESC")
            all_sources = [dict(r) for r in cur.fetchall()]

            cur = conn.execute("SELECT DISTINCT name FROM sources")
            already_tried = [r["name"] for r in cur.fetchall()]

            cur = conn.execute("""
                SELECT decision_reason, COUNT(*) as cnt
                FROM agent_decisions
                WHERE status = 'rejected'
                GROUP BY decision_reason
                ORDER BY cnt DESC
                LIMIT 5
            """)
            top_reasons = [{"reason": r["decision_reason"], "count": r["cnt"]} for r in cur.fetchall()]

            return {
                "active_sources": [s for s in all_sources if s["status"] == "active"],
                "trial_sources": [s for s in all_sources if s["status"] == "trial"],
                "already_tried_names": already_tried,
                "top_reject_reasons": top_reasons,
            }

