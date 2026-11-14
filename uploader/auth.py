import os
from pathlib import Path
from typing import Optional
from loguru import logger

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

from uploader.alerts import send_telegram_alert


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_SECRETS_DIR = Path(__file__).resolve().parent / "secrets"


def get_secrets_paths(secrets_dir: Optional[Path] = None) -> tuple[Path, Path]:
    """Return paths to (client_secret.json, token.json)."""
    sdir = Path(secrets_dir or DEFAULT_SECRETS_DIR).resolve()
    sdir.mkdir(parents=True, exist_ok=True)
    return sdir / "client_secret.json", sdir / "token.json"


def authenticate_interactive(secrets_dir: Optional[Path] = None) -> Credentials:
    """
    Run one-time interactive OAuth2 authorization in the user's browser.
    Saves token.json (including refresh token) upon completion.
    """
    client_secret_path, token_path = get_secrets_paths(secrets_dir)

    if not client_secret_path.is_file():
        # Check if there is any client_secret*.json file in secrets dir
        candidates = list(client_secret_path.parent.glob("client_secret*.json"))
        if candidates:
            client_secret_path = candidates[0]
        else:
            err = (
                f"OAuth client secret file not found at: {client_secret_path}\n"
                f"Please download your OAuth 2.0 Client ID (Desktop Application) from the Google Cloud Console, "
                f"rename it to 'client_secret.json', and place it in '{client_secret_path.parent}'."
            )
            logger.error(err)
            raise FileNotFoundError(err)

    logger.info(f"Initiating interactive OAuth flow using: {client_secret_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    logger.success(f"OAuth credentials successfully authorized and saved to: {token_path}")
    return creds


def get_authenticated_service(secrets_dir: Optional[Path] = None) -> Resource:
    """
    Load credentials from token.json and refresh them automatically if expired.
    If refresh fails (token revoked or expired), halts immediately, fires a Telegram alert,
    and never loops on auth errors.
    """
    client_secret_path, token_path = get_secrets_paths(secrets_dir)

    if not token_path.is_file():
        msg = (
            f"YouTube OAuth token not found at: {token_path}\n"
            f"Run 'python upload.py auth' to complete the one-time interactive authorization."
        )
        logger.error(msg)
        send_telegram_alert("AUTH_MISSING", msg)
        raise FileNotFoundError(msg)

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except Exception as e:
        msg = f"Failed to parse token file: {e}. Run 'python upload.py auth' to re-authenticate."
        logger.error(msg)
        send_telegram_alert("AUTH_CORRUPT", msg)
        raise RuntimeError(msg) from e

    if creds and creds.expired and creds.refresh_token:
        logger.info("YouTube OAuth access token expired; refreshing via refresh_token...")
        try:
            creds.refresh(Request())
            # Save updated token
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("OAuth token refreshed successfully.")
        except Exception as e:
            msg = f"OAuth token refresh failed (revoked or expired): {e}. Manual re-auth required: 'python upload.py auth'"
            logger.critical(msg)
            send_telegram_alert("AUTH_REVOKED", msg, {"error": str(e)})
            raise RuntimeError(msg) from e

    if not creds or not creds.valid:
        msg = "OAuth credentials are invalid. Please run 'python upload.py auth'."
        logger.critical(msg)
        send_telegram_alert("AUTH_INVALID", msg)
        raise RuntimeError(msg)

    return build("youtube", "v3", credentials=creds, cache_discovery=False)
