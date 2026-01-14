from __future__ import annotations

import logging
import sys
import warnings

from vocode.logger import LogManager, init_log_manager


def _has_tty_handler(log: logging.Logger) -> bool:
    for handler in log.handlers:
        if isinstance(handler, logging.StreamHandler):
            stream = getattr(handler, "stream", None)
            if stream in (sys.stdout, sys.stderr):
                return True
    return False


def test_log_manager_disables_tty_and_captures() -> None:
    root_logger = logging.getLogger()
    root_stream_handler = logging.StreamHandler(sys.stdout)
    root_logger.addHandler(root_stream_handler)

    named_logger = logging.getLogger("existing.nonprop")
    named_logger.setLevel(logging.INFO)
    named_logger.propagate = False
    named_stream_handler = logging.StreamHandler(sys.stdout)
    named_logger.addHandler(named_stream_handler)

    manager = init_log_manager(max_entries=None)
    assert isinstance(manager, LogManager)

    assert not _has_tty_handler(root_logger)
    assert not _has_tty_handler(named_logger)

    root_logger.warning("root warning message")
    named_logger.info("named logger message")

    records = manager.get_logs()
    messages = [record.message for record in records]
    assert "root warning message" in messages
    assert "named logger message" in messages


def test_log_manager_captures_warnings() -> None:
    manager = init_log_manager(max_entries=None)
    assert isinstance(manager, LogManager)
    warnings.warn("warning from warnings module", UserWarning)

    records = manager.get_logs()
    messages = [record.message for record in records]
    assert any("warning from warnings module" in message for message in messages)
