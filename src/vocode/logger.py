import logging
import structlog

LOG_FILE_NAME = "log.txt"

# logging.disable(logging.CRITICAL)

logging.basicConfig(
    level=logging.WARN,
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
