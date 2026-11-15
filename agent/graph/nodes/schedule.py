import json
import re
from pathlib import Path
from datetime import datetime, time
from typing import Dict, Any, List, Set
from zoneinfo import ZoneInfo
from loguru import logger

from graph.state import CurationState
from core.db import AgentDatabase
from graph.nodes.caption import is_generic_title


def interleave_sources(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reorder videos so identical sources/subreddits are not placed back-to-back."""
    if len(videos) <= 1:
        return videos

    # Group by source key (subreddit or twitter account)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for v in videos:
        key = v.get("source", "generic")
        if v.get("extra") and "subreddit" in v["extra"]:
            key = f"r/{v['extra']['subreddit']}"
        grouped.setdefault(key, []).append(v)

    # Sort groups by count descending
    sorted_groups = sorted(grouped.values(), key=len, reverse=True)
    result = []
    while any(sorted_groups):
        for g in sorted_groups:
            if g:
                result.append(g.pop(0))

    return result


def select_target_hours(target_count: int, peak_hours: List[int]) -> List[int]:
    """
    Select target_count hours across the day.
    Prioritizes peak viewing hours, then fills with daytime waking hours.
    """
    if target_count >= 24:
        return list(range(24))

    # Preferred daytime non-peak hours before late night
    daytime_non_peak = [h for h in [17, 16, 15, 11, 10, 9, 22, 23, 8, 7] if h not in peak_hours]
    late_night = [h for h in range(24) if h not in peak_hours and h not in daytime_non_peak]

    if target_count <= len(peak_hours):
        # Pick target_count evenly spaced peak hours
        step = max(1, len(peak_hours) // target_count)
        selected = peak_hours[::step][:target_count]
        for p in peak_hours:
            if len(selected) >= target_count:
                break
            if p not in selected:
                selected.append(p)
    else:
        selected = list(peak_hours)
        remaining = target_count - len(selected)
        for h in daytime_non_peak + late_night:
            if remaining <= 0:
                break
            selected.append(h)
            remaining -= 1

    return sorted(selected)


def schedule_node(
    state: CurationState,
    config: Dict[str, Any],
    db: AgentDatabase,
) -> Dict[str, Any]:
    """
    Assigns each edited video to an hourly slot for target_date.
    The slot count follows target_count (up to 24 slots).
    Prioritizes highest scores into peak hours and interleaves sources.
    Saves surplus videos to reserve_pool.
    Outputs to SQLite schedule table and data/edited/<target_date>/schedule.json.
    """
    approved = list(state.get("approved", []))
    target_date = state.get("target_date", "tomorrow")
    target_count = int(state.get("target_count", 6))
    dry_run = state.get("dry_run", False)

    tz_name = config.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    peak_hours = sorted(config.get("peak_hours", [12, 13, 14, 18, 19, 20, 21]))
    target_hours = select_target_hours(target_count, peak_hours)
    target_peak_hours = [h for h in target_hours if h in peak_hours]
    target_non_peak_hours = [h for h in target_hours if h not in peak_hours]

    logger.info(f"--- [Node: SCHEDULE] Building {len(target_hours)}-slot schedule for {target_date} ({tz_name}) ---")

    # Verify that all approved video files actually exist on disk before scheduling
    valid_approved = []
    for vid in approved:
        v_path = vid.get("edited_video_path") or vid.get("video_path")
        is_reserve = vid.get("already_edited") or vid.get("is_reserve_backfill")

        file_exists = False
        if v_path:
            try:
                file_exists = Path(v_path).is_file()
            except Exception:
                file_exists = False

        if not file_exists:
            # In dry-run mode, new edits are simulated and won't exist on disk,
            # but reserve clips must always exist. In non-dry-run mode, all clips must exist.
            if dry_run and not is_reserve:
                valid_approved.append(vid)
                continue

            logger.error(
                f"[Node: SCHEDULE] Video file not found on disk for '{vid.get('post_id')}': {v_path}. "
                "Skipping clip to prevent Phase 4 failure."
            )
            continue

        valid_approved.append(vid)

    approved = valid_approved

    # Sort all approved by score descending
    approved.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    # Surplus handling: save extra approved clips to reserve_pool
    # ARCHITECTURAL RULE: Keep the raw clip and caption in reserve; edit only when scheduled.
    if len(approved) > target_count:
        surplus = approved[target_count:]
        approved = approved[:target_count]
        logger.info(f"Saving {len(surplus)} surplus approved clips (raw + caption) to reserve_pool for future days.")
        for s in surplus:
            raw_v_path = s.get("raw_video_path") or s.get("video_path")
            if not dry_run and (not raw_v_path or not Path(raw_v_path).is_file()):
                logger.warning(f"Skipping surplus clip '{s.get('post_id')}' with missing raw file: {raw_v_path}")
                continue

            # Clean up any surplus edited file so we don't store pre-branded media
            edited_v_path = s.get("edited_video_path")
            if not dry_run and edited_v_path and Path(edited_v_path).is_file():
                try:
                    Path(edited_v_path).unlink()
                    logger.debug(f"Removed surplus branded video: {edited_v_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove surplus branded video {edited_v_path}: {e}")

            # Ensure saved title in reserve pool is specific and not generic
            s_title = s.get("youtube_title") or s.get("title")
            if is_generic_title(s_title):
                s_title = s.get("title") or s.get("caption") or f"Comedy Clip #{s['post_id']}"
            db.add_to_reserve_pool({
                "post_id": s["post_id"],
                "video_path": raw_v_path,
                "caption": s.get("caption"),
                "title": s_title,
                "description": s.get("youtube_description") or s.get("caption"),
                "uploader": s.get("uploader"),
                "post_url": s.get("post_url"),
                "score": s.get("score"),
                "duration": s.get("duration"),
                "resolution": s.get("resolution"),
            })

    # Separate peak candidates vs standard candidates
    num_peaks = min(len(target_peak_hours), len(approved))
    peak_candidates = approved[:num_peaks]
    regular_candidates = approved[num_peaks:]

    # Interleave sources within groups
    peak_ordered = interleave_sources(peak_candidates)
    regular_ordered = interleave_sources(regular_candidates)

    slot_assignments: Dict[int, Any] = {}

    # 1. First assign top videos to peak hours
    for h in target_peak_hours:
        if peak_ordered:
            slot_assignments[h] = (peak_ordered.pop(0), True)
        elif regular_ordered:
            slot_assignments[h] = (regular_ordered.pop(0), True)

    # 2. Next assign remaining videos to non-peak hours
    for h in target_non_peak_hours:
        if regular_ordered:
            slot_assignments[h] = (regular_ordered.pop(0), False)
        elif peak_ordered:
            slot_assignments[h] = (peak_ordered.pop(0), False)

    schedule_slots: List[Dict[str, Any]] = []
    empty_slots: List[int] = []
    seen_titles: Set[str] = set()

    # Map target hours in chronological order
    for hour in target_hours:
        slot_dt = datetime.strptime(target_date, "%Y-%m-%d").replace(
            hour=hour, minute=0, second=0, tzinfo=tz
        )
        slot_time_iso = slot_dt.isoformat()

        if hour in slot_assignments:
            chosen_vid, is_peak = slot_assignments[hour]
            slot_title = chosen_vid.get("youtube_title") or chosen_vid.get("title") or ""

            # Enforce title uniqueness across all slots in this schedule
            if is_generic_title(slot_title) or slot_title.lower().strip() in seen_titles:
                alt = chosen_vid.get("title") or chosen_vid.get("caption") or ""
                clean_alt = re.sub(r"\s*#\w+", "", alt).strip()
                if clean_alt and not is_generic_title(clean_alt) and f"{clean_alt} #shorts #funny".lower() not in seen_titles:
                    slot_title = f"{clean_alt} #shorts #funny"
                else:
                    base = re.sub(r"\s*#\w+", "", slot_title).strip() or "Comedy Clip"
                    if is_generic_title(base):
                        base = f"Hilarious Moment at {hour:02d}:00"
                    slot_title = f"{base} (Slot {hour:02d}:00) #shorts #funny"

            seen_titles.add(slot_title.lower().strip())
            chosen_vid["youtube_title"] = slot_title

            slot_entry = {
                "slot_hour": hour,
                "slot_time": slot_time_iso,
                "post_id": chosen_vid["post_id"],
                "video_path": chosen_vid.get("edited_video_path") or chosen_vid.get("video_path"),
                "title": slot_title,
                "description": chosen_vid.get("youtube_description") or "",
                "caption": chosen_vid.get("caption", ""),
                "source_url": chosen_vid.get("post_url", ""),
                "uploader": chosen_vid.get("uploader", "unknown"),
                "score": chosen_vid.get("score", 0.0),
                "is_peak": is_peak,
                "status": "queued",
            }
            schedule_slots.append(slot_entry)
        else:
            empty_slots.append(hour)

    if empty_slots:
        logger.warning(
            f"Schedule has {len(empty_slots)} unfilled slots: Hours {empty_slots} "
            f"(Total scheduled: {len(schedule_slots)}/{target_count})"
        )
    else:
        logger.success(f"Successfully scheduled all {len(schedule_slots)} slots!")

    # Write to database and schedule.json
    project_root = Path(__file__).parent.parent.parent.parent.resolve()
    edited_date_dir = project_root / "agent" / "data" / "edited" / target_date
    edited_date_dir.mkdir(parents=True, exist_ok=True)
    schedule_json_path = edited_date_dir / "schedule.json"

    if not dry_run:
        # Save to SQLite table
        db.save_schedule_slots(target_date, schedule_slots)

        # Save to schedule.json for Phase 4
        with open(schedule_json_path, "w", encoding="utf-8") as f:
            json.dump({
                "target_date": target_date,
                "timezone": tz_name,
                "created_at": datetime.now(tz).isoformat(),
                "total_slots": len(schedule_slots),
                "empty_slots": empty_slots,
                "slots": schedule_slots,
            }, f, indent=2)
        logger.info(f"Schedule JSON saved to {schedule_json_path}")
    else:
        logger.info(f"[DRY-RUN] Would write {len(schedule_slots)} slots to {schedule_json_path}")

    return {
        "final_schedule": schedule_slots,
    }
