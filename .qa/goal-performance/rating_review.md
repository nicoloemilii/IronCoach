# Independent history-index review

Scope: read-only audit of `app/services/rating_history.py` and its use in
`app/services/activity_rating.py`. No production files were edited. Synthetic
Python objects only: no database connection, provider access, personal data or
visual/browser verification.

## Differential result

`rating_review.py` compares the entire returned `_rating` dictionary using the
indexed context against the retained `_rating(..., history_context=None)` path.
The comparison includes score, components, prose, confidence, actions and limits.

Current source hashes and detailed results are in `rating_review.json`.

- 16,600 ratings compared; zero differences.
- 7,000 mixed cases include equal timestamps, future timestamps, sport aliases,
  missing/zero/negative durations, missing/zero/fractional loads, and three health
  sources across the Europe/Rome autumn DST transition.
- 9,600 additional cases exercise complete loads, duration fallback, large finite
  loads and decimal summation. The optimized code selected the conservative
  legacy distribution fallback 2,322 times.
- Both inspected source files stayed unchanged during the recorded run.

The shared loop advances the 42-day history before comparing a timestamp group,
then adds the whole group afterwards. This agrees with the legacy strict
`start_time < target_time` rule: equal-time sessions never become one another's
prior context. Expiry remains inclusive at exactly 42 days. The indexed weekly
range starts retain the legacy 7/14/21/28-day boundary membership.

The health cache is keyed by the Rome calendar day. This matches `_recovery`,
which depends on that day and the health index, not the session's sport or hour.
Its selected date interval retains all data used by the legacy latest-reading and
same-source baseline rules. The mixed differential matrix exercised multiple
same-day activities and same-date provider/manual readings.

## Observed non-blocking object-alias detail

`HistoryContext.recovery` returns the same mutable component dictionary for all
activities on a day. An explicit probe confirmed identity equality for the two
returned `recovery` components. Content is equal and current JSON output is
unchanged. The cache is request-local, so this is not a cross-request or workspace
data leak. A future caller mutating one returned component would also alter the
other result; returning a shallow dictionary copy would remove that coupling.
No present production consumer mutation was demonstrated in this review.

## Existing extreme-value behavior, reproduced in both paths

These are inherited behavior rather than indexed/legacy divergences:

- Thirty duration-only sessions with `duration_seconds = 1e-323` reach a duration
  median that becomes zero after conversion to minutes; both paths raise
  `ZeroDivisionError` during the dose ratio.
- Thirty duration-only sessions with `duration_seconds = 1e308` produce the same
  score in both paths but retain nonfinite words in a distribution explanation
  after the legacy sum overflows. The conservative index fallback preserves that
  legacy output.

Such finite values pass the current numeric predicate, although they do not
represent credible exercise durations. Any future correction should be explicit
validation/unavailability behavior with its own regression cases, rather than a
silent scoring change hidden inside this performance optimization.

## Verification limit

This audit directly checks the pure calculations and timestamp-group integration.
It does not independently prove diary query completeness, cursor isolation,
provider-identity resolution, HTTP transaction consistency or all possible binary
floating-point inputs. Those require the route/database tests and bounded stress
fixtures maintained by the root task. The passing matrix is evidence, not an
exhaustive proof.
