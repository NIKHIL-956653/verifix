import pytest

from app.documents import Document, PermissionDenied, Store, User, delete_document


def _store():
    s = Store()
    s.add(Document(1, "ann", "notes"))
    return s


def test_other_user_cannot_delete():
    s = _store()
    with pytest.raises(PermissionDenied):
        delete_document(User("bob"), 1, s)
    assert 1 in s.docs


def test_admin_can_delete():
    s = _store()
    delete_document(User("root", is_admin=True), 1, s)
    assert 1 not in s.docs


def test_owner_still_can_delete():
    s = _store()
    delete_document(User("ann"), 1, s)
    assert s.docs == {}
