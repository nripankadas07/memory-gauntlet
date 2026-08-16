# memory-gauntlet

`memory-gauntlet` is a deterministic, offline governance benchmark for agent
memory. It tests whether a memory adapter recalls current information while
respecting corrections, deletion, TTL expiry, role boundaries, and privacy
expectations.

The repository includes two zero-dependency reference adapters:

- `governed-sqlite`: current-version SQLite storage with owner mutation,
  deletion, TTL, and reader/role enforcement;
- `leaky-append-only`: an intentionally unsafe baseline that ignores expiry,
  deletion, and access boundaries and retains stale versions.

The contrast proves that the benchmark detects failures instead of merely
producing a flattering score.

## Quick start

```bash
python -m pip install -e .
memory-gauntlet demo --out artifacts/demo
```

Or without installation:

```bash
PYTHONPATH=src python -m memory_gauntlet demo --out artifacts/demo
```

Open `artifacts/demo/report.html`. The demo is synthetic, local, keyless, and
reproducible.

## Commands

```text
memory-gauntlet validate SCENARIO.json [...]
memory-gauntlet run SCENARIO.json [...] --adapter governed|leaky --out DIR
memory-gauntlet compare SCENARIO.json [...] --out DIR
memory-gauntlet demo --out DIR
```

## Scenario contract

Scenarios are versioned JSON event streams. They declare principals and roles,
then apply `write`, `correct`, `delete`, `advance`, and `query` steps. Each query
has a category and explicit visible/hidden IDs or text expectations.

```json
{
  "schema_version": "memory-gauntlet-scenario/v1",
  "id": "small-example",
  "principals": [{"id": "alice", "role": "product"}],
  "steps": [
    {"op": "write", "actor": "alice", "memory_id": "tz", "text": "Timezone UTC", "readers": []},
    {"op": "query", "actor": "alice", "query": "timezone", "category": "recall", "expect": {"visible_ids": ["tz"]}}
  ]
}
```

See the [scenario reference](docs/scenarios.md) and bundled
[`governance-tour`](examples/scenarios/governance-tour.json).

## Scores

- Recall: required current facts returned.
- Stale rate: forbidden prior facts returned after correction.
- Deletion compliance: deleted facts absent.
- TTL compliance: expired facts absent.
- Privacy leakage rate / role isolation: restricted facts kept from unauthorized
  actors.
- Cost: adapter operations and records inspected per query.
- Composite: equal mean of recall, freshness, deletion, TTL, and isolation.

Only dimensions exercised by typed scenario assertions participate in the
composite. Unexercised metrics are emitted as `null` in JSON and `n/a` in human
reports; they are never silently scored as perfect. The scorecard also exposes
the exercised dimension names and a coverage ratio.

An assertion is exercised only when it binds to prior scenario state. A
correction check must connect current and stale text to the same corrected
record; deletion and TTL checks must target a live-deleted or expired record;
and privacy/role checks must target a query-relevant record the querying actor
cannot access. Invented IDs and irrelevant queries therefore cannot create a
perfect governance score.

Scores measure only declared synthetic checks. They do not establish legal or
regulatory compliance.

All dynamic values are encoded as literals in Markdown reports and escaped in
HTML reports, including adapter, scenario, memory, and evidence identifiers.

## Artifacts

Each run emits stable `memory-gauntlet/v1` results, a compact scorecard,
Markdown, self-contained HTML, and checksums.

```text
results.json
scorecard.json
report.md
report.html
checksums.sha256
```

## Develop

```bash
make test
make demo
```

## License

MIT

See the [roadmap](ROADMAP.md), [research provenance](docs/research.md), and [AI-assistance disclosure](AI_ASSISTED.md).
