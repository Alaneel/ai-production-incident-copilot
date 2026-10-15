# Prometheus 3.15.0 security derivative

Uses unchanged upstream server source at commit `5241a27fe3c6983549fccc32f6e65917408c63cd` and the exact published runtime parent. The recipe verifies the immutable source archive SHA256, builds with Go 1.27.2 and changes only the listed security module versions. Source license/NOTICE and compiler licenses remain in the runtime filesystem. Original entrypoint, user, directories and configuration are inherited.

Both current Mantine and legacy React web UIs are retained from the exact official `prometheus-web-ui-3.15.0.tar.gz` release asset. Its SHA256 matches GitHub's asset digest and the upstream `sha256sums.txt`. The small preparation helper follows the upstream deterministic gzip/embed scheme; it does not change UI source, dependencies or capabilities. Included JavaScript license text is retained in the runtime as well as the embedded asset set. No Node build or UI feature removal is used.

The shipped `go.mod`/`go.sum` are frozen from the actual same-source bootstrap, and final builds use readonly module metadata with SHA256 checks. Runtime notices include the exact source recipe/archive reference, compiler terms, module LICENSE/NOTICE/COPYRIGHT/AUTHORS texts, and versioned covered-source retrieval URLs plus ZIP/h1 hashes. These third-party terms remain separate from OpsPilot's first-party MIT license. Source archive hashes establish consistency; they are not independently verified publisher signatures.

Actual local build, binary/lock/version readback, image-scan and platform/UI/pipeline acceptance results are recorded in OpsPilot's release verification. A recipe alone is not evidence that any other platform or future build passed those gates.
