# recaster-dfl changes

Changes made by PixelMorph LLC to MachineEditor/DeepFaceLab-MVE at
`6e366896e0119e26600c3fcebc914e6fc54fcfee`. Upstream's own history is in
`CHANGELOG.md` and in git.

## Unreleased (2026-09-25)

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

### Added

- `envs/{linux-x86_64-cuda12,macos-arm64-metal,windows-x86_64-cuda11}/environment.yml`:
  per-platform runtime environment specs, and the single source of runtime
  pins. Upstream's root `environment.yml` and `requirements-*.txt` are left
  unchanged and are not used.
- `ci/import_smoke.py` and `.github/workflows/ci.yml`: CPU import smoke and
  a weights-free source-tarball build.
- `NOTICE.md`, `CHANGES.md`, `WEIGHTS.sha256`, and a recaster-dfl section in
  `README.md`.

### Not carried over from Recaster's vendored copy

- `core/interact/interact_streaming.py` and its `DFL_STREAMING_MODE` branch
  in `core/interact/interact.py`: an unused WebSocket preview hook.
  `interact.py` is unchanged from upstream.
- `requirements.txt`, `requirements_3.10.txt` to `requirements_3.13*.txt`,
  and `PYTHON_VERSION_FIX.md`: unmaintained pin files, replaced by `envs/`.
- Comment and `tensorflow[and-cuda]` edits to `requirements-colab.txt` and
  `requirements-cuda.txt`. Those files are upstream's, and `envs/` is now
  authoritative.
