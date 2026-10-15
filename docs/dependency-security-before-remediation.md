# Historical dependency snapshot — before 13 October 2026 release remediation

This read-only audit used exact lock/installed versions and the [OSV API](https://google.github.io/osv.dev/api/), plus native `npm audit` against the official registry. All queries and advisory-detail retrievals completed. No dependency upgrade or automatic fix ran.

| Scope | Actual inventory | Result |
|---|---|---|
| Node | 708 lock instances / 699 distinct package-version pairs, including nested/dev/optional entries | OSV: 81 active advisory groups across 23 package-version pairs. npm: 32 vulnerable-package records, including inherited dependency findings; 1 critical, 24 high, 6 moderate, 1 low. These are different counting units. |
| Python | 63 installed third-party distributions, including all 49 pins in `requirements-demo.lock` | 12 alias-merged advisory groups across five package versions; five groups affect three runtime distributions, seven affect pip/pytest tooling. |
| Gateway Go | No third-party modules; tested host Go 1.25.6 stdlib | 43 version-level advisories, unscored by the Go database; call reachability not analyzed. |

The Python service and fault-lab manifests still contain version ranges. The new demo lock pins observed service-runtime versions, without artifact hashes; it improves reproducibility but does not make those dependencies security-clean. A fresh/container/platform-specific installation is outside this snapshot.

## Priority remediation review

- **Next 16.2.6:** 17 direct OSV matches, including Critical feature/platform-specific cases. The latest retrieved 16.x fix boundary is **16.3.8**; native npm suggests **16.4.0** as its candidate. Review compatibility with the actual vinext/worker build instead of treating either suggestion as verified. [Node `next/og` advisory](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j), [Windows server advisory](https://github.com/vercel/next.js/security/advisories/GHSA-p293-qw3h-jr36). The application uses vinext rather than a conventional Next server; limited source search found no `next/og`/`ImageResponse` or `next/image` use in app/services. Those particular paths appear absent, but bundle/runtime reachability and other Next advisories were not proven safe.
- **Python runtime:** checkpoint-postgres **3.1.0 → 3.1.1** for [namespace scoping](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-47pj-3jcm-6whg); SDK **0.4.2 → 0.4.4** for [custom-auth action handling](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fvww-7h3r-vfhp); urllib3 **2.7.0 → 2.8.0** for three response/proxy advisories, including [proxy TLS configuration](https://github.com/urllib3/urllib3/security/advisories/GHSA-8988-9cw3-xx77). These are fixed boundaries from current records, not applied upgrades. Postgres checkpoint use does not itself prove use of the vulnerable namespace-store feature, and transitive SDK installation does not prove custom-auth decorators run.
- **Go:** the tested gateway host compiler is 1.25.6. Several HTTP/TLS/parser cases have later fixes, including [HTTP/2 server memory exhaustion](https://pkg.go.dev/vuln/GO-2026-6603) and [header-size limits](https://pkg.go.dev/vuln/GO-2026-6608). Rebuild and retest with a patched pinned toolchain, such as the separately queried 1.26.9. The Docker builder uses the floating **`golang:1.23-alpine`** tag: its actual patch/digest was not resolved, so the host audit cannot attest to a deployed binary.
- **Build/dev chain:** matches include vinext, Vite, Wrangler/Miniflare, RSC, undici and parser/glob/compression libraries. `devDependencies` execute in builds and may contribute bundled runtime code. npm's suggested fixes include major changes and even older top-level versions; `npm audit fix --force` was not run and is not a safe remediation plan without review. pip 24.2 and pytest 8.4.2 also match tool-only advisories; they are not included in the service-runtime demo lock.

## Release conditions and limits

Plan reviewed dependency updates, regenerate the Node/Python locks, rerun the package audits and all Node/Python/Go checks, inspect the deployment bundle, and run the real dependency/container fault tests before describing a release as security-reviewed. Those container checks remain blocked by the unavailable local Docker runtime. Preserve the existing feature boundary analysis when deciding priority; a database severity label is not a proven exploit in this application.

CPython/Node/JDK binaries, Redis/PostgreSQL/Kafka/Docker/base OS images, worker platform, native dependencies, provider services and application authorization logic were not covered by this package audit. Keep demonstration endpoints local and preserve the documented [authorization boundaries](api-authorization.md).

This is the retained pre-remediation audit. Use [current release security](dependency-security.md) for the current locks and dispositions.
