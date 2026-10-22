# verifix

An evidence-driven AI software engineer agent, built on the Anthropic API.

Give it a local Git repository and a task ("POST /login returns 500 when the email field
is missing"). It investigates, writes a regression test, changes the code in an isolated
copy, runs the tests in a locked-down Docker sandbox, and reports what it found. Every
claim in the report cites an evidence id (`E5`) that points at the exact file read, diff
or test run behind it.

The core rule: **the agent never fabricates an outcome.** "Fixed" and "verified" are
different states, and only verifix itself (not the model) can set `verified`: it requires
a passing sandbox test run recorded after the last code change.

## What it does

| | |
|---|---|
| **Ask / Investigate** | Read-only questions and root-cause analysis with `file:line` evidence |
| **Fix** | Plan → reproduce with a failing test → minimal edit → verify, in a per-task `git worktree` |
| **Review** | Reviews your uncommitted changes; a finding is "confirmed" only with a failing test behind it |
| **Approvals** | Plans, file edits and dependency installs can each require your approval (diff preview) |
| **Sandbox** | Tests run with `--network none`, read-only filesystem, non-root, CPU/memory/PID limits, timeout |
| **Hand-off** | Apply to your checkout (conflict-checked), download the patch, or open a draft GitHub PR |
| **Benchmark** | 12 seeded-bug repos with hidden tests; measures success, regressions, false "verified", cost |

## Status

| Milestone | State |
|---|---|
| M0 Foundation | Done |
| M1 Repository explorer | Done |
| M2 Read-only AI engineer | Done |
| M3 Planner / debugger loop | Done |
| M4 Patch, diff, approvals | Done |
| M5 Sandboxed tests | Done |
| M6 Code review | Done |
| M7 Reliability benchmark | Done. Live runs: Sonnet 5.5 **12/12 ($0.64)**, Haiku 5.5 **12/12 ($0.06)**, 0 regressions, 0 false "verified" — [details](docs/EVALUATION.md) |
| M8 GitHub | Done: import by URL, draft PR, CI checks |
| M9 UI + docs | Done |

165 tests (real Postgres and real Docker where they matter; no test can reach the network).
What is measured and what is not yet is in [docs/EVALUATION.md](docs/EVALUATION.md).

## Run it locally (Windows PowerShell)

Prerequisites: Python 3.12+, Docker Desktop (running), Node 20+, Git.

```powershell
cd "G:\nickel_proj\8 projects\verifix"
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Open `.env` and put your key after `ANTHROPIC_API_KEY=`. Then:

```powershell
docker compose up -d
alembic upgrade head
python -m verifix setup-sandbox
pytest
uvicorn verifix.main:app --reload --port 8010
```

`setup-sandbox` pulls `python:3.12-slim` and adds pytest once (the only step besides
dependency installs that uses the network). In a second terminal:

```powershell
cd "G:\nickel_proj\8 projects\verifix\web"
npm install
npm run dev
```

Open http://localhost:5178. Add a repository by its path (or a `https://github.com/...`
URL), then start a task.

## Benchmark

```powershell
python -m verifix cases
python -m verifix eval --validate-ruler
python -m verifix eval --solver agent --mode record --cassettes evals\cassettes\run1 --confirm-cost --out reports\run1.json --save
python -m verifix eval --solver agent --mode replay --cassettes evals\cassettes\run1
```

`record` spends API credit (up to `--max-cost`, default $1, per case) and saves every
model response; `replay` re-runs the same run for free. The UI's Benchmark page shows
saved runs.

## Layout

```
src/verifix/
  core/      settings (SecretStr), logging with redaction, database
  llm/       model boundary: protocol, Anthropic adapter, retry + circuit breaker, pricing, FakeLLM
  models/    repos, tasks, evidence, steps, approvals, findings, eval_runs
  repos/     path jail, safe git, file list/read, search, symbols, overview, registry
  agent/     loop, policy + budgets, workspaces, task store, report reconciliation, prompts
  tools/     typed tools the model may call (read, edit, test, report)
  sandbox/   Docker runner, pytest output parsing, FakeSandbox
  evals/     benchmark cases, grader, solvers, cassettes
  github/    REST client, clone / PR-branch / push
  api/       FastAPI routes: health, repos, tasks, evals, github
  cli.py     python -m verifix setup-sandbox | cases | eval
alembic/     migrations
tests/       network-blocked test suite
web/         React + TypeScript + Vite UI
docs/        architecture, security, evaluation, API, decisions, roadmap
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Evaluation](docs/EVALUATION.md)
- [API](docs/API.md)
- [Decisions (ADRs)](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
