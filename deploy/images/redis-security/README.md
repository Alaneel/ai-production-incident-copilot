# Local Redis 7.4.12 sidecar security derivative

**Rebased on 2026-11-27 UTC onto the official `redis:7.4.12-alpine` image (Alpine 3.22.8); built and started locally on Linux arm64 and, under emulation, amd64; not scanned.** This OpsPilot sidecar keeps the official Redis 7.4.12 server and CLI, startup script, default user, data layout and service configuration unchanged. It replaces only `zlib` with the same Alpine 3.22 branch's `1.4-r0` and adds license files. OpenSSL is the parent's own `libcrypto3`/`libssl3 3.5.9-r0`.

## 2026-11-27 UTC: rebased on the official 7.4.12 image

The previous recipe sat on the unpatched `redis:7.4.11-alpine@sha256:858f009f…3499` (Alpine 3.21.8) and pinned three Alpine v3.21 packages by URL. One of those pins was withdrawn by the mirror on 2026-11-06 and broke the build, and Alpine 3.21 main support ended on 2026-11-01, so fixes published after that date never reach that branch.

### Candidates

Both tags were resolved at 2026-11-27 16:30 UTC, then pulled by per-platform digest and read on linux/arm64 and, under emulation, linux/amd64; the two architectures matched.

| Parent | Built | Alpine | Redis | `libcrypto3`/`libssl3` | `zlib` | Packages |
|---|---|---|---|---|---|---|
| `7.4.11-alpine@sha256:d9436322…ed3193` | 2026-10-21 | 3.21.9 | 7.4.11 | 3.3.7-r2 | 1.3.2-r1 | 19 |
| `7.4.12-alpine@sha256:7f7d30b7…287fbe` | 2026-11-09 | 3.22.8 | 7.4.12 | 3.5.9-r0 | 1.3.2-r1 | 20 |

`7.4.12-alpine3.21` (`sha256:c5d34a98…`, built 2026-11-05 on Alpine 3.21) also exists but was not pulled: it is on the unsupported branch and predates zlib 1.4-r0. No `7.4.13` tag and no Alpine 3.23 or 3.24 variant of 7.4 exists.

### Choice: `redis:7.4.12-alpine`, with zlib still pinned

Neither candidate carries zlib 1.4-r0. Both ship their Alpine base layer's `1.3.2-r1`, which lacks the CVE-2026-4738 fix, so no current official parent lets the package layer go. The recipe keeps one override: zlib `1.4-r0` from v3.22 main, pinned by URL and SHA-256 for aarch64 and x86_64 in `packages.lock`. That pin breaks the same way as before when Alpine publishes a newer zlib revision: the build stops at the download or checksum instead of changing silently. Drop the layer once an official Redis 7.4 image ships zlib 1.4-r0 or later.

`7.4.12-alpine` was chosen because:

- Alpine 3.22 main is supported until **2027-05-01**.
- Its OpenSSL `3.5.9-r0` carries the upstream fixes for all ten CVEs that the old `3.3.7-r2` pin backported. For each of them the [2026-09-29 OpenSSL advisory](https://openssl-library.org/news/secadv/20260929.txt) tells OpenSSL 3.5 users to upgrade to 3.5.9, so the OpenSSL pins can go.
- Redis 7.4.12 is a [security release](https://github.com/redis/redis/releases/tag/7.4.12). It fixes a heap overflow when a `PUBLISH`/`SPUBLISH` message larger than 4 GB is propagated over the cluster bus; OpsPilot runs Redis standalone.

The rebuilt `7.4.11-alpine` would have been the smaller diff, since its OpenSSL is already `3.3.7-r2` and the v3.21 zlib pins would stay. But it remains on Alpine 3.21, past support, and still needs the same zlib pin.

### Known gap: OpenSSL 3.5.9-r1 and -r2 are not applied

Alpine 3.22 published `3.5.9-r1` on 2026-11-23 for the [2026-11-10 OpenSSL advisory](https://openssl-library.org/news/secadv/20261110.txt) (CVE-2026-88822, CVE-2026-88823, CVE-2026-95691, CVE-2026-95692, CVE-2026-107496). It published `3.5.9-r2` on 2026-11-25 (CVE-2026-107493, CVE-2026-124709). OpenSSL rates all seven Low and fixes them upstream in 3.5.10. The parent image was built before both revisions, so this image ships `3.5.9-r0` without those fixes.

They are deliberately not pinned. Four more mirror URLs on a package that is revised often would bring back the 404 failure this rebase removes. The affected code is QUIC, AES-GCM-SIV, `CMS_encrypt()` and `PEM_X509_INFO_read_bio_ex()` with a caller-supplied stack. Redis does not use QUIC or CMS, and OpsPilot runs it without TLS. That is a reading of the advisories, not a function-level analysis. Recheck when the official image is rebuilt.

### What was checked for `opspilot-redis:7.4.12-security-20261127`

Image IDs: `sha256:bbe4ea9911a041264030a6028d02cfd832742213d172a87a209801a736d7459d` on linux/arm64 and `sha256:4b0d7fcb0a102b4bf2c9f5fcedc3f58160218677198857cbecd66da006ee657b` on linux/amd64 (emulated).

- **Build**: native linux/arm64, and linux/amd64 under emulation, both with `--no-cache`. Each fetched the pinned package, matched its SHA-256 and installed it through apk's Alpine signature check (`zlib 1.3.2-r1 -> 1.4-r0`). Each also passed the recipe's installed-version check, which asserts the inherited `libcrypto3`/`libssl3 3.5.9-r0` too.
- **Unchanged parts**, on both architectures: the installed package set differs from the parent only in zlib (20 packages). The server, CLI and entrypoint hashes match the parent's, and so do the entrypoint, command, user, working directory, volumes, ports and environment. The three license files in the image equal `licenses/`, which are byte-identical to the Redis 7.4.12, OpenSSL 3.5.9 and zlib 1.4 upstream texts.
- **Runtime**: on arm64, `redis-server --appendonly yes` (Compose's command) started with no network, a read-only root filesystem, all capabilities dropped, no-new-privileges and uid:gid 999:1000. `PING`, `SET`/`GET` and an AOF rewrite (status ok) succeeded, and after a container restart the value was read back from the AOF. The same checks passed on amd64 under QEMU emulation, which is not a native amd64 run.
- **Compose**: `docker compose up --build -d`, run under a separate project name, started all five services, and `http://localhost:8080/health` reported `"task_state":"redis"`. Compose starts Redis as root; the 7.4.12 entrypoint dropped it to uid 999 through `/bin/setpriv`. The project was then removed with `down -v`. Compose's own build of this context differs from the verified image only by its three `com.docker.compose` labels.

Not done: a vulnerability scan (no scanner is installed on the build machine), the 26-check persistence validation, Kubernetes or kind acceptance, a native amd64 run, and publication. `provenance.json` records the digests, hashes and each check.

## 2026-11-27 UTC: zlib pin replaced after upstream removal (previous recipe)

This section and the next one describe the previous recipe on `redis:7.4.11-alpine@sha256:858f009f…3499` and are kept as its record; "this recipe" and "the new image" in them mean that recipe and `opspilot-redis:7.4.11-security-20261127`.

The pinned `zlib-1.3.2-r1.apk` returned HTTP 404 from the Alpine v3.21 mirror for both architectures, so this recipe, and with it `docker compose up --build`, could no longer be built from a clean machine. Alpine v3.21 main now publishes `zlib 1.4-r0` (built 2026-11-06 UTC), whose [vendor recipe](https://github.com/alpinelinux/aports/blob/d3332621d44c2c4cf52e52217b16674db190212c/main/zlib/APKBUILD) lists CVE-2026-4738 as fixed in `1.4-r0` and keeps the earlier CVE-2026-85091 fix. `packages.lock` now pins that package for both architectures; the OpenSSL pins are unchanged and still served.

What was checked for the new image, `opspilot-redis:7.4.11-security-20261127` (ID `sha256:68f4580049ecced4ac675b7887452cb8ab97102050c7f2fa819e1a4fa09642a4`):

- **Build**: native Linux arm64, and Linux amd64 under emulation. Both passed the URL, SHA-256 and Alpine signature checks and ended with `zlib 1.4-r0`, `libcrypto3`/`libssl3 3.3.7-r2` and 19 installed packages.
- **Unchanged parts**: on arm64 the server, CLI and entrypoint hashes equal the inherited files recorded in `provenance.json`, and the retained zlib license text matches.
- **Runtime**: on arm64 the server started without a network, with a read-only root filesystem, all capabilities dropped and uid:gid 999:1000; `PING`, `SET`/`GET` and an AOF rewrite succeeded.

Not done for the new image: a vulnerability scan (no scanner was available), the 26-check persistence validation, Compose or Kubernetes acceptance, and starting the server on amd64. The sections below describe the earlier image and are kept as its record.

The official tags have also moved. On 2026-11-27 UTC `7.4.11-alpine` resolved to `sha256:d9436322…ed3193`, no longer this recipe's pinned parent, and `7.4.12-alpine` existed. Neither was pulled or validated here. Moving to a rebuilt or newer official parent is the next step for this recipe and would replace these package pins. Redis Reliability Lab's separate 8.10.2 official-image framework is unchanged.

## 2026-10-14 UTC: original Redis 7.4.11 derivative

The original sidecar scanner retained 21 package records: two high, seventeen medium and two low. Twenty records repeat ten OpenSSL advisories over two installed libraries; the remaining zlib record is scanner medium but **primary CNA high** (CVSS3.1 7.4 / CVSS4.0 8.3). The primary Alpine 3.21 security database identifies these exact fixed package revisions for all eleven unique CVEs. No finding is suppressed and no zero-finding claim is made before a fresh exact-image scan.

Both reviewed Linux architectures' package URLs and SHA-256 hashes are pinned in `packages.lock`; downloads use verified HTTPS and installation retains official Alpine signature verification, using no network dependency resolution and no untrusted-package flag. Other architectures fail. Package pins are from **v3.21**, matching the inherited Alpine 3.21.8; v3.24 packages are deliberately not mixed into this image. The current official `7.4.11-alpine` and `7.4.11-alpine3.21` aliases still resolve to the same unpatched parent digest. The Debian variant has a different distribution/package population and has not been selected or represented as a clean replacement.

Public registry metadata checks returned `MANIFEST_UNKNOWN` for `7.4.11-alpine3.22`, `7.4.11-alpine3.23` and `7.4.11-alpine3.24`; a newer supported Alpine image must be validated when an actual official tag becomes available.

The earlier local image was `opspilot-redis:7.4.11-security-20261013`, ID `sha256:cf899dc251f3311931b823b9b2d7093af5d5a202e76615c879e935570a599aaf`. Its unsuppressed Trivy 0.75.0 scan against the database updated **2026-10-14 07:11:52 UTC** reports **zero known package findings** across 19 installed OS packages. Only the three pinned revisions differ from the parent's package inventory; the other 16 packages are unchanged. This is an exact native-image snapshot, not a general host or application security claim.

All **26 real runtime checks passed**: the server/CLI/entrypoint hashes and seven inherited container configuration fields match the parent; 1,000 deterministic SET/GET values remain exact through AOF rewrite, process restart and container recreation on the same owned volume. The recreated server runs without root, with a read-only root filesystem and all capabilities dropped. Actual runtime license texts match the retained upstream bytes. All preexisting containers, networks and volumes remain intact; the owned successful fixture was cleaned up.

The first build failed because Alpine 3.21's apk 2 lacks an apk 3 query flag; its logs remain retained. The corrected build validates installed-database revisions and uses an empty repository file, verified to perform no index download or network dependency resolution. Official APK signatures and SHA256 pins remain enforced. Both architecture inputs are reviewed, but only Linux arm64 was built/scanned/executed; amd64 is **not claimed verified**. Current configured OpsPilot Compose acceptance used this exact context/image; Kubernetes acceptance remains blocked separately. No source or image has been pushed or published.

Alpine 3.21 main support ends **2026-11-01**. OpenSSL 3.3 itself is outside upstream support; these revisions are maintained Alpine backports. Recheck and validate a supported official Redis 7.4 image base before that deadline, and again immediately before any later publication. [Alpine support policy](https://alpinelinux.org/releases/) and [the vendor package listing](https://pkgs.alpinelinux.org/package/v3.21/main/aarch64/libcrypto3) document that scope; [OpenSSL's September advisory](https://openssl-library.org/news/secadv/20260929.txt) describes the vulnerable functions.

## Licenses and sources

License texts in `licenses/` retain Redis 7.4's RSALv2/SSPLv1 choices, OpenSSL's Apache-2.0 license and zlib's license. OpsPilot's MIT license applies to its own code and does not relicense the inherited Redis server. This recipe distributes build instructions and notices; it does not publish a server image. The corresponding upstream sources are [Redis 7.4.12](https://github.com/redis/redis/tree/7.4.12), [OpenSSL 3.5.9](https://github.com/openssl/openssl/tree/openssl-3.5.9) and zlib 1.4, whose source archive and checksum are named in the [vendor recipe](https://github.com/alpinelinux/aports/blob/a01d2885b6dc5443913a4a40f3689984ab66e7bb/main/zlib/APKBUILD).

The signed package metadata identifies the exact Alpine 3.22 vendor recipes: [OpenSSL 3.5.9-r0](https://github.com/alpinelinux/aports/blob/b6d0d5c5639c0034e9073c39e9ff139e28e71818/main/openssl/APKBUILD), inherited from the parent, and [zlib 1.4-r0](https://github.com/alpinelinux/aports/blob/a01d2885b6dc5443913a4a40f3689984ab66e7bb/main/zlib/APKBUILD), pinned here. The previous recipe used the Alpine 3.21 [OpenSSL r2 recipe](https://github.com/alpinelinux/aports/blob/a254386dfe34f70a05bdb8b2a3d7a5b20b6f076e/main/openssl/APKBUILD), the [zlib 1.4-r0 recipe](https://github.com/alpinelinux/aports/blob/d3332621d44c2c4cf52e52217b16674db190212c/main/zlib/APKBUILD) and, before that, the [zlib 1.3.2-r1 recipe](https://github.com/alpinelinux/aports/blob/c973d294927355eac1d2a6e97d86e41fbab8ffda/main/zlib/APKBUILD). Their commits and recipe hashes are recorded in `provenance.json`.
