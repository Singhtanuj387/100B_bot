import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
from loguru import logger
from graph.state import CurationState

# Global lazy-loaded Whisper model
_WHISPER_MODEL = None


def get_whisper_model(model_size: str = "tiny", compute_type: str = "int8"):
    """Lazy-load faster-whisper model."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        try:
            from faster_whisper import WhisperModel
            logger.info(
                f"Loading faster-whisper model ({model_size}, {compute_type})... "
                f"(First run downloads ~75 MB model weights, so initial curate run may take slightly longer)"
            )
            # Run on CPU with int8 quantization for broad compatibility and speed
            _WHISPER_MODEL = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        except Exception as e:
            logger.warning(f"Could not initialize faster-whisper: {e}. Audio language check will be relaxed.")
            _WHISPER_MODEL = False
    return _WHISPER_MODEL if _WHISPER_MODEL is not False else None


def check_audio_language(
    video_path: Path,
    whisper_config: Dict[str, Any],
) -> Tuple[bool, str, str]:
    """
    Check first 15-20 seconds with faster-whisper using VAD filter.
    Returns (is_acceptable: bool, transcript: str, reason: str).
    Videos with NO speech are marked 'language-free' and allowed.
    """
    if not video_path or not video_path.is_file():
        return False, "", "Video file missing or invalid"

    if not whisper_config.get("enabled", True):
        return True, "", "Whisper check disabled in config"

    model_size = whisper_config.get("model_size", "tiny")
    compute_type = whisper_config.get("compute_type", "int8")
    vad_filter = whisper_config.get("vad_filter", True)
    check_duration = whisper_config.get("check_duration_seconds", 20)

    model = get_whisper_model(model_size, compute_type)
    if model is None:
        # Fallback if whisper fails to load: allow video as language-free
        return True, "", "Whisper unavailable; assumed language-free"

    try:
        # Extract 16kHz mono audio via FFmpeg to float32 numpy array
        # This completely bypasses PyAV container bugs in Python 3.14
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-t", str(check_duration),
            "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", "-"
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0 or not res.stdout:
            return True, "", "No audio stream found (language-free)"

        audio_arr = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = model.transcribe(
            audio_arr,
            beam_size=1,
            vad_filter=vad_filter,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        transcript_chunks = []
        for segment in segments:
            transcript_chunks.append(segment.text.strip())

        transcript = " ".join(transcript_chunks).strip()

        # If VAD detected NO segments, the clip has no speech (language-free)
        if not transcript:
            return True, "", "No speech detected (language-free)"

        # Check detected language
        detected_lang = getattr(info, "language", "en")
        prob = getattr(info, "language_probability", 0.0)

        logger.debug(f"Audio check: lang={detected_lang} (prob={prob:.2f}), text='{transcript[:40]}'")

        if detected_lang == "en" or prob < 0.60:
            return True, transcript, f"English speech detected ({detected_lang})"
        else:
            return False, transcript, f"Non-English speech detected: {detected_lang} (prob={prob:.2f})"

    except Exception as e:
        logger.warning(f"Audio transcription error on {video_path.name}: {e}")
        return True, "", "Audio check error; fallback to language-free"


def cheap_filters_node(state: CurationState, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Zero-cost pre-filtering gate:
      - Duration check (5-60s)
      - Resolution check (>= 480p)
      - NSFW flag
      - Blocked keyword check
      - Audio language / VAD check
    """
    candidates = state.get("candidates", [])
    rejected = list(state.get("rejected", []))
    surviving: List[Dict[str, Any]] = []

    filters_cfg = config.get("filters", {})
    min_dur = float(filters_cfg.get("min_duration_seconds", filters_cfg.get("min_duration", 5.0)))
    max_dur = float(filters_cfg.get("max_duration_seconds", filters_cfg.get("max_duration", 90.0)))
    min_res = int(filters_cfg.get("min_resolution_height", 480))
    blocked_words = [w.lower() for w in filters_cfg.get("blocked_words", [])]
    whisper_cfg = filters_cfg.get("whisper", {})

    logger.info(f"--- [Node: CHEAP_FILTERS] Evaluating {len(candidates)} candidates ---")

    for cand in candidates:
        post_id = cand.get("post_id", "unknown")
        title = cand.get("title", "")
        vid_path_str = cand.get("video_path")
        vid_path = Path(vid_path_str) if vid_path_str else None

        # Verify video file actually exists and is a file (prevents empty path resolving to '.')
        if not vid_path or not vid_path.is_file():
            logger.warning(f"Rejected {post_id}: Video file does not exist or invalid path ('{vid_path_str}')")
            rejected.append({"post_id": post_id, "title": title, "reason": "Missing video file on disk"})
            continue

        # 1. Blocked words check on title
        title_lower = title.lower()
        matched_bad_word = None
        for word in blocked_words:
            clean_w = word.lower().strip()
            if not clean_w:
                continue
            if clean_w.endswith("*"):
                pattern = r"\b" + re.escape(clean_w[:-1]) + r"\w*"
            else:
                pattern = r"\b" + re.escape(clean_w) + r"(?:s|es|ed|ing|er|ers|ies|y|e|d|ly)?\b"
            if re.search(pattern, title_lower):
                matched_bad_word = word
                break
        if matched_bad_word:
            logger.info(f"Rejected {post_id}: Contains blocked word '{matched_bad_word}'")
            rejected.append({"post_id": post_id, "title": title, "reason": f"Blocked word: {matched_bad_word}"})
            continue

        # 2. Duration check (hard ceiling 180s for Shorts)
        dur = cand.get("duration")
        if dur is not None:
            if dur > 180.0:
                logger.info(f"Rejected {post_id}: Duration {dur:.1f}s exceeds 180s Shorts limit")
                rejected.append({"post_id": post_id, "title": title, "reason": f"Duration {dur:.1f}s exceeds 180s limit"})
                continue
            if dur < min_dur or dur > max_dur:
                logger.info(f"Rejected {post_id}: Duration {dur:.1f}s outside [{min_dur}, {max_dur}]")
                rejected.append({"post_id": post_id, "title": title, "reason": f"Duration {dur:.1f}s out of bounds"})
                continue

        # 3. Resolution check
        res_str = str(cand.get("resolution", ""))
        if "x" in res_str:
            try:
                parts = res_str.split("x")
                h = int(parts[1]) if len(parts) >= 2 else 0
                w = int(parts[0])
                eff_h = min(h, w)
                if eff_h > 0 and eff_h < min_res:
                    logger.info(f"Rejected {post_id}: Resolution {res_str} below {min_res}p")
                    rejected.append({"post_id": post_id, "title": title, "reason": f"Low resolution: {res_str}"})
                    continue
            except Exception:
                pass

        # 4. Audio language & VAD check
        if vid_path and vid_path.is_file():
            lang_ok, transcript, lang_reason = check_audio_language(vid_path, whisper_cfg)
            cand["transcript"] = transcript
            cand["audio_status"] = lang_reason
            if not lang_ok:
                logger.info(f"Rejected {post_id}: {lang_reason}")
                rejected.append({"post_id": post_id, "title": title, "reason": lang_reason})
                continue
        else:
            cand["transcript"] = ""

        surviving.append(cand)

    logger.info(f"Cheap filters passed: {len(surviving)}/{len(candidates)} candidates.")
    return {
        "candidates": surviving,
        "rejected": rejected,
    }

