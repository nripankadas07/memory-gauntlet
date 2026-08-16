# Scenario reference

Every file uses `memory-gauntlet-scenario/v1` and contains:

- `id`: stable scenario identifier;
- `principals`: objects with `id` and `role`;
- `steps`: ordered operations.

Operations:

- `write`: requires actor, memory ID, text; optionally readers and TTL seconds.
- `correct`: owner replaces the current text and advances its version.
- `delete`: owner requests erasure.
- `advance`: moves the logical clock by non-negative seconds.
- `query`: supplies actor, query, category, and expectations.

Query categories are `recall`, `correction`, `deletion`, `ttl`, `role`, and
`privacy`. Expectations can include:

- `visible_ids`: memory IDs that must be returned;
- `hidden_ids`: memory IDs that must not be returned;
- `contains`: text fragments that must be present;
- `excludes`: text fragments that must be absent.

Every expectation field must be a list of unique, non-empty strings, and every
query must exercise at least one assertion. Recall queries require visible
evidence; correction queries require both current and stale evidence; deletion,
TTL, role, and privacy queries require hidden evidence. Query limits are
positive integers, TTL values are non-negative integers, and readers must name
a declared principal, declared role, or `*`.

Validation also evaluates the ordered scenario history before scoring:

- recall evidence must bind to an existing, accessible, query-relevant record;
- correction evidence must follow `correct`, bind visible current evidence and
  excluded stale text to the same record, and retrieve against both versions;
- deletion evidence must bind to a record deleted while it was live;
- TTL evidence must bind to a record with an elapsed TTL at that logical time;
- role/privacy evidence must bind to a live record whose ACL excludes the
  querying principal and role; and
- hidden IDs or fragments that never existed do not exercise a dimension.

Every expectation is bound independently; one valid hidden assertion cannot
legitimize additional invented assertions. Scenario IDs must be unique within a
multi-file validation, run, or comparison.

Writes cannot reuse a memory ID, and correction/deletion operations must target
an existing undeleted memory owned by their actor. These checks make malformed
state transitions input errors rather than adapter-dependent benchmark results.

Use only synthetic data. Do not add real personal, health, employment, or
credential information to fixtures.
