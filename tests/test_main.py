import unittest
from unittest import mock

import pook
from gitlab import Gitlab
from gitlab.v4.objects import Project

from issue_migrator.main import (
    DownloadedImage,
    Migrator,
    _download_image_from_gitlab,
    _remove_image_sizes,
)

MODULE_PATH = "issue_migrator.main"


class TestMigrator_MigrateTextImages(unittest.TestCase):
    def test_can_replace_urls(self):
        # given
        m = Migrator(
            gitlab_host="",
            gitlab_repo="",
            gitlab_token="",
            github_repo="",
            github_token="",
            imgpile_api_key="xxx",
        )
        m._gl = mock.MagicMock(spec=Gitlab)
        m._gl_project = mock.MagicMock(spec=Project)
        text = (
            "before ![image](/uploads/903830a404b9289da9a6d6d0335a107b/image.png) after"
        )
        new_url = "https://www.new-url.com"
        want = f"before ![image]({new_url}) after"
        # when
        with (
            mock.patch(MODULE_PATH + "._download_image_from_gitlab") as download,
            mock.patch(MODULE_PATH + "._upload_image_to_imgpile") as upload,
        ):
            download.return_value = DownloadedImage(
                data="image".encode("utf-8"), content_type="xxx", filename="name"
            )
            upload.return_value = new_url
            got = m._migrate_text_images(text)

        # then
        self.assertEqual(got, want)

    def test_can_pass_through_text_without_images(self):
        # given
        m = Migrator(
            gitlab_host="",
            gitlab_repo="",
            gitlab_token="",
            github_repo="",
            github_token="",
            imgpile_api_key="xxx",
        )
        text = "dummy"

        # when
        got = m._migrate_text_images("dummy")

        # then
        self.assertEqual(got, text)


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

    def setUp(self):
        # Mocking the gitlab.Gitlab client object
        self.mock_gl = mock.MagicMock()
        self.mock_gl.url = "https://gitlab.example.com"
        self.mock_gl.private_token = "fake-token-123"

        # Mocking the gitlab Project object
        self.mock_project = mock.MagicMock()
        self.mock_project.encoded_id = "my-group%2Fmy-project"

        self.rel_url = "images/avatar.png"
        # The expected generated URL inside the function
        self.expected_url = "https://gitlab.example.com/-/project/my-group%2Fmy-project/images/avatar.png"

    @pook.on
    def test_download_image_success(self):
        """Test successful image download."""
        fake_binary_data = b"fake-jpeg-bytes"

        (
            pook.get(self.expected_url)
            .reply(200)
            .header("Content-Type", "image/jpeg")
            # No Content-Disposition header provided
            .body(fake_binary_data)
        )

        result = _download_image_from_gitlab(
            self.mock_gl, self.mock_project, self.rel_url
        )

        self.assertIsNotNone(result)
        assert result is not None
        # Should fall back to the end of the URL path ("avatar.png")
        self.assertEqual(result.filename, "avatar.png")
        self.assertEqual(result.content_type, "image/jpeg")

    @pook.on
    def test_download_image_failure(self):
        """Test that the function returns None and logs error when GitLab returns non-200."""
        (pook.get(self.expected_url).reply(404).body("Not Found"))

        result = _download_image_from_gitlab(
            self.mock_gl, self.mock_project, self.rel_url
        )

        # Assert that it gracefully handled the failure and returned None
        self.assertIsNone(result)
