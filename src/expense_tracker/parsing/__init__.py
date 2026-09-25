from ..sources.base import RawMessage
from . import mandiri
from .base import NotATransaction, ParsedTransaction, ParseFailure

__all__ = ["parse_message", "NotATransaction", "ParseFailure", "ParsedTransaction"]


def parse_message(msg: RawMessage) -> ParsedTransaction:
    """Pick the right bank parser. Only Mandiri for now; other banks plug in here."""
    return mandiri.parse(msg)
