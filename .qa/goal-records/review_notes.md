# Historical power records — independent review notes

Date: 2026-10-23. Read-only production review; private synthetic QA only. No personal database/provider or historical job was accessed. Initial notes precede the new module landing and are design checks, not allegations that unimplemented code is defective.

## Existing source constraints confirmed

- `app/commercial/mutations.py` currently serializes HTTP mutations with an `asyncio.Lock`, per account in hosted mode and global locally. The lock is held only for request lifetime. A background thread cannot simply acquire this loop-bound lock as a synchronous mutex.
- `app/services/backups.py:401` restores allowlisted personal tables in one `BEGIN IMMEDIATE` transaction after preview fingerprint validation and an online SQLite snapshot. Existing special cleanup removes Garmin delivery and duplicate-review state, not arbitrary new tables.
- `tables_for` only includes `CORE_TABLES + PROGRESS_TABLES`. Derived historical curves/jobs should remain outside the portable JSON allowlist; an old backup must continue to validate. Excluded caches still need explicit atomic invalidation on restore.
- `ActivityObservation` has id/activity_id/source_key/payload_json but no modification timestamp or source hash. Fingerprint validation must therefore actually cover selected payload bytes or a trusted equivalent revision; row id/source key alone cannot prove unchanged streams.
- `current_session` honors `WORKSPACE`; a worker should capture `sessionmaker(bind=request_db.get_bind())` and `copy_context()` at launch, never share the request `Session` across threads and never silently fall back to global `SessionLocal` in hosted processing.

## Restore/concurrency design recommendation sent to root

Use a synchronous mutex keyed by the actual DB/workspace identity around short derived-cache writes and restore replacement. Under that mutex, restore should clear derived curves and invalidate running/queued jobs in the same successful DB transaction, or atomically bump a database generation token. A worker must re-open/re-read its job token/generation and original source fingerprint before upsert. Use an unguessable job token to prevent the ABA case where restored integer ids are reused. No lock may be held through provider calls or raw-curve computation.

A fingerprint-only check does not detect a restore that reintroduces byte-identical source data; the numerical curve could remain valid, but an old worker must not mutate a new job/status row. An id-only check is insufficient after a restore. SQLite serializing individual writes also does not serialize the earlier read/compute/write decision.

## Required independent synthetic cases after implementation lands

1. Canonical observation wins over a newer alias; equivalent-load aliases with different full payloads do not authorize arbitrary stream selection.
2. GET records: only read SQL, no provider calls, no curve CPU work; no ORM changes or schema DDL. Job poll reads one selected job only.
3. Multiple workspaces with identical activity/job ids cannot see or overwrite each other's records/status or use another workspace's credentials. Verify captured context as well as captured DB bind.
4. Global worker/queue bounds; one active queued/running job per workspace. A full queue rejects without leaving a permanently active DB row or leaking the per-workspace lock.
5. Modify/delete/merge selected source after snapshot but before upsert: old result discarded. Perform an atomic restore during a paused calculation: no old-generation cache/status write occurs afterward.
6. Cancel during one activity: finish only current bounded work, save permitted current result if contract says so, then stop before starting the next activity; status and progress remain truthful.
7. Provider authentication/429 fatal response: stop once with a human-safe message, retain prior completed records, no retry storm, no credential/error-body leaks. Local-only mode never attempts provider calls.
8. Future activity and future samples are excluded; stored source identity/hash, algorithm version, time-window fingerprint and source provenance correspond to data actually used. No raw stream cache is persisted in the new derived table.
9. Old/new portable backups exclude derived table rows and remain valid. Restore transaction failure preserves original tables and existing cache/job state atomically. Startup marks abandoned running jobs interrupted without launching CPU/provider jobs.
10. Records candidates expose dates/source and actual valid-cache coverage. Missing/ambiguous/stale/unanalysed activities do not become zero-watt records or complete archive claims.

## Implemented scope explicitly delegated to this reviewer

Root subsequently delegated only `app/db/effort_models.py`, `effort_schema.py`, `effort_lock.py`, their migration hook, and schema/regression tests. These now define the agreed two derived tables and UUID job model; no raw-stream column is added. `database_key` canonicalizes SQLite file paths, and `database_lock` returns a shared synchronous RLock with weak registry values. In-memory engines remain distinct. `ensure_effort_schema` backs up an existing database before new-table DDL, refuses to proceed if backup fails, and is idempotent without a new version beyond1/2/3.

Root owns all worker/API/restore changes. The implemented restore removes both derived tables' rows within the replacement transaction under the same DB mutex. Worker writes re-read the UUID job and selected-source fingerprint in a short `BEGIN IMMEDIATE` section.

## Executed synthetic evidence

- `tests/test_effort_schema.py`:6 cases verify exact model columns/no raw field, idempotency, backup contents from before the DDL, failure-before-DDL, migration preserving schema versions1/2/3 and original activity, portable backup omission, and real two-thread mutex contention across two engines pointing at one file.
- `tests/test_effort_jobs_review.py`:9 independently authored lifecycle cases. Payload mutation with the same observation id/key is discarded; running cancellation finishes only the current activity; deleted fixed candidates count unavailable; shutdown prevents a late write; two SQLite databases with identical ids preserve copied WORKSPACE/WORKSPACE_KEY and separate results; a fatal429 stops after the current activity and preserves an earlier derived curve; actual export/preview/restore during paused computation removes the job UUID and prevents its old worker recreating any cache;10 simultaneous database scans demonstrate2 running plus8 waiting, the11th rejected without orphan row, queued cancellation doing no analysis, and admission capacity reusable afterward. An additional explicit refresh429 case compares every existing remote-cache column and confirms exact preservation, including JSON, identity/hash and all provenance timestamps.
- Latest focused execution:9 worker tests passed,17.38s while root's full suite was also running. Before the extra refresh429 case, the combined worker/schema run passed14 tests,8.15s. Earlier schema+migration+backup sweep:45 passed. These are temporary synthetic file/in-memory databases; no personal database/provider was opened. Fatal-provider behavior is injected at the analysis boundary, not a live provider request.
- `.qa/goal-records/review_api.py/json` executes the real new FastAPI router over a separate synthetic SQLite file, fixed2026-10-23T00:30Z. A stored-only explicit scan completes7 candidates and leaves original activity/observation rows unchanged. GET before/after indexing is tested while the curve calculator and provider are tripwires: no CPU curve work, external calls, DML or schema DDL. Canonical streams beat newer999W aliases; ambiguous equal-load aliases do not produce a curve; zero remains a measured record; a future activity is excluded; the exact Europe/Rome28-day boundary and one microsecond before it produce different period inclusion as expected. Same-row payload mutation invalidates a cached record, and a deleted activity's orphan cache is excluded. Production hashes match before/after the executed run; the report contains them. This is HTTP protocol validation through TestClient, not visual browser approval.
- Suggested private/no-store headers for mutable/personal records and polling were added by root. No visual inspection is claimed.

Remaining: root's full-suite/runtime/visual validation and any final lifecycle changes require their corresponding evidence. In particular, this review does not claim an actual provider retrieval, personal historical scan or device test. These notes do not claim visual validation.
