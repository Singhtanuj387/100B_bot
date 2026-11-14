from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

console = Console()


class ReviewUI:
    """
    Modular Terminal UI powered by Rich for reviewing borderline videos.
    Designed with a clean interface so Web UI or Telegram bot adapters
    can replace this class in the future without touching LangGraph.
    """

    @staticmethod
    def display_auto_approved_summary(approved_videos: List[Dict[str, Any]]) -> None:
        """Display a brief summary table of auto-approved videos."""
        if not approved_videos:
            return

        table = Table(title="[bold green]✅ Auto-Approved Videos Summary[/bold green]", show_lines=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("Score", style="bold green", width=6)
        table.add_column("Source", style="cyan", width=10)
        table.add_column("Title / Proposed Caption", style="white")

        for idx, vid in enumerate(approved_videos, 1):
            score_str = f"{vid.get('score', 0.0):.1f}"
            source = vid.get("source", "unknown")
            title = vid.get("title", "")[:45]
            caption = vid.get("caption") or "(Pending synthesis)"
            display_text = f"{title}\n[dim italic]Caption: {caption}[/dim italic]"
            table.add_row(str(idx), score_str, source, display_text)

        console.print(table)
        console.print()

    @staticmethod
    def review_batch(
        borderline_videos: List[Dict[str, Any]],
        auto_approved_count: int,
    ) -> Dict[str, Any]:
        """
        Interactive review loop for borderline videos (score 5.0 to 7.9).
        Returns a dictionary with:
          - 'approved': list of approved video dicts (with optional modified caption)
          - 'rejected': list of rejected video dicts
          - 'interrupted': bool (if user requested to quit/pause)
        """
        console.print(Panel.fit(
            f"[bold yellow]HUMAN REVIEW REQUIRED[/bold yellow]\n"
            f"[green]{auto_approved_count} auto-approved[/green] | "
            f"[yellow]{len(borderline_videos)} borderline clips to review[/yellow]",
            border_style="yellow",
        ))

        approved: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        batch_approve_remaining = False

        for idx, vid in enumerate(borderline_videos, 1):
            if batch_approve_remaining:
                vid["human_decision"] = "approved"
                vid["caption_edited_by_human"] = False
                approved.append(vid)
                continue

            console.rule(f"[bold cyan]Candidate {idx} of {len(borderline_videos)}[/bold cyan]")
            
            post_id = vid.get("post_id", "unknown")
            source = vid.get("source", "unknown")
            title = vid.get("title", "Untitled")
            score = vid.get("score", 0.0)
            reason = vid.get("reason", "No reason provided")
            caption = vid.get("caption") or vid.get("original_caption") or title
            thumb_path = vid.get("thumbnail_path") or vid.get("video_path")

            details_text = Text()
            details_text.append(f"Post ID   : {post_id} ({source})\n", style="cyan")
            details_text.append(f"Title     : {title}\n", style="bold white")
            details_text.append(f"Score     : {score:.1f}/10\n", style="bold yellow")
            details_text.append(f"LLM Reason: {reason}\n", style="dim italic")
            details_text.append(f"Caption   : {caption}\n", style="magenta")
            details_text.append(f"Preview   : {thumb_path}\n", style="blue underline")

            console.print(Panel(details_text, title=f"Video #{idx}", border_style="cyan"))

            caption_edited = False
            while True:
                choice = Prompt.ask(
                    "[bold](A)pprove | (R)eject | (E)dit caption | (B)atch approve rest[/bold]",
                    choices=["a", "r", "e", "b", "A", "R", "E", "B"],
                    default="a",
                ).lower()

                if choice == "a":
                    vid["human_decision"] = "approved"
                    vid["caption_edited_by_human"] = caption_edited
                    if caption_edited:
                        vid["caption"] = caption
                    approved.append(vid)
                    console.print("[green]✔ Approved![/green]\n")
                    break

                elif choice == "r":
                    reject_reason = Prompt.ask("Reject reason (optional)", default="Human rejected")
                    vid["human_decision"] = "rejected"
                    vid["reject_reason"] = reject_reason
                    rejected.append(vid)
                    console.print("[red]✘ Rejected![/red]\n")
                    break

                elif choice == "e":
                    new_caption = Prompt.ask("Enter new caption (max ~8 words)", default=caption)
                    caption = new_caption.strip()
                    caption_edited = True
                    vid["caption"] = caption
                    vid["caption_edited_by_human"] = True
                    console.print(f"[magenta]Updated caption:[/magenta] {caption}")
                    # Keep prompt loop open so user can approve the edited caption
                    continue

                elif choice == "b":
                    vid["human_decision"] = "approved"
                    vid["caption_edited_by_human"] = caption_edited
                    if caption_edited:
                        vid["caption"] = caption
                    approved.append(vid)
                    batch_approve_remaining = True
                    console.print("[green]✔ Approved current and all remaining candidates![/green]\n")
                    break

        return {
            "approved": approved,
            "rejected": rejected,
        }

