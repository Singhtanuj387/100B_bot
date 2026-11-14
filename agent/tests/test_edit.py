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

