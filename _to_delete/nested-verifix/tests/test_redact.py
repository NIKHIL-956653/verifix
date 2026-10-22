import json

import pytest
import structlog

from verifix.core.logging import configure_logging, get_logger
from verifix.core.redact import REDACTED, redact, redact_text, register_secret


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx",
        "ghp_0123456789abcdefghijABCDEFGHIJ",
        "github_pat_11ABCDEFG0123456789_abcdefghijklmnop",
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-1234567890-abcdefghij",
    ],
)
def test_known_formats_are_redacted(secret: str) -> None:
    out = redact_text(f"before {secret} after")
    assert secret not in out
    assert out == f"before {REDACTED} after"


def test_bearer_header_is_redacted() -> None:
    out = redact_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345")
    assert "abcdefghijklmnopqrstuvwxyz012345" not in out


def test_private_key_block_is_redacted() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    assert redact_text(f"x {pem} y") == f"x {REDACTED} y"


def test_registered_exact_value_is_redacted_even_without_a_known_format() -> None:
    register_secret("plain-looking-value-42")
    assert redact_text("token=plain-looking-value-42") == f"token={REDACTED}"


def test_short_values_are_not_registered() -> None:
    register_secret("abc")
    assert redact_text("abc abc") == "abc abc"


def test_sensitive_dict_keys_are_redacted_whatever_the_value() -> None:
    data = {"api_key": "whatever", "Authorization": "x", "password": "hunter2", "user": "nikhil"}
    out = redact(data)
    assert out == {
        "api_key": REDACTED,
        "Authorization": REDACTED,
        "password": REDACTED,
        "user": "nikhil",
    }


def test_nested_structures() -> None:
    data = {"steps": [{"output": "key sk-ant-api03-ZZZZZZZZZZZZZZZZ"}], "t": ("ghp_" + "a" * 30,)}
    out = json.dumps(redact(data))
    assert "sk-ant-api03" not in out
    assert "ghp_" not in out


def test_non_strings_pass_through() -> None:
    assert redact({"n": 3, "ok": True, "none": None}) == {"n": 3, "ok": True, "none": None}


def test_log_lines_never_contain_the_key(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "sk-ant-api03-ThisMustNeverBeLogged0001"
    configure_logging("INFO", json=True)
    log = get_logger("test")
    log.info("calling model", header=f"x-api-key: {secret}", api_key=secret)
    try:
        raise RuntimeError(f"request failed with key {secret}")
    except RuntimeError:
        log.exception("model call failed")
    err = capsys.readouterr().err
    assert secret not in err
    assert REDACTED in err
    structlog.reset_defaults()
