from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

REPO_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = REPO_DIR.parent
DEFAULT_SERVICE_ACCOUNT = WORKSPACE_DIR / "credentials" / "google_indexing_service_account.json"
INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"
PUBLISH_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"
METADATA_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications/metadata"

NotificationType = Literal["URL_UPDATED", "URL_DELETED"]


class IndexingClient:
    """Google Indexing API client using a service-account credential."""

    def __init__(
        self,
        service_account_path: str | Path | None = None,
        session: AuthorizedSession | None = None,
        timeout: float = 30,
    ) -> None:
        credential_path = Path(
            service_account_path
            or os.getenv("GOOGLE_INDEXING_SERVICE_ACCOUNT_FILE", DEFAULT_SERVICE_ACCOUNT)
        )
        self.timeout = timeout
        self.credential_path = credential_path

        if session is not None:
            self.session = session
            return

        if not credential_path.exists():
            raise FileNotFoundError(
                f"Google Indexing service-account file not found: {credential_path}"
            )

        credentials = service_account.Credentials.from_service_account_file(
            str(credential_path),
            scopes=[INDEXING_SCOPE],
        )
        self.session = AuthorizedSession(credentials)

    def service_account_email(self) -> str:
        if not self.credential_path.exists():
            raise FileNotFoundError(self.credential_path)
        import json
        data = json.loads(self.credential_path.read_text(encoding="utf-8"))
        return str(data.get("client_email", ""))

    def publish(
        self,
        url: str,
        notification_type: NotificationType = "URL_UPDATED",
    ) -> dict[str, Any]:
        if notification_type not in {"URL_UPDATED", "URL_DELETED"}:
            raise ValueError("notification_type must be URL_UPDATED or URL_DELETED")

        response = self.session.post(
            PUBLISH_ENDPOINT,
            json={"url": url, "type": notification_type},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def metadata(self, url: str) -> dict[str, Any]:
        response = self.session.get(
            METADATA_ENDPOINT,
            params={"url": url},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
