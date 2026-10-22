"""Build CSV exports."""

from app.csv_utils import escape_field


def export_rows(rows: list[list]) -> str:
    return "\n".join(",".join(escape_field(v) for v in row) for row in rows)
