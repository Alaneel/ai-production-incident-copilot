#!/usr/bin/env bash
# Linux amd64 hosted CI only; local test fixtures and rendering, no cluster access.
set -euo pipefail
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]]
tools="$RUNNER_TEMP/opspilot-ci-tools"
for command_name in curl tar make gcc sha256sum install sudo; do command -v "$command_name"; done
mkdir -p "$tools/bin" "$tools/src"
curl --fail --location --silent --show-error https://dl.k8s.io/release/v1.37.1/bin/linux/amd64/kubectl -o "$tools/bin/kubectl"
echo "65691ff77eb6fa44c908b77a1082c9f092c3b9733b5cefabec0d1104890e21a8  $tools/bin/kubectl" | sha256sum --check
chmod 0755 "$tools/bin/kubectl"
curl --fail --location --silent --show-error https://download.redis.io/releases/redis-7.4.11.tar.gz -o "$tools/src/redis.tar.gz"
echo "3c266ece0abd54ed3b1c912c6eb86b7508cf382cb690ee6649d3843f018f6357  $tools/src/redis.tar.gz" | sha256sum --check
tar -xzf "$tools/src/redis.tar.gz" -C "$tools/src"
make -C "$tools/src/redis-7.4.11" -j2 BUILD_TLS=no MALLOC=libc redis-server
install -m0755 "$tools/src/redis-7.4.11/src/redis-server" "$tools/bin/redis-server"
echo "$tools/bin" >> "$GITHUB_PATH"
sudo mkdir -p /private/tmp
sudo chmod 1777 /private/tmp
command -v docker
docker compose version
"$tools/bin/kubectl" version --client
"$tools/bin/redis-server" --version
