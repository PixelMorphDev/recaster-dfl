# NOTICE

recaster-dfl is a modified version of DeepFaceLab. It is maintained by
PixelMorph LLC.

## Lineage

```
iperov/DeepFaceLab                         (GPL-3.0; archived by its owner 2024-11-13)
  -> MachineEditor/DeepFaceLab-MVE         @ 6e366896e0119e26600c3fcebc914e6fc54fcfee (2024-08-23)
    -> recaster-dfl                        (this repository; branch main)
```

The complete upstream git history, including authorship of every upstream
commit, is kept in this repository. Every commit on top of
`6e366896e0119e26600c3fcebc914e6fc54fcfee` is a recaster-dfl modification.

## Copyright

Copyright for the original DeepFaceLab code belongs to iperov and the
DeepFaceLab contributors. Copyright for the DeepFaceLab-MVE changes belongs
to the MachineEditor/DeepFaceLab-MVE contributors. The upstream git history
records who wrote what. The modifications listed in CHANGES.md are
Copyright (C) 2026 PixelMorph LLC.

## License

This program is free software. You can redistribute it and/or modify it
under the terms of the GNU General Public License, version 3, as published
by the Free Software Foundation. The full text is in `LICENSE`, unchanged
from upstream. The recaster-dfl modifications are licensed under the same
terms.

This program is distributed WITHOUT ANY WARRANTY, without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
General Public License for details.

## Modifications (GPL-3.0 section 5(a))

Every modified upstream file carries a header comment of the form
`Modified by PixelMorph LLC, 2026-09-25: <what>`. CHANGES.md lists every
change. In summary:

- NumPy 1.24+ compatibility (`np.int` / `np.float` aliases replaced).
- XSeg: normalized polygon coordinates are scaled to pixels in
  `SegIEPolys.overlay_mask`.
- GPU device selection: first-device fallback, and a `NameError` fix in
  `nn.getCurrentDeviceConfig`.
- OpenCV/OpenMP/MKL/numexpr thread-limit defaults for thread-constrained
  hosts.
- Output directories are created before writing, and image-write failures
  are logged instead of being swallowed.
- Removed from the tree: upstream `doc/` (links now point to upstream) and
  the tracked `facelib/*.npy` weights (distributed separately, see below).
- Retained unchanged: upstream `flaskr/` (the `train --flask-preview`
  browser preview).
- Added `recaster_bridge/` (the out-of-process JSON-lines bridge, active
  only with `RECASTER_BRIDGE=1`), hooked in `core/interact/interact.py`;
  `extract --face-type` accepts `midfull_face`.
- Added: `envs/` (per-platform runtime environment specs), `ci/` and
  `.github/workflows/ci.yml`, `WEIGHTS.sha256`, and this file and
  CHANGES.md.

## Pretrained weights

`facelib/2DFAN.npy`, `facelib/3DFAN.npy`, `facelib/S3FD.npy` and
`facelib/FaceEnhancer.npy` are byte-for-byte identical to upstream
DeepFaceLab-MVE at `6e36689` (see `WEIGHTS.sha256`). recaster-dfl does not
modify them. Upstream states no separate license for them, and they are
distributed on the same terms as this repository. They are no longer
tracked at the tip of this repository. Release builds ship them as a
separate checksummed artifact. recaster-dfl makes no representation about
the terms of the datasets these models were originally trained on. No
representation is made that the model weights, or output produced with
them, are suitable for commercial use.

## Names

"DeepFaceLab" and "DeepFaceLab-MVE" are the names of the upstream projects.
They are used here only to describe where this code comes from.
recaster-dfl is not affiliated with, endorsed by, or supported by iperov,
the DeepFaceLab project, or the MachineEditor/DeepFaceLab-MVE maintainers.
Please report problems with recaster-dfl to this repository, not to
upstream.
