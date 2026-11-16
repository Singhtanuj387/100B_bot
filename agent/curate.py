#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║             AUTOMATED VIDEO CURATION AGENT — PHASE 3                 ║
║                                                                      ║
║  Orchestrates video curation for tomorrow using LangGraph:           ║
║    - Multi-loop discovery planning                                   ║
║    - Phase 2 Scraper ingestion                                       ║
║    - Zero-cost filters & Whisper audio VAD check                     ║
║    - Claude Vision judging with 4-6 keyframes                        ║
║    - Human-in-the-loop review with LangGraph interrupt & Rich UI    ║
║    - Viral caption generation (max 8 words)                          ║
║    - Phase 1 NVENC video editor integration                          ║
║    - 24-slot hourly schedule generation (schedule.json)              ║
╚══════════════════════════════════════════════════════════════════════╝

Usage:
    python curate.py --target 24 --date tomorrow
    python curate.py --resume
    python curate.py --dry-run
"""

import sys
import os
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import yaml
from dotenv import load_dotenv
from loguru import logger
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add agent directory to sys.path
BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

# Load .env from current working dir, agent dir, or project root
for env_candidate in [Path.cwd() / ".env", BASE_DIR / ".env", BASE_DIR.parent / ".env"]:
    if env_candidate.is_file():
        load_dotenv(dotenv_path=env_candidate)
load_dotenv()

from graph.build_graph import build_curation_graph
from graph.state import CurationState
from llm.client import LLMClient
from tools.scraper_tool import ScraperTool
from tools.editor_tool import EditorTool
from core.db import AgentDatabase
from core.review_ui import ReviewUI

console = Console()


def load_config(config_path: Path) -> dict:
    """Load agent configuration file and sync durations from scraper config (single source of truth)."""
    if not config_path.exists():
        logger.error(f"Config file not found at {config_path}")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # Synchronize duration bounds from scraper/config.yaml (single source of truth)
    scraper_cfg_path = config_path.parent.parent / "scraper" / "config.yaml"
    if scraper_cfg_path.is_file():
        try:
            with open(scraper_cfg_path, "r", encoding="utf-8") as sf:
                sc_cfg = yaml.safe_load(sf) or {}
            sc_filters = sc_cfg.get("filters", {})
            if "min_duration_seconds" in sc_filters:
                min_sec = float(sc_filters["min_duration_seconds"])
                cfg.setdefault("filters", {})["min_duration_seconds"] = min_sec
                cfg["filters"]["min_duration"] = min_sec
            if "max_duration_seconds" in sc_filters:
                max_sec = float(sc_filters["max_duration_seconds"])
                cfg.setdefault("filters", {})["max_duration_seconds"] = max_sec
                cfg["filters"]["max_duration"] = max_sec
        except Exception as e:
            logger.debug(f"Could not sync duration config from scraper: {e}")

    return cfg


def resolve_date_arg(date_str: str, timezone_name: str) -> str:
    """Resolve '--date tomorrow' or explicit YYYY-MM-DD into a date string."""
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)

    if date_str.lower() in ("tomorrow", "next_day"):
        target_dt = now + timedelta(days=1)
        return target_dt.strftime("%Y-%m-%d")
    elif date_str.lower() in ("today", "now"):
        return now.strftime("%Y-%m-%d")

    # Validate explicit YYYY-MM-DD format
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        logger.warning(f"Unrecognized date '{date_str}'; defaulting to tomorrow.")
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")


def print_final_summary(
    state: dict,
    target_count: int,
    target_date: str,
    cost_usd: float,
    llm_calls: int,
    dry_run: bool,
    schedule_path: Path,
) -> None:
    """Display final execution summary table using Rich."""
    approved_count = len(state.get("approved", []))
    rejected_count = len(state.get("rejected", []))
    scheduled_count = len(state.get("final_schedule", []))
    loops_used = state.get("loop_count", 1)

    table = Table(title="[bold green]🏆 CURATION RUN COMPLETED[/bold green]", show_header=True)
    table.add_column("Metric", style="bold white", width=28)
    table.add_column("Value", style="cyan")

    table.add_row("Target Date", f"{target_date}")
    table.add_row("Target Goal", f"{target_count} videos (1 per hour)")
    table.add_row("Curation Loops Used", f"{loops_used}")
    table.add_row("Total Approved Clips", f"{approved_count}")
    table.add_row("Total Rejected Clips", f"{rejected_count}")
    failed_count = len(state.get("judge_failed", []))
    if failed_count:
        table.add_row("Judge Failures (Retrying)", f"[yellow]{failed_count}[/yellow]")
    table.add_row("Final Scheduled Slots", f"[bold green]{scheduled_count} / {target_count}[/bold green]")
    table.add_row("LLM API Calls", f"{llm_calls}")
    table.add_row("Total LLM Cost (USD)", f"${cost_usd:.4f}")
    table.add_row("Execution Mode", "[yellow]DRY RUN[/yellow]" if dry_run else "[green]PRODUCTION[/green]")

    if schedule_path.exists() or dry_run:
        table.add_row("Schedule JSON", f"{schedule_path}")

    console.print()
    console.print(table)
    console.print()


def run_curation_graph(app, initial_state_or_input, graph_config):
    """
    Executes the LangGraph curation pipeline.
    If the graph halts at a human review interrupt, handles the ReviewUI
    in the terminal outside the graph and resumes execution via Command(resume=...).
    """
    state = app.invoke(initial_state_or_input, graph_config)

    while state.get("__interrupt__"):
        interrupt_item = state["__interrupt__"][0]
        payload = interrupt_item.value

        borderline = payload.get("borderline", [])
        auto_approved = payload.get("auto_approved", [])
        auto_approved_count = payload.get("auto_approved_count", len(auto_approved))

        logger.info(f"Human review required for {len(borderline)} borderline candidate(s).")

        # 1. Display summary of auto-approved clips
        if auto_approved:
            ReviewUI.display_auto_approved_summary(auto_approved)

        # 2. Run the interactive review loop outside the graph
        review_outcome = ReviewUI.review_batch(
            borderline_videos=borderline,
            auto_approved_count=auto_approved_count,
        )

        # 3. Resume the graph with user decisions
        logger.info("Resuming graph execution with human review decisions...")
        state = app.invoke(Command(resume=review_outcome), graph_config)

    return state


def compute_and_save_run_stats(db, final_state: dict, target_date: str) -> None:
    """Compute curation pipeline metrics and record to run_stats table."""
    raw_cands = final_state.get("raw_candidates", [])
    dropped_titles = final_state.get("dropped_titles", [])
    rejected = final_state.get("rejected", [])
    approved = final_state.get("approved", [])
    borderline = final_state.get("borderline", [])
    schedule = final_state.get("final_schedule", [])

    dropped_prescreen = len(dropped_titles)

    reason_counts = {
        "not_funny": 0,
        "non_english": 0,
        "watermark": 0,
        "duration": 0,
        "tv_film": 0,
        "other": 0,
    }

    dropped_duration = 0
    dropped_filters = 0
    rejected_judge = 0

    for r in rejected:
        reason_text = (r.get("reason") or r.get("decision_reason") or "").lower()
        if any(w in reason_text for w in ("not funny", "funny_score", "score", "threshold", "unfunny")):
            reason_counts["not_funny"] += 1
            rejected_judge += 1
        elif any(w in reason_text for w in ("english", "language", "speech")):
            reason_counts["non_english"] += 1
            dropped_filters += 1
        elif any(w in reason_text for w in ("watermark", "logo", "channel")):
            reason_counts["watermark"] += 1
            rejected_judge += 1
        elif any(w in reason_text for w in ("duration", "seconds", "too long", "too short", "length")):
            reason_counts["duration"] += 1
            dropped_duration += 1
            dropped_filters += 1
        elif any(w in reason_text for w in ("tv", "film", "movie", "broadcast")):
            reason_counts["tv_film"] += 1
            rejected_judge += 1
        else:
            reason_counts["other"] += 1
            if any(w in reason_text for w in ("filter", "resolution", "word", "low res")):
                dropped_filters += 1
            else:
                rejected_judge += 1

    source_stats = {}
    for a in approved:
        src = a.get("subreddit") or a.get("source") or "unknown"
        if src not in source_stats:
            source_stats[src] = {"judged": 0, "accepted": 0}
        source_stats[src]["accepted"] += 1
        source_stats[src]["judged"] += 1

    for r in rejected:
        src = r.get("subreddit") or r.get("source") or "unknown"
        if src in source_stats:
            source_stats[src]["judged"] += 1
        elif "funny" in (r.get("reason") or "").lower() or r.get("score"):
            if src not in source_stats:
                source_stats[src] = {"judged": 0, "accepted": 0}
            source_stats[src]["judged"] += 1

    approved_count = len(approved)
    selected_count = len(schedule)
    reserve_added = max(0, approved_count - selected_count)
    borderline_count = len(borderline)
    judged_count = approved_count + rejected_judge + borderline_count

    found_count = len(raw_cands) if raw_cands else (dropped_prescreen + len(rejected) + approved_count)
    downloaded_count = max(0, found_count - dropped_prescreen)

    stats = {
        "target_date": target_date,
        "found": found_count,
        "dropped_prescreen": dropped_prescreen,
        "dropped_duration": dropped_duration,
        "downloaded": downloaded_count,
        "dropped_filters": dropped_filters,
        "judged": judged_count,
        "rejected_judge": rejected_judge,
        "borderline": borderline_count,
        "approved": approved_count,
        "selected": selected_count,
        "reserve_added": reserve_added,
        "reject_reasons_json": reason_counts,
        "source_stats_json": source_stats,
    }

    try:
        db.record_run_stats(stats)
        logger.info(f"Recorded run_stats for {target_date}: {found_count} found, {len(rejected)} rejected, {approved_count} accepted.")
    except Exception as e:
        logger.warning(f"Could not record run_stats: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Phase 3 Curation Agent — Automated Video Selection & Scheduling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", type=int, default=6, help="Target number of scheduled videos (default: 6)")
    parser.add_argument("--date", type=str, default="tomorrow", help="Target date ('tomorrow' or 'YYYY-MM-DD')")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying files or calling paid APIs")
    parser.add_argument("--no-review", action="store_true", help="Unattended mode: auto-resolve borderline clips without interactive terminal prompts")
    parser.add_argument(
        "--borderline-policy",
        choices=["reject", "approve_top", "review"],
        default=None,
        help="Policy for borderline clips: reject, approve_top, or review (default: review when interactive, approve_top when --no-review)",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from previous SQLite checkpoint")
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--workers", type=int, default=None, help="Parallel video editing workers")
    parser.add_argument("--recursion-limit", type=int, default=None, help="LangGraph recursion limit (default: 100)")

    args = parser.parse_args()

    # Enforce API key for live runs (protect against approving junk in mock mode)
    has_gemini = bool(
        (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_API_KEY") or "").strip("'\" ")
    )
    has_anthropic = bool((os.getenv("ANTHROPIC_API_KEY") or "").strip("'\" "))

    if not args.dry_run and not (has_gemini or has_anthropic):
        logger.error("=" * 60)
        logger.error("FATAL ERROR: LLM API Key is missing!")
        logger.error("A real curation run (without --dry-run) requires GEMINI_API_KEY, GOOGLE_API_KEY, or ANTHROPIC_API_KEY in your .env file.")
        logger.error("Please add your key to .env:")
        logger.error("    GEMINI_API_KEY=AQ.Ab8... (or your Google AI Studio / Gemini key)")
        logger.error("    ANTHROPIC_API_KEY=sk-ant-api03-...")
        logger.error("Or run in simulation mode with:")
        logger.error("    python curate.py --dry-run")
        logger.error("=" * 60)
        sys.exit(1)

    # 1. Load Config & Environment
    config_path = BASE_DIR / args.config
    config = load_config(config_path)

    # Resolve Date & Timezone
    tz_name = config.get("timezone", "America/New_York")
    target_date = resolve_date_arg(args.date, tz_name)

    logger.info("============================================================")
    logger.info("  PHASE 3: LANGGRAPH VIDEO CURATION AGENT")
    logger.info(f"  Target: {args.target} clips | Date: {target_date} ({tz_name}) | Dry-run: {args.dry_run}")
    logger.info("============================================================")

    # 2. Setup SQLite Checkpointer & Database
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_db_path = data_dir / "checkpoints.db"
    agent_db_path = data_dir / "agent.db"

    checkpointer_conn = sqlite3.connect(str(checkpoints_db_path), check_same_thread=False)
    db = AgentDatabase(agent_db_path)

    # 3. Initialize Core Tools & LLM Client
    parallel_edits = args.workers or config.get("parallel_edits", 2)
    llm_client = LLMClient(config=config, dry_run=args.dry_run, db_handler=db)
    scraper_tool = ScraperTool()
    editor_tool = EditorTool(max_workers=parallel_edits)

    # 4. Build LangGraph
    app = build_curation_graph(
        config=config,
        llm_client=llm_client,
        scraper_tool=scraper_tool,
        editor_tool=editor_tool,
        db=db,
        checkpointer_conn=checkpointer_conn,
    )

    thread_id = f"curate_{target_date}"
    recursion_limit = args.recursion_limit or config.get("recursion_limit", 100)
    graph_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit,
    }

    # Resolve borderline policy default: review if interactive, approve_top if --no-review
    resolved_borderline_policy = args.borderline_policy or ("approve_top" if args.no_review else "review")

    # 5. Initialize or Resume State
    if args.resume:
        logger.info(f"Resuming graph execution for thread: {thread_id} (recursion_limit: {recursion_limit})...")
        final_state = run_curation_graph(app, None, graph_config)
    else:
        initial_state: CurationState = {
            "target_count": args.target,
            "target_date": target_date,
            "loop_count": 0,
            "max_loops": config.get("max_loops", 3),
            "spare_buffer": config.get("spare_buffer", 2),
            "candidates": [],
            "approved": [],
            "borderline": [],
            "rejected": [],
            "sources_tried": [],
            "plan": {},
            "cost_so_far": 0.0,
            "llm_calls_count": 0,
            "backfill_performed": False,
            "trigger_extra_loop": False,
            "human_interrupted": False,
            "dry_run": args.dry_run,
            "no_review": args.no_review,
            "borderline_policy": resolved_borderline_policy,
            "discovered": False,
            "selected_sources": [],
            "raw_candidates": [],
            "vetted_candidates": [],
            "dropped_titles": [],
            "edited_videos": [],
            "final_schedule": [],
        }
        final_state = run_curation_graph(app, initial_state, graph_config)

    # 6. Final Summary Report
    schedule_path = BASE_DIR / "data" / "edited" / target_date / "schedule.json"
    print_final_summary(
        state=final_state,
        target_count=args.target,
        target_date=target_date,
        cost_usd=llm_client.total_cost_usd,
        llm_calls=llm_client.call_count,
        dry_run=args.dry_run,
        schedule_path=schedule_path,
    )

    # 6.5 Record Run Stats (Phase 5 Dashboard Instrumentation)
    compute_and_save_run_stats(db=db, final_state=final_state, target_date=target_date)

    # 7. Optional Telegram Notification
    try:
        from core.telegram import notify_curation_complete
        notify_curation_complete(
            target_date=target_date,
            scheduled_count=len(final_state.get("final_schedule", [])),
            target_count=args.target,
            approved_count=len(final_state.get("approved", [])),
            rejected_count=len(final_state.get("rejected", [])),
            dry_run=args.dry_run,
            schedule_path=str(schedule_path) if schedule_path.exists() else None,
        )
    except Exception as e:
        logger.debug(f"Telegram notification skipped: {e}")

    checkpointer_conn.close()


if __name__ == "__main__":
    main()
