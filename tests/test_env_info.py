# Part of recaster-dfl (GPL-3.0). Copyright (C) 2026 PixelMorph LLC. See CHANGES.md.
"""Tests for ci/release/env_info.py's AGPL guard (pip metadata and conda-meta records)."""

import json
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ci" / "release"))

import env_info  # noqa: E402

GPL3_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007
...
13. Use with the GNU Affero General Public License.
"""


def _dist(root: Path, name: str, headers: dict, body: str = "") -> None:
    info = root / f"{name}-1.0.dist-info"
    info.mkdir()
    lines = ["Metadata-Version: 2.4", f"Name: {name}", "Version: 1.0"]
    for key, values in headers.items():
        for value in values if isinstance(values, list) else [values]:
            lines.append(f"{key}: {value}")
    (info / "METADATA").write_text("\n".join(lines) + "\n\n" + body, encoding="utf-8")


class PipLicenseTests(unittest.TestCase):
    def setUp(self):
        self.site = Path(tempfile.mkdtemp())

    def _problems(self):
        return env_info.pip_license_problems(metadata.distributions(path=[str(self.site)]))

    def test_agpl_in_each_field_is_found(self):
        _dist(self.site, "by-license", {"License": "AGPL-3.0-or-later"})
        _dist(self.site, "by-expression", {"License-Expression": "MIT OR AGPL-3.0-only"})
        _dist(self.site, "by-classifier", {"Classifier": [
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: GNU Affero General Public License v3"]})
        _dist(self.site, "by-name", {"License": "GNU Affero General Public License v3.0"})
        found = sorted(p.split(": ")[1].split()[0] for p in self._problems())
        self.assertEqual(found, ["by-classifier", "by-expression", "by-license", "by-name"])

    def test_gpl_lgpl_and_permissive_pass(self):
        _dist(self.site, "gpl-fulltext", {"License": GPL3_TEXT.replace("\n", "\n        ")})
        _dist(self.site, "lgpl", {"License": "LGPL-3.0", "Classifier":
                                  "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"})
        _dist(self.site, "gpl", {"License-Expression": "GPL-3.0-only"})
        _dist(self.site, "mit", {"License": "MIT", "Classifier": "License :: OSI Approved :: MIT License"})
        _dist(self.site, "none", {})
        # A description that mentions AGPL is not the package's license
        _dist(self.site, "mentions", {"License": "BSD-3-Clause"}, body="Unlike AGPL tools, this is BSD.")
        self.assertEqual(self._problems(), [])


class CondaLicenseTests(unittest.TestCase):
    def setUp(self):
        self.prefix = Path(tempfile.mkdtemp())
        (self.prefix / "conda-meta").mkdir()

    def _record(self, name, license_):
        data = {"name": name, "version": "1.0", "build": "0"}
        if license_ is not None:
            data["license"] = license_
        (self.prefix / "conda-meta" / f"{name}-1.0-0.json").write_text(json.dumps(data))

    def test_agpl_record_is_found(self):
        self._record("ok", "BSD-3-Clause")
        self._record("gpl", "GPL-3.0-or-later")
        self._record("nolicense", None)
        self._record("bad", "AGPL-3.0-only")
        self._record("bad2", "GNU Affero GPL v3")
        problems = env_info.conda_license_problems(self.prefix)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(all("AGPL-licensed package (conda)" in p for p in problems))

    def test_unreadable_record_is_a_problem(self):
        (self.prefix / "conda-meta" / "broken.json").write_text("{not json")
        self.assertEqual(len(env_info.conda_license_problems(self.prefix)), 1)

    def test_prefix_without_conda_meta(self):
        self.assertEqual(env_info.conda_license_problems(Path(tempfile.mkdtemp())), [])


if __name__ == "__main__":
    unittest.main()
