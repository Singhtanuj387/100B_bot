import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from core.db import AgentDatabase
from graph.nodes.judge import evaluate_single_candidate, judge_node
from graph.nodes.decide import decide_node
from graph.nodes.update_registry import update_registry_node
from graph.nodes.scrape import download_node


class TestJudgeFailureHandling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_agent.db"
        self.db = AgentDatabase(self.db_path)
        self.mock_llm = MagicMock()
        self.mock_llm.total_cost_usd = 0.0
        self.mock_llm.call_count = 0

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_evaluate_single_candidate_error_marks_judge_failed(self):
        """When LLM call throws, candidate is marked judge_failed with llm_score=None (not 5.0)."""
        cand = {"post_id": "test_err_vid", "title": "Funny Dog", "file_hash": "hash_123"}
        self.mock_llm.call_structured.side_effect = RuntimeError("API socket connection timed out")

        res = evaluate_single_candidate(
            cand=cand,
            sys_prompt="system",
            llm_client=self.mock_llm,
            db=self.db,
            num_frames=3,
            target_width=360,
        )

        self.assertEqual(res.get("status"), "judge_failed")
        self.assertTrue(res.get("judge_failed"))
        self.assertIsNone(res.get("llm_score"))
        self.assertNotEqual(res.get("llm_score"), 5.0)
        self.assertIn("API socket connection timed out", res.get("judge_reason", ""))

        # Verify it was NOT saved to cache
        cached = self.db.get_cached_judge("hash_123")
        self.assertIsNone(cached)

    def test_decide_node_skips_judge_failed_in_decisions_and_routes_to_retry(self):
        """decide_node must not put judge_failed into approved, borderline, or rejected."""
        mock_db = MagicMock()
        config = {
            "scoring": {
                "llm_weight": 0.70,
                "engagement_weight": 0.30,
                "auto_approve_threshold": 8.0,
                "auto_reject_threshold": 5.0,
            }
        }
        state = {
            "candidates": [
                {
                    "post_id": "good_clip",
                    "title": "A Great Clip",
                    "llm_score": 9.0,
                    "score": 5000,
                    "is_safe_for_ads": True,
                    "has_visible_watermark": False,
                    "is_english_or_no_speech": True,
                    "judge_reason": "Very funny",
                },
                {
                    "post_id": "failed_clip",
                    "title": "Failed API Clip",
                    "status": "judge_failed",
                    "judge_failed": True,
                    "llm_score": None,
                    "judge_reason": "API error 503",
                },
            ],
            "approved": [],
            "borderline": [],
            "rejected": [],
            "target_date": "2026-11-15",
        }

        result = decide_node(state, config, mock_db)

        # good_clip must be auto-approved
        self.assertEqual(len(result["approved"]), 1)
        self.assertEqual(result["approved"][0]["post_id"], "good_clip")

        # failed_clip must NOT be in approved, borderline, or rejected
        self.assertEqual(len(result["borderline"]), 0)
        self.assertEqual(len(result["rejected"]), 0)

        # failed_clip must be in judge_failed
        self.assertEqual(len(result["judge_failed"]), 1)
        self.assertEqual(result["judge_failed"][0]["post_id"], "failed_clip")

        # db.record_decision must only be called once (for good_clip), NEVER for failed_clip
        mock_db.record_decision.assert_called_once()
        args, _ = mock_db.record_decision.call_args
        self.assertEqual(args[0], "good_clip")

        # mock_db.record_judge_failed must be called for failed_clip
        mock_db.record_judge_failed.assert_called_once()
        failed_args, _ = mock_db.record_judge_failed.call_args
        self.assertEqual(failed_args[0]["post_id"], "failed_clip")

    def test_update_registry_skips_judge_failed(self):
        """Registry update must not count judge_failed clips in source metrics."""
        mock_db = MagicMock()
        # Even if a judge_failed clip were in the state, update_registry ignores it
        state = {
            "approved": [
                {
                    "post_id": "app_1",
                    "meta": {"source_type": "subreddit", "source_name": "funny", "listing_mode": "hot"},
                }
            ],
            "rejected": [
                {
                    "post_id": "rej_1",
                    "reason": "Duration too long",
                    "meta": {"source_type": "subreddit", "source_name": "funny", "listing_mode": "hot"},
                },
                {
                    "post_id": "failed_1",
                    "status": "judge_failed",
                    "judge_failed": True,
                    "reason": "Judge call error",
                    "meta": {"source_type": "subreddit", "source_name": "funny", "listing_mode": "hot"},
                },
            ],
            "judge_failed": [
                {
                    "post_id": "failed_2",
                    "status": "judge_failed",
                    "meta": {"source_type": "subreddit", "source_name": "funny", "listing_mode": "hot"},
                }
            ],
            "target_date": "2026-11-15",
        }

        update_registry_node(state, mock_db)

        # Verify only 1 approved and 1 rejected were counted (the judge_failed was skipped)
        mock_db.update_source_metrics.assert_called_once()
        _, kwargs = mock_db.update_source_metrics.call_args
        self.assertEqual(kwargs["approved"], 1)
        self.assertEqual(kwargs["rejected"], 1)  # failed_1 was skipped!

    def test_update_registry_skips_reserve_backfill_clips(self):
        """Backfilled reserve clips must NOT be counted in the registry and must not inflate 'funny'."""
        mock_db = MagicMock()
        state = {
            "approved": [
                {
                    "post_id": "fresh_approved",
                    "meta": {"source_type": "subreddit", "source_name": "Unexpected", "listing_mode": "hot"},
                },
                {
                    "post_id": "backfilled_clip_1",
                    "status": "reserve_backfill",
                    "is_reserve_backfill": True,
                    # No meta or source -> would default to 'funny' if not skipped
                },
                {
                    "post_id": "backfilled_clip_2",
                    "status": "reserve_backfill",
                    # No meta or source -> would default to 'funny' if not skipped
                },
            ],
            "rejected": [],
            "target_date": "2026-11-15",
        }

        update_registry_node(state, mock_db)

        # Only the 1 fresh approved clip from r/Unexpected must be updated in registry
        mock_db.update_source_metrics.assert_called_once()
        _, kwargs = mock_db.update_source_metrics.call_args
        self.assertEqual(kwargs["name"], "Unexpected")
        self.assertEqual(kwargs["approved"], 1)
        self.assertEqual(kwargs["downloaded"], 1)

    def test_judge_failure_rate_over_30_percent_stops_with_clear_message(self):
        """When > 30% of judge calls fail, judge_node raises RuntimeError with clear error."""
        # 4 out of 10 fail (40% > 30%)
        candidates = [{"post_id": f"clip_{i}"} for i in range(10)]

        def eval_with_failures(cand, *args, **kwargs):
            p_id = cand["post_id"]
            if p_id in ("clip_0", "clip_1", "clip_2", "clip_3"):
                cand["status"] = "judge_failed"
                cand["judge_failed"] = True
                cand["llm_score"] = None
                cand["judge_reason"] = "Gemini 429 quota exhausted"
                return cand
            cand["llm_score"] = 8.0
            cand["is_english_or_no_speech"] = True
            cand["is_safe_for_ads"] = True
            cand["has_visible_watermark"] = False
            cand["is_broadcast_or_movie"] = False
            cand["judge_reason"] = "Hilarious"
            return cand

        with patch("graph.nodes.judge.evaluate_single_candidate", side_effect=eval_with_failures):
            config = {
                "general": {"judge_workers": 2},
                "cost_limits": {"judge_total_timeout_seconds": 60, "judge_max_failure_rate": 0.30},
                "vision": {"sample_frames": 3, "target_width": 360},
            }
            state = {"candidates": candidates}

            with self.assertRaises(RuntimeError) as ctx:
                judge_node(state, config, self.mock_llm, self.db)

            msg = str(ctx.exception)
            self.assertIn("Judge failure rate exceeded threshold", msg)
            self.assertIn("4/10", msg)
            self.assertIn("40.0%", msg)
            self.assertIn("Stopping run", msg)

    def test_retry_in_next_run(self):
        """Failed candidate recorded in database is re-injected in next run by download_node."""
        # Create a mock video file in temp dir
        test_video = Path(self.temp_dir.name) / "retry_video.mp4"
        test_video.write_bytes(b"dummy video data")

        cand_failed = {
            "post_id": "retry_post_1",
            "source": "funny",
            "title": "Candidate for retry",
            "video_path": str(test_video),
            "judge_reason": "Network socket timeout",
        }

        # 1. Record failed candidate in db
        self.db.record_judge_failed(cand_failed)

        # 2. In next run, download_node retrieves it
        mock_scraper_tool = MagicMock()
        mock_scraper_tool.get_downloaded_videos.return_value = []
        state = {
            "target_date": "2026-11-16",
            "plan": {"target_to_fetch": 5},
            "dry_run": False,
        }

        res = download_node(state, mock_scraper_tool, db=self.db)
        candidates = res["candidates"]

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["post_id"], "retry_post_1")
        self.assertTrue(candidates[0].get("is_retry"))
        self.assertNotIn("judge_failed", candidates[0])

        # 3. Once candidate is decided, clear_judge_retry removes it
        self.db.clear_judge_retry("retry_post_1")
        pending_after = self.db.pop_pending_judge_retries()
        self.assertEqual(len(pending_after), 0)


if __name__ == "__main__":
    unittest.main()
