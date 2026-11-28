FROM redis:7.4.12-alpine@sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe
COPY packages.lock install-patches.sh /tmp/ops-redis-security/
RUN sh /tmp/ops-redis-security/install-patches.sh /tmp/ops-redis-security/packages.lock \
    && rm -rf /tmp/ops-redis-security
COPY licenses/ /usr/local/share/licenses/ops-redis-security/
LABEL org.opencontainers.image.title="OpsPilot local Redis 7.4.12 security derivative" \
      org.opencontainers.image.base.name="redis:7.4.12-alpine" \
      org.opencontainers.image.base.digest="sha256:7f7d30b7fdd09467bac58198a1f166dd69789d348d4fe859cccb20e0cf287fbe" \
      dev.opspilot.redis.openssl="3.5.9-r0" \
      dev.opspilot.redis.zlib="1.4-r0"
