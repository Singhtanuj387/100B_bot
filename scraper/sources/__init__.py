from typing import Dict, Type
from sources.base import BaseSource, VideoCandidate
from sources.reddit import RedditSource
from sources.twitter import TwitterSource

SOURCES: Dict[str, Type[BaseSource]] = {
    "reddit": RedditSource,
    "twitter": TwitterSource,
}


def get_source_plugin(name: str, config: dict) -> BaseSource:
    """Instantiate a source plugin by name."""
    cls = SOURCES.get(name.lower())
    if not cls:
        raise ValueError(f"Unknown source '{name}'. Available: {list(SOURCES.keys())}")
    return cls(config)


__all__ = ["BaseSource", "VideoCandidate", "RedditSource", "TwitterSource", "get_source_plugin", "SOURCES"]

