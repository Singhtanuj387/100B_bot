import pytest
from unittest.mock import MagicMock
from graph.nodes.backfill import backfill_node


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


def test_backfill_no_shortage(mock_db):
    state = {
        "target_count": 2,
        "approved": [{"post_id": "vid1"}, {"post_id": "vid2"}],
        "backfill_performed": False,
    }
    result = backfill_node(state, mock_db)
    assert result["backfill_performed"] is True
    assert result["trigger_extra_loop"] is False
    assert len(result["approved"]) == 2
    mock_db.fetch_reserve_pool_candidates.assert_not_called()


def test_backfill_sufficient_reserves(mock_db):
    mock_db.fetch_reserve_pool_candidates.return_value = [
        {"post_id": "res1", "title": "Reserve Clip 1"},
        {"post_id": "res2", "title": "Reserve Clip 2"},
    ]
    state = {
        "target_count": 3,
        "approved": [{"post_id": "vid1"}],
        "backfill_performed": False,
    }
    result = backfill_node(state, mock_db)
    assert result["backfill_performed"] is True
    assert result["trigger_extra_loop"] is False
    assert len(result["approved"]) == 3
    assert result["approved"][1]["status"] == "reserve_backfill"
    assert result["approved"][1]["is_reserve_backfill"] is True
    assert result["approved"][1]["already_edited"] is True
    mock_db.fetch_reserve_pool_candidates.assert_called_once_with(limit=2)
    mock_db.mark_reserve_used.assert_called_once_with(["res1", "res2"])


def test_backfill_insufficient_reserves_triggers_extra_loop(mock_db):
    mock_db.fetch_reserve_pool_candidates.return_value = [
        {"post_id": "res1", "title": "Reserve Clip 1"}
    ]
    state = {
        "target_count": 4,
        "approved": [{"post_id": "vid1"}],
        "backfill_performed": False,
    }
    result = backfill_node(state, mock_db)
    assert result["backfill_performed"] is True
    assert result["trigger_extra_loop"] is True
    assert len(result["approved"]) == 2
    mock_db.fetch_reserve_pool_candidates.assert_called_once_with(limit=3)
    mock_db.mark_reserve_used.assert_called_once_with(["res1"])


def test_backfill_already_performed_no_op(mock_db):
    state = {
        "target_count": 4,
        "approved": [{"post_id": "vid1"}],
        "backfill_performed": True,
    }
    result = backfill_node(state, mock_db)
    assert result["trigger_extra_loop"] is False
    mock_db.fetch_reserve_pool_candidates.assert_not_called()


def test_backfill_human_interrupted(mock_db):
    state = {
        "target_count": 4,
        "approved": [],
        "human_interrupted": True,
        "backfill_performed": False,
    }
    result = backfill_node(state, mock_db)
    assert result == {}
    mock_db.fetch_reserve_pool_candidates.assert_not_called()

