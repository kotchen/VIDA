from __future__ import annotations

import os
from collections.abc import MutableMapping


_DEFAULTS = {
    "HF_HUB_DISABLE_XET": "1",
    "HF_HUB_ETAG_TIMEOUT": "10",
    "HF_HUB_DOWNLOAD_TIMEOUT": "60",
}


def configure_huggingface_environment(
    environ: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environ is None else environ
    for name, value in _DEFAULTS.items():
        target.setdefault(name, value)
