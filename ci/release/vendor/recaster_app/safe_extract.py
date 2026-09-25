"""
Safe extraction of downloaded runtime archives (RF-501 slice E).

Every member is checked before anything is written:

- no absolute paths, drive letters, backslashes or ``..`` components
- no device, FIFO or other special members
- symlinks must be relative and stay inside the destination; hard links
  must point at a regular file already extracted inside it
- a member is never written through a symlinked directory that leads
  outside the destination
- setuid/setgid/sticky and group/other write bits are dropped
- the regular-file bytes written may not exceed ``max_bytes`` (a
  decompression bomb stops at the cap instead of filling the disk)

Zip archives (the Windows env) get the same name checks and may not
contain symlinks at all. After extraction every symlink is resolved once
more and must land inside the destination.

A corrupt or truncated archive (``tarfile``/``zipfile`` errors, ``EOFError``,
``zlib.error``) raises ``UnsafeArchiveError``; a member that can't be
written (name too long, disk full, a file where a directory should go)
raises its subclass ``ArchiveWriteError``.

Members are written by hand instead of ``TarFile.extract`` so the checks
are the same on every Python version (``filter="data"`` needs 3.8.17+/3.12).

Pure: no Qt.
"""

import os
import re
import shutil
import stat
import tarfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_COPY_CHUNK = 1 << 20

ProgressFn = Callable[[int, int], None]   # (bytes of the archive read, archive size)
CheckFn = Callable[[], None]              # raises to abort (e.g. cancel)


class UnsafeArchiveError(ValueError):
    """An archive member would escape the destination or is not allowed."""


class ArchiveWriteError(UnsafeArchiveError):
    """A member couldn't be written (``OSError``: name too long, disk full, ...)."""


def member_path(name: str) -> PurePosixPath:
    """The member's path relative to the destination. Raises ``UnsafeArchiveError``."""
    if not isinstance(name, str) or not name or "\x00" in name:
        raise UnsafeArchiveError(f"invalid member name {name!r}")
    if name.startswith("/") or _DRIVE_RE.match(name):
        raise UnsafeArchiveError(f"absolute path in archive: {name}")
    if "\\" in name:
        raise UnsafeArchiveError(f"backslash in archive path: {name}")
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if ".." in parts:
        raise UnsafeArchiveError(f"'..' in archive path: {name}")
    return PurePosixPath(*parts) if parts else PurePosixPath()


def _inside(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # different drives (Windows)
        return False


def _safe_mode(mode: int, *, executable_default: bool = False) -> int:
    mode &= 0o755
    if not mode:
        mode = 0o755 if executable_default else 0o644
    return mode | 0o600


class _Extractor:
    def __init__(self, dest: Path, max_bytes: Optional[int] = None):
        dest.mkdir(parents=True, exist_ok=True)
        self.root = os.path.realpath(str(dest))
        self.count = 0
        self.max_bytes = max_bytes
        self.written = 0

    def target(self, rel: PurePosixPath) -> str:
        return os.path.join(self.root, *rel.parts)

    def mkdirs(self, path: str, name: str) -> None:
        # realpath resolves the existing prefix, so a symlinked component
        # leading outside is caught before makedirs follows it
        if not _inside(self.root, os.path.realpath(path)):
            raise UnsafeArchiveError(f"archive path escapes the destination through a link: {name}")
        if os.path.lexists(path) and not os.path.isdir(path):
            raise UnsafeArchiveError(f"archive member replaces a file with a directory: {name}")
        os.makedirs(path, mode=0o755, exist_ok=True)

    def prepare_file(self, rel: PurePosixPath, name: str) -> str:
        target = self.target(rel)
        self.mkdirs(os.path.dirname(target), name)
        if os.path.lexists(target):
            if os.path.isdir(target) and not os.path.islink(target):
                raise UnsafeArchiveError(f"archive member replaces a directory: {name}")
            os.unlink(target)
        return target

    def add_bytes(self, n: int, name: str) -> None:
        self.written += n
        if self.max_bytes is not None and self.written > self.max_bytes:
            raise UnsafeArchiveError(
                f"archive unpacks to more than the expected {self.max_bytes} bytes (at {name})")

    def write(self, target: str, src, mode: int, name: str) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "wb") as out:
            while True:
                block = src.read(_COPY_CHUNK)
                if not block:
                    break
                self.add_bytes(len(block), name)
                out.write(block)
        if os.name != "nt":
            os.chmod(target, mode)
        self.count += 1

    def symlink(self, rel: PurePosixPath, link: str, name: str) -> None:
        if not link or link.startswith("/") or _DRIVE_RE.match(link) or "\\" in link:
            raise UnsafeArchiveError(f"symlink with an absolute or invalid target: {name} -> {link}")
        target = self.prepare_file(rel, name)
        resolved = os.path.normpath(os.path.join(os.path.realpath(os.path.dirname(target)), link))
        if not _inside(self.root, resolved):
            raise UnsafeArchiveError(f"symlink escapes the destination: {name} -> {link}")
        os.symlink(link, target)
        self.count += 1

    def hardlink(self, rel: PurePosixPath, link: str, name: str) -> None:
        source = self.target(member_path(link))
        real = os.path.realpath(source)
        if not _inside(self.root, real) or os.path.islink(source) or not os.path.isfile(source):
            raise UnsafeArchiveError(f"hard link to a missing or outside file: {name} -> {link}")
        target = self.prepare_file(rel, name)
        try:
            os.link(real, target)
        except OSError:
            self.add_bytes(os.path.getsize(real), name)  # a copy takes space; a link doesn't
            shutil.copy2(real, target)
        self.count += 1

    def verify_links(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            for entry in dirnames + filenames:
                path = os.path.join(dirpath, entry)
                if os.path.islink(path) and not _inside(self.root, os.path.realpath(path)):
                    raise UnsafeArchiveError(
                        f"symlink escapes the destination: {os.path.relpath(path, self.root)}")


def _extract_tar(archive: Path, fmt: str, ex: _Extractor, check: Optional[CheckFn],
                 progress: Optional[ProgressFn]) -> None:
    total = archive.stat().st_size
    with open(archive, "rb") as raw, tarfile.open(fileobj=raw, mode="r:gz" if fmt == "tar.gz" else "r:") as tar:
        for member in tar:
            if check:
                check()
            rel = member_path(member.name)
            if progress:
                progress(raw.tell(), total)
            if not rel.parts:
                continue
            if member.isdir():
                ex.mkdirs(ex.target(rel), member.name)
            elif member.isreg():
                target = ex.prepare_file(rel, member.name)
                src = tar.extractfile(member)
                with src:
                    ex.write(target, src, _safe_mode(member.mode), member.name)
            elif member.issym():
                ex.symlink(rel, member.linkname, member.name)
            elif member.islnk():
                ex.hardlink(rel, member.linkname, member.name)
            else:
                raise UnsafeArchiveError(f"device, FIFO or special file in archive: {member.name}")
        if progress:
            progress(total, total)


def _extract_zip(archive: Path, ex: _Extractor, check: Optional[CheckFn],
                 progress: Optional[ProgressFn]) -> None:
    total = archive.stat().st_size
    with zipfile.ZipFile(archive) as zf:
        done = 0
        for info in zf.infolist():
            if check:
                check()
            rel = member_path(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise UnsafeArchiveError(f"symlink or special file in zip archive: {info.filename}")
            done += info.compress_size
            if progress:
                progress(min(done, total), total)
            if not rel.parts:
                continue
            if info.is_dir() or kind == stat.S_IFDIR:
                ex.mkdirs(ex.target(rel), info.filename)
                continue
            target = ex.prepare_file(rel, info.filename)
            with zf.open(info) as src:
                ex.write(target, src, _safe_mode(stat.S_IMODE(mode)), info.filename)
        if progress:
            progress(total, total)


def extract_archive(archive: Path, dest: Path, fmt: str, *, check: Optional[CheckFn] = None,
                    progress: Optional[ProgressFn] = None, max_bytes: Optional[int] = None) -> int:
    """Extract ``archive`` (``tar.gz`` | ``tar`` | ``zip``) into ``dest``; returns the file count.

    Raises ``UnsafeArchiveError`` on the first unsafe member, on a corrupt
    archive and (``ArchiveWriteError``) on a member that can't be written;
    ``dest`` is then partially written (callers extract into a staging dir
    and delete it). ``check`` is called before each member and may raise to
    abort. ``max_bytes`` caps the regular-file bytes written.
    """
    if fmt not in ("tar.gz", "tar", "zip"):
        raise UnsafeArchiveError(f"unsupported archive format {fmt!r}")
    try:
        ex = _Extractor(Path(dest), max_bytes)
        if fmt == "zip":
            _extract_zip(Path(archive), ex, check, progress)
        else:
            _extract_tar(Path(archive), fmt, ex, check, progress)
        ex.verify_links()
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, zlib.error) as e:
        raise UnsafeArchiveError(f"corrupt archive: {e or type(e).__name__}") from e
    except OSError as e:
        name = os.path.basename(e.filename) if isinstance(e.filename, str) else ""
        where = f" ({name[:60]}{'...' if len(name) > 60 else ''})" if name else ""
        raise ArchiveWriteError(f"could not extract the archive{where}: {e.strerror or e}") from e
    return ex.count
