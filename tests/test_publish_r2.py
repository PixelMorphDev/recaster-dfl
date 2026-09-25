# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Tests for ci/release/publish_r2.py against a fake ``aws`` cli (a local directory as the bucket)."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISH = ROOT / "ci" / "release" / "publish_r2.py"
sys.path.insert(0, str(ROOT / "ci" / "release"))

import release_tools as rt  # noqa: E402

FAKE_AWS = textwrap.dedent('''\
    import json, os, shutil, sys
    from pathlib import Path
    store = Path(os.environ["FAKE_S3"])
    args = sys.argv[1:]
    def opt(name):
        return args[args.index(name) + 1]
    with open(store / "calls.log", "a") as log:
        log.write(" ".join(args) + "\\n")
    if args[:2] == ["s3api", "head-object"]:
        obj = store / "objects" / opt("--key")
        if not obj.exists():
            sys.stderr.write("An error occurred (404) when calling the HeadObject operation: Not Found\\n")
            sys.exit(254)
        meta = json.loads(obj.with_name(obj.name + ".meta").read_text())
        print(json.dumps({"ContentLength": obj.stat().st_size, "Metadata": meta}))
    elif args[:2] == ["s3", "cp"]:
        src, dest = args[2], args[3]
        key = dest.split("/", 3)[3]
        obj = store / "objects" / key
        obj.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, obj)
        k, _, v = opt("--metadata").partition("=")
        obj.with_name(obj.name + ".meta").write_text(json.dumps({k: v}))
    else:
        sys.exit(2)
''')


@unittest.skipIf(os.name == "nt", "POSIX fake aws")
class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        aws = bin_dir / "aws"
        aws.write_text(f"#!{sys.executable}\n" + FAKE_AWS)
        aws.chmod(0o755)
        self.store = self.tmp / "store"
        self.store.mkdir()
        self.dist = self.tmp / "dist"
        self.dist.mkdir()
        (self.dist / "a.tar.gz").write_bytes(b"artifact-a")
        (self.dist / "w.tar").write_bytes(b"weights")
        self.manifest = self.tmp / "manifest.json"
        entries = []
        for role, name, key in (("source", "a.tar.gz", "dfl/rdfl-2026.10.0/a.tar.gz"),
                                ("weights", "w.tar", "dfl/weights/w.tar")):
            path = self.dist / name
            entries.append({"role": role, "key": key, "file": name, "sha256": rt.sha256_file(path),
                            "size": path.stat().st_size, "content_type": "application/gzip"})
        rt.dump_json(entries, self.manifest)
        self.lock = self.tmp / "dfl_runtime.lock.json"
        rt.dump_json({"tag": "rdfl-2026.10.0"}, self.lock)
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", FAKE_S3=str(self.store),
                        R2_ACCOUNT_ID="acct0123456789", AWS_ACCESS_KEY_ID="AKIDTEST",
                        AWS_SECRET_ACCESS_KEY="s3cr3t-value", R2_BUCKET="recaster-runtimes")
        self.env.pop("GITHUB_ACTIONS", None)

    def publish(self, env=None):
        return subprocess.run([sys.executable, str(PUBLISH), "--manifest", str(self.manifest),
                               "--dist", str(self.dist), "--lock", str(self.lock)],
                              env=env or self.env, capture_output=True, text=True)

    def _objects(self):
        root = self.store / "objects"
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and not p.name.endswith(".meta"))

    def test_uploads_then_rerun_skips_identical(self):
        first = self.publish()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._objects(), ["dfl/rdfl-2026.10.0/a.tar.gz", "dfl/rdfl-2026.10.0/dfl_runtime.lock.json",
                                           "dfl/weights/w.tar"])
        second = self.publish()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.count("exists, identical"), 3)
        self.assertNotIn(" s3 cp", "".join((self.store / "calls.log").read_text().splitlines()[-3:]))

    def test_refuses_to_overwrite_different_content(self):
        self.assertEqual(self.publish().returncode, 0)
        (self.dist / "a.tar.gz").write_bytes(b"artifact-B")
        entries = json.loads(self.manifest.read_text())
        entries[0]["sha256"] = rt.sha256_file(self.dist / "a.tar.gz")
        rt.dump_json(entries, self.manifest)
        result = self.publish()
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual((self.store / "objects" / "dfl/rdfl-2026.10.0/a.tar.gz").read_bytes(), b"artifact-a")

    def test_local_file_not_matching_manifest_uploads_nothing(self):
        (self.dist / "w.tar").write_bytes(b"tampered")
        result = self.publish()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.store / "objects").exists())

    def test_missing_credentials(self):
        env = dict(self.env)
        del env["AWS_SECRET_ACCESS_KEY"]
        result = self.publish(env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not set", result.stderr)

    def test_never_prints_credentials_or_endpoint(self):
        result = self.publish()
        out = result.stdout + result.stderr
        for secret in ("acct0123456789", "AKIDTEST", "s3cr3t-value"):
            self.assertNotIn(secret, out)

    def _fake_prelude(self, code):
        """Run ``code`` in the fake aws cli before its normal dispatch."""
        aws = self.tmp / "bin" / "aws"
        src = aws.read_text()
        marker = 'if args[:2] == ["s3api", "head-object"]:'
        aws.write_text(src.replace(marker, code + "\n" + marker, 1))

    def _set_entry(self, index, **fields):
        entries = json.loads(self.manifest.read_text())
        entries[index].update(fields)
        rt.dump_json(entries, self.manifest)

    def _nothing_uploaded(self):
        log = self.store / "calls.log"
        return not log.exists() or " s3 cp " not in log.read_text()

    def test_head_403_fails_closed_without_upload(self):
        self._fake_prelude('if args[:2] == ["s3api", "head-object"]:\n'
                           '    sys.stderr.write("An error occurred (403) when calling the HeadObject operation: Forbidden\\n")\n'
                           '    sys.exit(254)')
        result = self.publish()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("head-object", result.stderr)
        self.assertTrue(self._nothing_uploaded())

    def test_existing_object_without_sha_metadata_is_refused(self):
        obj = self.store / "objects" / "dfl/weights/w.tar"
        obj.parent.mkdir(parents=True)
        obj.write_bytes(b"weights")          # same size and bytes, but no sha256 metadata
        obj.with_name(obj.name + ".meta").write_text("{}")
        result = self.publish()
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(obj.with_name(obj.name + ".meta").read_text(), "{}")

    def test_manifest_file_must_be_a_bare_name(self):
        outside = self.tmp / "outside.bin"
        outside.write_bytes(b"x")
        for name in ("../outside.bin", str(outside), "sub/a.tar.gz", "..", ".", "", ".hidden", "a\\b"):
            with self.subTest(name=name):
                self._set_entry(0, file=name, key=f"dfl/rdfl-2026.10.0/{name}", size=1,
                                sha256=rt.sha256_file(outside))
                result = self.publish()
                self.assertEqual(result.returncode, 1)
                self.assertIn("not a bare file name", result.stderr)
                self.assertTrue(self._nothing_uploaded())

    def test_manifest_key_must_be_under_the_tag_or_weights(self):
        for key in ("dfl/rdfl-2026.9.0/a.tar.gz", "dfl/a.tar.gz", "other/rdfl-2026.10.0/a.tar.gz",
                    "dfl/rdfl-2026.10.0/b.tar.gz", "dfl/rdfl-2026.10.0/../weights/a.tar.gz",
                    "dfl/rdfl-2026.10.0/sub/a.tar.gz", "/dfl/rdfl-2026.10.0/a.tar.gz"):
            with self.subTest(key=key):
                self._set_entry(0, key=key)
                result = self.publish()
                self.assertEqual(result.returncode, 1)
                self.assertIn("is not one of", result.stderr)
                self.assertTrue(self._nothing_uploaded())

    def test_weights_key_and_tag_key_both_accepted(self):
        self._set_entry(0, key="dfl/weights/a.tar.gz")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dfl/weights/a.tar.gz", self._objects())

    def test_lock_tag_must_be_a_release_tag(self):
        for tag in ("rdfl-dryrun-0123456789ab", "rdfl-2026.10", "rdfl-2026.10.0-beta", "../x", None):
            with self.subTest(tag=tag):
                rt.dump_json({"tag": tag}, self.lock)
                result = self.publish()
                self.assertEqual(result.returncode, 1)
                self.assertIn("not a release tag", result.stderr)
                self.assertTrue(self._nothing_uploaded())

    def test_masks_endpoint_on_actions(self):
        env = dict(self.env, GITHUB_ACTIONS="true")
        result = self.publish(env)
        self.assertEqual(result.stdout.splitlines()[0],
                         "::add-mask::https://acct0123456789.r2.cloudflarestorage.com")


if __name__ == "__main__":
    unittest.main()
