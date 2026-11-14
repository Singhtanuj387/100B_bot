import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from loguru import logger

from graph.state import CurationState
from llm.client import LLMClient
from llm.schemas import CaptionResult
from llm.prompts import build_caption_system_prompt, build_caption_user_prompt

GENERIC_TITLE_PATTERNS = [
    "wait till you see what happens next",
    "wait until you see what happens next",
    "wait till the end",
    "wait for the end",
    "funny moment",
    "he really thought he had it",
    "you won't believe what happens",
]


def is_generic_title(title: Optional[str]) -> bool:
    """Check if a title is empty, None, or matches known generic clickbait templates."""
    if not title or not str(title).strip():
        return True
    norm = str(title).lower().strip()
    # Strip emojis and hashtags for comparison
    clean = re.sub(r"[#\U0001F000-\U0001FFFF\U00002600-\U000027BF]", "", norm).strip()
    return any(p in clean for p in GENERIC_TITLE_PATTERNS)


def clean_caption_text(caption: str) -> str:
    """Enforce strict caption rules: <= 8 words, max 1 emoji, no hashtags, no URLs."""
    if not caption:
        return "Funny moment 😂"
    # Strip URLs
    caption = re.sub(r"https?://\S+", "", caption)
    # Strip hashtags
    caption = re.sub(r"#\w+", "", caption)
    # Strip usernames / mentions
    caption = re.sub(r"@\w+", "", caption)
    # Strip Reddit tags like [OC], (watch till end), WCGW, HMB
    caption = re.sub(r"\[.*?\]|\(.*?\)", "", caption)
    caption = re.sub(r"^[Ww][Cc][Gg][Ww]\s*[-:]*\s*", "", caption)
    caption = re.sub(r"^[Hh][Mm][Bb]\s*[-:]*\s*", "", caption)
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
    Enforces that every video receives a unique, content-specific title (never generic duplicates).
    Runs strictly AFTER approval is finalized.
    """
    approved = list(state.get("approved", []))
    logger.info(f"--- [Node: CAPTION] Generating captions & YouTube metadata for {len(approved)} approved clips ---")

    sys_prompt = build_caption_system_prompt()
    used_titles: Set[str] = set()

    for idx, cand in enumerate(approved):
        is_already_edited = cand.get("already_edited", False)
        yt_title = cand.get("youtube_title")

        # If already edited reserve clip with a valid non-generic title that hasn't been used yet
        if is_already_edited and yt_title and not is_generic_title(yt_title) and yt_title.lower().strip() not in used_titles:
            logger.info(f"Keeping existing YouTube title for reserve video {cand.get('post_id')}: \"{yt_title}\"")
            used_titles.add(yt_title.lower().strip())
            continue

        # If already edited but title is generic, missing, or duplicate, refine its metadata
        if is_already_edited:
            logger.info(f"Refining generic/duplicate YouTube title for reserve clip {cand.get('post_id')}...")

        # Only skip LLM if human explicitly edited the caption in interactive review
        is_human_edited = bool(cand.get("caption_edited_by_human"))
        existing_caption = cand.get("caption")

        if is_human_edited and existing_caption:
            cleaned = clean_caption_text(existing_caption)
            cand["caption"] = cleaned
            cand["youtube_title"] = cand.get("youtube_title") or f"{cleaned} #shorts #funny"
            cand["youtube_description"] = cand.get("youtube_description") or (
                f"Credit to original creator: {cand.get('uploader', 'unknown')}\n"
                f"Original Post: {cand.get('post_url', '')}\n\n"
                f"Subscribe for your daily laugh!"
            )
        elif not is_already_edited:
            # Generate via LLM for new clip
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
                fallback = clean_caption_text(cand.get("title", f"Comedy Moment {idx+1}"))
                cand["caption"] = fallback
                cand["youtube_title"] = f"{fallback} #shorts #funny"
                cand["youtube_description"] = (
                    f"Credit: {cand.get('uploader', 'unknown')}\n"
                    f"Source: {cand.get('post_url', '')}\n\n"
                    f"Subscribe for more funny shorts!"
                )
        else:
            # Already edited reserve clip whose title was generic: synthesize unique title from its original metadata
            raw_title = cand.get("title") or cand.get("caption") or f"Comedy Moment {idx+1}"
            cleaned = clean_caption_text(raw_title)
            cand["youtube_title"] = f"{cleaned} #shorts #funny"
            if not cand.get("youtube_description"):
                cand["youtube_description"] = (
                    f"Credit to creator: {cand.get('uploader', 'unknown')}\n"
                    f"Original source: {cand.get('post_url', '')}\n\n"
                    f"Subscribe for daily laughs!"
                )

        # Enforce strict uniqueness: no video can have a generic or duplicate title
        curr_title = cand.get("youtube_title", "")
        if is_generic_title(curr_title) or curr_title.lower().strip() in used_titles:
            base = clean_caption_text(cand.get("title") or cand.get("caption") or f"Comedy Moment {idx+1}")
            if not base or is_generic_title(base):
                base = f"Comedy Moment #{idx+1}"
            candidate_unique = f"{base} #shorts #funny"
            if candidate_unique.lower().strip() in used_titles:
                candidate_unique = f"{base} ({len(used_titles)+1}) #shorts #funny"
            cand["youtube_title"] = candidate_unique

        used_titles.add(cand["youtube_title"].lower().strip())

        # Save caption.txt next to video file if present
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

        logger.info(f"Caption for {cand.get('post_id')}: \"{cand.get('caption')}\" | Title: \"{cand.get('youtube_title')}\"")

    return {
        "approved": approved,
        "cost_so_far": llm_client.total_cost_usd,
        "llm_calls_count": llm_client.call_count,
    }
