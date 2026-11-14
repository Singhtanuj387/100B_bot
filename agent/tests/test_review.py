import pytest
from unittest.mock import MagicMock, patch
from graph.nodes.review import human_review_node


@pytest.fixture
def mock_db():
    return MagicMock()


def test_human_review_empty_borderline(mock_db):
    state = {
        "borderline": [],
        "approved": [{"post_id": "vid1"}],
        "rejected": [],
    }
    result = human_review_node(state, mock_db)
    assert result["borderline"] == []
    assert len(result["approved"]) == 1
    mock_db.record_decision.assert_not_called()


def test_human_review_non_interactive_dry_run(mock_db):
    state = {
        "borderline": [
            {"post_id": "vid_high", "score": 7.0},
            {"post_id": "vid_low", "score": 5.5},
        ],
        "approved": [],
        "rejected": [],
        "dry_run": True,
    }
    with patch("graph.nodes.review.sys_stdin_is_interactive", return_value=False):
        result = human_review_node(state, mock_db)
        assert len(result["approved"]) == 1
        assert result["approved"][0]["post_id"] == "vid_high"
        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["post_id"] == "vid_low"
        assert result["borderline"] == []


def test_human_review_resumed_from_interrupt(mock_db):
    state = {
        "borderline": [{"post_id": "vid_b", "score": 6.5}],
        "approved": [{"post_id": "vid_a"}],
        "rejected": [],
        "dry_run": False,
        "target_date": "2026-11-12",
    }
    # Simulate LangGraph resume where interrupt() returns user decisions
    with patch("graph.nodes.review.sys_stdin_is_interactive", return_value=True), \
         patch("graph.nodes.review.interrupt", return_value={"approved": [{"post_id": "vid_b", "caption": "Funny"}], "rejected": []}):
        result = human_review_node(state, mock_db)
        assert len(result["approved"]) == 2
        assert result["approved"][1]["post_id"] == "vid_b"
        assert result["borderline"] == []
        mock_db.record_decision.assert_called_once()

