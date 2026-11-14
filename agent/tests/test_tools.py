import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from tools.editor_tool import EditorTool
from tools.scraper_tool import ScraperTool


def test_editor_tool_subprocess_call(tmp_path):
    """Verify EditorTool invokes subprocess.run with PYTHONUTF8=1 without NameError."""
    dummy_input = tmp_path / "test_input.mp4"
    dummy_input.write_bytes(b"dummy video data")
    dummy_output = tmp_path / "test_output.mp4"

    tool = EditorTool(editor_script=tmp_path / "main.py")

    with patch("subprocess.run") as mock_run:
        # Simulate successful subprocess run and output file creation
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Rendered branded video successfully"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        # Create output file to satisfy exists() check
        dummy_output.write_bytes(b"rendered video")

        success, msg = tool.edit_single_video(
            input_video_path=dummy_input,
            caption="Check this out 😂🔥",
            output_video_path=dummy_output,
            dry_run=False,
        )

        assert success is True
        assert "Successfully edited" in msg
        mock_run.assert_called_once()

        # Check call arguments and environment
        call_kwargs = mock_run.call_args[1]
        env_passed = call_kwargs.get("env", {})
        assert env_passed.get("PYTHONUTF8") == "1"
        assert env_passed.get("PYTHONIOENCODING") == "utf-8"
        assert call_kwargs.get("encoding") == "utf-8"


def test_scraper_tool_subprocess_call(tmp_path):
    """Verify ScraperTool invokes subprocess.run with PYTHONUTF8=1 without NameError."""
    tool = ScraperTool(scraper_dir=tmp_path)

    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Scraped 5 candidates"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        success = tool.run_scrape(
            target_count=5,
            date_str="2026-11-12",
            sources=["reddit"],
            subreddits=["funny", "Unexpected"],
            dry_run=False,
        )

        assert success is True
        mock_run.assert_called_once()

        # Check call arguments and environment
        call_kwargs = mock_run.call_args[1]
        env_passed = call_kwargs.get("env", {})
        assert env_passed.get("PYTHONUTF8") == "1"
        assert env_passed.get("PYTHONIOENCODING") == "utf-8"
        assert call_kwargs.get("encoding") == "utf-8"

