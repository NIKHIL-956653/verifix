"""
Settings, read once from the environment (and .env).

Every secret is a SecretStr, so printing the settings object, logging it, or putting it
in an exception message shows '**********' instead of the value. Nothing in the codebase
should ever call .get_secret_value() except the one place that hands the key to the SDK.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VERIFIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"

    database_url: str = "postgresql+asyncpg://verifix:verifix_dev@localhost:5434/verifix"
    test_database_url: str = "postgresql+asyncpg://verifix:verifix_dev@localhost:5434/verifix_test"

    # Accept the conventional ANTHROPIC_API_KEY too, so one key can serve several projects.
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VERIFIX_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    model_main: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    llm_timeout_s: float = 120.0
    llm_max_attempts: int = 4

    log_level: str = "INFO"
    log_json: bool = False

    # Comma-separated. Kept as a string because pydantic-settings would otherwise expect JSON.
    cors_origins: str = "http://localhost:5178"

    # Repositories may only be registered under these directories (comma-separated).
    # Empty = any directory. Example: G:\nickel_proj
    allowed_roots: str = ""
    # Where per-task git worktrees live. Outside every repository on purpose.
    workspaces_dir: str = str(Path.home() / ".verifix" / "workspaces")

    # Sandbox (M5). verifix layers pytest (verifix-runtime) and repo deps (verifix-env) on top.
    sandbox_base_image: str = "python:3.12-slim"
    sandbox_memory: str = "1g"
    sandbox_cpus: str = "2"
    sandbox_pids: int = 256
    sandbox_timeout_s: int = 300
    docker_bin: str = "docker"

    # Default per-task budgets (M3). A task stops with status budget_exhausted at any limit.
    budget_max_model_calls: int = 40
    budget_max_tool_calls: int = 80
    budget_max_input_tokens: int = 1_500_000
    budget_max_output_tokens: int = 60_000
    budget_max_cost_usd: float = 2.00
    budget_max_minutes: float = 20.0

    # GitHub (M8). Optional; only needed to read CI status and open pull requests.
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    # Where repositories imported by URL are cloned.
    clones_dir: str = str(Path.home() / ".verifix" / "repos")

    @field_validator("anthropic_api_key", "github_token", mode="before")
    @classmethod
    def _blank_key_is_none(cls, v: object) -> object:
        # An empty ANTHROPIC_API_KEY= line in .env means "not configured", not "the empty key".
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def allowed_root_list(self) -> list[Path]:
        return [Path(p.strip()).expanduser() for p in self.allowed_roots.split(",") if p.strip()]

    @property
    def anthropic_configured(self) -> bool:
        return self.anthropic_api_key is not None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
