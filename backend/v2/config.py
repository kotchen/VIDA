from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class V2Settings:
    data_dir: Path
    database_path: Path
    max_concurrent_jobs: int
    upload_max_bytes: int
    master_key: bytes

    @classmethod
    def from_environ(cls, environ: Mapping[str, str], project_root: Path) -> "V2Settings":
        try:
            workers = int(environ.get("V2_MAX_CONCURRENT_JOBS", "2"))
        except ValueError as exc:
            raise ValueError("V2_MAX_CONCURRENT_JOBS must be a positive integer") from exc
        if workers < 1:
            raise ValueError("V2_MAX_CONCURRENT_JOBS must be a positive integer")
        try:
            upload_gb = int(environ.get("V2_UPLOAD_MAX_GB", "5"))
        except ValueError as exc:
            raise ValueError("V2_UPLOAD_MAX_GB must be a positive integer") from exc
        if upload_gb < 1:
            raise ValueError("V2_UPLOAD_MAX_GB must be a positive integer")
        encoded = environ.get("VIDA_PROFILE_MASTER_KEY", "")
        if re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", encoded) is None:
            raise ValueError("VIDA_PROFILE_MASTER_KEY must be URL-safe Base64")
        try:
            master_key = base64.b64decode(
                encoded.encode("ascii"), altchars=b"-_", validate=True
            )
        except Exception as exc:
            raise ValueError("VIDA_PROFILE_MASTER_KEY must be URL-safe Base64") from exc
        if base64.urlsafe_b64encode(master_key).decode("ascii") != encoded:
            raise ValueError("VIDA_PROFILE_MASTER_KEY must be URL-safe Base64")
        if len(master_key) != 32:
            raise ValueError("VIDA_PROFILE_MASTER_KEY must decode to exactly 32 bytes")
        data_dir = project_root / "data" / "v2"
        return cls(data_dir, data_dir / "vida-v2.sqlite3", workers, upload_gb * 1024**3, master_key)
