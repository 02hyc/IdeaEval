"""Typed public errors for pre-run failures."""


class ResearchIdeationError(Exception):
    """Base class for project API errors."""


class TopicError(ResearchIdeationError):
    """Raised when the user topic is missing or invalid."""


class ConfigError(ResearchIdeationError):
    """Raised when configuration, overrides, or required environment are invalid."""


class WorkspaceError(ResearchIdeationError):
    """Raised when a run workspace cannot be created before run metadata exists."""


class RunTimeout(BaseException):
    """Raised when the configured run-level timeout expires.

    Subclasses BaseException so it is not swallowed by adapter-level
    `except Exception:` handlers and propagates straight back to the runtime's
    timeout handler. Runtime SIGALRM-based timeouts and adapter-level cooperative
    timeouts both raise this so the runtime classifies them identically.
    """
