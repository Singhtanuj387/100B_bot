import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from llm.title_prescreen import prescreen_titles
from llm.schemas import TitlePrescreenResult, TitlePrescreenDecision


class TestTitlePrescreen(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.drops_log = Path(self.temp_dir.name) / "drops.jsonl"
        self.mock_llm = MagicMock()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prescreen_keeps_vague_and_drops_non_comedy(self):
        candidates = [
            {"post_id": "c1", "title": "Maybe Maybe Maybe", "source": "reddit", "extra": {"subreddit": "funny"}},
            {"post_id": "c2", "title": "Political debate on inflation and tax policies", "source": "reddit", "extra": {"subreddit": "news"}},
            {"post_id": "c3", "title": "Cat attempts dramatic leap but slips on rug", "source": "reddit", "extra": {"subreddit": "Unexpected"}},
        ]

        mock_result = TitlePrescreenResult(
            decisions=[
                TitlePrescreenDecision(post_id="c1", keep=True, drop_reason=None),
                TitlePrescreenDecision(post_id="c2", keep=False, drop_reason="news_politics"),
                TitlePrescreenDecision(post_id="c3", keep=True, drop_reason=None),
            ]
        )
        self.mock_llm.generate_structured.return_value = mock_result

        kept, dropped = prescreen_titles(
            candidates,
            self.mock_llm,
            drops_log_path=self.drops_log,
            batch_size=10,
        )

        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(kept[0]["post_id"], "c1")
        self.assertEqual(kept[1]["post_id"], "c3")
        self.assertEqual(dropped[0]["post_id"], "c2")
        self.assertEqual(dropped[0]["drop_reason"], "news_politics")

        # Verify drops log was written
        self.assertTrue(self.drops_log.is_file())
        lines = self.drops_log.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 1)
        self.assertIn("news_politics", lines[0])

    def test_prescreen_fallback_on_llm_exception(self):
        candidates = [
            {"post_id": "c1", "title": "Just a normal clip", "source": "reddit"},
            {"post_id": "c2", "title": "Another clip", "source": "reddit"},
        ]
        self.mock_llm.generate_structured.side_effect = RuntimeError("API connection timeout")

        kept, dropped = prescreen_titles(
            candidates,
            self.mock_llm,
            drops_log_path=self.drops_log,
            batch_size=10,
        )

        # Fail-safe: all candidates kept, zero dropped, run does not crash
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 0)


if __name__ == "__main__":
    unittest.main()
