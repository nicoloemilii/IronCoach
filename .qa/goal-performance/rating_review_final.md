# Final independent verification after numeric/cache repairs

This supplements the earlier read-only review; its artifacts are preserved.
No production source was edited by this verification task.

- Full backend suite: **1,152 passed**, one existing Starlette/httpx deprecation
  warning. Process exited successfully. Log: `backend-final-v2.log`.
- Independent differential run: **16,600 complete rating dictionaries compared**,
  zero indexed/retained-legacy differences and 2,322 conservative fallbacks.
  Report: `rating_review_final.json`; source hashes stayed stable during the run.
- Same-day recovery results are now separate dictionaries. Changing one returned
  component did not modify the other result.
- The reproduced `1e-323` duration case now returns unavailable score rather than
  raising. The `1e308` duration case returns finite JSON and explanatory text.
  Both indexed and retained calculation paths agree after the explicit repairs.

## Six cached-baseline comparisons on final sources

`benchmark_optimized_final.py` reran the unchanged synthetic fixtures against the
cached original baseline outputs, checking the gzip payload SHA and exact
canonical JSON bytes, including prose, components, scores and confidence.

| History | Activities | Baseline seconds | Final seconds | Exact JSON |
|---|---:|---:|---:|---|
| Dense, 35 elapsed days | 1,000 | 0.655726 | 0.066103 | Yes |
| Dense, 35 elapsed days | 3,000 | 4.552104 | 0.163820 | Yes |
| Dense, 35 elapsed days | 10,000 | 45.921326 | 0.530348 | Yes |
| One activity per 12 hours | 1,000 | 1.303343 | 0.141761 | Yes |
| One activity per 12 hours | 3,000 | 10.265956 | 0.430746 | Yes |
| One activity per 12 hours | 10,000 | 117.475880 | 1.492439 | Yes |

All six checks passed. Each calculation issued three SELECT queries with supplied
activity/load fixtures. The final source hashes and original baseline metadata
remained unchanged. Complete metadata: `optimized_timings_final.json`; terminal
output: `benchmark-final.log`. Earlier `optimized_timings_v2.json`, baseline
timings and compressed baseline outputs were not overwritten.

These timings include rating SQL and computation, excluding synthetic fixture
setup, serialization and digest writing. They are local measurements, not HTTP
latency guarantees or a universal complexity bound. The full suite and the
independent differential process had both finished before the six final timed
runs started. No personal database, provider or browser was used by these runs.
