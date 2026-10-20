from __future__ import annotations

import logging

from rich.logging import RichHandler

from auditer.utils.security import sanitise_git_output


class SanitisedFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return sanitise_git_output(original)


def setup_logging(level: str = "INFO", verbose: bool = False) -> None:
    if verbose:
        level = "DEBUG"

    handler = RichHandler(
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    handler.setFormatter(SanitisedFormatter("%(message)s"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )

    # Suppress noisy HTTP request logging from httpx/httpcore
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
