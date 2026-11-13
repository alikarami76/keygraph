#!/bin/sh
# Download the BIRD mini-dev set into $1 (default ../.bench-data/bird-minidev). Idempotent.
set -eu
DEST="${1:-../.bench-data/bird-minidev}"
URL="https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip"
mkdir -p "$DEST"
ZIP="$DEST/minidev.zip"
if [ ! -f "$ZIP" ]; then
  curl -fL --retry 3 -o "$ZIP.part" "$URL"
  mv "$ZIP.part" "$ZIP"
fi
WANT="cc48ba16838204e4e214512030cb572eeb5f7bcdd999bae4b9b6ff12ec13b92f"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$ZIP" | tee "$DEST/minidev.zip.sha256"
else
  shasum -a 256 "$ZIP" | tee "$DEST/minidev.zip.sha256"
fi
GOT=$(cut -d' ' -f1 "$DEST/minidev.zip.sha256")
if [ "$GOT" != "$WANT" ]; then
  echo "SHA-256 mismatch: expected $WANT, got $GOT" >&2
  exit 1
fi
unzip -n -q "$ZIP" -d "$DEST"
echo "unpacked into $DEST"
echo "use --db-root $DEST/minidev/MINIDEV/dev_databases"
