import json
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from loguru import logger


def probe_video(video_path: Path) -> Dict[str, Any]:
    """Inspect video dimensions, duration, and audio streams via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            return {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}

        data = json.loads(res.stdout)
        width, height = 0, 0
        has_audio = False

        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and width == 0:
                width = int(s.get("width", 0))
                height = int(s.get("height", 0))
            elif s.get("codec_type") == "audio":
                has_audio = True

        duration = float(data.get("format", {}).get("duration", 0.0))
        return {
            "duration": duration,
            "width": width,
            "height": height,
            "has_audio": has_audio,
        }
    except Exception as e:
        logger.warning(f"ffprobe failed for {video_path}: {e}")
        return {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}


def normalize_title(title: str, max_length: int = 100) -> str:
    """Ensure #Shorts is present and title does not exceed 100 characters."""
    t = (title or "Funny Moment").strip()
    if "#shorts" not in t.lower():
        tag = " #Shorts"
        if len(t) + len(tag) <= max_length:
            t = t + tag
        else:
            t = t[:max_length - len(tag)].strip() + tag
    return t[:max_length]


def normalize_description(desc: str, post_id: Optional[str] = None, max_length: int = 5000) -> str:
    """
    Ensure description includes #Shorts and hidden ref:<post_id> for crash recovery,
    and does not exceed 5000 characters.
    """
    d = (desc or "").strip()
    tag = "\n\n#Shorts" if "#shorts" not in d.lower() else ""
    ref_marker = f"\n\n[ref:{post_id}]" if post_id and f"[ref:{post_id}]" not in d else ""
    suffix = tag + ref_marker

    if len(d) + len(suffix) > max_length:
        d = d[:max_length - len(suffix)].strip()
    return (d + suffix)[:max_length]


def validate_video_and_metadata(
    video_path: Path,
    title: str,
    description: str,
    post_id: Optional[str] = None,
    max_size_bytes: int = 256 * 1024 * 1024,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate candidate video and text metadata before calling YouTube upload API.
    Fails fast with a clear descriptive reason if any requirement is violated.
    """
    p = Path(video_path)
    if not p.is_file():
        return False, f"Video file not found: {p}", {}

    file_size = p.stat().st_size
    if file_size <= 0:
        return False, f"Video file is empty (0 bytes): {p}", {}

    if file_size > max_size_bytes:
        mb = file_size / (1024 * 1024)
        return False, f"Video file too large ({mb:.1f}MB > {max_size_bytes / (1024*1024):.0f}MB)", {}

    probe = probe_video(p)
    duration = probe.get("duration", 0.0)
    width = probe.get("width", 0)
    height = probe.get("height", 0)
    has_audio = probe.get("has_audio", False)

    if duration <= 0.0:
        return False, f"Invalid or unreadable video duration ({duration}s)", probe

    # YouTube Shorts allow up to 180 seconds (3 mins)
    if duration > 180.0:
        return False, f"Duration exceeds YouTube Shorts limit ({duration:.1f}s > 180s)", probe

    # Shorts aspect ratio: vertical 9:16 or square (height >= width)
    if width > height and (width > 0 and height > 0):
        return False, f"Video is landscape ({width}x{height}); Shorts require 9:16 or square", probe

    if not has_audio:
        return False, "Video is missing an audio track", probe

    clean_title = normalize_title(title)
    clean_desc = normalize_description(description, post_id=post_id)

    return True, "OK", {
        "width": width,
        "height": height,
        "duration": duration,
        "title": clean_title,
        "description": clean_desc,
        "file_size": file_size,
    }
