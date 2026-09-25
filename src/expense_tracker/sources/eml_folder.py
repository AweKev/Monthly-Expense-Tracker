"""Read .eml files from a folder. Used for sample emails, tests and the synthetic demo."""

from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from .base import RawMessage, message_from_bytes


class EmlFolderSource:
    name = "eml"

    def __init__(self, folder: str | Path, tz: ZoneInfo):
        self.folder = Path(folder)
        self.tz = tz

    def fetch(self, since: datetime | None = None) -> Iterator[RawMessage]:
        for path in sorted(self.folder.rglob("*.eml")):
            msg = message_from_bytes(path.read_bytes(), source=self.name, tz=self.tz)
            if since and msg.received_at < since:
                continue
            yield msg
