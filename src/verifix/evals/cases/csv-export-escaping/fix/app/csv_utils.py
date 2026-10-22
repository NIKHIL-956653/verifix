"""CSV helpers."""

_NEEDS_QUOTING = (",", '"', "\n", "\r")


def escape_field(value) -> str:
    text = str(value)
    if any(c in text for c in _NEEDS_QUOTING):
        return '"' + text.replace('"', '""') + '"'
    return text
