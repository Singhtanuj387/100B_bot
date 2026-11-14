import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from core.db import AgentDatabase
from graph.nodes.discover import discover_node
from llm.schemas import DiscoveryResult, DiscoveryItem


class TestDiscoveryNode(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_agent.db"
        self.db = AgentDatabase(self.db_path)
        self.mock_llm = MagicMock()
        self.mock_llm.total_cost_usd = 0.005
        self.mock_llm.call_count = 1
        self.config = {
            "allowed_sources": {
                "subreddits": ["funny", "Unexpected"],
            }
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("graph.nodes.discover.collect_fresh_context")
    @patch("graph.nodes.discover.verify_sources")
    def test_discover_node_runs_on_first_loop(self, mock_verify, mock_context):
        mock_context.return_value = {
            "fresh_titles": ["Test title 1", "Test title 2"],
            "active_sources": [],
            "top_reject_reasons": [],
            "already_tried_names": ["funny"],
        }
        mock_discovery_result = DiscoveryResult(
            subreddits=[
                DiscoveryItem(source_type="subreddit", name="ContagiousLaughter", listing_mode="top_month", reason="Great audio"),
            ],
            search_queries=[
                DiscoveryItem(source_type="search_query", name="funny animal fail", listing_mode="top_week", reason="Viral pet comedy"),
            ],
            explanation="Focusing on physical and animal humor.",
        )
        self.mock_llm.generate_structured.return_value = mock_discovery_result
        mock_verify.return_value = (
            [
                {"source_type": "subreddit", "name": "ContagiousLaughter", "listing_mode": "top_month", "status": "trial", "added_by": "llm"},
            ],
            [],
        )

        state = {
            "target_date": "2026-11-15",
            "discovered": False,
            "dry_run": True,
        }

        res = discover_node(state, self.config, self.mock_llm, self.db)
        self.assertTrue(res.get("discovered"))
        self.mock_llm.generate_structured.assert_called_once()
        mock_verify.assert_called_once()

        # Verify added to database
        with self.db._get_connection() as conn:
            cur = conn.execute("SELECT * FROM sources WHERE name = 'ContagiousLaughter'")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "trial")

    def test_discover_node_skips_when_already_discovered(self):
        state = {
            "target_date": "2026-11-15",
            "discovered": True,
        }
        res = discover_node(state, self.config, self.mock_llm, self.db)
        # Should return empty dict and never invoke LLM
        self.assertEqual(res, {})
        self.mock_llm.generate_structured.assert_not_called()


if __name__ == "__main__":
    unittest.main()
