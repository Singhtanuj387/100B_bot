import pytest
from unittest.mock import MagicMock
from graph.nodes.schedule import schedule_node, interleave_sources


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def schedule_config():
    return {
        "timezone": "UTC",
        "peak_hours": [12, 13, 18, 19],
        "target_count": 4,
    }


def test_interleave_sources():
    videos = [
        {"post_id": "r1", "source": "reddit", "extra": {"subreddit": "funny"}},
        {"post_id": "r2", "source": "reddit", "extra": {"subreddit": "funny"}},
        {"post_id": "r3", "source": "reddit", "extra": {"subreddit": "funny"}},
        {"post_id": "t1", "source": "twitter"},
        {"post_id": "t2", "source": "twitter"},
    ]
    interleaved = interleave_sources(videos)
    assert len(interleaved) == 5
    # Ensure first two items have different sources
    assert interleaved[0]["source"] != interleaved[1]["source"]


def test_schedule_peak_allocation(schedule_config, mock_db):
    state = {
        "target_count": 4,
        "target_date": "2026-11-15",
        "dry_run": True,
        "approved": [
            {"post_id": "vid_high_1", "score": 9.5, "source": "reddit", "video_path": "v1.mp4", "title": "Top 1"},
            {"post_id": "vid_high_2", "score": 9.0, "source": "twitter", "video_path": "v2.mp4", "title": "Top 2"},
            {"post_id": "vid_low_1", "score": 8.1, "source": "reddit", "video_path": "v3.mp4", "title": "Low 1"},
            {"post_id": "vid_low_2", "score": 8.0, "source": "reddit", "video_path": "v4.mp4", "title": "Low 2"},
        ],
    }

    res = schedule_node(state, schedule_config, mock_db)
    slots = res["final_schedule"]
    assert len(slots) == 4

    # The highest scoring videos should be placed in peak hours (12, 13, 18, 19)
    peak_slots = [s for s in slots if s["is_peak"]]
    assert len(peak_slots) >= 2
    peak_ids = {s["post_id"] for s in peak_slots}
    assert "vid_high_1" in peak_ids or "vid_high_2" in peak_ids


def test_schedule_surplus_handling(schedule_config, mock_db):
    # Pass 6 videos when target is 4
    state = {
        "target_count": 4,
        "target_date": "2026-11-15",
        "dry_run": True,
        "approved": [
            {
                "post_id": f"vid_{i}",
                "score": 8.0 + (i * 0.2),
                "source": "reddit",
                "video_path": f"raw_v{i}.mp4",
                "raw_video_path": f"raw_v{i}.mp4",
                "edited_video_path": f"edited_v{i}.mp4",
                "caption": f"Caption {i} 😂",
                "title": f"V {i}",
            }
            for i in range(6)
        ],
    }

    res = schedule_node(state, schedule_config, mock_db)
    slots = res["final_schedule"]
    # Only 4 scheduled
    assert len(slots) == 4
    # Surplus (2 items) saved to reserve pool
    assert mock_db.add_to_reserve_pool.call_count == 2
    # Verify raw video path was saved to reserve pool (NOT edited path)
    saved_item = mock_db.add_to_reserve_pool.call_args_list[0][0][0]
    assert "raw_v" in saved_item["video_path"]
    assert "edited_v" not in saved_item["video_path"]


def test_slot_count_follows_target(schedule_config, mock_db):
    from graph.nodes.schedule import select_target_hours

    # Target 4 should only select 4 hours, not 24
    hours_4 = select_target_hours(4, [12, 13, 18, 19])
    assert len(hours_4) == 4
    assert set(hours_4) == {12, 13, 18, 19}

    # Target 2 selects 2 hours
    hours_2 = select_target_hours(2, [12, 13, 18, 19])
    assert len(hours_2) == 2

    # Target 24 selects all 24 hours
    hours_24 = select_target_hours(24, [12, 13, 18, 19])
    assert len(hours_24) == 24

    # When 0 videos approved with target 4, schedule produces 0 slots and warns for 4 unfilled slots (not 24)
    state = {
        "target_count": 4,
        "target_date": "2026-11-15",
        "dry_run": True,
        "approved": [],
    }
    res = schedule_node(state, schedule_config, mock_db)
    assert len(res["final_schedule"]) == 0


def test_schedule_skips_missing_reserve_video(schedule_config, mock_db):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        valid_file = tmp_dir / "valid_reserve.mp4"
        valid_file.write_bytes(b"content")
        missing_file = tmp_dir / "deleted_reserve.mp4"

        state = {
            "target_count": 2,
            "target_date": "2026-11-15",
            "dry_run": True,
            "approved": [
                {
                    "post_id": "res_missing",
                    "score": 9.0,
                    "source": "reddit",
                    "video_path": str(missing_file),
                    "already_edited": True,
                    "title": "Missing Reserve",
                },
                {
                    "post_id": "res_valid",
                    "score": 8.5,
                    "source": "reddit",
                    "video_path": str(valid_file),
                    "already_edited": True,
                    "title": "Valid Reserve",
                },
            ],
        }

        res = schedule_node(state, schedule_config, mock_db)
        slots = res["final_schedule"]
        # Only 1 slot scheduled because missing reserve was skipped
        assert len(slots) == 1
        assert slots[0]["post_id"] == "res_valid"


def test_schedule_skips_missing_video_in_production(schedule_config, mock_db):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        valid_file = tmp_dir / "prod_valid.mp4"
        valid_file.write_bytes(b"content")
        missing_file = tmp_dir / "prod_missing.mp4"

        state = {
            "target_count": 2,
            "target_date": "2026-11-15",
            "dry_run": False,
            "approved": [
                {
                    "post_id": "vid_missing",
                    "score": 9.2,
                    "source": "reddit",
                    "edited_video_path": str(missing_file),
                    "title": "Missing File",
                },
                {
                    "post_id": "vid_valid",
                    "score": 8.1,
                    "source": "reddit",
                    "edited_video_path": str(valid_file),
                    "title": "Valid File",
                },
            ],
        }

        res = schedule_node(state, schedule_config, mock_db)
        slots = res["final_schedule"]
        assert len(slots) == 1
        assert slots[0]["post_id"] == "vid_valid"


def test_schedule_deduplicates_titles(schedule_config, mock_db):
    state = {
        "target_count": 2,
        "target_date": "2026-11-15",
        "dry_run": True,
        "approved": [
            {
                "post_id": "v1",
                "score": 9.0,
                "video_path": "mock1.mp4",
                "title": "Cat falls off sofa",
                "youtube_title": "Wait till you see what happens next! 😂 #shorts #funny",  # Generic
            },
            {
                "post_id": "v2",
                "score": 8.0,
                "video_path": "mock2.mp4",
                "title": "Dog steals pizza slice",
                "youtube_title": "Wait till you see what happens next! 😂 #shorts #funny",  # Duplicate generic
            },
        ],
    }
    res = schedule_node(state, schedule_config, mock_db)
    slots = res["final_schedule"]
    assert len(slots) == 2
    t1 = slots[0]["title"]
    t2 = slots[1]["title"]
    # Titles must be strictly unique and not generic
    assert t1 != t2
    assert "Wait till you see what happens next" not in t1
    assert "Wait till you see what happens next" not in t2



