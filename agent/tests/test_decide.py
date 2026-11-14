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
            "min_publish_score": 8.0,
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


def test_decide_rank_based_top_6_selection(mock_db):
    """Test rank-based daily top-6 auto-approval and borderline routing."""
    config = {
        "scoring": {
            "llm_weight": 0.70,
            "engagement_weight": 0.30,
            "min_publish_score": 6.5,
            "auto_reject_threshold": 5.0,
        }
    }
    # 10 candidates with varying scores
    candidates = []
    for i in range(10):
        candidates.append({
            "post_id": f"clip_{i+1}",
            "title": f"Clip {i+1}",
            "llm_score": 9.5 - (i * 0.5),  # 9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5, 6.0, 5.5, 5.0
            "score": 1000 - (i * 50),
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "judge_reason": "Test clip",
            "created_utc": 1000.0 + i,
        })

    state = {
        "candidates": candidates,
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_count": 6,
        "target_date": "2026-11-15",
    }
    result = decide_node(state, config, mock_db)

    # Exactly 6 top ranked clips should be auto-approved
    assert len(result["approved"]) == 6
    approved_ids = [c["post_id"] for c in result["approved"]]
    assert approved_ids == ["clip_1", "clip_2", "clip_3", "clip_4", "clip_5", "clip_6"]
    for c in result["approved"]:
        assert c["score"] >= 6.5

    # Clips 7, 8, 9, 10 have scores >= 5.0 but lower than lowest chosen score -> Borderline pool
    assert len(result["borderline"]) == 4
    borderline_ids = [c["post_id"] for c in result["borderline"]]
    assert "clip_7" in borderline_ids
    assert "clip_8" in borderline_ids


def test_decide_ties_broken_by_engagement_and_recency(mock_db):
    """Test that rank ties with equal hybrid scores break by engagement then recency."""
    config = {
        "scoring": {
            "llm_weight": 0.70,
            "engagement_weight": 0.30,
            "min_publish_score": 6.5,
            "auto_reject_threshold": 5.0,
        }
    }
    # Candidate A and B have exact same LLM score and same normalized engagement,
    # but B has higher raw engagement score
    cand_a = {
        "post_id": "cand_a",
        "title": "Clip A",
        "llm_score": 8.0,
        "score": 500,
        "is_safe_for_ads": True,
        "has_visible_watermark": False,
        "is_english_or_no_speech": True,
        "judge_reason": "Equal score",
        "created_utc": 100.0,
    }
    cand_b = {
        "post_id": "cand_b",
        "title": "Clip B",
        "llm_score": 8.0,
        "score": 900,  # higher engagement
        "is_safe_for_ads": True,
        "has_visible_watermark": False,
        "is_english_or_no_speech": True,
        "judge_reason": "Equal score",
        "created_utc": 100.0,
    }
    cand_c = {
        "post_id": "cand_c",
        "title": "Clip C",
        "llm_score": 8.0,
        "score": 900,
        "is_safe_for_ads": True,
        "has_visible_watermark": False,
        "is_english_or_no_speech": True,
        "judge_reason": "Equal score",
        "created_utc": 200.0,  # more recent than B
    }

    state = {
        "candidates": [cand_a, cand_b, cand_c],
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_count": 2,
        "target_date": "2026-11-15",
    }
    result = decide_node(state, config, mock_db)
    # Target 2: cand_c (most recent of 900) and cand_b (900) should be approved before cand_a (500)
    assert len(result["approved"]) == 2
    approved_ids = [c["post_id"] for c in result["approved"]]
    assert approved_ids[0] == "cand_c"
    assert approved_ids[1] == "cand_b"
    assert result["borderline"][0]["post_id"] == "cand_a"


def test_decide_fewer_than_6_candidates_keeps_quality_bar(mock_db):
    """When fewer than 6 good clips exist, do not lower quality bar silently."""
    config = {
        "scoring": {
            "llm_weight": 0.70,
            "engagement_weight": 0.30,
            "min_publish_score": 6.5,
            "auto_reject_threshold": 5.0,
        }
    }
    candidates = [
        {"post_id": "g1", "title": "Good 1", "llm_score": 8.5, "score": 1000, "is_safe_for_ads": True, "has_visible_watermark": False, "is_english_or_no_speech": True, "created_utc": 1.0},
        {"post_id": "g2", "title": "Good 2", "llm_score": 8.0, "score": 800, "is_safe_for_ads": True, "has_visible_watermark": False, "is_english_or_no_speech": True, "created_utc": 2.0},
        {"post_id": "b1", "title": "Borderline 1", "llm_score": 5.5, "score": 50, "is_safe_for_ads": True, "has_visible_watermark": False, "is_english_or_no_speech": True, "created_utc": 3.0},
        {"post_id": "r1", "title": "Junk 1", "llm_score": 2.0, "score": 5, "is_safe_for_ads": True, "has_visible_watermark": False, "is_english_or_no_speech": True, "created_utc": 4.0},
    ]
    state = {
        "candidates": candidates,
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_count": 6,
        "target_date": "2026-11-15",
    }
    result = decide_node(state, config, mock_db)
    # Only 2 clips met min_publish_score 6.5; only 2 approved!
    assert len(result["approved"]) == 2
    assert {c["post_id"] for c in result["approved"]} == {"g1", "g2"}
    assert len(result["borderline"]) == 1
    assert result["borderline"][0]["post_id"] == "b1"
    assert len(result["rejected"]) == 1
    assert result["rejected"][0]["post_id"] == "r1"


def test_borderline_policy_approve_top_and_reject(mock_db):
    """Test human_review_node with approve_top and reject policies."""
    from graph.nodes.review import human_review_node

    borderline_clips = [
        {"post_id": "b_high", "title": "High Borderline", "score": 7.2},
        {"post_id": "b_mid", "title": "Mid Borderline", "score": 6.8},
        {"post_id": "b_low", "title": "Low Borderline", "score": 5.4},
    ]

    # Test approve_top: target 4, 2 already approved -> fill 2 slots from borderline >= 6.5
    state_approve_top = {
        "borderline": borderline_clips,
        "approved": [{"post_id": "a1"}, {"post_id": "a2"}],
        "rejected": [],
        "target_count": 4,
        "min_publish_score": 6.5,
        "borderline_policy": "approve_top",
        "no_review": True,
        "target_date": "2026-11-15",
    }
    res_approve = human_review_node(state_approve_top, mock_db)
    assert len(res_approve["approved"]) == 4
    approved_ids = [c["post_id"] for c in res_approve["approved"]]
    assert "b_high" in approved_ids
    assert "b_mid" in approved_ids
    # b_low (score 5.4 < 6.5) must NOT be approved
    assert "b_low" not in approved_ids
    assert len(res_approve["rejected"]) == 1
    assert res_approve["rejected"][0]["post_id"] == "b_low"

    # Test reject policy: all borderline dropped
    state_reject = {
        "borderline": borderline_clips,
        "approved": [{"post_id": "a1"}],
        "rejected": [],
        "target_count": 4,
        "borderline_policy": "reject",
        "no_review": True,
        "target_date": "2026-11-15",
    }
    res_reject = human_review_node(state_reject, mock_db)
    assert len(res_reject["approved"]) == 1
    assert len(res_reject["rejected"]) == 3


def test_decide_short_clip_duration_threshold_and_scores(mock_db):
    """
    Clips under short_clip_duration_threshold (8.0s) require higher score (>= 7.5) to auto-approve.
    Clips >= 8.0s only require min_publish_score (6.5).
    """
    config = {
        "scoring": {
            "llm_weight": 0.70,
            "engagement_weight": 0.30,
            "min_publish_score": 6.5,
            "short_clip_duration_threshold": 8.0,
            "short_clip_min_score": 7.5,
            "auto_reject_threshold": 5.0,
        }
    }
    candidates = [
        # Short clip (< 8.0s) with high score >= 7.5 -> Auto-approved
        {
            "post_id": "short_high",
            "title": "Short High Score",
            "llm_score": 8.5,
            "score": 1000,
            "duration": 6.5,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "created_utc": 10.0,
        },
        # Short clip (< 8.0s) with score 6.8 (>= 6.5 but < 7.5) -> Routed to borderline
        {
            "post_id": "short_borderline",
            "title": "Short Moderate Score",
            "llm_score": 6.8,
            "score": 500,
            "duration": 7.0,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "created_utc": 9.0,
        },
        # Regular clip (>= 8.0s) with score 6.8 (>= 6.5) -> Auto-approved
        {
            "post_id": "normal_ok",
            "title": "Normal Duration Moderate Score",
            "llm_score": 6.8,
            "score": 500,
            "duration": 15.0,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_english_or_no_speech": True,
            "created_utc": 8.0,
        },
    ]

    state = {
        "candidates": candidates,
        "approved": [],
        "borderline": [],
        "rejected": [],
        "target_count": 3,
        "target_date": "2026-11-15",
    }
    result = decide_node(state, config, mock_db)

    approved_ids = [c["post_id"] for c in result["approved"]]
    borderline_ids = [c["post_id"] for c in result["borderline"]]

    assert "short_high" in approved_ids
    assert "normal_ok" in approved_ids
    assert "short_borderline" in borderline_ids
    assert len(result["approved"]) == 2
    assert len(result["borderline"]) == 1


def test_borderline_policy_approve_top_short_clip_threshold(mock_db):
    """
    Under approve_top policy in human_review_node:
    Short clips (< 8.0s) must meet short_clip_min_score (7.5) to be promoted,
    while normal clips only require min_publish_score (6.5).
    """
    from graph.nodes.review import human_review_node

    config = {
        "scoring": {
            "min_publish_score": 6.5,
            "short_clip_duration_threshold": 8.0,
            "short_clip_min_score": 7.5,
        }
    }
    borderline_clips = [
        # Short clip with score >= 7.5 -> Approved
        {"post_id": "b_short_high", "title": "Short Top", "score": 7.8, "duration": 6.5},
        # Normal clip with score 6.8 (>= 6.5) -> Approved
        {"post_id": "b_normal_ok", "title": "Normal Ok", "score": 6.8, "duration": 20.0},
        # Short clip with score 6.9 (< 7.5) -> Rejected
        {"post_id": "b_short_low", "title": "Short Below Bar", "score": 6.9, "duration": 7.2},
    ]

    state = {
        "borderline": borderline_clips,
        "approved": [],
        "rejected": [],
        "target_count": 2,
        "borderline_policy": "approve_top",
        "no_review": True,
        "target_date": "2026-11-15",
    }

    result = human_review_node(state, mock_db, config=config)
    approved_ids = [c["post_id"] for c in result["approved"]]
    rejected_ids = [c["post_id"] for c in result["rejected"]]

    assert "b_short_high" in approved_ids
    assert "b_normal_ok" in approved_ids
    assert "b_short_low" in rejected_ids
    assert len(result["approved"]) == 2
    assert len(result["rejected"]) == 1



