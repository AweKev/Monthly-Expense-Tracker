"""Gmail API reader (read-only scope).

First run opens a browser for Google login and saves a token, after that
it refreshes silently. Setup steps are in the README.
"""

import base64
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from .base import RawMessage, message_from_bytes

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

log = logging.getLogger(__name__)

# Gmail limits how many API "units" one user can spend per minute (messages.get costs 5).
# A first sync can download hundreds of emails, so pace the requests and wait out rate limits.
GET_PAUSE_SECONDS = 0.1
RATE_LIMIT_WAIT_SECONDS = 60
RATE_LIMIT_MAX_RETRIES = 5


def _is_rate_limit(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 429:
        return True
    return status == 403 and "ratelimitexceeded" in str(exc).lower().replace(" ", "")


def execute_with_retry(request, sleep=time.sleep):
    """Run a Gmail API request, waiting and retrying when the per-minute quota is hit."""
    for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
        try:
            return request.execute(num_retries=3)  # built-in retry for 5xx / connection errors
        except Exception as exc:
            if not _is_rate_limit(exc) or attempt == RATE_LIMIT_MAX_RETRIES:
                raise
            log.warning("Gmail rate limit hit, waiting %ss (retry %s/%s)",
                        RATE_LIMIT_WAIT_SECONDS, attempt + 1, RATE_LIMIT_MAX_RETRIES)
            sleep(RATE_LIMIT_WAIT_SECONDS)


def get_credentials(credentials_path: str, token_path: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_file = Path(token_path)
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not Path(credentials_path).exists():
            raise FileNotFoundError(
                f"{credentials_path} not found. Download the OAuth client JSON from "
                "Google Cloud Console (see README, 'Gmail setup')."
            )
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    return creds


class GmailSource:
    name = "gmail"

    def __init__(self, credentials_path: str, token_path: str, query: str, tz: ZoneInfo):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.query = query
        self.tz = tz
        self._service = None

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            creds = get_credentials(self.credentials_path, self.token_path)
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def _build_query(self, since: datetime | None) -> str:
        if since is None:
            return self.query
        # Gmail's after: works on dates; go back one day and let dedup handle overlap.
        day = (since - timedelta(days=1)).strftime("%Y/%m/%d")
        return f"{self.query} after:{day}"

    def list_ids(self, since: datetime | None = None) -> Iterator[str]:
        request = self.service.users().messages().list(userId="me", q=self._build_query(since), maxResults=500)
        while request is not None:
            response = execute_with_retry(request)
            for item in response.get("messages", []):
                yield item["id"]
            request = self.service.users().messages().list_next(request, response)

    def get(self, gmail_id: str) -> RawMessage:
        request = self.service.users().messages().get(userId="me", id=gmail_id, format="raw")
        response = execute_with_retry(request)
        raw = base64.urlsafe_b64decode(response["raw"].encode("ascii"))
        # Use Gmail's own id so dedup is stable even if Message-ID headers repeat.
        return message_from_bytes(raw, source=self.name, tz=self.tz, message_id=f"gmail:{gmail_id}")

    def fetch(self, since: datetime | None = None, skip_ids: set[str] | None = None) -> Iterator[RawMessage]:
        skip_ids = skip_ids or set()
        for gmail_id in self.list_ids(since):
            if f"gmail:{gmail_id}" in skip_ids:
                continue
            yield self.get(gmail_id)
            time.sleep(GET_PAUSE_SECONDS)
