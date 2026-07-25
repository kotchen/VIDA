from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


class UnsafeMediaPath(ValueError):
    pass


class PathReferenceRepository(Protocol):
    def committed_paths(self) -> Iterable[str | None]: ...


@dataclass
class OpenMediaFile:
    file: BinaryIO
    size: int
    mtime: float
    mtime_ns: int

    def close(self) -> None:
        self.file.close()


class MediaService:
    def __init__(self, data_dir: Path, repositories: Iterable[PathReferenceRepository]):
        self._data_dir = Path(data_dir).resolve()
        self._repositories = tuple(repositories)

    def commit_file(self, staged: Path, final: Path) -> None:
        if self._has_symlink_component(staged) or self._has_symlink_component(final):
            raise UnsafeMediaPath("media path contains a symlink")
        source = self._bounded(staged)
        destination = self._bounded(final)
        source_parts = source.relative_to(self._data_dir).parts
        destination_parts = destination.relative_to(self._data_dir).parts
        if (
            len(source_parts) < 4 or len(destination_parts) < 4
            or source_parts[0] != "episodes" or destination_parts[0] != "episodes"
            or source_parts[1] != destination_parts[1]
            or source_parts[2] != "attempts" or len(source_parts) < 5
            or destination_parts[2] not in {"artifacts", "poster"}
        ):
            raise UnsafeMediaPath("media commit crosses episode or artifact boundary")
        if not source.is_file() or source.is_symlink():
            raise UnsafeMediaPath("staged media must be a regular file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)

    def remove_episode_tree(self, episode_id: str) -> None:
        relative = Path(episode_id)
        if (
            relative.is_absolute()
            or len(relative.parts) != 1
            or relative.name in {"", ".", ".."}
            or ":" in relative.name
            or "\x00" in relative.name
        ):
            raise UnsafeMediaPath("invalid episode id")
        episodes_root = self._data_dir / "episodes"
        candidate = episodes_root / relative.name
        if candidate.is_symlink() or self._has_symlink_component(candidate):
            raise UnsafeMediaPath("episode tree contains a symlink")
        if candidate.exists():
            self._remove_tree(candidate)

    def open_owned_file(
        self, episode_id: str, stored_path: str | None, kind: str
    ) -> OpenMediaFile:
        if not stored_path or kind not in {"media", "poster"}:
            raise UnsafeMediaPath("media path is unavailable")
        relative = Path(stored_path)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} or ":" in part or "\x00" in part
            for part in relative.parts
        ):
            raise UnsafeMediaPath("repository path must be relative")
        allowed_folder = "artifacts" if kind == "media" else "poster"
        if (
            len(relative.parts) < 4
            or relative.parts[0] != "episodes"
            or relative.parts[1] != episode_id
            or relative.parts[2] != allowed_folder
        ):
            raise UnsafeMediaPath("repository path crosses ownership boundary")
        descriptor = (
            _open_windows_relative(self._data_dir, relative.parts)
            if os.name == "nt"
            else _open_posix_relative(self._data_dir, relative.parts)
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise UnsafeMediaPath("media target must be a regular file")
            return OpenMediaFile(
                os.fdopen(descriptor, "rb", closefd=True),
                opened.st_size,
                opened.st_mtime,
                opened.st_mtime_ns,
            )
        except UnsafeMediaPath:
            os.close(descriptor)
            raise
        except OSError:
            os.close(descriptor)
            raise UnsafeMediaPath("media file changed during open") from None
        except BaseException:
            os.close(descriptor)
            raise

    def reconcile_orphans(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        episodes = self._data_dir / "episodes"
        if episodes.is_symlink():
            episodes.unlink(missing_ok=True)
            return
        if not episodes.is_dir():
            return
        for episode in tuple(episodes.iterdir()):
            if episode.is_symlink() or not episode.is_dir():
                if episode.is_symlink():
                    episode.unlink(missing_ok=True)
                continue
            for name in ("attempts", "artifacts", "poster"):
                root = episode / name
                if root.is_symlink():
                    root.unlink(missing_ok=True)
        references = self._references()
        for part in episodes.rglob("*.part"):
            self._unlink_local(part)
        for attempts in episodes.glob("*/attempts"):
            if not attempts.is_dir() or attempts.is_symlink():
                continue
            for attempt in tuple(attempts.iterdir()):
                if attempt.is_symlink():
                    attempt.unlink(missing_ok=True)
                    continue
                if self._has_symlink_component(attempt):
                    continue
                try:
                    resolved_attempt = self._bounded(attempt)
                except UnsafeMediaPath:
                    continue
                if any(
                    reference == resolved_attempt or resolved_attempt in reference.parents
                    for reference in references
                ):
                    continue
                if attempt.is_dir():
                    self._remove_tree(attempt)
                else:
                    attempt.unlink(missing_ok=True)
            _rmdir_empty(attempts)
        for folder_name in ("artifacts", "poster"):
            for folder in episodes.glob(f"*/{folder_name}"):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                for candidate in tuple(folder.rglob("*")):
                    if candidate.is_dir() and not candidate.is_symlink():
                        continue
                    if candidate.is_symlink() or candidate.resolve() not in references:
                        self._unlink_local(candidate)
                for directory in sorted(
                    (path for path in folder.rglob("*") if path.is_dir()),
                    key=lambda path: len(path.parts), reverse=True,
                ):
                    _rmdir_empty(directory)
                _rmdir_empty(folder)

    def _references(self) -> set[Path]:
        references: set[Path] = set()
        for repository in self._repositories:
            for raw in repository.committed_paths():
                if not raw:
                    continue
                path = Path(raw)
                if path.is_absolute():
                    continue
                candidate = self._data_dir / path
                if self._has_symlink_component(candidate):
                    continue
                try:
                    references.add(self._bounded(candidate))
                except UnsafeMediaPath:
                    continue
        return references

    def _bounded(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        if resolved == self._data_dir or self._data_dir not in resolved.parents:
            raise UnsafeMediaPath("media path escapes data/v2")
        return resolved

    def _has_symlink_component(self, path: Path) -> bool:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._data_dir / candidate
        try:
            relative = candidate.relative_to(self._data_dir)
        except ValueError:
            return False
        current = self._data_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _unlink_local(self, path: Path) -> None:
        if path.is_symlink():
            path.unlink(missing_ok=True)
            return
        try:
            bounded = self._bounded(path)
        except UnsafeMediaPath:
            return
        if bounded.is_file():
            bounded.unlink(missing_ok=True)

    def _remove_tree(self, path: Path) -> None:
        candidate = Path(path)
        if candidate.is_symlink():
            candidate.unlink(missing_ok=True)
            return
        if self._has_symlink_component(candidate):
            return
        try:
            bounded = self._bounded(candidate)
        except UnsafeMediaPath:
            return
        # Pass the checked lexical path to rmtree.  Passing the resolved path
        # would turn a last-moment link substitution into an external delete.
        if bounded != candidate.absolute() or not candidate.is_dir():
            return
        shutil.rmtree(candidate)


def _rmdir_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _open_posix_relative(root: Path, parts: tuple[str, ...]) -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise UnsafeMediaPath("platform lacks safe descriptor-relative open flags")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = os.O_NOFOLLOW
    opened_directories: list[int] = []
    try:
        current = os.open(root, directory_flags | no_follow)
        opened_directories.append(current)
        for component in parts[:-1]:
            current = os.open(
                component,
                directory_flags | no_follow,
                dir_fd=current,
            )
            opened_directories.append(current)
        return os.open(
            parts[-1],
            os.O_RDONLY | no_follow | getattr(os, "O_BINARY", 0),
            dir_fd=current,
        )
    except (OSError, ValueError, NotImplementedError):
        raise UnsafeMediaPath("media file cannot be opened safely") from None
    finally:
        for descriptor in reversed(opened_directories):
            os.close(descriptor)


def _open_windows_relative(root: Path, parts: tuple[str, ...]) -> int:
    # NtCreateFile's RootDirectory makes every component lookup relative to an
    # already-open directory handle. Parent replacements cannot redirect later
    # lookups, and reparse points are opened (then rejected) rather than followed.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    invalid_handle = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", ctypes.c_void_p),
            ("SecurityQualityOfService", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    nt_create = ntdll.NtCreateFile
    nt_create.argtypes = (
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock),
        ctypes.POINTER(ctypes.c_longlong), wintypes.ULONG, wintypes.ULONG,
        wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
    )
    nt_create.restype = wintypes.LONG
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    get_info.restype = wintypes.BOOL

    FILE_SHARE_READ = 0x1
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    FILE_READ_ATTRIBUTES = 0x80
    SYNCHRONIZE = 0x100000
    FILE_GENERIC_READ = 0x120089
    FILE_OPEN = 1
    FILE_DIRECTORY_FILE = 0x1
    FILE_SEQUENTIAL_ONLY = 0x4
    FILE_SYNCHRONOUS_IO_NONALERT = 0x20
    FILE_NON_DIRECTORY_FILE = 0x40
    OBJ_CASE_INSENSITIVE = 0x40
    OBJ_DONT_REPARSE = 0x1000
    FILE_ATTRIBUTE_TAG_INFO = 9

    root_handle = create_file(
        str(root), FILE_READ_ATTRIBUTES | SYNCHRONIZE, FILE_SHARE_READ, None,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if root_handle == invalid_handle:
        raise UnsafeMediaPath("media root cannot be opened safely")
    handles = [root_handle]

    def attributes(handle) -> int:
        info = FileAttributeTagInfo()
        if not get_info(handle, FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx failed")
        return info.FileAttributes

    def open_relative(parent, component: str, directory: bool):
        buffer = ctypes.create_unicode_buffer(component)
        name = UnicodeString(
            len(component.encode("utf-16-le")),
            len(component.encode("utf-16-le")) + 2,
            ctypes.cast(buffer, wintypes.LPWSTR),
        )
        attributes_value = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(name),
            OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE, None, None,
        )
        result = wintypes.HANDLE()
        io_status = IoStatusBlock()
        options = FILE_SYNCHRONOUS_IO_NONALERT | FILE_FLAG_OPEN_REPARSE_POINT
        options |= FILE_DIRECTORY_FILE if directory else (FILE_NON_DIRECTORY_FILE | FILE_SEQUENTIAL_ONLY)
        status = nt_create(
            ctypes.byref(result),
            (FILE_READ_ATTRIBUTES | SYNCHRONIZE) if directory else FILE_GENERIC_READ,
            ctypes.byref(attributes_value), ctypes.byref(io_status), None, 0,
            FILE_SHARE_READ, FILE_OPEN, options, None, 0,
        )
        if status != 0:
            raise OSError(f"NtCreateFile failed: 0x{status & 0xffffffff:08x}")
        return result

    try:
        if attributes(root_handle) & FILE_ATTRIBUTE_REPARSE_POINT:
            raise UnsafeMediaPath("media root is a reparse point")
        current = root_handle
        for component in parts[:-1]:
            current = open_relative(current, component, True)
            handles.append(current)
            if attributes(current) & FILE_ATTRIBUTE_REPARSE_POINT:
                raise UnsafeMediaPath("media path contains a reparse point")
        final_handle = open_relative(current, parts[-1], False)
        handles.append(final_handle)
        if attributes(final_handle) & FILE_ATTRIBUTE_REPARSE_POINT:
            raise UnsafeMediaPath("media file is a reparse point")
        try:
            descriptor = msvcrt.open_osfhandle(
                final_handle.value, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
            handles.pop()
        except BaseException:
            raise
        return descriptor
    except UnsafeMediaPath:
        raise
    except (OSError, ValueError):
        raise UnsafeMediaPath("media file cannot be opened safely") from None
    finally:
        for handle in reversed(handles):
            close_handle(handle)
