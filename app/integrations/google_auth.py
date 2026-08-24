from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CLIENT_SECRET = BASE_DIR / "credentials" / "google_client_secret.json"
DEFAULT_TOKEN = BASE_DIR / "credentials" / "google_token.json"

SCOPES = (
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
)


def get_credentials(
    scopes: Iterable[str] = SCOPES,
    client_secret_path: str | Path | None = None,
    token_path: str | Path | None = None,
) -> Credentials:
    """Return cached Google OAuth credentials, starting desktop OAuth when needed."""
    client_secret = Path(
        client_secret_path
        or os.getenv("GOOGLE_CLIENT_SECRET_FILE", DEFAULT_CLIENT_SECRET)
    )
    token_file = Path(
        token_path
        or os.getenv("GOOGLE_TOKEN_FILE", DEFAULT_TOKEN)
    )

    token_file.parent.mkdir(parents=True, exist_ok=True)
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), list(scopes))

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        if not client_secret.exists():
            raise FileNotFoundError(
                f"Google OAuth client file not found: {client_secret}. "
                "Create a Desktop OAuth client and place the downloaded JSON there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret), list(scopes)
        )
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds
