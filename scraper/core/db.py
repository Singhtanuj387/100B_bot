import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from loguru import logger
import imagehash


class VideoDatabase:
    """
    SQLite persistent storage for tracking video history and deduplication.
    Ensures no video is ever repeated across days or platforms.
    """

    def __init__(self, db_path: str = "data/videos.db"):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    post_id TEXT NOT NULL UNIQUE,
                    post_url TEXT,
                    title TEXT,
                    uploader TEXT,
                    scraped_at TEXT NOT NULL,
                    file_path TEXT,
                    caption TEXT,
                    duration REAL,
                    resolution TEXT,
                    file_hash TEXT,
                    phash_1 TEXT,
                    phash_2 TEXT,
                    phash_3 TEXT,
                    status TEXT NOT NULL,
                    filter_reason TEXT,
                    metadata_json TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_post_id ON videos(post_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_post_url ON videos(post_url);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON videos(file_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON videos(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_at ON videos(scraped_at);")

    def is_id_seen(self, post_id: str) -> bool:
        """Check if post_id exists in database."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM videos WHERE post_id = ? LIMIT 1;", (post_id,))
            return cur.fetchone() is not None

    def is_url_seen(self, post_url: str) -> bool:
        """Check if post_url exists in database."""
        if not post_url:
            return False
        with self._get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM videos WHERE post_url = ? LIMIT 1;", (post_url,))
            return cur.fetchone() is not None

    def is_file_hash_seen(self, file_hash: str) -> bool:
        """Check if identical binary file has already been downloaded."""
        if not file_hash:
            return False
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT 1 FROM videos WHERE file_hash = ? AND status = 'downloaded' LIMIT 1;",
                (file_hash,)
            )
            return cur.fetchone() is not None

    def find_phash_duplicates(
        self,
        candidate_phashes: List[str],
        threshold: int = 6,
    ) -> Optional[Dict[str, Any]]:
        """
        Compare perceptual frame hashes against all existing downloaded videos.
        Returns the matching video record if Hamming distance <= threshold on at least 2 frames.
        """
        if not candidate_phashes or len(candidate_phashes) < 2:
            return None

        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT id, post_id, title, phash_1, phash_2, phash_3
                FROM videos
                WHERE status = 'downloaded'
                  AND phash_1 IS NOT NULL
                  AND phash_2 IS NOT NULL;
            """)
            rows = cur.fetchall()

        cand_hashes = []
        for h_str in candidate_phashes:
            try:
                cand_hashes.append(imagehash.hex_to_hash(h_str))
            except Exception:
                continue

        if not cand_hashes:
            return None

        for row in rows:
            matches = 0
            db_hashes = []
            for col in ("phash_1", "phash_2", "phash_3"):
                val = row[col]
                if val:
                    try:
                        db_hashes.append(imagehash.hex_to_hash(val))
                    except Exception:
                        pass

            # Compare pairs of frame hashes
            num_compare = min(len(cand_hashes), len(db_hashes))
            for i in range(num_compare):
                dist = cand_hashes[i] - db_hashes[i]
                if dist <= threshold:
                    matches += 1

            # If at least 2 keyframes match closely, it's a perceptual duplicate
            if matches >= 2 or (num_compare == 1 and matches == 1):
                return dict(row)

        return None

    def record_seen(
        self,
        post_id: str,
        source: str,
        post_url: str = "",
        title: str = "",
        uploader: str = "",
        status: str = "seen",
        filter_reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a candidate as seen or filtered without full media download."""
        now = datetime.now().isoformat()
        meta_str = json.dumps(metadata) if metadata else None

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO videos (
                    source, post_id, post_url, title, uploader,
                    scraped_at, status, filter_reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    status = excluded.status,
                    filter_reason = excluded.filter_reason,
                    metadata_json = excluded.metadata_json;
            """, (
                source, post_id, post_url, title, uploader,
                now, status, filter_reason, meta_str
            ))

    def save_downloaded_video(
        self,
        source: str,
        post_id: str,
        post_url: str,
        title: str,
        uploader: str,
        file_path: str,
        caption: str,
        duration: float,
        resolution: str,
        file_hash: str,
        phashes: List[str],
        metadata: Dict[str, Any],
    ) -> None:
        """Save a fully validated and downloaded video record."""
        now = datetime.now().isoformat()
        ph1 = phashes[0] if len(phashes) > 0 else None
        ph2 = phashes[1] if len(phashes) > 1 else None
        ph3 = phashes[2] if len(phashes) > 2 else None
        meta_str = json.dumps(metadata)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO videos (
                    source, post_id, post_url, title, uploader,
                    scraped_at, file_path, caption, duration, resolution,
                    file_hash, phash_1, phash_2, phash_3, status, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    caption = excluded.caption,
                    duration = excluded.duration,
                    resolution = excluded.resolution,
                    file_hash = excluded.file_hash,
                    phash_1 = excluded.phash_1,
                    phash_2 = excluded.phash_2,
                    phash_3 = excluded.phash_3,
                    status = 'downloaded',
                    metadata_json = excluded.metadata_json;
            """, (
                source, post_id, post_url, title, uploader,
                now, file_path, caption, duration, resolution,
                file_hash, ph1, ph2, ph3, meta_str
            ))

    def get_videos_for_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Retrieve all downloaded videos for a given date prefix (e.g. '2026-11-12').
        Returns list of ready videos with metadata, caption, and file paths.
        """
        prefix = f"{date_str}%"
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT * FROM videos
                WHERE status = 'downloaded' AND scraped_at LIKE ?
                ORDER BY id ASC;
            """, (prefix,))
            rows = cur.fetchall()

        results = []
        for row in rows:
            d = dict(row)
            if d.get("metadata_json"):
                try:
                    d["meta"] = json.loads(d["metadata_json"])
                except Exception:
                    d["meta"] = {}
            results.append(d)
        return results

    def get_summary_stats(self) -> Dict[str, int]:
        """Return counts of videos by status."""
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT status, COUNT(*) as count FROM videos GROUP BY status;
            """)
            return {row["status"]: row["count"] for row in cur.fetchall()}
