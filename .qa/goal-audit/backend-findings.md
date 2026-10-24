# Backend audit evidence - 2026-10-19

All test requests use temporary SQLite and synthetic data. The runner sets
`IRON_DATA_DIR` under this QA directory and disables automatic provider sync.
No personal preferences, real activities, production DB data or provider state
were intentionally changed by this audit. No repository publication performed.

## Corrected failures

- Legacy feedback evaluation previously committed preliminary feedback before
  computing the final result. It now writes feedback, evaluation, adaptation and
  completion in one transaction, and restores previous state on failure.
  Both first save and edit failure were reproduced before the change.
- Legacy review and explicit activity matching used UTC calendar dates rather
  than the athlete's Europe/Rome dates. Both midnight regressions were reproduced.
- NaN/Infinity FTP inputs are rejected before calculations across Coach,
  activities and analysis endpoints. Readiness dates are typed and bounded;
  observed daily metrics cannot be dated in the future.
- Legacy profile partial updates validate known fields and nested object shapes
  while preserving valid custom fields and omitted nested values. Malformed
  profile writes cannot poison subsequent Coach reads.
- Validation responses omit raw personal input. Pydantic's raw NaN/Infinity
  input could previously make the default 422 JSON renderer throw a server error.
  Hosted mode keeps its own deliberately generic validation handler.
- Cancelled sessions no longer count as planned volume or consume available
  generation minutes. A cancelled 500-minute session previously blocked planning.
- Coach historical capacity now uses today's reference date; old archived
  activity no longer becomes the start of a seemingly recent 90-day window.
  Historical weeks and training days use Europe/Rome, reference dates exclude
  later observations, mixed offset/naive timestamps sort by instant, and 90-day
  windows contain 90 calendar days rather than 91.
- Experience tests now explicitly register the optional health/sync metadata
  they need; narrow test execution no longer depends on collection order.

## Verification already completed

- Final authoritative `.qa/goal-audit/backend-final-verified.log`: **755 passed**
  in 45.95 seconds, one pre-existing warning, after all current backend changes
  including the retrospective duplicate scan and performance guards.
- `.qa/goal-audit/backend-complete.log`: 702 backend tests passed, including
  training-insights and hosted isolation tests, before the five historical cases.
- `.qa/goal-audit/historical-before.log`: five historical regressions failed
  against the previous implementation.
- `.qa/goal-audit/historical-after.log`: 85 targeted backend tests passed after
  the historical fixes.
- `.qa/goal-audit/legacy-before.log`: all four new legacy transaction/calendar
  regressions failed before their fixes.
- `.qa/goal-audit/cancelled-before.log`: cancelled-volume regression failed
  before its fix; the focused 138-test run passed after it.

The only warning in the successful backend runs is the existing Starlette
deprecation notice about the test client's `httpx` dependency.

## Performance verification

On 1,200 synthetic activities plus 90 days of synthetic wellness, direct service
calls were profiled with fresh SQLAlchemy sessions. These timings include
cProfile overhead; they are comparative measurements, not production guarantees.

| Route | SQL before / after | Profiled ms before / after |
| --- | --- | --- |
| Coach today | 11108 / 80 | 7219 / 626 |
| Dashboard Home | 1290 / 72 | 992 / 258 |
| Activities | 5 / 5 | 1895 / 798 |
| Derived profile | 23 / 23 | 332 / 335 |

Coach/Home now batch the Intervals observation lookup into one query using a
ContextVar bound to the exact database session and read-operation lifetime.
Tests cover null versus absent and explicit zero, nested contexts, failures,
concurrent identical activity IDs in separate databases, malformed or nonfinite
legacy evidence, and unrelated future invalid observations. No cross-request or
cross-tenant cache is retained. Malformed evidence remains unknown and never
revives an older canonical load.

Activity rating week buckets use one chronological pass and plain read-only
value snapshots, preserving exact 7/14/21/28-day boundaries. All four benchmark
JSON payload byte counts match their pre-optimization measurements.

Authoritative artifacts: `performance-before.json`, `performance-after.json`,
the corresponding `.profile.txt` files and
`performance-after-source-sha256.json`, all below this QA directory.

## Test environment note

Windows `mkdir(mode=0700)` replaces the inherited workspace ACL. Under the tool's
restricted identity this made pytest unable to reopen its own directories.
`run_backend.py` changes that mode only for disposable directories below this
QA root so they inherit workspace ACLs, and routes TMP/TEMP there as well.
Application code and test assertions remain unchanged. Earlier temporary-folder
errors are environment failures, not passing or failing product behavior.

Browser rendering, real provider integration and physical Garmin execution are
not verified by backend tests. The root task owns those separate checks.
