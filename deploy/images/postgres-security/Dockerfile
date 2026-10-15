FROM golang:1.26.9-alpine@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS gosu-build
ARG TARGETARCH
WORKDIR /src/gosu
COPY gosu/go.mod gosu/go.sum ./
RUN test "$(go env GOVERSION)" = go1.26.9 && go mod download && go mod verify
COPY user-fixture/user_test.go /tmp/corrected-user_test.go
RUN set -eu; \
    cp -R "$(go env GOMODCACHE)/github.com/moby/sys/user@v0.4.1" /tmp/user-tests; \
    chmod -R u+w /tmp/user-tests; \
    echo '5544b3ff5453cfecd2b10eb014755e685f2e2ed41929be6c09bf55b49e920e1b  /tmp/user-tests/user_test.go' | sha256sum -c -; \
    cp /tmp/corrected-user_test.go /tmp/user-tests/user_test.go; \
    cd /tmp/user-tests; \
    go test -mod=readonly -count=1 -v .; \
    cd /src/gosu; \
    go mod verify
COPY gosu/*.go ./
RUN go test -mod=readonly -count=1 -v .
RUN arch="${TARGETARCH:-$(go env GOARCH)}"; \
    case "$arch" in arm64|amd64) ;; *) echo 'Only Linux arm64 and amd64 have reviewed package pins' >&2; exit 1 ;; esac \
    && CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -mod=readonly -buildvcs=false -trimpath -ldflags='-s -w' -o /out/gosu .

FROM postgres:17.11-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
RUN set -eu; \
    case "$(apk --print-arch)" in \
      aarch64) arch=aarch64; expected=1f77a542ac6634761b9fe38d8bdca19b154071dcfa25432e73f2bc8a0740a33f ;; \
      x86_64) arch=x86_64; expected=63aeea03c15a2f9018f81805cfc8aa926bdf5cd68921f22149c2fbb5d0ee9f47 ;; \
      *) echo 'Only Linux arm64 and amd64 have reviewed package pins' >&2; exit 1 ;; \
    esac; \
    wget -O /tmp/zlib.apk "https://dl-cdn.alpinelinux.org/alpine/v3.24/main/$arch/zlib-1.3.2-r1.apk"; \
    echo "$expected  /tmp/zlib.apk" | sha256sum -c -; \
    apk add --no-cache --no-network /tmp/zlib.apk; \
    rm /tmp/zlib.apk; \
    apk info --no-network --exists 'zlib=1.3.2-r1'
COPY --from=gosu-build /out/gosu /usr/local/bin/gosu
RUN chmod 0755 /usr/local/bin/gosu
COPY --from=gosu-build /usr/local/go/LICENSE /usr/local/share/licenses/tpp-postgres-helper/go-LICENSE
COPY --from=gosu-build /usr/local/go/PATENTS /usr/local/share/licenses/tpp-postgres-helper/go-PATENTS
COPY licenses/ /usr/local/share/licenses/tpp-postgres-helper/
LABEL org.opencontainers.image.title="TPP local PostgreSQL 17.11 security derivative" \
      org.opencontainers.image.base.name="postgres:17.11-alpine" \
      org.opencontainers.image.base.digest="sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24" \
      dev.tpp.gosu.upstream.commit="6456aaa0f3c854d199d0f037f068eb97515b7513" \
      dev.tpp.gosu.toolchain="go1.26.9" \
      dev.tpp.zlib.version="1.3.2-r1"
