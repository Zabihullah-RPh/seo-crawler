from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
REPO_CREDENTIALS = REPO_ROOT / "credentials"
WORKSPACE_CREDENTIALS = WORKSPACE_ROOT / "credentials"

DEFAULT_CLIENT_SECRET = WORKSPACE_CREDENTIALS / "google_client_secret.json"
DEFAULT_TOKEN = WORKSPACE_CREDENTIALS / "google_token.json"

OPENID_SCOPE = "openid"
SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

SCOPES = (OPENID_SCOPE, SEARCH_CONSOLE_SCOPE, ANALYTICS_SCOPE)


def _credential_path(env_name: str, filename: str) -> Path:
    override = os.getenv(env_name)
    if override:
        return Path(override)

    workspace_path = WORKSPACE_CREDENTIALS / filename
    if workspace_path.exists():
        return workspace_path

    return REPO_CREDENTIALS / filename


def get_credentials(
    scopes: Iterable[str] = SCOPES,
    client_secret_path: str | Path | None = None,
    token_path: str | Path | None = None,
) -> Credentials:
    """Return valid user OAuth credentials, refreshing or re-authorizing when needed."""
    requested_scopes = tuple(dict.fromkeys(scopes))
    client_secret = Path(client_secret_path) if client_secret_path else _credential_path(
        "GOOGLE_CLIENT_SECRET_FILE", "google_client_secret.json"
    )
    token_file = Path(token_path) if token_path else _credential_path(
        "GOOGLE_TOKEN_FILE", "google_token.json"
    )

    token_file.parent.mkdir(parents=True, exist_ok=True)
    creds: Credentials | None = None

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_file), list(requested_scopes)
            )
        except (ValueError, KeyError, TypeError):
            creds = None

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if creds and creds.valid and hasattr(creds, "has_scopes"):
        if not creds.has_scopes(requested_scopes):
            creds = None

    if not creds or not creds.valid:
        if not client_secret.exists():
            raise FileNotFoundError(
                f"Google OAuth client file not found: {client_secret}."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret), list(requested_scopes)
        )
        creds = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
        )

    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds
