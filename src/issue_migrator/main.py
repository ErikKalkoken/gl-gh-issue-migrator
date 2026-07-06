"""Main logic."""

import logging
import os
import sys

import configargparse
import yaml

from issue_migrator.migrator import MigrationError, Migrator

from . import __doc__ as package_doc
from . import __version__

logger = logging.getLogger(__name__)

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
        "gitlab_repo",
        help="Name of the GitLab repository, e.g. ErikKalkoken/aa-structures",
    )
    parser.add_argument(
        "github_repo",
        help="Name of the GitHub repository, e.g. ErikKalkoken/aa-structures",
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
        "--vercel-blob-token",
        required=True,
        env_var="BLOB_READ_WRITE_TOKEN",
        help="Token for uploads to a vercel blop.",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Run through the migration without creating any objects on GitHub.",
    )
    parser.add_argument(
        "--no-close-issues",
        action="store_true",
        help="Disables closing migrated issues.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        choices=logging.getLevelNamesMapping().keys(),
        default="INFO",
        help=("Set log level"),
    )
    parser.add_argument(
        "-s",
        "--show-config",
        action="store_true",
        help="Show effective config and exit (requires valid config).",
    )
    parser.add_argument(
        "--user-mapping",
        type=yaml.safe_load,
        default={},
        help=(
            "Define mapping of user handles as JSON string. "
            "Mapping is from Gitlab to Guthub, "
            "Note that user mentions that are not defined here will be muted."
            "e.g."
            '{"user1_gl":"user1_gh", "ErikKalkoken":"ErikKalkoken"}'
        ),
    )
    parser.add_argument(
        "--skip-user-validation",
        action="store_true",
        help="When set will skip validating users mappings.",
    )
    return parser


def main_cli():
    """Main program for running this script."""
    parser = _define_args()
    options = parser.parse_args()

    if options.show_config:
        print(options)
        print("----------")
        print(parser.format_values())
        return

    level_mapping = logging.getLevelNamesMapping()
    target_level = level_mapping.get(options.log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="{asctime} {levelname} {message}",
        style="{",
        datefmt="%Y/%m/%d %H:%M",
        level=target_level,
    )

    m = Migrator(
        github_repo=options.github_repo,
        github_token=options.github_token,
        gitlab_host=options.gitlab_host,
        gitlab_repo=options.gitlab_repo,
        gitlab_token=options.gitlab_token,
        is_dry_run=options.dry_run,
        no_close_issues=options.no_close_issues,
        skip_user_validation=options.skip_user_validation,
        user_mapping=dict(options.user_mapping),
        vercel_blob_token=options.vercel_blob_token,
    )
    try:
        m.connect()
    except MigrationError as ex:
        logger.critical("Connection error: %s", ex.message)
        sys.exit(1)

    try:
        m.run()
    except MigrationError as ex:
        logger.error("Migration error: %s", ex.message)
        sys.exit(1)

    if m.is_dry_run:
        logger.info("Dry Run completed!")
    else:
        logger.info("Migration completed!")


if __name__ == "__main__":
    main_cli()
