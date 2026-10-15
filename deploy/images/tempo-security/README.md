# Tempo 2.10.8 security derivative

Uses unchanged upstream server source at commit `f0f3ed59197bfe9f54f3b0f8015ccca112f9e544` and the exact published runtime parent. The recipe verifies the immutable source archive SHA256, builds with Go 1.26.9 and changes only the listed security module versions. Source license/NOTICE and compiler licenses remain in the runtime filesystem. Original entrypoint, user, directories and configuration are inherited.

The shipped `go.mod`/`go.sum` are frozen from the actual same-source bootstrap, and final builds use readonly module metadata with SHA256 checks. Runtime notices include the exact source recipe/archive reference, compiler terms, module LICENSE/NOTICE/COPYRIGHT/AUTHORS texts, and versioned covered-source retrieval URLs plus ZIP/h1 hashes. These third-party terms remain separate from OpsPilot's first-party MIT license. Source archive hashes establish consistency; they are not independently verified publisher signatures.

Actual local build, binary/lock/version readback, image-scan and platform/UI/pipeline acceptance results are recorded in OpsPilot's release verification. A recipe alone is not evidence that any other platform or future build passed those gates.
