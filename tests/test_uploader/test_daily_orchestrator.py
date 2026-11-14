"""Test suite for daily orchestrator lock management and execution flow."""
import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from run_daily import DailyLockManager, is_pid_alive, run_step


def test_lock_acquire_and_release(tmp_path):
    lock_file = tmp_path / "test_daily.lock"
    manager = DailyLockManager(lock_file)

    assert manager.acquire() is True
    assert lock_file.exists()

    content = json.loads(lock_file.read_text(encoding="utf-8"))
    assert content["pid"] == os.getpid()

    manager.release()
    assert not lock_file.exists()


@patch("run_daily.is_pid_alive")
def test_stale_lock_detection_replaces_dead_pid(mock_alive, tmp_path):
    lock_file = tmp_path / "stale.lock"
    # Write a dead PID into lock file
    lock_file.write_text(json.dumps({"pid": 999999, "started_at": "2026-11-10T00:00:00"}), encoding="utf-8")
    mock_alive.return_value = False  # Simulate dead process

    manager = DailyLockManager(lock_file)
    assert manager.acquire() is True

    # Lock file should now contain current PID
    new_content = json.loads(lock_file.read_text(encoding="utf-8"))
    assert new_content["pid"] == os.getpid()
    manager.release()


@patch("run_daily.is_pid_alive")
def test_lock_contention_blocks_when_active(mock_alive, tmp_path):
    lock_file = tmp_path / "active.lock"
    lock_file.write_text(json.dumps({"pid": 1234, "started_at": "2026-11-15T00:00:00"}), encoding="utf-8")
    mock_alive.return_value = True  # Process is alive

    manager = DailyLockManager(lock_file)
    assert manager.acquire() is False


@patch("subprocess.run")
def test_run_step_success_and_failure(mock_sub):
    mock_sub.return_value = MagicMock(returncode=0)
    assert run_step("Test Step", ["python", "--version"], timeout_s=10) is True

    mock_sub.return_value = MagicMock(returncode=1)
    assert run_step("Failing Step", ["python", "fail.py"], timeout_s=10) is False
