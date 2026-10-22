"""Import payment rows."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


@dataclass
class ImportResult:
    imported: list[tuple[str, Decimal]] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)


def parse_amount(text: str) -> Decimal:
    return Decimal(text.strip())


def import_rows(rows: list[tuple[str, str]]) -> ImportResult:
    result = ImportResult()
    for number, (name, amount) in enumerate(rows, start=1):
        try:
            result.imported.append((name, parse_amount(amount)))
        except InvalidOperation:
            result.errors.append((number, f"invalid amount {amount!r}"))
    return result
