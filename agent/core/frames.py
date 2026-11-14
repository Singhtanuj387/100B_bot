import base64
from pathlib import Path
from typing import List, Optional
import cv2
from loguru import logger


def extract_video_frames(
    video_path: Path,
    num_frames: int = 3,
    target_width: int = 360,
    save_thumbnail_path: Optional[Path] = None,
) -> List[str]:
    """
    Sample evenly spaced keyframes across a video, downscale to target_width,
    and return base64-encoded JPEG strings for Claude Vision evaluation.
    Optionally saves the middle frame as a thumbnail on disk.
    """
    if not video_path or not video_path.is_file():
        logger.error(f"Video file does not exist or is not a file: {video_path}")
        return []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error(f"OpenCV could not open video: {video_path}")
        return []

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            logger.warning(f"Invalid frame count {total_frames} for {video_path}")
            return []

        # Avoid extreme start and end (black frames/fades): sample between 10% and 90%
        if num_frames == 1:
            frame_indices = [int(total_frames * 0.5)]
        else:
            step = (0.80) / (num_frames - 1)
            fractions = [0.10 + i * step for i in range(num_frames)]
            frame_indices = [int(total_frames * frac) for frac in fractions]

        base64_frames: List[str] = []
        middle_idx = len(frame_indices) // 2

        for i, idx in enumerate(frame_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            # Resize keeping aspect ratio
            h, w = frame.shape[:2]
            if w > target_width:
                scale = target_width / float(w)
                new_w = target_width
                new_h = int(h * scale)
                frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

            # Save middle frame as thumbnail preview for human review if requested
            if save_thumbnail_path and i == middle_idx:
                try:
                    save_thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(save_thumbnail_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                except Exception as e:
                    logger.warning(f"Failed to save thumbnail: {e}")

            # Encode to JPEG
            success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if success:
                b64_str = base64.b64encode(buffer).decode("utf-8")
                base64_frames.append(b64_str)

        return base64_frames

    finally:
        cap.release()

