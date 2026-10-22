"""
Who may do what without asking (Policy), and how much a task may spend (Budget).

Permission classes:
  read     - inspect files, search, diffs. Always allowed.
  write    - change files in the task's worktree. Default: ask.
  execute  - run tests in the sandbox (no network, read-only). Default: allowed.
  install  - install dependencies (the one step with network). Default: ask.
  report   - plan / findings / finish. Always allowed.

These are enforced by the loop in application code. Nothing the model writes - and
nothing in the repository it reads - can change them (SECURITY.md, prompt injection).
"""

from typing import Literal

from pydantic import BaseModel, Field

from verifix.core.config import Settings

Permission = Literal["read", "write", "execute", "install", "report"]
Mode = Literal["ask", "auto"]


class Policy(BaseModel):
    approve_plan: bool = Field(True, description="fix tasks pause for approval after planning")
    write: Mode = "ask"
    execute: Mode = "auto"
    install: Mode = "ask"

    def needs_approval(self, permission: Permission) -> bool:
        if permission == "write":
            return self.write == "ask"
        if permission == "execute":
            return self.execute == "ask"
        if permission == "install":
            return self.install == "ask"
        return False

    @classmethod
    def autonomous(cls) -> "Policy":
        """For benchmark runs: no human in the loop, same sandbox limits."""
        return cls(approve_plan=False, write="auto", execute="auto", install="auto")


class Budget(BaseModel):
    max_model_calls: int = Field(40, ge=1, le=500)
    max_tool_calls: int = Field(80, ge=1, le=1000)
    max_input_tokens: int = Field(1_500_000, ge=1_000)
    max_output_tokens: int = Field(60_000, ge=500)
    max_cost_usd: float = Field(2.0, gt=0, le=100)
    max_minutes: float = Field(20.0, gt=0, le=240)

    @classmethod
    def from_settings(cls, s: Settings) -> "Budget":
        return cls(
            max_model_calls=s.budget_max_model_calls,
            max_tool_calls=s.budget_max_tool_calls,
            max_input_tokens=s.budget_max_input_tokens,
            max_output_tokens=s.budget_max_output_tokens,
            max_cost_usd=s.budget_max_cost_usd,
            max_minutes=s.budget_max_minutes,
        )
