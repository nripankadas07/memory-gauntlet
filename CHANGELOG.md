# Changelog

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
