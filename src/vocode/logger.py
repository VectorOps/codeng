from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import Optional

import structlog

LOG_FILE_NAME = "log.txt"


@dataclass
class LogRecordEntry:
    logger_name: str
    level: int
    level_name: str
    message: str
    created: float


class LogManager:
    def __init__(self, max_entries: Optional[int] = None) -> None:
        self._max_entries = max_entries
        self._records: list[LogRecordEntry] = []

    def add_record(self, record: logging.LogRecord) -> None:
        entry = LogRecordEntry(
            logger_name=record.name,
            level=record.levelno,
            level_name=record.levelname,
            message=record.getMessage(),
            created=record.created,
        )
        self._records.append(entry)
        if self._max_entries is not None and len(self._records) > self._max_entries:
            overflow = len(self._records) - self._max_entries
            if overflow > 0:
                del self._records[0:overflow]

    def get_records(self) -> list[LogRecordEntry]:
        return list(self._records)

    def get_logs(self) -> list[LogRecordEntry]:
        return self.get_records()


class _InMemoryLogHandler(logging.Handler):
    def __init__(self, manager: LogManager) -> None:
        super().__init__()
        self._manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        self._manager.add_record(record)


_log_manager: Optional[LogManager] = None
_log_handler: Optional[_InMemoryLogHandler] = None


def init_log_manager(max_entries: Optional[int] = None) -> LogManager:
    global _log_manager, _log_handler
    if _log_manager is None:
        _log_manager = LogManager(max_entries=max_entries)
        _log_handler = _InMemoryLogHandler(_log_manager)

    root_logger = logging.getLogger()
    if _log_handler is not None and _log_handler not in root_logger.handlers:
        root_logger.addHandler(_log_handler)

    try:
        import sys

        for handler in list(root_logger.handlers):
            if isinstance(handler, logging.StreamHandler) and getattr(
                handler, "stream", None
            ) in (sys.stdout, sys.stderr):
                root_logger.removeHandler(handler)

        manager = logging.root.manager
        for logger_obj in list(manager.loggerDict.values()):
            if isinstance(logger_obj, logging.Logger):
                for handler in list(logger_obj.handlers):
                    if isinstance(handler, logging.StreamHandler) and getattr(
                        handler, "stream", None
                    ) in (sys.stdout, sys.stderr):
                        logger_obj.removeHandler(handler)
                if (
                    _log_handler is not None
                    and not logger_obj.propagate
                    and _log_handler not in logger_obj.handlers
                ):
                    logger_obj.addHandler(_log_handler)
    except Exception:
        pass

    def _showwarning(
        message: warnings.WarningMessage | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: object | None = None,
        line: str | None = None,
    ) -> None:
        text = warnings.formatwarning(message, category, filename, lineno, line)
        logging.getLogger("py.warnings").warning(text.strip())

    warnings.showwarning = _showwarning
    return _log_manager


def get_log_manager_internal() -> Optional[LogManager]:
    return _log_manager


def install_in_memory_log_manager(max_entries: Optional[int] = None) -> LogManager:
    return init_log_manager(max_entries=max_entries)


def get_log_manager() -> Optional[LogManager]:
    return get_log_manager_internal()


# logging.disable(logging.CRITICAL)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.FileHandler(
            LOG_FILE_NAME,
            mode="w",
            encoding="utf-8",
            delay=False,
        )
    ],
)

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.dev.ConsoleRenderer(),
    ],
)

logger: structlog.BoundLogger = structlog.get_logger("vocode")


# Fix Litellm warning
warnings.filterwarnings(
    "ignore", category=UserWarning, message=r"^Pydantic serializer warnings:"
)
