#!/bin/sh
set -eu
lock=$1
test "$(cat /etc/alpine-release)" = 3.22.8
arch=$(apk --print-arch)
case "$arch" in aarch64|x86_64) ;; *) echo 'No reviewed package pins for this architecture' >&2; exit 1 ;; esac
# The parent already ships OpenSSL 3.5.9-r0; only zlib is replaced. Reject any other row.
test "$(awk -F '|' -v a="$arch" '$1 == a {n++} END {print n+0}' "$lock")" -eq 1
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
for name in zlib; do
  case "$name" in zlib) version=1.4-r0 ;; esac
  count=$(awk -F '|' -v a="$arch" -v p="$name" '$1 == a && $2 == p {n++} END {print n+0}' "$lock")
  test "$count" -eq 1
  row=$(awk -F '|' -v a="$arch" -v p="$name" '$1 == a && $2 == p {print}' "$lock")
  IFS='|' read -r recorded_arch recorded_name recorded_version url checksum <<EOF
$row
EOF
  test "$recorded_arch" = "$arch" && test "$recorded_name" = "$name" && test "$recorded_version" = "$version"
  test "$url" = "https://dl-cdn.alpinelinux.org/alpine/v3.22/main/$arch/$name-$version.apk"
  wget -O "$work/$name.apk" "$url"
  echo "$checksum  $work/$name.apk" | sha256sum -c -
done
# Signature checks use the inherited official Alpine trust store; no unsigned bypass.
apk add --no-cache --no-network --repositories-file /dev/null "$work"/*.apk
# apk2 and apk3 expose different installed-query flags; their database schema is stable.
# The inherited OpenSSL revision is checked too, so a different parent fails here.
for name in libcrypto3 libssl3 zlib; do
  case "$name" in libcrypto3|libssl3) version=3.5.9-r0 ;; zlib) version=1.4-r0 ;; esac
  awk -v name="$name" -v version="$version" '
    BEGIN { RS=""; FS="\n" }
    { package=""; actual=""; for (i=1; i<=NF; i++) {
        if (substr($i,1,2)=="P:") package=substr($i,3)
        if (substr($i,1,2)=="V:") actual=substr($i,3)
      }
      if (package==name) { count++; if (actual!=version) wrong=1 }
    }
    END { if (count!=1 || wrong) exit 1 }
  ' /lib/apk/db/installed
done
