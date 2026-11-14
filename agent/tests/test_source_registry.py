import tempfile
from pathlib import Path
import unittest
from core.db import AgentDatabase


class TestSourceRegistry(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_agent.db"
        self.db = AgentDatabase(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_composite_unique_key(self):
        # (subreddit, funny, hot) and (subreddit, funny, top_year) should both be allowed
        self.db.seed_default_sources(["funny"])
        with self.db._get_connection() as conn:
            cur = conn.execute("SELECT * FROM sources WHERE name = 'funny'")
            rows = cur.fetchall()
            # Expect 2 rows: 'hot' and 'top_month'
            self.assertEqual(len(rows), 2)
            modes = {r["listing_mode"] for r in rows}
            self.assertEqual(modes, {"hot", "top_month"})

    def test_selection_80_20_split(self):
        self.db.seed_default_sources()
        # Add 2 trial sources
        trials = [
            {"source_type": "subreddit", "name": "trial_sub1", "listing_mode": "hot"},
            {"source_type": "subreddit", "name": "trial_sub2", "listing_mode": "hot"},
        ]
        added = self.db.add_trial_sources(trials, run_date="2026-11-14")
        self.assertEqual(added, 2)

        selection = self.db.get_sources_for_selection(total_budget=5, active_ratio=0.8, trial_ratio=0.2)
        self.assertEqual(len(selection), 5)
        # Should contain at least 1 trial source
        statuses = [s["status"] for s in selection]
        self.assertIn("active", statuses)
        self.assertIn("trial", statuses)

    def test_daily_trial_cap_of_5(self):
        trials = [
            {"source_type": "subreddit", "name": f"trial_{i}", "listing_mode": "hot"}
            for i in range(10)
        ]
        added = self.db.add_trial_sources(trials, run_date="2026-11-14", max_daily_trials=5)
        self.assertEqual(added, 5)

        # Trying to add more on the same day adds 0
        added_more = self.db.add_trial_sources(trials[5:], run_date="2026-11-14", max_daily_trials=5)
        self.assertEqual(added_more, 0)

    def test_promote_trial_source(self):
        self.db.add_trial_sources(
            [{"source_type": "subreddit", "name": "rising_star", "listing_mode": "hot"}],
            run_date="2026-11-14",
        )
        # Record 10 downloads, 6 approved (smoothed yield = (6+1)/(10+3) = 7/13 ~ 0.538 >= 0.30)
        self.db.update_source_metrics(
            source_type="subreddit",
            name="rising_star",
            listing_mode="hot",
            found=15,
            downloaded=10,
            approved=6,
            rejected=4,
        )
        with self.db._get_connection() as conn:
            cur = conn.execute("SELECT status, yield_rate FROM sources WHERE name = 'rising_star'")
            row = cur.fetchone()
            self.assertEqual(row["status"], "active")
            self.assertGreaterEqual(row["yield_rate"], 0.30)

    def test_pause_low_yield_source(self):
        self.db.add_trial_sources(
            [{"source_type": "subreddit", "name": "bad_sub", "listing_mode": "hot"}],
            run_date="2026-11-14",
        )
        # Record 10 downloads, 0 approved (smoothed yield = (0+1)/(10+3) = 1/13 ~ 0.076 < 0.15)
        self.db.update_source_metrics(
            source_type="subreddit",
            name="bad_sub",
            listing_mode="hot",
            found=12,
            downloaded=10,
            approved=0,
            rejected=10,
        )
        with self.db._get_connection() as conn:
            cur = conn.execute("SELECT status, yield_rate FROM sources WHERE name = 'bad_sub'")
            row = cur.fetchone()
            self.assertEqual(row["status"], "paused")

    def test_archive_exhaustion(self):
        self.db.update_source_metrics(
            source_type="subreddit",
            name="archive_sub",
            listing_mode="top_year",
            found=0,
            downloaded=0,
            approved=0,
            rejected=0,
        )
        self.db.update_source_metrics(
            source_type="subreddit",
            name="archive_sub",
            listing_mode="top_year",
            found=0,
            downloaded=0,
            approved=0,
            rejected=0,
        )
        self.db.update_source_metrics(
            source_type="subreddit",
            name="archive_sub",
            listing_mode="top_year",
            found=0,
            downloaded=0,
            approved=0,
            rejected=0,
        )
        with self.db._get_connection() as conn:
            cur = conn.execute("SELECT status, consecutive_empty_runs FROM sources WHERE name = 'archive_sub'")
            row = cur.fetchone()
            self.assertEqual(row["status"], "exhausted")
            self.assertEqual(row["consecutive_empty_runs"], 3)


if __name__ == "__main__":
    unittest.main()
