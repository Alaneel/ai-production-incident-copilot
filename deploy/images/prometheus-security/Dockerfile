# Same upstream server source; only reviewed compiler/security module updates.
FROM golang:1.27.2-alpine@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d AS build
ENV GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOMAXPROCS=4 GOTELEMETRY=off
WORKDIR /src
RUN test "$(go env GOVERSION)" = go1.27.2 \
    && wget -O /tmp/upstream.tar.gz https://api.github.com/repos/prometheus/prometheus/tarball/5241a27fe3c6983549fccc32f6e65917408c63cd \
    && echo '86915195a800d177ed905100e1e9da642ca625dcecbd7d9806f34ba94485ab3b  /tmp/upstream.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/upstream.tar.gz --strip-components=1 -C /src \
    && rm /tmp/upstream.tar.gz
RUN wget -O /tmp/upstream-ui.tar.gz https://github.com/prometheus/prometheus/releases/download/v3.15.0/prometheus-web-ui-3.15.0.tar.gz \
    && echo 'fa8c918ed05e3f89e232e9b69eccd6ed074602cd9e8a2cab7a92bec9651974ae  /tmp/upstream-ui.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/upstream-ui.tar.gz -C /src/web/ui \
    && rm /tmp/upstream-ui.tar.gz
COPY prepare-ui.sh /tmp/prepare-ui.sh
RUN sh /tmp/prepare-ui.sh
COPY go.mod go.sum ./
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go mod download && GOFLAGS=-mod=readonly go mod verify
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOWORK=off CGO_ENABLED=0 go build -p=2 -mod=readonly -buildvcs=false -trimpath -tags=netgo,builtinassets -ldflags="-s -w -X github.com/prometheus/common/version.Version=3.15.0 -X github.com/prometheus/common/version.Revision=5241a27fe3c6983549fccc32f6e65917408c63cd" -o /out/prometheus ./cmd/prometheus \
    && GOWORK=off CGO_ENABLED=0 go build -p=2 -mod=readonly -buildvcs=false -trimpath -ldflags="-s -w -X github.com/prometheus/common/version.Version=3.15.0 -X github.com/prometheus/common/version.Revision=5241a27fe3c6983549fccc32f6e65917408c63cd" -o /out/promtool ./cmd/promtool
RUN echo 'd85af091d5bf9ba19f98e4d92e24b368d3e5537af086521ef10c7dd669695fd7  go.mod' | sha256sum -c - && echo 'eaeb5e4f16a9edcab1aeeb2b1a90a0f85decbd0065b3bf63e03729d324f7fc1f  go.sum' | sha256sum -c -
RUN cp go.mod /out/go.mod && cp go.sum /out/go.sum
COPY collect-module-notices.go /tmp/collect-module-notices.go
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go run /tmp/collect-module-notices.go /out/module-notices /out/prometheus /out/promtool
FROM prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e
COPY --from=build /out/prometheus /bin/prometheus
COPY --from=build /out/promtool /bin/promtool
COPY LICENSE /usr/share/doc/opspilot-sidecar/LICENSE
COPY NOTICE /usr/share/doc/opspilot-sidecar/NOTICE
COPY --from=build /src/web/ui/static/react-app/static/js/main.5b2d3908.js.LICENSE.txt /usr/share/doc/opspilot-sidecar/UI-LICENSE.txt
COPY --from=build /src/web/ui/static/mantine-ui/assets/third-party-licenses.txt /usr/share/doc/opspilot-sidecar/UI-Mantine-LICENSES.txt
COPY --from=build /usr/local/go/LICENSE /usr/share/doc/opspilot-sidecar/Go-LICENSE
COPY --from=build /usr/local/go/PATENTS /usr/share/doc/opspilot-sidecar/Go-PATENTS
COPY --from=build /out/go.mod /out/go.sum /usr/share/doc/opspilot-sidecar/
LABEL dev.opspilot.upstream.commit="5241a27fe3c6983549fccc32f6e65917408c63cd" dev.opspilot.compiler="go1.27.2"
COPY --from=build /out/module-notices /usr/share/doc/opspilot-sidecar/module-notices
COPY Dockerfile source-lock.json /usr/share/doc/opspilot-sidecar/source/
