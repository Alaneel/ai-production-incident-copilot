# Same upstream server source; only reviewed compiler/security module updates.
FROM golang:1.26.9-alpine@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS build
ENV GOTOOLCHAIN=local GOWORK=off CGO_ENABLED=0 GOMAXPROCS=4 GOTELEMETRY=off
WORKDIR /src
RUN test "$(go env GOVERSION)" = go1.26.9 \
    && wget -O /tmp/upstream.tar.gz https://api.github.com/repos/grafana/tempo/tarball/f0f3ed59197bfe9f54f3b0f8015ccca112f9e544 \
    && echo '129c0cae5964ce9f02391157ab096a9b68c9bc3185257f6d82d622cbd298d9ba  /tmp/upstream.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/upstream.tar.gz --strip-components=1 -C /src \
    && rm /tmp/upstream.tar.gz
COPY go.mod go.sum ./
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go mod download && GOFLAGS=-mod=readonly go mod verify
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOWORK=off CGO_ENABLED=0 go build -p=2 -mod=readonly -buildvcs=false -trimpath -tags=netgo -ldflags="-s -w -X main.Version=2.10.8 -X main.Revision=f0f3ed59197bfe9f54f3b0f8015ccca112f9e544 -X main.Branch=opspilot-security" -o /out/tempo ./cmd/tempo
RUN echo '7da36745bda2cc5241ebc440baba9763de99cb4b63042387e4dd65aadcd3f58f  go.mod' | sha256sum -c - && echo 'bd3efdbd44e063f95bce966651a35b722242b444c1ffb0a2536dd377109f706a  go.sum' | sha256sum -c -
RUN cp go.mod /out/go.mod && cp go.sum /out/go.sum
COPY collect-module-notices.go /tmp/collect-module-notices.go
RUN --mount=type=cache,id=opspilot-public-sidecar-modules-20261014,target=/go/pkg/mod --mount=type=cache,id=opspilot-public-sidecar-build-20261014,target=/root/.cache/go-build GOFLAGS=-mod=readonly go run /tmp/collect-module-notices.go /out/module-notices /out/tempo
FROM grafana/tempo:2.10.8@sha256:f0561deb1c68ec44d6e6e7e4487f30106c4e5e768642077695b37958b105812a
COPY --from=build /out/tempo /tempo
COPY LICENSE /usr/share/doc/opspilot-sidecar/LICENSE
COPY --from=build /usr/local/go/LICENSE /usr/share/doc/opspilot-sidecar/Go-LICENSE
COPY --from=build /usr/local/go/PATENTS /usr/share/doc/opspilot-sidecar/Go-PATENTS
COPY --from=build /out/go.mod /out/go.sum /usr/share/doc/opspilot-sidecar/
LABEL dev.opspilot.upstream.commit="f0f3ed59197bfe9f54f3b0f8015ccca112f9e544" dev.opspilot.compiler="go1.26.9"
COPY --from=build /out/module-notices /usr/share/doc/opspilot-sidecar/module-notices
COPY Dockerfile source-lock.json /usr/share/doc/opspilot-sidecar/source/
