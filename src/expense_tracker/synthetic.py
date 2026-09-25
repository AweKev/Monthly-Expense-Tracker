"""Generate fake transaction emails for tests and the public demo.

These are clearly labelled synthetic and use a made-up sender. They follow the
generic label/value layout; once real samples are in, the templates here get
updated to mirror the real structure (with fake values).
"""

import hashlib
import random
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

SENDER = "Demo Bank <noreply@demo-bank.example>"
DISCLAIMER = "SYNTHETIC DATA. This is not a real bank email."

MERCHANTS = {
    "qris": [
        ("KOPI KENANGAN", 18000, 32000), ("WARTEG BAHARI", 12000, 25000), ("MIXUE", 8000, 22000),
        ("AYAM GEPREK SAYANG", 15000, 28000), ("TOKO MADURA BERKAH", 5000, 40000),
        ("INDOMARET", 10000, 85000), ("ALFAMART", 10000, 70000), ("FOTOKOPI KAMPUS", 2000, 15000),
        ("NASI PADANG SEDERHANA", 15000, 30000), ("APOTEK K-24", 15000, 90000),
    ],
    "purchase": [
        ("SHOPEE", 35000, 250000), ("TOKOPEDIA", 40000, 300000), ("GOJEK", 10000, 45000),
        ("GRAB", 12000, 50000), ("SPOTIFY", 54990, 54990), ("STEAM", 30000, 150000),
    ],
    "bill": [("PLN PRABAYAR", 50000, 100000), ("PULSA TELKOMSEL", 25000, 100000), ("PAKET DATA XL", 50000, 100000)],
    "friends": ["ANDI PRATAMA", "BUDI SANTOSO", "CITRA LESTARI", "DEWI ANGGRAINI", "EKO SAPUTRA"],
}


FRIEND_QR_CATEGORY = {"Andi Pratama": "Makanan", "Budi Santoso": "Jasa", "Citra Lestari": "Edukasi",
                      "Dewi Anggraini": "Fashion", "Eko Saputra": "Jasa"}


def _pan(name: str) -> str:
    """Fixed fake Merchant PAN per name, like a real QR code."""
    digest = hashlib.sha256(name.encode()).hexdigest()
    return "93600" + "".join(str(int(c, 16) % 10) for c in digest)[:14]


def _acquirer(name: str) -> str:
    return ("Bank Mandiri", "GoPay", "BRI", "DANA")[int(hashlib.sha256(name.encode()).hexdigest(), 16) % 4]


def _round(value: int) -> int:
    return value if value < 1000 else int(round(value / 500) * 500)


def _html(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td>{label}</td><td>:</td><td>{value}</td></tr>" for label, value in rows)
    return (
        f"<html><body><h2>{title}</h2><table>{body}</table>"
        f"<p>Simpan email ini sebagai bukti transaksi.</p><p><small>{DISCLAIMER}</small></p></body></html>"
    )


def _email(subject: str, rows: list[tuple[str, str]], when: datetime, tz: ZoneInfo) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = SENDER
    msg["To"] = "demo@example.com"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when.replace(tzinfo=tz))
    msg["Message-ID"] = make_msgid(domain="demo-bank.example")
    text = "\n".join(f"{label} : {value}" for label, value in rows) + f"\n\n{DISCLAIMER}"
    msg.set_content(text)
    msg.add_alternative(_html(subject, rows), subtype="html")
    return msg


def _rp(value: int) -> str:
    return "Rp " + f"{value:,}".replace(",", ".") + ",00"


def _when_rows(when: datetime) -> list[tuple[str, str]]:
    months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    return [("Tanggal", f"{when.day:02d} {months[when.month - 1]} {when.year}"), ("Waktu", when.strftime("%H:%M:%S WIB"))]


def generate(out_dir: str | Path, days: int = 60, seed: int = 42, end: date | None = None,
             tz: ZoneInfo = ZoneInfo("Asia/Jakarta")) -> int:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    end = end or date.today()
    count = 0

    def save(msg: EmailMessage) -> None:
        nonlocal count
        count += 1
        (out / f"synthetic_{count:05d}.eml").write_bytes(bytes(msg))

    def ref() -> str:
        return str(rng.randint(10**11, 10**12 - 1))

    for offset in range(days, -1, -1):
        day = end - timedelta(days=offset)

        if day.day == 1:
            when = datetime.combine(day, time(9, 5))
            save(_email("Dana Masuk", [("Nama Pengirim", "ORANG TUA DEMO"), ("Nominal", _rp(2_500_000))]
                        + _when_rows(when) + [("No. Referensi", ref())], when, tz))

        for _ in range(rng.randint(1, 4)):
            name, lo, hi = rng.choice(MERCHANTS["qris"])
            when = datetime.combine(day, time(rng.randint(7, 22), rng.randint(0, 59), rng.randint(0, 59)))
            save(_email("Pembayaran QRIS Berhasil",
                        [("Penerima", name), ("Nominal Transaksi", _rp(_round(rng.randint(lo, hi)))),
                         ("Biaya Admin", _rp(0))] + _when_rows(when)
                        + [("No. Referensi", ref()), ("Merchant PAN", _pan(name)), ("Pengakuisisi", _acquirer(name))],
                        when, tz))

        if rng.random() < 0.15:
            # Paying a friend through their personal GoPay QR: "Name, Category" like real Livin' emails.
            friend = rng.choice(MERCHANTS["friends"]).title()
            label = f"{friend}, {FRIEND_QR_CATEGORY[friend]}"
            when = datetime.combine(day, time(rng.randint(9, 22), rng.randint(0, 59), rng.randint(0, 59)))
            save(_email("Pembayaran QRIS Berhasil",
                        [("Penerima", label), ("Nominal Transaksi", _rp(_round(rng.randint(10_000, 60_000))))]
                        + _when_rows(when) + [("No. Referensi", ref()), ("Merchant PAN", _pan(friend)),
                                              ("Pengakuisisi", "GoPay")], when, tz))

        if rng.random() < 0.35:
            name, lo, hi = rng.choice(MERCHANTS["purchase"])
            when = datetime.combine(day, time(rng.randint(8, 23), rng.randint(0, 59)))
            save(_email("Pembayaran Berhasil",
                        [("Merchant", name), ("Jumlah", _rp(_round(rng.randint(lo, hi))))]
                        + _when_rows(when) + [("No. Referensi", ref())], when, tz))

        if rng.random() < 0.15:
            wallet = rng.choice(["GOPAY", "SHOPEEPAY", "DANA"])
            when = datetime.combine(day, time(rng.randint(8, 22), rng.randint(0, 59)))
            save(_email("Top Up Berhasil",
                        [("Penerima", f"{wallet} - 0812****7788"), ("Nominal", _rp(rng.choice([50_000, 100_000]))),
                         ("Biaya Admin", _rp(1_000))] + _when_rows(when), when, tz))

        if rng.random() < 0.12:
            friend = rng.choice(MERCHANTS["friends"])
            when = datetime.combine(day, time(rng.randint(10, 22), rng.randint(0, 59)))
            save(_email("Transfer Berhasil",
                        [("Nama Penerima", friend), ("Rekening Tujuan", "BCA - ****" + _pan(friend)[-4:]),
                         ("Nominal", _rp(_round(rng.randint(20_000, 150_000)))), ("Biaya Transfer", _rp(2_500))]
                        + _when_rows(when) + [("No. Referensi", ref())], when, tz))

        if rng.random() < 0.06:
            friend = rng.choice(MERCHANTS["friends"])
            when = datetime.combine(day, time(rng.randint(10, 22), rng.randint(0, 59)))
            save(_email("Dana Masuk", [("Nama Pengirim", friend), ("Nominal", _rp(_round(rng.randint(20_000, 100_000))))]
                        + _when_rows(when), when, tz))

        if day.day in (5, 20):
            name, lo, hi = rng.choice(MERCHANTS["bill"])
            when = datetime.combine(day, time(19, rng.randint(0, 59)))
            save(_email("Pembayaran Berhasil", [("Produk", name), ("Nominal", _rp(_round(rng.randint(lo, hi))))]
                        + _when_rows(when) + [("No. Referensi", ref())], when, tz))

        if rng.random() < 0.05:
            when = datetime.combine(day, time(rng.randint(8, 21), rng.randint(0, 59)))
            save(_email("Tarik Tunai Berhasil", [("Lokasi", "ATM KAMPUS C"), ("Nominal", _rp(rng.choice([100_000, 200_000])))]
                        + _when_rows(when), when, tz))

        if rng.random() < 0.05:
            when = datetime.combine(day, time(12, 0))
            msg = EmailMessage()
            msg["From"], msg["Subject"] = SENDER, "Promo spesial minggu ini"
            msg["Date"] = format_datetime(when.replace(tzinfo=tz))
            msg["Message-ID"] = make_msgid(domain="demo-bank.example")
            msg.set_content(f"Nikmati promo cashback di merchant pilihan.\n\n{DISCLAIMER}")
            save(msg)

    return count
