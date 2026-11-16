import sqlite3
from pathlib import Path
import pytest
from core.db import AgentDatabase


def test_two_slots_in_same_hour(tmp_path):
    db_file = tmp_path / "test_agent.db"
    db = AgentDatabase(db_file)

    slots = [
        {
            "slot_hour": 14,
            "slot_time": "2026-11-20T14:00:00-05:00",
            "post_id": "test_p1",
            "video_path": "test1.mp4",
            "title": "First slot",
        },
        {
            "slot_hour": 14,
            "slot_time": "2026-11-20T14:30:00-05:00",
            "post_id": "test_p2",
            "video_path": "test2.mp4",
            "title": "Second slot in same hour",
        },
    ]

    # Save both slots for the same target_date in the same hour
    db.save_schedule_slots("2026-11-20", slots)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT slot_hour, slot_time, post_id, title FROM schedule WHERE target_date = '2026-11-20' ORDER BY slot_time"
    ).fetchall()

    assert len(rows) == 2
    assert rows[0]["slot_hour"] == 14
    assert rows[0]["slot_time"] == "2026-11-20T14:00:00-05:00"
    assert rows[0]["post_id"] == "test_p1"

    assert rows[1]["slot_hour"] == 14
    assert rows[1]["slot_time"] == "2026-11-20T14:30:00-05:00"
    assert rows[1]["post_id"] == "test_p2"

    conn.close()
