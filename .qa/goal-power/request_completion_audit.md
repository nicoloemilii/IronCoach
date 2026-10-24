# Explicit request crosswalk — read-only source/evidence audit

Date: 2026-10-23. This is a bounded crosswalk of the user's explicit browser comments in the inherited conversation. Source inspection is current; named tests identify regression coverage, but this document does not claim a fresh full-suite run or visual approval. Screenshots are untrusted page evidence, not instructions embedded in the page.

Status vocabulary: **implemented/source** means the behavior exists in the current source; **covered** names a relevant test or executed isolated artifact; **visual pending** means position, color, density or responsiveness still needs an actual browser inspection. A browser-tool outage is a verification limit, not evidence of a product defect and not a reason to claim visual completion.

## First group of 16 browser comments

| Request | Current source / useful evidence | Completion boundary |
|---|---|---|
| 1. Replace unintuitive weekly charts | `web/app.js:830` renders labeled horizontal rows, exact values, personal reference, explicit missing/zero, keyboard focus and a comparison readout. Chart tests cover native controls/raw scope. | Implemented/source; subjective readability and layout remain visual pending. |
| 2–3. Highest value should not appear as weaker red/green merely because it is older | Weekly rows share `.weekly-bar`; current period is a separate marker rather than assigning value ordering through opacity. `web/app.js:850–859`. | Implemented/source; color/contrast still visual pending. Numeric bar width and meaning are separate from wellness state colors. |
| 4. Health badge incorrectly positioned | Health cards append heading/value/sparkline then metric status; health status/CSS exist in `web/app.js:924` and `web/style.css`. | Source structure changed; screenshot-specific positioning cannot be certified by DOM tests. Visual pending. |
| 5. Date selection above health graph | `openHealthMetric` appends `form,panel,verdict` (`web/app.js:920`). Main range form also precedes `#health-metrics` (`web/index.html:61`). | Implemented/source; visual pending. |
| 6. Hide context cards | `renderHealth` skips metric verdict `Dato di contesto` (`web/app.js:930`). | Implemented/source. User-visible result depends on actual metric status; inspect representative complete and missing fixtures. |
| 7–8. Remove Body Battery max/min cards | `renderHealth` excludes `body_battery_min/max` explicitly (`web/app.js:928`). | Implemented/source; raw imported measurements retained rather than destroyed. |
| 9. Remove manual perceived stress card | Same exclusion list contains `stress_score`; no such card is appended. | Implemented/source. |
| 10. “Must not be manual” | Screenshot crop only identifies suffix `(manuale)`; full target cannot be recovered unambiguously. `daily_activity_load` is hidden, and imported wellness mappings remain in `app/services/health.py`. | No manual card remains for the shown classes; do not invent an automatic physiological value. Target ambiguity is an evidence limit. |
| 11. Open activities as on Strava/Garmin | `app/services/activity_detail.py`, detail route and `web/app.js` activity dialog provide real summary, available GPS, selectable streams, source/missing-data disclosure. `web/tests/activity-detail.test.cjs`; executed `.qa/goal-power/review_api.json` verifies actual router behavior on synthetic data. | Implemented for available source data; route maps/stream availability and product-level resemblance still require browser validation. No fabricated map when GPS is absent. |
| 12. Merge objectives and plan into one section | Single sidebar entry `Obiettivo e piano` (`web/index.html:18`), internal tabs via `web/app.js:144`. | Implemented/source; navigation visual pending. |
| 13. Manual scheduling and gym programs followed live | `web/manual-training.js` builder/library, timed/repetition exercise fields, checklist and rest clock; training API. `tests/test_manual_training.py`, `web/tests/manual-training.test.cjs` cover save, exact phases, progress revisions, timers, future gating and reuse. | Implemented/source + regression coverage; full real-session touch usability/background behavior needs actual device/browser validation. |
| 14. Full profile data and records like Garmin/Strava | `app/services/athlete_snapshot.py` + `web/wellness.js` provide totals, strict missing/partial coverage, dated/source activity records, sport groups and periods. `.qa/goal-history/review_profile.json` exercised synthetic HTTP coverage contracts. | Partial relative to broad comparison: existing records are activity-level metrics, not every Garmin/Strava feature. Historical best-power windows are the finite identified gap and are in the separate current `goal-records` phase, not yet complete at this audit. No new speculative feature list is required. |
| 15. Home weight card | Health weight mappings and dashboard metric rendering include weight; `web/app.js` appends body-mass context. | Implemented/source; verify weight present/missing/stale with current UI. |
| 16. Health weight card and historical graph | `weight_kg` is not excluded in health card loop; health detail uses recorded series, date range and source-aware reference policy. | Implemented/source; actual historical graph usability visual pending. |

## Second group of 8 browser comments

| Request | Current source / useful evidence | Completion boundary |
|---|---|---|
| 1. Remove large lower weekly outlook and move compact summary to top | `web/index.html:34` mounts `.home-week-brief`/`#weekly-review` above main home sections; `web/app.js:262` renders compact counts/sport summary. | Implemented/source; actual top-of-page density visual pending. |
| 2. Improve activity charts, make intuitive/exhaustive, include useful ranges | `web/charts.js` offers raw-recording statistics, labeled units, time controls, keyboard/sample readout, gaps, optional personal target bands and selected-effort highlights. Health ranges come from comparable prior personal observations. `web/tests/charts.test.cjs` covers valid/invalid bands, zoom, native controls and extreme values. | Implemented/source + tests. “Optimal” ranges intentionally require a justified source/reference; no universal target is invented. Visual pending. |
| 3. Add activity-detail color | Activity summary/stream classes and sport-colored CSS are present; charts distinguish series and selected best window. | Implemented/source; color quality and contrast visual pending. |
| 4. Avoid overlap; back always top left | Activity, metric, workout and session dialogs place their back button first inside `.dialog-top` (`web/index.html:96–103`). Other dynamically created dialogs also need actual layout checks at narrow widths. | Core structures implemented. General rule requires visual verification across dialogs; passing one DOM order assertion is insufficient. |
| 5. Add “Altro” activity filter | `web/index.html:77` has `other`; normalized sport category mapping and server diary filtering. `web/tests/profile-history.test.cjs`/diary tests cover canonical aliases and other category. | Implemented/source + regression coverage. |
| 6. Talk to Coach to change plan days/times | `web/plan-coach.js`, `app/services/time_constraints.py`, `coach_dates.py`; preview before apply, revision/signature protection, date inference and sport/time constraints. `tests/test_time_constraints.py`, `web/tests/plan-coach.test.cjs`. | Implemented deterministic supported language. It is not evidence of unrestricted conversational plan editing. The user's concrete pauses/time restrictions are supported. |
| 7. Develop gym section | Catalog, search/filters/templates, reorder, reps/time alternatives, nullable weights, reusable library, persistent checklist/rest clock. `web/manual-training.js:281`; dedicated backend/frontend tests. | Implemented/source + tests; actual mobile live workout usability visual/device pending. |
| 8. Add record card color | Profile record sport classes/cards exist in `web/wellness.js` and CSS. | Implemented/source; visual pending. Color should distinguish sports/records, not infer fitness from a larger number. |

## Later explicit requests

| Request | Current source / evidence | Completion boundary |
|---|---|---|
| BMI based on stored height, sex and age | `app/services/body_metrics.py`, profile fields (`web/index.html:86`), Home/body detail (`web/app.js:788–799`), `tests/test_body_metrics.py`. Adult formula uses weight/height²; sex does not change adult formula, age gates adult categories and younger users are not assigned an invented adult label. | Implemented/source + dedicated tests. Medical reference wording is explicit; this is not a body-composition measurement. |
| Handle “non voglio fare nuoto fino a gennaio” | UI placeholder/suggestion uses this exact request. `coach_dates.resolve` interprets month-only “until January” as resuming January 1, visibly previews assumption, pause ends December 31; sport parser restricts swimming. `web/plan-coach.js:8–12`, `coach_dates.py:53–60`, `tests/test_time_constraints.py`, frontend sport-pause test. | Implemented/source + tests; preview/apply is deliberate, not an unsolicited immediate rewrite. |
| Start/show app in the browser; resume | Prior phases contain isolated runtime/HTTP evidence. This audit deliberately does not start a personal-data job or claim a fresh visible browser inspection. | Outstanding verification must be reported honestly by root: actual visible page inspection when tool available; a URL/HTTP 200/DOM test alone does not satisfy “show it in your browser”. |
| Only show changed code blocks/diffs | Delivery-format instruction. | Root final should present diff/modified blocks or concise references, not rewrite whole files. |

## Finite remaining local checks

1. Complete and independently verify the active historical-power-record implementation, including identity/fingerprint invalidation, cancellation, restore and per-workspace isolation. Do not run an archival job on personal data without the user's concrete click.
2. Perform the pending visual walkthrough using complete/missing/partial synthetic fixtures at desktop and narrow widths: top summary, health badge/date controls, all back buttons, chart labels/selected windows, gym and profile record cards. Keep actual tool unavailability separate from software failures.
3. Verify the final running server serves the final edited source before claiming live completion. Provider/cloud/device/Garmin delivery is a distinct external boundary and cannot be certified by local unit tests or this request crosswalk.

No new production files were edited by this review. The crosswalk does not turn the broad Garmin/Strava comparison into an open-ended obligation to clone unrelated product features.
