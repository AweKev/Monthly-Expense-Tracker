"""Rule-based categorizer v0 and kind detection (expense / income / internal).

Order: user rules from the DB (phase 2 bot corrections) > seed rules below > default per transaction type.
Phase 5 replaces the fallback with an ML model trained on user corrections.
"""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import CategoryRule, Counterparty, Direction, Kind, PartyType, TxnType
from .parsing.base import ParsedTransaction

MAKAN = "Makan & Minum"
BELANJA = "Belanja Harian"
ONLINE = "Belanja Online"
TRANSPORT = "Transportasi"
TAGIHAN = "Tagihan & Utilitas"
PULSA = "Pulsa & Internet"
HIBURAN = "Hiburan & Langganan"
KESEHATAN = "Kesehatan"
PENDIDIKAN = "Pendidikan"
TUNAI = "Tunai"
TRANSFER_ORANG = "Transfer ke Orang"
TOPUP = "Top-up E-wallet"
INTERNAL = "Transfer Internal"
PEMASUKAN = "Pemasukan"
LAINNYA = "Lainnya"

# (regex on counterparty, category). Case-insensitive, first match wins.
SEED_RULES: list[tuple[str, str]] = [
    (r"indomaret|alfamart|alfamidi|superindo|hypermart|lawson|family ?mart|circle ?k|toko|minimarket|madura", BELANJA),
    (r"kopi|coffee|cafe|kafe|starbucks|janji jiwa|kenangan|fore|mixue|warung|warteg|resto|bakso|mie|ayam|"
     r"geprek|nasi|sate|mcd|mcdonald|kfc|burger|pizza|solaria|hokben|richeese|gofood|grabfood|shopeefood|"
     r"es teh|chatime|boba|bakery|roti|makan", MAKAN),
    (r"gojek|goride|gocar|grab|maxim|indrive|kai|kereta|krl|commuter|transjakarta|mrt|lrt|pertamina|shell|"
     r"spbu|bensin|parkir|tol|e-?toll|traveloka|tiket\.com|pegipegi|garuda|lion|citilink|damri", TRANSPORT),
    (r"shopee(?!pay)|tokopedia|lazada|blibli|bukalapak|tiktok ?shop|zalora|amazon|aliexpress", ONLINE),
    (r"pln|listrik|pdam|air minum|bpjs|pajak|kost|kos |sewa|asuransi|indihome|biznet|first ?media|myrepublic",
     TAGIHAN),
    (r"telkomsel|indosat|im3|xl|axis|tri|smartfren|by\.u|pulsa|paket data|kuota", PULSA),
    (r"netflix|spotify|youtube|disney|vidio|steam|playstation|xbox|google play|app store|apple|cgv|xxi|"
     r"cinepolis|game|garena|moonton|mobile legends|genshin|hoyoverse", HIBURAN),
    (r"apotek|apotik|kimia farma|k-24|century|guardian|watsons|klinik|rumah sakit|rs |dokter|halodoc|"
     r"alodokter", KESEHATAN),
    (r"universitas|unair|kampus|ukt|spp|kursus|course|udemy|coursera|dicoding|gramedia|buku|fotokopi|"
     r"print|edukasi|pendidikan", PENDIDIKAN),
]

# Whole-word matching so "toko" doesn't hit "tokopedia" and "tri" doesn't hit "electric".
_COMPILED_SEED_RULES = [(re.compile(rf"\b(?:{p})\b", re.IGNORECASE), c) for p, c in SEED_RULES]

TYPE_DEFAULTS: dict[TxnType, str] = {
    TxnType.BILL: TAGIHAN,
    TxnType.ATM_WITHDRAWAL: TUNAI,
    TxnType.TRANSFER_OUT: TRANSFER_ORANG,
    TxnType.TRANSFER_IN: PEMASUKAN,
    TxnType.TOPUP: TOPUP,
}


@dataclass
class Classification:
    kind: Kind
    category: str
    category_source: str  # rule | default


def _is_own_name(name: str, settings: Settings) -> bool:
    upper = name.upper()
    return any(own and own in upper for own in settings.own_name_list)


def _is_own_ewallet(name: str, settings: Settings) -> bool:
    upper = name.upper()
    return any(wallet in upper for wallet in settings.own_ewallet_list)


def detect_kind(parsed: ParsedTransaction, settings: Settings) -> Kind:
    if parsed.direction == Direction.IN:
        return Kind.INTERNAL if _is_own_name(parsed.counterparty, settings) else Kind.INCOME
    if parsed.txn_type == TxnType.TOPUP:
        if settings.count_topups_as_spending:
            return Kind.EXPENSE
        return Kind.INTERNAL if _is_own_ewallet(parsed.counterparty, settings) else Kind.EXPENSE
    if parsed.txn_type == TxnType.TRANSFER_OUT and _is_own_name(parsed.counterparty, settings):
        return Kind.INTERNAL
    return Kind.EXPENSE


def load_user_rules(session: Session) -> list[CategoryRule]:
    return list(session.scalars(select(CategoryRule).order_by(CategoryRule.priority.desc(), CategoryRule.id.desc())))


def classify(parsed: ParsedTransaction, settings: Settings, user_rules: list[CategoryRule] | None = None,
             party: Counterparty | None = None) -> Classification:
    kind = detect_kind(parsed, settings)
    if kind == Kind.INTERNAL:
        category = TOPUP if parsed.txn_type == TxnType.TOPUP else INTERNAL
        return Classification(kind, category, "rule")
    if kind == Kind.INCOME:
        return Classification(kind, PEMASUKAN, "default")

    # Per-counterparty knowledge (keyed by QR Merchant PAN etc.) beats name-based rules.
    if party is not None:
        if party.category:
            return Classification(kind, party.category, "user")
        if party.party_type == PartyType.PERSON:
            return Classification(kind, TRANSFER_ORANG, "user" if party.party_source == "user" else "rule")

    counterparty = parsed.counterparty.lower()
    for rule in user_rules or []:
        if rule.pattern.lower() in counterparty:
            return Classification(kind, rule.category, "user")

    # Topups/ATM/transfers have fixed meanings; don't let "GOPAY" match a transport rule, etc.
    if parsed.txn_type in (TxnType.TOPUP, TxnType.ATM_WITHDRAWAL, TxnType.TRANSFER_OUT):
        return Classification(kind, TYPE_DEFAULTS[parsed.txn_type], "default")

    for pattern, category in _COMPILED_SEED_RULES:
        if counterparty and pattern.search(counterparty):
            return Classification(kind, category, "rule")

    return Classification(kind, TYPE_DEFAULTS.get(parsed.txn_type, LAINNYA), "default")
