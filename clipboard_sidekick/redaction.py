"""Secret redaction for clipboard text destined for remote backends.

Pure stdlib (re). No I/O. Detection rules are applied to the raw text
independently; a single text may trip several rules.
"""

import re

_REDACTED = "[REDACTED]"
_REDACTED_PRIVATE_KEY = "[REDACTED: PRIVATE KEY]"
_REDACTED_CARD = "[REDACTED: CARD]"

# Private key blocks first so their body is removed wholesale before the
# token/hex rules get a chance to nibble at it.
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
# Any BEGIN left after the terminated blocks are gone is unterminated:
# redact from the BEGIN marker to the end of the string.
_PRIVATE_KEY_UNTERMINATED_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*\Z",
)

# password= / passwd: / pwd = style pairs (case-insensitive). The value is
# either a quoted string (single or double quotes, no newlines) or the rest
# of the current token (no whitespace, comma or semicolon).
_PASSWORD_RE = re.compile(
    r"(?P<key>(?:password|passwd|pwd)\s*[:=]\s*)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,;\n]+)",
    re.IGNORECASE,
)

# API keys / tokens. Bounded character classes only.
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9-]{16,}"),
    re.compile(r"pk_live_[A-Za-z0-9]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"[0-9a-f]{32,}", re.IGNORECASE),
]

# Credit-card-like runs: 13-19 digits, optional single space or dash between
# digits. Luhn check gates the redaction.
_CARD_CANDIDATE_RE = re.compile(r"\b\d(?:[- ]?\d){12,18}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_cards(text: str) -> str:
    def _sub(match: re.Match[str]) -> str:
        digits = re.sub(r"[- ]", "", match.group(0))
        if _luhn_ok(digits):
            return _REDACTED_CARD
        return match.group(0)

    return _CARD_CANDIDATE_RE.sub(_sub, text)


def redact(text: str) -> tuple[str, bool]:
    """Return (redacted_text, triggered). triggered is True iff any redaction occurred."""
    if not text or not text.strip():
        return text, False

    triggered = False
    out = text

    new = _PRIVATE_KEY_BLOCK_RE.sub(_REDACTED_PRIVATE_KEY, out)
    new = _PRIVATE_KEY_UNTERMINATED_RE.sub(_REDACTED_PRIVATE_KEY, new)
    if new != out:
        triggered = True
        out = new

    new = _PASSWORD_RE.sub(lambda m: m.group("key") + _REDACTED, out)
    if new != out:
        triggered = True
        out = new

    for pattern in _TOKEN_PATTERNS:
        new = pattern.sub(_REDACTED, out)
        if new != out:
            triggered = True
            out = new

    new = _redact_cards(out)
    if new != out:
        triggered = True
        out = new

    return out, triggered


def should_skip_prefetch(text: str) -> bool:
    """True when the text trips redaction and must not be sent to a remote backend."""
    return redact(text)[1]
