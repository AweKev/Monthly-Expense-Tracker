"""Who is on the other side of a transaction: a person (friend) or a merchant (store).

The name alone isn't reliable: a friend's personal GoPay QR looks like "Erin Josephine, Edukasi"
(GoPay appends the category picked at sign-up), and stores can be named after people.
So: make a guess, let the user confirm once, and remember it by a stable key.
The QRIS Merchant PAN is the best key: it is fixed per QR code, even if the display name changes.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Counterparty, PartyType, TxnType
from .parsing.base import ParsedTransaction

# Only these types get a counterparty record. Top-ups, bills and ATM have a fixed meaning.
TRACKED_TYPES = {TxnType.QRIS, TxnType.PURCHASE, TxnType.TRANSFER_OUT, TxnType.TRANSFER_IN}

EWALLET_ACQUIRERS = ("gopay", "dana", "ovo", "shopeepay", "linkaja")

# Words that mark a business name. Checked on the name part only (before ", Category").
BUSINESS_HINTS = re.compile(
    r"\b(?:toko|warung|warteg|kedai|depot|kios|cafe|kafe|coffee|kopi|resto|restoran|rumah makan|rm|bakso|mie|"
    r"ayam|nasi|sate|seblak|geprek|print|printing|fotokopi|fotocopy|copy|laundry|salon|barbershop|apotek|"
    r"apotik|klinik|bengkel|mart|minimarket|swalayan|store|shop|official|cv|pt|ud|tbk|indomaret|alfamart|"
    r"parkir|kantin|catering|bakery|roti|es|jus|juice|boba|tea|teh|pulsa|cell|cellular|konter|grosir|"
    r"sembako|fashion|distro|optik|travel|hotel|kos|kost|pertamina|spbu)\b",
    re.IGNORECASE,
)

# "Name, Category": what personal e-wallet QRs look like.
NAME_WITH_CATEGORY = re.compile(r"^(?P<name>[^,]{2,60}),\s*(?P<category>[A-Za-z &/-]{3,40})$")
PERSON_NAME = re.compile(r"^[A-Za-z][A-Za-z.' ]{1,58}$")


def counterparty_key(parsed: ParsedTransaction) -> str | None:
    if parsed.txn_type not in TRACKED_TYPES:
        return None
    if parsed.counterparty_id:
        return f"pan:{parsed.counterparty_id}"
    account = re.sub(r"\s+", " ", parsed.counterparty_account).strip().upper()
    if account and re.search(r"\d{4}", account):
        return f"acct:{account}"
    name = re.sub(r"\s+", " ", parsed.counterparty).strip().upper()
    return f"name:{name}" if name else None


def guess_party_type(parsed: ParsedTransaction) -> tuple[PartyType, str]:
    """Return (guess, reason). The reason is shown when you review guesses."""
    if parsed.txn_type in (TxnType.TRANSFER_OUT, TxnType.TRANSFER_IN):
        return PartyType.PERSON, "transfer bank"

    name = parsed.counterparty.strip()
    match = NAME_WITH_CATEGORY.match(name)
    name_part = match.group("name").strip() if match else name

    if BUSINESS_HINTS.search(name_part):
        return PartyType.MERCHANT, "ada kata usaha di nama"

    via_ewallet = any(w in parsed.acquirer.lower() for w in EWALLET_ACQUIRERS)
    if match and via_ewallet and PERSON_NAME.match(name_part) and 1 <= len(name_part.split()) <= 4:
        return PartyType.PERSON, f"QR 'Nama, {match.group('category')}' lewat {parsed.acquirer}"

    return PartyType.MERCHANT, "bawaan untuk bayar QR"


def get_or_create(session: Session, parsed: ParsedTransaction) -> Counterparty | None:
    key = counterparty_key(parsed)
    if key is None:
        return None
    party = session.scalars(select(Counterparty).where(Counterparty.key == key)).first()
    if party is None:
        party_type, reason = guess_party_type(parsed)
        party = Counterparty(key=key, name=parsed.counterparty[:255], party_type=party_type,
                             party_source="guess", guess_reason=reason[:255])
        session.add(party)
        session.flush()
    elif parsed.counterparty and party.name != parsed.counterparty:
        party.name = parsed.counterparty[:255]  # keep the latest display name
    return party


def find(session: Session, query: str) -> list[Counterparty]:
    """By id ("12"), exact key ("pan:936..."), or name substring (case-insensitive)."""
    if query.isdigit():
        party = session.get(Counterparty, int(query))
        if party:
            return [party]
    exact = session.scalars(select(Counterparty).where(Counterparty.key == query)).all()
    if exact:
        return list(exact)
    return list(session.scalars(select(Counterparty).where(Counterparty.name.ilike(f"%{query}%"))))


def tag(party: Counterparty, party_type: PartyType | None = None, category: str | None = None) -> None:
    if party_type is not None:
        party.party_type = party_type
        party.party_source = "user"
    if category is not None:
        party.category = category or None  # "" clears it
