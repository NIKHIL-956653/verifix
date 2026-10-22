import csv
import io

from app.export import export_rows


def _parse(text):
    return list(csv.reader(io.StringIO(text)))


def test_comma_stays_one_field():
    assert _parse(export_rows([["Widget, large", 5]])) == [["Widget, large", "5"]]


def test_inner_quotes_doubled():
    assert _parse(export_rows([['He said "hi"', 1]])) == [['He said "hi"', "1"]]


def test_newline_inside_field():
    assert _parse(export_rows([["line1\nline2", 2]])) == [["line1\nline2", "2"]]


def test_plain_fields_stay_unquoted():
    assert export_rows([["abc", 1]]) == "abc,1"
