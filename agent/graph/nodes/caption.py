import re
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger

from graph.state import CurationState
from llm.client import LLMClient
from llm.schemas import CaptionResult
from llm.prompts import build_caption_system_prompt, build_caption_user_prompt


def clean_caption_text(caption: str) -> str:
    """Enforce strict caption rules: <= 8 words, max 1 emoji, no hashtags, no URLs."""
    # Strip URLs
    caption = re.sub(r"https?://\S+", "", caption)
    # Strip hashtags
    caption = re.sub(r"#\w+", "", caption)
    # Strip usernames / mentions
    caption = re.sub(r"@\w+", "", caption)
    # Strip Reddit tags like [OC], (watch till end)
    caption = re.sub(r"\[.*?\]|\(.*?\)", "", caption)
    caption = re.sub(r"\s+", " ", caption).strip()

    # Complete modern emoji range matching all emoticons, gestures, faces and symbols
    emoji_pattern = re.compile(
        r"[\U0001F000-\U0001FFFF\U00002600-\U000027BF]"
    )
    emojis = emoji_pattern.findall(caption)
    if len(emojis) > 1:
        first_emoji = emojis[0]
        # Remove all emojis
        caption = emoji_pattern.sub("", caption).strip()
        # Append only the first emoji at the end
        caption = f"{caption} {first_emoji}".strip()

    # Max 8 words
    words = caption.split()
    if len(words) > 8:
        # Keep first 7 words plus emoji if present
        if emojis:
            caption = " ".join(words[:7]) + f" {emojis[0]}"
        else:
            caption = " ".join(words[:8])

    return caption.strip()


def caption_node(state: CurationState, llm_client: LLMClient) -> Dict[str, Any]:
    """
    Generate punchy captions, YouTube titles, and descriptions for all approved videos.
    Runs strictly AFTER approval is finalized.
    """
    approved = list(state.get("approved", []))
    logger.info(f"--- [Node: CAPTION] Generating captions & YouTube metadata for {len(approved)} approved clips ---")

    sys_prompt = build_caption_system_prompt()

    for cand in approved:
        # Check if video was backfilled from reserve pool (already edited with caption & YT metadata)
        if cand.get("already_edited"):
            logger.info(f"Skipping caption synthesis for already-edited reserve video: {cand.get('post_id')}")
            continue

        # Only skip the LLM if the human explicitly edited the caption (pressed "E" in review)
        is_human_edited = bool(cand.get("caption_edited_by_human"))
        existing_caption = cand.get("caption")

        if is_human_edited and existing_caption:
            cleaned = clean_caption_text(existing_caption)
            cand["caption"] = cleaned
            # Create YouTube metadata if missing
            cand["youtube_title"] = cand.get("youtube_title") or f"{cleaned} #shorts #funny"
            cand["youtube_description"] = cand.get("youtube_description") or (
                f"Credit to original creator: {cand.get('uploader', 'unknown')}\n"
                f"Original Post: {cand.get('post_url', '')}\n\n"
                f"Subscribe for your daily laugh!"
            )
        else:
            # Generate via LLM
            usr_prompt = build_caption_user_prompt(
                title=cand.get("title", ""),
                original_caption=cand.get("original_caption", ""),
                uploader=cand.get("uploader", "unknown"),
                post_url=cand.get("post_url", ""),
                transcript=cand.get("transcript", ""),
            )

            try:
                res: CaptionResult = llm_client.call_structured(
                    task_type="caption",
                    system_prompt=sys_prompt,
                    user_prompt=usr_prompt,
                    schema=CaptionResult,
                )
                cleaned = clean_caption_text(res.caption)
                cand["caption"] = cleaned
                cand["youtube_title"] = res.youtube_title
                cand["youtube_description"] = res.youtube_description

            except Exception as e:
                logger.warning(f"Caption LLM error for {cand.get('post_id')}: {e}. Fallback to cleaned title.")
                fallback = clean_caption_text(cand.get("title", "Funny moment"))
                cand["caption"] = fallback
                cand["youtube_title"] = f"{fallback} 😂 #shorts #funny"
                cand["youtube_description"] = (
                    f"Credit: {cand.get('uploader', 'unknown')}\n"
                    f"Source: {cand.get('post_url', '')}\n\n"
                    f"Subscribe for more funny shorts!"
                )

        # Save caption.txt next to video file
        vid_path_str = cand.get("video_path")
        if vid_path_str:
            vid_path = Path(vid_path_str)
            if vid_path.parent.exists():
                caption_file = vid_path.parent / "caption.txt"
                try:
                    with open(caption_file, "w", encoding="utf-8") as f:
                        f.write(cand["caption"])
                    cand["caption_path"] = str(caption_file)
                except Exception as e:
                    logger.warning(f"Could not write caption.txt to {caption_file}: {e}")

        logger.info(f"Caption for {cand.get('post_id')}: \"{cand.get('caption')}\"")

    return {
        "approved": approved,
        "cost_so_far": llm_client.total_cost_usd,
        "llm_calls_count": llm_client.call_count,
    }
