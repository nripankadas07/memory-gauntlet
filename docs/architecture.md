# Architecture

The benchmark has four separable layers:

1. A versioned scenario loader validates deterministic JSON steps and replays a
   lightweight state model to prove each scored assertion has relevant setup.
2. `MemoryAdapter` defines write, correct, delete, query, and logical-clock
   operations.
3. The runner applies the same scenario to any adapter and retains every query
   observation.
4. The scorer derives metrics from explicit expected-visible and
   expected-hidden evidence. Report formats are projections of one canonical
   result.

The logical clock advances only through scenario steps. No wall time, random
value, model, embedding, or network service participates. SQLite is part of the
Python standard library. Adapter operation counters provide a portable cost
proxy without timing noise.

Every scenario starts with an empty adapter and logical clock. Multi-scenario
runs aggregate only counters and scored observations; records, identifiers,
retrieval results, and time never cross scenario boundaries. The adapter
contract's `fresh()` method preserves implementation configuration while
creating that isolated state.

Metrics without assertion denominators are represented as unavailable. The
composite averages only exercised dimensions and publishes both their names and
the resulting coverage ratio.

Semantic validation runs before an adapter is created. It binds correction,
deletion, TTL, role, and privacy assertions to affected records, operations,
logical time, query terms, and ACL context. The runner therefore never awards
coverage for absence assertions about invented data.

The governed adapter is a reference implementation, not the definition of a
correct architecture. Third-party adapters should conform to the abstract
contract and be tested by the same scenarios.
