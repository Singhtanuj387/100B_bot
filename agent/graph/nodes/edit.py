from pathlib import Path
from typing import Dict, Any, List
from loguru import logger
from graph.state import CurationState
from tools.editor_tool import EditorTool


def edit_node(
    state: CurationState,
    editor_tool: EditorTool,
) -> Dict[str, Any]:
    """
    Call Phase 1 Video Editor tool in parallel to brand all approved clips.
    Outputs to data/edited/<target_date>/.
    """
    approved = list(state.get("approved", []))
    target_date = state.get("target_date", "tomorrow")
    dry_run = state.get("dry_run", False)

    logger.info(f"--- [Node: EDIT] Branding {len(approved)} approved clips with Phase 1 Editor ---")

    project_root = Path(__file__).parent.parent.parent.parent.resolve()
    edited_dir = project_root / "agent" / "data" / "edited" / target_date
    edited_dir.mkdir(parents=True, exist_ok=True)

    # Prepare edit tasks
    tasks = []
    already_edited_clips = []
    for vid in approved:
        # Guarantee raw_video_path is preserved on candidate dictionary
        if not vid.get("raw_video_path"):
            vid["raw_video_path"] = vid.get("video_path")

        if vid.get("already_edited"):
            logger.info(f"Skipping video branding for already-edited reserve video: {vid.get('post_id')}")
            vid["edited_video_path"] = vid.get("edited_video_path") or vid.get("video_path")
            vid["edit_status"] = "success"
            already_edited_clips.append(vid)
            continue

        tasks.append({
            "post_id": vid["post_id"],
            "video_path": vid.get("raw_video_path") or vid["video_path"],
            "caption": vid.get("caption") or vid.get("title", "Funny moment"),
            "original_vid": vid,
        })

    # Execute batch edits for new clips
    results = editor_tool.batch_edit(
        tasks=tasks,
        output_dir=edited_dir,
        dry_run=dry_run,
    ) if tasks else []

    # Update approved list with both already-edited and newly edited video paths
    updated_approved: List[Dict[str, Any]] = list(already_edited_clips)
    for r in results:
        orig = r["original_vid"]
        orig["edited_video_path"] = r.get("edited_video_path")
        orig["edit_status"] = r.get("edit_status")
        orig["raw_video_path"] = orig.get("raw_video_path") or orig.get("video_path")
        updated_approved.append(orig)

    # Filter to only successful edits (or dry-run simulated)
    successful_edits = [v for v in updated_approved if v.get("edit_status") == "success" or dry_run]
    failed_edits = [v for v in updated_approved if v.get("edit_status") == "failed" and not dry_run]

    if failed_edits:
        logger.warning(f"{len(failed_edits)} videos failed during branding.")

    logger.info(f"Edit node finalized {len(successful_edits)} branded videos.")
    return {
        "approved": successful_edits,
        "edited_videos": successful_edits,
    }

