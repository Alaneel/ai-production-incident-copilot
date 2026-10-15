# Source-only public preview — 2026-10-16

The repository owner explicitly authorized publication to
`Alaneel/ai-production-incident-copilot` and the corresponding OpsPilot Profile
link update. First-party release rights and MIT selection were confirmed during
the preceding review. Third-party source and distributions retain their own
notices and terms; no container, dependency bundle or internal review archive is
distributed by this source publication.

The source was exported into an independent directory from the reviewed,
parentless candidate `fdbb23e55d55d2bedf98ef9cc8571da62269e010`, tree
`f45899473687645f5a8d78638e82b4a6ab360d28`, containing 612 reviewed files. It was
not taken from the old Desktop HEAD or an upload of its working directory.
The original worktree, index, refs, remote configuration and unreviewed history
remain local and unchanged. The public Git history starts from a fresh snapshot;
the earlier candidate identity is provenance, not a public ancestor.

Publication-only changes clarify checkpoint/model/evaluation scope, retain two
previously omitted third-party source entries in the notice index, and replace
personal absolute paths in seven historical aggregate reports with repository-
relative references. No workflow, tool permission, evaluator or safety assertion
is changed. The [manifest](../PUBLICATION-MANIFEST.json) records each public file's
SHA256, size and Git mode, and the original/public hashes and JSON pointers of
each sanitized historical report. Those projections are explicitly not
byte-identical originals; their case/report hashes and outcomes are unchanged.
The manifest excludes only itself to avoid a recursive hash; its Git blob and
the published commit bind the manifest bytes.

## What publication does not certify

- Ten offline cases and 150/32 synthetic suites use deterministic reasoning and
  memory checkpoints; they do not measure model quality or PostgreSQL durability.
- Historical configured-model smoke uses synthetic telemetry and an in-memory
  graph, pauses at human approval and executes no production action. It is not a
  fresh model benchmark.
- The retained 27 real PostgreSQL checks and 39-check Compose experiment are
  separately recorded dependency/persistence evidence from release preparation.
- Two current Kubernetes attempts failed at rollout with storage exhaustion.
  They completed zero fault checks; current full-eight acceptance is not run.
- Hosted CI status is observable on GitHub after push; local test results are
  not hosted-CI success. The original dated validation remains historical.
- Production SSO, namespace-scoped telemetry isolation, semantic faithfulness
  and approved repair efficacy remain unverified. The disclosed security and
  scanner-coverage limitations remain in [the security record](dependency-security.md).

This publication does not add features, access production systems, call a paid
model, change shared Docker capacity, or redistribute the internal portfolio ZIP.

## Publication-copy recheck

The independent publication copy was checked with CPython 3.13.16, Node 22.23.3,
npm 10.9.9 and Go 1.26.9. Installs used the frozen SHA/integrity locks; all three
first-party editable packages resolved to this copy, not the original worktree.

| Check | Observed result |
|---|---|
| Backend/evaluation/fault/render suite | 1821 passed + 108 subtests; 27 PostgreSQL-only cases explicitly skipped; zero failures |
| Frontend | 213 passed, typecheck and neutral public build passed |
| Lint | Zero errors, three existing generated-declaration warnings |
| Actual Python/TypeScript stream contract | 1 passed, including fragmented-body and missing-complete behavior |
| Offline compiled-workflow demo | 10/10; zero model or execution calls |
| Evidence rendering | Rebuilt separately; retained SVG/JSON byte-identical, source/runtime/case-record checks passed |
| Go gateway | vet, race tests, module verification and trimmed native build passed |

No PostgreSQL server, Compose stack or Kubernetes cluster was started for this
publication recheck. The earlier full PostgreSQL/Compose results remain dated
release-preparation evidence, not newly rerun tests. Initial local sandbox/socket
and Compose-plugin discovery failures were retained; the complete affected suites
passed after correcting the test environment, without source/assertion changes.
Raw local logs and development tool directories are not part of the public tree.
