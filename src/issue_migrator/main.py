import argparse
import logging
import os
import random
import re
from dataclasses import dataclass
from typing import List, Optional

import gitlab
import requests
from github import Auth, Github
from github.Repository import Repository
from gitlab.v4.objects import Project

LABEL_MIGRATED = "source: gitlab"

GITHUB_LABEL_COLORS = [
    # Reds & Pinks
    "ff4444",
    "cf222e",
    "e11d48",
    "f43f5e",
    "ff8585",
    "d73a4a",
    "ff7b72",
    "f25f5c",
    "fbbf24",
    "ff0055",
    # Oranges & Yellows
    "d97706",
    "f59e0b",
    "e67e22",
    "ff9f43",
    "ffaa00",
    "bf5af2",
    "e3b341",
    "f97316",
    "f4b400",
    "dfb317",
    # Greens
    "2ea44f",
    "1a7f37",
    "22c55e",
    "10b981",
    "56f287",
    "008000",
    "34d399",
    "2ecc71",
    "70e000",
    "00af91",
    # Blues & Cyans
    "0564d4",
    "0969da",
    "1f6feb",
    "38bdf8",
    "06b6d4",
    "2196f3",
    "00bcd4",
    "79c0ff",
    "539bf5",
    "2d8cf0",
    # Purples & Grays
    "8250df",
    "a855f7",
    "c084fc",
    "d8b4fe",
    "bc8cff",
    "6e7681",
    "8b949e",
    "484f58",
    "30363d",
    "a370f7",
]


logging.basicConfig(
    format="{asctime} {levelname} {message}",
    style="{",
    datefmt="%Y/%m/%d %H:%M",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Regex to find GitLab uploaded images
IMG_REGEX = r"!\[.*?\]\(((?:/uploads/|\.\./uploads/)[^\)]+)\)"


@dataclass
class MigrationError(Exception):
    message: str


class Migrator:
    def __init__(
        self,
        gitlab_project: str,
        gitlab_token: str,
        github_repo: str,
        github_token: str,
        imgpile_api_key: str,
        is_dry_run: bool = True,
    ):
        self._gitlab_project = gitlab_project
        self._gitlab_token = gitlab_token
        self._github_repo = github_repo
        self._github_token = github_token
        self._imgpile_api_key = imgpile_api_key
        self.is_dry_run = is_dry_run
        self._gl: Optional[gitlab.Gitlab] = None
        self._gl_project: Optional[Project] = None
        self._gh_repo: Optional[Repository] = None

    @property
    def gl(self) -> gitlab.Gitlab:
        if self._gl is None:
            raise RuntimeError("Not yet configured")
        return self._gl

    @property
    def gl_project(self) -> Project:
        if self._gl_project is None:
            raise RuntimeError("Not yet configured")
        return self._gl_project

    @property
    def gh_repo(self) -> Repository:
        if self._gh_repo is None:
            raise RuntimeError("Not yet configured")
        return self._gh_repo

    @property
    def imgpile_api_key(self) -> str:
        if self._imgpile_api_key is None:
            raise RuntimeError("Not yet configured")
        return self._imgpile_api_key

    def connect(self):
        self._gl = gitlab.Gitlab(
            url="https://gitlab.com", private_token=self._gitlab_token
        )
        self._gl_project = self.gl.projects.get(self._gitlab_project)
        logger.info(
            "Connected to GitLab project: %s (ID: %s)",
            self.gl_project.name_with_namespace,
            self.gl_project.id,
        )

        auth = Auth.Token(self._github_token)
        self.gh = Github(auth=auth)
        self._gh_repo = self.gh.get_repo(self._github_repo)
        logger.info(
            "Connected to GitHub repo: %s (ID: %d", self.gh_repo.name, self.gh_repo.id
        )

    def run(self):
        self._sync_labels()
        self._migrate_issues()

    def _sync_labels(self):
        """Ensure GL labels also exists on GH."""
        gl_labels: List[str] = [LABEL_MIGRATED]
        issues = self.gl_project.issues.list(iterator=True)
        for issue in issues:
            for label in issue.labels:
                gl_labels.append(label)

        gh_labels = {x.name for x in self.gh_repo.get_labels()}
        has_missing = False
        for label in gl_labels:
            if label in gh_labels:
                continue
            has_missing = True
            if not self.is_dry_run:
                self.gh_repo.create_label(
                    label,
                    random.choice(GITHUB_LABEL_COLORS),
                    description="Migrated from GitLab",
                )
                logger.info("Created missing label: %s", label)

        if not has_missing:
            logger.info("Labels are in sync")

    def _migrate_issues(self):
        issues = self.gl_project.issues.list(
            state="opened",
            order_by="created_at",
            sort="asc",
            iterator=True,
        )
        logger.info("Found %d opened issue to migrate", issues.total)
        done_count = 0
        for gl_issue in issues:
            author = gl_issue.author.get("name") or "?"
            orig_labels = ", ".join(sorted(gl_issue.labels))
            issue_body = (
                f"> 🚚 **Migrated from GitLab**\n"
                f"> **Original Issue:** [GL-#{gl_issue.iid}]({gl_issue.web_url})\n"
                f"> **Author:** {author}\n"
                f"> **Created At:** {gl_issue.created_at}\n"
                f"> **State at Migration:** {gl_issue.state}\n"
                f"> **Labels:** {orig_labels}\n\n"
                f"---\n\n"
                f"### Original Description\n"
                f"{gl_issue.description}"
            )
            if not self.is_dry_run:
                gh_issue = self.gh_repo.create_issue(
                    title=gl_issue.title, body=issue_body
                )

                gh_issue.add_to_labels(LABEL_MIGRATED)
                for label in gl_issue.labels:
                    gh_issue.add_to_labels(label)

            notes = gl_issue.notes.list(iterator=True, sort="asc")
            for gl_note in notes:
                if gl_note.system:
                    continue

                author = gl_note.author.get("name") or "?"
                comment_url: str = f"{gl_issue.web_url}#note_{gl_note.id}"
                body_2 = self._migrate_text_images(gl_note.body)
                formatted_comment = (
                    f"> 🚚 **Migrated Comment** "
                    f"| **Author:** {author} "
                    f"| **Date:** {gl_note.created_at} "
                    f"| [Link]({comment_url}) \n\n"
                    f"{body_2}"
                )
                if not self.is_dry_run:
                    gh_issue.create_comment(body=formatted_comment)

            done_count += 1
            logger.info(
                "Migrated GL issue #%s: %s [%d / %d]",
                gl_issue.iid,
                gl_issue.title,
                done_count,
                issues.total,
            )

    def _migrate_text_images(self, text: str):
        if not text:
            return text

        matches: List[str] = re.findall(IMG_REGEX, text)
        for rel_url in matches:
            filename = rel_url.split("/")[-1]

            image = _download_image_from_gitlab(self.gl, self.gl_project, rel_url)
            if not image:
                continue

            new_image_url = _upload_image_to_imgpile(
                image, filename, self.imgpile_api_key
            )
            if not new_image_url:
                continue

            # Replace the relative GitLab link with the permanent imgBB URL
            text = text.replace(rel_url, new_image_url)

        return text


def _download_image_from_gitlab(
    gl: gitlab.Gitlab, gl_project: Project, rel_url: str
) -> bytes:
    # gl_img_url = f"{gl.url}/{gl_project.path_with_namespace}{rel_url.lstrip('.')}"
    # https://gitlab.com/-/project/83987261/uploads/bc9742193bd73dd1b45aa5619e5fab4c/cheese_cake.webp
    # https://gitlab.com/ErikKalkoken/alpha-dummy/uploads/bc9742193bd73dd1b45aa5619e5fab4c/cheese_cake.webp
    gl_img_url = f"{gl.url}/-/project/{gl_project.encoded_id}/{rel_url.lstrip('.')}"
    filename = rel_url.split("/")[-1]
    headers = {"PRIVATE-TOKEN": gl.private_token}
    response = requests.get(gl_img_url, headers=headers)  # type: ignore

    if not response.ok:
        logger.error(
            "Failed to download image %s from GitLab: %d %s %s",
            filename,
            gl_img_url,
            response.status_code,
            response.text,
        )
        return bytes()

    logger.info("Downloaded image from GitLab: %s", filename)
    return response.content


def _upload_image_to_imgpile(image_bytes: bytes, filename: str, api_key: str) -> str:
    """Uploads image to Imgpile and return URL."""
    url = "https://cdn.imgpile.com/api/v1/media"

    headers = {"Authorization": f"Bearer {api_key}"}

    # Using a tuple to pass raw bytes directly
    files = {"file": (filename, image_bytes, "image/jpeg")}
    response = requests.post(url, headers=headers, files=files)
    if not response.ok:
        logger.error(
            "imgpile upload failed for %s: %s %s",
            filename,
            response.status_code,
            response.text,
        )
        return ""

    response_data = response.json()
    logger.debug("response: %s", response_data)
    media = response_data["media"]
    filename_2 = media["filename"]
    ext_2 = media["ext"]
    url_2 = f"https://cdn.imgpile.com/f/{filename_2}_xl.{ext_2}"
    logger.info("Uploaded image to imgpile: %s %s", filename, url_2)
    return url_2


# def _upload_image_to_ibb(image_bytes: bytes, filename: str, api_key: str) -> str:
#     """Uploads binary image data to imgBB and returns the direct image URL.

#     Return URL of uploaded image on success or empty when upload failed..
#     """
#     url = "https://api.imgbb.com/1/upload"
#     data = {"key": api_key, "name": filename}
#     files = {"image": (filename, image_bytes)}
#     response = requests.post(url, data=data, files=files)
#     if not response.ok:
#         logger.error(
#             "imgBB upload failed for %s: %s %s",
#             filename,
#             response.status_code,
#             response.text,
#         )
#         return ""

#     res_json = response.json()
#     url = res_json["data"]["url"]
#     logger.info("Uploaded image to imgBB: %s", url)
#     return url


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run through the migration without creating any objects on GitHub.",
    )
    parser.add_argument(
        "--gitlab-project",
        required=True,
        help="Name of the GitLab project, e.g. ErikKalkoken/aa-structures",
    )
    parser.add_argument(
        "--gitlab-token",
        default=os.environ.get("GITLAB_TOKEN"),
        required=True,
        help=(
            "Personal access token for GitLab. "
            "Can also be set via environment variable: GITLAB_TOKEN"
        ),
    )
    parser.add_argument(
        "--github-repo",
        required=True,
        help="Name of the GitHub repository, e.g. ErikKalkoken/aa-structures",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_TOKEN"),
        required=True,
        help=(
            "Personal access token for GitHub. "
            "Can also be set via environment variable: GITHUB_TOKEN"
        ),
    )
    parser.add_argument(
        "--imgpile-api-key",
        required=True,
        default=os.environ.get("IMGPILE_API_KEY"),
        help=(
            "API key for uploads to imgpile. "
            "Can also be set via environment variable: IMGPILE_API_KEY"
        ),
    )
    args = parser.parse_args()

    m = Migrator(
        gitlab_project=args.gitlab_project,
        gitlab_token=args.gitlab_token,
        github_repo=args.github_repo,
        github_token=args.github_token,
        imgpile_api_key=args.imgpile_api_key,
        is_dry_run=args.dry_run,
    )

    m.connect()

    try:
        m.run()
    except MigrationError as ex:
        logger.error("Migration error: " + ex.message)
        exit(1)

    logger.info("Migration completed!")


if __name__ == "__main__":
    main()
