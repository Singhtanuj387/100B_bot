import re
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple


def normalize_text(text: str) -> str:
    """Strip all punctuation, symbols, whitespace and lowercase."""
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def levenshtein_distance(s1: str, s2: str) -> int:
    """Standard Levenshtein edit distance."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def fuzzy_contains(haystack_norm: str, needle_norm: str, max_typos: int = 1) -> bool:
    """
    Check if needle_norm is contained within haystack_norm, allowing up to max_typos.
    If needle_norm is short (< 5 chars), require exact substring match to avoid false positives.
    """
    if not needle_norm or not haystack_norm:
        return False

    # Exact substring check first
    if needle_norm in haystack_norm:
        return True

    n_len = len(needle_norm)
    if n_len < 5 or max_typos <= 0:
        return False

    allowed_typos = 2 if (n_len >= 8 and max_typos >= 2) else 1

    # Sliding window across haystack
    h_len = len(haystack_norm)
    for w_len in (n_len - 1, n_len, n_len + 1):
        if w_len > h_len:
            continue
        for start in range(h_len - w_len + 1):
            sub = haystack_norm[start : start + w_len]
            if levenshtein_distance(sub, needle_norm) <= allowed_typos:
                return True

    return False


class BlocklistMatcher:
    """
    Fuzzy matcher against configured agency/social blocklists and dynamic learned DB blocklist.
    """

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent.parent / "config" / "agency_blocklist.yaml"
        self.config_path = Path(config_path)
        self.agencies: List[Dict[str, Any]] = []
        self.social_platforms: List[Dict[str, Any]] = []
        self._load_config()

    def _load_config(self) -> None:
        if not self.config_path.is_file():
            return
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            self.agencies = data.get("agencies", [])
            self.social_platforms = data.get("social_platforms", [])

    def check_agency_match(self, text: str) -> Optional[Tuple[str, str]]:
        """
        Check if any agency name or alias is matched in text.
        Returns (agency_name, matched_alias) or None.
        """
        if not text:
            return None
        norm_haystack = normalize_text(text)
        if not norm_haystack:
            return None

        for agency in self.agencies:
            name = agency.get("name", "Unknown Agency")
            aliases = agency.get("aliases", [name])
            for alias in aliases:
                norm_needle = normalize_text(alias)
                if norm_needle and fuzzy_contains(norm_haystack, norm_needle, max_typos=2):
                    return name, alias
        return None

    def check_social_match(self, text: str) -> Optional[Tuple[str, str]]:
        """
        Check if any social watermark name (TikTok, Instagram, etc.) is matched in text.
        Returns (platform_name, matched_alias) or None.
        """
        if not text:
            return None
        norm_haystack = normalize_text(text)
        if not norm_haystack:
            return None

        for platform in self.social_platforms:
            name = platform.get("name", "Social")
            aliases = platform.get("aliases", [name])
            for alias in aliases:
                norm_needle = normalize_text(alias)
                if norm_needle and fuzzy_contains(norm_haystack, norm_needle, max_typos=1):
                    return name, alias
        return None

    def check_domain_match(self, domain_or_url: str) -> Optional[str]:
        """Check if domain or URL matches a blocklisted agency domain."""
        if not domain_or_url:
            return None
        clean_url = domain_or_url.lower()
        for agency in self.agencies:
            for domain in agency.get("domains", []):
                if domain.lower() in clean_url:
                    return agency.get("name", domain)
        return None

    def check_learned_blocklist(
        self,
        active_entries: List[Dict[str, Any]],
        title: str = "",
        uploader: str = "",
        subreddit: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Check against dynamic learned blocklist rows from SQLite.
        Entries have type: 'uploader', 'subreddit', 'text', 'domain', 'claimant', 'track'.
        """
        norm_title = normalize_text(title)
        norm_uploader = normalize_text(uploader)
        norm_sub = normalize_text(subreddit.replace("r/", ""))

        for entry in active_entries:
            etype = entry.get("type", "")
            val = entry.get("value", "")
            norm_val = normalize_text(val)
            if not norm_val:
                continue

            if etype == "uploader" and norm_uploader == norm_val:
                return entry
            elif etype == "subreddit" and norm_sub == norm_val:
                return entry
            elif etype in ("text", "claimant", "track") and fuzzy_contains(norm_title, norm_val, max_typos=1):
                return entry

        return None
