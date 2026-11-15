import os
import time
import socket
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from loguru import logger
import yaml

from googleapiclient.discovery import Resource
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from uploader.validator import validate_video_and_metadata
from uploader.alerts import send_telegram_alert
from uploader.db import UploadDatabase, update_schedule_slot


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


class QuotaTracker:
    """Tracks daily YouTube Data API v3 quota consumption persisted in SQLite by Pacific date."""

    def __init__(self, config: Dict[str, Any], db: Optional[UploadDatabase] = None):
        quota_cfg = config.get("quota", {})
        self.daily_cap = int(quota_cfg.get("daily_cap", 10000))
        self.insert_cost = int(quota_cfg.get("insert_cost", 1600))
        self.list_cost = int(quota_cfg.get("list_cost", 1))
        self.db = db
        self.pacific_date = self.get_current_pacific_date()

        self.used_today = 0
        self.inserts_today = 0
        self.reads_today = 0
        if self.db:
            q_data = self.db.get_daily_quota(self.pacific_date)
            self.used_today = q_data.get("quota_used", 0)
            self.inserts_today = q_data.get("inserts_count", 0)
            self.reads_today = q_data.get("reads_count", 0)

    @staticmethod
    def get_current_pacific_date() -> str:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")

    def _sync_pacific_date(self):
        cur_p_date = self.get_current_pacific_date()
        if cur_p_date != self.pacific_date:
            self.pacific_date = cur_p_date
            self.used_today = 0
            self.inserts_today = 0
            self.reads_today = 0
            if self.db:
                q_data = self.db.get_daily_quota(self.pacific_date)
                self.used_today = q_data.get("quota_used", 0)
                self.inserts_today = q_data.get("inserts_count", 0)
                self.reads_today = q_data.get("reads_count", 0)

    def record_insert(self):
        self._sync_pacific_date()
        self.used_today += self.insert_cost
        self.inserts_today += 1
        if self.db:
            self.db.record_quota_usage(
                self.pacific_date,
                insert_delta=1,
                read_delta=0,
                cost_delta=self.insert_cost,
            )
        logger.info(
            f"Quota Update: +{self.insert_cost} units (videos.insert #{self.inserts_today} on {self.pacific_date} PT). "
            f"Total used today: {self.used_today}/{self.daily_cap} units."
        )

    def record_read(self):
        self._sync_pacific_date()
        self.used_today += self.list_cost
        self.reads_today += 1
        if self.db:
            self.db.record_quota_usage(
                self.pacific_date,
                insert_delta=0,
                read_delta=1,
                cost_delta=self.list_cost,
            )
        logger.debug(f"Quota Update: +{self.list_cost} units (read #{self.reads_today}). Total: {self.used_today}")

    def can_insert(self) -> bool:
        self._sync_pacific_date()
        return (self.used_today + self.insert_cost) <= self.daily_cap


class YouTubeUploader:
    """
    Manages YouTube Shorts video uploads with chunked resumable upload,
    crash recovery with description ref scanning, quota budgeting, and error handling.
    """

    def __init__(
        self,
        youtube_service: Resource,
        config: Optional[Dict[str, Any]] = None,
        db: Optional[UploadDatabase] = None,
    ):
        self.youtube = youtube_service
        self.config = config or self._load_config()
        self.db = db or UploadDatabase()
        self.quota = QuotaTracker(self.config, db=self.db)
        self.channel_uploads_playlist_id: Optional[str] = None

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        if DEFAULT_CONFIG_PATH.is_file():
            with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def get_uploads_playlist_id(self) -> Optional[str]:
        """Fetch the authenticated user's uploads playlist ID."""
        if not self.youtube:
            return None

        if self.channel_uploads_playlist_id:
            return self.channel_uploads_playlist_id

        try:
            self.quota.record_read()
            resp = self.youtube.channels().list(mine=True, part="contentDetails").execute()
            items = resp.get("items", [])
            if items:
                playlist_id = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                self.channel_uploads_playlist_id = playlist_id
                return playlist_id
        except Exception as e:
            logger.warning(f"Could not retrieve channel uploads playlist ID: {e}")
        return None

    def find_uploaded_video_by_ref(self, post_id: str) -> Optional[str]:
        """
        Scan recent uploads on the channel for hidden marker [ref:<post_id>].
        Used during crash recovery to prevent duplicate uploads.
        """
        playlist_id = self.get_uploads_playlist_id()
        if not playlist_id:
            return None

        marker = f"[ref:{post_id}]"
        try:
            self.quota.record_read()
            resp = self.youtube.playlistItems().list(
                playlistId=playlist_id,
                part="snippet,contentDetails",
                maxResults=50,
            ).execute()

            for item in resp.get("items", []):
                snippet = item.get("snippet", {})
                desc = snippet.get("description", "")
                if marker in desc:
                    video_id = snippet.get("resourceId", {}).get("videoId") or item.get("contentDetails", {}).get("videoId")
                    logger.info(f"Crash Recovery: Found existing YouTube upload for {post_id} via ref marker (Video ID: {video_id})")
                    return video_id
        except Exception as e:
            logger.warning(f"Error scanning uploads playlist for ref marker {marker}: {e}")
        return None

    @staticmethod
    def parse_slot_time_utc(slot_time_str: str) -> datetime:
        """Parse ISO slot_time string and return UTC datetime."""
        dt = datetime.fromisoformat(slot_time_str)
        return dt.astimezone(timezone.utc)

    def is_slot_past_or_too_close(self, slot_time_str: str, min_lead_minutes: int = 15) -> bool:
        """Check if slot is in the past or within min_lead_minutes from now."""
        slot_dt_utc = self.parse_slot_time_utc(slot_time_str)
        now_utc = datetime.now(timezone.utc)
        lead_threshold = now_utc + timedelta(minutes=min_lead_minutes)
        return slot_dt_utc <= lead_threshold

    def is_slot_too_late_for_realtime(self, slot_time_str: str, max_late_minutes: int = 30) -> bool:
        """For realtime mode: check if slot is older than slot_time + max_late_minutes."""
        slot_dt_utc = self.parse_slot_time_utc(slot_time_str)
        now_utc = datetime.now(timezone.utc)
        late_threshold = slot_dt_utc + timedelta(minutes=max_late_minutes)
        return now_utc > late_threshold

    def check_kill_switch(self) -> bool:
        """Check if STOP kill switch file is present in repo root or uploader/."""
        root_stop = Path(__file__).resolve().parent.parent / "STOP"
        uploader_stop = Path(__file__).resolve().parent / "STOP"
        if root_stop.exists() or uploader_stop.exists():
            logger.warning("Kill switch file 'STOP' detected! Uploads disabled.")
            return True
        return False

    def upload_slot(
        self,
        slot: Dict[str, Any],
        target_date: str,
        schedule_path: Optional[Path] = None,
        confirm_public: bool = False,
        mode: str = "scheduled",
        dry_run: bool = False,
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Execute upload for a single slot.
        Returns: (status, video_id, error_message)
        """
        post_id = slot.get("post_id", "unknown")
        slot_time_str = slot.get("slot_time", "")
        raw_video_path = slot.get("video_path", "")
        title = slot.get("title", "")
        desc = slot.get("description", "")

        # 0. Check Kill Switch
        if self.check_kill_switch():
            reason = "Kill switch STOP file present"
            self.db.record_attempt(post_id, target_date, slot_time_str, "pending", error=reason)
            return "pending", None, reason

        # 1. Idempotency Check in Database
        existing_record = self.db.get_upload(post_id)
        if existing_record and existing_record.get("status") in ("uploaded", "scheduled"):
            video_id = existing_record.get("video_id")
            logger.info(f"Slot {post_id} already recorded as {existing_record['status']} (Video ID: {video_id}); skipping upload.")
            return existing_record["status"], video_id, None

        # 2. Crash Recovery Check on YouTube Channel (via hidden ref marker)
        recovered_id = self.find_uploaded_video_by_ref(post_id)
        if recovered_id:
            logger.success(f"Crash recovery successful for {post_id}: synced video_id {recovered_id}")
            self.db.record_attempt(post_id, target_date, slot_time_str, "uploaded", video_id=recovered_id)
            if schedule_path:
                update_schedule_slot(schedule_path, post_id, {"status": "uploaded", "video_id": recovered_id})
            return "uploaded", recovered_id, None

        # 3. Timing Constraints Check
        if mode == "scheduled":
            # For scheduled uploads, skip if slot is already in the past or < 15 min away
            if self.is_slot_past_or_too_close(slot_time_str, min_lead_minutes=15):
                reason = "slot_in_past (scheduled slot is in the past or < 15m away)"
                logger.warning(f"Skipping {post_id}: {reason}")
                self.db.record_attempt(post_id, target_date, slot_time_str, "failed", error=reason)
                if schedule_path:
                    update_schedule_slot(schedule_path, post_id, {"status": "failed", "error": reason})
                return "failed", None, reason
        elif mode == "realtime":
            # For realtime, check if late beyond max_late_minutes
            max_late = int(self.config.get("upload", {}).get("max_late_minutes", 30))
            if self.is_slot_too_late_for_realtime(slot_time_str, max_late_minutes=max_late):
                reason = f"slot_in_past (realtime slot is over {max_late}m late)"
                logger.warning(f"Skipping {post_id}: {reason}")
                self.db.record_attempt(post_id, target_date, slot_time_str, "failed", error=reason)
                if schedule_path:
                    update_schedule_slot(schedule_path, post_id, {"status": "failed", "error": reason})
                return "failed", None, reason

        # 4. Check Daily Max Uploads Limit (Pacific-time day)
        upload_cfg = self.config.get("upload", {})
        max_uploads_per_day = int(upload_cfg.get("max_uploads_per_day", 6))
        pacific_date = self.quota.get_current_pacific_date()
        uploads_today_pt = self.db.get_pacific_uploads_count(pacific_date)

        if uploads_today_pt >= max_uploads_per_day:
            reason = (
                f"Daily upload limit reached for Pacific day {pacific_date} "
                f"({uploads_today_pt}/{max_uploads_per_day} uploads). Postponing remaining slots."
            )
            logger.warning(reason)
            self.db.record_attempt(post_id, target_date, slot_time_str, "pending", error=reason)
            if schedule_path:
                update_schedule_slot(schedule_path, post_id, {"status": "pending", "error": reason})
            send_telegram_alert("DAILY_UPLOAD_LIMIT_REACHED", reason, {
                "pacific_date": pacific_date,
                "uploads_today": uploads_today_pt,
                "max_uploads": max_uploads_per_day,
            })
            return "pending", None, reason

        # 5. Check Daily Quota Cap
        if not self.quota.can_insert():
            reason = f"Daily YouTube upload quota limit reached ({self.quota.used_today}/{self.quota.daily_cap})"
            logger.warning(reason)
            self.db.record_attempt(post_id, target_date, slot_time_str, "pending", error=reason)
            if schedule_path:
                update_schedule_slot(schedule_path, post_id, {"status": "pending", "error": reason})
            send_telegram_alert("QUOTA_EXCEEDED", reason, {
                "used_today": self.quota.used_today,
                "daily_cap": self.quota.daily_cap,
            })
            return "pending", None, reason

        # 6. Pre-Upload Validation
        video_path = Path(raw_video_path)
        valid, val_reason, norm_data = validate_video_and_metadata(
            video_path=video_path,
            title=title,
            description=desc,
            post_id=post_id,
        )
        if not valid:
            logger.error(f"Validation failed for {post_id}: {val_reason}")
            self.db.record_attempt(post_id, target_date, slot_time_str, "failed", error=val_reason)
            if schedule_path:
                update_schedule_slot(schedule_path, post_id, {"status": "failed", "error": val_reason})
            return "failed", None, val_reason

        # 7. Determine Privacy and publishAt
        # When first_run_private is true or --confirm-public is missing: upload private without publishAt.
        # Scheduled mode sets publishAt only with --confirm-public or upload.go_live: true.
        first_run_private = upload_cfg.get("first_run_private", True)
        go_live = upload_cfg.get("go_live", False)

        publish_at_rfc3339: Optional[str] = None
        privacy_status = "private"

        if mode == "scheduled":
            if (confirm_public or go_live) and not first_run_private:
                slot_utc = self.parse_slot_time_utc(slot_time_str)
                publish_at_rfc3339 = slot_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                privacy_status = "private"  # Scheduled releases MUST have privacyStatus=private in YouTube API
                final_status = "scheduled"
            else:
                logger.info(f"Safety mode: Uploading {post_id} as private draft without publishAt (confirm_public={confirm_public}, go_live={go_live}, first_run_private={first_run_private})")
                publish_at_rfc3339 = None
                privacy_status = "private"
                final_status = "uploaded"
        else:  # realtime mode
            if confirm_public and not first_run_private:
                privacy_status = "public"
            else:
                privacy_status = "private"
            final_status = "uploaded"

        # 8. Dry-Run Handling
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would upload {post_id} to YouTube: Title='{norm_data['title']}' | "
                f"Privacy='{privacy_status}' | publishAt='{publish_at_rfc3339}' | File={video_path}"
            )
            return "scheduled" if publish_at_rfc3339 else "uploaded", f"dry_run_{post_id}", None

        # 9. Build Request Body
        body: Dict[str, Any] = {
            "snippet": {
                "title": norm_data["title"],
                "description": norm_data["description"],
                "tags": upload_cfg.get("tags", ["shorts", "comedy", "funny", "viral"]),
                "categoryId": str(upload_cfg.get("category_id", "23")),
                "defaultLanguage": upload_cfg.get("default_language", "en"),
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }
        if publish_at_rfc3339:
            body["status"]["publishAt"] = publish_at_rfc3339

        # Mark in-progress attempt
        self.db.record_attempt(
            post_id=post_id,
            target_date=target_date,
            slot_time=slot_time_str,
            status="uploading",
            privacy=privacy_status,
            publish_at=publish_at_rfc3339,
        )

        chunk_size = int(upload_cfg.get("chunk_size_bytes", 8 * 1024 * 1024))
        media = MediaFileUpload(str(video_path), chunksize=chunk_size, resumable=True, mimetype="video/mp4")

        # 9. Resumable Upload with Backoff Loop
        logger.info(f"Initiating YouTube resumable upload for {post_id} (Size: {norm_data['file_size'] / (1024*1024):.1f}MB, Mode: {mode}, Privacy: {privacy_status})...")
        video_id = None
        attempts = 0
        max_retries = 5

        try:
            request = self.youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )
            self.quota.record_insert()

            response = None
            while response is None:
                try:
                    upload_status, response = request.next_chunk()
                    if upload_status:
                        pct = int(upload_status.progress() * 100)
                        logger.info(f"Upload progress for {post_id}: {pct}%")
                except (HttpError, socket.error, OSError) as e:
                    attempts += 1
                    # Check for fatal error reasons
                    if isinstance(e, HttpError):
                        err_content = e.content.decode("utf-8") if isinstance(e.content, bytes) else str(e.content)
                        if "quotaExceeded" in err_content:
                            msg = "YouTube Data API quotaExceeded! Halting remaining uploads."
                            logger.critical(msg)
                            send_telegram_alert("QUOTA_EXCEEDED", msg)
                            self.db.update_status(post_id, "pending", error="quotaExceeded")
                            return "pending", None, "quotaExceeded"

                        if "uploadLimitExceeded" in err_content:
                            msg = "YouTube uploadLimitExceeded! Daily account upload limit reached."
                            logger.critical(msg)
                            send_telegram_alert("UPLOAD_LIMIT_EXCEEDED", msg)
                            self.db.update_status(post_id, "pending", error="uploadLimitExceeded")
                            return "pending", None, "uploadLimitExceeded"

                        if e.resp.status in (401, 403) and "quotaExceeded" not in err_content:
                            msg = f"YouTube API authorization or policy error ({e.resp.status}): {err_content}"
                            logger.error(msg)
                            send_telegram_alert("AUTH_OR_FORBIDDEN", msg)
                            self.db.update_status(post_id, "failed", error=str(e))
                            return "failed", None, str(e)

                    if attempts >= max_retries:
                        logger.error(f"Upload failed for {post_id} after {max_retries} attempts: {e}")
                        self.db.update_status(post_id, "failed", error=str(e))
                        return "failed", None, str(e)

                    sleep_s = 2 ** attempts
                    logger.warning(f"Upload chunk error for {post_id} ({e}); retrying in {sleep_s}s (Attempt {attempts}/{max_retries})...")
                    time.sleep(sleep_s)

            if response and "id" in response:
                video_id = response["id"]
                logger.success(f"YouTube upload successful for {post_id} -> Video ID: {video_id}")
            else:
                err_msg = f"YouTube insert returned no video ID: {response}"
                logger.error(err_msg)
                self.db.update_status(post_id, "failed", error=err_msg)
                return "failed", None, err_msg

        except Exception as e:
            logger.error(f"Unexpected upload exception for {post_id}: {e}")
            self.db.update_status(post_id, "failed", error=str(e))
            return "failed", None, str(e)

        # 10. Post-Upload Verification & Unaudited Lock Detection
        self.check_unaudited_lock(video_id, requested_privacy=privacy_status)

        # 11. Record Success in DB & schedule.json
        self.db.update_status(
            post_id=post_id,
            status=final_status,
            video_id=video_id,
            privacy=privacy_status,
            pacific_date=pacific_date,
        )
        if schedule_path:
            update_schedule_slot(
                schedule_path,
                post_id,
                {
                    "status": final_status,
                    "video_id": video_id,
                    "uploaded_at": datetime.now().isoformat(),
                    "privacy": privacy_status,
                },
            )

        return final_status, video_id, None

    def check_unaudited_lock(self, video_id: str, requested_privacy: str):
        """
        Verify post-upload status. If YouTube forced the video to private due to unverified project,
        log a clear warning and send a notification without failing the upload.
        """
        if not self.youtube:
            return

        try:
            self.quota.record_read()
            resp = self.youtube.videos().list(part="status", id=video_id).execute()
            items = resp.get("items", [])
            if items:
                status = items[0].get("status", {})
                actual_privacy = status.get("privacyStatus")
                if requested_privacy == "public" and actual_privacy == "private":
                    msg = (
                        f"API project not audited; video {video_id} was locked private by YouTube. "
                        f"Complete Google OAuth app verification or keep private mode."
                    )
                    logger.warning(msg)
                    send_telegram_alert("UNAUDITED_PROJECT_LOCK", msg, {"video_id": video_id})
        except Exception as e:
            logger.debug(f"Could not check post-upload status for {video_id}: {e}")
