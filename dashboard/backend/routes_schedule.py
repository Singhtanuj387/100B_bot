import os
import re
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, List

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from dashboard.backend.db import get_ro_connection, WORKSPACE_DIR, update_local_retimed_data
import dashboard.backend.db as backend_db
from dashboard.backend.youtube_api import has_manage_scope, update_video_publish_time

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

AGENT_CONFIG_PATH = WORKSPACE_DIR / "agent" / "config.yaml"
UPLOADER_CONFIG_PATH = WORKSPACE_DIR / "uploader" / "config.yaml"
SCHEDULE_DATA_DIR = WORKSPACE_DIR / "agent" / "data"


class ScheduleUpdateRequest(BaseModel):
    videos_per_day: int = Field(..., ge=1, le=12)
    slots: List[str]


def parse_hhmm_to_minutes(time_str: str) -> int:
    """Validate and convert 'HH:MM' string to minutes from midnight."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str.strip())
    if not m:
        raise ValueError(f"Invalid time format '{time_str}'. Expected 'HH:MM' (24-hour).")
    hours, minutes = int(m.group(1)), int(m.group(2))
    if not (0 <= hours <= 23):
        raise ValueError(f"Hour {hours} out of bounds (0-23).")
    if not (0 <= minutes <= 59):
        raise ValueError(f"Minute {minutes} out of bounds (0-59).")
    if minutes % 15 != 0:
        raise ValueError(f"Time '{time_str}' does not snap to 15-minute increments (must be :00, :15, :30, or :45).")
    return hours * 60 + minutes


def minutes_to_hhmm(total_minutes: int) -> str:
    hours = (total_minutes // 60) % 24
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def read_yaml_preserving_comments(path: Path) -> tuple[YAML, Any]:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f)
    return yaml, data


def write_yaml_atomically(path: Path, yaml: YAML, data: Any):
    # Save .bak copy first
    bak_path = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak_path)

    temp_file = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
        os.replace(temp_file, path)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


@router.get("")
def get_schedule():
    """
    GET /api/schedule:
    Returns videos_per_day, slots (HH:MM sorted), timezone,
    and for tomorrow each slot's assigned clip and status.
    """
    # 1. Read agent config
    yaml, agent_cfg = read_yaml_preserving_comments(AGENT_CONFIG_PATH)
    tz_name = agent_cfg.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)

    raw_peaks = agent_cfg.get("peak_hours", [12, 14, 17, 19, 20, 21])
    parsed_slots = []
    for p in raw_peaks:
        try:
            if isinstance(p, int):
                parsed_slots.append(f"{p:02d}:00")
            elif isinstance(p, str):
                if ":" in p:
                    parts = p.split(":")
                    parsed_slots.append(f"{int(parts[0]):02d}:{int(parts[1]):02d}")
                else:
                    parsed_slots.append(f"{int(p):02d}:00")
        except Exception:
            pass

    # Sort slots chronologically
    sorted_slots = sorted(set(parsed_slots), key=lambda x: parse_hhmm_to_minutes(x))

    target_count = int(agent_cfg.get("target_count", len(sorted_slots) or 6))

    # Read tomorrow's assigned clips from schedule joined with uploads
    now_ny = datetime.now(tz)
    tomorrow_dt = now_ny + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")

    tomorrow_slots = []
    with get_ro_connection() as conn:
        try:
            cur = conn.execute("""
                SELECT s.slot_time, s.slot_hour, s.post_id, s.title, s.uploader,
                       COALESCE(u.status, s.status, 'queued') as status,
                       u.video_id, u.publish_at
                FROM schedule s
                LEFT JOIN uploads u ON s.post_id = u.post_id AND s.target_date = u.target_date
                WHERE s.target_date = ?
                ORDER BY s.slot_time ASC
            """, (tomorrow_str,))
            for r in cur.fetchall():
                d = dict(r)
                # Parse slot time string to HH:MM ET
                slot_iso = d.get("slot_time") or ""
                time_disp = "12 PM ET"
                try:
                    dt = datetime.fromisoformat(slot_iso).astimezone(ZoneInfo("America/New_York"))
                    if dt.minute == 0:
                        time_disp = f"{dt.strftime('%I').lstrip('0')} {dt.strftime('%p')} ET"
                    else:
                        time_disp = f"{dt.strftime('%I:%M %p').lstrip('0')} ET"
                except Exception:
                    h = d.get("slot_hour", 12)
                    period = "PM" if h >= 12 else "AM"
                    h12 = h % 12 or 12
                    time_disp = f"{h12} {period} ET"
                d["time_display"] = time_disp
                d["thumb_url"] = f"/api/thumbs/{d['post_id']}.jpg"
                tomorrow_slots.append(d)
        except Exception:
            pass

    return {
        "videos_per_day": target_count,
        "slots": sorted_slots,
        "timezone": tz_name,
        "tomorrow_date": tomorrow_str,
        "tomorrow_day_name": tomorrow_dt.strftime("%A"),
        "tomorrow_slots": tomorrow_slots,
        "can_retime_live": has_manage_scope(),
    }


@router.put("")
async def update_schedule(request: Request, body: ScheduleUpdateRequest):
    """
    PUT /api/schedule:
    Validate 1 to 12 slots, unique times, 15-minute steps, slot count == videos_per_day.
    Atomically updates agent/config.yaml and uploader/config.yaml with ruamel.yaml (.bak backup).
    Retimes scheduled YouTube clips if manage scope is available.
    """
    # Security Rule: Require Custom Header, JSON content-type, and Host check
    host = request.headers.get("host", "").lower()
    if not (host.startswith("127.0.0.1") or host.startswith("localhost") or host.startswith("testserver")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Host check failed. Requests must originate from localhost/127.0.0.1.",
        )

    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported Media Type: Content-Type must be application/json.",
        )

    custom_header = request.headers.get("x-dashboard-request") or request.headers.get("x-requested-with")
    if not custom_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing security header: X-Dashboard-Request or X-Requested-With required.",
        )

    # Validation: 1 to 12 slots
    videos_per_day = body.videos_per_day
    if not (1 <= videos_per_day <= 12):
        raise HTTPException(status_code=400, detail="videos_per_day must be between 1 and 12.")

    slots_raw = body.slots
    if len(slots_raw) != videos_per_day:
        raise HTTPException(
            status_code=400,
            detail=f"Slot count ({len(slots_raw)}) does not match videos_per_day ({videos_per_day}).",
        )

    # Validate each slot format and 15-min increment
    minutes_list = []
    seen_slots = set()
    for s in slots_raw:
        try:
            mins = parse_hhmm_to_minutes(s)
            formatted = minutes_to_hhmm(mins)
            if formatted in seen_slots:
                raise HTTPException(status_code=400, detail=f"Duplicate slot time: {formatted}.")
            seen_slots.add(formatted)
            minutes_list.append(mins)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))

    # Sort slots chronologically
    minutes_list.sort()
    sorted_slots = [minutes_to_hhmm(m) for m in minutes_list]

    # Save to agent/config.yaml using ruamel.yaml
    agent_yaml, agent_cfg = read_yaml_preserving_comments(AGENT_CONFIG_PATH)
    agent_cfg["target_count"] = videos_per_day
    agent_cfg["peak_hours"] = sorted_slots
    write_yaml_atomically(AGENT_CONFIG_PATH, agent_yaml, agent_cfg)

    # Save to uploader/config.yaml: keep max_uploads_per_day equal to videos_per_day
    uploader_yaml, uploader_cfg = read_yaml_preserving_comments(UPLOADER_CONFIG_PATH)
    if "upload" not in uploader_cfg:
        uploader_cfg["upload"] = {}
    uploader_cfg["upload"]["max_uploads_per_day"] = videos_per_day
    write_yaml_atomically(UPLOADER_CONFIG_PATH, uploader_yaml, uploader_cfg)

    # Check for tomorrow's scheduled clips and handle retiming
    tz_name = agent_cfg.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    now_ny = datetime.now(tz)
    tomorrow_dt = now_ny + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
    tomorrow_day_name = tomorrow_dt.strftime("%A")

    can_retime = has_manage_scope()
    retimed_count = 0
    retimed_items = []

    if can_retime:
        # Check if tomorrow has scheduled uploads in database
        with get_ro_connection() as conn:
            try:
                cur = conn.execute("""
                    SELECT u.video_id, u.post_id, u.publish_at, COALESCE(s.slot_time, u.slot_time) as slot_time
                    FROM uploads u
                    LEFT JOIN schedule s ON u.post_id = s.post_id AND u.target_date = s.target_date
                    WHERE u.target_date = ? AND u.status = 'scheduled' AND u.video_id IS NOT NULL
                    ORDER BY COALESCE(s.slot_time, u.slot_time) ASC
                """, (tomorrow_str,))
                scheduled_clips = [dict(r) for r in cur.fetchall()]
            except Exception:
                scheduled_clips = []

        if scheduled_clips:
            # Pair available scheduled clips with new sorted slots
            for idx, clip in enumerate(scheduled_clips):
                if idx < len(sorted_slots):
                    vid_id = clip["video_id"]
                    post_id = clip["post_id"]
                    slot_hhmm = sorted_slots[idx]
                    h, m = int(slot_hhmm.split(":")[0]), int(slot_hhmm.split(":")[1])
                    new_dt = tomorrow_dt.replace(hour=h, minute=m, second=0, microsecond=0)
                    # If new time is still in future
                    if new_dt > now_ny:
                        # RFC 3339 format for YouTube API in UTC
                        new_dt_utc = new_dt.astimezone(timezone.utc)
                        new_publish_at_rfc3339 = new_dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                        new_slot_time_iso = new_dt.isoformat()

                        success = update_video_publish_time(vid_id, new_publish_at_rfc3339)
                        if success:
                            retimed_count += 1
                            retimed_items.append({
                                "post_id": post_id,
                                "video_id": vid_id,
                                "slot_hour": h,
                                "slot_time": new_slot_time_iso,
                                "publish_at": new_publish_at_rfc3339,
                            })

            # Controlled write to local DB and schedule.json after successful videos.update
            if retimed_items:
                update_local_retimed_data(
                    target_date=tomorrow_str,
                    retimed_items=retimed_items,
                    db_path=backend_db.DB_PATH,
                    data_dir=SCHEDULE_DATA_DIR,
                )

            notice = f"{tomorrow_day_name}’s {len(scheduled_clips)} clips are already scheduled on YouTube. Saving moves them to the new times."
        else:
            notice = f"Schedule saved! {videos_per_day} slots will take effect on the next daily run."
    else:
        notice = "New times saved for future runs only. To retime existing scheduled clips on YouTube, run: python upload.py auth --manage"

    return {
        "status": "success",
        "videos_per_day": videos_per_day,
        "slots": sorted_slots,
        "retimed_count": retimed_count,
        "can_retime": can_retime,
        "notice": notice,
    }
