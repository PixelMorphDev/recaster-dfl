# ci/release

Tooling for `.github/workflows/release.yml`, the runtime release pipeline
(Recaster RF-501 slice E2). The workflow header describes the jobs; this
file covers the modes and the tag-day steps.

| File | Purpose |
|------|---------|
| `build_source.sh` | Weights-free `git archive` of HEAD + `SOURCE.txt` |
| `release_tools.py` | Weights tar, `measure`, `env-id`, `make-lock`, `install-check`, `check-vendor` |
| `env_info.py` | Pins, min OS/driver, AGPL/forbidden-package guard for the built env |
| `runtime_smoke.py` | Probe, CPU S3FD/2DFAN pass, ONNX export check, import smoke |
| `publish_r2.py` | No-overwrite upload to R2 `recaster-runtimes`, lock last |
| `lock_pr_checks.py`, `open_lock_pr.sh` | Owner-side Recaster lock PR from a published run |
| `pack-env/` | Locked conda-pack tool env |
| `vendor/`, `sync_vendor.sh` | Recaster's lock model and safe-extract, vendored (see `VENDORED.txt`) |

## Modes

A tag push of `rdfl-YYYY.M.P[-rcN]` on a main commit is a release. Pull
requests, `workflow_dispatch` and anything else are dry runs, tagged
`rdfl-dryrun-<sha12>`.

Only the tag name, artifact retention, the lock's comment and the `publish`
job differ between the two. The build inputs are the same: the env build
never uses a package cache (`cache-downloads: false`,
`cache-environment: false`, no key inputs), so every dry run is a rehearsal
of the release build. `tests/test_release_qa_guards.py`
(`WorkflowModeParityTests`) fails if a cache input becomes an expression, a
cache key is passed without its flag literally `true`, or the env job
branches on the release flag, the event or the ref. That keeps
`rdfl-2026.10.0-rc1` from happening again: its env jobs failed on a
release-only input combination (`cache-downloads: false` with
`cache-downloads-key`) that no dry run had used.

## Tags

Tags are never moved, deleted or reused, even when the run failed before
uploading. Fix forward with a PR and tag the next `-rcN`.

| Tag | Commit | Run | Result |
|-----|--------|-----|--------|
| `rdfl-2026.10.0-rc1` | `a320d9d` | [36125530274](https://github.com/PixelMorphDev/recaster-dfl/actions/runs/36125530274) | Failed in both env jobs (setup-micromamba cache inputs). lock and publish skipped; nothing on R2. Used, not published. |

**Next tag: `rdfl-2026.10.0-rc2`.**

## Tag-day checklist

1. The fix PRs are merged to main and `ci.yml` is green on main.
2. Rehearse the exact commit: run a dry run of `release.yml` on main
   (`gh workflow run release.yml -R PixelMorphDev/recaster-dfl --ref main`;
   needs a token with Actions write) and wait until `tools`, `source`,
   `weights`, both `env` legs and `lock` are green. Check that the run's head
   SHA is the commit you'll tag.
3. `release` environment: the owner is the required reviewer, the deployment
   policy allows `rdfl-*` tags only, and `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`
   and `R2_SECRET_ACCESS_KEY` are set. Check the names only, never print the
   values.
4. The tag isn't already used: `git ls-remote --tags origin 'rdfl-*'` and the
   table above. For this release that is `rdfl-2026.10.0-rc2`.
5. Tag the rehearsed main commit and push only that tag:
   `git tag -a rdfl-2026.10.0-rc2 <sha> -m "rdfl-2026.10.0-rc2"` and
   `git push origin rdfl-2026.10.0-rc2`.
6. Watch the run. When `lock` is green, read `dfl_runtime.lock.json` in the
   run summary (tag, commit, both envs, no dry-run comment), then approve the
   `publish` deployment.
7. `publish` checks every object and the public lock after upload. If it
   fails part-way, re-running the job is safe: identical objects are skipped
   and nothing is overwritten.
8. Add the tag to the table above (PR), then open the Recaster lock PR from
   the run: `ci/release/open_lock_pr.sh <run-id> <recaster-checkout>`.
9. If the tag run fails, leave the tag alone, record it in the table as used,
   fix on a PR (its dry run is the rehearsal) and go back to step 1 with the
   next `-rcN`.
