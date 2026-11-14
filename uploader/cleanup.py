import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger
import yaml

from uploader.db import UploadDatabase


DEFAULT_BASE_DIR = Path(__file__).resolve().parent.parent


def archive_verified_slot(
    video_path: Path,
    target_date: str,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Move an uploaded/scheduled video file from data/edited/<date> to data/published/<date>.
    Preserves schedule.json and meta.json in data/edited/<date>.
    """
    src = Path(video_path)
    if not src.is_file():
        return None

    root = Path(base_dir or DEFAULT_BASE_DIR).resolve()
    dest_dir = root / "agent" / "data" / "published" / target_date
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / src.name

    try:
        shutil.move(str(src), str(dest_path))
        logger.info(f"Archived verified video to: {dest_path}")
        return dest_path
    except Exception as e:
        logger.error(f"Failed to archive {src} -> {dest_path}: {e}")
        return None


def purge_old_media(
    retention_days: int = 14,
    base_dir: Optional[Path] = None,
    db: Optional[UploadDatabase] = None,
) -> Dict[str, int]:
    """
    Safely purge raw and edited media files older than retention_days.
    
    STRICT SAFETY RULES:
    1. Only removes media files (.mp4, .mov, .mkv, .webm, .avi) in dated directories older than retention_days.
    2. NEVER touches the reserve pool (agent/data/reserve).
    3. NEVER touches deduplication, agent, or registry databases (*.db).
    4. NEVER deletes schedule.json or meta.json.
    """
    root = Path(base_dir or DEFAULT_BASE_DIR).resolve()
    cutoff_dt = datetime.now() - timedelta(days=retention_days)
    cutoff_date_str = cutoff_dt.strftime("%Y-%m-%d")

    logger.info(f"Purging media older than {retention_days} days (Cutoff: {cutoff_date_str})...")

    targets = [
        root / "agent" / "data" / "published",
        root / "agent" / "data" / "edited",
        root / "agent" / "data" / "raw",
        root / "scraper" / "data" / "raw",
    ]

    purged_files = 0
    purged_bytes = 0

    video_extensions = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

    for target_dir in targets:
        if not target_dir.is_dir():
            continue

        for date_dir in target_dir.iterdir():
            if not date_dir.is_dir():
                continue

            # Verify it's a date folder (YYYY-MM-DD)
            dir_name = date_dir.name
            try:
                folder_dt = datetime.strptime(dir_name, "%Y-%m-%d")
            except ValueError:
                continue

            if folder_dt >= cutoff_dt:
                continue  # Within retention period; skip

            # Explicit reserve-pool safety check
            if "reserve" in str(date_dir).lower():
                logger.warning(f"Safety trigger: Refusing to touch reserve directory: {date_dir}")
                continue

            # Iterate through files in the expired date directory
            for f in date_dir.glob("*"):
                # Safety checks: never delete DB, schedule.json, or meta.json
                if f.name in ("schedule.json", "meta.json") or f.suffix.lower() in (".db", ".sqlite", ".json"):
                    continue

                if f.suffix.lower() in video_extensions:
                    try:
                        size = f.stat().st_size
                        f.unlink()
                        purged_files += 1
                        purged_bytes += size
                        logger.debug(f"Purged expired media: {f}")
                    except Exception as e:
                        logger.warning(f"Could not purge {f}: {e}")

    mb = purged_bytes / (1024 * 1024)
    logger.info(f"Cleanup complete: Removed {purged_files} file(s) ({mb:.1f} MB) older than {cutoff_date_str}.")
    return {"purged_files": purged_files, "purged_bytes": purged_bytes}
