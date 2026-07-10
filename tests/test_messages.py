import unittest
from unittest.mock import MagicMock

from issue_migrator.messages import Messages


class TestMessages(unittest.TestCase):

    def setUp(self):
        self.mock_console = MagicMock()

    def test_functions(self):
        cases = [
            {
                "func": "critical",
                "text": "Critical failure",
                "level_name": "CRITICAL",
                "style": "bold magenta",
            },
            {
                "func": "error",
                "text": "An error occurred",
                "level_name": "ERROR",
                "style": "bold red",
            },
            {
                "func": "info",
                "text": "Informational text",
                "level_name": "INFO",
                "style": "cyan",
            },
            {
                "func": "notice",
                "text": "Notice message",
                "level_name": "NOTICE",
                "style": None,
            },
            {
                "func": "success",
                "text": "Operation successful",
                "level_name": "SUCCESS",
                "style": "bold green",
            },
            {
                "func": "warning",
                "text": "Warning issued",
                "level_name": "WARNING",
                "style": "yellow",
            },
        ]
        messages = Messages()
        for case in cases:
            with self.subTest(level=case["level_name"]):
                self.mock_console.reset_mock()
                getattr(messages, case["func"])(case["text"], console=self.mock_console)
                self.mock_console.print.assert_called_once_with(
                    f"[{case['level_name']}] {case['text']}",
                    style=case["style"],
                    markup=False,
                )
