# Local PostgreSQL 17.11 security derivative

This build retains the pinned official PostgreSQL 17.11 server, entrypoint, UID/GID, configuration, and data layout. It changes the Alpine zlib package to the signed, SHA-256 checked `1.4-r0` security revision and replaces the privilege-switching helper with a build of unchanged upstream gosu 1.19 source using Go 1.26.9 and `github.com/moby/sys/user v0.4.1`. It is a local project derivative, not a new official PostgreSQL or gosu release.

The helper's `main.go`, `setup-user.go`, and `version.go` are byte-identical to upstream commit `6456aaa0f3c854d199d0f037f068eb97515b7513`. The Go module manifest changes only the compiler baseline and the user/group parser dependency. The original `golang.org/x/sys v0.1.0` dependency remains: its reported `CVE-2026-39824` affects a Windows-only function absent from this Linux helper. Raw scanner findings must remain visible alongside that platform classification.

The upstream helper did not contain the vulnerable HTTP/TLS functions behind the critical/high compiler-version scanner findings. An unsuppressed govulncheck 1.9.0 binary scan and independently matched primary-record symbols support that assessment. However, its actual `SetupUser → GetExecUserPath → ParsePasswdFilter/ParseGroupFilter` path used the affected parser from `CVE-2026-61801`. Upgrading that parser removes a real input-bound weakness, even though the project's trusted image account files do not provide the advisory's attacker-controlled input. The Go database did not include this moby advisory in the retained scan; the primary maintainer advisory supplies that missing coverage.

Only Linux arm64 and amd64 package pins are included. The Dockerfile rejects other architectures. It verifies the zlib download hash and Alpine package signature, uses a digest-pinned compiler, and verifies Go module checksums. An unavailable old Alpine package fails the build; the build never silently selects a later package.

Validation is pending. Before adopting this image, retain a final image ID/platform, fresh unsuppressed scan, gosu build information, helper UID/GID/group/HOME/argument behavior checks, bounded malformed-account parser checks, database startup/restart and compressed dump/restore checks, then rerun the project's complete PostgreSQL/Kafka integration suite and guarded demos. Historical benchmark charts remain historical. No registry push is needed for local validation.

Third-party license texts for gosu (Apache-2.0), moby/sys/user (Apache-2.0), and golang.org/x/sys (BSD-3-Clause plus patent grant) are in `licenses/` and copied into the image under `/usr/local/share/licenses/opspilot-postgres-security/`. Go's BSD-3-Clause license and patent grant are copied from the exact compiler stage. The inherited PostgreSQL and Alpine components retain the parent image's packaging and notices. zlib's [license and upstream source](https://zlib.net/zlib_license.html), the [Alpine package recipe](https://gitlab.alpinelinux.org/alpine/aports/-/tree/bdf501c870a92a05c097857401f69c48c4810384/main/zlib), [gosu source](https://github.com/tianon/gosu/tree/6456aaa0f3c854d199d0f037f068eb97515b7513), and [parser advisory](https://github.com/moby/sys/security/advisories/GHSA-mjcv-p78q-w5fw) identify the corresponding upstream material. `provenance.json` records immutable source and package hashes.

The current Compose and kind manifests refer to the local tag `opspilot-postgres:17.11-security-20261128`. Build it with `docker compose build postgres` before starting Compose. For kind, load that locally built tag into the target cluster before applying manifests; it is not a published registry image. Historical benchmark results refer to the earlier image and do not validate this revision.

## 2026-11-28 UTC: names corrected

The image title, the license directory and the `dev.*` labels still carried names from the recipe this one was adapted from: `TPP local PostgreSQL 17.11 security derivative`, `tpp-postgres-helper` and `dev.tpp.*`. They are now `OpsPilot local PostgreSQL 17.11 security derivative`, `opspilot-postgres-security` and `dev.opspilot.postgres.*`, and the tag moved from `…-20261121` to `…-20261128`. Nothing else in the recipe changed.

The linux/arm64 rebuild was compared file by file with the previous image, `sha256:4ab06ae6…`:

- The container configuration is identical apart from the labels.
- All 3,895 entries match, including the server, the entrypoint, the gosu binary and zlib. The exceptions are the license directory's name, the build timestamp in `/var/log/apk.log`, and the mode of the four `licenses/` files. Those files are now 0644. They were 0600 in the previous image because `COPY` keeps the build checkout's file modes.

The new image started the way Compose starts it: as root, then dropping to `postgres` through gosu. A table survived a restart, and a compressed `pg_dump`/`pg_restore` round trip returned the same rows. This covers the startup/restart and dump/restore items above, on arm64 only. The rest of the list is still pending.

The linux/amd64 build could not be checked on the arm64 build machine: the Go toolchain crashed twice under Docker Desktop's emulator, at different points, during `go mod download`. CI builds this recipe natively on x86_64. The new image was not scanned.
