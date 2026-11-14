import pytest
from unittest.mock import MagicMock
from graph.nodes.edit import edit_node


def test_edit_node_skips_already_edited():
    mock_editor = MagicMock()

    state = {
        "approved": [
            {
                "post_id": "res_vid",
                "video_path": "data/edited/2026-11-11/res_vid_branded.mp4",
                "already_edited": True,
                "caption": "Pre-made caption 😂",
            },
            {
                "post_id": "fresh_vid",
                "video_path": "scraper/data/raw/2026-11-12/fresh_vid/video.mp4",
                "already_edited": False,
                "caption": "Fresh caption 😂",
            },
        ],
        "target_date": "2026-11-12",
        "dry_run": False,
    }

    mock_editor.batch_edit.return_value = [
        {
            "original_vid": state["approved"][1],
            "edited_video_path": "data/edited/2026-11-12/fresh_vid_branded.mp4",
            "edit_status": "success",
        }
    ]

    result = edit_node(state, mock_editor)

    # batch_edit should only be called with 1 task (fresh_vid), NOT res_vid
    assert mock_editor.batch_edit.call_count == 1
    tasks_passed = mock_editor.batch_edit.call_args[1]["tasks"]
    assert len(tasks_passed) == 1
    assert tasks_passed[0]["post_id"] == "fresh_vid"

    # Both clips should be in final approved list
    approved = result["approved"]
    assert len(approved) == 2
    res_clip = next(v for v in approved if v["post_id"] == "res_vid")
    assert res_clip["edited_video_path"] == "data/edited/2026-11-11/res_vid_branded.mp4"
    assert res_clip["edit_status"] == "success"


def test_editor_compositing_90_second_clip():
    """Verify that 90-second clips generate valid FFmpeg scaling and compositing commands."""
    import importlib.util
    from pathlib import Path
    project_root = Path(__file__).parent.parent.parent.resolve()
    compose_path = project_root / "core" / "compose.py"
    spec = importlib.util.spec_from_file_location("root_compose", str(compose_path))
    root_compose = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(root_compose)
    build_ffmpeg_command = root_compose.build_ffmpeg_command

    input_path = Path("test_90s.mp4")
    bg_path = Path("bg_90s.png")
    placement = {"x": 0, "y": 420, "w": 1080, "h": 1080}
    video_info = {
        "duration": 90.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "crop_filter": None,
    }
    output_path = Path("output_90s.mp4")
    config = {
        "encoding": {
            "gpu": "cpu",
            "cpu_preset": "fast",
            "cpu_crf": 18,
            "audio_codec": "aac",
            "audio_bitrate": "192k",
        }
    }

    cmd, encoder_label = build_ffmpeg_command(
        input_path=input_path,
        bg_path=bg_path,
        placement=placement,
        video_info=video_info,
        output_path=output_path,
        config=config,
    )

    # Must loop background template and terminate cleanly with -shortest for 90s clip
    assert "-loop" in cmd
    assert "-shortest" in cmd
    assert "-movflags" in cmd
    assert "+faststart" in cmd
    assert str(output_path) in cmd
    assert "scale=1080:1080:flags=lanczos" in "".join(cmd)


