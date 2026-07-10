import unittest
from unittest.mock import MagicMock, patch

from issue_migrator import messages


class TestLogger(unittest.TestCase):

    def setUp(self):
        self.mock_console = MagicMock()

    @patch("issue_migrator.messages._default_console")
    def test_logger_functions(self, mock_default_console):
        cases = [
            {
                "func": messages.critical,
                "text": "Critical failure",
                "level_name": "CRITICAL",
                "style": "bold magenta",
            },
            {
                "func": messages.error,
                "text": "An error occurred",
                "level_name": "ERROR",
                "style": "bold red",
            },
            {
                "func": messages.info,
                "text": "Informational text",
                "level_name": "INFO",
                "style": "cyan",
            },
            {
                "func": messages.notice,
                "text": "Notice message",
                "level_name": "NOTICE",
                "style": None,
            },
            {
                "func": messages.success,
                "text": "Operation successful",
                "level_name": "SUCCESS",
                "style": "bold green",
            },
            {
                "func": messages.warning,
                "text": "Warning issued",
                "level_name": "WARNING",
                "style": "yellow",
            },
        ]

        for case in cases:
            with self.subTest(level=case["level_name"]):
                self.mock_console.reset_mock()
                case["func"](case["text"], console=self.mock_console)
                self.mock_console.print.assert_called_once_with(
                    f"[{case['level_name']}] {case['text']}",
                    style=case["style"],
                    markup=False,
                )

        for case in cases:
            with self.subTest(level=case["level_name"], console="default"):
                mock_default_console.reset_mock()
                case["func"](case["text"])
                mock_default_console.print.assert_called_once_with(
                    f"[{case['level_name']}] {case['text']}",
                    style=case["style"],
                    markup=False,
                )
