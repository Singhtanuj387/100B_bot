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


def get_placeholder_thumbnail() -> Path:
    """
    Return path to a 9:16 gradient placeholder JPEG.
    Generates it via PIL and caches in CACHE_DIR / 'placeholder.jpg'.
    """
    placeholder_path = CACHE_DIR / "placeholder.jpg"
    if placeholder_path.is_file() and placeholder_path.stat().st_size > 0:
        return placeholder_path

    try:
        from PIL import Image, ImageDraw

        width, height = 180, 320
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)

        # Gradient from #335C4F (51, 92, 79) to #17302B (23, 48, 43)
        top_color = (51, 92, 79)
        bottom_color = (23, 48, 43)
        for y in range(height):
            ratio = y / float(height - 1)
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # Draw subtle centered play triangle
        cx, cy = width // 2, height // 2
        play_size = 14
        points = [
            (cx - play_size // 2, cy - play_size),
            (cx - play_size // 2, cy + play_size),
            (cx + play_size, cy),
        ]
        draw.polygon(points, fill=(255, 255, 255))

        img.save(str(placeholder_path), "JPEG", quality=85)
        return placeholder_path
    except Exception:
        if not placeholder_path.exists():
            placeholder_path.write_bytes(
                b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
            )
        return placeholder_path
