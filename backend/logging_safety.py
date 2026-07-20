from __future__ import annotations

import logging
from urllib.parse import urlsplit


SENSITIVE_DEPENDENCY_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
    "yt_dlp",
)


def disable_sensitive_dependency_logs() -> None:
    """Prevent dependencies from rendering request URLs, headers, or payloads."""
    for name in SENSITIVE_DEPENDENCY_LOGGERS:
        logging.getLogger(name).disabled = True


def log_exception(
    logger: logging.Logger,
    level: int,
    event: str,
    exception: BaseException,
) -> None:
    """Log useful failure context without rendering attacker-controlled details."""
    logger.log(level, "%s error_type=%s", event, type(exception).__name__)


def safe_url_origin(value: str | None) -> str:
    """Return only a URL's scheme and host, excluding credentials/path/query data."""
    try:
        parsed = urlsplit(value or "")
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if scheme not in {"http", "https"} or not host:
            return "unavailable"
        port = parsed.port
    except (TypeError, ValueError):
        return "unavailable"
    suffix = "" if port is None else f":{port}"
    return f"{scheme}://{host.lower()}{suffix}"
