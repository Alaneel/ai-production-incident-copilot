#!/bin/sh
# Reproduce upstream scripts/compress_assets.sh's deterministic gzip/embed step.
# The exact official release archive supplies both current and legacy UIs.
set -eu
cd /src
test -s web/ui/static/mantine-ui/index.html
test -s web/ui/static/react-app/index.html
cp web/ui/embed.go.tmpl web/ui/embed.go
find web/ui/static -type f ! -name '*.gz' -exec sh -c '
  for file do
    gzip -n -c "$file" > "${file}.gz"
  done
' sh {} +
printf '\n//go:embed' >> web/ui/embed.go
find web/ui/static -type f -name '*.gz' | LC_ALL=C sort | while IFS= read -r file; do
  printf ' %s' "${file#web/ui/}"
done >> web/ui/embed.go
printf '\nvar EmbedFS embed.FS\n' >> web/ui/embed.go
