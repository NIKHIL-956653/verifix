import pytest

from app.auth import LoginError, login


def test_missing_email_raises_login_error():
    with pytest.raises(LoginError, match="email"):
        login({"password": "x"})


def test_blank_email_raises_login_error():
    with pytest.raises(LoginError):
        login({"email": "   ", "password": "x"})


def test_valid_login_still_works():
    assert login({"email": "X@Y.org", "password": "p"}) == {"user": "x@y.org"}
