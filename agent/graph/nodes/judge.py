import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List
import yaml
from loguru import logger

from graph.state import CurationState
from core.frames import extract_video_frames
from core.db import AgentDatabase
from llm.client import LLMClient
from llm.schemas import JudgeResult, BorderlineRankResult
from llm.prompts import (
    build_judge_system_prompt,
    build_judge_user_prompt,
    build_borderline_rank_system_prompt,
    build_borderline_rank_user_prompt,
)


def _load_few_shot_examples(config_dir: Path) -> str:
    """Load good and bad examples from config/judge_examples.yaml into prompt format."""
    examples_file = config_dir / "judge_examples.yaml"
    if not examples_file.exists():
        return "No specific examples provided. Follow standard evaluation rules."

    try:
        with open(examples_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        lines = ["GOOD VIRAL EXAMPLES:"]
        for ex in data.get("good_examples", [])[:5]:
            lines.append(f"- Title: '{ex.get('title')}' -> Score: {ex.get('funny_score')}/10 | Reason: {ex.get('reason')}")

        lines.append("\nBAD / REJECT EXAMPLES:")
        for ex in data.get("bad_examples", [])[:5]:
            lines.append(f"- Title: '{ex.get('title')}' -> Score: {ex.get('funny_score')}/10 | Reason: {ex.get('reason')}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Error reading judge examples: {e}")
        return ""


def evaluate_single_candidate(
    cand: Dict[str, Any],
    sys_prompt: str,
    llm_client: LLMClient,
    db: AgentDatabase,
    num_frames: int,
    target_width: int,
) -> Dict[str, Any]:
    """Worker task to evaluate one candidate clip with cache check and vision LLM."""
    file_hash = cand.get("file_hash") or cand.get("post_id")
    vid_path_str = cand.get("video_path")
    video_path = Path(vid_path_str) if vid_path_str else None
    thumb_path = video_path.parent / "thumbnail.jpg" if (video_path and video_path.is_file()) else None

    # 1. Check SQLite LLM Judge Cache
    if file_hash:
        cached = db.get_cached_judge(file_hash)
        if cached:
            logger.info(f"Using cached judge evaluation for {cand.get('post_id')} (score={cached['funny_score']:.1f})")
            cand["llm_score"] = cached["funny_score"]
            cand["is_english_or_no_speech"] = cached["is_english_or_no_speech"]
            cand["is_safe_for_ads"] = cached["is_safe_for_ads"]
            cand["has_visible_watermark"] = cached["has_visible_watermark"]
            cand["is_broadcast_or_movie"] = False
            cand["judge_reason"] = cached["reason"]
            cand["thumbnail_path"] = str(thumb_path) if thumb_path and thumb_path.is_file() else str(video_path or "")
            return cand

    # 2. Extract frames if not cached
    base64_frames = []
    if video_path and video_path.is_file():
        base64_frames = extract_video_frames(
            video_path=video_path,
            num_frames=num_frames,
            target_width=target_width,
            save_thumbnail_path=thumb_path,
        )
    cand["thumbnail_path"] = str(thumb_path) if thumb_path and thumb_path.is_file() else str(video_path or "")

    # 3. Build user prompt
    usr_prompt = build_judge_user_prompt(
        title=cand.get("title", ""),
        original_caption=cand.get("original_caption", ""),
        transcript=cand.get("transcript", ""),
        uploader=cand.get("uploader", "unknown"),
        source=cand.get("source", "unknown"),
    )

    # 4. Call Vision LLM
    try:
        res: JudgeResult = llm_client.call_structured(
            task_type="judge",
            system_prompt=sys_prompt,
            user_prompt=usr_prompt,
            schema=JudgeResult,
            images_base64=base64_frames,
        )

        cand["llm_score"] = res.funny_score
        cand["is_english_or_no_speech"] = res.is_english_or_no_speech
        cand["is_safe_for_ads"] = res.is_safe_for_ads
        cand["has_visible_watermark"] = res.has_visible_watermark
        cand["is_broadcast_or_movie"] = getattr(res, "is_broadcast_or_movie", False)
        cand["judge_reason"] = res.reason

        # 5. Persist to cache
        if file_hash:
            db.set_cached_judge(
                file_hash=file_hash,
                funny_score=res.funny_score,
                is_english_or_no_speech=res.is_english_or_no_speech,
                is_safe_for_ads=res.is_safe_for_ads,
                has_visible_watermark=res.has_visible_watermark,
                reason=res.reason,
            )

        logger.info(f"Judged {cand.get('post_id')}: {res.funny_score:.1f}/10 - '{res.reason}'")

    except Exception as e:
        logger.error(f"Judging failed for {cand.get('post_id')}: {e}")
        cand["status"] = "judge_failed"
        cand["judge_failed"] = True
        cand["llm_score"] = None
        cand["is_english_or_no_speech"] = None
        cand["is_safe_for_ads"] = None
        cand["has_visible_watermark"] = None
        cand["is_broadcast_or_movie"] = None
        cand["judge_reason"] = f"Judge call error: {str(e)[:100]}"

    return cand


def rank_borderline_candidates(
    borderline_candidates: List[Dict[str, Any]],
    llm_client: LLMClient,
    max_compare: int = 8,
) -> None:
    """
    Perform comparative side-by-side ranking for borderline clips (5.0 to 7.99).
    Calibrates their relative scores in a single comparative LLM call.
    """
    if len(borderline_candidates) < 2:
        return

    clips_to_compare = borderline_candidates[:max_compare]
    logger.info(f"Running comparative side-by-side ranking for {len(clips_to_compare)} borderline clips...")

    sys_prompt = build_borderline_rank_system_prompt()
    usr_prompt = build_borderline_rank_user_prompt(clips_to_compare)

    try:
        rank_result: BorderlineRankResult = llm_client.generate_structured(
            system_prompt=sys_prompt,
            user_prompt=usr_prompt,
            response_schema=BorderlineRankResult,
            task_type="borderline_rank",
        )

        cand_map = {c["post_id"]: c for c in clips_to_compare}
        for item in rank_result.ranked_clips:
            cand = cand_map.get(item.post_id)
            if cand:
                old_score = cand.get("llm_score", 6.0)
                cand["llm_score"] = item.calibrated_score
                cand["comparative_rank"] = item.rank
                cand["comparative_reason"] = item.comparative_reason
                logger.info(
                    f"Calibrated borderline [{cand['post_id']}]: {old_score:.1f} -> {item.calibrated_score:.1f} "
                    f"(Rank #{item.rank}: {item.comparative_reason})"
                )

    except Exception as e:
        logger.warning(f"Error during comparative borderline ranking: {e}. Keeping initial scores.")


def judge_node(
    state: CurationState,
    config: Dict[str, Any],
    llm_client: LLMClient,
    db: AgentDatabase,
) -> Dict[str, Any]:
    """
    Evaluate surviving candidates using Vision LLM with 4-6 keyframes.
    Runs up to 4 parallel workers paced by the rate limiter.
    Enforces a total time limit for the entire judge stage to prevent hanging.
    Performs comparative side-by-side ranking for borderline clips if time permits.
    """
    candidates = state.get("candidates", [])
    config_dir = Path(__file__).parent.parent.parent / "config"
    few_shot_text = _load_few_shot_examples(config_dir)
    sys_prompt = build_judge_system_prompt(few_shot_text)

    vision_cfg = config.get("vision", {})
    num_frames = int(vision_cfg.get("sample_frames", 3))
    target_width = int(vision_cfg.get("target_width", 360))
    parallel_workers = int(config.get("general", {}).get("judge_workers", 4))

    # Scalable time limit for the judge stage (scales with candidate count, e.g. 60s/clip)
    judge_start_time = time.time()
    cost_limits = config.get("cost_limits", {})
    num_cands = max(1, len(candidates))

    if "judge_timeout_seconds_per_clip" in cost_limits:
        sec_per_clip = float(cost_limits["judge_timeout_seconds_per_clip"])
        min_floor = float(cost_limits.get("judge_total_timeout_min_seconds", 300.0))
        max_judge_time = max(min_floor, float(num_cands) * sec_per_clip)
    elif "judge_total_timeout_seconds" in cost_limits or "judge_total_timeout_seconds" in config.get("general", {}):
        max_judge_time = float(
            cost_limits.get("judge_total_timeout_seconds")
            or config.get("general", {}).get("judge_total_timeout_seconds")
        )
    else:
        # Default scaling: 60s per clip, minimum 300s
        min_floor = float(cost_limits.get("judge_total_timeout_min_seconds", 300.0))
        max_judge_time = max(min_floor, float(num_cands) * 60.0)

    logger.info(
        f"--- [Node: JUDGE] Evaluating {len(candidates)} candidates "
        f"({parallel_workers} workers | total timeout limit: {max_judge_time:.0f}s [{max_judge_time/60:.1f}m]) ---"
    )

    judged_candidates = []
    if parallel_workers > 1 and len(candidates) > 1:
        executor = ThreadPoolExecutor(max_workers=parallel_workers)
        future_to_cand = {
            executor.submit(
                evaluate_single_candidate,
                c,
                sys_prompt,
                llm_client,
                db,
                num_frames,
                target_width,
            ): c
            for c in candidates
        }
        try:
            for fut in as_completed(future_to_cand):
                elapsed = time.time() - judge_start_time
                if elapsed >= max_judge_time:
                    logger.warning(
                        f"Judge stage reached total time limit of {max_judge_time:.0f}s! "
                        f"Aborting remaining tasks ({len(judged_candidates)}/{len(candidates)} completed)."
                    )
                    break
                remaining = max(1.0, max_judge_time - elapsed)
                try:
                    res_cand = fut.result(timeout=remaining)
                    judged_candidates.append(res_cand)
                except TimeoutError:
                    logger.warning(
                        f"Judge stage total time limit ({max_judge_time:.0f}s) reached waiting on task."
                    )
                    break
                except Exception as exc:
                    c = future_to_cand[fut]
                    logger.error(f"Worker exception for candidate {c.get('post_id')}: {exc}")
                    c["status"] = "judge_failed"
                    c["judge_failed"] = True
                    c["llm_score"] = None
                    c["judge_reason"] = f"Worker error: {str(exc)[:100]}"
                    judged_candidates.append(c)
        finally:
            for f in future_to_cand:
                if not f.done():
                    f.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
    else:
        for c in candidates:
            if (time.time() - judge_start_time) >= max_judge_time:
                logger.warning(
                    f"Judge stage reached total time limit of {max_judge_time:.0f}s! "
                    f"Skipping remaining candidates."
                )
                break
            res_cand = evaluate_single_candidate(
                c, sys_prompt, llm_client, db, num_frames, target_width
            )
            judged_candidates.append(res_cand)

    # Any candidate unjudged due to timeout is marked judge_failed (never default to neutral 5.0)
    judged_ids = {c.get("post_id") for c in judged_candidates}
    for c in candidates:
        if c.get("post_id") not in judged_ids:
            c["status"] = "judge_failed"
            c["judge_failed"] = True
            c["llm_score"] = None
            c["judge_reason"] = "Judge step total time limit reached"
            judged_candidates.append(c)

    # Failure Rate Gate: If more than 30% of calls fail, stop with a clear message
    total_candidates = len(candidates)
    failed_candidates = [
        c for c in judged_candidates
        if c.get("judge_failed") or c.get("status") == "judge_failed"
    ]
    failed_count = len(failed_candidates)

    if total_candidates > 0:
        failure_rate = failed_count / total_candidates
        max_failure_rate = float(
            cost_limits.get("judge_max_failure_rate")
            or config.get("general", {}).get("judge_max_failure_rate")
            or 0.30
        )
        if failure_rate > max_failure_rate:
            err_msg = (
                f"Judge failure rate exceeded threshold: {failed_count}/{total_candidates} "
                f"({failure_rate:.1%}) calls failed (max allowed: {max_failure_rate:.0%}). "
                f"Stopping run to prevent corrupt curation data or wasted API quota."
            )
            logger.error(err_msg)
            # Record failed clips in database so they can be retried in the next run
            for fc in failed_candidates:
                try:
                    db.record_judge_failed(fc)
                except Exception as ex:
                    logger.warning(f"Could not record judge_failed candidate in db: {ex}")
            raise RuntimeError(err_msg)

    # Comparative Borderline Ranking (only for successfully evaluated non-failed clips, if time permits)
    elapsed_after_judge = time.time() - judge_start_time
    if elapsed_after_judge < (max_judge_time - 15.0):
        borderline_candidates = [
            c for c in judged_candidates
            if not c.get("judge_failed")
            and c.get("status") != "judge_failed"
            and c.get("llm_score") is not None
            and 5.0 <= float(c.get("llm_score", 0.0)) <= 7.99
            and not c.get("is_broadcast_or_movie", False)
            and c.get("is_safe_for_ads", True)
        ]
        if len(borderline_candidates) >= 2:
            rank_borderline_candidates(borderline_candidates, llm_client)
    else:
        logger.info("Skipping borderline comparative ranking: judge total time limit nearly reached.")

    return {
        "candidates": judged_candidates,
        "cost_so_far": llm_client.total_cost_usd,
        "llm_calls_count": llm_client.call_count,
    }
