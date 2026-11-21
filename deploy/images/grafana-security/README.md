# Grafana 12.4.13: bounded Alpine zlib patch

This build inherits the exact Grafana 12.4.13 image and replaces only Alpine
3.24 zlib `1.3.2-r0` with the signed vendor package `1.4-r0`. The former
`1.3.2-r1` x86_64 package is no longer available at its pinned URL. Both reviewed
Linux architectures have SHA-256-pinned inputs. The installer checks the parent
Alpine release and package baseline, architecture, URL, digest and vendor APK
signature; it does not download mutable indexes or bypass signature verification.
The official [Alpine recipe](https://github.com/alpinelinux/aports/blob/bdf501c870a92a05c097857401f69c48c4810384/main/zlib/APKBUILD)
lists the security upgrade, including CVE-2026-85091. Source and package details
are in `provenance.json`.

An isolated local Linux/arm64 build produced image ID
`sha256:2dae6fec3de4523079cdb00adce085e1edc4b16396669bc396f437564de8ada8`.
Package inventory comparison found only the zlib change; runtime config, Grafana
executable, entrypoint and all 11,551 public UI file hashes matched the pinned
parent. In a temporary container with no external networking, `/api/health`
returned database `ok` and version `12.4.13`. The zlib 1.4 source archive matched
the Alpine recipe SHA-512, and its license matched the retained license file.
The amd64 package hash was verified, but no derived amd64 image was built or run.
The previous 2026-10-14 image and benchmark results remain historical evidence;
they do not establish behavior of the new image. No image has been published or
deployed as part of this local verification.

Build the updated local image before using the Compose or Kind configuration:

```sh
docker build -t opspilot-grafana:12.4.13-security-20261121 .
```

Build from this directory. In a local Kind cluster, load the resulting image
into the intended cluster before applying manifests. The resulting local image ID
is evidence for one build, not a registry digest or a promise that a rebuild is
bit-for-bit identical. Revalidate deployment in its actual environment.

The original Grafana program, entrypoint, defaults, UI and upstream notices are
inherited; Grafana remains subject to its upstream AGPL terms. Existing upstream
Tempo and OpenPGP findings require their own assessment and are not suppressed.
