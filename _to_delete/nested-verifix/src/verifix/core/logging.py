"""
Structured logging with redaction as a mandatory processor.

Every log event passes through redact() before rendering, so a secret that slips into a
log call - an exception message containing a header, a tool output with a token in it -
is replaced before it reaches the console or a file.
"""

import logging
import sys
from typing import Any

import structlog

from verifix.core.redact import redact


def _redact_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact(event_dict)


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            _redact_processor,  # after exc formatting, so tracebacks are redacted too
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
