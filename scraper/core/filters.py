import re
from typing import Tuple, Dict, Any, List, Optional
from loguru import logger

# Common English stop words for fast language verification
ENGLISH_INDICATORS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her",
    "she", "or", "an", "will", "my", "one", "all", "would", "there",
    "their", "what", "so", "up", "out", "if", "about", "who", "get",
    "which", "go", "me", "when", "make", "can", "like", "time", "no",
    "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then",
    "now", "look", "only", "come", "its", "over", "think", "also",
    "bro", "man", "dog", "cat", "dude", "wait", "guy", "trying", "laugh",
}


def is_english_or_universal(text: str) -> bool:
    """
    Fast, offline language verification.
    Verifies that the text is predominantly Latin/ASCII characters
    and does not contain foreign non-Latin scripts (Cyrillic, Arabic, CJK, etc.).
    """
    if not text:
        return True

    # Check for non-Latin / non-ASCII character blocks
    # Allow standard punctuation, emojis, and Latin-1
    non_latin = re.findall(r"[\u0400-\u04FF\u0600-\u06FF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF\u0900-\u097F]", text)
    if len(non_latin) > 2:
        return False

    # Check Latin words against English indicators if text is long enough
    words = re.findall(r"[A-Za-z]+", text.lower())
    if len(words) >= 3:
        eng_count = sum(1 for w in words if w in ENGLISH_INDICATORS)
        # If text has multiple words but none match English, it might be Spanish/French/German
        # But short meme titles like "Cat jumps" are accepted
        if eng_count == 0 and len(words) >= 5:
            return False

    return True


def check_candidate_pre_download(candidate, config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Pre-download filter to discard unwanted candidates early before downloading media.
    Checks:
      - Duration (if known before download): min_duration_seconds <= dur <= max_duration_seconds (max 180s)
      - NSFW flag
      - Keyword blocklist (politics, gore, tragedy, death)
      - English / universal text
      - Minimum upvotes / likes threshold
    """
    filter_cfg = config.get("filters", {})
    min_dur = float(filter_cfg.get("min_duration_seconds", filter_cfg.get("min_duration", 5.0)))
    max_dur = float(filter_cfg.get("max_duration_seconds", filter_cfg.get("max_duration", 90.0)))

    # 0. Pre-download Duration Check (if duration is known from Reddit JSON/PRAW)
    cand_dur = getattr(candidate, "duration", None)
    if cand_dur is not None:
        try:
            dur_val = float(cand_dur)
            # YouTube Shorts allow up to 180s (since Oct 2024); reject > 180s always
            if dur_val > 180.0:
                return False, f"duration_too_long: Duration too long ({dur_val:.1f}s > 180.0s limit)"
            if dur_val > max_dur:
                return False, f"duration_too_long: Duration too long ({dur_val:.1f}s > {max_dur:.1f}s)"
            if dur_val < min_dur:
                return False, f"duration_too_short: Duration too short ({dur_val:.1f}s < {min_dur:.1f}s)"
        except (ValueError, TypeError):
            pass

    # 1. NSFW Check
    if filter_cfg.get("skip_nsfw", True) and getattr(candidate, "is_nsfw", False):
        return False, "NSFW flagged"

    # 2. Keyword Blocklist Check
    title = (candidate.title or "").lower()
    blocklist = filter_cfg.get("keyword_blocklist", [])
    for kw in blocklist:
        clean_kw = kw.lower().strip()
        if not clean_kw:
            continue
        # Match stems like "injur" or words like "kill" with suffixes (killed, injured, killing, injuries)
        if clean_kw.endswith("*"):
            pattern = r"\b" + re.escape(clean_kw[:-1]) + r"\w*"
        else:
            pattern = r"\b" + re.escape(clean_kw) + r"(?:s|es|ed|ing|er|ers|ies|y|e|d|ly)?\b"
        if re.search(pattern, title):
            return False, f"Blocked keyword: '{kw}'"

    # 3. Language Check
    if filter_cfg.get("enforce_english", True):
        if not is_english_or_universal(candidate.title or ""):
            return False, "Non-English title"

    # 4. Minimum Upvotes / Engagement
    min_upvotes = filter_cfg.get("min_upvotes", 0)
    if candidate.score < min_upvotes:
        return False, f"Low engagement (score {candidate.score} < {min_upvotes})"

    return True, "Passed pre-download filter"


def check_media_post_download(
    duration: float,
    width: int,
    height: int,
    config: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Post-download filter:
      - Duration: 5.0s <= duration <= 90.0s (configurable, hard limit 180s)
      - Resolution: height >= 480 or width >= 480
    """
    filter_cfg = config.get("filters", {})
    min_dur = float(filter_cfg.get("min_duration_seconds", filter_cfg.get("min_duration", 5.0)))
    max_dur = float(filter_cfg.get("max_duration_seconds", filter_cfg.get("max_duration", 90.0)))

    if duration > 180.0:
        return False, f"duration_too_long: Duration too long ({duration:.1f}s > 180.0s hard limit)"
    if duration < min_dur:
        return False, f"duration_too_short: Duration too short ({duration:.1f}s < {min_dur:.1f}s)"
    if duration > max_dur:
        return False, f"duration_too_long: Duration too long ({duration:.1f}s > {max_dur:.1f}s)"

    min_h = filter_cfg.get("min_height", 480)
    min_w = filter_cfg.get("min_width", 480)

    if height < min_h and width < min_w:
        return False, f"Resolution too low ({width}x{height} < {min_w}x{min_h})"

    return True, "Passed media quality check"


def rank_candidates(candidates: List[Any], config: Optional[Dict[str, Any]] = None) -> List[Any]:
    """
    Rank candidate posts by engagement and freshness.
    Composite Score = Upvotes + (2.0 * Comments) + Upvote_Ratio * 50
    """
    def score_fn(c):
        base_score = float(getattr(c, "score", 0))
        comments = float(getattr(c, "num_comments", 0))
        ratio = float(getattr(c, "upvote_ratio", 0.9)) if getattr(c, "upvote_ratio", None) else 0.9

        # Bonus for LLM funny score if present
        funny_bonus = llm_funny_score({"title": c.title, "score": c.score})

        return base_score + (comments * 2.0) + (ratio * 50.0) + funny_bonus

    return sorted(candidates, key=score_fn, reverse=True)


def llm_funny_score(video_meta: Dict[str, Any]) -> float:
    """
    Optional hook: leave a clear function llm_funny_score(video_meta)
    that can be connected to an LLM later. For now, returns a baseline score.
    """
    # Placeholder: return baseline 10.0 (ready for LLM prompt rating 0-100)
    return 10.0

