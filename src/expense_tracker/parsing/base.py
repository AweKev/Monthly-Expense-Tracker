from dataclasses import dataclass, field
from datetime import datetime

from ..models import Direction, TxnType


class NotATransaction(Exception):
    """Email is from the bank but is not a completed transaction (promo, OTP, failed payment)."""


class ParseFailure(Exception):
    """Looks like a transaction but we could not read it. Kept for re-parsing later."""


@dataclass
class ParsedTransaction:
    txn_type: TxnType
    direction: Direction
    amount: int
    occurred_at: datetime
    fee: int = 0
    counterparty: str = ""
    counterparty_account: str = ""
    reference: str = ""
    counterparty_id: str = ""  # stable ID, e.g. QRIS Merchant PAN
    acquirer: str = ""  # QRIS "Pengakuisisi": who runs the merchant's QR (GoPay, Bank Mandiri, ...)
    fields: dict[str, str] = field(default_factory=dict)  # raw extracted fields, for debugging
