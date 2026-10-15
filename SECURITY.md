# Security policy

OpsPilot is a local engineering lab. The supported release scope is synthetic evaluation and isolated development/fault experiments. It is not a production access-control or incident-remediation service.

Keep development endpoints on loopback. Use the required authorization mode and dedicated non-default operator tokens before exposing any API; the default Compose credentials are public local fixtures. Do not use production model keys, Kubernetes contexts, data or observability endpoints for the demo or CI. The [authorization guide](docs/api-authorization.md) documents Reader/Operator boundaries and the separate approval/recovery checks. Production SSO and model faithfulness remain unfinished.

The [dependency record](docs/dependency-security.md) retains an unfixed build-time glob recursion advisory and its conditions. Local maintainer builds require reviewed source. Pull-request CI can process untrusted fork source; use ephemeral time-limited runners with read-only permissions and no provider/deployment credentials. This bounds the residual build denial-of-service risk rather than fixing the advisory. Never expose the dev server or a remote build-as-a-service endpoint to untrusted users.

For a suspected vulnerability, retain a minimal synthetic reproduction and the exact lock/compiler/image versions. Do not post keys, real incident data or private cluster details in a public issue. A private reporting channel has not been configured; the repository owner must configure one before requesting confidential external reports. No security response SLA is promised.
