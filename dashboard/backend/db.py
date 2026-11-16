import os
import json
import shutil
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


def get_rw_connection(timeout: float = 30.0) -> sqlite3.Connection:
    """Return a controlled read-write SQLite connection with row factory."""
    db_file = DB_PATH.resolve()
    conn = sqlite3.connect(str(db_file), timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


def update_local_retimed_data(
    target_date: str,
    retimed_items: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """
    Controlled write of retimed clip times to SQLite (schedule and uploads tables)
    and schedule.json files in an explicit transaction BEFORE YouTube updates.
    """
    if not retimed_items:
        return {"db_updated": 0, "json_updated": 0}

    actual_db_path = Path(db_path or DB_PATH).resolve()
    actual_data_dir = Path(data_dir or (WORKSPACE_DIR / "agent" / "data")).resolve()
    db_updated = 0

    if actual_db_path.is_file():
        conn = sqlite3.connect(str(actual_db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                # 1. Defensively clean up any orphaned schedule rows for target_date that do NOT belong to scheduled clips
                conn.execute("""
                    DELETE FROM schedule
                    WHERE target_date = ?
                      AND (post_id IS NULL OR post_id NOT IN (
                          SELECT post_id FROM uploads WHERE target_date = ?
                      ))
                """, (target_date, target_date))

                # Deduplicate if duplicate rows exist for the same post_id in schedule
                conn.execute("""
                    DELETE FROM schedule
                    WHERE target_date = ? AND id NOT IN (
                        SELECT MAX(id) FROM schedule WHERE target_date = ? GROUP BY post_id
                    )
                """, (target_date, target_date))

                # 2. Temporarily rename slot_time for affected rows to avoid UNIQUE(target_date, slot_time) collisions
                for item in retimed_items:
                    conn.execute(
                        "UPDATE schedule SET slot_time = ? WHERE target_date = ? AND post_id = ?",
                        (f"__retime_tmp__{item['post_id']}", target_date, item["post_id"]),
                    )

                # 3. Apply final slot_time and slot_hour to schedule table
                for item in retimed_items:
                    cur = conn.execute(
                        "UPDATE schedule SET slot_time = ?, slot_hour = ? WHERE target_date = ? AND post_id = ?",
                        (item["slot_time"], item["slot_hour"], target_date, item["post_id"]),
                    )
                    db_updated += cur.rowcount

                # 4. Update uploads table (publish_at and slot_time)
                for item in retimed_items:
                    conn.execute(
                        "UPDATE uploads SET publish_at = ?, slot_time = ? WHERE target_date = ? AND post_id = ?",
                        (item["publish_at"], item["slot_time"], target_date, item["post_id"]),
                    )
        finally:
            conn.close()

    # 5. Update schedule.json files
    json_updated = 0
    candidate_paths = [
        actual_data_dir / "edited" / target_date / "schedule.json",
        actual_data_dir / "published" / target_date / "schedule.json",
        actual_data_dir / target_date / "schedule.json",
        actual_data_dir / "schedule.json",
    ]

    retimed_by_post = {item["post_id"]: item for item in retimed_items}

    for json_path in candidate_paths:
        if not json_path.is_file():
            continue

        try:
            # Create a backup copy (.bak) if not already existing
            bak_file = json_path.with_suffix(".json.bak")
            if not bak_file.exists():
                try:
                    shutil.copy2(json_path, bak_file)
                except Exception:
                    pass

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            slots = data.get("slots", [])
            modified = False
            for s in slots:
                pid = s.get("post_id")
                if pid in retimed_by_post:
                    info = retimed_by_post[pid]
                    s["slot_time"] = info["slot_time"]
                    s["slot_hour"] = info["slot_hour"]
                    if "publish_at" in s:
                        s["publish_at"] = info["publish_at"]
                    if "publishAt" in s:
                        s["publishAt"] = info["publish_at"]
                    modified = True

            if modified:
                slots.sort(key=lambda x: x.get("slot_time", ""))
                data["slots"] = slots

                temp_file = json_path.with_name(f"{json_path.name}.tmp.{os.getpid()}")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_file, json_path)
                json_updated += 1
        except Exception:
            pass

    return {"db_updated": db_updated, "json_updated": json_updated}


def rollback_local_retimed_data(
    target_date: str,
    retimed_items: List[Dict[str, Any]],
    db_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> None:
    """
    Roll back local SQLite changes and restore schedule.json from .bak backups
    if YouTube retiming fails.
    """
    if not retimed_items:
        return

    actual_db_path = Path(db_path or DB_PATH).resolve()
    actual_data_dir = Path(data_dir or (WORKSPACE_DIR / "agent" / "data")).resolve()

    if actual_db_path.is_file():
        conn = sqlite3.connect(str(actual_db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                # 1. Temporarily rename to avoid collisions during rollback
                for item in retimed_items:
                    if item.get("old_slot_time"):
                        conn.execute(
                            "UPDATE schedule SET slot_time = ? WHERE target_date = ? AND post_id = ?",
                            (f"__rollback_tmp__{item['post_id']}", target_date, item["post_id"]),
                        )

                # 2. Restore original slot_time and slot_hour to schedule
                for item in retimed_items:
                    if item.get("old_slot_time"):
                        conn.execute(
                            "UPDATE schedule SET slot_time = ?, slot_hour = ? WHERE target_date = ? AND post_id = ?",
                            (item["old_slot_time"], item["old_slot_hour"], target_date, item["post_id"]),
                        )

                # 3. Restore original publish_at and slot_time to uploads
                for item in retimed_items:
                    if item.get("old_publish_at") or item.get("old_slot_time"):
                        conn.execute(
                            "UPDATE uploads SET publish_at = ?, slot_time = ? WHERE target_date = ? AND post_id = ?",
                            (item.get("old_publish_at"), item.get("old_slot_time"), target_date, item["post_id"]),
                        )
        except Exception:
            pass
        finally:
            conn.close()

    # 4. Restore schedule.json from .bak backups if available
    candidate_paths = [
        actual_data_dir / "edited" / target_date / "schedule.json",
        actual_data_dir / "published" / target_date / "schedule.json",
        actual_data_dir / target_date / "schedule.json",
        actual_data_dir / "schedule.json",
    ]
    for json_path in candidate_paths:
        bak_file = json_path.with_suffix(".json.bak")
        if bak_file.is_file():
            try:
                shutil.copy2(bak_file, json_path)
            except Exception:
                pass


def get_next_run_time_from_scheduler() -> Optional[str]:
    """
    Read the next scheduled run time from Windows Task Scheduler for '\100B Daily Video Bot',
    converting from system local time to America/New_York ISO format.
    """
    if os.name != "nt":
        return None
    try:
        import subprocess
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", r"\100B Daily Video Bot", "/fo", "LIST"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.strip().startswith("Next Run Time:"):
                    val = line.split(":", 1)[1].strip()
                    # e.g., '17-11-2026 18:00:00'
                    try:
                        dt_local = datetime.strptime(val, "%d-%m-%Y %H:%M:%S").replace(
                            tzinfo=datetime.now().astimezone().tzinfo
                        )
                        dt_ny = dt_local.astimezone(ZoneInfo("America/New_York"))
                        return dt_ny.isoformat()
                    except Exception:
                        pass
    except Exception:
        pass
    return None


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
    scheduler_next_run = get_next_run_time_from_scheduler()
    if scheduler_next_run:
        next_run_iso = scheduler_next_run
    else:
        # Fallback: daily run is at 18:00 IST = 07:30 AM New York time
        from datetime import timedelta
        tz = ZoneInfo("America/New_York")
        now_ny = datetime.now(tz)
        next_run_dt = now_ny.replace(hour=7, minute=30, second=0, microsecond=0)
        if now_ny >= next_run_dt:
            next_run_dt += timedelta(days=1)
        next_run_iso = next_run_dt.isoformat()

    return {
        "last_run": last_run,
        "next_run_time": next_run_iso,
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
    Ends strictly at today (channel timezone America/New_York) so future test dates
    are not shown in the picker. Starts with today selected.
    """
    from datetime import timedelta
    tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(tz)
    today_str = now_ny.strftime("%Y-%m-%d")

    with get_ro_connection() as conn:
        dates_set = set()
        # Query distinct dates from DB, but only up to today (ignore future test dates)
        try:
            for r in conn.execute(
                "SELECT DISTINCT target_date FROM run_stats WHERE target_date <= ? ORDER BY target_date DESC LIMIT ?",
                (today_str, num_days)
            ):
                if r[0]: dates_set.add(r[0])
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute(
                "SELECT DISTINCT target_date FROM schedule WHERE target_date <= ? ORDER BY target_date DESC LIMIT ?",
                (today_str, num_days)
            ):
                if r[0]: dates_set.add(r[0])
        except sqlite3.OperationalError:
            pass

        try:
            for r in conn.execute(
                "SELECT DISTINCT target_date FROM agent_decisions WHERE target_date <= ? ORDER BY target_date DESC LIMIT ?",
                (today_str, num_days)
            ):
                if r[0]: dates_set.add(r[0])
        except sqlite3.OperationalError:
            pass

        # Ensure contiguous num_days strip ending at today
        for offset in range(-(num_days - 1), 1):
            dates_set.add((now_ny + timedelta(days=offset)).strftime("%Y-%m-%d"))

        # Take the last num_days ending at today
        valid_dates = [d for d in dates_set if d <= today_str]
        sorted_dates = sorted(valid_dates, reverse=True)[:num_days]
        sorted_dates.sort()  # Ascending order for the 7-day strip (older -> newer, ending at today)

        results = []
        is_running = is_pipeline_active()

        for d in sorted_dates:
            day_data = _get_day_stats_internal(conn, d)
            day_data["date"] = d
            day_data["is_today"] = (d == today_str)
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
    Detailed data for Agent view: funnel ribbon, totals, lineup (for next day per design),
    reserve pool rows, rejection reasons, and source yield.
    Under a selected day, shows the next day's curated clips (tomorrow's lineup).
    """
    from datetime import timedelta
    try:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        next_date = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        next_date = target_date

    with get_ro_connection() as conn:
        stats = _get_day_stats_internal(conn, target_date)

        # Lineup: Under a selected day, show the next day's curated clips (tomorrow's lineup)
        lineup_rows = []
        try:
            cur = conn.execute("""
                WITH latest_dec AS (
                    SELECT post_id, hybrid_score, ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY id DESC) as rn
                    FROM agent_decisions
                )
                SELECT s.slot_time, s.slot_hour, s.post_id, s.title, s.uploader, s.source_url, s.video_path,
                       COALESCE(d.hybrid_score, r.score) as hybrid_score
                FROM schedule s
                LEFT JOIN latest_dec d ON s.post_id = d.post_id AND d.rn = 1
                LEFT JOIN reserve_pool r ON s.post_id = r.post_id
                WHERE s.target_date = ?
                ORDER BY s.slot_time ASC
            """, (next_date,))
            lineup_rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            pass

        has_next_date_lineup = bool(lineup_rows)

        # Fallback if no rows found for next_date: check target_date itself
        if not lineup_rows:
            try:
                cur = conn.execute("""
                    WITH latest_dec AS (
                        SELECT post_id, hybrid_score, ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY id DESC) as rn
                        FROM agent_decisions
                    )
                    SELECT s.slot_time, s.slot_hour, s.post_id, s.title, s.uploader, s.source_url, s.video_path,
                           COALESCE(d.hybrid_score, r.score) as hybrid_score
                    FROM schedule s
                    LEFT JOIN latest_dec d ON s.post_id = d.post_id AND d.rn = 1
                    LEFT JOIN reserve_pool r ON s.post_id = r.post_id
                    WHERE s.target_date = ?
                    ORDER BY s.slot_time ASC
                """, (target_date,))
                lineup_rows = [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                lineup_rows = []

        lineup_target_date = next_date if has_next_date_lineup else target_date

        # Format time_display in America/New_York (ET)
        for idx, r in enumerate(lineup_rows):
            slot_iso = r.get("slot_time") or ""
            time_disp = "12 PM ET"
            try:
                dt_iso = datetime.fromisoformat(slot_iso).astimezone(ZoneInfo("America/New_York"))
                if dt_iso.minute == 0:
                    time_disp = f"{dt_iso.strftime('%I').lstrip('0')} {dt_iso.strftime('%p')} ET"
                else:
                    time_disp = f"{dt_iso.strftime('%I:%M %p').lstrip('0')} ET"
            except Exception:
                h = r.get("slot_hour", 12 + idx)
                period = "PM" if h >= 12 else "AM"
                h12 = h % 12 or 12
                time_disp = f"{h12} {period} ET"
            r["time_display"] = time_disp
            r["thumb_url"] = f"/api/thumbs/{r['post_id']}.jpg"

        now_ny = datetime.now(ZoneInfo("America/New_York"))
        today_str = now_ny.strftime("%Y-%m-%d")
        is_today = (target_date == today_str)

        # Reserve clips: today shows currently available pool; past days show clips added on that day
        if is_today:
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
            stored_reserve = stats.get("reserve_added") if stats.get("reserve_added") is not None else len(reserve_rows)
        else:
            try:
                cur = conn.execute("""
                    SELECT post_id, title, uploader, score, duration, status
                    FROM reserve_pool
                    WHERE DATE(added_at) = ? OR added_at LIKE ? || '%'
                    ORDER BY score DESC
                    LIMIT 12
                """, (target_date, target_date))
                reserve_rows = [dict(r) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                reserve_rows = []
            stored_reserve = stats.get("reserve_added", len(reserve_rows))

        # Sources summary: for past days, use that day's stats only (never today's running total)
        source_stats = stats.get("source_stats_json") or {}
        if not source_stats:
            derived_sources: Dict[str, Dict[str, int]] = {}
            try:
                cur = conn.execute("SELECT source_url FROM schedule WHERE target_date = ?", (target_date,))
                for (s_url,) in cur.fetchall():
                    if s_url and "/r/" in s_url:
                        parts = s_url.split("/r/")[1].split("/")
                        if parts and parts[0]:
                            src = f"r/{parts[0]}"
                            derived_sources.setdefault(src, {"judged": 0, "accepted": 0})
                            derived_sources[src]["accepted"] += 1
                            derived_sources[src]["judged"] += 1

                cur = conn.execute("SELECT post_url FROM reserve_pool WHERE DATE(added_at) = ? OR added_at LIKE ? || '%'", (target_date, target_date))
                for (p_url,) in cur.fetchall():
                    if p_url and "/r/" in p_url:
                        parts = p_url.split("/r/")[1].split("/")
                        if parts and parts[0]:
                            src = f"r/{parts[0]}"
                            derived_sources.setdefault(src, {"judged": 0, "accepted": 0})
                            derived_sources[src]["accepted"] += 1
                            derived_sources[src]["judged"] += 1
            except Exception:
                pass

            if derived_sources:
                source_stats = derived_sources
            elif is_today:
                try:
                    cur = conn.execute("SELECT name, downloaded_count, approved_count FROM sources WHERE status != 'exhausted' ORDER BY yield_rate DESC LIMIT 6")
                    for r in cur.fetchall():
                        source_stats[f"r/{r['name']}"] = {
                            "judged": r["downloaded_count"],
                            "accepted": r["approved_count"],
                        }
                except sqlite3.OperationalError:
                    pass
            else:
                source_stats = {}

        # Funnel counts for ribbon
        # Proportions: dropped_before_judging, rejected_by_judge, stored_in_reserve, selected_for_tomorrow
        dropped_before = stats.get("dropped_prescreen", 0) + stats.get("dropped_filters", 0)
        rejected_judge = stats.get("rejected_judge", 0)
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
            "lineup_target_date": lineup_target_date,
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
