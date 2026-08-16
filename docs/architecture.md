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
coverage for absence assertions about invented data. Every assertion is bound
individually, so adding nonexistent hidden IDs cannot dilute a real failure.
Each category permits only its meaningful assertion fields. Correction evidence
must bind current and stale text to the same record, while a hidden target must
also appear in the deliberately ungoverned adapter's deterministic top results
for the scenario's declared query and limit. Scenario identifiers must be unique
within a validation or benchmark run. TTL and logical-clock arithmetic is
bounded to SQLite's signed 64-bit integer range before adapter mutation.

The complete report bundle is staged in unique regular files and flushed before
publication. A verified-directory advisory lock serializes cooperating writers
from preflight through cleanup. Existing artifacts are moved to private
backups; publication uses descriptor-relative atomic renames and restores the
complete original set if any publication fails. A rename that completes before
reporting an error is reconciled from source/target inode state. Temporary and
backup files are then removed, and file descriptors/streams have explicit
single-owner cleanup on every failure path. Every directory component is
identity-checked and must be a real directory; Darwin's `/var`
system alias is normalized only after ownership, target, and identity checks.
Non-regular artifact targets are rejected before staging, and platforms without
the required descriptor-relative filesystem operations fail closed.

The source distribution contains the complete test corpus, scenarios,
documentation, and deterministic demo artifacts. Packaging tests extract the
sdist, run its full suite, and build the wheel from that extracted tree.

The governed adapter is a reference implementation, not the definition of a
correct architecture. Third-party adapters should conform to the abstract
contract and be tested by the same scenarios.
