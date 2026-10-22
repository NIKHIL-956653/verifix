import pytest

from app.documents import Document, NotFound, Store, User, delete_document


def test_owner_can_delete():
    s = Store()
    s.add(Document(1, "ann", "notes"))
    delete_document(User("ann"), 1, s)
    assert 1 not in s.docs


def test_missing_document():
    with pytest.raises(NotFound):
        delete_document(User("ann"), 9, Store())
