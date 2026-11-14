from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field


class VideoCandidate(BaseModel):
    """Normalized video candidate metadata representation."""
    source: str
    post_id: str
    post_url: str
    title: str
    uploader: str
    score: int = 0
    upvote_ratio: float = 0.95
    num_comments: int = 0
    created_utc: float = 0.0
    video_url: Optional[str] = None
    is_nsfw: bool = False
    duration: Optional[float] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class BaseSource(ABC):
    """
    Abstract base plugin class for video sources.
    Every source (Reddit, X, Instagram, TikTok, etc.) implements this interface.
    """

    name: str = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def fetch_candidates(self, target_count: int) -> List[VideoCandidate]:
        """
        Fetch video candidates from the platform.
        Returns a list of un-downloaded VideoCandidate objects.
        """
        pass

