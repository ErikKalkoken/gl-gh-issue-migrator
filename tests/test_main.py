import unittest
from unittest import mock

from gitlab.v4.objects import Project

from issue_migrator.main import Migrator

MODULE_PATH = "issue_migrator.main"


class TestMigrator_MigrateTextImages(unittest.TestCase):
    def test_can_replace_urls(self):
        # given
        m = Migrator(
            gitlab_project="",
            gitlab_token="",
            github_repo="",
            github_token="",
            imgpile_api_key="xxx",
        )
        m._gl_project = mock.MagicMock(spec=Project)
        text = (
            "before ![image](/uploads/903830a404b9289da9a6d6d0335a107b/image.png) after"
        )
        new_url = "https://www.new-url.com"
        want = f"before ![image]({new_url}) after"
        # when
        with (
            mock.patch(MODULE_PATH + "._download_image_from_gitlab") as download,
            mock.patch(MODULE_PATH + "._upload_image_to_ibb") as upload,
        ):
            download.return_value = "image".encode("utf-8")
            upload.return_value = new_url
            got = m._migrate_text_images(text)

        # then
        self.assertEqual(got, want)

    def test_can_pass_through_text_without_images(self):
        # given
        m = Migrator(
            gitlab_project="",
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
