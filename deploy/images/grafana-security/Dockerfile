FROM grafana/grafana:12.4.13@sha256:41bbc1b5e04416f81891806076db2a2ff60a5d4da5f78d36a88b033a8c199185
USER root
COPY packages.lock install-patches.sh /tmp/grafana-security/
RUN sh /tmp/grafana-security/install-patches.sh /tmp/grafana-security/packages.lock \
    && rm -rf /tmp/grafana-security
COPY licenses/ /usr/local/share/licenses/opspilot-grafana-security/
USER 472
LABEL org.opencontainers.image.base.name="grafana/grafana:12.4.13" \
      org.opencontainers.image.base.digest="sha256:41bbc1b5e04416f81891806076db2a2ff60a5d4da5f78d36a88b033a8c199185" \
      dev.opspilot.grafana.zlib.version="1.3.2-r1"
