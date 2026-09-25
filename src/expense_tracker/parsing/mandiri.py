"""Parser for Mandiri (Livin') transaction emails.

Verified on real QRIS and GoPay top-up emails (see tests/fixtures/). Other types
(transfer, money in, bills, ATM) still use the generic layout until samples arrive.
Every tweak should come with a fixture in tests/fixtures/ so nothing regresses.
"""

import re

from ..models import Direction, TxnType
from ..sources.base import RawMessage
from .base import NotATransaction, ParsedTransaction, ParseFailure
from .fields import extract_fields, find_amount_in_text, html_to_text, parse_amount, parse_datetime

# Checked in order, first match wins.
TYPE_KEYWORDS: list[tuple[TxnType, tuple[str, ...]]] = [
    (TxnType.TRANSFER_IN, ("dana masuk", "transfer masuk", "uang masuk", "menerima dana", "anda menerima",
                           "incoming transfer", "terima transfer")),
    (TxnType.ATM_WITHDRAWAL, ("tarik tunai", "penarikan tunai", "cash withdrawal", "tarik tunai tanpa kartu")),
    (TxnType.TOPUP, ("top up", "top-up", "topup", "isi saldo", "isi ulang saldo")),
    (TxnType.QRIS, ("qris",)),
    (TxnType.BILL, ("pln", "token listrik", "pulsa", "paket data", "tagihan", "bpjs", "pdam", "indihome",
                    "telkom", "pascabayar", "prabayar")),
    (TxnType.TRANSFER_OUT, ("transfer", "bi-fast", "bifast", "kirim uang")),
    (TxnType.PURCHASE, ("pembayaran", "pembelian", "payment", "purchase", "belanja")),
]

# Body markers that beat the subject. Livin' QRIS emails are titled just "Pembayaran Berhasil!".
STRONG_BODY_MARKERS: list[tuple[TxnType, tuple[str, ...]]] = [
    (TxnType.QRIS, ("no. ref. qris", "transaksi anda dengan qr")),
]

NOT_TRANSACTION_HINTS = ("kode otp", "one time password", "promo", "newsletter", "e-statement",
                         "rekening koran", "perubahan data", "login", "aktivasi")
FAILED_HINTS = ("transaksi gagal", "tidak berhasil", "dibatalkan", "failed")

EWALLETS = ("GOPAY", "OVO", "DANA", "SHOPEEPAY", "LINKAJA", "FLIP", "JAGO", "SEABANK")


def detect_type(haystack: str) -> TxnType:
    text = haystack.lower()
    for txn_type, keywords in TYPE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return txn_type
    return TxnType.OTHER


def _find_ewallet(text: str) -> str:
    upper = text.upper()
    for wallet in EWALLETS:
        if re.search(rf"\b{wallet}\b", upper):
            return wallet
    return ""


def _masked(value: str) -> str:
    """Keep a card sub line only if it looks like an account ("****0802"), not a location ("KARAWANG - ID")."""
    return value if re.search(r"\*{2,}\d+|\d{4,}", value) else ""


def parse(msg: RawMessage) -> ParsedTransaction:
    text = msg.body_text or html_to_text(msg.body_html)
    if not text and not msg.body_html:
        raise NotATransaction("empty email")

    haystack = f"{msg.subject}\n{text}"
    lower = haystack.lower()

    fields = extract_fields(msg.body_html, text)

    # Only trust subject and the status field here: footers often say "jika transaksi tidak berhasil...".
    status_text = f"{msg.subject} {fields.get('status', '')}".lower()
    if any(hint in status_text for hint in FAILED_HINTS):
        raise NotATransaction("transaction failed or cancelled")

    amount = parse_amount(fields.get("amount", "")) or parse_amount(fields.get("total", ""))
    if amount is None:
        amount = find_amount_in_text(text)

    if amount is None:
        if any(hint in lower for hint in NOT_TRANSACTION_HINTS):
            raise NotATransaction("no amount, looks like a non-transaction email")
        raise ParseFailure(f"no amount found (fields: {sorted(fields)})")
    if amount <= 0:
        raise ParseFailure(f"amount is {amount}")

    fee = parse_amount(fields.get("fee", "")) or 0
    total = parse_amount(fields.get("total", ""))
    if "amount" not in fields and total and fee and total > fee:
        amount = total - fee  # only a total was given, back out the fee

    # Subject and the "jenis transaksi" field are more reliable than body text (footers mention everything).
    txn_type = TxnType.OTHER
    for marker_type, markers in STRONG_BODY_MARKERS:
        if any(marker in lower for marker in markers):
            txn_type = marker_type
            break
    if txn_type == TxnType.OTHER:
        txn_type = detect_type(f"{msg.subject} {fields.get('transaction_type', '')}")
    if txn_type == TxnType.OTHER:
        txn_type = detect_type(text)
    direction = Direction.IN if txn_type == TxnType.TRANSFER_IN else Direction.OUT

    if direction == Direction.IN:
        counterparty = fields.get("sender_name", "") or fields.get("counterparty", "")
    else:
        counterparty = fields.get("counterparty", "")
    if txn_type == TxnType.TOPUP:
        # "GoPay Customer" -> "GOPAY", so all top-ups to one wallet group together.
        counterparty = _find_ewallet(counterparty) or counterparty or _find_ewallet(haystack)
    if txn_type == TxnType.ATM_WITHDRAWAL and not counterparty:
        counterparty = "Tarik Tunai"

    occurred_at = None
    if "datetime" in fields:
        occurred_at = parse_datetime(fields["datetime"], fields.get("time", fields["datetime"]))
    if occurred_at is None and "date" in fields:
        occurred_at = parse_datetime(fields["date"], fields.get("time", ""))
    if occurred_at is None:
        occurred_at = msg.received_at

    return ParsedTransaction(
        txn_type=txn_type,
        direction=direction,
        amount=amount,
        fee=fee,
        counterparty=counterparty.strip()[:255],
        counterparty_account=(fields.get("counterparty_account") or _masked(fields.get("counterparty_sub", "")))[:64],
        reference=fields.get("reference", "")[:128],
        counterparty_id=re.sub(r"\s+", "", fields.get("counterparty_id", ""))[:64],
        acquirer=fields.get("acquirer", "")[:64],
        occurred_at=occurred_at,
        fields=fields,
    )
