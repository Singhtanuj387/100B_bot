import pytest
import sqlite3
from unittest.mock import MagicMock, patch
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

from curate import run_curation_graph
from graph.nodes.review import human_review_node


def test_run_curation_graph_handles_interrupt_and_resumes():
    mock_db = MagicMock()

    # Build a mini-graph with human_review_node
    workflow = StateGraph(dict)

    def _review(state):
        return human_review_node(state, mock_db)

    workflow.add_node("human_review", _review)
    workflow.add_edge(START, "human_review")
    workflow.add_edge("human_review", END)

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn=conn)
    app = workflow.compile(checkpointer=checkpointer)

    graph_config = {"configurable": {"thread_id": "test_thread"}, "recursion_limit": 100}

    initial_state = {
        "borderline": [
            {"post_id": "border_1", "score": 6.8, "title": "Funny dog"},
        ],
        "approved": [],
        "rejected": [],
        "dry_run": False,
        "target_date": "2026-11-12",
    }

    # Mock terminal interaction
    with patch("graph.nodes.review.sys_stdin_is_interactive", return_value=True), \
         patch("curate.ReviewUI.display_auto_approved_summary") as mock_disp, \
         patch("curate.ReviewUI.review_batch") as mock_review_batch:

        mock_review_batch.return_value = {
            "approved": [{"post_id": "border_1", "score": 6.8, "caption": "Good boy"}],
            "rejected": [],
        }

        final_state = run_curation_graph(app, initial_state, graph_config)

        # Verify ReviewUI was called outside the graph
        mock_review_batch.assert_called_once()
        # Verify graph received the decision via Command(resume=...)
        assert len(final_state["approved"]) == 1
        assert final_state["approved"][0]["post_id"] == "border_1"
        assert final_state["borderline"] == []
        # Verify DB recorded the decision
        mock_db.record_decision.assert_called_once()

    conn.close()

