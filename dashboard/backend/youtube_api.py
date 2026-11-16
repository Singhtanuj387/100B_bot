import time
import json
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource
from dashboard.backend.db import WORKSPACE_DIR

TOKEN_MANAGE_PATH = WORKSPACE_DIR / "uploader" / "secrets" / "token_manage.json"
TOKEN_PATH = WORKSPACE_DIR / "uploader" / "secrets" / "token.json"

# In-memory cache for videos.list: {video_id: (timestamp, stats_and_status)}
_VIDEOS_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 60.0


def has_manage_scope() -> bool:
    """Check if token_manage.json exists and possesses the youtube manage scope."""
    for p in (TOKEN_MANAGE_PATH, TOKEN_PATH):
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                scopes = data.get("scopes", [])
                if "https://www.googleapis.com/auth/youtube" in scopes:
                    return True
            except Exception:
                pass
    return False


def get_youtube_service() -> Optional[Resource]:
    """Build YouTube service from token_manage.json if available, or token.json."""
    token_file = None
    if TOKEN_MANAGE_PATH.is_file():
        token_file = TOKEN_MANAGE_PATH
    elif TOKEN_PATH.is_file():
        token_file = TOKEN_PATH

    if not token_file:
        return None

    try:
        data = json.loads(token_file.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(data)
        return build("youtube", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def fetch_live_video_stats(video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Fetch statistics and status for up to 50 video IDs via videos.list.
    Results are cached in-memory for 60 seconds.
    Returns: {video_id: {"status": {...}, "statistics": {...}}}
    """
    if not video_ids:
        return {}

    now = time.time()
    results: Dict[str, Dict[str, Any]] = {}
    missing_ids: List[str] = []

    for vid in video_ids:
        if vid in _VIDEOS_CACHE:
            ts, data = _VIDEOS_CACHE[vid]
            if now - ts < CACHE_TTL_SECONDS:
                results[vid] = data
                continue
        missing_ids.append(vid)

    if not missing_ids:
        return results

    service = get_youtube_service()
    if not service:
        # If no client or offline, return empty for missing IDs
        return results

    # Batch up to 50 IDs per request
    for i in range(0, len(missing_ids), 50):
        chunk = missing_ids[i:i + 50]
        try:
            resp = service.videos().list(
                part="status,statistics",
                id=",".join(chunk)
            ).execute()
            returned_ids = set()
            for item in resp.get("items", []):
                vid = item["id"]
                returned_ids.add(vid)
                data = {
                    "found": True,
                    "missing": False,
                    "status": item.get("status", {}),
                    "statistics": item.get("statistics", {}),
                }
                _VIDEOS_CACHE[vid] = (now, data)
                results[vid] = data

            # For an ID YouTube doesn't return, mark it as missing
            for vid in chunk:
                if vid not in returned_ids:
                    data = {
                        "found": False,
                        "missing": True,
                        "status": {},
                        "statistics": {},
                    }
                    _VIDEOS_CACHE[vid] = (now, data)
                    results[vid] = data
        except Exception:
            # On API error, do not crash; leave missing IDs uncached
            pass

    return results


def update_video_publish_time(video_id: str, new_publish_at_rfc3339: str) -> bool:
    """
    Update publishAt for a scheduled video.
    Crucial Rule: Read current status first, and send the full status back with only publishAt changed.
    """
    if not has_manage_scope():
        return False

    service = get_youtube_service()
    if not service:
        return False

    try:
        # 1. Read current status
        current = service.videos().list(part="status", id=video_id).execute()
        items = current.get("items", [])
        if not items:
            return False

        current_status = items[0].get("status", {})
        # Never touch already public videos
        if current_status.get("privacyStatus") == "public":
            return False

        # 2. Update publishAt
        current_status["publishAt"] = new_publish_at_rfc3339
        # Keep privacyStatus private when publishAt is set
        current_status["privacyStatus"] = "private"

        # 3. Call videos.update with the full status object
        service.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": current_status,
            }
        ).execute()

        # Invalidate cache
        _VIDEOS_CACHE.pop(video_id, None)
        return True
    except Exception:
        return False
