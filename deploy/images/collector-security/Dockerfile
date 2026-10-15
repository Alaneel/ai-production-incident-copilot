FROM golang:1.26.9-alpine@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS build
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOWORK=off GOMAXPROCS=4 GOTELEMETRY=off
WORKDIR /src/collector
COPY generated/*.go ./
COPY go.mod go.sum LICENSE ./
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build test "$(go env GOVERSION)" = go1.26.9 \
    && GOFLAGS=-mod=readonly go mod download && GOFLAGS=-mod=readonly go mod verify \
    && go build -p=2 -mod=readonly -buildvcs=false -trimpath -ldflags='-s -w' -o /out/otelcol-opspilot . \
    && echo '182198eaee3c0fa2594fa55d14cdb3a63ee3b61aeb35498116f94a67d66e41bb  go.mod' | sha256sum -c - && echo '5a930bad6574a39bdf87f094fb2e557fb5615b8df10e86f0833cd1f542bf5ffb  go.sum' | sha256sum -c -
COPY collect-module-notices.go /tmp/collect-module-notices.go
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go run /tmp/collect-module-notices.go /out/module-notices /out/otelcol-opspilot
FROM otel/opentelemetry-collector-contrib:0.162.0@sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6
COPY --from=build /out/otelcol-opspilot /otelcol-contrib
COPY LICENSE /usr/share/doc/opspilot-sidecar/LICENSE
COPY --from=build /usr/local/go/LICENSE /usr/share/doc/opspilot-sidecar/Go-LICENSE
COPY --from=build /usr/local/go/PATENTS /usr/share/doc/opspilot-sidecar/Go-PATENTS
COPY --from=build /src/collector/go.mod /src/collector/go.sum /usr/share/doc/opspilot-sidecar/
COPY builder-config.yaml generated-source-manifest.json /usr/share/doc/opspilot-sidecar/
LABEL dev.opspilot.distribution="configured-five-component-pipeline" dev.opspilot.compiler="go1.26.9"
COPY --from=build /out/module-notices /usr/share/doc/opspilot-sidecar/module-notices
COPY Dockerfile builder-config.yaml generated-source-manifest.json /usr/share/doc/opspilot-sidecar/source/
COPY generated/*.go /usr/share/doc/opspilot-sidecar/source/generated/
