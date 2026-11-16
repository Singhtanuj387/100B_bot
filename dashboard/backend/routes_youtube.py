import os
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Request, HTTPException, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from dashboard.backend.db import get_ro_connection, WORKSPACE_DIR
from dashboard.backend.thumbs import resolve_video_path_for_post, get_or_generate_thumbnail
from dashboard.backend.youtube_api import fetch_live_video_stats

router = APIRouter(prefix="/api", tags=["youtube"])


def resolve_date(date_param: str, tz_name: str = "America/New_York") -> str:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    s = (date_param or "today").strip().lower()
    if s in ("today", "now"):
        return now.strftime("%Y-%m-%d")
    elif s in ("yesterday", "prev", "prev_day"):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    elif s in ("tomorrow", "next_day"):
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return now.strftime("%Y-%m-%d")


def determine_clip_status(row: Dict[str, Any], live_info: Dict[str, Any], now_utc: datetime) -> tuple[str, str]:
    """
    Status rules:
      - Published (public, publish time passed)
      - On air (published within the last 60 minutes)
      - Scheduled (private with a future publishAt)
      - Draft (private, no publishAt)
      - Failed (status='failed' or error)
    Returns: (status_label, status_detail_text)
    """
    db_status = (row.get("status") or "").lower()
    last_error = row.get("last_error")

    if db_status in ("failed", "error") or last_error:
        return "Failed", f"Upload failed: {last_error or 'Unknown error'}"

    # Live status from YouTube API
    yt_status = live_info.get("status", {})
    yt_privacy = yt_status.get("privacyStatus")
    yt_publish_at = yt_status.get("publishAt") or row.get("publish_at")
    yt_stats = live_info.get("statistics", {})

    views_str = f"{int(yt_stats['viewCount']):,}" if "viewCount" in yt_stats else "—"
    likes_str = f"{int(yt_stats['likeCount']):,}" if "likeCount" in yt_stats else "—"

    # If public on YouTube
    if yt_privacy == "public" or db_status in ("published", "uploaded") and not yt_publish_at:
        # Check if published within last 60 minutes
        uploaded_at_str = row.get("uploaded_at")
        is_on_air = False
        time_text = "recently"
        if uploaded_at_str:
            try:
                up_dt = datetime.fromisoformat(uploaded_at_str)
                if up_dt.tzinfo is None:
                    up_dt = up_dt.replace(tzinfo=timezone.utc)
                diff_minutes = (now_utc - up_dt).total_seconds() / 60.0
                if 0 <= diff_minutes <= 60:
                    is_on_air = True
                time_text = up_dt.astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p").lstrip("0")
            except Exception:
                pass

        if is_on_air:
            return "On air", f"On air since {time_text} · {views_str} views"
        return "Published", f"Published {time_text} · {views_str} views · {likes_str} likes"

    # If scheduled (private with future publishAt)
    if yt_publish_at:
        try:
            pub_dt = datetime.fromisoformat(yt_publish_at.replace("Z", "+00:00"))
            if pub_dt > now_utc:
                pub_ny = pub_dt.astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p").lstrip("0")
                return "Scheduled", f"Goes live at {pub_ny}"
            else:
                return "Published", f"Published · {views_str} views · {likes_str} likes"
        except Exception:
            return "Scheduled", "Scheduled on YouTube"

    if yt_privacy == "private" or db_status in ("draft", "queued"):
        return "Draft", "Private draft"

    return "Draft", "Queued"


@router.get("/youtube/day/{date}")
def get_youtube_day(date: str):
    """
    GET /api/youtube/day/{date} (today, yesterday, or YYYY-MM-DD):
    Rundown of videos published, on air, or scheduled for date, with live stats.
    """
    tz_name = "America/New_York"
    tz = ZoneInfo(tz_name)
    target_date = resolve_date(date, tz_name)
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    now_utc = datetime.now(timezone.utc)

    rows = []
    with get_ro_connection() as conn:
        try:
            cur = conn.execute("""
                SELECT s.slot_time, s.slot_hour, s.post_id, s.title, s.uploader, s.source_url, s.video_path,
                       COALESCE(u.status, s.status, 'queued') as status,
                       u.video_id, u.uploaded_at, u.publish_at, u.privacy, u.last_error
                FROM schedule s
                LEFT JOIN uploads u ON s.post_id = u.post_id AND s.target_date = u.target_date
                WHERE s.target_date = ?
                ORDER BY s.slot_time ASC
            """, (target_date,))
            rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            pass

        # If schedule had no rows for target_date, check uploads table alone
        if not rows:
            try:
                cur = conn.execute("""
                    SELECT u.slot_time, u.post_id, u.status, u.video_id, u.uploaded_at,
                           u.publish_at, u.privacy, u.last_error,
                           r.title, r.uploader
                    FROM uploads u
                    LEFT JOIN reserve_pool r ON u.post_id = r.post_id
                    WHERE u.target_date = ?
                    ORDER BY u.slot_time ASC
                """, (target_date,))
                rows = [dict(r) for r in cur.fetchall()]
            except Exception:
                pass

    video_ids = [r["video_id"] for r in rows if r.get("video_id")]
    live_stats = fetch_live_video_stats(video_ids)

    # Process items
    published_count = 0
    on_air_count = 0
    scheduled_count = 0
    total_views = 0

    items = []
    for r in rows:
        vid_id = r.get("video_id")
        live = live_stats.get(vid_id, {})
        status_label, status_detail = determine_clip_status(r, live, now_utc)

        if status_label == "Published":
            published_count += 1
        elif status_label == "On air":
            on_air_count += 1
        elif status_label == "Scheduled":
            scheduled_count += 1

        stats_obj = live.get("statistics", {})
        if "viewCount" in stats_obj:
            try:
                total_views += int(stats_obj["viewCount"])
            except Exception:
                pass

        slot_time_iso = r.get("slot_time") or ""
        time_disp = "12:00 PM"
        try:
            dt = datetime.fromisoformat(slot_time_iso)
            time_disp = dt.strftime("%I:%M %p").lstrip("0")
        except Exception:
            pass

        items.append({
            "post_id": r["post_id"],
            "video_id": vid_id,
            "title": r.get("title") or "Comedy Clip",
            "uploader": r.get("uploader") or "unknown",
            "slot_time": slot_time_iso,
            "time_display": time_disp,
            "status": status_label,
            "status_detail": status_detail,
            "views": stats_obj.get("viewCount"),
            "likes": stats_obj.get("likeCount"),
            "youtube_url": f"https://www.youtube.com/shorts/{vid_id}" if vid_id else None,
            "studio_url": f"https://studio.youtube.com/video/{vid_id}/edit" if vid_id else None,
            "thumb_url": f"/api/thumbs/{r['post_id']}.jpg",
            "stream_url": f"/api/video/{r['post_id']}/stream",
            "has_local_file": resolve_video_path_for_post(r["post_id"]) is not None,
        })

    day_header = target_dt.strftime("%A %d %B")
    return {
        "date": target_date,
        "day_header": day_header,
        "published_count": published_count,
        "on_air_count": on_air_count,
        "scheduled_count": scheduled_count,
        "total_views": total_views,
        "clips": items,
    }


@router.get("/thumbs/{post_id}.jpg")
def get_thumbnail(post_id: str):
    """Serve cached 1-second thumbnail or generate it via ffmpeg."""
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", post_id)
    thumb_path = get_or_generate_thumbnail(clean_id)
    if thumb_path and thumb_path.is_file():
        return FileResponse(thumb_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(status_code=404, detail="Thumbnail not available")


@router.get("/video/{post_id}/stream")
def stream_video(post_id: str, request: Request):
    """
    HTTP Range streaming of local video file for post_id.
    Never accepts arbitrary path from client; validates post_id against database.
    """
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", post_id)
    video_path = resolve_video_path_for_post(clean_id)

    if not video_path or not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video file not found for this post_id.")

    file_size = video_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        # Parse range header: e.g. "bytes=0-1024" or "bytes=1024-"
        range_match = re.match(r"^bytes=(\d+)-(\d*)$", range_header.strip())
        if range_match:
            start = int(range_match.group(1))
            end_str = range_match.group(2)
            end = int(end_str) if end_str else file_size - 1

            if start >= file_size or end >= file_size or start > end:
                return Response(
                    status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )

            chunk_length = (end - start) + 1

            def range_generator(path: Path, start_offset: int, length: int):
                with open(path, "rb") as f:
                    f.seek(start_offset)
                    remaining = length
                    chunk_size = 1024 * 64
                    while remaining > 0:
                        read_bytes = min(remaining, chunk_size)
                        data = f.read(read_bytes)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_length),
                "Content-Type": "video/mp4",
            }
            return StreamingResponse(
                range_generator(video_path, start, chunk_length),
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                headers=headers,
            )

    # Full response when Range header is not present
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": "video/mp4",
    }
    return FileResponse(video_path, media_type="video/mp4", headers=headers)
