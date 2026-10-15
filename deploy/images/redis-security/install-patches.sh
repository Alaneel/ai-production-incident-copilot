#!/bin/sh
set -eu
lock=$1
test "$(cat /etc/alpine-release)" = 3.21.8
arch=$(apk --print-arch)
case "$arch" in aarch64|x86_64) ;; *) echo 'No reviewed package pins for this architecture' >&2; exit 1 ;; esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
for name in libcrypto3 libssl3 zlib; do
  case "$name" in libcrypto3|libssl3) version=3.3.7-r2 ;; zlib) version=1.3.2-r1 ;; esac
  count=$(awk -F '|' -v a="$arch" -v p="$name" '$1 == a && $2 == p {n++} END {print n+0}' "$lock")
  test "$count" -eq 1
  row=$(awk -F '|' -v a="$arch" -v p="$name" '$1 == a && $2 == p {print}' "$lock")
  IFS='|' read -r recorded_arch recorded_name recorded_version url checksum <<EOF
$row
EOF
  test "$recorded_arch" = "$arch" && test "$recorded_name" = "$name" && test "$recorded_version" = "$version"
  test "$url" = "https://dl-cdn.alpinelinux.org/alpine/v3.21/main/$arch/$name-$version.apk"
  wget -O "$work/$name.apk" "$url"
  echo "$checksum  $work/$name.apk" | sha256sum -c -
done
# Signature checks use the inherited official Alpine trust store; no unsigned bypass.
apk add --no-cache --no-network --repositories-file /dev/null "$work"/*.apk
# apk2 and apk3 expose different installed-query flags; their database schema is stable.
for name in libcrypto3 libssl3 zlib; do
  case "$name" in libcrypto3|libssl3) version=3.3.7-r2 ;; zlib) version=1.3.2-r1 ;; esac
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
