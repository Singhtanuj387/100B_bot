"""
Auto Video Editor — Core Processing Package
"""

from core.probe import get_video_metadata
from core.header import generate_template, render_branding, load_typography
from core.compose import compose_video, is_nvenc_available
from core.utils import (
    load_config,
    check_dependencies,
    load_font,
    create_circular_image,
    wrap_text,
    normalize_path,
    format_size,
)

__all__ = [
    "get_video_metadata",
    "generate_template",
    "render_branding",
    "load_typography",
    "compose_video",
    "is_nvenc_available",
    "load_config",
    "check_dependencies",
    "load_font",
    "create_circular_image",
    "wrap_text",
    "normalize_path",
    "format_size",
]

