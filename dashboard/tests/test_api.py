import os
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient

from dashboard.backend.app import app
import dashboard.backend.db as backend_db
import dashboard.backend.routes_schedule as routes_sched
import dashboard.backend.routes_youtube as routes_yt
import dashboard.backend.thumbs as backend_thumbs
import dashboard.backend.youtube_api as yt_api


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_db_setup(tmp_path, monkeypatch):
    """Setup temporary test database with run_stats, schedule, uploads, daily_runs."""
    db_file = tmp_path / "agent.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE run_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            found INTEGER DEFAULT 62,
            dropped_prescreen INTEGER DEFAULT 10,
            dropped_duration INTEGER DEFAULT 5,
            downloaded INTEGER DEFAULT 52,
            dropped_filters INTEGER DEFAULT 11,
            judged INTEGER DEFAULT 41,
            rejected_judge INTEGER DEFAULT 29,
            borderline INTEGER DEFAULT 0,
            approved INTEGER DEFAULT 12,
            selected INTEGER DEFAULT 6,
            reserve_added INTEGER DEFAULT 6,
            reject_reasons_json TEXT DEFAULT '{"not_funny": 19, "non_english": 11, "watermark": 8, "duration": 7, "tv_film": 5, "other": 0}',
            source_stats_json TEXT DEFAULT '{"r/Unexpected": {"judged": 9, "accepted": 4}}',
            created_at TEXT NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE daily_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            uploaded INTEGER DEFAULT 6,
            scheduled INTEGER DEFAULT 6,
            failed INTEGER DEFAULT 0,
            message TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_date TEXT NOT NULL,
            slot_hour INTEGER NOT NULL,
            slot_time TEXT NOT NULL,
            post_id TEXT NOT NULL,
            video_path TEXT NOT NULL,
            title TEXT,
            description TEXT,
            caption TEXT,
            source_url TEXT,
            uploader TEXT,
            status TEXT DEFAULT 'queued',
            created_at TEXT NOT NULL,
            UNIQUE(target_date, slot_time)
        );
    """)
    conn.execute("""
        CREATE TABLE uploads (
            post_id TEXT PRIMARY KEY,
            target_date TEXT NOT NULL,
            slot_time TEXT NOT NULL,
            status TEXT NOT NULL,
            video_id TEXT,
            attempts INTEGER DEFAULT 1,
            last_error TEXT,
            uploaded_at TEXT,
            privacy TEXT,
            publish_at TEXT,
            pacific_date TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE reserve_pool (
            post_id TEXT PRIMARY KEY,
            video_path TEXT NOT NULL,
            caption TEXT,
            title TEXT,
            description TEXT,
            uploader TEXT,
            post_url TEXT,
            score REAL,
            duration REAL,
            resolution TEXT,
            status TEXT DEFAULT 'available',
            added_at TEXT NOT NULL,
            used_at TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE agent_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            file_hash TEXT,
            hybrid_score REAL,
            status TEXT NOT NULL,
            decision_reason TEXT,
            target_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)

    # Populate sample rows
    conn.execute("""
        INSERT INTO daily_runs (started_at, finished_at, status, uploaded, scheduled, failed, message)
        VALUES ('2026-11-14T18:00:00', '2026-11-14T18:02:00', 'success', 6, 6, 0, 'Completed successfully');
    """)
    conn.execute("""
        INSERT INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES ('2026-11-15', 12, '2026-11-15T12:00:00-05:00', 'clip1', 'clip1.mp4', 'Tractor fail', 'u1', 'scheduled', '2026-11-14T18:00:00');
    """)
    conn.execute("""
        INSERT INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
        VALUES ('clip1', '2026-11-15', '2026-11-15T12:00:00-05:00', 'scheduled', 'yt_vid_1', '2026-11-14T18:01:00', 'private', '2026-11-15T12:00:00-05:00');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(backend_db, "DB_PATH", db_file)
    return db_file


def test_health_check(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_host_header_security(client):
    # Host header that is not localhost or 127.0.0.1 must be rejected
    resp = client.get("/api/health", headers={"Host": "malicious-site.com"})
    assert resp.status_code == 403


def test_pipeline_status(client, test_db_setup):
    resp = client.get("/api/pipeline/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "last_run" in data
    assert data["last_run"]["status"] == "success"
    assert "next_run_time" in data


def test_agent_days(client, test_db_setup):
    resp = client.get("/api/agent/days?days=7")
    assert resp.status_code == 200
    days = resp.json()
    assert isinstance(days, list)
    assert len(days) > 0


def test_agent_day_detail(client, test_db_setup):
    resp = client.get("/api/agent/day/2026-11-15")
    assert resp.status_code == 200
    data = resp.json()
    assert "totals" in data
    assert "funnel" in data
    assert "lineup" in data
    assert "reasons" in data
    assert len(data["lineup"]) == 1
    assert data["lineup"][0]["post_id"] == "clip1"


def test_schedule_get(client, test_db_setup):
    resp = client.get("/api/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert "videos_per_day" in data
    assert "slots" in data
    assert "timezone" in data


def test_schedule_put_security_and_validation(client, tmp_path, monkeypatch):
    # Setup temp configs for testing
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("# Initial comment\ntarget_count: 6\npeak_hours:\n  - '12:00'\n", encoding="utf-8")
    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", cfg_file)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", cfg_file)

    # 1. Missing custom security header
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 3, "slots": ["12:00", "14:00", "16:00"]},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "Missing security header" in resp.json()["detail"]

    # 2. Slot count mismatch
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 3, "slots": ["12:00", "14:00"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 400
    assert "Slot count" in resp.json()["detail"]

    # 3. Not snapping to 15 minutes
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 2, "slots": ["12:07", "14:00"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 400
    assert "15-minute increments" in resp.json()["detail"]

    # 4. Duplicate slots
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 2, "slots": ["12:00", "12:00"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 400
    assert "Duplicate slot time" in resp.json()["detail"]


def test_schedule_put_ruamel_roundtrip_comment_preservation(client, tmp_path, monkeypatch):
    agent_cfg = tmp_path / "agent_config.yaml"
    uploader_cfg = tmp_path / "uploader_config.yaml"

    initial_agent_yaml = (
        "# Peak publishing hours header comment\n"
        "timezone: 'America/New_York'\n"
        "target_count: 6  # inline comment on target_count\n"
        "# Another comment\n"
        "peak_hours:\n"
        "  - '12:00'\n"
        "  - '14:00'\n"
    )
    agent_cfg.write_text(initial_agent_yaml, encoding="utf-8")
    uploader_cfg.write_text("upload:\n  max_uploads_per_day: 6\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", agent_cfg)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", uploader_cfg)

    # Valid update
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 3, "slots": ["12:00", "14:15", "17:30"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["videos_per_day"] == 3
    assert data["slots"] == ["12:00", "14:15", "17:30"]

    # Verify backup exists
    assert agent_cfg.with_suffix(".yaml.bak").exists()

    # Verify comments were preserved
    updated_text = agent_cfg.read_text(encoding="utf-8")
    assert "# Peak publishing hours header comment" in updated_text
    assert "# Another comment" in updated_text
    assert "14:15" in updated_text
    assert "17:30" in updated_text


def test_youtube_status_rules(client, test_db_setup, monkeypatch):
    mock_service = MagicMock()
    mock_service.videos().list().execute.return_value = {
        "items": [
            {
                "id": "yt_vid_1",
                "status": {
                    "privacyStatus": "private",
                    "publishAt": "2026-11-15T12:00:00-05:00",
                },
                "statistics": {
                    "viewCount": "42",
                    "likeCount": "5"
                }
            }
        ]
    }
    monkeypatch.setattr(yt_api, "get_youtube_service", lambda: mock_service)
    yt_api._VIDEOS_CACHE.clear()

    resp = client.get("/api/youtube/day/2026-11-15")
    assert resp.status_code == 200
    data = resp.json()
    assert "clips" in data
    assert len(data["clips"]) == 1
    clip = data["clips"][0]
    assert clip["post_id"] == "clip1"
    # Was inserted as scheduled
    assert clip["status"] in ("Scheduled", "Published")


def test_youtube_missing_deleted_video(client, test_db_setup, monkeypatch):
    # Mock youtube service returning empty items (video was deleted on YouTube)
    mock_service = MagicMock()
    mock_service.videos().list().execute.return_value = {
        "items": []
    }
    monkeypatch.setattr(yt_api, "get_youtube_service", lambda: mock_service)
    yt_api._VIDEOS_CACHE.clear()

    resp = client.get("/api/youtube/day/2026-11-15")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["clips"]) == 1
    clip = data["clips"][0]
    assert clip["post_id"] == "clip1"
    assert clip["status"] == "Missing on YouTube"
    assert "Missing on YouTube" in clip["status_detail"]
    assert data["published_count"] == 0
    assert data["scheduled_count"] == 0


def test_http_range_video_streaming(client, tmp_path, monkeypatch):
    # Create dummy 100-byte video file
    dummy_video = tmp_path / "test_stream.mp4"
    data_bytes = bytes([i % 256 for i in range(100)])
    dummy_video.write_bytes(data_bytes)

    monkeypatch.setattr(routes_yt, "resolve_video_path_for_post", lambda post_id: dummy_video if post_id == "stream_clip" else None)

    # 1. Full request (no Range header)
    resp = client.get("/api/video/stream_clip/stream")
    assert resp.status_code == 200
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert len(resp.content) == 100

    # 2. Range request: bytes 0-9 (first 10 bytes)
    resp_range = client.get("/api/video/stream_clip/stream", headers={"Range": "bytes=0-9"})
    assert resp_range.status_code == 206
    assert resp_range.headers["Content-Range"] == "bytes 0-9/100"
    assert resp_range.headers["Content-Length"] == "10"
    assert resp_range.content == data_bytes[0:10]

    # 3. Range request: bytes 50-
    resp_range2 = client.get("/api/video/stream_clip/stream", headers={"Range": "bytes=50-"})
    assert resp_range2.status_code == 206
    assert resp_range2.headers["Content-Range"] == "bytes 50-99/100"
    assert resp_range2.content == data_bytes[50:100]

    # 4. Invalid post_id -> 404
    resp_404 = client.get("/api/video/nonexistent/stream")
    assert resp_404.status_code == 404


def test_mock_youtube_retiming(client, test_db_setup, tmp_path, monkeypatch):
    # Setup configs
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("target_count: 6\npeak_hours: ['12:00']\nupload:\n  max_uploads_per_day: 6\n", encoding="utf-8")
    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", cfg_file)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", cfg_file)

    # Mock manage scope
    monkeypatch.setattr(routes_sched, "has_manage_scope", lambda: True)

    # Mock youtube service
    mock_service = MagicMock()
    mock_service.videos().list().execute.return_value = {
        "items": [
            {
                "id": "yt_vid_1",
                "status": {
                    "privacyStatus": "private",
                    "publishAt": "2026-11-15T12:00:00Z",
                    "license": "youtube",
                    "embeddable": True,
                }
            }
        ]
    }
    monkeypatch.setattr(yt_api, "get_youtube_service", lambda: mock_service)
    monkeypatch.setattr(yt_api, "has_manage_scope", lambda: True)

    # Test updating publish time
    ret = yt_api.update_video_publish_time("yt_vid_1", "2026-11-15T14:30:00Z")
    assert ret is True

    # Verify videos().update was called with the full status object
    mock_service.videos().update.assert_called_once()
    call_args = mock_service.videos().update.call_args[1]
    assert call_args["part"] == "status"
    assert call_args["body"]["id"] == "yt_vid_1"
    assert call_args["body"]["status"]["publishAt"] == "2026-11-15T14:30:00Z"
    assert call_args["body"]["status"]["embeddable"] is True
