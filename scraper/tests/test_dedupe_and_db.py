import tempfile
import os
from pathlib import Path
from core.db import VideoDatabase
from core.dedupe import Deduplicator


def test_db_init_and_id_tracking():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_videos.db")
        db = VideoDatabase(db_path)
        dedup = Deduplicator(db, phash_threshold=6)

        assert not db.is_id_seen("post_123")
        is_seen, _ = dedup.is_id_or_url_seen("post_123")
        assert not is_seen

        # Record seen
        db.record_seen("post_123", "reddit", post_url="https://reddit.com/123", title="Test Title", status="seen")
        assert db.is_id_seen("post_123")
        is_seen, reason = dedup.is_id_or_url_seen("post_123")
        assert is_seen
        assert "already seen" in reason


def test_db_downloaded_save_and_query():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_videos.db")
        db = VideoDatabase(db_path)

        date_today = "2026-11-12"
        db.save_downloaded_video(
            source="reddit",
            post_id="post_abc",
            post_url="https://reddit.com/abc",
            title="Hilarious cat clip",
            uploader="catlover",
            file_path="/path/to/video.mp4",
            caption="Hilarious cat clip 😂",
            duration=14.5,
            resolution="1080x1920",
            file_hash="hash_1234567890",
            phashes=["ffd0e0e0e0e0e0ff", "ffc0c0c0c0c0c0ff"],
            metadata={"source": "reddit", "score": 1200},
        )

        assert db.is_file_hash_seen("hash_1234567890")

        # Query for date
        videos = db.get_videos_for_date("2026-11")
        assert len(videos) == 1
        assert videos[0]["post_id"] == "post_abc"
        assert videos[0]["caption"] == "Hilarious cat clip 😂"


def test_failed_download_cleans_up_empty_folder():
    from unittest.mock import MagicMock, patch
    from core.downloader import VideoDownloader

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_videos.db")
        db = VideoDatabase(db_path)
        dedup = Deduplicator(db, phash_threshold=6)
        downloader = VideoDownloader(db, dedup, config={})

        cand = MagicMock()
        cand.post_id = "reddit_123fail"
        cand.source = "reddit"
        cand.post_url = "https://reddit.com/123fail"
        cand.video_url = "https://reddit.com/123fail"
        cand.title = "Failed Video"
        cand.uploader = "someuser"
        cand.extra = {}

        dest_dir = Path(tmp_dir) / "reddit_123fail"

        # Mock download failure
        with patch("core.downloader.download_reddit_with_ffmpeg", return_value=False), \
             patch("core.downloader.download_with_ytdlp", return_value=False):
            success, msg, meta = downloader.process_candidate(cand, dest_dir)
            assert not success
            # Verify dest_dir was cleaned up and does not exist
            assert not dest_dir.exists()

