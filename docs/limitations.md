# Limitations

- The baseline retrieval function is lexical token overlap, not semantic
  similarity.
- Scenarios are authored expectations and can omit important failure modes.
- Semantic validation proves that an assertion targets relevant synthetic
  setup; it does not prove the scenario corpus covers every real threat.
- Text containment is case-insensitive but not language-aware.
- Role lists model a simple ACL, not contextual authorization or delegation.
- Deletion checks application retrieval, not storage-media erasure.
- Cost is operation and record count, not latency, energy, or money.
- Passing does not prove privacy, data-protection, medical, or security
  compliance.
- The intentionally leaky adapter must never be used for production data.
- Markdown output encodes dynamic values as literals; HTML output escapes them.
