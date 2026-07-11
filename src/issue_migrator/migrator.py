"""Issue migration."""

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
import yaml
from diskcache import Cache
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
from rich import progress
from rich.console import Console

from issue_migrator.messages import Messages, MessagesLogHandler

REQUEST_TIMEOUT = 10  # seconds
CACHE_TIMEOUT = 3600 * 6  # seconds
LABEL_MIGRATED = "source: gitlab"
MIGRATED_LABEL_DESCRIPTION = "This label was migrated from GitLab"
LOG_LEVEL = "WARN"
LOG_FORMAT = "%(message)s"
LOG_DATEFMT = "[%X]"

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
    """A class for migrating issues from a GitLab repo to a GitHub repo.

    Should be as context manager to ensure connections are automatically closed.

    `connect()` must be called before any other methods.
    """

    def __init__(
        self,
        github_repo_name: str,
        github_token: str,
        gitlab_host: str,
        gitlab_repo_name: str,
        gitlab_token: str,
        vercel_blob_token: str,
        cache_directory: Optional[str] = None,
        cache: Optional[Cache] = None,
        is_dry_run: Optional[bool] = False,
        console: Optional[Console] = None,
    ):
        self.console = console or Console()
        self.github_repo_name = github_repo_name
        self.github_token = github_token
        self.gitlab_host = gitlab_host
        self.gitlab_repo_name = gitlab_repo_name
        self.gitlab_token = gitlab_token
        self.is_dry_run = is_dry_run
        self.messages = Messages(console=self.console)
        self.vercel_blob_token = vercel_blob_token
        self._gl: Optional[gitlab.Gitlab] = None
        self._gl_project: Optional[Project] = None
        self._gh_repo: Optional[Repository] = None
        self._gh: Optional[Github] = None
        self._unknown_users: Set[str] = set()

        if cache_directory:
            user_mapping = Cache(directory=cache_directory)
        elif cache:
            user_mapping = cache
        else:
            user_mapping = Cache(directory=".")

        self.user_mapping = user_mapping

        # replace github handlers with out own
        logger = logging.getLogger("github")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        handler = MessagesLogHandler(messages=self.messages)
        logger.addHandler(handler)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

        return False

    @property
    def gl(self) -> gitlab.Gitlab:
        """Return a valid gitlab instance or raise error."""
        if self._gl is None:
            raise RuntimeError("Not connected")
        return self._gl

    @property
    def gl_project(self) -> Project:
        """Return a valid gitlab project or raise error."""
        if self._gl_project is None:
            raise RuntimeError("Not connected")
        return self._gl_project

    @property
    def gh(self) -> Github:
        """Return a valid github instance or raise error."""
        if self._gh is None:
            raise RuntimeError("Not connected")
        return self._gh

    @property
    def gh_repo(self) -> Repository:
        """Return a valid github project or raise error."""
        if self._gh_repo is None:
            raise RuntimeError("Not connected")
        return self._gh_repo

    def connect(self):
        """Connect to GitLab and GitHub projects.

        `close()` must be called if class is not used as context manager.
        """
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

        except Exception as ex:
            raise MigrationError(message=f"Unexpected error: {ex}") from ex

        self.messages.notice(
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

        except Exception as ex:
            raise MigrationError(message=f"Unexpected error: {ex}") from ex

        self.messages.notice(
            f"Connected to GitHub repo: {self.gh_repo.name} (ID: {self.gh_repo.id}) "
            f"as {gh_user.login}"
        )

    def close(self):
        """Close all open connections."""
        self.user_mapping.close()

        if self._gh:
            self._gh.close()  # closes the GH connection
            self._gh = None

    def clear_cache(self):
        """Clear all caches."""
        self.user_mapping.clear()
        self.messages.notice("Cache cleared")

    def load_user_mappings(self, user_mapping: Dict[str, str]) -> bool:
        """Loads mapping between GitLab and GitHub users into cache
        and reports whether they are valid."""

        if not user_mapping:
            self.messages.notice("No user mapping provided")
            return True

        ok_all = True
        with progress.Progress(
            progress.SpinnerColumn(),
            progress.TextColumn("[progress.description]{task.description}"),
            progress.BarColumn(),
            progress.MofNCompleteColumn(),
            progress.TextColumn("{task.fields[current]}"),
            transient=True,
            console=self.console,
        ) as pb:
            task = pb.add_task(
                "Load user mappings", total=len(user_mapping), current=""
            )
            for gl_username, gh_username in user_mapping.items():
                ok = True
                current = f"{gl_username} -> {gh_username}"
                pb.update(task, current=current)

                name = self.user_mapping.get(gl_username)
                if not name or name != gh_username:
                    if not self._gitlab_user_exists(gl_username):
                        ok_all = ok = False
                        self.messages.warning(
                            f"Unknown GitLab username: {gl_username}", pb.console
                        )

                    if not self._github_user_exists(gh_username):
                        ok_all = ok = False
                        self.messages.warning(
                            f"Unknown GitHub username: {gh_username}", pb.console
                        )

                    if ok:
                        self.user_mapping.set(
                            key=gl_username, value=gh_username, expire=CACHE_TIMEOUT
                        )
                    else:
                        self.messages.error(
                            f"Invalid mapping: {gl_username} -> {gh_username}",
                            pb.console,
                        )

                pb.advance(task)

        if ok_all:
            self.messages.notice(f"{len(user_mapping)} user mappings loaded")

        return ok_all

    def _gitlab_user_exists(self, gl_username) -> bool:
        gl_users = self.gl.users.list(username=gl_username, get_all=True)
        if len(gl_users) == 0:
            return False

        return True

    def _github_user_exists(self, gh_username) -> bool:
        query = f"user:{gh_username}"
        try:
            result = self.gh.search_users(query)
            if result.totalCount == 0:
                return False

        except GithubException:
            return False

        return True

    def find_user_mappings(self):
        """Find user mappings for unknown users.

        Will try to find users on GitHub with the same username as on GitLab.
        """
        if not self._unknown_users:
            return

        invalids = []
        valids = []
        with progress.Progress(
            progress.SpinnerColumn(),
            progress.TextColumn("[progress.description]{task.description}"),
            progress.BarColumn(),
            progress.MofNCompleteColumn(),
            progress.TextColumn("{task.fields[current]}"),
            transient=True,
            console=self.console,
        ) as pb:
            task = pb.add_task(
                "Finding user mappings", total=len(self._unknown_users), current=""
            )
            for username in self._unknown_users:
                pb.update(task, current=username)
                if self._github_user_exists(username):
                    valids.append(username)
                else:
                    invalids.append(username)

                pb.advance(task)

        if valids:
            mappings = yaml.dump({"user-mapping": {x: x for x in valids}})
            self.messages.success(f"Found {len(valids)} mappings:\n{mappings}")

        if invalids:
            out_str = ", ".join(sorted(invalids))
            self.messages.warning(f"No mapping found: {out_str}")

    def sync_labels(self):
        """Check for missing labels in GitHub repo and add them when missing."""

        gh_label_names = {x.name for x in self.gh_repo.get_labels()}
        # self.messages.notice(f"GH labels: {', '.join(sorted(gh_labels))}")

        gl_labels = {
            label.name: MIGRATED_LABEL_DESCRIPTION
            for label in self.gl_project.labels.list(iterator=True)
        }
        gl_labels[LABEL_MIGRATED] = "This issue was migrated from GitLab"
        # self.messages.notice(f"GL labels: {', '.join(sorted(gl_labels))}")

        missing_names = set(gl_labels.keys()) - gh_label_names
        if not missing_names:
            self.messages.info("Labels are in sync")
            return

        _names = '"' + '", "'.join(sorted(missing_names)) + '"'
        self.messages.info(f"Missing labels: {_names}")
        if self.is_dry_run:
            return

        with progress.Progress(
            progress.SpinnerColumn(),
            progress.TextColumn("[progress.description]{task.description}"),
            progress.BarColumn(),
            progress.MofNCompleteColumn(),
            progress.TextColumn("{task.fields[current]}"),
            transient=True,
            console=self.console,
        ) as pb:
            task = pb.add_task(
                "Creating missing labels", total=len(missing_names), current=""
            )
            for name in missing_names:
                pb.update(task, current=name)
                self.gh_repo.create_label(
                    name,
                    random.choice(GITHUB_LABEL_COLORS),
                    description=gl_labels[name],
                )
                pb.advance(task)

        total = len(missing_names)
        self.messages.success(f"Created {total} missing labels")

    def migrate_issues(
        self,
        issue_ids: Optional[List[int]] = None,
        no_close_issues: bool = False,
    ):
        """Migrate all issues of a project."""

        _issue_ids = set(issue_ids or [])
        issues = self.gl_project.issues.list(
            state="opened",
            order_by="created_at",
            sort="asc",
            iterator=True,
        )
        if not issues.total:
            self.messages.info("Found no issues to migrate")
            return

        migrated_count = 0
        skipped_count = 0
        failed_count = 0

        self.messages.info(f"Found {issues.total} opened issue to migrate")
        with progress.Progress(
            progress.SpinnerColumn(),
            progress.TextColumn("[progress.description]{task.description}"),
            progress.BarColumn(),
            progress.MofNCompleteColumn(),
            progress.TextColumn("{task.fields[current]}"),
            transient=True,
            console=self.console,
        ) as pb:
            task = pb.add_task("Migrating issues", total=issues.total, current="")
            for gl_issue in issues:
                current = f"#{gl_issue.encoded_id}: {gl_issue.title}"
                pb.update(task, current=current)

                if _issue_ids and gl_issue.iid not in _issue_ids:
                    skipped_count += 1
                    self.messages.notice(
                        f"Skipping not included issue: {_issue_str(gl_issue)}",
                        pb.console,
                    )
                    pb.advance(task)
                    continue

                try:
                    if self._issue_exists(gl_issue):
                        skipped_count += 1
                        self.messages.warning(
                            f"Skipping already migrated issue: {_issue_str(gl_issue)}",
                            pb.console,
                        )
                        pb.advance(task)
                        continue

                    self._migrate_issue(
                        gl_issue=gl_issue,
                        pb=pb,
                        no_close_issues=no_close_issues,
                    )

                except Exception as ex:
                    failed_count += 1
                    self.messages.error(
                        f"Failed to migrate issue {_issue_str(gl_issue)}: {ex}",
                        pb.console,
                    )
                    pb.advance(task)
                    continue

                migrated_count += 1
                pb.advance(task)

        self.messages.info(
            f"Finished processing {issues.total} issues: {migrated_count} migrated, "
            f"{skipped_count} skipped, {failed_count} failed."
        )
        if self._unknown_users:
            self.messages.warning(
                f"Unkown users: {', '.join(sorted(self._unknown_users))}"
            )

    def _issue_exists(self, gl_issue: ProjectIssue) -> bool:
        """Report whether an GitLab issue exists on GitHub."""
        search_string = _issue_str(gl_issue)
        query = f"{search_string} repo:{self.github_repo_name} type:issue state:open"
        found_issues = self.gh.search_issues(query)
        return found_issues.totalCount > 0

    def _migrate_issue(
        self,
        gl_issue: ProjectIssue,
        pb: progress.Progress,
        no_close_issues: bool = False,
    ):
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
            f"> 📦 **Migrated from GitLab**\n"
            f"> **Original Issue:** [{issue_str}]({gl_issue.web_url})\n"
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
                self.messages.error(
                    f"Failed to migrate note #{gl_note.get_id()} "
                    f"for issue {_issue_str(gl_issue)}: {ex}",
                    pb.console,
                )

        if gh_issue and not no_close_issues:
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
            f"| [Original]({comment_url}) \n\n"
            f"{description_2}"
        )
        if gh_issue:
            gh_issue.create_comment(body=formatted_comment)

    def _map_author(self, author):
        gl_username = author.get("username")
        try:
            gh_username = self.user_mapping[gl_username]
            display = f"@{gh_username}"
        except KeyError:
            # self.messages.warning(f"No mapping found for GL user: {username}")
            display = author.get("name") or "?"
            self._unknown_users.add(gl_username)

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
            data = self._download_embedded_file_from_gitlab(rel_url)
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
            # If group 1 or group 2 matched, we are inside a code block.
            # Return it as-is.
            if match.group(1) or match.group(2):
                return match.group(0)

            # Group 3 matched the user mention
            gl_username = match.group(3)

            # Gitlab usernames cannot end in standard punctuation.
            # If a trailing period/comma/exclamation was caught, separate it.
            trailing_punctuation = ""
            while gl_username and gl_username[-1] in ".,!?":
                trailing_punctuation = gl_username[-1] + trailing_punctuation
                gl_username = gl_username[:-1]

            try:
                gh_username = self.user_mapping[gl_username]
                mention = f"@{gh_username}"
            except KeyError:
                mention = "@\u200b" + gl_username  # disables the mention
                self._unknown_users.add(gl_username)

            return f"{mention}{trailing_punctuation}"

        return re.sub(pattern, replace, text)

    def _download_embedded_file_from_gitlab(self, rel_url: str) -> bytes:
        """Download an embedded file from GitLab and return it.

        Return empty when there was an error.
        """
        rel_url_2 = rel_url.lstrip(".")
        url = f"{self.gitlab_host}/-/project/{self.gl_project.encoded_id}/{rel_url_2}"
        filename = rel_url.split("/")[-1]
        headers = {"PRIVATE-TOKEN": self.gitlab_token}
        time.sleep(0.2)  # rate limit is 500 / minute
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)  # type: ignore
        # self.messages.debug(
        #     f"GitLab file download: GET {url} {response.status_code} {response.headers}"
        # )
        if not response.ok:
            self.messages.error(
                f"Failed to download file {filename} from GitLab: "
                f"{url} {response.status_code} {response.text}"
            )
            return bytes()

        image = response.content
        # mime_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        # self.messages.notice(f"Downloaded file from GitLab: {filename} {mime_type}")
        return image


def _upload_file_to_vercel(path: PurePath, data: bytes, token: str) -> str:
    options = {"token": token, "addRandomSuffix": True}
    response = vercel_blob.put(
        path=str(path), data=data, timeout=REQUEST_TIMEOUT, options=options
    )
    # self.messages.debug(f"Vercel file upload: {path} {response}")
    url = response.get("url") or ""
    # if url:
    # self.messages.notice(f"Uploaded file to vercel: {url}")
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
