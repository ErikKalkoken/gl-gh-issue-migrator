import enum
from typing import Optional

from rich.console import Console


class Messages:
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
        _Level.DEBUG: "dim blue",
        _Level.ERROR: "bold red",
        _Level.INFO: "cyan",
        _Level.SUCCESS: "bold green",
        _Level.WARNING: "yellow",
    }

    def __init__(self, console: Optional[Console] = None):
        """Initialize the Messages class with an optional rich Console."""
        self.console = console or Console()

    def _message(self, text: str, level: _Level, console: Optional[Console] = None):
        """Internal helper to print the styled message."""
        active_console = console or self.console
        active_console.print(
            f"[{level.name}] {text}", style=self._STYLE.get(level), markup=False
        )

    def critical(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a critical message."""
        self._message(text, self._Level.CRITICAL, console)

    def debug(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a debug message."""
        self._message(text, self._Level.DEBUG, console)

    def error(self, text: str, console: Optional[Console] = None) -> None:
        """Produce an error message."""
        self._message(text, self._Level.ERROR, console)

    def info(self, text: str, console: Optional[Console] = None) -> None:
        """Produce an info message."""
        self._message(text, self._Level.INFO, console)

    def notice(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a notice message."""
        self._message(text, self._Level.NOTICE, console)

    def success(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a success message."""
        self._message(text, self._Level.SUCCESS, console)

    def warning(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a warning message."""
        self._message(text, self._Level.WARNING, console)
