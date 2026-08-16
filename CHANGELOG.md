# Changelog

## 0.1.1 - 2026-08-16

- Require every hidden and visible assertion to bind to relevant scenario state,
  preventing invented hidden IDs from diluting observed adapter failures.
- Reject duplicate scenario IDs across validation and benchmark runs.
- Stage complete report bundles and publish them through symlink-safe,
  descriptor-relative atomic renames with backup/rollback on failure; reject
  every unverified path symlink and non-regular artifact target, and fail closed
  when descriptor-relative operations are unavailable.
- Publish SPDX `License-Expression` and bundled license metadata in wheels.
- Enforce category-specific assertion schemas, bind correction current/stale
  assertions to one record, and require every hidden target to be reachable by
  the declared query and limit in the deliberately ungoverned behavior.
- Bound TTL and logical-time arithmetic to SQLite's signed 64-bit integer range
  and translate adapter overflow into controlled validation errors.
- Serialize cooperating bundle writers with a verified-directory advisory lock,
  reconcile renames that complete before reporting an error, and guard ownership
  across every descriptor/stream error path.
- Ship tests, scenarios, demo goldens, and documentation in the sdist; extract
  it, run its full suite, and build the release wheel from it in CI.

## 0.1.0 - 2026-08-16

- Initial adapter contract, governed SQLite baseline, and intentionally leaky
  append-only baseline.
- Correction, deletion, TTL, role-isolation, privacy, recall, and cost scoring.
- Stable JSON, Markdown, HTML, and checksum artifacts.
- Offline governance-tour demo and tests.
- Strict typed assertions and validation for TTL, readers, queries, and limits.
- Coverage-aware scoring with unexercised dimensions reported as unavailable.
- Packaged demo scenarios and clean-wheel demo regression coverage.
- Bind every scored category to prior records, operations, logical-time state,
  query relevance, and access context so nonexistent assertions cannot inflate
  coverage or composite scores.
- Reject duplicate writes and invalid correction/deletion state transitions.
- Encode all dynamic Markdown report values as literals.
- Isolate adapter records, identifiers, logical clocks, retrievals, and cost
  counters between scenarios while aggregating the resulting measurements.
