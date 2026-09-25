#!/usr/bin/env bash
# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
#
# Build the weights-free source artifact: build_source.sh <tag> <repo-url> <out-dir>
#
# git archive of HEAD with members at the archive root (Recaster extracts it
# straight into runtimes/dfl/<tag>/ and expects main.py there) plus a
# SOURCE.txt naming the repo, commit and tag. Reproducible: the same commit
# and git version give the same tar, and gzip -n stores no name or time.
set -euo pipefail

tag="$1"; repo="$2"; out_dir="$3"
commit="$(git rev-parse HEAD)"
name="recaster-dfl-src-${tag}.tar.gz"
mkdir -p "$out_dir"

source_txt="repo: ${repo}
commit: ${commit}
tag: ${tag}
upstream: MachineEditor/DeepFaceLab-MVE@6e366896e0119e26600c3fcebc914e6fc54fcfee
"

git -c core.autocrlf=false archive --format=tar \
  --add-virtual-file="SOURCE.txt:${source_txt}" HEAD \
  | gzip -n -9 > "${out_dir}/${name}"

if tar -tzf "${out_dir}/${name}" | grep -E '\.npy$'; then
  echo "error: weights leaked into the source artifact" >&2
  exit 1
fi
for required in main.py SOURCE.txt LICENSE NOTICE.md recaster_bridge/VERSION; do
  tar -tzf "${out_dir}/${name}" "$required" > /dev/null
done
echo "${out_dir}/${name}"
