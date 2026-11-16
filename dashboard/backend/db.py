import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

# Decoupled database accessor: plain sqlite3, mode=ro (read-only), NO core imports
WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = WORKSPACE_DIR / "agent" / "data" / "agent.db"


def get_ro_connection() -> sqlite3.Connection:
    """Return a read-only SQLite connection using SQLite URI mode."""
    db_file = DB_PATH.resolve()
    if not db_file.is_file():
        # If DB file doesn't exist yet, connect to in-memory or fallback
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn
    # Windows-safe file URI: file:///C:/path/agent.db?mode=ro
    uri = f"file:///{db_file.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def get_pipeline_status() -> Dict[str, Any]:
    """Return last run info from daily_runs and next run time."""
    with get_ro_connection() as conn:
        try:
            row = conn.execute("""
                SELECT * FROM daily_runs ORDER BY id DESC LIMIT 1
            """).fetchone()
        except sqlite3.OperationalError:
            row = None

    last_run = None
    if row:
        last_run = {
            "id": row["id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "status": row["status"],
            "uploaded": row["uploaded"],
            "scheduled": row["scheduled"],
            "failed": row["failed"],
            "message": row["message"],
        }

    # Channel timezone (America/New_York)
    tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(tz)
    # Next daily run is scheduled at 18:00 (6:00 PM) channel time
    next_run_dt = now_ny.replace(hour=18, minute=0, second=0, microsecond=0)
    if now_ny >= next_run_dt:
        from datetime import timedelta
        next_run_dt += timedelta(days=1)

    return {
        "last_run": last_run,
        "next_run_time": next_run_dt.isoformat(),
        "channel_timezone": "America/New_York",
    }


def is_pipeline_active() -> bool:
    """Check if daily_run.lock exists and its PID is alive."""
    lock_file = WORKSPACE_DIR / "daily_run.lock"
    if not lock_file.is_file():
        return False
    try:
        content = json.loads(lock_file.read_text(encoding="utf-8"))
        pid = int(content.get("pid", 0))
        if pid <= 0:
            return False
        import sys
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def get_agent_days(num_days: int = 7) -> List[Dict[str, Any]]:
    """
    Return summary statistics for the last `num_days` days for the day picker.
    Pulls from run_stats, falling back to deduplicated agent_decisions if run_stats is missing.
    """
    tz = ZoneInfo("America/New_York")
    today_str = datetime.now(tz).strftime("%Y-%m-%d")

    with get_ro_connection() as conn:
        # Check available dates from run_stats, schedule, and agent_decisions
        dates_set = set()
        try:
            for r in conn.execute("SELECT DISTINCT target_date FROM run_stats ORDER BY target_date DESC LIMIT ?", (num_days,)):
                if r[0]: dates_set.add(r[0])
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute("SELECT DISTINCT target_date FROM schedule ORDER BY target_date DESC LIMIT ?", (num_days,)):
                if r[0]: dates_set.add(r[0])
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute("SELECT DISTINCT target_date FROM agent_decisions ORDER BY target_date DESC LIMIT ?", (num_days,)):
                if r[0]: dates_set.add(r[0])
        except sqlite3.OperationalError:
            pass

        # Also generate contiguous list ending at tomorrow/today if needed
        from datetime import timedelta
        base_now = datetime.now(tz)
        for offset in range(-6, 2):
            dates_set.add((base_now + timedelta(days=offset)).strftime("%Y-%m-%d"))

        sorted_dates = sorted(dates_set, reverse=True)[:num_days]
        sorted_dates.sort()  # Ascending order for the 7-day strip (older -> newer)

        results = []
        is_running = is_pipeline_active()

        for d in sorted_dates:
            day_data = _get_day_stats_internal(conn, d)
            day_data["date"] = d
            if d == today_str and is_running:
                day_data["is_running"] = True
            else:
                day_data["is_running"] = False
            results.append(day_data)

    return results


def _get_day_stats_internal(conn: sqlite3.Connection, target_date: str) -> Dict[str, Any]:
    """Helper to get stats from run_stats or fallback to agent_decisions."""
    # 1. Try latest run_stats row for this date
    try:
        row = conn.execute("""
            SELECT * FROM run_stats
            WHERE target_date = ?
            ORDER BY id DESC LIMIT 1
        """, (target_date,)).fetchone()
        if row:
            d = dict(row)
            for k in ("reject_reasons_json", "source_stats_json"):
                if isinstance(d.get(k), str):
                    try:
                        d[k] = json.loads(d[k])
                    except Exception:
                        d[k] = {}
            d["estimated"] = False
            return d
    except sqlite3.OperationalError:
        pass

    # 2. Fallback to deduplicated agent_decisions
    # agent_decisions is an event log: use latest row per post_id and target_date
    try:
        cur = conn.execute("""
            WITH latest_decisions AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY post_id, target_date ORDER BY id DESC) as rn
                FROM agent_decisions
                WHERE target_date = ?
            )
            SELECT status, decision_reason, hybrid_score
            FROM latest_decisions
            WHERE rn = 1
        """, (target_date,))
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        rows = []

    # Get scheduled count
    try:
        sched_row = conn.execute("SELECT COUNT(*) as cnt FROM schedule WHERE target_date = ?", (target_date,)).fetchone()
        selected_count = sched_row["cnt"] if sched_row else 0
    except sqlite3.OperationalError:
        selected_count = 0

    # Get available reserve count
    try:
        res_row = conn.execute("SELECT COUNT(*) as cnt FROM reserve_pool WHERE status = 'available'").fetchone()
        reserve_count = res_row["cnt"] if res_row else 0
    except sqlite3.OperationalError:
        reserve_count = 0

    if not rows:
        return {
            "target_date": target_date,
            "found": 0,
            "dropped_prescreen": 0,
            "dropped_duration": 0,
            "downloaded": 0,
            "dropped_filters": 0,
            "judged": 0,
            "rejected_judge": 0,
            "borderline": 0,
            "approved": selected_count,
            "selected": selected_count,
            "reserve_added": 0,
            "reject_reasons_json": {"not_funny": 0, "non_english": 0, "watermark": 0, "duration": 0, "tv_film": 0, "other": 0},
            "source_stats_json": {},
            "estimated": True,
        }

    accepted_count = sum(1 for r in rows if r["status"] in ("auto_approved", "approved"))
    rejected_count = sum(1 for r in rows if r["status"] in ("rejected", "auto_rejected"))
    borderline_count = sum(1 for r in rows if r["status"] == "borderline")
    found_count = len(rows)

    reasons = {"not_funny": 0, "non_english": 0, "watermark": 0, "duration": 0, "tv_film": 0, "other": 0}
    for r in rows:
        if r["status"] in ("rejected", "auto_rejected"):
            txt = (r["decision_reason"] or "").lower()
            if any(w in txt for w in ("not funny", "score", "threshold", "unfunny")):
                reasons["not_funny"] += 1
            elif any(w in txt for w in ("english", "language", "speech")):
                reasons["non_english"] += 1
            elif any(w in txt for w in ("watermark", "logo", "channel")):
                reasons["watermark"] += 1
            elif any(w in txt for w in ("duration", "seconds", "too long", "too short")):
                reasons["duration"] += 1
            elif any(w in txt for w in ("tv", "film", "movie")):
                reasons["tv_film"] += 1
            else:
                reasons["other"] += 1

    return {
        "target_date": target_date,
        "found": found_count,
        "dropped_prescreen": 0,
        "dropped_duration": reasons["duration"],
        "downloaded": found_count,
        "dropped_filters": reasons["non_english"] + reasons["duration"],
        "judged": found_count,
        "rejected_judge": rejected_count,
        "borderline": borderline_count,
        "approved": accepted_count,
        "selected": selected_count or accepted_count,
        "reserve_added": max(0, accepted_count - selected_count),
        "reject_reasons_json": reasons,
        "source_stats_json": {},
        "estimated": True,
    }


def get_agent_day_detail(target_date: str) -> Dict[str, Any]:
    """
    Detailed data for Agent view: funnel ribbon, totals, tomorrow lineup,
    reserve pool rows, rejection reasons, and source yield.
    """
    with get_ro_connection() as conn:
        stats = _get_day_stats_internal(conn, target_date)

        # Lineup: schedule joined with agent_decisions (latest per post_id)
        try:
            cur = conn.execute("""
                WITH latest_dec AS (
                    SELECT post_id, hybrid_score, ROW_NUMBER() OVER (PARTITION BY post_id, target_date ORDER BY id DESC) as rn
                    FROM agent_decisions
                    WHERE target_date = ?
                )
                SELECT s.slot_time, s.slot_hour, s.post_id, s.title, s.uploader, s.source_url, s.video_path,
                       COALESCE(d.hybrid_score, 8.0) as hybrid_score
                FROM schedule s
                LEFT JOIN latest_dec d ON s.post_id = d.post_id AND d.rn = 1
                WHERE s.target_date = ?
                ORDER BY s.slot_time ASC
            """, (target_date, target_date))
            lineup_rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            try:
                cur = conn.execute("""
                    SELECT slot_time, slot_hour, post_id, title, uploader, source_url, video_path, 8.0 as hybrid_score
                    FROM schedule
                    WHERE target_date = ?
                    ORDER BY slot_time ASC
                """, (target_date,))
                lineup_rows = [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                lineup_rows = []

        # Available reserve clips
        try:
            cur = conn.execute("""
                SELECT post_id, title, uploader, score, duration, status
                FROM reserve_pool
                WHERE status = 'available'
                ORDER BY score DESC
                LIMIT 12
            """)
            reserve_rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            reserve_rows = []

        # Sources summary
        source_stats = stats.get("source_stats_json") or {}
        # If empty in run_stats, query sources table
        if not source_stats:
            try:
                cur = conn.execute("SELECT name, downloaded_count, approved_count FROM sources WHERE status != 'exhausted' ORDER BY yield_rate DESC LIMIT 6")
                for r in cur.fetchall():
                    source_stats[f"r/{r['name']}"] = {
                        "judged": r["downloaded_count"],
                        "accepted": r["approved_count"],
                    }
            except sqlite3.OperationalError:
                pass

        # Funnel counts for ribbon
        # Proportions: dropped_before_judging, rejected_by_judge, stored_in_reserve, selected_for_tomorrow
        dropped_before = stats.get("dropped_prescreen", 0) + stats.get("dropped_filters", 0)
        rejected_judge = stats.get("rejected_judge", 0)
        stored_reserve = len(reserve_rows) if reserve_rows else stats.get("reserve_added", 0)
        selected_tomorrow = len(lineup_rows) if lineup_rows else stats.get("selected", 0)

        # Fallback if totals are 0
        total_found = stats.get("found", 0)
        total_accepted = stats.get("approved", 0)
        total_rejected = dropped_before + rejected_judge

        funnel = {
            "dropped_before_judging": dropped_before,
            "rejected_by_judge": rejected_judge,
            "stored_in_reserve": stored_reserve,
            "selected_for_tomorrow": selected_tomorrow,
        }

        # Add thumbnail URLs to lineup rows
        for item in lineup_rows:
            item["thumb_url"] = f"/api/thumbs/{item['post_id']}.jpg"

        return {
            "target_date": target_date,
            "estimated": stats.get("estimated", False),
            "totals": {
                "found": total_found,
                "rejected": total_rejected,
                "accepted": total_accepted,
            },
            "funnel": funnel,
            "lineup": lineup_rows,
            "reserve": reserve_rows,
            "reasons": stats.get("reject_reasons_json", {}),
            "sources": source_stats,
        }
