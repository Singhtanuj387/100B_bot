from core.rights.matcher import BlocklistMatcher, normalize_text, levenshtein_distance
from core.rights.ocr import VideoOCRChecker
from core.rights.audio_check import (
    AudioRightsChecker,
    LocalMusicDetector,
    AudDProvider,
    AudioRecognitionProvider,
)
from core.rights.visual_match import VisualMatchChecker
from core.rights.perceptual import HistoryClaimsChecker, compute_video_phashes
from core.rights.engine import RightsCheckEngine
from core.rights.alerts import notify_rights_alert, log_alert, send_windows_notification

__all__ = [
    "BlocklistMatcher",
    "normalize_text",
    "levenshtein_distance",
    "VideoOCRChecker",
    "AudioRightsChecker",
    "LocalMusicDetector",
    "AudDProvider",
    "AudioRecognitionProvider",
    "VisualMatchChecker",
    "HistoryClaimsChecker",
    "compute_video_phashes",
    "RightsCheckEngine",
    "notify_rights_alert",
    "log_alert",
    "send_windows_notification",
]
