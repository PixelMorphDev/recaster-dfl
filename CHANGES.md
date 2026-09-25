# recaster-dfl changes

Changes made by PixelMorph LLC to MachineEditor/DeepFaceLab-MVE at
`6e366896e0119e26600c3fcebc914e6fc54fcfee`. Upstream's own history is in
`CHANGELOG.md` and in git.

## Unreleased (2026-09-25): recaster_bridge v1, release pipeline

### Added

- `.github/workflows/release.yml` and `ci/release/`: the runtime release
  pipeline (Recaster RF-501 slice E2). On an `rdfl-*` tag it builds the
  weights-free source tarball, the weights tar (from upstream `6e36689`,
  checked against `WEIGHTS.sha256`) and conda-packed envs for
  `linux-x86_64-cuda12` and `macos-arm64-metal`. It installs them into a
  fresh prefix with Recaster's own safe-extract rules and `conda-unpack`,
  runs the bridge probe, a CPU S3FD/2DFAN pass and the import smoke, writes
  `dfl_runtime.lock.json` (validated with Recaster's lock model, vendored
  in `ci/release/vendor/`), and, after owner approval in the `release`
  environment, uploads everything to R2 without overwriting existing
  objects. Pull requests and manual runs are dry runs with no secrets and
  no upload. `ci/release/open_lock_pr.sh` opens the Recaster lock PR from a
  published run. The PR also bumps the app's `third_party/deepfacelab`
  submodule to the lock's `dfl.commit` (fetched from this repo's main, and
  refused if the commit isn't on it). The script stops before changing
  anything if the app checkout has tracked changes or the PR branch already
  exists locally or on origin.
- Hash-pinned env builds (design 4.6). Each built platform
  (`linux-x86_64-cuda12`, `macos-arm64-metal`) now has
  `envs/<platform>/conda-lock.yml` (conda-lock 3.0.4, exact conda-forge
  builds with sha256) and `requirements.txt` (the PyPI closure from
  `requirements.in`, exact versions with hashes, minus what conda provides).
  release.yml builds from them with micromamba and
  `pip install --require-hashes --no-deps --only-binary :all:` + `pip check`.
  `envs/lock.sh` regenerates both from `environment.yml` / `requirements.in`.
  The formerly floating PyPI deps are pinned at the versions the first dry run
  resolved: `ffmpeg-python==0.2.0`, `tf2onnx==1.17.0`, `tensorboardX==2.6.5`.
  `onnx` is pinned to 1.18.0: the resolved 1.19.0 needs
  `ml_dtypes.float4_e2m1fn` (ml-dtypes >= 0.5; 1.19.1+ declares it) and TF
  2.16.2 pins ml-dtypes 0.3.x, so `import tf2onnx` failed and DFM export was
  broken. 1.18.0 is the newest onnx without an ml_dtypes dependency.
  The env id is `<platform>-<sha12>` over both lock files.
- The conda-pack tool env (`python=3.11`, `conda-pack=0.9.2`) is installed
  from `ci/release/pack-env/conda-lock.yml` (conda-lock 4.0.2, linux-64 and
  osx-arm64, sha256 per package; micromamba 2.9.0 rejects a mismatch)
  instead of being solved at build time.
- The release smoke requires the probe's `devices` to be a list (empty on CPU
  runners); `null` (enumeration failed) fails the smoke.
- The release smoke also exports a tiny leras graph to ONNX the way the
  models' `export_dfm` does (`tf2onnx.convert._convert_common`, opsets 12 and
  13), checks it with `onnx.checker` and compares onnx's reference evaluator
  with TF's output, so DFM export can't silently break again.
- Vendored Recaster lock model refreshed (app `6e1c720`): a dry-run lock is
  complete but never published (`is_published_for` is False when
  `release_problem()` is set). The dry-run install and `make-lock` check
  `is_complete_for`; for a release tag, `make-lock` also requires
  `release_problem()` to be None, so a release can't produce a lock the app
  would refuse to publish.
- Each env's `explicit.txt` and `pip-freeze.txt` are published next to it
  (`dfl/<tag>/rdfl-env-<env_id>.{explicit,pip-freeze}.txt`) and listed in the
  publish manifest.
- `ci/release/env_info.py` fails the build if any installed package is
  AGPL-licensed (pip `License` / `License-Expression` / classifiers, conda
  `conda-meta` `license`).
- `ci/release/lock_pr_checks.py`: `open_lock_pr.sh` now accepts only a
  successful tag-push run of `release.yml` in this repo for an
  `rdfl-YYYY.M.P[-rcN]` tag, a lock for that tag and commit with no dry-run
  comment (`dry run`/`dry-run`/`dry_run`/`dryrun`, any case, as the app's lock
  model), URLs only under `dfl/<tag>/` (and, for the weights only,
  `dfl/weights/`), and
  requires the public `dfl/<tag>/dfl_runtime.lock.json` to be byte-identical
  to the run's artifact.
- `ci/release/publish_r2.py` refuses a manifest `file` that isn't a bare name,
  a `key` other than `dfl/<tag>/<file>` or `dfl/weights/<file>`, and a lock
  tag that isn't a release tag. The publish job fails (instead of skipping)
  when the R2 secrets are missing on a tag run, and checks every manifest
  object and the public lock after upload.
- `ci/release/vendor/VENDORED.txt` records the app constants
  `release_tools.py` mirrors (`const:` lines, with their app locations);
  `check-vendor` compares them, and `check-vendor --app` / `sync_vendor.sh`
  also compare the vendored files and constants with an app checkout.
- `tests/test_release_tools.py`, `tests/test_publish_r2.py`,
  `tests/test_lock_pr_checks.py`, `tests/test_env_info.py`,
  `tests/test_env_locks.py`: release tooling tests (`unpacked_size`, weights
  determinism, lock validation, install emulation, vendored files and
  constants, no-overwrite R2 publishing and manifest validation against a
  fake `aws` cli, lock PR checks, the AGPL guard, and the committed env locks).
  `tests/test_release_qa_guards.py` (QA): the lock PR checks refuse every lock
  the app's lock model refuses to publish, and the env build installs only
  from the committed locks.

- `recaster_bridge/` (protocol 1, bridge 1.0.0): a file-based JSON-lines
  side channel for running DFL out of process. Active only when
  `RECASTER_BRIDGE=1` and `RECASTER_RUN_DIR` names an existing run
  directory, and only in the process that ran `main.py` (spawned worker
  processes use the stock interact).
  - `protocol.py`: the event writer (`events.jsonl`, one `O_APPEND` write per
    line, monotonic `seq`) and control-line parsing.
  - `answers.py`: prompt answers from `answers.json`
    (case-insensitive substring match, first hit wins; policies
    `answers_then_default` and `answers_then_ask`).
  - `control.py`: polls `control.jsonl` (`heartbeat`, `stop`, `answer`;
    training commands answer with an `unsupported_command` warning for now),
    the heartbeat watchdog and `alive` events.
  - `hooks.py`: activation guards, the `hello` event, and exactly one
    `done` event per run (exit code, uncaught exception, or `stop`, which
    terminates DFL's worker processes and exits with code 130).
  - `interact.py`: `InteractDesktop` subclass that answers `input_*` from
    the answers file, emits `answered` for every prompt, `progress` for
    `progress_bar*` (throttled to 10 Hz) and `warning` for `log_err`.
    Terminal output is unchanged.
  - `probe.py`: `python -m recaster_bridge.probe --json` prints versions
    and devices as one JSON line.
  - `VERSION`: `protocol=1` / `bridge=1.0.0`, read by Recaster without
    importing the package.
- `tests/`: bridge unit and subprocess tests (stdlib `unittest`, no
  TensorFlow), and the protocol v1 golden fixtures in
  `tests/fixtures/dfl_protocol/v1/` (byte-identical in Recaster).
- `.github/workflows/ci.yml`: `bridge-tests` job.

### Changed

- `core/interact/interact.py`: `RECASTER_BRIDGE=1` selects
  `recaster_bridge.interact.make_interact(InteractDesktop)`. With the
  variable unset the module is unchanged from upstream and the bridge is
  never imported.
- `main.py`: `extract --face-type` accepts `midfull_face`, which
  `Extractor.main` already supported (`FaceType.fromString`) but the CLI
  rejected.

### Fixed

- `recaster_bridge/hooks.py`, `control.py` (bridge 1.1.0, protocol still 1;
  Recaster REC-198): a stop left DFL processes behind when the caller had
  died. The heartbeat watchdog only SIGTERMed `multiprocessing.active_children()`
  and then called `os._exit`, so grandchildren, workers that ignore SIGTERM
  and the resource tracker lived on with PPID 1. Now, when the bridge leads
  its process group (the Recaster runner starts DFL in its own session),
  every stop (control `stop`, heartbeat lost, parent exited) writes
  `done{cancelled}` and closes `events.jsonl`, then SIGTERMs every other
  group member, waits up to 2 s, SIGKILLs what is left and exits 130. Only
  a member that survives SIGKILL, or a group that can't be listed (`/proc`
  on Linux, `ps` elsewhere), makes it `killpg(SIGKILL)` the group, itself
  included. A bridge that doesn't lead its group never signals it. With a
  heartbeat set, the control thread also checks its parent pid once a
  second: when it changes (the caller died and the process was reparented,
  to pid 1 or a subreaper), it emits `warning{code: "parent_lost"}` and
  stops with `state{stopping, reason: "parent_exited"}` instead of waiting
  out the heartbeat timeout. No new event types (Recaster's reader rejects
  unknown ones); `parent_lost` and `parent_exited` are new values of the
  free-form `warning.code` and `state.reason`. If DFL's main thread exits
  while a stop is reaping, the atexit hook waits for the stop to finish.
  multiprocessing's resource tracker is never signalled: it ignores SIGTERM,
  so it used to wait out the 2 s grace and get SIGKILLed before it could
  unlink the named semaphores it tracks (on macOS they persist until
  reboot). It reads EOF when the bridge and its workers are gone, unlinks
  and exits on its own; the Recaster runner's group kill after the bridge
  exits is the safety net. `done{cancelled}` is now written and
  `events.jsonl` closed before any worker is terminated, so a crash a dying
  worker causes in the main thread can't turn the stop into `done{error}`.
  `tests/test_bridge_reap.py` runs a stub with multiprocessing workers, a
  SIGTERM-ignoring worker, a grandchild and the resource tracker, SIGKILLs
  its intermediate parent (or stops heartbeating) and checks that the group
  is empty within 6 s; a stub holding a `multiprocessing.Lock` must exit
  within 1.5 s of a stop and leave no named semaphore behind.

- `.github/workflows/release.yml`: the first tag run (`rdfl-2026.10.0-rc1`,
  run 36125530274) failed in both env jobs before building anything, with
  `You must enable 'cache-downloads' to use 'cache-downloads-key'`. Release
  mode set `cache-downloads: false` but still passed `cache-downloads-key`;
  PR dry runs enabled the cache, so they never took that path. Nothing was
  uploaded. The env build now uses no package cache in either mode (literal
  `cache-downloads: false`, `cache-environment: false`, no key inputs), so a
  PR or manual dry run builds with exactly the release's inputs and is a
  rehearsal of the tag run. The conda-pack tool env was not affected (it is
  created with `micromamba create` from its lock, not the action).
  `tests/test_release_qa_guards.py` parses the workflow (PyYAML 6.0.3, pinned
  in the tools job) and fails if any cache input is an expression, a cache
  key is passed without its flag set to a literal true, or the env job
  references the release flag, the event or the ref. `rdfl-2026.10.0-rc1`
  stays as it is (tags are never moved or reused); the next tag is
  `rdfl-2026.10.0-rc2`. `ci/release/README.md` has the tag-day checklist.
- `recaster_bridge/probe.py`: `devices` was always `null`, because
  `Devices.getDevices()` raises until `Devices.initialize_main_env()` has run.
  The probe now runs it first (before it imports TensorFlow, as DFL's main
  does), so it reports DFL's device table (`METAL` on Apple Silicon, CUDA
  GPUs, `[]` on CPU-only hosts). When enumeration fails it still reports
  `null` and prints the reason to stderr. It is skipped when TensorFlow isn't
  importable.
- `core/leras/device.py`: `Devices.initialize_main_env()` waited forever
  (`p.join(); q.get()` with no timeout) when the enumeration child died
  without a result, for example a TensorFlow that is found but fails or
  crashes on import. It now reads the queue before joining, polls it once a
  second, and raises `RuntimeError` when the child exits without a result or
  after `NN_DEVICES_TIMEOUT_S` seconds (default 120), terminating the child
  first. The probe reports that as `devices: null` with the reason on stderr.

## 2026-09-25: baseline

First recaster-dfl baseline. The code changes are forward-ported from the
DeepFaceLab copy previously vendored inside Recaster, one commit per
category.

### Fixed

- **NumPy 1.24+ compatibility.** Replaced the removed `np.int` / `np.float`
  aliases with `np.int64` / `np.float64`.
  - `XSegEditor/XSegEditor.py`, `core/imagelib/text.py`, `core/qtex/qtex.py`,
    `facelib/FANExtractor.py`, `facelib/LandmarksProcessor.py`,
    `facelib/S3FDExtractor.py`, `mainscripts/Extractor.py`
  - Two further `np.float` uses, replaced with `np.float64`:
    `core/imagelib/text.py` (`get_draw_text_lines`, used by manual extract)
    and `mainscripts/XSegUtil.py` (`apply_xseg`, when face types differ).
  - No other removed NumPy aliases (`np.int`, `np.float`, `np.bool`,
    `np.object`, `np.str`, `np.long`, `np.complex`) remain in the tree.
- **XSeg masks from normalized polygons.** `core/imagelib/SegIEPolys.py`
  `overlay_mask` scales `[0, 1]` polygon coordinates to pixels before
  rasterizing. Before this fix they truncated to `(0, 0)` and produced empty
  training masks. Pixel-coordinate polygons are unchanged.
- **GPU device selection.**
  - `core/leras/device.py`: `get_best_device` / `get_worst_device` fall back
    to the first device when none wins the memory comparison (Metal GPUs
    report 0 memory).
  - `core/leras/nn.py`: `getCurrentDeviceConfig` references
    `nn.DeviceConfig`. The bare `DeviceConfig` it used before was unbound
    and raised `NameError`.
- **Thread limits.** `main.py` defaults `OPENCV_NUM_THREADS`,
  `OMP_NUM_THREADS`, `MKL_NUM_THREADS` and `NUMEXPR_NUM_THREADS` to 4
  (caller values win) and applies `cv2.setNumThreads`. `core/cv2ex.py` does
  the same at import, as a fallback for subprocess entry points. This avoids
  `Can't spawn new thread: res = 11` on thread-constrained hosts.
- **Output directories and write errors.**
  - `core/cv2ex.py`: `cv2_imwrite` creates the parent directory, returns
    `True` / `False`, and logs failures instead of swallowing them.
  - `mainscripts/Extractor.py`: creates the output directory if it's
    missing, and skips a face whose crop failed to save instead of reading
    a file that was never written.
- **Misc.**
  - `main.py`: removed invalid `\ ` escapes in three help strings, which
    raise a `SyntaxWarning` on Python 3.12+.
  - `utils/__init__.py`: added, so `utils` is a regular package.

### Changed

- `.gitignore`: added `!tests/` and `!tests/**` exceptions to upstream's
  `test*` pattern, so files under `tests/` are tracked
  (`tests/**/__pycache__/` stays ignored). Other `test*` paths are still
  ignored as upstream intended.

### Removed

- `doc/`: upstream screenshots, artwork and feature write-ups, which are
  not used at runtime. Links in `README.md` and `CHANGELOG.md` now point to
  upstream at `6e36689`.
- `facelib/*.npy` are no longer tracked (now in `.gitignore`). Their
  sha256s are in `WEIGHTS.sha256`, and they ship as a separate artifact.
  They are still present in upstream's git history.
- `.github/ISSUE_TEMPLATE.md`: upstream's issue template, which doesn't
  apply to this repository.

### Added

- `envs/{linux-x86_64-cuda12,macos-arm64-metal,windows-x86_64-cuda11}/environment.yml`:
  per-platform runtime environment specs, and the single source of runtime
  pins. Upstream's root `environment.yml` and `requirements-*.txt` are left
  unchanged and are not used. `tf2onnx` (DFM export) resolves to 1.17.0
  with TensorFlow 2.16.2 and NumPy 1.26.4 on Linux py3.10 (PyPI). On
  Windows, TensorFlow 2.10.1's `protobuf<3.20` cap makes pip backtrack to
  tf2onnx 1.14.0 / onnx 1.12.0.
- `ci/import_smoke.py` and `.github/workflows/ci.yml`: CPU import smoke and
  a weights-free source-tarball build.
- conda-lock files (`envs/<platform>/conda-lock.yml`) are not committed
  yet. Generating them from each `environment.yml` is planned.
- `NOTICE.md`, `CHANGES.md`, `WEIGHTS.sha256`, and a recaster-dfl section in
  `README.md`.

### Retained

- `flaskr/`: kept unchanged from upstream. It serves the browser training
  preview behind `main.py train --flask-preview` (lazy import in
  `mainscripts/Trainer.py`). Its `flask` / `flask-socketio` dependencies
  are not in `envs/` yet, so that flag fails with `ImportError` until they
  are added.

### Not carried over from Recaster's vendored copy

- `core/interact/interact_streaming.py` and its `DFL_STREAMING_MODE` branch
  in `core/interact/interact.py`: an unused WebSocket preview hook.
  `interact.py` is unchanged from upstream.
- `requirements.txt`, `requirements_3.10.txt` to `requirements_3.13*.txt`,
  and `PYTHON_VERSION_FIX.md`: unmaintained pin files, replaced by `envs/`.
- Comment and `tensorflow[and-cuda]` edits to `requirements-colab.txt` and
  `requirements-cuda.txt`. Those files are upstream's, and `envs/` is now
  authoritative.
