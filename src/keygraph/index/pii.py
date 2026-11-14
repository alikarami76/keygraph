"""Personal-value detection shared by packets (what the agent may see) and answer validation (what it may write).

`looks_pii` finds emails, IBANs, card numbers (Luhn), Iranian national ids and phone numbers (+98 and 09 forms plus
generic 10-15 digit runs) in a text. `scrub` masks those and any long digit run. Both are heuristics that fail safe: a
long number that is only an id may be masked too."""

from __future__ import annotations

import re

MASK = "***"
LONG_DIGITS = 9
_EMAIL = re.compile(r"[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63})*\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\bIR\d{2}(?:[ -]?\d{4}){5}[ -]?\d{2}\b|\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.I)
_RUN = re.compile(r"\+?\d{2,}(?:[ -]\d{3,})*")
_SECRET_KW = re.compile(r"""(?ix)\b(\w{0,20}?(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|auth|credential|private[_-]?key))
                            ((?:[_-]\w+)?["']?)(\s*[:=]\s*|\s+)["']?[^\s,;"')]+""")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _classify(run: str) -> str | None:
    digits = re.sub(r"\D", "", run)
    n = len(digits)
    if 13 <= n <= 19 and _luhn(digits):
        return "card"
    if run.startswith("+98") or digits.startswith("0098") and n == 14 or digits.startswith("98") and n == 12 \
            or digits.startswith("09") and n == 11:
        return "phone"
    if n == 10 and run.isdigit():
        return "national_id"
    if 10 <= n <= 15:
        return "phone"
    return None


def looks_pii(value) -> str | None:
    """The kind of personal value found in the text (email, iban, card, phone, national_id), or None."""
    s = str(value if value is not None else "")[:2000]
    if _EMAIL.search(s):
        return "email"
    if _IBAN.search(s):
        return "iban"
    for m in _RUN.finditer(s):
        kind = _classify(m.group(0))
        if kind:
            return kind
    return None


def scrub(text) -> str:
    """Mask emails, IBANs, card, phone and national-id numbers and any digit run of nine or more digits."""
    s = _EMAIL.sub(MASK, str(text if text is not None else ""))
    s = _IBAN.sub(MASK, s)

    def run(m: re.Match) -> str:
        return MASK if len(re.sub(r"\D", "", m.group(0))) >= LONG_DIGITS or _classify(m.group(0)) else m.group(0)

    return _RUN.sub(run, s)


def mask_keywords(text: str) -> str:
    """Mask the value after a secret-like word, with `=`, `:` or just a space: `secret hunter2`, `PGPASSWORD x`."""
    return _SECRET_KW.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}", text)
