import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import Dict, List, Optional

import gitlab
import requests
import vercel_blob
from github import Auth, Github
from github.GithubException import (
    BadCredentialsException,
    GithubException,
    UnknownObjectException,
)
from github.Issue import Issue
from github.Repository import Repository
from gitlab.exceptions import GitlabAuthenticationError, GitlabGetError
from gitlab.v4.objects import Project, ProjectIssue, ProjectIssueNote

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds
LABEL_MIGRATED = "source: gitlab"

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
        github_repo_name: str,
        github_token: str,
        gitlab_host: str,
        gitlab_repo_name: str,
        gitlab_token: str,
        is_dry_run: bool,
        issue_ids: List[int],
        no_close_issues: bool,
        no_user_validation: bool,
        user_mapping: Dict[str, str],
        vercel_blob_token: str,
    ):
        self.github_repo_name = github_repo_name
        self.github_token = github_token
        self.gitlab_host = gitlab_host
        self.gitlab_repo_name = gitlab_repo_name
        self.gitlab_token = gitlab_token
        self.is_dry_run = is_dry_run
        self.issue_ids = set(issue_ids or [])
        self.no_close_issues = no_close_issues
        self.no_user_validation = no_user_validation
        self.user_mapping = user_mapping
        self.vercel_blob_token = vercel_blob_token
        self._gl: Optional[gitlab.Gitlab] = None
        self._gl_project: Optional[Project] = None
        self._gh_repo: Optional[Repository] = None
        self._gh: Optional[Github] = None

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
    def gh(self) -> Github:
        if self._gh is None:
            raise RuntimeError("Not yet configured")
        return self._gh

    @property
    def gh_repo(self) -> Repository:
        if self._gh_repo is None:
            raise RuntimeError("Not yet configured")
        return self._gh_repo

    def connect(self):
        self._gl = gitlab.Gitlab(url=self.gitlab_host, private_token=self.gitlab_token)

        try:
            self._gl.auth()
            self._gl_project = self.gl.projects.get(self.gitlab_repo_name)

        except GitlabAuthenticationError as ex:
            raise MigrationError(message=f"GitLab token not valid: {ex}") from ex

        except GitlabGetError as ex:
            raise MigrationError(
                message=f"GitLab project not found: {self.gitlab_repo_name}"
            ) from ex

        logger.info(
            "Connected to GitLab project: %s (ID: %s) as %s",
            self.gl_project.name_with_namespace,
            self.gl_project.id,
            self._gl.user.username,  # type: ignore
        )

        try:
            auth = Auth.Token(self.github_token)
            self._gh = Github(auth=auth)
            gh_user = self._gh.get_user()
            self._gh_repo = self._gh.get_repo(self.github_repo_name)

        except BadCredentialsException as ex:
            raise MigrationError(f"GitHub token invalid: {ex}") from ex

        except UnknownObjectException as ex:
            raise MigrationError(
                f"GitHub repo not found: {self.github_repo_name}"
            ) from ex

        logger.info(
            "Connected to GitHub repo: %s (ID: %d) as %s",
            self.gh_repo.name,
            self.gh_repo.id,
            gh_user.login,
        )

    def run(self):
        if not self._validate_user_mappings():
            raise MigrationError("Some user mappings are not valid")

        self._sync_labels()
        self._migrate_issues()

    def _validate_user_mappings(self) -> bool:
        """Validates usernames in mapping against GitLab and GitHub server
        and reports whether they are valid."""

        if self.no_user_validation:
            logger.info("Skipped user validation")
            return True

        if not self.user_mapping:
            logger.info("No user mapping defined")
            return True

        for username in self.user_mapping.keys():
            users = self.gl.users.list(username=username, get_all=True)
            if len(users) == 0:
                logger.error("Unknown GitLab username: %s", username)
                return False

            user = users[0]
            logger.debug("Found valid GitLab user: %s, %s", username, user.name)

        for username in self.user_mapping.values():
            query = f"user:{username}"
            try:
                result = self.gh.search_users(query)
                if result.totalCount == 0:
                    logger.error("Unknown GitHub username: %s", username)
                    return False

            except GithubException:
                logger.error("Unknown GitHub username: %s", username)
                return False

            user = result[0]
            display_name = user.name if user.name else user.login
            logger.debug("Found valid GitHub user: %s, %s", username, display_name)

        logger.info("All user names are valid")

        return True

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
        if not issues.total:
            logger.warning("Found no issues to migrate")
            return

        migrated_count = 0
        skipped_count = 0
        failed_count = 0

        def progress_str() -> str:
            if not issues.total:
                return "[?]"
            p = round(
                (migrated_count + skipped_count + failed_count) / issues.total * 100, 0
            )
            return f"[{p:.0f}%]"

        logger.info("Found %d opened issue to migrate", issues.total)
        for gl_issue in issues:
            if self.issue_ids and gl_issue.iid not in self.issue_ids:
                skipped_count += 1
                logger.info(
                    "Skipping not included issue: %s %s",
                    _issue_str(gl_issue),
                    progress_str(),
                )
                continue

            try:
                if self._issue_exists(gl_issue):
                    skipped_count += 1
                    logger.warning(
                        "Skipping already migrated issue: %s %s",
                        _issue_str(gl_issue),
                        progress_str(),
                    )
                    continue

                self._migrate_issue(gl_issue)
            except Exception as ex:
                failed_count += 1
                logger.error(
                    "Failed to migrate issue %s: %s",
                    _issue_str(gl_issue),
                    ex,
                    exc_info=True,
                )
                continue

            migrated_count += 1
            logger.info(
                "Migrated GL issue %s: %s %s",
                _issue_str(gl_issue),
                gl_issue.title,
                progress_str(),
            )

        logger.info(
            "Finished processing %d issues: %d migrated, %d skipped, %d failed.",
            issues.total,
            migrated_count,
            skipped_count,
            failed_count,
        )

    def _issue_exists(self, gl_issue: ProjectIssue) -> bool:
        """Report whether an GitLab issue exists on GitHub."""
        search_string = _issue_str(gl_issue)
        query = f"{search_string} repo:{self.github_repo_name} type:issue state:open"
        found_issues = self.gh.search_issues(query)
        return found_issues.totalCount > 0

    def _migrate_issue(self, gl_issue: ProjectIssue):
        """Migrate an issue."""
        author = gl_issue.author.get("name") or "?"
        orig_labels = ", ".join(sorted(gl_issue.labels))
        description_2 = gl_issue.description or ""
        description_2 = self._migrate_embedded_files(
            description_2, str(gl_issue.encoded_id)
        )
        description_2 = _migrate_mentions(description_2, self.user_mapping)
        issue_str = _issue_str(gl_issue)
        issue_body = (
            f"> 📦 **Migrated from GitLab**\n"
            f"> **Original Issue:** [{issue_str}]({gl_issue.web_url})\n"
            f"> **Author:** {author}\n"
            f"> **Created At:** {gl_issue.created_at}\n"
            f"> **State at Migration:** {gl_issue.state}\n"
            f"> **Labels:** {orig_labels}\n\n"
            f"---\n\n"
            f"{description_2}"
        )
        logger.debug("Issue %s: %s", gl_issue.encoded_id, gl_issue.description)

        gl_assignees = [assignee.get("username") for assignee in gl_issue.assignees]
        gh_assignees = []
        for gl_user in gl_assignees:
            try:
                gh_user = self.user_mapping[gl_user]
            except KeyError:
                logger.warning("Skipping unknown assingee: %s %s", gl_user, issue_str)
                continue
            gh_assignees.append(gh_user)

        if not self.is_dry_run:
            params = {
                "title": gl_issue.title,
                "body": issue_body,
            }
            if gh_assignees:
                params["assignees"] = gh_assignees

            gh_issue = self.gh_repo.create_issue(**params)

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
                    "Failed to migrate note #%s for issue %s: %s",
                    gl_note.get_id(),
                    _issue_str(gl_issue),
                    ex,
                    exc_info=True,
                )
                continue

        if gh_issue and not self.no_close_issues:
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
        description_2 = _migrate_mentions(description_2, self.user_mapping)
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

        if not self.is_dry_run:
            data = _download_embedded_file_from_gitlab(
                self.gitlab_host,
                str(self.gl_project.encoded_id),
                rel_url,
                self.gitlab_token,
            )
            if not data:
                return text

            path = PurePath(self.github_repo_name) / issue_num / filename
            new_image_url = _upload_file_to_vercel(path, data, self.vercel_blob_token)
            if not new_image_url:
                return text
        else:
            new_image_url = f"migrated/{filename}"

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


def _migrate_mentions(text: str, user_mapping: Dict[str, str]) -> str:
    """Migrates @user mentions in GitLab descriptions
    and ignore any mentions found inside inline code, code blocks, or emails.
    Will substitutae when a mapping is given. Otherwise disable the mention.
    """
    # 1. Matches multi-line code blocks
    # 2. Matches inline code blocks
    # 3. Matches @username (ignoring mid-word/email @ symbols)
    pattern = r"(```[\s\S]*?```)|(`[^`\n]+?`)|(?<!\w)@([\w.-]+)"

    def replace(match):
        # If group 1 or group 2 matched, we are inside a code block. Return it as-is.
        if match.group(1) or match.group(2):
            return match.group(0)

        # Group 3 matched the user mention
        mention = match.group(3)

        if mention in user_mapping:
            mention = "@" + user_mapping[mention]

        else:
            mention = "@\u200b" + mention  # disables the mention
            logger.warning("Disabled mention for unmapped user: %s", mention)

        # Gitlab usernames cannot end in standard punctuation.
        # If a trailing period/comma/exclamation was caught, separate it.
        trailing_punctuation = ""
        while mention and mention[-1] in ".,!?":
            trailing_punctuation = mention[-1] + trailing_punctuation
            mention = mention[:-1]

        return f"{mention}{trailing_punctuation}"

    return re.sub(pattern, replace, text)


def _issue_str(issue: ProjectIssue) -> str:
    """Return string representation of a GitLab issue ID."""
    return f"GL-#{issue.iid:04d}"
