# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Guards on the committed env locks (envs/<platform>/conda-lock.yml + requirements.txt).

Parsed with regexes (stdlib only). Asserts the design 4.2 pins, the
forbidden list, that every PyPI requirement is exact and hash-pinned, that
conda and pip never install the same package, and that each lock is in sync
with its spec.
"""

import hashlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ci" / "release"))

import release_tools as rt  # noqa: E402

PLATFORMS = ("linux-x86_64-cuda12", "macos-arm64-metal")
FORBIDDEN = ("onnxruntime", "onnxruntime-gpu", "insightface", "opencv-python-headless", "torch")
PIP_PINS = {
    "linux-x86_64-cuda12": {"tensorflow": "2.16.2", "opencv-python": "4.10.0.84", "nvidia-cudnn-cu12": "8.9.7.29",
                            "onnx": "1.18.0", "ml-dtypes": "0.3.2"},
    "macos-arm64-metal": {"tensorflow": "2.16.2", "tensorflow-metal": "1.2.0", "opencv-python": "4.10.0.84",
                          "onnx": "1.18.0", "ml-dtypes": "0.3.2"},
}
CONDA_PINS = {"numpy": "1.26.4"}
CONDA_SUBDIR = {"linux-x86_64-cuda12": "linux-64", "macos-arm64-metal": "osx-arm64"}


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def conda_packages(path: Path) -> dict:
    """name -> {version, manager, platform, url, sha256, python_pkg} from a conda-lock.yml."""
    text = path.read_text(encoding="utf-8")
    body = text.split("\npackage:\n", 1)[1]
    out = {}
    for block in re.split(r"\n(?=- name: )", body):
        name = re.search(r"^- name: (\S+)", block, re.M).group(1)
        out[name] = {
            "version": re.search(r"^  version: '?([^'\n]+)'?$", block, re.M).group(1),
            "manager": re.search(r"^  manager: (\S+)", block, re.M).group(1),
            "platform": re.search(r"^  platform: (\S+)", block, re.M).group(1),
            "url": re.search(r"^  url: (\S+)", block, re.M).group(1),
            "sha256": re.search(r"^    sha256: ([0-9a-f]{64})$", block, re.M),
            "python_pkg": bool(re.search(r"^    (python|python_abi): ", block, re.M)),
        }
    return out


def pip_requirements(path: Path) -> dict:
    """name -> (version, [hashes]) from a uv --generate-hashes requirements.txt."""
    out = {}
    text = path.read_text(encoding="utf-8")
    for entry in re.split(r"\n(?=[A-Za-z0-9])", text):
        if not entry or entry.startswith("#"):
            continue
        head = entry.split()[0]
        match = re.fullmatch(r"([A-Za-z0-9._-]+)(\[[^\]]+\])?==([^\s\\]+)", head)
        assert match, f"{path}: not an exact pin: {head!r}"
        out[norm(match.group(1))] = (match.group(3), re.findall(r"--hash=sha256:([0-9a-f]{64})", entry))
    return out


def requirements_in(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            match = re.fullmatch(r"([A-Za-z0-9._-]+)(\[[^\]]+\])?==(\S+)", line)
            assert match, f"{path}: not an exact pin: {line!r}"
            out[norm(match.group(1))] = match.group(3)
    return out


class EnvLockTests(unittest.TestCase):
    def test_locks_are_committed(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                files = rt.env_spec_files(ROOT, platform)
                self.assertEqual([f.name for f in files], ["conda-lock.yml", "requirements.txt"])

    def test_conda_lock_is_single_platform_hashed_and_pinned(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                pkgs = conda_packages(ROOT / "envs" / platform / "conda-lock.yml")
                self.assertTrue(pkgs)
                for name, pkg in pkgs.items():
                    self.assertEqual(pkg["manager"], "conda", name)       # PyPI lives in requirements.txt
                    self.assertEqual(pkg["platform"], CONDA_SUBDIR[platform], name)
                    self.assertTrue(pkg["url"].startswith("https://conda.anaconda.org/conda-forge/"), name)
                    self.assertIsNotNone(pkg["sha256"], name)
                for name, version in CONDA_PINS.items():
                    self.assertEqual(pkgs[name]["version"], version)
                self.assertTrue(pkgs["python"]["version"].startswith("3.10."))

    def test_pip_lock_is_exact_hashed_and_pinned(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                reqs = pip_requirements(ROOT / "envs" / platform / "requirements.txt")
                for name, (_version, hashes) in reqs.items():
                    self.assertTrue(hashes, f"{name} has no --hash")
                for name, version in PIP_PINS[platform].items():
                    self.assertEqual(reqs[name][0], version, name)

    def test_forbidden_packages_absent(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                names = set(map(norm, conda_packages(ROOT / "envs" / platform / "conda-lock.yml")))
                names |= set(pip_requirements(ROOT / "envs" / platform / "requirements.txt"))
                for name in FORBIDDEN:
                    self.assertNotIn(name, names)

    def test_conda_and_pip_never_overlap(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                conda_py = {norm(n) for n, p in conda_packages(ROOT / "envs" / platform / "conda-lock.yml").items()
                            if p["python_pkg"]}
                pip = set(pip_requirements(ROOT / "envs" / platform / "requirements.txt"))
                self.assertEqual(conda_py & pip, set())

    def test_pip_lock_matches_its_spec(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                spec = requirements_in(ROOT / "envs" / platform / "requirements.in")
                reqs = pip_requirements(ROOT / "envs" / platform / "requirements.txt")
                for name, version in spec.items():
                    self.assertEqual(reqs.get(name, (None,))[0], version, name)
                for name in ("ffmpeg-python", "tf2onnx", "tensorboardx"):
                    self.assertIn(name, spec)

    def test_env_id_covers_both_locks(self):
        for platform in PLATFORMS:
            with self.subTest(platform):
                files = rt.env_spec_files(ROOT, platform)
                lines = "".join(f"{rt.sha256_file(f)}  {f.name}\n" for f in files)
                expected = f"{platform}-{hashlib.sha256(lines.encode()).hexdigest()[:12]}"
                self.assertEqual(rt.env_id(ROOT, platform), expected)


if __name__ == "__main__":
    unittest.main()
