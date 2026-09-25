<!-- Modified by PixelMorph LLC, 2026-09-25 (recaster-dfl): added the recaster-dfl section; the upstream README follows unchanged except for image links. -->
# recaster-dfl

recaster-dfl is a GPL-3.0 fork of
[MachineEditor/DeepFaceLab-MVE](https://github.com/MachineEditor/DeepFaceLab-MVE)
at commit `6e36689`. MVE is itself a fork of
[iperov/DeepFaceLab](https://github.com/iperov/DeepFaceLab). recaster-dfl is
maintained by PixelMorph LLC. It is not affiliated with or endorsed by the upstream projects (see
[NOTICE.md](NOTICE.md)).

## Relationship to upstream

- Upstream `master` has had no commits since August 2024. recaster-dfl
  diverges from it on purpose and does not track it.
- The full upstream history is kept. Everything after `6e36689` on `main`
  is a recaster-dfl change, listed in [CHANGES.md](CHANGES.md). The changes
  so far are small compatibility and bug fixes: NumPy 1.24+, XSeg mask
  rasterization, GPU device selection, thread limits, and output-directory
  handling.
- The command-line interface (`python main.py <op> ...`) is upstream's,
  unchanged.

## Usage

recaster-dfl runs as a standalone program: `python main.py <command> …`.
See the upstream documentation for commands.

Each tagged release is expected to produce three artifacts:
- a weights-free source tarball
- a separate, content-addressed weights artifact
- per-platform environment bundles built from `envs/`

## Running it directly

1. Create an environment from the spec for your platform:
   `conda env create -f envs/<platform>/environment.yml`, where the
   platforms are `linux-x86_64-cuda12`, `macos-arm64-metal` and
   `windows-x86_64-cuda11`.
2. Put the four weight files in `facelib/`. They are byte-identical to
   upstream at `6e36689`, so either extract the weights release artifact or
   restore them from git history, e.g.
   `git show 6e36689:facelib/S3FD.npy > facelib/S3FD.npy`.
3. Verify the weights with `sha256sum -c WEIGHTS.sha256`, or on macOS
   `shasum -a 256 -c WEIGHTS.sha256`.
4. Run `python main.py --help`.

`python ci/import_smoke.py` checks that every entry-point module imports in
the active environment.

## License

GPL-3.0, the same as upstream. See [LICENSE](LICENSE), which is unchanged,
and [NOTICE.md](NOTICE.md) for attribution and modification notices.

---

## Upstream README (DeepFaceLab-MVE)

<table align="center" border="0">

<tr><td colspan=2 align="center">

# DeepFaceLab  

<a href="https://arxiv.org/abs/2005.05535">

<img src="https://static.arxiv.org/static/browse/0.3.0/images/icons/favicon.ico" width=14></img>
https://arxiv.org/abs/2005.05535</a>


### the leading software for creating deepfakes

<img src="https://raw.githubusercontent.com/MachineEditor/DeepFaceLab-MVE/6e366896e0119e26600c3fcebc914e6fc54fcfee/doc/DFL_welcome.png" align="center">

</td></tr>
<tr><td colspan=2 align="center">

<p align="center">

![](https://raw.githubusercontent.com/MachineEditor/DeepFaceLab-MVE/6e366896e0119e26600c3fcebc914e6fc54fcfee/doc/logo_tensorflow.png)
![](https://raw.githubusercontent.com/MachineEditor/DeepFaceLab-MVE/6e366896e0119e26600c3fcebc914e6fc54fcfee/doc/logo_cuda.png)
![](https://raw.githubusercontent.com/MachineEditor/DeepFaceLab-MVE/6e366896e0119e26600c3fcebc914e6fc54fcfee/doc/logo_directx.png)

</p>

<tr><td colspan=2 align="center">
  <tr><td colspan=2 align="center">

# CHANGELOG 
### [View most recent changes](CHANGELOG.md)

<tr><td colspan=2 align="center">
  <tr><td colspan=2 align="center">

## Mini tutorial

<a href="https://www.youtube.com/watch?v=kOIMXt8KK8M">

<img src="https://raw.githubusercontent.com/MachineEditor/DeepFaceLab-MVE/6e366896e0119e26600c3fcebc914e6fc54fcfee/doc/mini_tutorial.jpg" align="center">

</a>

</table>
