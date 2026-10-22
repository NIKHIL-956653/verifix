from verifix.core.config import Settings


def test_defaults_without_key() -> None:
    s = Settings(_env_file=None)
    assert s.env == "test"
    assert s.anthropic_configured is False
    assert s.model_main == "claude-sonnet-5-5"


def test_conventional_anthropic_env_name_is_accepted(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-abcdefghijkl")
    s = Settings(_env_file=None)
    assert s.anthropic_configured
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test-abcdefghijkl"


def test_prefixed_name_wins_over_conventional(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-conventional")
    monkeypatch.setenv("VERIFIX_ANTHROPIC_API_KEY", "sk-ant-test-prefixed-one")
    s = Settings(_env_file=None)
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-test-prefixed-one"


def test_blank_key_means_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert Settings(_env_file=None).anthropic_configured is False


def test_key_never_appears_in_repr_or_dump(monkeypatch) -> None:
    secret = "sk-ant-test-must-not-leak-123456"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    s = Settings(_env_file=None)
    assert secret not in repr(s)
    assert secret not in str(s.model_dump())
    assert secret not in s.model_dump_json()


def test_cors_origins_comma_separated(monkeypatch) -> None:
    monkeypatch.setenv("VERIFIX_CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173,")
    s = Settings(_env_file=None)
    assert s.cors_origin_list == ["http://localhost:5173", "http://127.0.0.1:5173"]
