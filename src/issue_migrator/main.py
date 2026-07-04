"""Main logic."""

import argparse
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import List, Optional

import gitlab
import requests
import vercel_blob
from github import Auth, Github
from github.GithubException import BadCredentialsException, UnknownObjectException
from github.Issue import Issue
from github.Repository import Repository
from gitlab.exceptions import GitlabAuthenticationError, GitlabGetError
from gitlab.v4.objects import Project, ProjectIssue, ProjectIssueNote

from . import __doc__ as package_doc

logger = logging.getLogger(__name__)

LABEL_MIGRATED = "source: gitlab"
GITLAB_PUBLIC_HOST = "https://gitlab.com"
REQUEST_TIMEOUT = 10  # seconds

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


@dataclass
class MigrationError(Exception):
    """A migration error."""

    message: str


class Migrator:
    """The issue migrator."""

    def __init__(
        self,
        gitlab_host: str,
        gitlab_repo: str,
        gitlab_token: str,
        github_repo: str,
        github_token: str,
        vercel_blob_token: str,
        is_dry_run: bool = True,
    ):
        self.gitlab_host = gitlab_host
        self.gitlab_repo = gitlab_repo
        self.gitlab_token = gitlab_token
        self.github_repo = github_repo
        self.github_token = github_token
        self.is_dry_run = is_dry_run
        self.vercel_blob_token = vercel_blob_token
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

    def connect(self):
        self._gl = gitlab.Gitlab(url=self.gitlab_host, private_token=self.gitlab_token)
        try:
            self._gl_project = self.gl.projects.get(self.gitlab_repo)
        except GitlabAuthenticationError as ex:
            raise MigrationError(message=f"GitLab token not valid: {ex}") from ex
        except GitlabGetError as ex:
            raise MigrationError(
                message=f"GitLab project not found: {self.gitlab_repo}"
            ) from ex

        logger.info(
            "Connected to GitLab project: %s (ID: %s)",
            self.gl_project.name_with_namespace,
            self.gl_project.id,
        )

        try:
            auth = Auth.Token(self.github_token)
            gh = Github(auth=auth)
            self._gh_repo = gh.get_repo(self.github_repo)

        except BadCredentialsException as ex:
            raise MigrationError(f"GitHub token invalid: {ex}") from ex

        except UnknownObjectException as ex:
            raise MigrationError(f"GitHub repo not found: {self.github_repo}") from ex

        logger.info(
            "Connected to GitHub repo: %s (ID: %d", self.gh_repo.name, self.gh_repo.id
        )

    def run(self):
        self._sync_labels()
        self._migrate_issues()

    def _sync_labels(self):
        """Ensure GL labels also exists on GH."""
        gl_labels = {label.name for label in self.gl_project.labels.list(iterator=True)}
        logger.debug("GL labels: %s", ", ".join(sorted(gl_labels)))

        gh_labels = {x.name for x in self.gh_repo.get_labels()}
        logger.debug("GH labels: %s", ", ".join(sorted(gh_labels)))

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
        description_2 = self._migrate_embedded_files(
            gl_issue.description, str(gl_issue.encoded_id)
        )
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
        logger.debug("Issue %s: %s", gl_issue.encoded_id, gl_issue.description)

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
        description_2 = self._migrate_embedded_files(
            gl_note.body, str(gl_issue.encoded_id)
        )
        formatted_comment = (
            f"> 📦 **Migrated Comment** "
            f"| **Author:** {author} "
            f"| **Date:** {gl_note.created_at} "
            f"| [Link]({comment_url}) \n\n"
            f"{description_2}"
        )
        logger.debug(
            "Note %s-%s: %s", gl_issue.encoded_id, gl_note.id, gl_issue.description
        )
        if gh_issue:
            gh_issue.create_comment(body=formatted_comment)

    def _migrate_embedded_files(self, text: str, issue_num: str) -> str:
        """Return a new text version where all private URLs for embedded files
        have been replaced with public URLs.
        Also removes size artifacts after image links.
        """
        if not text:
            return text

        pattern = r"!\[.*?\]\(((?:/uploads/|\.\./uploads/)[^\)]+)\)"
        matches: List[str] = re.findall(pattern, text)
        if matches:
            for rel_url in matches:
                text = self._migrate_embedded_file(text, issue_num, rel_url)

            text = _remove_image_sizes(text)

        pattern = r"(?<!!)\[[^\]]*\]\((/uploads/[^)]+)\)"
        matches: List[str] = re.findall(pattern, text)
        if matches:
            for rel_url in matches:
                text = self._migrate_embedded_file(text, issue_num, rel_url)

        return text

    def _migrate_embedded_file(self, text: str, issue_num: str, rel_url: str):
        filename = rel_url.split("/")[-1]

        data = _download_embedded_file_from_gitlab(
            self.gitlab_host,
            str(self.gl_project.encoded_id),
            rel_url,
            self.gitlab_token,
        )
        if not data:
            return text

        path = PurePath(self.github_repo) / issue_num / filename
        new_image_url = _upload_file_to_vercel(path, data, self.vercel_blob_token)
        if not new_image_url:
            return text

        text = text.replace(rel_url, new_image_url)
        return text


def _download_embedded_file_from_gitlab(
    host_url: str, project_id: str, rel_url: str, token: str
) -> bytes:
    """Download an embedded file from GitLab and return it.

    Return empty when there was an error.
    """
    url = f"{host_url}/-/project/{project_id}/{rel_url.lstrip('.')}"
    filename = rel_url.split("/")[-1]
    headers = {"PRIVATE-TOKEN": token}
    time.sleep(0.2)  # rate limit is 500 / minute
    response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)  # type: ignore
    logger.debug(
        "GitLab file download: GET %s %d %s",
        url,
        response.status_code,
        response.headers,
    )
    if not response.ok:
        logger.error(
            "Failed to download file %s from GitLab: %s %d %s",
            filename,
            url,
            response.status_code,
            response.text,
        )
        return bytes()

    image = response.content
    mime_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    logger.info("Downloaded file from GitLab: %s %s", filename, mime_type)
    return image


def _upload_file_to_vercel(path: PurePath, data: bytes, token: str) -> str:
    options = {"token": token, "addRandomSuffix": True}
    response = vercel_blob.put(
        path=str(path), data=data, timeout=REQUEST_TIMEOUT, options=options
    )
    logger.debug("Vercel file upload: %s %s", path, response)
    url = response.get("url") or ""
    if url:
        logger.info("Uploaded file to vercel: %s", url)
    return url


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


def _parse_args():
    parser = argparse.ArgumentParser(
        description=package_doc, formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    gitlab_host = os.environ.get("GITLAB_HOST")
    parser.add_argument(
        "--gitlab-host",
        default=gitlab_host or GITLAB_PUBLIC_HOST,
        help=(
            "URL of the GitLab host. "
            "Can also be set via environment variable: GITLAB_HOST"
        ),
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
    vercel_token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    parser.add_argument(
        "--vercel-blob-token",
        default=vercel_token,
        required=not vercel_token,
        help=(
            "Token for uploads to a vercel blop. "
            "Can also be set via environment variable: BLOB_READ_WRITE_TOKEN"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run through the migration without creating any objects on GitHub.",
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
    logging.basicConfig(
        format="{asctime} {levelname} {message}",
        style="{",
        datefmt="%Y/%m/%d %H:%M",
        level=target_level,
    )

    m = Migrator(
        gitlab_host=args.gitlab_host,
        gitlab_repo=args.gitlab_repo,
        gitlab_token=args.gitlab_token,
        github_repo=args.github_repo,
        github_token=args.github_token,
        vercel_blob_token=args.vercel_blob_token,
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
