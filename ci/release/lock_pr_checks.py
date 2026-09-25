# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Checks open_lock_pr.sh runs before it copies a lock into the Recaster app.

    lock_pr_checks.py --repo OWNER/NAME --run run.json --lock dfl_runtime.lock.json

``run.json`` is ``gh api repos/<repo>/actions/runs/<id>``. Stdlib only (it
runs with the owner's python3). Prints the release tag on success, exits 1
with the reasons otherwise. Requires:

- the run: a successful ``push`` run of ``.github/workflows/release.yml`` in
  ``--repo`` (head repository too) for a release tag (``rdfl-YYYY.M.P[-rcN]``)
- the lock: that tag and the run's commit, not a placeholder, no ``DRY RUN``
  comment, and every URL ``<base>/dfl/<tag>/<name>`` or ``<base>/dfl/weights/<name>``
- the public copy at ``<base>/dfl/<tag>/dfl_runtime.lock.json`` (uploaded by
  the publish job) byte-identical to the artifact
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_tools import DEFAULT_BASE_URL, KEY_PREFIX, RELEASE_TAG_RE  # noqa: E402

WORKFLOW_PATH = ".github/workflows/release.yml"
LOCK_NAME = "dfl_runtime.lock.json"
MAX_LOCK_BYTES = 1 << 20


def run_problems(run: dict, repo: str) -> list:
    problems = []
    for key, expected in (("event", "push"), ("status", "completed"), ("conclusion", "success"),
                          ("path", WORKFLOW_PATH)):
        if run.get(key) != expected:
            problems.append(f"run {key} is {run.get(key)!r}, expected {expected!r}")
    for key in ("repository", "head_repository"):
        name = (run.get(key) or {}).get("full_name")
        if name != repo:
            problems.append(f"run {key} is {name!r}, expected {repo!r}")
    tag = run.get("head_branch")
    if not isinstance(tag, str) or not RELEASE_TAG_RE.fullmatch(tag):
        problems.append(f"run ref {tag!r} is not a release tag (rdfl-YYYY.M.P[-rcN])")
    return problems


def _artifacts(lock: dict) -> list:
    arts = lock.get("artifacts") or {}
    items = [("source", arts.get("source")), ("weights", arts.get("weights"))]
    items += [(f"env {p}", a) for p, a in sorted((arts.get("envs") or {}).items())]
    return items


def lock_problems(lock: dict, tag: str, commit: str, base_url: str = DEFAULT_BASE_URL) -> list:
    problems = []
    if lock.get("tag") != tag:
        problems.append(f"lock tag {lock.get('tag')!r}, the run is for {tag!r}")
    if lock.get("placeholder") is not False:
        problems.append("the lock is a placeholder")
    if "DRY RUN" in str(lock.get("comment") or "").upper():
        problems.append("the lock is from a dry run (comment says DRY RUN)")
    if (lock.get("dfl") or {}).get("commit") != commit:
        problems.append(f"lock commit {(lock.get('dfl') or {}).get('commit')!r}, the run built {commit!r}")
    base = base_url.rstrip("/")
    prefixes = (f"{base}/{KEY_PREFIX}/{tag}/", f"{base}/{KEY_PREFIX}/weights/")
    for role, artifact in _artifacts(lock):
        if not isinstance(artifact, dict):
            problems.append(f"{role}: missing")
            continue
        urls = artifact.get("urls") or []
        if not urls:
            problems.append(f"{role}: no urls")
        for url in urls:
            if not isinstance(url, str) or url not in [p + str(artifact.get("name")) for p in prefixes]:
                problems.append(f"{role}: url {url!r} is not {prefixes[0]}<name> or {prefixes[1]}<name>")
    return problems


def public_lock_url(tag: str, base_url: str = DEFAULT_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{KEY_PREFIX}/{tag}/{LOCK_NAME}"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Recaster/lock-pr", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(MAX_LOCK_BYTES + 1)
    if len(data) > MAX_LOCK_BYTES:
        raise ValueError(f"{url}: more than {MAX_LOCK_BYTES} bytes")
    return data


def check(run: dict, lock_bytes: bytes, repo: str, base_url: str = DEFAULT_BASE_URL, fetcher=fetch) -> tuple:
    """(tag, problems). The public copy is only fetched once the run and the lock pass."""
    problems = run_problems(run, repo)
    if problems:
        return None, problems
    tag = run["head_branch"]
    try:
        lock = json.loads(lock_bytes.decode("utf-8"))
    except ValueError as e:
        return tag, [f"the lock artifact is not JSON: {e}"]
    problems = lock_problems(lock, tag, run.get("head_sha"), base_url)
    if problems:
        return tag, problems
    url = public_lock_url(tag, base_url)
    try:
        public = fetcher(url)
    except Exception as e:  # any fetch failure means "not verifiably published"
        return tag, [f"could not fetch the published lock {url}: {e}"]
    if public != lock_bytes:
        return tag, [f"the published lock {url} differs from the run's artifact"]
    return tag, []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lock_pr_checks")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run", required=True, help="gh api repos/<repo>/actions/runs/<id> output")
    parser.add_argument("--lock", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    tag, problems = check(run, Path(args.lock).read_bytes(), args.repo, args.base_url)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
