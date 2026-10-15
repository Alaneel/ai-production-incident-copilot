# Same upstream server source; only reviewed compiler/security module updates.
FROM golang:1.26.9-alpine@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS build
ENV GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOMAXPROCS=4 GOTELEMETRY=off
WORKDIR /src
RUN test "$(go env GOVERSION)" = go1.26.9 \
    && wget -O /tmp/upstream.tar.gz https://api.github.com/repos/grafana/loki/tarball/09e6ce2ff1bdc19763a10265b870c86f51c98655 \
    && echo '4302923af3479c2d129e71a7645ad5ebeff3c5ac8d1a104beb99abf1b8f0746f  /tmp/upstream.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/upstream.tar.gz --strip-components=1 -C /src \
    && rm /tmp/upstream.tar.gz
COPY go.mod go.sum ./
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go mod download && GOFLAGS=-mod=readonly go mod verify
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOWORK=off CGO_ENABLED=0 go build -p=2 -mod=readonly -buildvcs=false -trimpath -tags=netgo -ldflags="-s -w -X github.com/grafana/loki/v3/pkg/util/build.Version=3.7.8 -X github.com/grafana/loki/v3/pkg/util/build.Revision=09e6ce2ff1bdc19763a10265b870c86f51c98655 -X github.com/grafana/loki/v3/pkg/util/build.Branch=opspilot-security" -o /out/loki ./cmd/loki
RUN echo 'ede0678aab73dc43e810b1112ae2fd3960363394b1eec64f48fb4f96925f14e6  go.mod' | sha256sum -c - && echo '7df86b4b54b6a8f9532d9ad3035681e5bc8548f83fc1c14fe9ae5a23b021a881  go.sum' | sha256sum -c -
RUN cp go.mod /out/go.mod && cp go.sum /out/go.sum
COPY collect-module-notices.go /tmp/collect-module-notices.go
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go run /tmp/collect-module-notices.go /out/module-notices /out/loki
FROM grafana/loki:3.7.8@sha256:1107dd5274e0ada47e42472b7a7e71f3b2a2fe878878108f3e2f9e51528f0193
COPY --from=build /out/loki /usr/bin/loki
COPY LICENSE /usr/share/doc/opspilot-sidecar/LICENSE
COPY --from=build /usr/local/go/LICENSE /usr/share/doc/opspilot-sidecar/Go-LICENSE
COPY --from=build /usr/local/go/PATENTS /usr/share/doc/opspilot-sidecar/Go-PATENTS
COPY --from=build /out/go.mod /out/go.sum /usr/share/doc/opspilot-sidecar/
LABEL dev.opspilot.upstream.commit="09e6ce2ff1bdc19763a10265b870c86f51c98655" dev.opspilot.compiler="go1.26.9"
COPY --from=build /out/module-notices /usr/share/doc/opspilot-sidecar/module-notices
COPY Dockerfile source-lock.json /usr/share/doc/opspilot-sidecar/source/
