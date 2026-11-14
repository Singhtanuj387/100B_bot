#!/usr/bin/env python3
"""
==============================================================================
               100B VIDEO BOT — DAILY MASTER ORCHESTRATOR
==============================================================================

Automates the complete daily production and upload cycle:
  1. Locks execution to prevent overlapping runs (with PID stale lock detection)
  2. Runs curation: agent/curate.py --date tomorrow --no-review --borderline-policy approve_top
  3. Runs upload: upload.py upload --date tomorrow
  4. Runs post-upload verification: upload.py verify --date tomorrow
  5. Runs safe cleanup: moves uploaded files to published/, purges media > 14 days old
  6. Dispatches consolidated Telegram / Windows notification summary
"""

import sys
import os
import json
import time
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger

from uploader.alerts import send_telegram_alert
from uploader.cleanup import archive_verified_slot, purge_old_media
from uploader.db import UploadDatabase


BASE_DIR = Path(__file__).resolve().parent
LOCK_FILE = BASE_DIR / "daily_run.lock"


def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is currently active."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


class DailyLockManager:
    """Acquires a process lock with automatic stale PID detection and cleanup."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.acquired = False

    def acquire(self) -> bool:
        if self.lock_path.exists():
            try:
                content = json.loads(self.lock_path.read_text(encoding="utf-8"))
                existing_pid = int(content.get("pid", 0))
                if is_pid_alive(existing_pid):
                    logger.error(f"Another daily run is already active (PID {existing_pid}). Halting.")
                    return False
                else:
                    logger.warning(f"Detected stale lock file from dead PID {existing_pid}. Removing stale lock.")
                    self.lock_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Corrupt lock file detected ({e}); replacing.")
                self.lock_path.unlink(missing_ok=True)

        # Write current PID
        lock_data = {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
        }
        try:
            self.lock_path.write_text(json.dumps(lock_data), encoding="utf-8")
            self.acquired = True
            logger.info(f"Acquired master daily lock (PID: {os.getpid()})")
            return True
        except Exception as e:
            logger.error(f"Could not create lock file: {e}")
            return False

    def release(self):
        if self.acquired and self.lock_path.exists():
            try:
                self.lock_path.unlink(missing_ok=True)
                logger.info("Released master daily lock.")
            except Exception as e:
                logger.warning(f"Error removing lock file: {e}")
        self.acquired = False

    def __enter__(self):
        if not self.acquire():
            sys.exit(1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def run_step(name: str, cmd: list, timeout_s: int = 3600) -> bool:
    """Execute a single pipeline step in a subprocess with isolated timeout."""
    logger.info(f"\n{'='*70}\n[STEP] Starting: {name}\nCommand: {' '.join(cmd)}\n{'='*70}")
    start = time.time()
    try:
        proc = subprocess.run(cmd, timeout=timeout_s, cwd=str(BASE_DIR))
        elapsed = round(time.time() - start, 1)
        if proc.returncode == 0:
            logger.success(f"[STEP SUCCESS] {name} completed in {elapsed}s.")
            return True
        else:
            logger.error(f"[STEP FAILED] {name} exited with code {proc.returncode} in {elapsed}s.")
            return False
    except subprocess.TimeoutExpired:
        logger.critical(f"[STEP TIMEOUT] {name} exceeded {timeout_s}s timeout!")
        return False
    except Exception as e:
        logger.error(f"[STEP EXCEPTION] {name} failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="100B Automated Video Bot — Daily Orchestration")
    parser.add_argument("--date", default="tomorrow", help="Target date ('tomorrow' or 'YYYY-MM-DD')")
    parser.add_argument("--dry-run", action="store_true", help="Simulate pipeline without real uploads or paid APIs")
    parser.add_argument("--confirm-public", action="store_true", help="Allow public / scheduled releases")
    parser.add_argument("--skip-curation", action="store_true", help="Skip curation step and go straight to upload")
    parser.add_argument("--skip-upload", action="store_true", help="Skip upload step (curation only)")
    parser.add_argument("--timeout", type=int, default=7200, help="Global pipeline timeout in seconds (default: 7200)")

    args = parser.parse_args()

    # Acquire Master Process Lock
    with DailyLockManager(LOCK_FILE):
        logger.info(f"=== Starting Daily Bot Run for Date: {args.date} (Dry Run: {args.dry_run}) ===")

        # Step 1: Curation
        curation_ok = True
        if not args.skip_curation:
            curate_cmd = [
                sys.executable,
                str(BASE_DIR / "agent" / "curate.py"),
                "--date", args.date,
                "--no-review",
                "--borderline-policy", "approve_top",
            ]
            if args.dry_run:
                curate_cmd.append("--dry-run")

            curation_ok = run_step("Video Curation (LangGraph)", curate_cmd, timeout_s=3600)
            if not curation_ok:
                msg = f"Daily curation step failed for date: {args.date}."
                logger.error(msg)
                send_telegram_alert("CURATION_FAILED", msg)
                sys.exit(2)

        # Step 2: Upload
        upload_ok = True
        if not args.skip_upload:
            upload_cmd = [
                sys.executable,
                str(BASE_DIR / "upload.py"),
                "upload",
                "--date", args.date,
            ]
            if args.dry_run:
                upload_cmd.append("--dry-run")
            if args.confirm_public:
                upload_cmd.append("--confirm-public")

            upload_ok = run_step("YouTube Upload Batch", upload_cmd, timeout_s=1800)

            # Step 3: Verification
            if not args.dry_run:
                verify_cmd = [
                    sys.executable,
                    str(BASE_DIR / "upload.py"),
                    "verify",
                    "--date", args.date,
                ]
                run_step("YouTube Post-Upload Verification", verify_cmd, timeout_s=300)

        # Step 4: Cleanup & Archival
        logger.info("\n--- [STEP] Media Archival & Retention Purge ---")
        try:
            # Move uploaded videos to data/published/
            from upload import resolve_date_arg
            target_date_str = resolve_date_arg(args.date)
            schedule_path = BASE_DIR / "agent" / "data" / "edited" / target_date_str / "schedule.json"

            if schedule_path.is_file():
                with open(schedule_path, "r", encoding="utf-8") as f:
                    sched = json.load(f)
                for slot in sched.get("slots", []):
                    if slot.get("status") in ("uploaded", "scheduled"):
                        v_path = slot.get("video_path")
                        if v_path and Path(v_path).is_file():
                            new_path = archive_verified_slot(Path(v_path), target_date_str, base_dir=BASE_DIR)
                            if new_path:
                                slot["video_path"] = str(new_path)

            # Purge media older than 14 days
            purge_old_media(retention_days=14, base_dir=BASE_DIR)
        except Exception as e:
            logger.warning(f"Archival/cleanup step encountered warning: {e}")

        # Step 5: Final Summary Notification
        logger.success(f"Daily pipeline finished successfully for date: {args.date}!")
        send_telegram_alert(
            "DAILY_RUN_COMPLETE",
            f"<b>🏆 Daily 100B Run Completed</b>\n\n"
            f"<b>Date:</b> <code>{args.date}</code>\n"
            f"<b>Curation:</b> {'✅ Done' if curation_ok else '❌ Failed'}\n"
            f"<b>Upload:</b> {'✅ Done' if upload_ok else '❌ Skipped/Failed'}\n",
        )


if __name__ == "__main__":
    main()
