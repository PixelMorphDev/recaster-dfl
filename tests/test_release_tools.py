# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Tests for ci/release/release_tools.py (the release pipeline's archive, lock and install tooling).

The lock and install tests need pydantic (the vendored app lock model); they
are skipped without it and run in release.yml's ``tools`` job.
"""

import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ci" / "release"))

import release_tools as rt  # noqa: E402

try:
    import pydantic  # noqa: F401
    HAVE_PYDANTIC = True
except ImportError:
    HAVE_PYDANTIC = False


def _add(tar, name, data=b"", **kw):
    info = tarfile.TarInfo(name)
    for key, value in kw.items():
        setattr(info, key, value)
    if info.type == tarfile.REGTYPE:
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    else:
        tar.addfile(info)


def _meta(path):
    return rt.measure(Path(path))


class UnpackedSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _tar(self, name, mode):
        path = self.tmp / name
        with tarfile.open(path, mode) as tar:
            _add(tar, "d", type=tarfile.DIRTYPE, mode=0o755)
            _add(tar, "d/a.bin", b"x" * 10, mode=0o644)
            _add(tar, "d/b.bin", type=tarfile.LNKTYPE, linkname="d/a.bin")
            _add(tar, "d/c.bin", type=tarfile.SYMTYPE, linkname="a.bin")
            _add(tar, "./e.txt", b"hello", mode=0o644)
        return path

    def test_hard_links_count_their_target_symlinks_and_dirs_count_zero(self):
        for name, mode in (("t.tar", "w"), ("t.tar.gz", "w:gz")):
            path = self._tar(name, mode)
            self.assertEqual(rt.unpacked_size(path), 10 + 10 + 5, name)

    def test_matches_what_the_app_extractor_writes(self):
        from recaster_app.safe_extract import extract_archive
        path = self._tar("t.tar.gz", "w:gz")
        dest = self.tmp / "out"
        extract_archive(path, dest, "tar.gz")
        written = sum(os.lstat(os.path.join(d, f)).st_size
                      for d, _s, files in os.walk(dest) for f in files
                      if not os.path.islink(os.path.join(d, f)))
        self.assertEqual(written, rt.unpacked_size(path))

    def test_hard_link_to_missing_member_is_an_error(self):
        path = self.tmp / "bad.tar"
        with tarfile.open(path, "w") as tar:
            _add(tar, "b", type=tarfile.LNKTYPE, linkname="nope")
        with self.assertRaises(rt.ReleaseError):
            rt.unpacked_size(path)

    def test_zip_sums_file_sizes(self):
        path = self.tmp / "t.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("dir/", "")
            zf.writestr("dir/a", b"abc")
            zf.writestr("b", b"12345")
        self.assertEqual(rt.unpacked_size(path), 8)

    def test_measure(self):
        path = self._tar("t.tar.gz", "w:gz")
        meta = rt.measure(path)
        self.assertEqual(meta["format"], "tar.gz")
        self.assertEqual(meta["size"], path.stat().st_size)
        self.assertEqual(meta["sha256"], rt.sha256_file(path))
        self.assertEqual(len(meta["sha256"]), 64)


class WeightsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "src" / "facelib").mkdir(parents=True)
        self.files = {"facelib/S3FD.npy": os.urandom(4096), "facelib/2DFAN.npy": os.urandom(2048)}
        lines = []
        for rel, data in self.files.items():
            (self.tmp / "src" / rel).write_bytes(data)
            import hashlib
            lines.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
        self.manifest = self.tmp / "WEIGHTS.sha256"
        self.manifest.write_text("\n".join(lines) + "\n")

    def test_deterministic_sorted_fixed_metadata(self):
        a = rt.build_weights(self.tmp / "src", self.manifest, self.tmp / "a")
        os.utime(self.tmp / "src" / "facelib" / "S3FD.npy", (1, 1))
        b = rt.build_weights(self.tmp / "src", self.manifest, self.tmp / "b")
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(a.name, rt.weights_artifact_name(self.manifest))
        with tarfile.open(a) as tar:
            members = tar.getmembers()
        self.assertEqual([m.name for m in members], sorted(self.files))
        for m in members:
            self.assertEqual((m.mtime, m.uid, m.gid, m.mode, m.uname), (rt.WEIGHTS_MTIME, 0, 0, 0o644, ""))
        self.assertEqual(rt.unpacked_size(a), sum(len(d) for d in self.files.values()))

    def test_sha_mismatch_is_refused(self):
        (self.tmp / "src" / "facelib" / "S3FD.npy").write_bytes(b"tampered" * 200)
        with self.assertRaises(rt.ReleaseError):
            rt.build_weights(self.tmp / "src", self.manifest, self.tmp / "a")

    def test_manifest_rejects_escaping_paths(self):
        self.manifest.write_text("0" * 64 + "  ../evil.npy\n")
        with self.assertRaises(rt.ReleaseError):
            rt.read_weights_manifest(self.manifest)


def _fake_runtime(tmp: Path):
    """Source, weights and a fake env archive that install like the real ones."""
    src = tmp / "src.tar.gz"
    with tarfile.open(src, "w:gz") as tar:
        _add(tar, "main.py", b"print('dfl')\n", mode=0o644)
        for d in rt.REQUIRED_SOURCE_DIRS:
            _add(tar, f"{d}/__init__.py", b"", mode=0o644)
        _add(tar, "recaster_bridge/VERSION", b"protocol=1\nbridge=1.0.0\n", mode=0o644)
        _add(tar, "SOURCE.txt", b"commit: " + b"a" * 40 + b"\n", mode=0o644)
    weights = tmp / "recaster-dfl-weights-000000000000.tar"
    with tarfile.open(weights, "w") as tar:
        _add(tar, "facelib/S3FD.npy", b"w" * 4096, mode=0o644)
    env = tmp / "rdfl-env-test.tar.gz"
    python_shim = f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n".encode()
    unpack = b"import pathlib, os\npathlib.Path('unpacked.txt').write_text(os.getcwd())\n"
    with tarfile.open(env, "w:gz") as tar:
        _add(tar, "bin/python", python_shim, mode=0o755)
        _add(tar, "bin/python3.10", type=tarfile.SYMTYPE, linkname="python")
        _add(tar, "bin/conda-unpack", unpack, mode=0o755)
        _add(tar, "lib/libx.so", b"z" * 100, mode=0o644)
        _add(tar, "lib/libx.so.1", type=tarfile.LNKTYPE, linkname="lib/libx.so")
    return src, weights, env


@unittest.skipUnless(HAVE_PYDANTIC, "pydantic not installed (runs in release.yml tools job)")
class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src, self.weights, self.env = _fake_runtime(self.tmp)

    def _env_meta(self, platform="macos-arm64-metal"):
        meta = _meta(self.env)
        meta.update(platform=platform, env_id=f"{platform}-0123456789ab", python="3.10.16",
                    pins={"tensorflow": "2.16.2", "numpy": "1.26.4"}, min_os="12.0")
        return meta

    def _lock(self, envs=None, **kw):
        return rt.make_lock(tag="rdfl-dryrun-abc", commit="a" * 40, repo="https://github.com/PixelMorphDev/recaster-dfl",
                            repo_root=ROOT, source=_meta(self.src), weights=_meta(self.weights),
                            envs=envs if envs is not None else [self._env_meta()], **kw)

    def test_valid_lock_and_manifest(self):
        from recaster_app.lock_model import RuntimeLock
        lock, manifest = self._lock(comment="dry run")
        parsed = RuntimeLock.model_validate(lock)
        self.assertFalse(parsed.placeholder)
        self.assertTrue(parsed.is_published_for("macos-arm64-metal"))
        self.assertEqual(parsed.dfl.bridge_protocol, 1)
        keys = {m["role"]: m["key"] for m in manifest}
        self.assertEqual(keys["source"], f"dfl/rdfl-dryrun-abc/{self.src.name}")
        self.assertEqual(keys["weights"], f"dfl/weights/{self.weights.name}")
        self.assertEqual(keys["env"], f"dfl/rdfl-dryrun-abc/{self.env.name}")
        self.assertEqual(lock["artifacts"]["source"]["urls"],
                         [f"https://runtimes.recaster.studio/{keys['source']}"])
        self.assertEqual(list(lock), ["schema", "runtime", "tag", "placeholder", "comment", "dfl",
                                      "artifacts", "signature", "signing_key_id"])

    def test_unknown_platform_rejected(self):
        with self.assertRaises(Exception):
            self._lock(envs=[self._env_meta("linux-aarch64")])

    def test_untrusted_base_url_rejected(self):
        with self.assertRaises(Exception):
            self._lock(base_url="https://example.com")

    def test_duplicate_platform_rejected(self):
        with self.assertRaises(rt.ReleaseError):
            self._lock(envs=[self._env_meta(), self._env_meta()])

    @unittest.skipIf(os.name == "nt", "POSIX env layout")
    def test_install_check_mirrors_runtime_manager(self):
        lock, _manifest = self._lock()
        lock_path = self.tmp / "dfl_runtime.lock.json"
        rt.dump_json(lock, lock_path)
        root = self.tmp / "runtimes"
        result = rt.install_check(lock_path, "macos-arm64-metal", self.tmp, root)
        env_dir = Path(result["env_dir"])
        self.assertEqual(env_dir.parent, root / "envs")
        self.assertTrue(env_dir.name.startswith("macos-arm64-metal-0123456789ab-"))
        # conda-unpack ran in the final env dir, not the .partial staging dir
        self.assertEqual(os.path.realpath((env_dir / "unpacked.txt").read_text()), os.path.realpath(env_dir))
        source = Path(result["source_dir"])
        self.assertEqual(source, root / "dfl" / "rdfl-dryrun-abc")
        self.assertEqual((source / "facelib" / "S3FD.npy").stat().st_size, 4096)
        self.assertFalse(list(root.rglob("*.partial")))

    def test_install_check_rejects_a_changed_archive(self):
        lock, _manifest = self._lock()
        lock_path = self.tmp / "dfl_runtime.lock.json"
        rt.dump_json(lock, lock_path)
        with open(self.src, "ab") as f:
            f.write(b"\0")
        with self.assertRaises(rt.ReleaseError):
            rt.install_check(lock_path, "macos-arm64-metal", self.tmp, self.tmp / "runtimes")

    def test_install_check_rejects_a_prefixed_source_archive(self):
        prefixed = self.tmp / "src.tar.gz"
        with tarfile.open(prefixed, "w:gz") as tar:
            _add(tar, "recaster-dfl/main.py", b"", mode=0o644)
        lock, _manifest = self._lock()
        lock_path = self.tmp / "dfl_runtime.lock.json"
        rt.dump_json(lock, lock_path)
        with self.assertRaises(rt.ReleaseError):
            rt.install_check(lock_path, "macos-arm64-metal", self.tmp, self.tmp / "runtimes")


class VendorTests(unittest.TestCase):
    def test_vendored_app_files_unchanged(self):
        self.assertEqual(rt.check_vendor(), [])

    def test_env_id_uses_the_env_spec(self):
        env_id = rt.env_id(ROOT, "linux-x86_64-cuda12")
        self.assertRegex(env_id, r"^linux-x86_64-cuda12-[0-9a-f]{12}$")
        self.assertEqual(rt.env_spec(ROOT, "linux-x86_64-cuda12").name, "environment.yml")


if __name__ == "__main__":
    unittest.main()
