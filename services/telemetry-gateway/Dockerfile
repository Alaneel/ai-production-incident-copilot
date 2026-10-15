FROM golang:1.26.9-alpine@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS build
ENV GOTOOLCHAIN=local
WORKDIR /src
COPY services/telemetry-gateway/go.mod .
COPY services/telemetry-gateway/cmd ./cmd
COPY services/telemetry-gateway/internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /gateway /gateway
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/opspilot/
EXPOSE 8081
ENTRYPOINT ["/gateway"]
