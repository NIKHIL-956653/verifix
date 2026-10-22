"""
Secret redaction for anything that leaves the process: logs, API responses, reports.

Two layers, because each catches what the other misses:

1. Pattern matching for well-known credential formats (Anthropic, GitHub, AWS, bearer
   tokens, private-key blocks). This catches secrets that arrive from *outside* - a key
   pasted into a repository file, a token printed in a test log.
2. Exact-value matching for the secrets this process itself holds (register_secret).
   This catches our own key even if its format changes.

Dictionary keys that look like credentials (api_key, token, password, authorization...)
have their whole value replaced regardless of what the value looks like.

Redaction is applied to data, never relied on as the only control: secrets should not be
read or logged in the first place. This is the last line, not the first.
"""

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub classic tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub fine-grained
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),  # Slack
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{16,}=*"),  # Authorization: Bearer ...
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)

_SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|authorization|cookie|private[_-]?key)"
)

# Exact secret values held by this process. Values shorter than 8 characters are ignored:
# replacing every occurrence of a 3-character string would mangle ordinary text.
_known_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    if value and len(value) >= 8:
        _known_secrets.add(value)


def redact_text(text: str) -> str:
    for secret in _known_secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact strings inside dicts, lists and tuples. Other types pass through."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SENSITIVE_KEY.search(k) and v not in (None, "", False):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    return value
