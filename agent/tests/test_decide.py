import pytest
from unittest.mock import MagicMock
from graph.nodes.decide import decide_node, normalize_engagement


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def sample_config():
    return {
        "scoring": {
            "llm_weight": 0.70,
            "engagement_weight": 0.30,
            "auto_approve_threshold": 8.0,
            "auto_reject_threshold": 5.0,
            "human_review_min": 5.0,
            "human_review_max": 7.99,
        }
    }


def test_normalize_engagement():
    assert normalize_engagement(5) == 1.0
    assert 4.0 <= normalize_engagement(100) <= 6.0
    assert 7.0 <= normalize_engagement(1000) <= 8.5
    assert normalize_engagement(100000) == 10.0


def test_decide_auto_approve(sample_config, mock_db):
    state = {
        "candidates": [{
            "post_id": "vid_high",
            "title": "Hilarious dog prank",
            "llm_score": 9.0,
            "score": 5000,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "judge_reason": "Great timing",
        }],
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_date": "2026-11-12",
    }
    result = decide_node(state, sample_config, mock_db)
    assert len(result["approved"]) == 1
    assert result["approved"][0]["post_id"] == "vid_high"
    assert result["approved"][0]["score"] >= 8.0
    assert len(result["borderline"]) == 0
    assert len(result["rejected"]) == 0


def test_decide_auto_reject_low_score(sample_config, mock_db):
    state = {
        "candidates": [{
            "post_id": "vid_low",
            "title": "Boring unboxing",
            "llm_score": 3.0,
            "score": 20,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "judge_reason": "Not funny",
        }],
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_date": "2026-11-12",
    }
    result = decide_node(state, sample_config, mock_db)
    assert len(result["approved"]) == 0
    assert len(result["borderline"]) == 0
    assert len(result["rejected"]) == 1
    assert "Score too low" in result["rejected"][0]["reason"]


def test_decide_borderline_human_review(sample_config, mock_db):
    state = {
        "candidates": [{
            "post_id": "vid_mid",
            "title": "Mildly amusing reaction",
            "llm_score": 6.5,
            "score": 500,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "judge_reason": "Cute but not viral",
        }],
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_date": "2026-11-12",
    }
    result = decide_node(state, sample_config, mock_db)
    assert len(result["approved"]) == 0
    assert len(result["borderline"]) == 1
    assert result["borderline"][0]["post_id"] == "vid_mid"
    assert 5.0 <= result["borderline"][0]["score"] < 8.0


def test_decide_reject_watermark_and_safety(sample_config, mock_db):
    state = {
        "candidates": [
            {
                "post_id": "vid_watermark",
                "title": "Good joke but huge logo",
                "llm_score": 9.5,
                "score": 5000,
                "is_safe_for_ads": True,
                "has_visible_watermark": True,
                "is_english_or_no_speech": True,
                "judge_reason": "Competitor logo",
            },
            {
                "post_id": "vid_unsafe",
                "title": "Dangerous stunt accident",
                "llm_score": 9.0,
                "score": 5000,
                "is_safe_for_ads": False,
                "has_visible_watermark": False,
                "is_english_or_no_speech": True,
                "judge_reason": "Severe injury",
            },
        ],
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_date": "2026-11-12",
    }
    result = decide_node(state, sample_config, mock_db)
    assert len(result["approved"]) == 0
    assert len(result["borderline"]) == 0
    assert len(result["rejected"]) == 2

