import pytest

from app.auth import LoginError, login


def test_login_ok():
    assert login({"email": " A@B.com ", "password": "x"}) == {"user": "a@b.com"}


def test_missing_password():
    with pytest.raises(LoginError):
        login({"email": "a@b.com"})
