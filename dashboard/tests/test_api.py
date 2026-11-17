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
    # Missing score should return null (None), not a made-up 8.0
    assert data["lineup"][0]["hybrid_score"] is None


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
    monkeypatch.setattr(routes_sched, "has_manage_scope", lambda: False)

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

    # 4. Range request with end past file size: bytes 50-200 -> clamped to 50-99
    resp_range3 = client.get("/api/video/stream_clip/stream", headers={"Range": "bytes=50-200"})
    assert resp_range3.status_code == 206
    assert resp_range3.headers["Content-Range"] == "bytes 50-99/100"
    assert resp_range3.headers["Content-Length"] == "50"
    assert resp_range3.content == data_bytes[50:100]

    # 5. Invalid post_id -> 404
    resp_404 = client.get("/api/video/nonexistent/stream")
    assert resp_404.status_code == 404


def test_thumbnail_missing_returns_placeholder(client, monkeypatch, tmp_path):
    # Missing thumbnail returns placeholder with 200 OK (no 404 console errors)
    monkeypatch.setattr(routes_yt, "get_or_generate_thumbnail", lambda post_id: None)
    dummy_placeholder = tmp_path / "placeholder.jpg"
    dummy_placeholder.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(routes_yt, "get_placeholder_thumbnail", lambda: dummy_placeholder)

    resp = client.get("/api/thumbs/missing_clip_123.jpg")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8\xff\xd9"


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


def test_put_schedule_retiming_updates_local_data(client, test_db_setup, tmp_path, monkeypatch):
    """
    Test that successful YouTube videos.update during PUT /api/schedule
    updates local SQLite (schedule.slot_time, uploads.publish_at) and schedule.json.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(tz)
    tomorrow_dt = now_ny + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")

    # 1. Setup configs
    agent_cfg = tmp_path / "agent_config.yaml"
    uploader_cfg = tmp_path / "uploader_config.yaml"
    agent_cfg.write_text("timezone: 'America/New_York'\ntarget_count: 2\npeak_hours: ['12:00', '14:00']\n", encoding="utf-8")
    uploader_cfg.write_text("upload:\n  max_uploads_per_day: 2\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", agent_cfg)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", uploader_cfg)

    # 2. Insert test scheduled clips into test_db_setup for tomorrow
    conn = sqlite3.connect(str(test_db_setup))
    old_slot_1 = f"{tomorrow_str}T12:00:00-05:00"
    old_slot_2 = f"{tomorrow_str}T14:00:00-05:00"
    old_pub_1 = f"{tomorrow_str}T17:00:00Z"
    old_pub_2 = f"{tomorrow_str}T19:00:00Z"

    conn.execute("""
        INSERT OR REPLACE INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES (?, 12, ?, 'retime_clip1', 'clip1.mp4', 'Clip 1', 'up1', 'scheduled', '2026-11-14T18:00:00');
    """, (tomorrow_str, old_slot_1))
    conn.execute("""
        INSERT OR REPLACE INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES (?, 14, ?, 'retime_clip2', 'clip2.mp4', 'Clip 2', 'up2', 'scheduled', '2026-11-14T18:00:00');
    """, (tomorrow_str, old_slot_2))

    conn.execute("""
        INSERT OR REPLACE INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
        VALUES ('retime_clip1', ?, ?, 'scheduled', 'vid_retime_1', '2026-11-14T18:01:00', 'private', ?);
    """, (tomorrow_str, old_slot_1, old_pub_1))
    conn.execute("""
        INSERT OR REPLACE INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
        VALUES ('retime_clip2', ?, ?, 'scheduled', 'vid_retime_2', '2026-11-14T18:01:00', 'private', ?);
    """, (tomorrow_str, old_slot_2, old_pub_2))
    conn.commit()
    conn.close()

    # 3. Create schedule.json for tomorrow
    data_dir = tmp_path / "data"
    edited_dir = data_dir / "edited" / tomorrow_str
    edited_dir.mkdir(parents=True, exist_ok=True)
    sched_json_path = edited_dir / "schedule.json"
    sched_json_content = {
        "target_date": tomorrow_str,
        "slots": [
            {
                "slot_hour": 12,
                "slot_time": old_slot_1,
                "post_id": "retime_clip1",
                "publish_at": old_pub_1,
            },
            {
                "slot_hour": 14,
                "slot_time": old_slot_2,
                "post_id": "retime_clip2",
                "publish_at": old_pub_2,
            },
        ]
    }
    sched_json_path.write_text(json.dumps(sched_json_content, indent=2), encoding="utf-8")

    monkeypatch.setattr(routes_sched, "SCHEDULE_DATA_DIR", data_dir)
    monkeypatch.setattr(routes_sched, "has_manage_scope", lambda: True)

    # 4. Mock update_video_publish_time
    mock_retime = MagicMock(return_value=True)
    monkeypatch.setattr(routes_sched, "update_video_publish_time", mock_retime)

    # 5. Execute PUT /api/schedule with new slots: 13:00 and 17:30
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 2, "slots": ["13:00", "17:30"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["status"] == "success"
    assert res_data["retimed_count"] == 2
    assert mock_retime.call_count == 2

    # 6. Verify SQLite schedule table was updated
    conn = sqlite3.connect(str(test_db_setup))
    rows_sched = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT post_id, slot_time, slot_hour FROM schedule WHERE target_date = ?",
            (tomorrow_str,)
        ).fetchall()
    }
    expected_slot_1 = f"{tomorrow_str}T13:00:00-05:00"
    expected_slot_2 = f"{tomorrow_str}T17:30:00-05:00"
    assert rows_sched["retime_clip1"] == (expected_slot_1, 13)
    assert rows_sched["retime_clip2"] == (expected_slot_2, 17)

    # 7. Verify SQLite uploads table was updated
    rows_uploads = {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT post_id, slot_time, publish_at FROM uploads WHERE target_date = ?",
            (tomorrow_str,)
        ).fetchall()
    }
    # 13:00 -05:00 is 18:00 UTC, 17:30 -05:00 is 22:30 UTC
    expected_pub_1 = f"{tomorrow_str}T18:00:00Z"
    expected_pub_2 = f"{tomorrow_str}T22:30:00Z"
    assert rows_uploads["retime_clip1"] == (expected_slot_1, expected_pub_1)
    assert rows_uploads["retime_clip2"] == (expected_slot_2, expected_pub_2)
    conn.close()

    # 8. Verify schedule.json was updated
    updated_json = json.loads(sched_json_path.read_text(encoding="utf-8"))
    json_slots = {s["post_id"]: s for s in updated_json["slots"]}
    assert json_slots["retime_clip1"]["slot_time"] == expected_slot_1
    assert json_slots["retime_clip1"]["slot_hour"] == 13
    assert json_slots["retime_clip1"]["publish_at"] == expected_pub_1

    assert json_slots["retime_clip2"]["slot_time"] == expected_slot_2
    assert json_slots["retime_clip2"]["slot_hour"] == 17
    assert json_slots["retime_clip2"]["publish_at"] == expected_pub_2


def test_put_schedule_no_change_does_nothing(client, test_db_setup, tmp_path, monkeypatch):
    """A no-change save must do nothing: no YouTube calls and no config rewrite."""
    cfg_file = tmp_path / "agent_config.yaml"
    cfg_content = "# Preserved comment\ntimezone: 'America/New_York'\ntarget_count: 2\npeak_hours: ['12:00', '14:00']\n"
    cfg_file.write_text(cfg_content, encoding="utf-8")
    uploader_cfg = tmp_path / "uploader_config.yaml"
    uploader_cfg.write_text("upload:\n  max_uploads_per_day: 2\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", cfg_file)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", uploader_cfg)

    mock_retime = MagicMock(return_value=True)
    monkeypatch.setattr(routes_sched, "update_video_publish_time", mock_retime)

    # Save exact same slots and count
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 2, "slots": ["12:00", "14:00"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert res["retimed_count"] == 0
    assert res["notice"] == "No changes to schedule."
    # YouTube API must NOT have been called
    assert mock_retime.call_count == 0
    # Config file must remain untouched
    assert cfg_file.read_text(encoding="utf-8") == cfg_content


def test_put_schedule_with_stale_rows_cleans_up_and_pairs_only_moved_clips(client, test_db_setup, tmp_path, monkeypatch):
    """
    Test that stale rows in schedule table (e.g. an extra 13:00 and 18:00 row)
    are cleaned up without causing unique-key collisions, and only clips that actually
    moved are retimed.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(tz)
    tomorrow_dt = now_ny + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")

    # Config has 6 slots
    agent_cfg = tmp_path / "agent_config.yaml"
    uploader_cfg = tmp_path / "uploader_config.yaml"
    agent_cfg.write_text("timezone: 'America/New_York'\ntarget_count: 6\npeak_hours: ['12:00', '14:00', '17:00', '19:00', '20:00', '21:00']\n", encoding="utf-8")
    uploader_cfg.write_text("upload:\n  max_uploads_per_day: 6\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", agent_cfg)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", uploader_cfg)

    conn = sqlite3.connect(str(test_db_setup))
    post_ids = [f"clip_{i}" for i in range(1, 7)]
    hours = [12, 14, 17, 19, 20, 21]

    # Insert 6 valid clips into uploads
    for pid, h in zip(post_ids, hours):
        st = f"{tomorrow_str}T{h:02d}:00:00-05:00"
        pub = f"{tomorrow_str}T{h+5:02d}:00:00Z"
        conn.execute("""
            INSERT OR REPLACE INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
            VALUES (?, ?, ?, 'scheduled', ?, '2026-11-16T18:00:00', 'private', ?);
        """, (pid, tomorrow_str, st, f"yt_{pid}", pub))
        conn.execute("""
            INSERT OR REPLACE INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
            VALUES (?, ?, ?, ?, 'p.mp4', 'Title', 'up', 'scheduled', '2026-11-16T18:00:00');
        """, (tomorrow_str, h, st, pid))

    # Insert STALE rows that caused previous failures:
    # 1. Duplicate row for clip_2 at 13:00
    stale_st_13 = f"{tomorrow_str}T13:00:00-05:00"
    conn.execute("""
        INSERT INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES (?, 13, ?, 'clip_2', 'p.mp4', 'Duplicate', 'up', 'scheduled', '2026-11-16T18:00:00');
    """, (tomorrow_str, stale_st_13))

    # 2. Orphaned row at 18:00
    stale_st_18 = f"{tomorrow_str}T18:00:00-05:00"
    conn.execute("""
        INSERT INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES (?, 18, ?, 'stale_orphan_clip', 'p.mp4', 'Orphan', 'up', 'scheduled', '2026-11-16T18:00:00');
    """, (tomorrow_str, stale_st_18))
    conn.commit()
    conn.close()

    monkeypatch.setattr(routes_sched, "has_manage_scope", lambda: True)
    mock_retime = MagicMock(return_value=True)
    monkeypatch.setattr(routes_sched, "update_video_publish_time", mock_retime)

    # Change ONLY the 3rd slot (17:00 -> 17:30). The other 5 slots are unchanged.
    new_slots = ["12:00", "14:00", "17:30", "19:00", "20:00", "21:00"]
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 6, "slots": new_slots},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    # ONLY clip_3 moved! The other 5 clips stayed at their uploaded slots.
    assert res["retimed_count"] == 1
    assert mock_retime.call_count == 1
    assert mock_retime.call_args[0][0] == "yt_clip_3"

    # Verify stale rows in schedule were cleaned up and no unique collisions occurred
    conn = sqlite3.connect(str(test_db_setup))
    sched_rows = conn.execute(
        "SELECT post_id, slot_time, slot_hour FROM schedule WHERE target_date = ? ORDER BY slot_time",
        (tomorrow_str,)
    ).fetchall()
    conn.close()

    # Exactly 6 rows should exist now
    assert len(sched_rows) == 6
    pids = [r[0] for r in sched_rows]
    assert "stale_orphan_clip" not in pids
    # clip_2 must only appear once
    assert pids.count("clip_2") == 1
    # clip_3 must be at 17:30
    clip_3_row = next(r for r in sched_rows if r[0] == "clip_3")
    assert f"{tomorrow_str}T17:30:00-05:00" == clip_3_row[1]


def test_put_schedule_rollback_on_youtube_failure(client, test_db_setup, tmp_path, monkeypatch):
    """
    Test that if YouTube update fails for any clip, all previous YouTube updates
    and local SQLite/schedule.json data are safely rolled back.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(tz)
    tomorrow_dt = now_ny + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")

    agent_cfg = tmp_path / "agent_config.yaml"
    uploader_cfg = tmp_path / "uploader_config.yaml"
    agent_cfg.write_text("timezone: 'America/New_York'\ntarget_count: 2\npeak_hours: ['12:00', '14:00']\n", encoding="utf-8")
    uploader_cfg.write_text("upload:\n  max_uploads_per_day: 2\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", agent_cfg)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", uploader_cfg)

    old_slot_1 = f"{tomorrow_str}T12:00:00-05:00"
    old_slot_2 = f"{tomorrow_str}T14:00:00-05:00"
    old_pub_1 = f"{tomorrow_str}T17:00:00Z"
    old_pub_2 = f"{tomorrow_str}T19:00:00Z"

    conn = sqlite3.connect(str(test_db_setup))
    conn.execute("""
        INSERT OR REPLACE INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
        VALUES ('rb_clip_1', ?, ?, 'scheduled', 'yt_rb_1', '2026-11-16T18:00:00', 'private', ?);
    """, (tomorrow_str, old_slot_1, old_pub_1))
    conn.execute("""
        INSERT OR REPLACE INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
        VALUES ('rb_clip_2', ?, ?, 'scheduled', 'yt_rb_2', '2026-11-16T18:00:00', 'private', ?);
    """, (tomorrow_str, old_slot_2, old_pub_2))

    conn.execute("""
        INSERT OR REPLACE INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES (?, 12, ?, 'rb_clip_1', 'p.mp4', 'Clip 1', 'up', 'scheduled', '2026-11-16T18:00:00');
    """, (tomorrow_str, old_slot_1))
    conn.execute("""
        INSERT OR REPLACE INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
        VALUES (?, 14, ?, 'rb_clip_2', 'p.mp4', 'Clip 2', 'up', 'scheduled', '2026-11-16T18:00:00');
    """, (tomorrow_str, old_slot_2))
    conn.commit()
    conn.close()

    monkeypatch.setattr(routes_sched, "has_manage_scope", lambda: True)

    # First video succeeds, second video fails
    call_log = []
    def mock_retime_fn(vid_id, publish_at):
        call_log.append((vid_id, publish_at))
        if vid_id == "yt_rb_2":
            return False
        return True

    monkeypatch.setattr(routes_sched, "update_video_publish_time", mock_retime_fn)

    # Request new slots: 13:00 and 15:00
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 2, "slots": ["13:00", "15:00"]},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 500
    assert "safely rolled back" in resp.json()["detail"]

    # Verify that the first video was reverted on YouTube
    revert_calls = [c for c in call_log if c[0] == "yt_rb_1" and c[1] == old_pub_1]
    assert len(revert_calls) == 1

    # Verify SQLite was rolled back to original times
    conn = sqlite3.connect(str(test_db_setup))
    sched_1 = conn.execute("SELECT slot_time, slot_hour FROM schedule WHERE post_id = 'rb_clip_1'").fetchone()
    sched_2 = conn.execute("SELECT slot_time, slot_hour FROM schedule WHERE post_id = 'rb_clip_2'").fetchone()
    up_1 = conn.execute("SELECT slot_time, publish_at FROM uploads WHERE post_id = 'rb_clip_1'").fetchone()
    up_2 = conn.execute("SELECT slot_time, publish_at FROM uploads WHERE post_id = 'rb_clip_2'").fetchone()
    conn.close()

    assert sched_1[0] == old_slot_1
    assert sched_1[1] == 12
    assert sched_2[0] == old_slot_2
    assert sched_2[1] == 14

    assert up_1[0] == old_slot_1
    assert up_1[1] == old_pub_1
    assert up_2[0] == old_slot_2
    assert up_2[1] == old_pub_2


def test_put_schedule_move_one_slot_retimes_only_that_clip(client, test_db_setup, tmp_path, monkeypatch):
    """
    Test moving ONLY one slot (e.g. 12:00 to 22:00) retimes ONLY that one clip.
    It must make exactly 1 YouTube call (12:00 clip to 22:00), leaving the other
    5 clips (14:00, 17:00, 19:00, 20:00, 21:00) untouched.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(tz)
    tomorrow_dt = now_ny + timedelta(days=1)
    tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")

    # 1. Config with 6 slots
    agent_cfg = tmp_path / "agent_config.yaml"
    uploader_cfg = tmp_path / "uploader_config.yaml"
    agent_cfg.write_text(
        "timezone: 'America/New_York'\ntarget_count: 6\npeak_hours: ['12:00', '14:00', '17:00', '19:00', '20:00', '21:00']\n",
        encoding="utf-8"
    )
    uploader_cfg.write_text("upload:\n  max_uploads_per_day: 6\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", agent_cfg)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", uploader_cfg)

    # 2. Insert 6 clips for tomorrow
    conn = sqlite3.connect(str(test_db_setup))
    hours = [12, 14, 17, 19, 20, 21]
    for h in hours:
        pid = f"clip_{h}"
        st = f"{tomorrow_str}T{h:02d}:00:00-05:00"
        pub = f"{tomorrow_str}T{h+5:02d}:00:00Z" if h + 5 < 24 else f"{tomorrow_str}T{h+5-24:02d}:00:00Z"
        conn.execute("""
            INSERT OR REPLACE INTO uploads (post_id, target_date, slot_time, status, video_id, uploaded_at, privacy, publish_at)
            VALUES (?, ?, ?, 'scheduled', ?, '2026-11-16T18:00:00', 'private', ?);
        """, (pid, tomorrow_str, st, f"yt_{pid}", pub))
        conn.execute("""
            INSERT OR REPLACE INTO schedule (target_date, slot_hour, slot_time, post_id, video_path, title, uploader, status, created_at)
            VALUES (?, ?, ?, ?, 'video.mp4', 'Title', 'uploader', 'scheduled', '2026-11-16T18:00:00');
        """, (tomorrow_str, h, st, pid))
    conn.commit()
    conn.close()

    monkeypatch.setattr(routes_sched, "has_manage_scope", lambda: True)
    mock_retime = MagicMock(return_value=True)
    monkeypatch.setattr(routes_sched, "update_video_publish_time", mock_retime)

    # Move ONLY the 12:00 slot to 22:00. The remaining 5 slots (14:00, 17:00, 19:00, 20:00, 21:00) are identical.
    new_slots = ["14:00", "17:00", "19:00", "20:00", "21:00", "22:00"]
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 6, "slots": new_slots},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    # MUST retimed exactly 1 clip!
    assert res["retimed_count"] == 1

    # Exactly 1 YouTube call for the moved clip (yt_clip_12)
    assert mock_retime.call_count == 1
    assert mock_retime.call_args[0][0] == "yt_clip_12"

    # Verify SQLite state
    conn = sqlite3.connect(str(test_db_setup))
    sched_rows = dict(conn.execute(
        "SELECT post_id, slot_hour FROM schedule WHERE target_date = ?",
        (tomorrow_str,)
    ).fetchall())
    uploads_rows = dict(conn.execute(
        "SELECT post_id, slot_time FROM uploads WHERE target_date = ?",
        (tomorrow_str,)
    ).fetchall())
    conn.close()

    # clip_12 was retimed to 22:00
    assert sched_rows["clip_12"] == 22
    assert uploads_rows["clip_12"] == f"{tomorrow_str}T22:00:00-05:00"

    # All other 5 clips kept their original slots
    for h in [14, 17, 19, 20, 21]:
        assert sched_rows[f"clip_{h}"] == h
        assert uploads_rows[f"clip_{h}"] == f"{tomorrow_str}T{h:02d}:00:00-05:00"


def test_put_schedule_preserves_all_comments_and_blank_lines(client, test_db_setup, tmp_path, monkeypatch):
    """
    Test that updating schedule with PUT /api/schedule modifies peak_hours in place
    and preserves ALL comments and blank lines throughout agent/config.yaml,
    specifically including '# Allowed sources and exploration targets (Reddit is primary; Twitter is experimental)'
    and the blank line before it. Compares the whole file.
    """
    real_agent_config = backend_db.WORKSPACE_DIR / "agent" / "config.yaml"
    original_content = real_agent_config.read_text(encoding="utf-8")

    test_agent_cfg = tmp_path / "agent_config.yaml"
    test_agent_cfg.write_text(original_content, encoding="utf-8")

    test_uploader_cfg = tmp_path / "uploader_config.yaml"
    test_uploader_cfg.write_text("upload:\n  max_uploads_per_day: 6\n", encoding="utf-8")

    monkeypatch.setattr(routes_sched, "AGENT_CONFIG_PATH", test_agent_cfg)
    monkeypatch.setattr(routes_sched, "UPLOADER_CONFIG_PATH", test_uploader_cfg)

    # PUT /api/schedule with new slots: change 12:00 to 22:00
    new_slots = ["14:00", "17:00", "19:00", "20:00", "21:00", "22:00"]
    resp = client.put(
        "/api/schedule",
        json={"videos_per_day": 6, "slots": new_slots},
        headers={"Content-Type": "application/json", "X-Dashboard-Request": "1"},
    )
    assert resp.status_code == 200

    # Read saved file content and compare whole file
    saved_content = test_agent_cfg.read_text(encoding="utf-8")

    expected_comment = "# Allowed sources and exploration targets (Reddit is primary; Twitter is experimental)"
    assert expected_comment in saved_content

    # Normalize line endings
    normalized_saved = saved_content.replace("\r\n", "\n")
    expected_block = (
        "  - 22:00\n\n"
        "# Allowed sources and exploration targets (Reddit is primary; Twitter is experimental)\n"
        "allowed_sources:"
    )
    assert expected_block in normalized_saved

    # Whole-file comparison: only the peak_hours list elements differ
    orig_lines = [l.strip() for l in original_content.replace("\r\n", "\n").splitlines()]
    saved_lines = [l.strip() for l in normalized_saved.splitlines()]

    orig_peak_idx = orig_lines.index("peak_hours:")
    orig_allowed_idx = orig_lines.index("allowed_sources:")

    saved_peak_idx = saved_lines.index("peak_hours:")
    saved_allowed_idx = saved_lines.index("allowed_sources:")

    # Everything before peak_hours must be identical
    assert orig_lines[:orig_peak_idx] == saved_lines[:saved_peak_idx]

    # Everything after allowed_sources must be identical
    assert orig_lines[orig_allowed_idx:] == saved_lines[saved_allowed_idx:]

    # The comment line between them must match
    assert expected_comment in saved_lines[saved_peak_idx:saved_allowed_idx]
