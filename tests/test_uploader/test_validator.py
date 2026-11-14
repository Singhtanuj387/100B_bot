"""Test suite for uploader pre-flight validator."""
import pytest
from pathlib import Path
from unittest.mock import patch

from uploader.validator import (
    validate_video_and_metadata,
    normalize_title,
    normalize_description,
)


def test_normalize_title():
    # Adds #Shorts if missing
    t1 = normalize_title("Insane skateboard trick")
    assert "#Shorts" in t1
    assert t1 == "Insane skateboard trick #Shorts"

    # Keeps existing #shorts without duplicating
    t2 = normalize_title("Funny dog #shorts")
    assert t2 == "Funny dog #shorts"

    # Truncates to max 100 characters while preserving #Shorts
    long_title = "A" * 120
    t3 = normalize_title(long_title)
    assert len(t3) <= 100
    assert "#Shorts" in t3


def test_normalize_description():
    d1 = normalize_description("Credit: test user", post_id="reddit_abc123")
    assert "#Shorts" in d1
    assert "[ref:reddit_abc123]" in d1

    # Length limit preserved
    long_desc = "B" * 6000
    d2 = normalize_description(long_desc, post_id="reddit_xyz")
    assert len(d2) <= 5000
    assert "[ref:reddit_xyz]" in d2


@patch("uploader.validator.probe_video")
def test_validate_video_success(mock_probe, tmp_path):
    mock_probe.return_value = {
        "duration": 45.0,
        "width": 1080,
        "height": 1920,
        "has_audio": True,
    }
    vid = tmp_path / "test.mp4"
    vid.write_bytes(b"dummy video content")

    valid, reason, data = validate_video_and_metadata(
        video_path=vid,
        title="Epic Cat Jump",
        description="Hilarious cat",
        post_id="post_1",
    )
    assert valid is True
    assert reason == "OK"
    assert data["duration"] == 45.0
    assert data["width"] == 1080
    assert data["height"] == 1920


@patch("uploader.validator.probe_video")
def test_validate_video_duration_too_long(mock_probe, tmp_path):
    mock_probe.return_value = {
        "duration": 190.0,  # > 180s Shorts limit
        "width": 1080,
        "height": 1920,
        "has_audio": True,
    }
    vid = tmp_path / "long.mp4"
    vid.write_bytes(b"dummy")

    valid, reason, data = validate_video_and_metadata(vid, "Title", "Desc")
    assert valid is False
    assert "exceeds YouTube Shorts limit" in reason


@patch("uploader.validator.probe_video")
def test_validate_video_landscape_rejected(mock_probe, tmp_path):
    mock_probe.return_value = {
        "duration": 30.0,
        "width": 1920,
        "height": 1080,  # Landscape: width > height
        "has_audio": True,
    }
    vid = tmp_path / "landscape.mp4"
    vid.write_bytes(b"dummy")

    valid, reason, data = validate_video_and_metadata(vid, "Title", "Desc")
    assert valid is False
    assert "Shorts require 9:16 or square" in reason


@patch("uploader.validator.probe_video")
def test_validate_video_missing_audio(mock_probe, tmp_path):
    mock_probe.return_value = {
        "duration": 20.0,
        "width": 1080,
        "height": 1920,
        "has_audio": False,  # Missing audio
    }
    vid = tmp_path / "silent.mp4"
    vid.write_bytes(b"dummy")

    valid, reason, data = validate_video_and_metadata(vid, "Title", "Desc")
    assert valid is False
    assert "missing an audio track" in reason


def test_validate_video_missing_file(tmp_path):
    vid = tmp_path / "nonexistent.mp4"
    valid, reason, _ = validate_video_and_metadata(vid, "Title", "Desc")
    assert valid is False
    assert "not found" in reason
