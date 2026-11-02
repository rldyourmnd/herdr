#!/bin/sh
# Build only. Close the UI and use install.py to deliver; keep its server running.
set -eu
desktop_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
desktop_cache="${SWIFTPM_CACHE_PATH:-$HOME/Library/Caches/org.swift.swiftpm}"
exec /usr/bin/xcrun swift build --package-path "$desktop_source" \
  --scratch-path "$desktop_cache/herdr-desktop/build" \
  --cache-path "$desktop_cache" -c release
