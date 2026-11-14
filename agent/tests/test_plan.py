import pytest
from unittest.mock import MagicMock
from graph.nodes.plan import plan_node
from llm.schemas import PlanResult


def test_plan_node_loop_1_reddit_only():
    config = {
        "allowed_sources": {
            "subreddits": ["funny", "Unexpected", "MadeMeSmile"],
            "twitter_accounts": ["LaughsDaily"],
            "twitter_enabled": False,
        }
    }
    state = {
        "loop_count": 0,
        "target_count": 24,
        "spare_buffer": 4,
        "approved": [],
        "borderline": [],
        "sources_tried": [],
    }
    mock_llm = MagicMock()
    result = plan_node(state, config, mock_llm)

    # In loop 1, LLM is not called; Twitter is skipped
    mock_llm.call_structured.assert_not_called()
    assert result["loop_count"] == 1
    assert result["plan"]["twitter_accounts"] == []
    assert len(result["plan"]["subreddits"]) > 0


def test_plan_node_loop_2_skips_twitter_when_disabled():
    config = {
        "allowed_sources": {
            "subreddits": ["funny", "Unexpected", "AnimalsBeingDerps"],
            "twitter_accounts": ["LaughsDaily", "DailyViralVids"],
            "twitter_enabled": False,
        }
    }
    state = {
        "loop_count": 1,
        "target_count": 24,
        "spare_buffer": 4,
        "approved": [],
        "borderline": [],
        "rejected": [{"reason": "low_resolution"}],
        "sources_tried": ["funny"],
    }
    mock_llm = MagicMock()
    # Even if LLM returns a twitter account in schema
    mock_llm.call_structured.return_value = PlanResult(
        subreddits=["Unexpected", "AnimalsBeingDerps"],
        twitter_accounts=["LaughsDaily"],
        strategy_reason="Pivot to animals and unexpected",
        proposed_new_sources=[],
    )

    result = plan_node(state, config, mock_llm)

    assert result["loop_count"] == 2
    # Because twitter_enabled is False, twitter_accounts must be filtered out
    assert result["plan"]["twitter_accounts"] == []
    assert result["plan"]["subreddits"] == ["Unexpected", "AnimalsBeingDerps"]


def test_plan_node_loop_2_fallback_skips_twitter_when_disabled():
    config = {
        "allowed_sources": {
            "subreddits": ["funny", "Unexpected"],
            "twitter_accounts": ["LaughsDaily"],
            "twitter_enabled": False,
        }
    }
    state = {
        "loop_count": 1,
        "target_count": 24,
        "spare_buffer": 4,
        "approved": [],
        "borderline": [],
        "rejected": [],
        "sources_tried": ["funny"],
    }
    mock_llm = MagicMock()
    mock_llm.call_structured.side_effect = RuntimeError("LLM failure")

    result = plan_node(state, config, mock_llm)

    assert result["loop_count"] == 2
    assert result["plan"]["twitter_accounts"] == []
    assert "Unexpected" in result["plan"]["subreddits"]

