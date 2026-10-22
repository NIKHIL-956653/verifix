# Architecture

A modular monolith: one FastAPI process, one Postgres, and, from M5, one sandbox image
started per test run. Components are added when a milestone needs them, not before.

```
React UI ──/api──▶ FastAPI ──▶ Task service ──▶ Agent loop (bounded)            [M3]
                                   │                  │ tool request (typed)
                                   │                  ▼
                                   │            Tool gateway ── permission check ── approval queue   [M3–M4]
                                   │                  │
                                   │      ┌───────────┼───────────────────┐
                                   │      ▼           ▼                   ▼
                                   │  Repo reader   Git (read-only,    Sandbox runner          [M1 / M4 / M5]
                                   │  (path-jailed) task worktree)     (Docker, no network)
                                   ▼
                              Postgres: repos, tasks, evidence  (+ steps, approvals, patches, eval_runs later)
```

## What exists after M0

| Component | File(s) | Notes |
|---|---|---|
| Settings | `core/config.py` | Every secret is a `SecretStr`. Accepts `ANTHROPIC_API_KEY` or `VERIFIX_ANTHROPIC_API_KEY` |
| Redaction | `core/redact.py` | Known credential formats, registered exact values, sensitive dict keys |
| Logging | `core/logging.py` | structlog. Redaction is a mandatory processor that runs after traceback formatting |
| Database | `core/db.py`, `models/`, `alembic/` | Async SQLAlchemy 2.1 + asyncpg. Lazy engine, so importing never connects |
| LLM boundary | `llm/base.py` | `LLM` protocol, `ToolSpec`, `ToolCall`, `LLMResponse`, a closed set of error types |
| Anthropic adapter | `llm/anthropic.py` | SDK retries off. Our retry + breaker on. Prompt caching on system + tools |
| Resilience | `llm/resilience.py` | Full-jitter exponential backoff, circuit breaker, injectable clock/sleep |
| Pricing | `llm/pricing.py` | Per-model prices. An unknown model raises instead of costing $0 |
| Fake LLM | `llm/fake.py` | Scripted responses and recorded requests. Fails loudly when the script runs out |
| API | `main.py`, `api/health.py` | App factory, CORS limited to configured origins, `/health` |
| UI | `web/` | Vite + React + TS. The dev server proxies `/api` to `:8010` |

## Data model

- **repos**: a registered checkout (unique resolved `root_path`).
- **tasks**: one request against one repo. `status` is constrained in the database to
  `queued, planning, awaiting_approval, running, fixed_unverified, verified, failed,
  cancelled, budget_exhausted`. `verified` will only be set by the verification engine.
- **evidence**: raw, redacted output of an action (file read, search, diff, test run),
  with a SHA-256 of the content and a per-task sequence number (unique). Reports cite
  evidence by id. The database rejects a malformed hash or a duplicate sequence number.

## Agent loop (from M3, designed now)

1. The model receives the task, a system prompt and the allowed tools (typed JSON Schema).
2. It returns either text or tool requests. Nothing has run at that point.
3. The gateway validates each request against its Pydantic schema, checks permissions
   (read-only: allowed; consequential: queued for approval), runs it, and stores the
   result as evidence.
4. The result goes back to the model, which continues until done or until a budget
   (tool calls, tokens, dollars, minutes) runs out. Then the task stops with
   `budget_exhausted`. It never stops with a fake success.

## Why no LangChain, LangGraph, vector DB, Redis or Temporal

None is needed for M0–M2, and each would make the agent harder to evaluate. See
[DECISIONS.md](DECISIONS.md) (ADR-002, ADR-005). Each gets reconsidered when a benchmark
number shows a gap it would close.
