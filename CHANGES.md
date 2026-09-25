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
  published run.
- `tests/test_release_tools.py`: release tooling tests (`unpacked_size`,
  weights determinism, lock validation, install emulation, vendored files).

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
