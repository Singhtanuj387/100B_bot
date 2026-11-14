import os
from typing import Optional, List, Dict, Any
from loguru import logger
import httpx


def send_telegram_message(
    text: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """
    Send an automated notification message to a Telegram chat / channel.
    Credentials can be passed explicitly or read from TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
    Returns True if sent successfully, False otherwise (fails silently if unconfigured).
    """
    token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip("'\" ")
    cid = (chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip("'\" ")

    if not token or not cid:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("Telegram notification sent successfully.")
                return True
            else:
                logger.warning(f"Telegram notification returned status {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.warning(f"Failed to deliver Telegram notification: {e}")
        return False


def notify_curation_complete(
    target_date: str,
    scheduled_count: int,
    target_count: int,
    approved_count: int,
    rejected_count: int,
    dry_run: bool = False,
    schedule_path: Optional[str] = None,
) -> bool:
    """Send formatted curation completion summary to Telegram."""
    mode_str = "DRY-RUN (Simulated)" if dry_run else "PRODUCTION (Scheduled)"
    msg = (
        f"<b>🏆 Video Curation Run Completed</b>\n\n"
        f"<b>Target Date:</b> <code>{target_date}</code>\n"
        f"<b>Mode:</b> {mode_str}\n"
        f"<b>Scheduled:</b> <b>{scheduled_count} / {target_count}</b> slots\n"
        f"<b>Approved:</b> {approved_count} videos\n"
        f"<b>Rejected:</b> {rejected_count} clips\n"
    )
    if schedule_path:
        msg += f"\n📁 <i>{schedule_path}</i>"

    return send_telegram_message(msg)
