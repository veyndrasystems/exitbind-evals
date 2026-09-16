# Limitations and release boundary

This v1 is a reproducible harness, not a claim about Exitbind's universal
effectiveness. Fourteen cases are enough to exercise the mechanics but not to
represent all repositories, hosts, checks, or failure modes. The three pinned
repositories are public corpus references; only a reviewed, deterministic
adapter and fetched case inputs can turn a metadata case into an executed
repository case.

The result is bounded by the supplied binary, adapter, OS, toolchain, timeout,
and workspace integrity. A local pass is not CI parity, publication, or stable
release acceptance. Exact candidate commit, raw results, environment capture,
and independent review are required before publishing metrics.

Adapter execution uses a minimal process-environment allowlist but no OS
sandbox. Raw local run directories remain the source evidence. `public-export-v2`
artifacts expose deterministic UTF-8 sanitized stream copies, bind each copy to
the local raw stream's stored and actual SHA-256, and retain relative local-raw
references without changing the run. They are independently inspectable, but
do not replace local raw evidence when byte-for-byte reproduction is required.
The path redactor does not scan arbitrary secrets.
