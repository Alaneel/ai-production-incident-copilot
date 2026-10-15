#!/bin/sh
set -eu
lock=$1
test "$(cat /etc/alpine-release)" = 3.24.2
arch=$(apk --print-arch)
case "$arch" in aarch64|x86_64) ;; *) echo 'No reviewed zlib pin for this architecture' >&2; exit 1 ;; esac
package_version() {
  awk -v version="$1" '
    BEGIN { RS=""; FS="\n" }
    { package=""; actual=""; for (i=1; i<=NF; i++) {
        if (substr($i,1,2)=="P:") package=substr($i,3)
        if (substr($i,1,2)=="V:") actual=substr($i,3)
      }
      if (package=="zlib") { count++; if (actual!=version) wrong=1 }
    }
    END { if (count!=1 || wrong) exit 1 }
  ' /lib/apk/db/installed
}
# Refuse an unexpected upstream inventory rather than silently changing its scope.
package_version 1.3.2-r0
count=$(awk -F '|' -v a="$arch" '$1 == a && $2 == "zlib" {n++} END {print n+0}' "$lock")
test "$count" -eq 1
row=$(awk -F '|' -v a="$arch" '$1 == a && $2 == "zlib" {print}' "$lock")
IFS='|' read -r recorded_arch name version url checksum <<EOF
$row
EOF
test "$recorded_arch" = "$arch" && test "$name" = zlib && test "$version" = 1.3.2-r1
test "$url" = "https://dl-cdn.alpinelinux.org/alpine/v3.24/main/$arch/zlib-1.3.2-r1.apk"
test "${#checksum}" -eq 64
case "$checksum" in *[!0-9a-f]*) echo 'Invalid package digest' >&2; exit 1 ;; esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
wget -O "$work/zlib.apk" "$url"
echo "$checksum  $work/zlib.apk" | sha256sum -c -
# Inherited official Alpine trust keys verify signatures. No indexes or unsigned bypass.
apk add --no-cache --no-network --repositories-file /dev/null "$work/zlib.apk"
package_version 1.3.2-r1
