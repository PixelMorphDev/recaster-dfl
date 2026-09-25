#!/usr/bin/env bash
# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
#
# Regenerate an env's locks from its specs (runs on macOS or Linux, for any target):
#   envs/lock.sh <platform>...        e.g. envs/lock.sh linux-x86_64-cuda12 macos-arm64-metal
#
#   environment.yml -> conda-lock.yml     conda-lock, one platform per file
#   requirements.in -> requirements.txt   uv pip compile --generate-hashes for the target,
#                                         constrained to the conda-lock's Python packages,
#                                         which are left out (conda installs them)
#
# Needs conda-lock (CONDA_LOCK, default `conda-lock`), a conda or mamba for its
# solver (CONDA_EXE, default `conda`) and uv (UV, default `uv`). Commit the
# results; the env_id (release_tools.py env-id) changes with them.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
CONDA_LOCK="${CONDA_LOCK:-conda-lock}"
CONDA_EXE="${CONDA_EXE:-conda}"
UV="${UV:-uv}"

for platform in "$@"; do
  case "$platform" in
    linux-x86_64-cuda12) subdir=linux-64; pyplat=x86_64-manylinux_2_35 ;;   # ubuntu-22.04, the build runner
    macos-arm64-metal)   subdir=osx-arm64; pyplat=aarch64-apple-darwin ;;
    *) echo "error: no lock recipe for $platform" >&2; exit 1 ;;
  esac
  dir="$here/$platform"
  work="$(mktemp -d)"
  "$CONDA_LOCK" lock --conda "$CONDA_EXE" --no-mamba --no-micromamba \
    -f "$dir/environment.yml" -p "$subdir" --lockfile "$dir/conda-lock.yml"
  # name==version for every conda package that is a Python package, plus the interpreter version
  "$UV" run --no-project -q --with "pyyaml==6.0.3" python - "$dir/conda-lock.yml" "$work" <<'PY'
import re, sys, yaml
from pathlib import Path
lock, work = yaml.safe_load(open(sys.argv[1])), Path(sys.argv[2])
pkgs = [p for p in lock["package"] if p["manager"] == "conda"]
python = next(p["version"] for p in pkgs if p["name"] == "python")
py = sorted({re.sub(r"[-_.]+", "-", p["name"]).lower(): p["version"] for p in pkgs
             if p["name"] != "python" and ("python" in p["dependencies"] or "python_abi" in p["dependencies"])}.items())
(work / "constraints.txt").write_text("".join(f"{n}=={v}\n" for n, v in py))
(work / "no-emit.txt").write_text("".join(f"{n}\n" for n, _ in py))
(work / "python.txt").write_text(python)
PY
  no_emit=(); while read -r name; do no_emit+=(--no-emit-package "$name"); done < "$work/no-emit.txt"
  (cd "$dir" && "$UV" pip compile requirements.in -q -o requirements.txt --generate-hashes \
    --python-version "$(cat "$work/python.txt")" --python-platform "$pyplat" --only-binary :all: \
    -c "$work/constraints.txt" "${no_emit[@]}" --no-strip-extras \
    --custom-compile-command "envs/lock.sh $platform")
  rm -rf "$work"
  echo "locked $platform: $(grep -c '^- name:' "$dir/conda-lock.yml") conda, $(grep -c '^[a-z0-9]' "$dir/requirements.txt") pip"
done
