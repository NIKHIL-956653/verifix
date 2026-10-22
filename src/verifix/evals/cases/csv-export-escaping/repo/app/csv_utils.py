"""CSV helpers."""


def escape_field(value) -> str:
    # BUG: only handles commas, and inner quotes are not doubled.
    text = str(value)
    if "," in text:
        return '"' + text + '"'
    return text
