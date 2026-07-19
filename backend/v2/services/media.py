from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol


class UnsafeMediaPath(ValueError):
    pass


class PathReferenceRepository(Protocol):
    def committed_paths(self) -> Iterable[str | None]: ...


class MediaService:
    def __init__(self, data_dir: Path, repositories: Iterable[PathReferenceRepository]):
        self._data_dir = Path(data_dir).resolve()
        self._repositories = tuple(repositories)

    def commit_file(self, staged: Path, final: Path) -> None:
        source = self._bounded(staged)
        destination = self._bounded(final)
        source_parts = source.relative_to(self._data_dir).parts
        destination_parts = destination.relative_to(self._data_dir).parts
        if (
            len(source_parts) < 4 or len(destination_parts) < 4
            or source_parts[0] != "episodes" or destination_parts[0] != "episodes"
            or source_parts[1] != destination_parts[1]
            or destination_parts[2] not in {"artifacts", "poster"}
        ):
            raise UnsafeMediaPath("media commit crosses episode or artifact boundary")
        if not source.is_file() or source.is_symlink():
            raise UnsafeMediaPath("staged media must be a regular file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)

    def reconcile_orphans(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        references = self._references()
        episodes = self._data_dir / "episodes"
        if not episodes.is_dir():
            return
        for part in episodes.rglob("*.part"):
            self._unlink_local(part)
        for attempts in episodes.glob("*/attempts"):
            if not attempts.is_dir() or attempts.is_symlink():
                continue
            for attempt in tuple(attempts.iterdir()):
                if any(reference == attempt.resolve() or attempt.resolve() in reference.parents for reference in references):
                    continue
                if attempt.is_dir() and not attempt.is_symlink():
                    shutil.rmtree(attempt)
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
                try:
                    references.add(self._bounded(self._data_dir / path))
                except UnsafeMediaPath:
                    continue
        return references

    def _bounded(self, path: Path) -> Path:
        resolved = Path(path).resolve()
        if resolved == self._data_dir or self._data_dir not in resolved.parents:
            raise UnsafeMediaPath("media path escapes data/v2")
        return resolved

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


def _rmdir_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass
