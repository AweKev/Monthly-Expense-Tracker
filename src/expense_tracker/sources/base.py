"""Shared message type and MIME decoding used by every email source."""

import email
import hashlib
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Iterator, Protocol
from zoneinfo import ZoneInfo


@dataclass
class RawMessage:
    message_id: str
    source: str
    received_at: datetime  # naive, local time
    sender: str
    subject: str
    body_html: str
    body_text: str


class EmailSource(Protocol):
    name: str

    def fetch(self, since: datetime | None = None) -> Iterator[RawMessage]: ...


def _local_naive(dt: datetime, tz: ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(tz).replace(tzinfo=None)


def message_from_bytes(raw: bytes, source: str, tz: ZoneInfo, message_id: str | None = None) -> RawMessage:
    """Turn an RFC 822 message (Gmail 'raw' format or a .eml file) into a RawMessage."""
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]

    html, text = "", ""
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", errors="replace")
        if ctype == "text/html" and not html:
            html = content
        elif ctype == "text/plain" and not text:
            text = content

    date_header = msg.get("Date")
    try:
        received = parsedate_to_datetime(date_header) if date_header else datetime.now(tz)
    except (TypeError, ValueError):
        received = datetime.now(tz)

    if not message_id:
        message_id = (msg.get("Message-ID") or "").strip("<> ")
    if not message_id:
        message_id = "sha1:" + hashlib.sha1(raw).hexdigest()

    return RawMessage(
        message_id=message_id,
        source=source,
        received_at=_local_naive(received, tz),
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body_html=html,
        body_text=text,
    )
