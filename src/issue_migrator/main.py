"""Main logic."""

import os
import sys

import configargparse
import yaml
import yaml.parser
from rich.console import Console

from issue_migrator.migrator import MigrationError, Migrator

from . import __doc__ as package_doc
from . import __version__
from .messages import Messages

GITLAB_PUBLIC_HOST = "https://gitlab.com"


def _define_args() -> configargparse.ArgumentParser:
    parser = configargparse.ArgParser(
        default_config_files=["config.ini"],
        config_file_parser_class=configargparse.ConfigparserConfigFileParser,
        description=package_doc,
        formatter_class=configargparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-v", "--version", action="version", version=__version__)
    gitlab_host = os.environ.get("GITLAB_HOST")
    parser.add_argument(
        "gitlab_repo_name",
        help="Name of the GitLab repository, e.g. ErikKalkoken/aa-structures",
    )
    parser.add_argument(
        "github_repo_name",
        help="Name of the GitHub repository, e.g. ErikKalkoken/aa-structures",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run through the migration without making any changes.",
    )
    parser.add_argument(
        "--gitlab-host",
        default=gitlab_host or GITLAB_PUBLIC_HOST,
        help=(
            "URL of the GitLab host. "
            "Can also be set via environment variable: GITLAB_HOST"
        ),
    )
    parser.add_argument(
        "--gitlab-token",
        env_var="GITLAB_TOKEN",
        required=True,
        help="Personal access token for GitLab.",
    )
    parser.add_argument(
        "--github-token",
        env_var="GITHUB_TOKEN",
        required=True,
        help="Personal access token for GitHub.",
    )
    parser.add_argument(
        "--issue-id",
        type=int,
        action="append",
        help=(
            "Only include issue given by ID. "
            "This arg can be specified multiple times to add more IDs."
        ),
    )
    parser.add_argument(
        "--find-users",
        action="store_true",
        help="When set will try to find unknown users on GitHub",
    )

    parser.add_argument(
        "--no-close-issues",
        action="store_true",
        help="Disables closing migrated issues.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="When set will disable colors in output",
    )
    parser.add_argument(
        "--no-migration",
        action="store_true",
        help=(
            "When set will not run the migration. "
            "Useful when one only wants to validate user mappings"
        ),
    )
    parser.add_argument(
        "--no-labels",
        action="store_true",
        help=("When set will not run sync labels."),
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show effective config and exit (requires valid config).",
    )
    parser.add_argument(
        "--no-user-validation",
        action="store_true",
        help="When set will skip validating users mappings.",
    )
    parser.add_argument(
        "--user-mapping",
        type=yaml.safe_load,
        default={},
        help=(
            "Define mapping of user handles as JSON string. "
            "Mapping is from Gitlab to Github, "
            "Note that user mentions that are not defined here will be muted."
            "e.g."
            '{"user1_gl":"user1_gh", "ErikKalkoken":"ErikKalkoken"}'
        ),
    )
    parser.add_argument(
        "--vercel-blob-token",
        required=True,
        env_var="BLOB_READ_WRITE_TOKEN",
        help="Token for uploads to a vercel blob.",
    )
    return parser


def main_cli():
    """Main program for running this script."""
    parser = _define_args()
    try:
        options = parser.parse_args()
    except yaml.MarkedYAMLError as ex:
        print(f"Configuration error: {ex}")
        sys.exit(1)

    if options.show_config:
        print(options)
        print("----------")
        print(parser.format_values())
        return

    if options.no_color:
        console = Console(color_system=None)
    else:
        console = Console()
    messages = Messages(console=console)

    with Migrator(
        github_repo_name=options.github_repo_name,
        github_token=options.github_token,
        gitlab_host=options.gitlab_host,
        gitlab_repo_name=options.gitlab_repo_name,
        gitlab_token=options.gitlab_token,
        is_dry_run=options.dry_run,
        no_color=options.no_color,
        user_mapping=dict(options.user_mapping),
        vercel_blob_token=options.vercel_blob_token,
    ) as m:

        try:
            m.connect()

        except MigrationError as ex:
            messages.critical(ex.message)
            sys.exit(1)

        except KeyboardInterrupt:
            messages.critical("Aborted by user")
            sys.exit(1)

        try:
            if options.no_user_validation:
                messages.notice("Skipped user validation as requested")
            else:
                if not m.validate_user_mappings():
                    messages.critical("Some user mappings are invalid")
                    sys.exit(1)

            if options.no_labels:
                messages.notice("Skipped label sync as requested")
            else:
                m.sync_labels()

            if options.no_migration:
                messages.notice("Skipped migration as requested")
            else:
                m.migrate_issues(
                    issue_ids=options.issue_id,
                    no_close_issues=options.no_close_issues,
                )

            if options.find_users:
                m.find_github_users()

        except KeyboardInterrupt:
            messages.critical("Aborted by user")
            sys.exit(1)

        except MigrationError as ex:
            messages.critical(ex.message)
            sys.exit(1)

        if m.is_dry_run:
            messages.success("Dry Run completed!")
        else:
            messages.success("Migration completed!")


if __name__ == "__main__":
    main_cli()
