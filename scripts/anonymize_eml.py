"""Turn a real bank .eml into an anonymized test fixture.

Keeps the HTML structure (what the parser needs), removes headers, images and links,
replaces names, masked accounts and long ID numbers. Add your own replacements with --replace.

    python scripts/anonymize_eml.py samples/x.eml tests/fixtures/livin_x.eml --replace "REAL NAME=PENGGUNA DEMO"
"""

import argparse
import hashlib
import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import make_msgid

from bs4 import BeautifulSoup


def _fake_digits(real: str) -> str:
    """Same real number -> same fake number (across files), so a friend's QR stays recognizable in tests."""
    digest = hashlib.sha256(("fixture-salt:" + real).encode()).hexdigest()
    return "".join(str(int(c, 16) % 10) for c in digest)[: len(real)]


def anonymize(raw: bytes, replacements: dict[str, str]) -> EmailMessage:
    src = message_from_bytes(raw, policy=policy.default)
    html = next(p for p in src.walk() if p.get_content_type() == "text/html").get_content()

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        img.decompose()
    for link in soup.find_all("a"):
        link.attrs.pop("href", None)
    html = str(soup)

    for real, fake in replacements.items():
        html = re.sub(re.escape(real), fake, html, flags=re.IGNORECASE)
    html = re.sub(r"\*{4}\d{4}", "****0000", html)  # masked account numbers
    html = re.sub(r"\d{9,}", lambda m: _fake_digits(m.group(0)), html)  # refs, PANs

    out = EmailMessage()
    out["From"] = str(src["From"])
    out["To"] = "demo@example.com"
    out["Subject"] = str(src["Subject"])
    out["Date"] = str(src["Date"])
    out["Message-ID"] = make_msgid(domain="fixture.example")
    out.set_content("<!-- anonymized test fixture -->" + html, subtype="html")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("--replace", action="append", default=[], help="REAL=FAKE, case-insensitive")
    args = parser.parse_args()
    pairs = dict(item.split("=", 1) for item in args.replace)
    with open(args.src, "rb") as f:
        msg = anonymize(f.read(), pairs)
    with open(args.dst, "wb") as f:
        f.write(bytes(msg))
    print(f"wrote {args.dst}")
