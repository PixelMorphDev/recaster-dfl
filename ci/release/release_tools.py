# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Release tooling for the recaster-dfl runtime artifacts (.github/workflows/release.yml).

Subcommands (run with the CI's tool Python, not the packed env)::

    measure        sha256, size and unpacked_size of an archive -> JSON
    build-weights  deterministic facelib/*.npy tar, checked against WEIGHTS.sha256
    install-check  install the artifacts the way Recaster's runtime manager does
    make-lock      dfl_runtime.lock.json + the R2 publish manifest, validated
                   against the app's lock model (vendor/recaster_app)
    env-id         the env's id: <platform>-<sha12 of its conda-lock.yml, else environment.yml>
    check-vendor   the vendored app files match vendor/VENDORED.txt

``unpacked_size`` follows the app's definition (lock_model.Artifact): the sum
of the apparent sizes of the archive's regular files, every path counted,
hard-linked paths included (a hard link counts its target's size).
Directories and symlinks count 0.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional

HERE = Path(__file__).resolve().parent
VENDOR_DIR = HERE / "vendor"
sys.path.insert(0, str(VENDOR_DIR))

UPSTREAM = "MachineEditor/DeepFaceLab-MVE@6e366896e0119e26600c3fcebc914e6fc54fcfee"
DEFAULT_BASE_URL = "https://runtimes.recaster.studio"
# R2 key prefix for everything this pipeline publishes
KEY_PREFIX = "dfl"
# Fixed member metadata for the weights tar, so the same weights give the same bytes
WEIGHTS_MTIME = 1724371200   # 2024-08-23, the upstream commit the weights come from
CHUNK = 1 << 20

# Mirrors runtime_manager.EXTRACT_SIZE_FACTOR / EXTRACT_SIZE_SLACK and
# CONDA_UNPACK_TIMEOUT_S; locator._REQUIRED_SOURCE_DIRS / _WEIGHTS_FILE /
# _MIN_WEIGHTS_BYTES; runner._DROP_ENV_KEYS / _DROP_ENV_PREFIXES.
EXTRACT_SIZE_FACTOR = 1.1
EXTRACT_SIZE_SLACK = 1 << 20
CONDA_UNPACK_TIMEOUT_S = 900
REQUIRED_SOURCE_DIRS = ("mainscripts", "core", "models", "facelib", "DFLIMG")
WEIGHTS_FILE = PurePosixPath("facelib/S3FD.npy")
MIN_WEIGHTS_BYTES = 1024
DROP_ENV_KEYS = ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
                 "RECASTER_BRIDGE", "RECASTER_RUN_DIR", "RECASTER_RUN_ID", "RECASTER_PROTOCOL",
                 "RECASTER_HEARTBEAT_S", "RECASTER_BRIDGE_OWNER_PID")
DROP_ENV_PREFIXES = ("QT_", "QTWEBENGINE_", "NN_")


class ReleaseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Measuring archives
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_format(path: Path) -> str:
    name = path.name
    for fmt in ("tar.gz", "tar", "zip"):
        if name.endswith("." + fmt):
            return fmt
    raise ReleaseError(f"unknown archive format: {name}")


def _norm_member(name: str) -> str:
    return "/".join(p for p in PurePosixPath(name).parts if p not in ("", "."))


def unpacked_size(path: Path, fmt: Optional[str] = None) -> int:
    """Sum of regular-file sizes over every member path (hard links count their target)."""
    fmt = fmt or archive_format(path)
    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            return sum(info.file_size for info in zf.infolist() if not info.is_dir())
    sizes: Dict[str, int] = {}
    total = 0
    with tarfile.open(path, mode="r|gz" if fmt == "tar.gz" else "r|") as tar:
        for member in tar:
            name = _norm_member(member.name)
            if member.isreg():
                sizes[name] = member.size
                total += member.size
            elif member.islnk():
                target = _norm_member(member.linkname)
                if target not in sizes:
                    raise ReleaseError(f"hard link to a file not earlier in the archive: "
                                       f"{member.name} -> {member.linkname}")
                sizes[name] = sizes[target]
                total += sizes[target]
    return total


def measure(path: Path) -> dict:
    fmt = archive_format(path)
    return {
        "name": path.name,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "unpacked_size": unpacked_size(path, fmt),
        "format": fmt,
    }


# ---------------------------------------------------------------------------
# Weights artifact
# ---------------------------------------------------------------------------

def read_weights_manifest(manifest: Path) -> List[tuple]:
    entries = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, rel = line.partition("  ")
        rel = rel.strip()
        pure = PurePosixPath(rel)
        if len(digest) != 64 or not rel or pure.is_absolute() or ".." in pure.parts:
            raise ReleaseError(f"bad WEIGHTS.sha256 line: {line!r}")
        entries.append((digest.lower(), rel))
    if not entries:
        raise ReleaseError("WEIGHTS.sha256 lists no files")
    return sorted(entries, key=lambda e: e[1])


def weights_artifact_name(manifest: Path) -> str:
    """Content-addressed by the manifest: the same weights keep the same name across tags."""
    return f"recaster-dfl-weights-{hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]}.tar"


def build_weights(src_dir: Path, manifest: Path, out_dir: Path) -> Path:
    """Tar the manifest's files (verified) with fixed metadata. Returns the tar path."""
    entries = read_weights_manifest(manifest)
    for digest, rel in entries:
        actual = sha256_file(src_dir / rel)
        if actual != digest:
            raise ReleaseError(f"{rel}: sha256 {actual} does not match WEIGHTS.sha256 ({digest})")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / weights_artifact_name(manifest)
    with open(out, "wb") as raw, tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for _digest, rel in entries:
            path = src_dir / rel
            info = tarfile.TarInfo(rel)
            info.size = path.stat().st_size
            info.mtime = WEIGHTS_MTIME
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(path, "rb") as f:
                tar.addfile(info, f)
    return out


# ---------------------------------------------------------------------------
# Install check (mirrors dfl_desktop/dfl/runtime_manager.py install)
# ---------------------------------------------------------------------------

def child_env(base=None) -> Dict[str, str]:
    """runner.clean_child_env (not frozen) plus PYTHONNOUSERSITE, as run_conda_unpack uses."""
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items()
           if k not in DROP_ENV_KEYS and not k.startswith(DROP_ENV_PREFIXES)}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def env_python_path(env_dir: Path) -> Path:
    return env_dir / "python.exe" if os.name == "nt" else env_dir / "bin" / "python"


def conda_unpack_command(env_dir: Path) -> List[str]:
    if os.name == "nt":
        return [str(env_dir / "Scripts" / "conda-unpack.exe")]
    return [str(env_python_path(env_dir)), str(env_dir / "bin" / "conda-unpack")]


def _verify_download(archive: Path, artifact) -> None:
    size = archive.stat().st_size
    if size != artifact.size:
        raise ReleaseError(f"{archive.name}: {size} bytes, the lock says {artifact.size}")
    digest = sha256_file(archive)
    if digest != artifact.sha256:
        raise ReleaseError(f"{archive.name}: sha256 {digest}, the lock says {artifact.sha256}")


def _extract(archive: Path, artifact, dest: Path) -> int:
    from recaster_app.safe_extract import extract_archive
    if dest.exists():
        shutil.rmtree(dest)
    return extract_archive(archive, dest, artifact.format,
                           max_bytes=int(artifact.unpacked_size * EXTRACT_SIZE_FACTOR) + EXTRACT_SIZE_SLACK)


def _place_weights(cache: Path, dest: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(cache):
        rel = Path(dirpath).relative_to(cache)
        for name in filenames:
            src = Path(dirpath) / name
            target = dest / rel / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            try:
                os.link(src, target)
            except OSError:
                shutil.copy2(src, target)


def _source_problem(path: Path) -> Optional[str]:
    if not (path / "main.py").is_file():
        return "main.py is missing (archive members must sit at the archive root)"
    missing = [d for d in REQUIRED_SOURCE_DIRS if not (path / d).is_dir()]
    if missing:
        return "missing " + ", ".join(f"{d}/" for d in missing)
    weights = path / WEIGHTS_FILE
    if not weights.is_file() or weights.stat().st_size < MIN_WEIGHTS_BYTES:
        return f"the model weights are missing or truncated ({WEIGHTS_FILE})"
    return None


def _bridge_protocol(source_dir: Path) -> Optional[int]:
    try:
        text = (source_dir / "recaster_bridge" / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "protocol":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def install_check(lock_path: Path, platform: str, artifacts_dir: Path, root: Path) -> dict:
    """Install source, weights and ``platform``'s env from ``artifacts_dir`` under ``root``.

    Same order and rules as RuntimeManager._install_locked: verify size and
    sha256, safe-extract into ``<final>.partial`` with the extraction cap,
    require the env's bin/python, move the env into place *then* run its
    conda-unpack (prefixes are rewritten to the final path), hard-link the
    weights into the source tree and validate it. No re-sign on macOS: the
    app doesn't do one yet (runtime_manager TODO(E2)), so the smoke must pass
    without it.
    """
    from recaster_app.lock_model import load_lock
    lock = load_lock(lock_path)
    env = lock.env_for(platform)
    if env is None or not lock.is_published_for(platform):
        raise ReleaseError(f"the lock has no complete runtime for {platform}")
    source, weights = lock.artifacts.source, lock.artifacts.weights
    for artifact in (env, weights, source):
        _verify_download(artifacts_dir / artifact.name, artifact)

    env_final = root / "envs" / f"{env.env_id}-{env.sha256[:12]}"
    staging = env_final.with_name(env_final.name + ".partial")
    env_files = _extract(artifacts_dir / env.name, env, staging)
    if not env_python_path(staging).is_file():
        raise ReleaseError(f"the env archive has no {env_python_path(Path('.')).as_posix()}")
    env_final.parent.mkdir(parents=True, exist_ok=True)
    if env_final.exists():
        shutil.rmtree(env_final)
    os.replace(staging, env_final)
    if env.conda_unpack:
        script = Path(conda_unpack_command(env_final)[-1])
        if not script.is_file():
            raise ReleaseError(f"the env has no conda-unpack ({script.name})")
        proc = subprocess.run(conda_unpack_command(env_final), cwd=str(env_final), env=child_env(),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=CONDA_UNPACK_TIMEOUT_S)
        output = proc.stdout.decode("utf-8", "replace").strip()
        if output:
            print(output[-4000:])
        if proc.returncode != 0:
            raise ReleaseError(f"conda-unpack failed (exit code {proc.returncode})")

    weights_dir = root / "weights" / weights.sha256[:12]
    weights_files = _extract(artifacts_dir / weights.name, weights, weights_dir)

    tag_dir = root / "dfl" / lock.tag
    source_staging = tag_dir.with_name(tag_dir.name + ".partial")
    source_files = _extract(artifacts_dir / source.name, source, source_staging)
    _place_weights(weights_dir, source_staging)
    problem = _source_problem(source_staging)
    if problem:
        raise ReleaseError(f"the installed source is incomplete: {problem}")
    protocol = _bridge_protocol(source_staging)
    if protocol != lock.dfl.bridge_protocol:
        raise ReleaseError(f"recaster_bridge/VERSION protocol {protocol}, the lock says {lock.dfl.bridge_protocol}")
    if tag_dir.exists():
        shutil.rmtree(tag_dir)
    os.replace(source_staging, tag_dir)
    return {
        "source_dir": str(tag_dir),
        "env_dir": str(env_final),
        "python": str(env_python_path(env_final)),
        "file_counts": {"env": env_files, "weights": weights_files, "source": source_files},
    }


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

def env_spec(repo_root: Path, platform: str) -> Path:
    """The file an env is built from: the conda-lock once committed, else the environment.yml."""
    env_dir = repo_root / "envs" / platform
    lock = env_dir / "conda-lock.yml"
    return lock if lock.is_file() else env_dir / "environment.yml"


def env_id(repo_root: Path, platform: str) -> str:
    return f"{platform}-{sha256_file(env_spec(repo_root, platform))[:12]}"


def read_bridge_version(repo_root: Path) -> dict:
    out = {}
    for line in (repo_root / "recaster_bridge" / "VERSION").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip():
            out[key.strip()] = value.strip()
    return {"bridge_protocol": int(out["protocol"]), "bridge_version": out["bridge"]}


def object_key(role: str, tag: str, name: str) -> str:
    """R2 key: weights are content-addressed and shared by tags; the rest is per tag."""
    if role == "weights":
        return f"{KEY_PREFIX}/weights/{name}"
    return f"{KEY_PREFIX}/{tag}/{name}"


_CONTENT_TYPES = {"tar.gz": "application/gzip", "tar": "application/x-tar", "zip": "application/zip"}


def make_lock(*, tag: str, commit: str, repo: str, repo_root: Path, source: dict, weights: dict,
              envs: Iterable[dict], base_url: str = DEFAULT_BASE_URL,
              comment: Optional[str] = None) -> tuple:
    """(lock dict, publish manifest). Validated with the app's RuntimeLock; raises on any problem."""
    from recaster_app.lock_model import PLATFORMS, RuntimeLock

    base_url = base_url.rstrip("/")
    manifest = []

    def artifact(role: str, meta: dict, extra: Optional[dict] = None) -> dict:
        key = object_key(role, tag, meta["name"])
        manifest.append({"role": role, "key": key, "file": meta["name"], "sha256": meta["sha256"],
                         "size": meta["size"], "content_type": _CONTENT_TYPES[meta["format"]]})
        entry = {"name": meta["name"], "urls": [f"{base_url}/{key}"], "sha256": meta["sha256"],
                 "size": meta["size"], "unpacked_size": meta["unpacked_size"], "format": meta["format"]}
        entry.update(extra or {})
        return entry

    env_entries = {}
    for meta in sorted(envs, key=lambda m: m["platform"]):
        platform = meta["platform"]
        if platform in env_entries:
            raise ReleaseError(f"two env artifacts for {platform}")
        extra = {"env_id": meta["env_id"], "python": meta["python"], "pins": dict(sorted(meta["pins"].items())),
                 "conda_unpack": True}
        for key in ("min_driver", "min_os"):
            if meta.get(key):
                extra[key] = meta[key]
        env_entries[platform] = artifact("env", meta, extra)

    lock = {
        "schema": 1,
        "runtime": "recaster-dfl",
        "tag": tag,
        "placeholder": False,
        "comment": comment,
        "dfl": {"repo": repo, "commit": commit, "upstream": UPSTREAM, **read_bridge_version(repo_root)},
        "artifacts": {
            "source": artifact("source", source),
            "weights": artifact("weights", weights),
            "envs": {p: env_entries[p] for p in PLATFORMS if p in env_entries},
        },
        "signature": None,
        "signing_key_id": None,
    }
    parsed = RuntimeLock.model_validate_json(json.dumps(lock))
    for platform in env_entries:
        if not parsed.is_published_for(platform):
            raise ReleaseError(f"the lock is not complete for {platform}")
    keys = [m["key"] for m in manifest]
    if len(keys) != len(set(keys)):
        raise ReleaseError("two artifacts map to the same R2 key")
    return lock, manifest


def dump_json(data, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Vendored app files
# ---------------------------------------------------------------------------

def check_vendor() -> List[str]:
    problems = []
    for line in (VENDOR_DIR / "VENDORED.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("app_commit:"):
            continue
        digest, rel = line.split()[:2]
        actual = sha256_file(VENDOR_DIR / rel)
        if actual != digest:
            problems.append(f"{rel}: sha256 {actual}, VENDORED.txt says {digest}")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="release_tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("measure")
    p.add_argument("archive")
    p.add_argument("--extra", help="JSON object merged into the output (env metadata)")
    p.add_argument("--out", required=True)

    p = sub.add_parser("build-weights")
    p.add_argument("--src", required=True, help="directory containing facelib/*.npy")
    p.add_argument("--manifest", required=True)
    p.add_argument("--out-dir", required=True)

    p = sub.add_parser("install-check")
    p.add_argument("--lock", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--artifacts", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("make-lock")
    p.add_argument("--tag", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--repo-root", default=str(HERE.parent.parent))
    p.add_argument("--source", required=True)
    p.add_argument("--weights", required=True)
    p.add_argument("--env", action="append", default=[])
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--comment")
    p.add_argument("--out", required=True)
    p.add_argument("--manifest-out", required=True)

    p = sub.add_parser("env-id")
    p.add_argument("--platform", required=True)
    p.add_argument("--repo-root", default=str(HERE.parent.parent))

    sub.add_parser("check-vendor")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "measure":
            result = measure(Path(args.archive))
            if args.extra:
                result.update(json.loads(args.extra))
            dump_json(result, Path(args.out))
            print(json.dumps(result, indent=2))
        elif args.cmd == "build-weights":
            print(build_weights(Path(args.src), Path(args.manifest), Path(args.out_dir)))
        elif args.cmd == "install-check":
            result = install_check(Path(args.lock), args.platform, Path(args.artifacts), Path(args.root))
            dump_json(result, Path(args.out))
            print(json.dumps(result, indent=2))
        elif args.cmd == "make-lock":
            lock, manifest = make_lock(
                tag=args.tag, commit=args.commit, repo=args.repo, repo_root=Path(args.repo_root),
                source=_load_json(args.source), weights=_load_json(args.weights),
                envs=[_load_json(e) for e in args.env], base_url=args.base_url, comment=args.comment)
            dump_json(lock, Path(args.out))
            dump_json(manifest, Path(args.manifest_out))
            print(json.dumps(lock, indent=2))
        elif args.cmd == "env-id":
            print(env_id(Path(args.repo_root), args.platform))
        elif args.cmd == "check-vendor":
            problems = check_vendor()
            for problem in problems:
                print(problem, file=sys.stderr)
            if problems:
                return 1
            print("vendored app files match VENDORED.txt")
    except ReleaseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
