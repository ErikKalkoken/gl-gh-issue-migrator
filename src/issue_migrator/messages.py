"""User messages."""

import enum
import logging
from typing import Optional

from rich.console import Console


class Messages:
    """A class for generating user messages with semantic colors on the console."""

    class Level(enum.IntEnum):
        """The severity level of a message."""

        CRITICAL = 50
        DEBUG = 10
        ERROR = 40
        INFO = 20
        NOTICE = 15
        SUCCESS = 25
        WARNING = 30

    _STYLE = {
        Level.CRITICAL: "bold magenta",
        Level.DEBUG: "dim blue",
        Level.ERROR: "bold red",
        Level.INFO: "cyan",
        Level.SUCCESS: "bold green",
        Level.WARNING: "yellow",
    }

    _SYMBOL = {
        Level.CRITICAL: ("!", "[FATAL]"),
        Level.DEBUG: ("⚙", ""),
        Level.ERROR: ("✖", "[X]"),
        Level.INFO: ("»", "[i]"),
        Level.SUCCESS: ("✔", "[OK]"),
        Level.WARNING: ("⚠", "[!]"),
    }

    def __init__(self, console: Optional[Console] = None):
        """Initialize the Messages class with an optional rich Console."""
        self._console = console or Console()
        self._supports_unicode = "utf-8" in self._console.encoding.lower()

    def print(self, text: str, level: Level, console: Optional[Console] = None):
        """Internal helper to print the styled message."""
        active_console = console or self._console

        symbols = self._SYMBOL.get(level)
        if not symbols:
            symbol = ""
        else:
            if self._supports_unicode:
                i = 0
            else:
                i = 1
            symbol = symbols[i] + " "

        active_console.print(
            f"{symbol}{text}", style=self._STYLE.get(level), markup=False
        )

    def critical(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a critical message."""
        self.print(text, self.Level.CRITICAL, console)

    def debug(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a debug message."""
        self.print(text, self.Level.DEBUG, console)

    def error(self, text: str, console: Optional[Console] = None) -> None:
        """Produce an error message."""
        self.print(text, self.Level.ERROR, console)

    def info(self, text: str, console: Optional[Console] = None) -> None:
        """Produce an info message."""
        self.print(text, self.Level.INFO, console)

    def notice(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a notice message."""
        self.print(text, self.Level.NOTICE, console)

    def success(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a success message."""
        self.print(text, self.Level.SUCCESS, console)

    def warning(self, text: str, console: Optional[Console] = None) -> None:
        """Produce a warning message."""
        self.print(text, self.Level.WARNING, console)


class MessagesLogHandler(logging.Handler):
    """A log handler for printing log records with messages."""

    def __init__(self, messages: Messages, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._messages = messages

    def emit(self, record):
        try:
            match record.levelno:
                case logging.DEBUG:
                    level = Messages.Level.DEBUG
                case logging.INFO:
                    level = Messages.Level.INFO
                case logging.WARNING:
                    level = Messages.Level.WARNING
                case logging.ERROR:
                    level = Messages.Level.ERROR
                case logging.CRITICAL:
                    level = Messages.Level.CRITICAL
                case _:
                    level = Messages.Level.NOTICE

            self._messages.print(f"{record.name}: {record.msg}", level=level)

        except Exception:  # pylint: disable=W0718
            self.handleError(record)
