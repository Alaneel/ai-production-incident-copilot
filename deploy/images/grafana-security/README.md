# Grafana 12.4.13: bounded Alpine zlib patch

This build inherits the exact Grafana 12.4.13 image and replaces only Alpine
3.24's `zlib` package, from `1.3.2-r0` to the signed vendor `1.3.2-r1` package.
Both reviewed Linux architectures have separate SHA256-pinned package inputs.
The installer refuses a different Alpine release, package baseline, architecture,
URL or digest. It uses the parent's official Alpine signing keys and downloads
no mutable package indexes. No signature bypass or vulnerability suppression is
used.

The original Grafana program, native helpers, UI, `/run.sh`, default configuration
and upstream license files are inherited. Root is used only while installing the
package; the final image restores the parent's exact user `472`. Entrypoint,
working directory, environment and exposed port are inherited unchanged.
The added license directory retains the exact upstream AGPLv3 text and zlib
notice. Grafana is not relicensed under the first-party project's MIT license.
The exact upstream tag has no root `NOTICE` file (HTTP404, recorded in source
receipts); all existing notices in the parent remain present.

The primary CNA rates [CVE-2026-85091](https://www.cve.org/CVERecord?id=CVE-2026-85091)
HIGH (CVSS3.1 7.4 / CVSS4.0 8.3), although the retained Trivy package finding is
MEDIUM. The Alpine recipe at the recorded package commit explicitly lists the
fix in `1.3.2-r1`. The underlying Grafana/Tempo package findings remain visible
and need their separate source, actual ELF and deployed-configuration assessment;
this patch does not remove or suppress them.

## Current status

The derived Linux arm64 image was built and verified locally: `opspilot-grafana:12.4.13-security-20261014`, ID `sha256:4d65f19d24dee13d160effc33d991e442f7d5a746c775fdea3ec778d8321a0ab`. Only zlib's revision changes; all original startup/config fields, server/CLI/entrypoint/defaults and 11,551 UI file hashes match the exact parent. Applicable upstream/zlib notices were read back. Final Compose acceptance passed 39 checks, including Grafana health/version and actual analytics/update/plugin-preinstall flags.

The fresh exact-ID 14 October scan retains two raw High Tempo version matches and one Unknown OpenPGP record; zlib is fixed. Immutable embedded source and actual ELF functions establish the limited NOT AFFECTED dispositions explained in the main security record, without suppressing findings. Both parent architectures/package inputs are verified; no derived `linux/amd64` execution is claimed. No image was published.

An authorized local build can use this directory as its complete context:

```sh
docker build -t opspilot-grafana:12.4.13-security-20261014 .
```

For any future rebuild, retain the actual image ID and context hashes, verify exactly
one package revision changes, compare the Grafana/server/CLI/entrypoint/defaults
and UI asset bytes with the pinned parent, and check inherited runtime config
including user `472`. Start Grafana only in the owned local acceptance stack,
check health, rendered UI and provisioned data-source/dashboard behavior, and
confirm analytics/update/plugin-preinstall flags from the running instance.
Scan that exact image ID against the recorded local advisory snapshot; preserve
raw findings and the distinct scope of any NOT AFFECTED classifications. Confirm
cleanup does not touch pre-existing containers, networks or volumes. These conditions must be rechecked for any different build or configuration.

The Dockerfile uses ordinary `COPY` and a fixed parent image so it also works
with the classic Docker build API. Public upstream software and fixed package
URLs are the only build inputs; no credentials or model calls are required.
