import os
import cv2
import json
import imagehash
from PIL import Image
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger


def compute_video_phashes(video_path: str, num_frames: int = 3) -> List[str]:
    """Compute perceptual hashes (dHash + pHash) for num_frames of a video."""
    if not video_path or not os.path.isfile(video_path):
        return []
    hashes = []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return hashes

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        return hashes

    indices = [int(total_frames * (i + 1) / (num_frames + 1)) for i in range(num_frames)]

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret and frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            # Combine pHash and dHash for high discrimination
            ph = str(imagehash.phash(pil_img))
            dh = str(imagehash.dhash(pil_img))
            hashes.append(f"{ph}:{dh}")

    cap.release()
    return hashes


def compare_phashes(hashes_a: List[str], hashes_b: List[str], threshold: int = 8) -> Tuple[bool, int]:
    """
    Compare two sets of video perceptual hashes.
    Returns (is_duplicate_match, min_hamming_distance).
    If distance < threshold (default 8) across matching frame positions, it is a near-duplicate.
    """
    if not hashes_a or not hashes_b:
        return False, 999

    min_dist = 999
    match_count = 0

    for ha in hashes_a:
        parts_a = ha.split(":")
        pa = imagehash.hex_to_hash(parts_a[0])
        da = imagehash.hex_to_hash(parts_a[1]) if len(parts_a) > 1 else pa

        for hb in hashes_b:
            parts_b = hb.split(":")
            pb = imagehash.hex_to_hash(parts_b[0])
            db = imagehash.hex_to_hash(parts_b[1]) if len(parts_b) > 1 else pb

            dist_p = pa - pb
            dist_d = da - db
            avg_dist = (dist_p + dist_d) // 2
            if avg_dist < min_dist:
                min_dist = avg_dist

            if dist_p < threshold and dist_d < threshold:
                match_count += 1

    is_match = (match_count >= 1 and min_dist < threshold)
    return is_match, min_dist


class HistoryClaimsChecker:
    """
    Layer 5: Checks incoming videos against historical claimed video perceptual hashes,
    and handles blocklist suggestion generation.
    """

    def check_history_duplicate(
        self,
        video_phashes: List[str],
        claimed_clips_rows: List[Dict[str, Any]],
        threshold: int = 8,
    ) -> Optional[Dict[str, Any]]:
        """
        Check if video matches any previously claimed clip via perceptual hashing.
        Returns matching claim dict or None.
        """
        if not video_phashes:
            return None

        for claim in claimed_clips_rows:
            raw_hashes = claim.get("phashes_json")
            if not raw_hashes:
                continue
            try:
                claim_hashes = json.loads(raw_hashes) if isinstance(raw_hashes, str) else raw_hashes
            except Exception:
                continue

            is_match, dist = compare_phashes(video_phashes, claim_hashes, threshold=threshold)
            if is_match:
                return {
                    "matched_claim_id": claim.get("id"),
                    "claimant": claim.get("claimant"),
                    "asset_title": claim.get("asset_title"),
                    "distance": dist,
                }
        return None

    def generate_claim_suggestions(self, claim_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate suggested additions for the learned blocklist based on a registered claim:
          - Agency/asset keywords from asset_title
          - Claimant name
          - Uploader
          - Subreddit penalty
        """
        suggestions = []
        claim_id = claim_data.get("id")

        # 1. Claimant
        claimant = (claim_data.get("claimant") or "").strip()
        if claimant and len(claimant) >= 3:
            suggestions.append({
                "type": "claimant",
                "value": claimant,
                "source_claim_id": claim_id,
                "status": "pending_confirmation",
            })

        # 2. Asset title keywords
        asset_title = (claim_data.get("asset_title") or "").strip()
        if asset_title:
            # Common agency indicators in asset titles (e.g. "Collab ViralHog - Female Pheasant...")
            for ag in ("ViralHog", "Jukin", "Collab", "Storyful", "Newsflare", "Caters", "Barcroft", "Rumble"):
                if ag.lower() in asset_title.lower():
                    suggestions.append({
                        "type": "text",
                        "value": ag,
                        "source_claim_id": claim_id,
                        "status": "pending_confirmation",
                    })

            # Track title / artist if audio claim
            if claim_data.get("claim_type") in ("audio", "both") and len(asset_title) >= 4:
                suggestions.append({
                    "type": "track",
                    "value": asset_title,
                    "source_claim_id": claim_id,
                    "status": "pending_confirmation",
                })

        # 3. Uploader
        uploader = (claim_data.get("uploader") or "").strip()
        if uploader and uploader not in ("Unknown", "KnownUploader", "[deleted]"):
            suggestions.append({
                "type": "uploader",
                "value": uploader,
                "source_claim_id": claim_id,
                "status": "pending_confirmation",
            })

        # 4. Subreddit penalty
        src = (claim_data.get("source_name") or "").strip()
        if src.startswith("r/"):
            sub = src[2:]
            suggestions.append({
                "type": "subreddit",
                "value": sub,
                "source_claim_id": claim_id,
                "status": "pending_confirmation",
            })

        # Deduplicate suggestions by (type, value)
        unique_suggestions = []
        seen = set()
        for s in suggestions:
            k = (s["type"], s["value"].lower())
            if k not in seen:
                seen.add(k)
                unique_suggestions.append(s)

        return unique_suggestions
