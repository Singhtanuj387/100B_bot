import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
from sources.twitter import (
    TwitterSource,
    parse_metric_text,
    parse_netscape_cookies,
    add_cookies_to_playwright,
)
from sources.base import VideoCandidate


class TestTwitterSource(unittest.TestCase):
    def setUp(self):
        self.config = {
            "twitter": {
                "accounts": ["test_acc"],
                "search_queries": ["funny video filter:videos"],
                "cookies_file": "test_cookies.txt",
                "use_playwright_fallback": True,
                "headless": True,
                "rate_limit_delay": [0.0, 0.0],
            }
        }

    def test_parse_metric_text(self):
        self.assertEqual(parse_metric_text("14.2K"), 14200)
        self.assertEqual(parse_metric_text("1.5M"), 1500000)
        self.assertEqual(parse_metric_text("1,420 Likes. Like"), 1420)
        self.assertEqual(parse_metric_text("500"), 500)
        self.assertEqual(parse_metric_text("35 Replies"), 35)
        self.assertIsNone(parse_metric_text(None))
        self.assertIsNone(parse_metric_text(""))
        self.assertIsNone(parse_metric_text("No numbers here"))

    def test_parse_netscape_cookies(self, tmp_path_factory=None):
        import tempfile
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write(".x.com\tTRUE\t/\tTRUE\t1750000000\tauth_token\tsecret123\n")
            f.write(".x.com\tTRUE\t/\tTRUE\t0\tct0\tcsrf456\n")
            temp_file = f.name

        try:
            cookies = parse_netscape_cookies(Path(temp_file))
            self.assertEqual(len(cookies), 2)
            self.assertEqual(cookies[0]["name"], "auth_token")
            self.assertEqual(cookies[0]["value"], "secret123")
            self.assertEqual(cookies[0]["domain"], ".x.com")
            self.assertTrue(cookies[0]["secure"])
            self.assertEqual(cookies[0]["expires"], 1750000000.0)
            self.assertEqual(cookies[1]["name"], "ct0")
            self.assertEqual(cookies[1]["value"], "csrf456")
        finally:
            import os
            if os.path.exists(temp_file):
                os.remove(temp_file)

    def test_add_cookies_to_playwright(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".txt") as f:
            f.write(".x.com\tTRUE\t/\tTRUE\t1750000000\tauth_token\tabc\n")
            temp_file = f.name

        try:
            mock_context = MagicMock()
            loaded = add_cookies_to_playwright(mock_context, temp_file)
            self.assertEqual(loaded, 1)
            mock_context.add_cookies.assert_called()
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)

    @patch.object(TwitterSource, "_fetch_account_via_ytdlp")
    @patch.object(TwitterSource, "_fetch_via_playwright")
    def test_fetch_candidates_uses_playwright_if_ytdlp_empty(self, mock_pw, mock_ytdlp):
        source = TwitterSource(self.config)
        mock_ytdlp.return_value = []
        mock_pw.return_value = [
            VideoCandidate(
                source="twitter",
                post_id="twitter_123",
                post_url="https://x.com/test_acc/status/123",
                title="Funny clip",
                uploader="test_acc",
                score=4500,
                num_comments=120,
                video_url="https://x.com/test_acc/status/123",
                is_nsfw=False,
            )
        ]

        cands = source.fetch_candidates(target_count=1)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].score, 4500)
        self.assertEqual(cands[0].num_comments, 120)
        mock_ytdlp.assert_called_once()
        mock_pw.assert_called_once()


if __name__ == "__main__":
    unittest.main()

