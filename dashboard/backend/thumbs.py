import subprocess
from pathlib import Path
from typing import Optional
from dashboard.backend.db import get_ro_connection, WORKSPACE_DIR

CACHE_DIR = WORKSPACE_DIR / "dashboard" / "cache" / "thumbs"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def resolve_video_path_for_post(post_id: str) -> Optional[Path]:
    """
    Safely find local video path for post_id without accepting arbitrary client paths.
    Checks schedule, reserve_pool, and known folders (edited, published).
    """
    with get_ro_connection() as conn:
        try:
            row = conn.execute("SELECT video_path FROM schedule WHERE post_id = ? LIMIT 1", (post_id,)).fetchone()
            if row and row["video_path"]:
                p = Path(row["video_path"])
                if p.is_file():
                    return p
        except Exception:
            pass

        try:
            row = conn.execute("SELECT video_path FROM reserve_pool WHERE post_id = ? LIMIT 1", (post_id,)).fetchone()
            if row and row["video_path"]:
                p = Path(row["video_path"])
                if p.is_file():
                    return p
        except Exception:
            pass

    # Search in agent/data/edited and agent/data/published
    for search_dir in (WORKSPACE_DIR / "agent" / "data" / "edited", WORKSPACE_DIR / "agent" / "data" / "published"):
        if search_dir.is_dir():
            matches = list(search_dir.glob(f"**/*{post_id}*.mp4"))
            if matches and matches[0].is_file():
                return matches[0]

    return None


def get_or_generate_thumbnail(post_id: str) -> Optional[Path]:
    """
    Check if cached thumbnail exists in dashboard/cache/thumbs/<post_id>.jpg.
    If not, extract a 1-second frame using ffmpeg and cache it.
    """
    thumb_path = CACHE_DIR / f"{post_id}.jpg"
    if thumb_path.is_file() and thumb_path.stat().st_size > 0:
        return thumb_path

    video_path = resolve_video_path_for_post(post_id)
    if not video_path or not video_path.is_file():
        return None

    try:
        # ffmpeg -ss 00:00:01 -i <video> -vframes 1 -q:v 2 <thumb_path>
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", "00:00:01",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-vf", "scale=180:-1",
            str(thumb_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if res.returncode == 0 and thumb_path.is_file() and thumb_path.stat().st_size > 0:
            return thumb_path
    except Exception:
        pass

    return None
