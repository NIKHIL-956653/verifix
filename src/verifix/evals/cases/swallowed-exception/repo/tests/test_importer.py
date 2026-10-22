from decimal import Decimal

from app.importer import import_rows


def test_good_rows():
    r = import_rows([("a", "1.50"), ("b", " 2 ")])
    assert r.imported == [("a", Decimal("1.50")), ("b", Decimal("2"))]
