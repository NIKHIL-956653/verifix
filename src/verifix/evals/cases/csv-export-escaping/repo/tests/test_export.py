from app.csv_utils import escape_field
from app.export import export_rows


def test_plain_rows():
    assert export_rows([["a", 1], ["b", 2]]) == "a,1\nb,2"


def test_plain_field_unchanged():
    assert escape_field("abc") == "abc"
