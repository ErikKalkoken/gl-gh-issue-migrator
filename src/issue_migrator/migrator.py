import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import PurePath
from typing import Dict, List, Optional, Set

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
from rich.progress import Progress, track

from . import messages

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
        no_labels: bool,
        no_migration: bool,
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
        self.no_labels = no_labels
        self.no_migration = no_migration
        self.no_user_validation = no_user_validation
        self.user_mapping = user_mapping
        self.vercel_blob_token = vercel_blob_token
        self._gl: Optional[gitlab.Gitlab] = None
        self._gl_project: Optional[Project] = None
        self._gh_repo: Optional[Repository] = None
        self._gh: Optional[Github] = None
        self._unknown_users: Set[str] = set()

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

        messages.notice(
            f"Connected to GitLab project: {self.gl_project.name_with_namespace} "
            f"(ID: {self.gl_project.id}) as {self._gl.user.username}"  # type: ignore
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

        messages.notice(
            f"Connected to GitHub repo: {self.gh_repo.name} (ID: {self.gh_repo.id}) "
            f"as {gh_user.login}"
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
            messages.notice("Skipped user validation")
            return True

        if not self.user_mapping:
            messages.notice("No user mapping defined")
            return True

        gl_ok = True
        with Progress(transient=True) as progress:
            task = progress.add_task(
                "Validating GitLab users...", total=len(self.user_mapping.keys())
            )
            for username in self.user_mapping.keys():
                users = self.gl.users.list(username=username, get_all=True)
                if len(users) == 0:
                    gl_ok = False
                    messages.error(
                        f"Unknown GitLab username: {username}", progress.console
                    )
                    progress.advance(task)
                    continue

                progress.advance(task)

        if gl_ok:
            messages.success("GitLab usernames are valid")
        else:
            messages.notice("Completed validating GitLab usernames")

        gh_ok = True
        with Progress() as progress:
            task = progress.add_task(
                "Validating GitHub users...", total=len(self.user_mapping.values())
            )
            for username in self.user_mapping.values():
                query = f"user:{username}"
                try:
                    result = self.gh.search_users(query)
                    if result.totalCount == 0:
                        gh_ok = False
                        messages.error(
                            f"Unknown GitHub username: {username}", progress.console
                        )
                        progress.advance(task)
                        continue

                except GithubException:
                    gh_ok = False
                    messages.error(
                        f"Unknown GitHub username: {username}", progress.console
                    )
                    progress.advance(task)
                    continue

                progress.advance(task)

        if gh_ok:
            messages.success("GitHub usernames are valid")
        else:
            messages.notice("Completed validating GitHub usernames")

        return gh_ok and gl_ok

    def _sync_labels(self):
        """Check for missing labels in GitHub repo and add them when missing."""
        if self.no_labels:
            messages.notice("User requested to skip label sync")
            return

        gh_labels = {x.name for x in self.gh_repo.get_labels()}
        # messages.notice(f"GH labels: {', '.join(sorted(gh_labels))}")

        if LABEL_MIGRATED not in gh_labels:
            if not self.is_dry_run:
                self.gh_repo.create_label(
                    LABEL_MIGRATED,
                    random.choice(GITHUB_LABEL_COLORS),
                    description="This issue was migrated from GitLab",
                )
                messages.notice(f"Created missing label: {LABEL_MIGRATED}")
            else:
                messages.notice(f"Label missing: {LABEL_MIGRATED}")

        gl_labels = {label.name for label in self.gl_project.labels.list(iterator=True)}
        # messages.notice(f"GL labels: {', '.join(sorted(gl_labels))}")

        missing_labels = gl_labels - gh_labels
        if not missing_labels:
            messages.notice("Labels are in sync")
            return

        names = ", ".join(sorted(missing_labels))
        messages.info(f"Missing labels: {names}")
        if self.is_dry_run:
            return

        for label in track(
            missing_labels, description="Creating missing labels...", transient=True
        ):
            self.gh_repo.create_label(
                label,
                random.choice(GITHUB_LABEL_COLORS),
                description="This label was migrated from GitLab",
            )

        messages.success("Created missing labels")

    def _migrate_issues(self):
        """Migrate all issues of a project."""

        if self.no_migration:
            messages.notice("User requested to skip migration")
            return

        issues = self.gl_project.issues.list(
            state="opened",
            order_by="created_at",
            sort="asc",
            iterator=True,
        )
        if not issues.total:
            messages.warning("Found no issues to migrate")
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
            return f"\\[{p:.0f}%]"

        messages.info(f"Found {issues.total} opened issue to migrate")
        for gl_issue in issues:
            if self.issue_ids and gl_issue.iid not in self.issue_ids:
                skipped_count += 1
                messages.notice(
                    f"Skipping not included issue: {_issue_str(gl_issue)} {progress_str()}"
                )
                continue

            try:
                if self._issue_exists(gl_issue):
                    skipped_count += 1
                    messages.warning(
                        f"Skipping already migrated issue: {_issue_str(gl_issue)} {progress_str()}"
                    )
                    continue

                self._migrate_issue(gl_issue)
            except Exception as ex:
                failed_count += 1
                messages.error(f"Failed to migrate issue {_issue_str(gl_issue)}: {ex}")
                continue

            migrated_count += 1
            messages.success(
                f"Migrated GL issue {_issue_str(gl_issue)}: {gl_issue.title} {progress_str()}"
            )

        messages.info(
            f"Finished processing {issues.total} issues: {migrated_count} migrated, "
            f"{skipped_count} skipped, {failed_count} failed."
        )
        if self._unknown_users:
            messages.warning(f"Unkown users: {', '.join(sorted(self._unknown_users))}")
        else:
            messages.notice("No unknown users")

    def _issue_exists(self, gl_issue: ProjectIssue) -> bool:
        """Report whether an GitLab issue exists on GitHub."""
        search_string = _issue_str(gl_issue)
        query = f"{search_string} repo:{self.github_repo_name} type:issue state:open"
        found_issues = self.gh.search_issues(query)
        return found_issues.totalCount > 0

    def _migrate_issue(self, gl_issue: ProjectIssue):
        """Migrate an issue."""
        author = self._map_author(gl_issue.author)
        orig_labels = ", ".join(sorted(gl_issue.labels))
        description_2 = gl_issue.description or ""
        description_2 = self._migrate_embedded_files(
            description_2, str(gl_issue.encoded_id)
        )
        description_2 = self._migrate_mentions(description_2)
        issue_str = _issue_str(gl_issue)
        issue_body = (
            f"> 逃 **Migrated from GitLab**\n"
            f"> **Original Issue:** \\[{issue_str}]({gl_issue.web_url})\n"
            f"> **Author:** {author}\n"
            f"> **Created At:** {gl_issue.created_at}\n"
            f"> **State at Migration:** {gl_issue.state}\n"
            f"> **Labels:** {orig_labels}\n\n"
            f"---\n\n"
            f"{description_2}"
        )

        gl_assignees = [assignee.get("username") for assignee in gl_issue.assignees]
        gh_assignees = []
        for gl_username in gl_assignees:
            try:
                gh_username = self.user_mapping[gl_username]
            except KeyError:
                messages.warning(
                    f"Skipping unknown assingee: {gl_username} {issue_str}"
                )
                self._unknown_users.add(gl_username)
                continue

            gh_assignees.append(gh_username)

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
                messages.error(
                    f"Failed to migrate note #{gl_note.get_id()} "
                    f"for issue {_issue_str(gl_issue)}: {ex}",
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
        author = self._map_author(gl_note.author)
        comment_url: str = f"{gl_issue.web_url}#note_{gl_note.id}"
        description_2 = self._migrate_embedded_files(
            gl_note.body, str(gl_issue.encoded_id)
        )
        description_2 = self._migrate_mentions(description_2)
        formatted_comment = (
            f"> 📦 **Migrated Comment** "
            f"| **Author:** {author} "
            f"| **Date:** {gl_note.created_at} "
            f"| [Link]({comment_url}) \n\n"
            f"{description_2}"
        )
        if gh_issue:
            gh_issue.create_comment(body=formatted_comment)

    def _map_author(self, author):
        username = author.get("username")
        if username in self.user_mapping:
            display = "@" + self.user_mapping[username]
        else:
            messages.warning(f"No mapping found for GL user: {username}")
            display = author.get("name") or "?"
            self._unknown_users.add(username)

        return display

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

    def _migrate_mentions(self, text: str) -> str:
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
            username = match.group(3)

            # Gitlab usernames cannot end in standard punctuation.
            # If a trailing period/comma/exclamation was caught, separate it.
            trailing_punctuation = ""
            while username and username[-1] in ".,!?":
                trailing_punctuation = username[-1] + trailing_punctuation
                username = username[:-1]

            if username in self.user_mapping:
                mention = "@" + self.user_mapping[username]

            else:
                mention = "@\u200b" + username  # disables the mention
                messages.warning(f"Disabled mention for unknown user: {username}")
                self._unknown_users.add(username)

            return f"{mention}{trailing_punctuation}"

        return re.sub(pattern, replace, text)


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
    # messages.debug(
    #     f"GitLab file download: GET {url} {response.status_code} {response.headers}"
    # )
    if not response.ok:
        messages.error(
            f"Failed to download file {filename} from GitLab: "
            f"{url} {response.status_code} {response.text}"
        )
        return bytes()

    image = response.content
    mime_type = response.headers.get("Content-Type", "").split(";")[0].strip()
    messages.notice(f"Downloaded file from GitLab: {filename} {mime_type}")
    return image


def _upload_file_to_vercel(path: PurePath, data: bytes, token: str) -> str:
    options = {"token": token, "addRandomSuffix": True}
    response = vercel_blob.put(
        path=str(path), data=data, timeout=REQUEST_TIMEOUT, options=options
    )
    # messages.debug(f"Vercel file upload: {path} {response}")
    url = response.get("url") or ""
    if url:
        messages.notice(f"Uploaded file to vercel: {url}")
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


def _issue_str(issue: ProjectIssue) -> str:
    """Return string representation of a GitLab issue ID."""
    return f"GL-#{issue.iid:04d}"
