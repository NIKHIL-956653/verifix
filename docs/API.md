# API

Base URL in development: `http://localhost:8010` (the UI calls it through the Vite proxy at
`/api`). Interactive docs with every schema: `http://localhost:8010/docs`.

## Health

| Method | Path | |
|---|---|---|
| GET | `/health` | `status` ok/degraded, `database`, `anthropic` configured/missing (never the key), `model_main` |

## Repositories

| Method | Path | |
|---|---|---|
| POST | `/repos` `{path}` | Register a local git repo (idempotent). 422 for non-git, outside `VERIFIX_ALLOWED_ROOTS`, or unsafe git config |
| GET | `/repos` | List |
| GET / DELETE | `/repos/{id}` | Get / forget (files on disk untouched) |
| GET | `/repos/{id}/overview` | Languages, frameworks, test runner, manifests |
| GET | `/repos/{id}/files` | Files (deny-list applied; `hidden` = withheld count) |
| GET | `/repos/{id}/file?path&start&end` | Numbered lines |
| GET | `/repos/{id}/search?q&regex&case_sensitive&glob` | `path:line: text` hits |
| GET | `/repos/{id}/symbols?name|prefix` | Python symbol index |
| GET | `/repos/{id}/git/status`, `/git/diff?staged&base` | Read-only git |

## Tasks

| Method | Path | |
|---|---|---|
| POST | `/tasks` `{repo_id, kind, request, policy?, budget?}` | `kind`: ask, investigate, fix, review. Starts in the background |
| GET | `/tasks?repo_id` | List |
| GET | `/tasks/{id}` | Status, counters, plan, report, `pending_approval` |
| GET | `/tasks/{id}/steps?after=<seq>` | Timeline (poll with `after`) |
| GET | `/tasks/{id}/evidence`, `/evidence/{seq}` | Ledger; one item with content |
| GET | `/tasks/{id}/findings` | Review findings |
| GET | `/tasks/{id}/diff`, `/patch` | Workspace diff (JSON) / downloadable patch |
| POST | `/tasks/{id}/approvals/{approval_id}` `{approve, reason?}` | Decide; a decision is final (409 on repeat) |
| POST | `/tasks/{id}/cancel` | Cancel a running or paused task |
| POST | `/tasks/{id}/retry` `{budget?}` | Continue a failed or budget-exhausted task from its saved conversation |
| POST | `/tasks/{id}/apply` `{confirm: true}` | Apply to your checkout (conflict-checked, not staged) |
| DELETE | `/tasks/{id}/workspace` | Remove the worktree |

Policy: `{approve_plan: bool, write: ask|auto, execute: ask|auto, install: ask|auto}`.
Budget: `{max_model_calls, max_tool_calls, max_input_tokens, max_output_tokens, max_cost_usd, max_minutes}`.

Task statuses: `queued, planning, awaiting_approval, running, completed, fixed_unverified,
verified, failed, cancelled, budget_exhausted`.

## GitHub

| Method | Path | |
|---|---|---|
| GET | `/github/status` | Whether a token is configured |
| POST | `/github/import` `{url, confirm: true}` | Clone `https://github.com/owner/repo` into `VERIFIX_CLONES_DIR` and register |
| POST | `/tasks/{id}/pull-request` `{confirm: true, title?, base?, draft?}` | Push `verifix/pr-<id12>` (source_head + patch) and open a PR |
| GET | `/tasks/{id}/pull-request/checks` | CI check runs and an overall verdict |

## Benchmark

| Method | Path | |
|---|---|---|
| GET | `/evals/cases` | Case list (no hidden tests or reference fixes) |
| POST | `/evals` `{solver: reference|noop|agent, cases?, name?, confirm_cost, max_cost_usd_per_case}` | Start a run (202). `agent` requires `confirm_cost: true` |
| GET | `/evals` | Runs with summaries |
| GET | `/evals/{id}` | Summary + per-case results; `summary.state` running/finished/failed |
