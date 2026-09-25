"""Generic helpers: pull "label: value" pairs out of bank emails, parse Rupiah amounts and Indonesian dates.

Bank emails are usually a table of labels and values. Instead of hard-coding
one template, we extract every label/value pair and map known labels onto
canonical field names. A new template then mostly needs new label synonyms,
not a new parser.
"""

import re
from datetime import date, datetime, time

from bs4 import BeautifulSoup

# ---------------------------------------------------------------- labels

# Checked in order, first match wins. Each entry: (canonical field, substrings).
# Order matters: "no. referensi" must hit reference before the generic "no." account rule.
# Labels that contain a known word but mean something else. "Customer PAN" is your own QR identity.
IGNORED_LABELS = ("customer pan", "terminal id", "terminal")

LABEL_RULES: list[tuple[str, tuple[str, ...]]] = [
    # QRIS: the Merchant PAN is a fixed ID per QR code, used to recognize the same friend/store again.
    ("counterparty_id", ("merchant pan", "nmid")),
    ("acquirer", ("pengakuisisi", "acquirer")),
    ("reference", ("referensi", "no. ref", "no ref", "ref no", "nomor transaksi", "no. transaksi", "id transaksi")),
    ("datetime", ("tanggal & waktu", "tanggal dan waktu", "tanggal/waktu", "waktu transaksi", "tanggal transaksi")),
    ("source_account", ("sumber dana", "rekening sumber", "dari rekening", "rekening asal")),
    ("sender_name", ("nama pengirim", "pengirim", "dari")),
    ("counterparty_account", ("rekening tujuan", "rekening penerima", "no. rekening", "nomor rekening",
                              "id pelanggan", "nomor pelanggan", "no. pelanggan", "nomor hp", "no. hp",
                              "nomor ponsel", "virtual account", "nomor meter")),
    ("fee", ("biaya", "admin", "fee")),
    ("total", ("total",)),
    ("amount", ("nominal", "jumlah", "nilai transaksi", "amount")),
    ("counterparty", ("nama penerima", "penerima", "nama merchant", "merchant", "nama toko", "tujuan",
                      "kepada", "nama pelanggan", "produk", "layanan", "penyedia jasa")),
    ("status", ("status",)),
    ("transaction_type", ("jenis transaksi", "tipe transaksi", "transaksi")),
    ("date", ("tanggal", "tgl")),
    ("time", ("waktu", "jam", "pukul")),
]


def normalize_label(label: str) -> str:
    label = label.replace("\xa0", " ").strip().strip(":").strip()
    return re.sub(r"\s+", " ", label).lower()


def canonical_field(label: str) -> str | None:
    norm = normalize_label(label)
    # Labels are short. This keeps prose like "Berikut adalah detail transaksi Anda" from being read as one.
    if not norm or len(norm) > 40 or len(norm.split()) > 4:
        return None
    if any(norm == ignored or norm.startswith(ignored + " ") for ignored in IGNORED_LABELS):
        return None
    for field, needles in LABEL_RULES:
        if any(needle == norm or needle in norm for needle in needles):
            return field
    return None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip().strip(":").strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "script", "head"]):
        tag.decompose()
    lines = [_clean(line) for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def _pairs_from_html(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    pairs = []
    for row in soup.find_all("tr"):
        # Only rows whose cells are leaves; skip layout tables that wrap other tables.
        if row.find("table"):
            continue
        cells = [_clean(c.get_text(" ")) for c in row.find_all(["td", "th"])]
        cells = [c for c in cells if c and c != ":"]
        if len(cells) >= 2:
            pairs.append((cells[0], cells[-1]))
    return pairs


def cards_from_html(html: str) -> list[tuple[str, str, str]]:
    """Livin' 'card' blocks: <p>Label</p><h4>Main value</h4><p>sub line</p>.

    Used for Penerima / Penyedia Jasa (name + location or masked account) and Sumber Dana.
    Returns (label, value, sub).
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for heading in soup.find_all(["h3", "h4", "h5"]):
        label_tag = heading.find_previous_sibling("p")
        if label_tag is None:
            continue
        sub_tag = heading.find_next_sibling("p")
        label, value = _clean(label_tag.get_text(" ")), _clean(heading.get_text(" "))
        sub = _clean(sub_tag.get_text(" ")) if sub_tag else ""
        if label and value and canonical_field(label):
            cards.append((label, value, sub))
    return cards


def _pairs_from_text(text: str) -> list[tuple[str, str]]:
    lines = [_clean(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    pairs = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r"^([^:]{2,40}?)\s*:\s*(.+)$", line)
        if match and canonical_field(match.group(1)):
            pairs.append((match.group(1), match.group(2)))
        elif canonical_field(line) and i + 1 < len(lines) and not canonical_field(lines[i + 1]):
            # Label on one line, value on the next.
            pairs.append((line, lines[i + 1]))
            i += 1
        i += 1
    return pairs


def extract_fields(html: str, text: str) -> dict[str, str]:
    """Return {canonical_field: value}. The first occurrence of each field wins.

    Card blocks also add "<field>_sub" (e.g. counterparty_sub = "****0802" or "SURABAYA - ID").
    """
    fields: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    if html:
        for label, value, sub in cards_from_html(html):
            field = canonical_field(label)
            if field and field not in fields:
                fields[field] = value
                if sub:
                    fields[f"{field}_sub"] = sub
        pairs += _pairs_from_html(html)
    pairs += _pairs_from_text(text or html_to_text(html))
    for label, value in pairs:
        field = canonical_field(label)
        if field and field not in fields and value:
            fields[field] = value
    return fields


# ---------------------------------------------------------------- amounts

_AMOUNT_RE = re.compile(r"(?:rp\.?|idr)\s*([\d.,]+)", re.IGNORECASE)


def parse_amount(value: str) -> int | None:
    """'Rp 1.250.000,00' -> 1250000. Handles both Indonesian and English separators."""
    if not value:
        return None
    match = _AMOUNT_RE.search(value)
    number = match.group(1) if match else None
    if number is None:
        bare = re.search(r"\d[\d.,]*", value)
        if not bare:
            return None
        number = bare.group(0)
    number = number.strip(".,")

    if "." in number and "," in number:
        decimal_sep = "." if number.rfind(".") > number.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        number = number.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        sep = "." if "." in number else ("," if "," in number else None)
        if sep:
            parts = number.split(sep)
            if len(parts) == 2 and len(parts[1]) != 3:
                number = parts[0] + "." + parts[1]  # decimal, e.g. 25000,00
            else:
                number = number.replace(sep, "")  # thousands, e.g. 1.250.000
    try:
        return round(float(number))
    except ValueError:
        return None


def find_amount_in_text(text: str) -> int | None:
    """Fallback: first 'Rp ...' in free text."""
    match = _AMOUNT_RE.search(text or "")
    return parse_amount(match.group(0)) if match else None


# ---------------------------------------------------------------- dates

MONTHS = {
    "jan": 1, "januari": 1, "january": 1,
    "feb": 2, "februari": 2, "february": 2, "peb": 2,
    "mar": 3, "maret": 3, "march": 3,
    "apr": 4, "april": 4,
    "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "agus": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "nopember": 11, "november": 11,
    "des": 12, "desember": 12, "dec": 12, "december": 12,
}

_DATE_PATTERNS = [
    # 25 Sep 2026 / 25 September 2026 / 25-Sep-2026
    (re.compile(r"(\d{1,2})[\s\-/]+([A-Za-z]{3,9})[\s\-/,]+(\d{4})"), "dmy_name"),
    # 2026-09-25
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), "ymd"),
    # 25/09/2026 or 25-09-2026
    (re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})"), "dmy"),
    # 25/09/26
    (re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b"), "dmy_short"),
]
_TIME_RE = re.compile(r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?")


def parse_date(value: str) -> date | None:
    if not value:
        return None
    for pattern, kind in _DATE_PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        try:
            if kind == "dmy_name":
                month = MONTHS.get(match.group(2).lower())
                if month is None:
                    continue
                return date(int(match.group(3)), month, int(match.group(1)))
            if kind == "ymd":
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if kind == "dmy":
                return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            if kind == "dmy_short":
                return date(2000 + int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            continue
    return None


def parse_time(value: str) -> time | None:
    if not value:
        return None
    # Drop the date part first so "2026-09-25" is not read as a time.
    for pattern, _ in _DATE_PATTERNS:
        value = pattern.sub(" ", value)
    match = _TIME_RE.search(value)
    if not match:
        return None
    hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return time(hour, minute, second)


def parse_datetime(date_value: str = "", time_value: str = "") -> datetime | None:
    day = parse_date(date_value) or parse_date(time_value)
    if day is None:
        return None
    clock = parse_time(time_value) or parse_time(date_value) or time(0, 0)
    return datetime.combine(day, clock)
