"""Logging Configuration"""

import logging
import logging.handlers
from datetime import date
import structlog
from pathlib import Path
from pythonjsonlogger import jsonlogger

# Per-file size cap before rotation, and how many rotated files to keep.
LOG_MAX_BYTES = 10 * 1024 * 1024   # 10 MB a run
LOG_BACKUP_COUNT = 5


def setup_logging(log_level="INFO", log_file=None):
    """Configure structured logging"""

    # Create logs directory if it doesn't exist
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure standard logging
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(message)s"
    )

    # Add file handler with JSON formatting if log file specified.
    # Logs are broken up PER DAY (date-stamped filename) and each file is capped
    # at 10 MB with a few rotations retained, so a log can never grow unbounded
    # (this replaced a plain FileHandler that had ballooned to 384 MB).
    if log_file:
        log_path = Path(log_file)
        dated = log_path.with_name(
            f"{log_path.stem}-{date.today().isoformat()}{log_path.suffix}"
        )
        file_handler = logging.handlers.RotatingFileHandler(
            dated,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        json_formatter = jsonlogger.JsonFormatter(
            '%(asctime)s %(name)s %(levelname)s %(message)s'
        )
        file_handler.setFormatter(json_formatter)
        logging.root.addHandler(file_handler)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name=None):
    """Get a structured logger instance"""
    return structlog.get_logger(name)
