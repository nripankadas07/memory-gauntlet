# Limitations

- The baseline retrieval function is lexical token overlap, not semantic
  similarity.
- Scenarios are authored expectations and can omit important failure modes.
- Semantic validation proves that an assertion targets relevant synthetic
  setup; it does not prove the scenario corpus covers every real threat.
- Text containment is case-insensitive but not language-aware.
- Hidden-target reachability models the bundled lexical ungoverned adapter; a
  materially different third-party retrieval/ranking function can expose other
  targets and needs matching scenario validation outside this benchmark.
- Role lists model a simple ACL, not contextual authorization or delegation.
- Deletion checks application retrieval, not storage-media erasure.
- Cost is operation and record count, not latency, energy, or money.
- Passing does not prove privacy, data-protection, medical, or security
  compliance.
- The intentionally leaky adapter must never be used for production data.
- Markdown output encodes dynamic values as literals; HTML output escapes them.
- Output path components must not be symlinks, and named artifacts must be
  regular files when they already exist. Bundle publication stages all files
  and restores the original set after an in-process publication failure, but a
  concurrent reader can briefly observe sequential atomic renames.
- The directory lock is advisory and coordinates only writers using this
  implementation. Abrupt process/host failure can leave private temporary or
  backup files and a partially published set for manual recovery.
- Secure output fails closed on platforms without descriptor-relative file,
  directory, stat, rename, unlink, and advisory-lock operations.
