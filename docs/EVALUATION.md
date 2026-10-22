# Evaluation

The agent is measured, not demoed.

## Principles

1. **Validate the ruler before trusting the score.** The reference fix must score 100%,
   an empty patch 0%, and every hidden test must fail on the buggy code. If any of these
   fails, the benchmark is broken, not the agent. (Lesson from rag-curriculum, where a
   whitespace-sensitive check reported 38 of 41 false misses.)
2. **Outcomes come from execution, not the model.** Success = hidden tests pass and no
   previously passing test fails, in the sandbox. The grader never reads the agent's report.
3. **Replayable.** Every model response can be recorded to a JSONL cassette and replayed
   for free; replay refuses to continue if the agent's conversation diverges.

## Benchmark cases

| Case | Bug class |
|---|---|
| login-missing-email | input validation (KeyError → 500) |
| pagination-off-by-one | off-by-one |
| missing-auth-check | authorization |
| swallowed-exception | error handling |
| discount-rounding | numeric (half-up rounding; banker's `round()` fails the hidden test) |
| date-range-inclusive | boundary |
| mutable-default | shared state |
| sql-injection | security (parameterised query; `O'Brien` must still work) |
| csv-export-escaping | escaping (RFC 4180; the fix spans two files) |
| rate-limit-stale-window | stale state (vague report: "users blocked on their first request of the day") |
| free-shipping-units | unit mismatch (cents vs dollars across two modules) |
| filter-after-pagination | operation ordering (filter applied after slicing) |

Each case: a buggy repo whose visible tests pass, hidden tests the agent never sees, a
request written like a user's bug report, and a reference fix. The last four are a
harder tier (multi-file fixes, vaguer reports) added after the first live run, which
covered only the original eight.

## Metrics

| Metric | Definition |
|---|---|
| Success rate | Patch applies, all hidden tests pass, no regressions |
| Regressions | Visible tests that passed on the buggy code and fail after the patch |
| Patch precision | Expected source files ∩ changed source files / changed source files (tests excluded) |
| False verified | Agent's status was `verified` but the grader says not solved |
| Verified precision | Of tasks the agent marked `verified`, the fraction actually solved |
| Evidence coverage | Fraction of report claims citing existing evidence ids |
| Tool reliability | 1 − failed tool calls / tool calls |
| Cost, time | USD and seconds per case |

## Results

### Ruler validation — 22 Oct 2026, local Docker sandbox

`python -m verifix eval --validate-ruler` (also `test_ruler_reference_scores_one_noop_scores_zero`):

| Check | Result |
|---|---|
| Reference fix | 8 / 8 solved (100%) |
| Empty patch | 0 / 8 (0%) |
| Hidden tests fail on the buggy code | 8 / 8 |
| Regression detection | a patch that passes the hidden tests but breaks a visible one is graded "not solved" with the regression named |

### Pipeline check with a scripted model — 22 Oct 2026

Two cases through the real agent loop, real workspaces and real sandbox, with the model
replaced by a fixed script (`test_agent_solver_end_to_end_with_record_replay`):

| Case | Script | Agent status | Graded |
|---|---|---|---|
| login-missing-email | reproduce with a failing test, fix, run suite | verified | solved |
| pagination-off-by-one | cosmetic edit, run visible tests, claim fixed | verified | **not solved → false verified** |

This shows the benchmark catches an overconfident agent: verifix's own reconciliation
can only check that tests ran and passed; the hidden tests show the fix was wrong. The
same run replayed from its cassettes gives identical results.

### First live run and a case correction — 22 Oct 2026

The first live run (Sonnet 5.5, `login-missing-email` only, $0.06) was graded "not
solved / false verified": the agent fixed the missing-email case the report described,
but the hidden tests also required rejecting a blank email (`"   "`), which the request
never mentioned. That was a benchmark defect, not an agent one — hidden tests must only
check what the request asks for. The case's request now names the blank-email
requirement too. Recorded here because changing a case invalidates comparisons with runs
made before the change.

### First live run (original 8 cases) — 22 Oct 2026

Model `claude-sonnet-5-5`, fully autonomous, recorded to `evals/cassettes/run1`
(eval run `f0b01ffc`, `reports/run1.json`), on a Windows 11 machine with Docker
Desktop: **8/8 solved, 0 regressions, 0 false "verified", patch precision 1.0,
evidence coverage 1.0, $0.40 total, 24 s per case.**

An earlier attempt at this run failed 6 of 8 cases with "model provider unavailable":
one case hit a network failure reaching Anthropic, and the shared client's circuit
breaker stayed open for every case after it (total spend $0.09, since the dead cases
spent nothing). Fixed by giving each benchmark case its own client, so a breaker trip
cannot outlive the case that caused it. One side-finding from that broken run: the agent
had already written a correct fix for `discount-rounding` before the connection died, and
the grader scored the patch "solved" even though the agent's own status was `failed` —
the grader really does judge only the patch.

### Two models, all 12 cases — 22 Oct 2026

Fully autonomous, the same 12 cases (including the harder tier), recorded to
`evals/cassettes/haiku1` and `run2` (eval runs `cba15fee` and `2b2b522f`).

| Metric | claude-haiku-5-5 | claude-sonnet-5-5 |
|---|---|---|
| Success rate | **12 / 12** | **12 / 12** |
| Regressions | 0 | 0 |
| False "verified" | 0 | 0 |
| Patch precision | 0.958 (one case touched an extra file) | 1.0 |
| Evidence coverage | 1.0 | 1.0 |
| Tool reliability | 0.861 (fumbled tool calls, recovered) | 0.984 |
| Total cost | **$0.06** | **$0.64** |
| Mean time per case | 29 s | 25 s |

Reading, honestly: both models saturate this benchmark, so it no longer separates
them on success rate — only on cost, patch tidiness and tool reliability, where the
10x-cheaper Haiku is messier but still lands every fix. The bugs are 1-2 file fixes
with reports that name the failure; the benchmark needs a genuinely hard tier (larger
repos, misleading reports, bugs the visible tests don't hint at) before a 100% here
can mean "ready for real work". That is the top item under Next.

## Next

- More cases (20–30), including multi-file fixes and a dependency-install case.
- Fault injection: failing tool, invalid patch, provider 529, timeout.
- Strategy comparisons (prompt variants, plan approval on/off) on the same cases.
