"""
Logger - Unified Logging with Loguru
===========================================
This module provides a unified logging interface using loguru.
It supports task-specific logging, multiple log levels, and flexible configuration.

A stdlib InterceptHandler bridges all standard `logging` calls into loguru,
so toolkit/crawler modules need zero changes.
"""

import logging
import sys
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union
from loguru import logger
import contextvars


LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[task_id]} | {name}:{function}:{line} + {message}{exception}"

# Context variables to support async environments
_task_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('task_id', default=None)
_agent_label_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('agent_label', default=None)
_pair_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('pair_id', default=None)

# Noisy third-party loggers that should be suppressed to WARNING
# Noisy third-party loggers suppressed to WARNING
_NOISY_LOGGERS = (
    "urllib3",
    "httpx",
    "aiohttp",
    "filelock",
    "sentence_transformers",
    "transformers",
    "huggingface_hub",
    "openai",
)

# Loggers silenced entirely (CRITICAL only)
_SILENT_LOGGERS = (
    "botocore",
    "boto3",
    "anthropic",
)


class InterceptHandler(logging.Handler):
    """Route stdlib logging records into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level to loguru level name
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller frame outside the logging module
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


class Logger:
    """
    Unified logger for using loguru.
    Supports task-specific logging and provides a clean interface.
    """

    _initialized = False
    _log_dir: Optional[Path] = None

    def __init__(self, task_id: Optional[str] = None):
        self._task_id: Optional[str] = None

        # Initialize loguru on first use
        if not Logger._initialized:
            self._setup_default_logger()

        if task_id:
            self.set_task_id(task_id)

    @staticmethod
    def _patch_record(record):
        """Ensure common metadata is present on each log record."""
        extra = record["extra"]

        if extra.get("task_id") in (None, ""):
            context_task_id = _task_id_context.get()
            extra["task_id"] = context_task_id or "NO-TASK"

        if extra.get("agent_label") is None:
            extra["agent_label"] = _agent_label_context.get()

        if extra.get("pair_id") is None:
            extra["pair_id"] = _pair_id_context.get()

        file_path = record.get("file")
        if file_path is not None:
            try:
                path_obj = Path(file_path.path).resolve()
                cwd = Path.cwd().resolve()
                extra["file_path"] = path_obj.relative_to(cwd).as_posix()
            except Exception:
                extra["file_path"] = Path(file_path.path).as_posix()
        else:
            extra.setdefault("file_path", "<unknown>")

    @staticmethod
    def _sanitize_base64_content(data: Any) -> Any:
        """
        Recursively replace base64 content with '<base64_content>' placeholder.

        This prevents log files from becoming bloated with large base64-encoded
        images or PDFs while still showing the structure of the data.
        """
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if key == "data" and isinstance(value, str):
                    if len(value) > 100 and re.match(r'^[A-Za-z0-9+/=]+$', value[:100]):
                        result[key] = "<base64_content>"
                        continue
                result[key] = Logger._sanitize_base64_content(value)
            return result

        elif isinstance(data, (list, tuple)):
            result = [Logger._sanitize_base64_content(item) for item in data]
            return result if isinstance(data, list) else tuple(result)

        else:
            return data

    @classmethod
    def _setup_default_logger(cls):
        """Setup default loguru configuration"""
        if cls._initialized:
            return

        # Remove default handler
        logger.remove()

        # Ensure consistent record metadata
        logger.configure(patcher=cls._patch_record)

        # Single console handler (patcher guarantees task_id is always set)
        logger.add(
            sys.stdout,
            format=LOG_FORMAT,
            level="DEBUG",
        )

        # Install stdlib bridge
        cls._install_intercept_handler()

        cls._initialized = True

    @classmethod
    def _install_intercept_handler(cls):
        """Install InterceptHandler on the root stdlib logger and suppress noisy libs."""
        root = logging.getLogger()
        root.handlers = [InterceptHandler()]
        root.setLevel(logging.DEBUG)

        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

        for name in _SILENT_LOGGERS:
            logging.getLogger(name).setLevel(logging.CRITICAL)

    @classmethod
    def _cleanup_stale_files(cls, log_dir: Path):
        """Delete 0-byte error.log and llm.log left from previous runs."""
        for name in ("error.log", "llm.log"):
            path = log_dir / name
            try:
                if path.exists() and path.stat().st_size == 0:
                    path.unlink()
            except OSError:
                pass

    @classmethod
    def setup(
        cls,
        log_dir: Optional[Union[str, Path]] = None,
        console: bool = True,
        level: str = "INFO",
        rotation: str = "10 MB",
        retention: str = "7 days",
        compression: str = "zip"
    ):
        """
        Setup loguru logging with custom configuration

        Args:
            log_dir: Directory for log files (optional)
            console: Whether to output to console
            level: Logging level (TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL)
            rotation: When to rotate log files (e.g., "10 MB", "1 week", "00:00")
            retention: How long to keep old log files (e.g., "7 days", "1 month")
            compression: Compression format for rotated logs (e.g., "zip", "tar.gz")
        """
        # Remove all existing handlers
        logger.remove()

        # Ensure consistent record metadata
        logger.configure(patcher=cls._patch_record)

        cls._log_dir = Path(log_dir) if log_dir else None

        # Single console handler
        if console:
            logger.add(
                sys.stdout,
                format=LOG_FORMAT,
                level=level,
            )

        # Process-level file handler. Records WITHOUT a pair_id and WITHOUT
        # an agent_label land here (i.e. orchestration / benchmark-driver logs).
        # Per-pair and per-agent records go to their own sinks via
        # add_pair_log / add_agent_log.
        if cls._log_dir:
            cls._log_dir.mkdir(parents=True, exist_ok=True)

            # Clean up dead files from previous runs
            cls._cleanup_stale_files(cls._log_dir)

            def process_level_filter(record):
                extra = record["extra"]
                return extra.get("agent_label") is None and extra.get("pair_id") is None

            logger.add(
                cls._log_dir / "agent.log",
                format=LOG_FORMAT,
                level="DEBUG",
                filter=process_level_filter,
                rotation=rotation,
                retention=retention,
                compression=compression,
            )

        # Install stdlib bridge (idempotent — replaces root handlers)
        cls._install_intercept_handler()

        # Session boundary marker
        logger.info("=== SESSION START ===")

        cls._initialized = True

    def _log_with_exception_support(self, level: str, message: str, *args, **kwargs) -> None:
        """Emit log while ensuring active exceptions include stack traces."""
        exception_param = kwargs.pop("exception", None)
        exc_info_flag = kwargs.pop("exc_info", False)

        exc_object: Any = None
        if isinstance(exception_param, BaseException):
            exc_object = exception_param
        elif exception_param:
            exc_object = True

        if exc_object is None and exc_info_flag:
            exc_object = True

        if exc_object is None and sys.exc_info()[0] is not None:
            exc_object = True

        logger_instance = self._get_logger()
        if exc_object is not None:
            emitter = getattr(logger_instance.opt(exception=exc_object), level)
        else:
            emitter = getattr(logger_instance, level)

        emitter(message, *args, **kwargs)

    def set_task_id(self, task_id: str):
        """
        Set task ID for this logger instance

        Args:
            task_id: Task identifier
        """
        self._task_id = task_id
        _task_id_context.set(task_id)

    def get_task_id(self) -> Optional[str]:
        """Get current task ID"""
        return self._task_id or _task_id_context.get()

    @contextmanager
    def task_scope(self, task_id: str):
        """Temporarily set task_id for logs within this scope."""
        previous_local = self._task_id
        token = _task_id_context.set(task_id)
        self._task_id = task_id
        try:
            yield self
        finally:
            self._task_id = previous_local
            _task_id_context.reset(token)

    @classmethod
    def add_agent_log(cls, agent_label: str, log_dir: Union[str, Path]) -> int:
        """Add a per-agent log file that only receives records from that agent.

        Returns the loguru sink ID so it can be removed later.
        """
        log_path = Path(log_dir) / "agent.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Capture the current pair context, if this agent is being run inside a
        # pair_scope. Without this, every concurrent judge_agent sink receives
        # every other pair's judge logs because they share the same agent label.
        expected_pair_id = _pair_id_context.get()

        def agent_filter(record):
            extra = record["extra"]
            if extra.get("agent_label") != agent_label:
                return False
            if expected_pair_id is not None and extra.get("pair_id") != expected_pair_id:
                return False
            return True

        sink_id = logger.add(
            log_path,
            format=LOG_FORMAT,
            level="DEBUG",
            filter=agent_filter,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
        )
        return sink_id

    @classmethod
    def remove_agent_log(cls, sink_id: int):
        """Remove a previously added per-agent log sink.

        Idempotent: another concurrent caller (e.g. a stray Logger.setup() that
        nuked all sinks) may have already removed this sink_id. Swallow that
        case rather than letting cleanup overwrite a successful return value
        from inside a try/finally.
        """
        try:
            logger.remove(sink_id)
        except ValueError:
            pass

    @classmethod
    def add_pair_log(cls, pair_id: str, log_dir: Union[str, Path]) -> int:
        """Add a per-pair log file that only receives records from that pair.

        Filters records by the `pair_id` contextvar (set via pair_scope()) AND
        excludes agent-scoped records so they don't double-write into both the
        pair log and the agent log. Returns the loguru sink ID.
        """
        log_path = Path(log_dir) / "agent.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def pair_filter(record):
            extra = record["extra"]
            return extra.get("pair_id") == pair_id and extra.get("agent_label") is None

        sink_id = logger.add(
            log_path,
            format=LOG_FORMAT,
            level="DEBUG",
            filter=pair_filter,
            rotation="10 MB",
            retention="7 days",
            compression="zip",
        )
        return sink_id

    @classmethod
    def remove_pair_log(cls, sink_id: int):
        """Remove a previously added per-pair log sink. Idempotent — see remove_agent_log."""
        try:
            logger.remove(sink_id)
        except ValueError:
            pass

    @contextmanager
    def agent_scope(self, agent_label: str):
        """Set agent_label context so per-agent log sinks receive records."""
        token = _agent_label_context.set(agent_label)
        try:
            yield self
        finally:
            _agent_label_context.reset(token)

    @contextmanager
    def pair_scope(self, pair_id: str):
        """Set pair_id context so per-pair log sinks receive records."""
        token = _pair_id_context.set(pair_id)
        try:
            yield self
        finally:
            _pair_id_context.reset(token)

    def _get_logger(self):
        """Get logger instance with task_id context"""
        task_id = self.get_task_id() or "NO-TASK"
        return logger.bind(task_id=task_id)

    # Logging methods

    def trace(self, message: str, *args, **kwargs):
        """Log trace message (most detailed)"""
        self._get_logger().trace(message, *args, **kwargs)

    def debug(self, message: str, *args, **kwargs):
        """Log debug message"""
        self._get_logger().debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        """Log info message"""
        self._get_logger().info(message, *args, **kwargs)

    def success(self, message: str, *args, **kwargs):
        """Log success message"""
        self._get_logger().success(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """Log warning message"""
        self._get_logger().warning(message, *args, **kwargs)

    def warn(self, message: str, *args, **kwargs):
        """Alias for warning"""
        self.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """Log error message"""
        self._log_with_exception_support("error", message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        """Log critical message"""
        self._log_with_exception_support("critical", message, *args, **kwargs)

    def exception(self, message: str, *args, **kwargs):
        """Log exception with traceback"""
        self._get_logger().exception(message, *args, **kwargs)

    # Context managers

    def catch(self, *args, **kwargs):
        """
        Context manager to catch exceptions and log them

        Usage:
            with logger.catch():
                # code that might raise exceptions
        """
        return self._get_logger().catch(*args, **kwargs)

    def contextualize(self, **kwargs):
        """
        Context manager to add extra context to logs

        Usage:
            with logger.contextualize(user_id="123"):
                logger.info("User action")  # Will include user_id in log
        """
        return self._get_logger().contextualize(**kwargs)


# Module-level convenience functions

def setup_logging(
    log_dir: Optional[Union[str, Path]] = None,
    console: bool = True,
    level: str = "INFO",
    rotation: str = "10 MB",
    retention: str = "7 days",
    compression: str = "zip"
):
    """
    Setup loguru logging (convenience function)
    """
    Logger.setup(
        log_dir=log_dir,
        console=console,
        level=level,
        rotation=rotation,
        retention=retention,
        compression=compression
    )


def get_logger(task_id: Optional[str] = None) -> Logger:
    """
    Get a logger instance (convenience function)
    """
    return Logger(task_id=task_id)
