# Memory Gauntlet report

| Adapter | Recall | Stale rate | Deletion | TTL | Leakage | Composite | Coverage | Records/query |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| governed&#45;sqlite | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 1.0000 | 1.0000 | 2.1429 |
| leaky&#45;append&#45;only | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.2000 | 1.0000 | 3.7143 |

## Query evidence

### governed&#45;sqlite

- **PASS** `governance&#45;tour` step 2 (recall): returned `timezone`
- **PASS** `governance&#45;tour` step 4 (correction): returned `timezone`
- **PASS** `governance&#45;tour` step 7 (deletion): returned `none`
- **PASS** `governance&#45;tour` step 10 (ttl): returned `none`
- **PASS** `governance&#45;tour` step 12 (recall): returned `budget`
- **PASS** `governance&#45;tour` step 13 (role): returned `none`
- **PASS** `governance&#45;tour` step 15 (privacy): returned `none`

### leaky&#45;append&#45;only

- **PASS** `governance&#45;tour` step 2 (recall): returned `timezone`
- **FAIL** `governance&#45;tour` step 4 (correction): returned `timezone, timezone`
- **FAIL** `governance&#45;tour` step 7 (deletion): returned `phoenix`
- **FAIL** `governance&#45;tour` step 10 (ttl): returned `otp`
- **PASS** `governance&#45;tour` step 12 (recall): returned `budget`
- **FAIL** `governance&#45;tour` step 13 (role): returned `budget`
- **FAIL** `governance&#45;tour` step 15 (privacy): returned `allergy`

## Limits

- The benchmark measures declared synthetic expectations&#44; not human memory quality&#46;
- Lexical retrieval is a deterministic baseline&#44; not a semantic&#45;memory claim&#46;
- Cost counts records inspected and operations&#59; it is not financial cost or latency&#46;
- Passing scenarios does not prove regulatory privacy compliance&#46;
