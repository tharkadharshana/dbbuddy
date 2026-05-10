"""
DataMind Logging System
=======================
Structured JSON logging similar to Winston (Node.js).
Configure via .env:
  LOG_LEVEL=DEBUG   # DEBUG | INFO | WARNING | ERROR  (default: INFO)
  LOG_FORMAT=json   # json | pretty                   (default: pretty in dev)
  LOG_FILE=logs/datamind.log  # optional file output

Usage:
  from logger import get_logger
  log = get_logger(__name__)

  log.info("User logged in", user="john@example.com", ip="1.2.3.4")
  log.debug("SQL generated", template="revenue_trend", sql="SELECT ...")
  log.warning("LLM retry", attempt=2, llm="gemini")
  log.error("DB connection failed", host="localhost", error=str(e))
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


# ── Config from .env ──────────────────────────────────────────────────────────

_LEVEL_MAP = {
    "DEBUG":   logging.DEBUG,
    "INFO":    logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR":   logging.ERROR,
}

LOG_LEVEL   = _LEVEL_MAP.get(os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
LOG_FORMAT  = os.getenv("LOG_FORMAT", "pretty")   # "json" or "pretty"
LOG_FILE    = os.getenv("LOG_FILE", "")           # e.g. "logs/datamind.log"


# ── JSON formatter ────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Outputs one JSON object per line — easy to parse/search/grep."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":      datetime.utcnow().isoformat() + "Z",
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        # Extra fields attached via log.info("msg", **kwargs)
        for key, val in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "id", "levelname", "levelno",
                "lineno", "message", "module", "msecs", "msg", "name",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName", "taskName",
            ):
                payload[key] = val

        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


# ── Pretty formatter (coloured, human-readable) ───────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREY   = "\033[90m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BRED   = "\033[1;91m"

_LEVEL_COLORS = {
    "DEBUG":   GREY,
    "INFO":    GREEN,
    "WARNING": YELLOW,
    "ERROR":   RED,
    "CRITICAL":BRED,
}

class PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts    = datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]
        color = _LEVEL_COLORS.get(record.levelname, RESET)
        level = f"{color}{record.levelname:<8}{RESET}"
        name  = f"{GREY}{record.name}{RESET}"
        msg   = record.getMessage()

        # Collect extra fields
        extras = {}
        for key, val in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "id", "levelname", "levelno",
                "lineno", "message", "module", "msecs", "msg", "name",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName", "taskName",
            ):
                extras[key] = val

        line = f"{GREY}{ts}{RESET} {level} {CYAN}{name}{RESET}  {BOLD}{msg}{RESET}"
        if extras:
            kv = "  ".join(f"{GREY}{k}{RESET}={GREEN}{v!r}{RESET}" for k, v in extras.items())
            line += f"\n          {kv}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ── Logger factory ────────────────────────────────────────────────────────────

def _build_handler(stream=None, filepath=None) -> logging.Handler:
    handler = logging.StreamHandler(stream) if stream else logging.FileHandler(filepath)
    if LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(PrettyFormatter())
    handler.setLevel(LOG_LEVEL)
    return handler


def _setup_root():
    root = logging.getLogger("datamind")
    if root.handlers:
        return  # already configured
    root.setLevel(LOG_LEVEL)
    root.addHandler(_build_handler(stream=sys.stdout))

    if LOG_FILE:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        root.addHandler(_build_handler(filepath=str(log_path)))
        root.info(f"File logging enabled", path=str(log_path))

    # Quieten noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "prophet", "cmdstanpy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_root()


# Python's logging.LogRecord has reserved built-in attributes.
# Passing any of these as `extra` keys raises KeyError at runtime.
# We prefix colliding keys with "_" so they still appear in output.
_RESERVED_LOG_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "id", "levelname", "levelno", "lineno", "message",
    "module", "msecs", "msg", "name", "pathname", "process",
    "processName", "relativeCreated", "stack_info", "thread",
    "threadName", "taskName",
})


class DataMindLogger:
    """
    Thin wrapper that lets you pass keyword args as structured fields:
      log.info("msg", user="alice", duration_ms=42)
      log.info("Add DB config", name="prod")  # 'name' is safe — auto-prefixed to '_name'
    """
    def __init__(self, name: str):
        self._log = logging.getLogger(f"datamind.{name}")

    def _emit(self, level: int, msg: str, **kwargs):
        if self._log.isEnabledFor(level):
            # Rename any key that would collide with a built-in LogRecord field
            extra = {
                (f"_{k}" if k in _RESERVED_LOG_ATTRS else k): v
                for k, v in kwargs.items()
            }
            self._log.log(level, msg, extra=extra, stacklevel=3)

    def debug(self, msg: str, **kw):   self._emit(logging.DEBUG,   msg, **kw)
    def info(self, msg: str, **kw):    self._emit(logging.INFO,    msg, **kw)
    def warning(self, msg: str, **kw): self._emit(logging.WARNING,  msg, **kw)
    def error(self, msg: str, **kw):   self._emit(logging.ERROR,   msg, **kw)

    def exception(self, msg: str, **kw):
        """Log ERROR with full traceback automatically."""
        exc = traceback.format_exc()
        self._emit(logging.ERROR, msg, traceback=exc, **kw)


def get_logger(name: str) -> DataMindLogger:
    """Get a named logger. Use module __name__ as the name."""
    return DataMindLogger(name)
