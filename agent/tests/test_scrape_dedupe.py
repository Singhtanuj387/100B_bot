import pytest
from unittest.mock import MagicMock
from graph.nodes.scrape import scrape_node


def test_scrape_node_filters_borderline_candidates():
    mock_scraper_tool = MagicMock()
    # Mock raw videos on disk
    mock_scraper_tool.get_downloaded_videos.return_value = [
        {"post_id": "vid_borderline", "file_hash": "hash_1"},
        {"post_id": "vid_approved", "file_hash": "hash_2"},
        {"post_id": "vid_rejected", "file_hash": "hash_3"},
        {"post_id": "vid_fresh", "file_hash": "hash_4"},
    ]

    state = {
        "target_date": "2026-11-12",
        "plan": {"target_to_fetch": 1},
        "dry_run": True,
        "candidates": [],
        "approved": [{"post_id": "vid_approved", "file_hash": "hash_2"}],
        "borderline": [{"post_id": "vid_borderline", "file_hash": "hash_1"}],
        "rejected": [{"post_id": "vid_rejected", "file_hash": "hash_3"}],
    }

    result = scrape_node(state, mock_scraper_tool)
    new_cands = result["candidates"]

    # Only vid_fresh should be acquired
    assert len(new_cands) == 1
    assert new_cands[0]["post_id"] == "vid_fresh"

