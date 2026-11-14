import hashlib
from pathlib import Path
from typing import List, Optional, Tuple
import cv2
from PIL import Image
import imagehash
from loguru import logger
from core.db import VideoDatabase


def compute_file_sha256(filepath: str | Path) -> str:
    """Compute SHA-256 hash of a file's binary contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def extract_keyframes_and_phash(
    video_path: str | Path,
    sample_percentages: List[int] = [15, 50, 85],
) -> List[str]:
    """
    Extract video frames at specified percentage checkpoints and compute
    their perceptual difference hashes (dhash).

    Returns:
        List of hex strings representing perceptual frame hashes.
    """
    path_str = str(Path(video_path).resolve())
    cap = cv2.VideoCapture(path_str)

    if not cap.isOpened():
        logger.warning(f"OpenCV could not open video for hashing: {video_path}")
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        return []

    phashes = []
    for pct in sample_percentages:
        frame_idx = max(0, min(total_frames - 1, int(total_frames * (pct / 100.0))))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret and frame is not None:
            # Convert BGR (OpenCV) to RGB (Pillow)
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_frame)
            # Compute difference hash (dhash)
            h = imagehash.dhash(pil_img)
            phashes.append(str(h))

    cap.release()
    return phashes


class Deduplicator:
    """
    3-Layer deduplication engine:
      1. Post ID / URL deduplication (before download)
      2. File content hash deduplication (after download)
      3. Perceptual frame hashing (cross-platform / re-upload detection)
    """

    def __init__(self, db: VideoDatabase, phash_threshold: int = 6):
        self.db = db
        self.phash_threshold = phash_threshold

    def is_id_or_url_seen(self, post_id: str, post_url: str = "") -> Tuple[bool, str]:
        """Pre-download check: Check if post_id or post_url has been recorded."""
        if self.db.is_id_seen(post_id):
            return True, f"post_id '{post_id}' already seen in database"
        if post_url and self.db.is_url_seen(post_url):
            return True, f"post_url '{post_url}' already seen in database"
        return False, ""

    def check_downloaded_media(
        self,
        filepath: str | Path,
        sample_percentages: List[int] = [15, 50, 85],
    ) -> Tuple[bool, str, str, List[str]]:
        """
        Post-download verification:
          - Computes binary file hash
          - Computes frame perceptual hashes
          - Checks database for duplicates

        Returns:
            (is_duplicate, reason, file_hash, list_of_phashes)
        """
        # Level 2: Binary file hash
        file_hash = compute_file_sha256(filepath)
        if self.db.is_file_hash_seen(file_hash):
            return True, f"Exact file hash match: {file_hash[:12]}...", file_hash, []

        # Level 3: Perceptual frame hash
        phashes = extract_keyframes_and_phash(filepath, sample_percentages)
        if phashes:
            match = self.db.find_phash_duplicates(phashes, threshold=self.phash_threshold)
            if match:
                matched_id = match.get("post_id", "unknown")
                return True, f"Perceptual repost duplicate of post '{matched_id}'", file_hash, phashes

        return False, "", file_hash, phashes

