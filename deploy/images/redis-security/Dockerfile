FROM redis:7.4.11-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499
COPY packages.lock install-patches.sh /tmp/ops-redis-security/
RUN sh /tmp/ops-redis-security/install-patches.sh /tmp/ops-redis-security/packages.lock \
    && rm -rf /tmp/ops-redis-security
COPY licenses/ /usr/local/share/licenses/ops-redis-security/
LABEL org.opencontainers.image.title="OpsPilot local Redis 7.4.11 security derivative" \
      org.opencontainers.image.base.name="redis:7.4.11-alpine" \
      org.opencontainers.image.base.digest="sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499" \
      dev.opspilot.redis.openssl="3.3.7-r2" \
      dev.opspilot.redis.zlib="1.3.2-r1"
