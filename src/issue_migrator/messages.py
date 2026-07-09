import enum

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


def _message(text: str, level: _Level):
    _console.print(f"\\[{level.name}] {text}", style=_STYLE.get(level))


def critical(text: str):
    """Produce a critical message."""
    _message(text, _Level.CRITICAL)


def error(text: str):
    """Produce an error message."""
    _message(text, _Level.ERROR)


def info(text: str):
    """Produce an info message."""
    _message(text, _Level.INFO)


def notice(text: str):
    """Produce a notice message."""
    _message(text, _Level.NOTICE)


def success(text: str):
    """Produce a success message."""
    _message(text, _Level.SUCCESS)


def warning(text: str):
    """Produce a warning message."""
    _message(text, _Level.WARNING)
