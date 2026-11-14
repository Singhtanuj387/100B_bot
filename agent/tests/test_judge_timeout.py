import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from core.db import AgentDatabase
from graph.nodes.judge import judge_node


class TestJudgeTimeout(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_agent.db"
        self.db = AgentDatabase(self.db_path)
        self.mock_llm = MagicMock()
        self.mock_llm.total_cost_usd = 0.0
        self.mock_llm.call_count = 0

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("graph.nodes.judge.evaluate_single_candidate")
    def test_judge_total_timeout_raises_when_over_30_percent_fail(self, mock_eval):
        """When total timeout causes > 30% failure rate, judge stops with clear message."""
        def slow_eval(cand, *args, **kwargs):
            time.sleep(0.3)
            cand["llm_score"] = 8.5
            cand["is_english_or_no_speech"] = True
            cand["is_safe_for_ads"] = True
            cand["has_visible_watermark"] = False
            cand["is_broadcast_or_movie"] = False
            cand["judge_reason"] = "Slowly evaluated"
            return cand

        mock_eval.side_effect = slow_eval

        # 6 candidates with a strict total time limit of 0.25 seconds (all will timeout/fail)
        candidates = [{"post_id": f"c_{i}"} for i in range(6)]
        config = {
            "general": {"judge_workers": 2, "judge_total_timeout_seconds": 0.25},
            "cost_limits": {"judge_total_timeout_seconds": 0.25, "judge_max_failure_rate": 0.30},
            "vision": {"sample_frames": 3, "target_width": 360},
        }
        state = {"candidates": candidates}

        with self.assertRaises(RuntimeError) as ctx:
            judge_node(state, config, self.mock_llm, self.db)

        self.assertIn("Judge failure rate exceeded threshold", str(ctx.exception))
        self.assertIn("calls failed (max allowed: 30%)", str(ctx.exception))

    @patch("graph.nodes.judge.evaluate_single_candidate")
    def test_judge_timeout_marks_judge_failed_within_threshold(self, mock_eval):
        """When failures are <= 30%, failed candidates are marked judge_failed (never 5.0 neutral)."""
        # Fast evaluation for 4 clips, 1 failure/timeout
        def mixed_eval(cand, *args, **kwargs):
            p_id = cand.get("post_id")
            if p_id == "c_fail":
                cand["status"] = "judge_failed"
                cand["judge_failed"] = True
                cand["llm_score"] = None
                cand["judge_reason"] = "Simulated API error"
                return cand
            cand["llm_score"] = 8.5
            cand["is_english_or_no_speech"] = True
            cand["is_safe_for_ads"] = True
            cand["has_visible_watermark"] = False
            cand["is_broadcast_or_movie"] = False
            cand["judge_reason"] = "Good comedy"
            return cand

        mock_eval.side_effect = mixed_eval

        # 5 candidates: 1 failed (20% failure rate, <= 30% threshold)
        candidates = [{"post_id": f"c_{i}"} for i in range(4)] + [{"post_id": "c_fail"}]
        config = {
            "general": {"judge_workers": 2, "judge_total_timeout_seconds": 30.0},
            "cost_limits": {"judge_total_timeout_seconds": 30.0, "judge_max_failure_rate": 0.30},
            "vision": {"sample_frames": 3, "target_width": 360},
        }
        state = {"candidates": candidates}

        res = judge_node(state, config, self.mock_llm, self.db)
        judged = res["candidates"]
        self.assertEqual(len(judged), 5)

        failed_cand = next(c for c in judged if c.get("post_id") == "c_fail")
        self.assertTrue(failed_cand.get("judge_failed"))
        self.assertEqual(failed_cand.get("status"), "judge_failed")
        self.assertIsNone(failed_cand.get("llm_score"))
        self.assertNotEqual(failed_cand.get("llm_score"), 5.0)

    @patch("graph.nodes.judge.evaluate_single_candidate")
    def test_judge_timeout_scales_with_candidate_count(self, mock_eval):
        """Judge timeout scales dynamically based on candidate count (e.g. 60s per clip)."""
        mock_eval.side_effect = lambda cand, *args, **kwargs: {
            **cand,
            "llm_score": 8.0,
            "is_english_or_no_speech": True,
            "is_safe_for_ads": True,
            "has_visible_watermark": False,
            "is_broadcast_or_movie": False,
        }

        # 10 clips with 60s per clip -> 600s total timeout
        candidates = [{"post_id": f"c_{i}"} for i in range(10)]
        config = {
            "general": {"judge_workers": 2},
            "cost_limits": {
                "judge_timeout_seconds_per_clip": 60.0,
                "judge_total_timeout_min_seconds": 300.0,
                "judge_max_failure_rate": 0.30,
            },
            "vision": {"sample_frames": 3, "target_width": 360},
        }
        state = {"candidates": candidates}
        res = judge_node(state, config, self.mock_llm, self.db)
        self.assertEqual(len(res["candidates"]), 10)
        for c in res["candidates"]:
            self.assertEqual(c["llm_score"], 8.0)


if __name__ == "__main__":
    unittest.main()
