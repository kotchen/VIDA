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

    def open_owned_file(
        self, episode_id: str, stored_path: str | None, kind: str
    ) -> OpenMediaFile:
        if not stored_path or kind not in {"media", "poster"}:
            raise UnsafeMediaPath("media path is unavailable")
        relative = Path(stored_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise UnsafeMediaPath("repository path must be relative")
        allowed_folder = "artifacts" if kind == "media" else "poster"
        if (
            len(relative.parts) < 4
            or relative.parts[0] != "episodes"
            or relative.parts[1] != episode_id
            or relative.parts[2] != allowed_folder
        ):
            raise UnsafeMediaPath("repository path crosses ownership boundary")
        candidate = self._data_dir.joinpath(*relative.parts)
        if self._has_symlink_component(candidate):
            raise UnsafeMediaPath("media path contains a symlink")
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise UnsafeMediaPath("media file does not exist") from None
        if self._data_dir not in resolved.parents:
            raise UnsafeMediaPath("media path escapes data/v2")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(candidate, flags)
        except OSError:
            raise UnsafeMediaPath("media file cannot be opened") from None
        try:
            opened = os.fstat(descriptor)
            lexical = os.stat(candidate, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(lexical.st_mode)
                or (opened.st_dev, opened.st_ino) != (lexical.st_dev, lexical.st_ino)
                or candidate.resolve(strict=True) != resolved
            ):
                raise UnsafeMediaPath("media file changed during open")
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
