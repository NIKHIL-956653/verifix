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

Each case: a buggy repo whose visible tests pass, hidden tests the agent never sees, a
request written like a user's bug report, and a reference fix.

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

### Live model

Not yet run. Record one with:

```
python -m verifix eval --solver agent --mode record --cassettes evals/cassettes/<name> --confirm-cost --out reports/<name>.json --save
```

Results will be added here with the commit, model, date and cost. Nothing is reported
here that a run did not produce.

## Next

- More cases (20–30), including multi-file fixes and a dependency-install case.
- Fault injection: failing tool, invalid patch, provider 529, timeout.
- Strategy comparisons (prompt variants, plan approval on/off) on the same cases.
