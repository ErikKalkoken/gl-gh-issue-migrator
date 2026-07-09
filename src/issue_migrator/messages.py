import enum
from typing import Optional

from rich.console import Console


class _Level(enum.IntEnum):
    CRITICAL = 50
    DEBUG = 10
    ERROR = 40
    INFO = 20
    NOTICE = 15
    SUCCESS = 25
    WARNING = 30


_STYLE = {
    _Level.CRITICAL: "bold magenta",
    _Level.DEBUG: "blue",
    _Level.ERROR: "bold red",
    _Level.INFO: "cyan",
    _Level.SUCCESS: "bold green",
    _Level.WARNING: "yellow",
}

_console = Console()


def _message(text: str, level: _Level, console: Optional[Console]):
    console = console or _console
    console.print(f"\\[{level.name}] {text}", style=_STYLE.get(level))


def critical(text: str, console: Optional[Console] = None):
    """Produce a critical message."""
    _message(text, _Level.CRITICAL, console)


def error(text: str, console: Optional[Console] = None):
    """Produce an error message."""
    _message(text, _Level.ERROR, console)


def info(text: str, console: Optional[Console] = None):
    """Produce an info message."""
    _message(text, _Level.INFO, console)


def notice(text: str, console: Optional[Console] = None):
    """Produce a notice message."""
    _message(text, _Level.NOTICE, console)


def success(text: str, console: Optional[Console] = None):
    """Produce a success message."""
    _message(text, _Level.SUCCESS, console)


def warning(text: str, console: Optional[Console] = None):
    """Produce a warning message."""
    _message(text, _Level.WARNING, console)
