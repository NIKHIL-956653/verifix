from decimal import Decimal

from app.importer import import_rows


def test_bad_rows_are_reported_with_row_numbers():
    r = import_rows([("a", "1"), ("b", "abc"), ("c", "3"), ("d", "")])
    assert [n for n, _ in r.errors] == [2, 4]
    assert all(isinstance(msg, str) and msg for _, msg in r.errors)


def test_good_rows_still_imported():
    r = import_rows([("a", "1"), ("b", "abc"), ("c", "3")])
    assert r.imported == [("a", Decimal("1")), ("c", Decimal("3"))]


def test_clean_import_has_no_errors():
    assert import_rows([("a", "5")]).errors == []
