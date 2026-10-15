# OpsPilot release collector 0.162.0

The official Collector Builder 0.162.0 produces exactly the five configured components: OTLP receiver (HTTP and gRPC), batch and memory limiter processors, debug and OTLP HTTP exporters. Environment/file/YAML providers retain local config loading. The existing trace pipeline config is unchanged. The distribution includes this configured feature set; it is not the complete upstream Contrib catalog.

Go 1.26.9, x/net 0.60.0 and gRPC 1.83.2 close known active HTTP/2 transport findings. All component/provider versions match the official 0.162 manifest. The four official OCB-generated Go source files and actual `go.mod`/`go.sum` are frozen in this context; the final build performs no OCB generation or live dependency selection and uses readonly locks with SHA256 checks. Runtime covered-source notices retain exact versioned module ZIP URLs/hashes, collected license/NOTICE/COPYRIGHT/AUTHORS text and the frozen generated main-module source. Apache/MPL and all other upstream terms remain separate from first-party MIT.

Actual local binary/lock/version readback, image scans and full configured ingestion/query acceptance are recorded in the main release verification. This scoped distribution does not promise optional upstream Contrib components.
