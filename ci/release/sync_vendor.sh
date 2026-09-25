#!/usr/bin/env bash
# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
#
# Refresh the vendored app files from a Recaster checkout:
#   ci/release/sync_vendor.sh <path-to-recaster>
# Copies dfl_desktop/dfl/{lock_model,safe_extract}.py and rewrites VENDORED.txt
# with the checkout's HEAD and the new sha256s. Commit the result.
set -euo pipefail

app="$(cd "$1" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
vendor="$here/vendor"
commit="$(git -C "$app" rev-parse HEAD)"
if [ -n "$(git -C "$app" status --porcelain -- dfl_desktop/dfl/lock_model.py dfl_desktop/dfl/safe_extract.py)" ]; then
  echo "error: uncommitted changes to the vendored files in $app" >&2
  exit 1
fi

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
{
  sed -n '/^#/p' "$vendor/VENDORED.txt"
  echo "app_commit: $commit"
  for name in lock_model safe_extract; do
    cp "$app/dfl_desktop/dfl/$name.py" "$vendor/recaster_app/$name.py"
    echo "$(sha "$vendor/recaster_app/$name.py")  recaster_app/$name.py  dfl_desktop/dfl/$name.py"
  done
} > "$vendor/VENDORED.txt.new"
mv "$vendor/VENDORED.txt.new" "$vendor/VENDORED.txt"
python3 "$here/release_tools.py" check-vendor
echo "vendored from $commit"
