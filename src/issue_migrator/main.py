"""Main logic."""

import argparse
import logging
import os
import random
import re
import sys
from dataclasses import dataclass
from typing import List, Optional

import gitlab
import requests
from github import Auth, Github
from github.GithubException import BadCredentialsException, UnknownObjectException
from github.Issue import Issue
from github.Repository import Repository
from gitlab.exceptions import GitlabAuthenticationError, GitlabGetError
from gitlab.v4.objects import Project, ProjectIssue, ProjectIssueNote

from . import __doc__ as package_doc

logger = logging.getLogger(__name__)

LABEL_MIGRATED = "source: gitlab"
GITLAB_URL = "https://gitlab.com"
REQUEST_TIMEOUT = 5  # seconds

GITHUB_LABEL_COLORS = [  # spell-checker: disable
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


# Regex to find GitLab uploaded images
IMG_REGEX = r"!\[.*?\]\(((?:/uploads/|\.\./uploads/)[^\)]+)\)"


@dataclass
class MigrationError(Exception):
    """A migration error."""

    message: str


@dataclass
class DownloadedImage:
    """An image downloaded from GitLab."""

    bytes_data: bytes
    content_type: str
    filename: str


class Migrator:
    """The issue migrator."""

    def __init__(
        self,
        gitlab_repo: str,
        gitlab_token: str,
        github_repo: str,
        github_token: str,
        imgpile_api_key: str,
        is_dry_run: bool = True,
    ):
        self._gitlab_project = gitlab_repo
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
        self._gl = gitlab.Gitlab(url=GITLAB_URL, private_token=self._gitlab_token)
        try:
            self._gl_project = self.gl.projects.get(self._gitlab_project)
        except GitlabAuthenticationError as ex:
            raise MigrationError(message=f"GitLab token not valid: {ex}") from ex
        except GitlabGetError as ex:
            raise MigrationError(
                message=f"GitLab project not found: {self._gitlab_project}"
            ) from ex

        logger.info(
            "Connected to GitLab project: %s (ID: %s)",
            self.gl_project.name_with_namespace,
            self.gl_project.id,
        )

        try:
            auth = Auth.Token(self._github_token)
            gh = Github(auth=auth)
            self._gh_repo = gh.get_repo(self._github_repo)

        except BadCredentialsException as ex:
            raise MigrationError(f"GitHub token invalid: {ex}") from ex

        except UnknownObjectException as ex:
            raise MigrationError(f"GitHub repo not found: {self._github_repo}") from ex

        logger.info(
            "Connected to GitHub repo: %s (ID: %d", self.gh_repo.name, self.gh_repo.id
        )

    def run(self):
        self._sync_labels()
        self._migrate_issues()

    def _sync_labels(self):
        """Ensure GL labels also exists on GH."""
        gl_labels = {label.name for label in self.gl_project.labels.list(iterator=True)}
        gh_labels = {x.name for x in self.gh_repo.get_labels()}

        if LABEL_MIGRATED not in gh_labels:
            if not self.is_dry_run:
                self.gh_repo.create_label(
                    LABEL_MIGRATED,
                    random.choice(GITHUB_LABEL_COLORS),
                    description="This issue was migrated from GitLab",
                )
                logger.info("Created missing label: %s", LABEL_MIGRATED)
            else:
                logger.info("Label missing: %s", LABEL_MIGRATED)

        has_missing = False
        for label in gl_labels:
            if label in gh_labels:
                continue

            has_missing = True
            if not self.is_dry_run:
                self.gh_repo.create_label(
                    label,
                    random.choice(GITHUB_LABEL_COLORS),
                    description="This label was migrated from GitLab",
                )
                logger.info("Created missing label: %s", label)
            else:
                logger.info("Label missing: %s", label)

        if not has_missing:
            logger.info("Labels are in sync")

    def _migrate_issues(self):
        """Migrate all issues of a project."""
        issues = self.gl_project.issues.list(
            state="opened",
            order_by="created_at",
            sort="asc",
            iterator=True,
        )
        logger.info("Found %d opened issue to migrate", issues.total)
        done_count = 0
        for gl_issue in issues:
            try:
                self._migrate_issue(gl_issue)
            except Exception as ex:
                logger.error(
                    "Failed to migrate issue #%s: %s", gl_issue.iid, ex, exc_info=True
                )
                continue

            done_count += 1
            logger.info(
                "Migrated GL issue #%s: %s [%d / %d]",
                gl_issue.iid,
                gl_issue.title,
                done_count,
                issues.total,
            )

    def _migrate_issue(self, gl_issue: ProjectIssue):
        author = gl_issue.author.get("name") or "?"
        orig_labels = ", ".join(sorted(gl_issue.labels))
        description_2 = self._migrate_text_images(gl_issue.description)
        issue_body = (
            f"> 📦 **Migrated from GitLab**\n"
            f"> **Original Issue:** [GL-#{gl_issue.iid}]({gl_issue.web_url})\n"
            f"> **Author:** {author}\n"
            f"> **Created At:** {gl_issue.created_at}\n"
            f"> **State at Migration:** {gl_issue.state}\n"
            f"> **Labels:** {orig_labels}\n\n"
            f"---\n\n"
            f"{description_2}"
        )
        if not self.is_dry_run:
            gh_issue = self.gh_repo.create_issue(title=gl_issue.title, body=issue_body)

            gh_issue.add_to_labels(LABEL_MIGRATED)
            for label in gl_issue.labels:
                gh_issue.add_to_labels(label)

        else:
            gh_issue = None

        notes = gl_issue.notes.list(iterator=True, sort="asc")
        for gl_note in notes:
            if gl_note.system:
                continue

            try:
                self._migrate_note(gl_issue, gh_issue, gl_note)
            except Exception as ex:
                logger.error(
                    "Failed to migrate note #%s for issue #%s: %s",
                    gl_note.get_id(),
                    gl_issue.get_id(),
                    ex,
                    exc_info=True,
                )
                continue

        if gh_issue:
            migration_note = (
                "📦 **Issue Transferred**\n\n"
                "This issue has been moved to a new repository: "
                f"[GitHub Issue #{gh_issue.number}]({gh_issue.html_url})\n\n"
                "We are closing this thread to keep the discussion centralized."
            )
            gl_issue.notes.create({"body": migration_note})
            gl_issue.state_event = "close"
            gl_issue.save()

    def _migrate_note(
        self,
        gl_issue: ProjectIssue,
        gh_issue: Optional[Issue],
        gl_note: ProjectIssueNote,
    ):
        author = gl_note.author.get("name") or "?"
        comment_url: str = f"{gl_issue.web_url}#note_{gl_note.id}"
        description_2 = self._migrate_text_images(gl_note.body)
        formatted_comment = (
            f"> 📦 **Migrated Comment** "
            f"| **Author:** {author} "
            f"| **Date:** {gl_note.created_at} "
            f"| [Link]({comment_url}) \n\n"
            f"{description_2}"
        )
        if gh_issue:
            gh_issue.create_comment(body=formatted_comment)

    def _migrate_text_images(self, text: str) -> str:
        """Return a new text version where all private URLs for images
        have been replaced with public URLs. Also removes size artifacts.
        """
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

            # Replace the relative GitLab link with the permanent repository URL
            text = text.replace(rel_url, new_image_url)

        return _remove_image_sizes(text)


def _download_image_from_gitlab(
    gl: gitlab.Gitlab, gl_project: Project, rel_url: str
) -> Optional[DownloadedImage]:
    """Download an image file from GitLab and return it.

    Return empty when there was an error.
    """
    gl_img_url = f"{gl.url}/-/project/{gl_project.encoded_id}/{rel_url.lstrip('.')}"
    filename = rel_url.split("/")[-1]
    headers = {"PRIVATE-TOKEN": gl.private_token}
    response = requests.get(gl_img_url, headers=headers, timeout=REQUEST_TIMEOUT)  # type: ignore
    logger.debug(
        "GitLab image download: GET %s %d %s",
        gl_img_url,
        response.status_code,
        response.headers,
    )
    if not response.ok:
        logger.error(
            "Failed to download image %s from GitLab: %s %d %s",
            filename,
            gl_img_url,
            response.status_code,
            response.text,
        )
        return None

    img_bytes = response.content
    mime_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    image = DownloadedImage(
        filename=filename, content_type=mime_type, bytes_data=img_bytes
    )

    logger.info("Downloaded image from GitLab: %s", image.filename)
    return image


def _upload_image_to_imgpile(
    image: DownloadedImage, filename: str, api_key: str
) -> str:
    """Upload image to Imgpile and return it's direct URL.

    Return empty when there was an error.
    """
    url = "https://cdn.imgpile.com/api/v1/media"

    headers = {"Authorization": f"Bearer {api_key}"}

    # Using a tuple to pass raw bytes directly
    files = {"file": (image.filename, image.bytes_data, image.content_type)}
    response = requests.post(url, headers=headers, files=files, timeout=REQUEST_TIMEOUT)
    logger.debug(
        "Image upload imgPile: POST %s %d %s %s",
        url,
        response.status_code,
        response.text,
        response.headers,
    )
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


def _remove_image_sizes(markdown_text: str) -> str:
    """
    Removes optional size parameters like {width=900 height=491} from markdown image links
    while leaving code blocks (and any image-like syntax inside them) completely untouched.
    """
    # Pattern matches either:
    # Group 1: A markdown code block (``` ... ```)
    # Group 2: A standard markdown image reference (![...](...))
    # Group 3: Optional trailing size parameters wrapped in curly braces ({...})
    pattern = r"(```[\s\S]*?```)|(!\[.*?\]\(.*?\))\s*(\{[^}]*\})"

    def replacer(match):
        # If Group 1 matches, it means we are inside a code block. Return it exactly as is.
        if match.group(1):
            return match.group(1)

        # If Group 2 matches, return just the image markdown, discarding the curly braces (Group 3).
        return match.group(2)

    return re.sub(pattern, replacer, markdown_text)


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


def _parse_args():
    parser = argparse.ArgumentParser(
        description=package_doc, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run through the migration without creating any objects on GitHub.",
    )
    parser.add_argument(
        "--gitlab-repo",
        required=True,
        help="Name of the GitLab repository, e.g. ErikKalkoken/aa-structures",
    )
    gitlab_token = os.environ.get("GITLAB_TOKEN")
    parser.add_argument(
        "--gitlab-token",
        default=gitlab_token,
        required=not gitlab_token,
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
    github_token = os.environ.get("GITHUB_TOKEN")
    parser.add_argument(
        "--github-token",
        default=github_token,
        required=not github_token,
        help=(
            "Personal access token for GitHub. "
            "Can also be set via environment variable: GITHUB_TOKEN"
        ),
    )
    imgpile_api_key = os.environ.get("IMGPILE_API_KEY")
    parser.add_argument(
        "--imgpile-api-key",
        default=imgpile_api_key,
        required=not imgpile_api_key,
        help=(
            "API key for uploads to imgpile. "
            "Can also be set via environment variable: IMGPILE_API_KEY"
        ),
    )
    parser.add_argument(
        "--log-level",
        choices=logging.getLevelNamesMapping().keys(),
        default="INFO",
        help=("Set log level"),
    )
    args = parser.parse_args()
    return args


def main_cli():
    """Main program for running this script."""
    args = _parse_args()

    level_mapping = logging.getLevelNamesMapping()
    target_level = level_mapping.get(args.log_level.upper(), logging.INFO)
    logger.setLevel(target_level)
    logger.propagate = False
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="{asctime} {levelname} {message}", style="{", datefmt="%Y/%m/%d %H:%M"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    m = Migrator(
        gitlab_repo=args.gitlab_repo,
        gitlab_token=args.gitlab_token,
        github_repo=args.github_repo,
        github_token=args.github_token,
        imgpile_api_key=args.imgpile_api_key,
        is_dry_run=args.dry_run,
    )

    try:
        m.connect()
        m.run()
    except MigrationError as ex:
        logger.critical("Migration error: %s", ex.message)
        sys.exit(1)

    if m.is_dry_run:
        logger.info("Dry Run completed!")
    else:
        logger.info("Migration completed!")


if __name__ == "__main__":
    main_cli()
