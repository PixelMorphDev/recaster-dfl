# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Upload the release artifacts to Cloudflare R2 (S3 API via the aws cli).

    publish_r2.py --manifest publish-manifest.json --dist <dir> --lock dfl_runtime.lock.json

Environment: ``R2_ACCOUNT_ID``, ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``
(the workflow maps the ``release`` environment's R2 secrets onto them) and
``R2_BUCKET``. Nothing here prints a credential or the account endpoint.

The manifest is checked before anything else: the lock's tag must be a
release tag (``rdfl-YYYY.M.P[-rcN]``), every ``file`` a bare file name in
``--dist``, and every ``key`` ``dfl/<tag>/<file>`` or ``dfl/weights/<file>``.

Objects are never overwritten. For each manifest entry the file's size and
sha256 are checked first, then ``head-object``:

- missing: upload with ``x-amz-meta-sha256``, then head again and compare
- present with the same size and sha256 metadata: skip (a re-run is safe)
- present with anything else: fail

The lock itself goes last, to ``dfl/<tag>/dfl_runtime.lock.json``, under
the same rule.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_tools import KEY_PREFIX, RELEASE_TAG_RE, sha256_file  # noqa: E402

CACHE_CONTROL = "public, max-age=31536000, immutable"


class PublishError(Exception):
    pass


def _aws(args, endpoint):
    env = dict(os.environ, AWS_DEFAULT_REGION="auto", AWS_PAGER="",
               # R2 doesn't implement the aws cli >= 2.23 default CRC trailers
               AWS_REQUEST_CHECKSUM_CALCULATION="when_required",
               AWS_RESPONSE_CHECKSUM_VALIDATION="when_required")
    return subprocess.run(["aws", *args, "--endpoint-url", endpoint], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def head(bucket, key, endpoint):
    """Object metadata, or None if the key doesn't exist."""
    proc = _aws(["s3api", "head-object", "--bucket", bucket, "--key", key, "--output", "json"], endpoint)
    if proc.returncode == 0:
        return json.loads(proc.stdout)
    if "404" in proc.stderr or "Not Found" in proc.stderr:
        return None
    raise PublishError(f"head-object {key} failed (exit {proc.returncode}): {_redact(proc.stderr, endpoint)}")


def _redact(text, endpoint):
    return text.replace(endpoint, "<r2-endpoint>").strip()[-800:]


def _matches(meta, sha256, size):
    return (meta.get("ContentLength") == size
            and (meta.get("Metadata") or {}).get("sha256") == sha256)


def check_manifest(manifest, tag):
    """Refuse anything that could write outside the release's keys or read outside --dist."""
    if not isinstance(tag, str) or not RELEASE_TAG_RE.fullmatch(tag):
        raise PublishError(f"the lock's tag {tag!r} is not a release tag (rdfl-YYYY.M.P[-rcN])")
    if not isinstance(manifest, list) or not manifest:
        raise PublishError("the manifest lists no artifacts")
    for entry in manifest:
        name = entry.get("file")
        if (not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name
                or name.startswith(".") or Path(name).name != name):
            raise PublishError(f"manifest file {name!r} is not a bare file name")
        allowed = (f"{KEY_PREFIX}/{tag}/{name}", f"{KEY_PREFIX}/weights/{name}")
        if entry.get("key") not in allowed:
            raise PublishError(f"manifest key {entry.get('key')!r} for {name} is not one of {', '.join(allowed)}")


def put(bucket, key, path, sha256, size, content_type, endpoint):
    existing = head(bucket, key, endpoint)
    if existing is not None:
        if _matches(existing, sha256, size):
            print(f"exists, identical: {key}")
            return "skipped"
        found = (existing.get("Metadata") or {}).get("sha256")
        raise PublishError(f"{key} already exists with different content "
                           f"(size {existing.get('ContentLength')}, sha256 {found}); refusing to overwrite")
    proc = _aws(["s3", "cp", str(path), f"s3://{bucket}/{key}", "--only-show-errors", "--no-progress",
                 "--metadata", f"sha256={sha256}", "--content-type", content_type,
                 "--cache-control", CACHE_CONTROL], endpoint)
    if proc.returncode != 0:
        raise PublishError(f"upload {key} failed (exit {proc.returncode}): {_redact(proc.stderr, endpoint)}")
    after = head(bucket, key, endpoint)
    if after is None or not _matches(after, sha256, size):
        raise PublishError(f"{key}: the uploaded object doesn't match (size/sha256 metadata)")
    print(f"uploaded: {key} ({size} bytes)")
    return "uploaded"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="publish_r2")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dist", required=True)
    parser.add_argument("--lock", required=True)
    args = parser.parse_args(argv)

    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    bucket = os.environ.get("R2_BUCKET", "").strip()
    if not account or not bucket or not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
        print("error: R2 credentials or bucket not set", file=sys.stderr)
        return 1
    endpoint = f"https://{account}.r2.cloudflarestorage.com"
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{endpoint}", flush=True)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    lock_path = Path(args.lock)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    dist = Path(args.dist)
    try:
        check_manifest(manifest, lock.get("tag"))
        # Check every local file before the first upload
        for entry in manifest:
            path = dist / entry["file"]
            if not path.is_file():
                raise PublishError(f"missing artifact {entry['file']}")
            if path.stat().st_size != entry["size"] or sha256_file(path) != entry["sha256"]:
                raise PublishError(f"{entry['file']} doesn't match the manifest")
        results = {}
        for entry in manifest:
            results[entry["key"]] = put(bucket, entry["key"], dist / entry["file"], entry["sha256"],
                                        entry["size"], entry["content_type"], endpoint)
        lock_key = f"{KEY_PREFIX}/{lock['tag']}/{lock_path.name}"
        results[lock_key] = put(bucket, lock_key, lock_path, sha256_file(lock_path), lock_path.stat().st_size,
                                "application/json", endpoint)
    except PublishError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
