import tempfile
import unittest
from pathlib import PurePath
from typing import Any, Dict, NamedTuple
from unittest import mock

import pook
from diskcache import Cache
from gitlab.v4.objects import Project
from rich.console import Console

from issue_migrator.main import GITLAB_PUBLIC_HOST
from issue_migrator.migrator import (
    REQUEST_TIMEOUT,
    Migrator,
    _remove_image_sizes,
    _upload_file_to_vercel,
)

MODULE_PATH = "issue_migrator.migrator"


def make_migrator_params(**kwargs) -> dict:
    """Return a new migrator with preset values."""
    params = {
        "cache": mock.MagicMock(spec=Cache),
        "console": Console(quiet=True),
        "github_app_id": "1234567",
        "github_installation_id": 1234567,
        "github_private_key": "private-key",
        "github_repo_name": "ErikKalkoken/github-repo",
        "gitlab_host": GITLAB_PUBLIC_HOST,
        "gitlab_repo_name": "ErikKalkoken/gitlab-repo",
        "gitlab_token": "gitlab_token",
        "vercel_blob_token": "vercel_blob_token",
    }
    params.update(kwargs)
    return params


class TestRemoveImageSizes(unittest.TestCase):

    def test_image_with_spaces_and_size_parameters(self):
        """Test that image sizes with preceding whitespace are successfully removed."""
        text = "![Architecture Diagram](images/arch.png) {width=900 height=491}"
        expected = "![Architecture Diagram](images/arch.png)"
        self.assertEqual(_remove_image_sizes(text), expected)

    def test_image_with_no_spaces_and_size_parameters(self):
        """Test that image sizes without preceding whitespace are successfully removed."""
        text = "![Logo](logo.png){width=150}"
        expected = "![Logo](logo.png)"
        self.assertEqual(_remove_image_sizes(text), expected)

    def test_image_without_sizes(self):
        """Test that an image tag without any trailing sizes remains untouched."""
        text = "![No sizes here](photo.jpg)"
        self.assertEqual(_remove_image_sizes(text), text)

    def test_multiple_images_same_line(self):
        """Test that multiple images with sizes on the exact same line are all stripped correctly."""
        text = (
            "First: ![Img1](1.png){width=100} and Second: ![Img2](2.jpg) {height=200}"
        )
        expected = "First: ![Img1](1.png) and Second: ![Img2](2.jpg)"
        self.assertEqual(_remove_image_sizes(text), expected)

    def test_multiple_images_mixed_lines(self):
        """Test a block with multiple images across different lines, some with and some without sizes."""
        text = (
            "![Img1](1.png){width=50}\n"
            "![Img2](2.png)\n"
            "![Img3](3.png) {height=100}\n"
            "![Img4](4.png)"
        )
        expected = (
            "![Img1](1.png)\n" "![Img2](2.png)\n" "![Img3](3.png)\n" "![Img4](4.png)"
        )
        self.assertEqual(_remove_image_sizes(text), expected)

    def test_code_block_remains_untouched(self):
        """Test that nothing inside code blocks is altered, even if it looks like an image with sizes."""
        text = """
```python
# This fake image tag inside code should not be changed!
![Fake](test.png){width=100 height=100}
```
"""
        self.assertEqual(_remove_image_sizes(text), text)

    def test_mixed_markdown(self):
        """Test a full markdown document containing both real images, fake images in code, and text."""
        input_markdown = (
            "# Document Title\n\n"
            "Here is an image: ![Real Image](real.png){width=500}\n\n"
            "```python\n"
            "![Fake Image](fake.png){width=200}\n"
            "```\n\n"
            "Another real image: ![Second](second.png) {height=100}"
        )
        expected_markdown = (
            "# Document Title\n\n"
            "Here is an image: ![Real Image](real.png)\n\n"
            "```python\n"
            "![Fake Image](fake.png){width=200}\n"
            "```\n\n"
            "Another real image: ![Second](second.png)"
        )
        self.assertEqual(_remove_image_sizes(input_markdown), expected_markdown)

    def test_empty_string(self):
        """Test that an empty string returns an empty string without raising an exception."""
        self.assertEqual(_remove_image_sizes(""), "")


class TestDownloadImageFromGitLab(unittest.TestCase):

    @pook.on
    def test_download_image_success(self):
        """Test successful image download."""
        host_url = "https://gitlab.example.com"
        token = "fake-token-123"
        params = make_migrator_params(gitlab_token=token, gitlab_host=host_url)
        with Migrator(**params) as m:
            gl_project = mock.MagicMock()
            gl_project.encoded_id = "my-group%2Fmy-project"
            m._gl_project = gl_project
            rel_url = "images/avatar.png"
            expected_url = "https://gitlab.example.com/-/project/my-group%2Fmy-project/images/avatar.png"
            fake_binary_data = b"fake-jpeg-bytes"
            (
                pook.get(expected_url)
                .reply(200)
                .header("Content-Type", "image/jpeg")
                # No Content-Disposition header provided
                .body(fake_binary_data)
            )

            result = m._download_embedded_file_from_gitlab(rel_url)

            self.assertEqual(result, fake_binary_data)

    @pook.on
    def test_download_image_failure(self):
        """Test that the function returns None and logs error when GitLab returns non-200."""
        host_url = "https://gitlab.example.com"
        token = "fake-token-123"
        params = make_migrator_params(gitlab_token=token, gitlab_host=host_url)
        with Migrator(**params) as m:
            gl_project = mock.MagicMock()
            gl_project.encoded_id = "my-group%2Fmy-project"
            m._gl_project = gl_project
            rel_url = "images/avatar.png"
            expected_url = "https://gitlab.example.com/-/project/my-group%2Fmy-project/images/avatar.png"

            (pook.get(expected_url).reply(404).body("Not Found"))

            result = m._download_embedded_file_from_gitlab(rel_url)

            # Assert that it gracefully handled the failure and returned empty
            self.assertFalse(result)


class TestUploadFileToVercel(unittest.TestCase):
    @mock.patch(MODULE_PATH + ".vercel_blob")
    def test_upload_file_to_vercel_table(self, mock_vercel_blob):
        class Case(NamedTuple):
            name: str
            path: PurePath
            data: bytes
            token: str
            mock_response: Dict[str, Any]
            expected_output: str

        # Define the test table using the NamedTuple schema
        test_table = [
            Case(
                name="Successful upload with valid URL",
                path=PurePath("images/photo.png"),
                data=b"fake_image_bytes",
                token="verc_token_123",
                mock_response={"url": "https://blob.vercel-storage.com/photo-abc.png"},
                expected_output="https://blob.vercel-storage.com/photo-abc.png",
            ),
            Case(
                name="Missing URL in response returns empty string",
                path=PurePath("docs/readme.md"),
                data=b"markdown_data",
                token="verc_token_456",
                mock_response={},
                expected_output="",
            ),
            Case(
                name="None URL value returns empty string",
                path=PurePath("data.json"),
                data=b"{}",
                token="verc_token_789",
                mock_response={"url": None},
                expected_output="",
            ),
        ]

        for case in test_table:
            with self.subTest(name=case.name):
                # Reset the mock for isolation between sub-tests
                mock_vercel_blob.put.reset_mock()
                mock_vercel_blob.put.return_value = case.mock_response

                # Execute the function under test using dot notation from the NamedTuple
                result = _upload_file_to_vercel(
                    path=case.path, data=case.data, token=case.token
                )

                # Assert the return value matches expectation
                self.assertEqual(result, case.expected_output)

                # Verify that vercel_blob.put was called with the correct parameters
                mock_vercel_blob.put.assert_called_once_with(
                    path=str(case.path),
                    data=case.data,
                    timeout=REQUEST_TIMEOUT,
                    options={"token": case.token, "addRandomSuffix": True},
                )


class TestMigrator_MigrateEmbeddedFiles(unittest.TestCase):

    def test_replace_embedded_file_urls(self):
        new_url = "https://cdn.example.com/new-link"
        test_cases = [
            (
                "Replace upload link",
                "Here is the [Specification](/uploads/abc/spec.pdf).",
                f"Here is the [Specification]({new_url}).",
            ),
            (
                "Ignore external file link",
                "Download the doc from [Google Drive](https://drive.google.com/file.pdf).",
                "Download the doc from [Google Drive](https://drive.google.com/file.pdf).",
            ),
            (
                "Replace image upload",
                "Check out the diagram: ![Arch Diagram](/uploads/xyz/arch.png){width=900 height=491}",
                f"Check out the diagram: ![Arch Diagram]({new_url})",
            ),
            (
                "Mixed GitLab uploads and external links",
                "Get the [Local Report](/uploads/rep.docx) or the [External Doc](https://example.com/ext.docx).",
                f"Get the [Local Report]({new_url}) or the [External Doc](https://example.com/ext.docx).",
            ),
            (
                "No links present",
                "Just plain text description with no attachments.",
                "Just plain text description with no attachments.",
            ),
        ]

        for name, input_desc, expected in test_cases:
            with self.subTest(name=name):
                with (
                    mock.patch(
                        MODULE_PATH + ".Migrator._download_embedded_file_from_gitlab"
                    ) as download,
                    mock.patch(MODULE_PATH + "._upload_file_to_vercel") as upload,
                ):
                    download.return_value = "image".encode("utf-8")
                    upload.return_value = new_url

                    params = make_migrator_params()
                    with Migrator(**params) as m:
                        m._gl_project = mock.MagicMock(spec=Project)

                        result = m._migrate_embedded_files(input_desc, "1")
                        self.assertEqual(result, expected)


class TestMigrateMentions(unittest.TestCase):

    def test_deactivate_unknown_mentions(self):
        test_cases = [
            (
                "Standard mention",
                "Hello @alice, welcome!",
                "Hello @\u200balice, welcome!",
            ),
            (
                "Multiple mentions",
                "@alice and @bob should look at this.",
                "@\u200balice and @\u200bbob should look at this.",
            ),
            (
                "Mention inside inline code block",
                "Run `git checkout @charlie` to see changes.",
                "Run `git checkout @charlie` to see changes.",
            ),
            (
                "Mention inside multi-line code block",
                "```\n# Debugging\n@dan where is this error?\n```",
                "```\n# Debugging\n@dan where is this error?\n```",
            ),
            (
                "Mixed content with mentions inside and outside code blocks",
                "Hey @elena, check this code:\n```\n@elena left this note\n```\nAlso pinging @frank.",
                "Hey @\u200belena, check this code:\n```\n@elena left this note\n```\nAlso pinging @\u200bfrank.",
            ),
            (
                "Ignore email addresses or false positives",
                "Contact support@gitlab.com or visit @gitlab.",
                "Contact support@gitlab.com or visit @\u200bgitlab.",
            ),
            (
                "Mention with special characters allowed in usernames",
                "Ping @user.name-123",
                "Ping @\u200buser.name-123",
            ),
        ]

        params = make_migrator_params()
        with Migrator(**params) as m:
            for description, input_text, expected in test_cases:
                with self.subTest(msg=description):
                    actual = m._migrate_mentions(input_text)
                    self.assertEqual(
                        actual,
                        expected,
                        f"\nFailed on: '{description}'\nExpected: {expected}\nGot: {actual}",
                    )

    def test_can_map_known_mentions(self):
        # given
        with tempfile.TemporaryDirectory() as temp_dir_str:
            params = make_migrator_params(cache_directory=temp_dir_str)
            with Migrator(**params) as m:
                m.user_mapping["alice"] = "alice2"
                input_text = "Hello @alice, welcome!"

                # when
                got = m._migrate_mentions(input_text)

                # then
                want = "Hello @alice2, welcome!"
                self.assertEqual(got, want)

    def test_should_add_unknown_users_to_list(self):
        # given
        with tempfile.TemporaryDirectory() as temp_dir_str:
            params = make_migrator_params(cache_directory=temp_dir_str)
            with Migrator(**params) as m:
                m.user_mapping["alice"] = "alice2"
                input_text = (
                    "Hello @alice and @bob! Hello @channel, @everyone, @here, @all"
                )

                # when
                m._migrate_mentions(input_text)

                # then
                self.assertSetEqual(set(m._unknown_users), {"bob"})
