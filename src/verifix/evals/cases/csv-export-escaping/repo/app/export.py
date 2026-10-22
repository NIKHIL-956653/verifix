"""Build CSV exports."""


def export_rows(rows: list[list]) -> str:
    # BUG: joins raw values and never escapes them.
    return "\n".join(",".join(str(v) for v in row) for row in rows)
