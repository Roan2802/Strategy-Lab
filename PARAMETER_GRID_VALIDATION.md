# Parameter grid validation — 2026-11-07

**Result: PASS for tested bounded-grid plumbing; strategy quality is not established.** Parameter enable/fix controls, numeric Start/Stop/Stap fields, strategy parameter bounds, and trial parameter persistence were checked in the UI and Duka-only optimization runs.

## Evidence

- Bounded Grid and Random runs, deterministic Random seed repeat, resume from 3 to 5 trials, and exact best-trial parameter reuse are detailed in `OPTIMIZER_DIAGNOSTICS.md`.
- Duka XAUUSD M1 run `f06fb1b8-e636-4ed2-8968-c4d3ba36e828` exercised a user-defined entry-interval range 60–120 with step 60 while the other seven parameters were fixed. The saved trial stayed within the requested domain; its train and holdout results were negative.
- Existing grid diagnostics report 768 raw combinations and 512 Spot-feasible combinations for the documented space. Categorical constraints are handled separately from numeric bounds.
- The tester UI exposes nine LabBreakout parameters. Current labels are Start, Stop and Stap; persisted/API keys remain `min`, `max` and `step`.

## Limits

The results do not demonstrate profitability. Continuous Random spaces can repeat values; Grid endpoints may be used when a parameter has no explicit step. Generic historical margin preflight, multiworker execution, TPE sampler-state resume, multi-objective optimization, and robustness of selected parameters across unseen periods remain unverified or unsupported. Duka data only was used; no live/paper trading was run.
