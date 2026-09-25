#!/usr/bin/env bash
# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
#
# Refresh the vendored app files from a Recaster checkout:
#   ci/release/sync_vendor.sh <path-to-recaster>
# Copies dfl_desktop/dfl/{lock_model,safe_extract}.py, re-reads the mirrored
# constants ("const:" lines) from the app sources, and rewrites VENDORED.txt
# with the checkout's HEAD and the new sha256s. Then runs check-vendor --app:
# if an app constant changed, update release_tools.py to match. Commit the result.
set -euo pipefail

app="$(cd "$1" && pwd)"
here="$(cd "$(dirname "$0")" && pwd)"
vendor="$here/vendor"
commit="$(git -C "$app" rev-parse HEAD)"
sources=(dfl_desktop/dfl/lock_model.py dfl_desktop/dfl/safe_extract.py)
while read -r rel; do sources+=("$rel"); done < <(sed -n 's/^const: [^ ]*  \([^:]*\):.*/\1/p' "$vendor/VENDORED.txt" | sort -u)
if [ -n "$(git -C "$app" status --porcelain -- "${sources[@]}")" ]; then
  echo "error: uncommitted changes to the vendored or mirrored files in $app" >&2
  exit 1
fi

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
consts="$(python3 "$here/release_tools.py" vendor-consts --app "$app")"
{
  sed -n '/^#/p' "$vendor/VENDORED.txt"
  echo "app_commit: $commit"
  for name in lock_model safe_extract; do
    cp "$app/dfl_desktop/dfl/$name.py" "$vendor/recaster_app/$name.py"
    echo "$(sha "$vendor/recaster_app/$name.py")  recaster_app/$name.py  dfl_desktop/dfl/$name.py"
  done
  printf '%s\n' "$consts"
} > "$vendor/VENDORED.txt.new"
mv "$vendor/VENDORED.txt.new" "$vendor/VENDORED.txt"
python3 "$here/release_tools.py" check-vendor --app "$app"
echo "vendored from $commit"
