"""Test suite for YouTube Shorts upload client with mocked Google API."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from googleapiclient.errors import HttpError
from uploader.youtube_client import YouTubeUploader, QuotaTracker
from uploader.db import UploadDatabase


@pytest.fixture
def mock_youtube():
    service = MagicMock()
    return service


@pytest.fixture
def mock_db(tmp_path):
    return UploadDatabase(tmp_path / "test.db")


@pytest.fixture
def sample_config():
    return {
        "timezone": "America/New_York",
        "upload": {
            "mode": "scheduled",
            "go_live": False,
            "first_run_private": True,
            "max_uploads_per_day": 6,
            "category_id": "23",
            "tags": ["shorts", "comedy", "funny"],
            "max_late_minutes": 30,
            "chunk_size_bytes": 1024 * 1024,
        },
        "quota": {
            "daily_cap": 10000,
            "insert_cost": 1600,
            "list_cost": 1,
        },
    }


def test_quota_tracker(mock_db):
    config = {"quota": {"daily_cap": 3200, "insert_cost": 1600, "list_cost": 1}}
    tracker1 = QuotaTracker(config, db=mock_db)

    assert tracker1.can_insert() is True
    tracker1.record_insert()
    assert tracker1.used_today == 1600
    assert tracker1.can_insert() is True

    tracker1.record_insert()
    assert tracker1.used_today == 3200
    assert tracker1.can_insert() is False  # Limit reached

    # Verify persistence: A new tracker session loads existing usage from DB instead of resetting to 0
    tracker2 = QuotaTracker(config, db=mock_db)
    assert tracker2.used_today == 3200
    assert tracker2.inserts_today == 2
    assert tracker2.can_insert() is False


def test_timezone_conversion_to_utc():
    # Eastern time 12:00 PM (-05:00) -> 17:00:00Z UTC
    slot_time = "2026-11-20T12:00:00-05:00"
    utc_dt = YouTubeUploader.parse_slot_time_utc(slot_time)
    assert utc_dt.hour == 17
    assert utc_dt.tzinfo == timezone.utc
    assert utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-11-20T17:00:00Z"


def test_is_slot_past_or_too_close():
    uploader = YouTubeUploader(MagicMock(), config={}, db=MagicMock())
    now_utc = datetime.now(timezone.utc)

    # 1. Past slot
    past_str = (now_utc - timedelta(hours=2)).isoformat()
    assert uploader.is_slot_past_or_too_close(past_str, min_lead_minutes=15) is True

    # 2. Too close (5 minutes away)
    close_str = (now_utc + timedelta(minutes=5)).isoformat()
    assert uploader.is_slot_past_or_too_close(close_str, min_lead_minutes=15) is True

    # 3. Plenty of lead time (3 hours away)
    future_str = (now_utc + timedelta(hours=3)).isoformat()
    assert uploader.is_slot_past_or_too_close(future_str, min_lead_minutes=15) is False


@patch("uploader.youtube_client.validate_video_and_metadata")
@patch("uploader.youtube_client.MediaFileUpload")
def test_upload_slot_private_without_publish_at(mock_media, mock_val, mock_youtube, mock_db, sample_config, tmp_path):
    """When first_run_private is true, upload as private draft without publishAt."""
    sample_config["upload"]["first_run_private"] = True
    uploader = YouTubeUploader(mock_youtube, config=sample_config, db=mock_db)

    # Setup mocks
    mock_val.return_value = (True, "OK", {
        "title": "Funny Dog #Shorts",
        "description": "Hilarious dog\n\n#Shorts\n\n[ref:p1]",
        "file_size": 5000,
    })
    # Future slot time
    future_slot = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    slot = {
        "post_id": "p1",
        "slot_time": future_slot,
        "video_path": str(tmp_path / "vid.mp4"),
        "title": "Funny Dog",
        "description": "Hilarious dog",
    }

    # Mock resumable request
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "yt_vid_999"})
    mock_youtube.videos().insert.return_value = mock_request
    mock_youtube.videos().list().execute.return_value = {"items": [{"status": {"privacyStatus": "private"}}]}

    status, video_id, err = uploader.upload_slot(
        slot=slot,
        target_date="2026-11-20",
        confirm_public=False,
        mode="scheduled",
    )

    assert status == "uploaded"
    assert video_id == "yt_vid_999"
    assert err is None

    # Inspect insert call body
    insert_call = mock_youtube.videos().insert.call_args[1]
    body = insert_call["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert "publishAt" not in body["status"], "publishAt must NOT be set when not confirmed"


@patch("uploader.youtube_client.validate_video_and_metadata")
@patch("uploader.youtube_client.MediaFileUpload")
def test_upload_slot_scheduled_with_confirm_public(mock_media, mock_val, mock_youtube, mock_db, sample_config, tmp_path):
    """When confirm_public is true and first_run_private is false, scheduled mode sets publishAt."""
    sample_config["upload"]["first_run_private"] = False
    uploader = YouTubeUploader(mock_youtube, config=sample_config, db=mock_db)

    mock_val.return_value = (True, "OK", {
        "title": "Epic Prank #Shorts",
        "description": "Prank video\n\n#Shorts\n\n[ref:p2]",
        "file_size": 5000,
    })
    future_slot = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    slot = {
        "post_id": "p2",
        "slot_time": future_slot,
        "video_path": str(tmp_path / "vid2.mp4"),
        "title": "Epic Prank",
        "description": "Prank video",
    }

    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "yt_vid_888"})
    mock_youtube.videos().insert.return_value = mock_request
    mock_youtube.videos().list().execute.return_value = {"items": [{"status": {"privacyStatus": "private"}}]}

    status, video_id, err = uploader.upload_slot(
        slot=slot,
        target_date="2026-11-20",
        confirm_public=True,
        mode="scheduled",
    )

    assert status == "scheduled"
    assert video_id == "yt_vid_888"

    insert_call = mock_youtube.videos().insert.call_args[1]
    body = insert_call["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert "publishAt" in body["status"]


@patch("uploader.youtube_client.send_telegram_alert")
def test_check_unaudited_lock_detection(mock_alert, mock_youtube, mock_db, sample_config):
    uploader = YouTubeUploader(mock_youtube, config=sample_config, db=mock_db)

    # YouTube returned private when requested as public
    mock_youtube.videos().list().execute.return_value = {
        "items": [{"status": {"privacyStatus": "private"}}]
    }

    uploader.check_unaudited_lock(video_id="locked_vid_123", requested_privacy="public")
    assert mock_alert.called
    alert_args = mock_alert.call_args
    assert alert_args[0][0] == "UNAUDITED_PROJECT_LOCK"
