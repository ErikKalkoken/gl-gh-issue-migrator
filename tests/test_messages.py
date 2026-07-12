import unittest
from typing import NamedTuple, Optional
from unittest.mock import MagicMock

from issue_migrator.messages import Messages


class TestMessages(unittest.TestCase):

    def setUp(self):
        self.mock_console = MagicMock()

    def test_functions(self):
        class LogCase(NamedTuple):
            func: str
            text: str
            style: Optional[str]
            want: str

        cases = [
            LogCase(
                func="critical",
                text="Critical failure",
                style="bold magenta",
                want="! Critical failure",
            ),
            LogCase(
                func="error",
                text="An error occurred",
                style="bold red",
                want="✖ An error occurred",
            ),
            LogCase(
                func="info",
                text="Informational text",
                style="cyan",
                want="» Informational text",
            ),
            LogCase(
                func="notice", text="Notice message", style=None, want="Notice message"
            ),
            LogCase(
                func="success",
                text="Operation successful",
                style="bold green",
                want="✔ Operation successful",
            ),
            LogCase(
                func="warning",
                text="Warning issued",
                style="yellow",
                want="⚠ Warning issued",
            ),
        ]
        messages = Messages()
        for case in cases:
            with self.subTest(level=case.func):
                self.mock_console.reset_mock()
                getattr(messages, case.func)(case.text, console=self.mock_console)
                self.mock_console.print.assert_called_once_with(
                    case.want,
                    style=case.style,
                    markup=False,
                )
