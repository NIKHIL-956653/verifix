# verifix

An evidence-driven AI software engineer agent, built on the Anthropic API.

Give it a local Git repository and a task ("why does login return 500 when the email
field is missing?"). It is designed to investigate, write a regression test, propose a
minimal fix, run the tests in an isolated sandbox, and report what it found, with every
claim backed by a file reference, a diff, or real test output.

The core rule is that **the agent never fabricates an outcome.** It keeps four states
apart: a suspected root cause, a reproduced failure, a fix that has been applied, and a
fix that has been verified. Test results come from exit codes the sandbox recorded,
never from the model's own words.

## Status

| Milestone | State |
|---|---|
| M0 Foundation | **Done.** API, database, migrations, LLM boundary, redaction, 49 tests |
| M1 Repository explorer | Next |
| M2–M9 | Planned, see [docs/ROADMAP.md](docs/ROADMAP.md) |

This README lists what is built. Anything planned is labelled as planned.

## Run it locally (Windows PowerShell)

Prerequisites: Python 3.12+, Docker Desktop (running), Node 20+, Git.

```powershell
cd "G:\nickel_proj\8 projects\verifix"
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env          # then put your key after ANTHROPIC_API_KEY=
docker compose up -d                 # Postgres on localhost:5434 (dev + test databases)
alembic upgrade head
pytest                               # 49 tests; no test ever calls the real API
uvicorn verifix.main:app --reload --port 8010
```

In a second terminal:

```powershell
cd "G:\nickel_proj\8 projects\verifix\web"
npm install
npm run dev                          # http://localhost:5173
```

The page shows the API version, whether the database answers, and whether a model key
is configured. It never shows the key itself.

## Layout

```
src/verifix/
  core/     config (SecretStr settings), structured logging, secret redaction, database
  llm/      model boundary: protocol, Anthropic adapter, retry + circuit breaker, pricing, FakeLLM
  models/   repos, tasks, evidence (SQLAlchemy)
  api/      FastAPI routes (health so far)
alembic/    migrations
tests/      network-blocked test suite, plus one real-Postgres migration test
web/        React + TypeScript + Vite UI
docs/       architecture, roadmap, security, evaluation, API, decisions
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Evaluation methodology](docs/EVALUATION.md)
- [API](docs/API.md)
- [Decisions (ADRs)](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
