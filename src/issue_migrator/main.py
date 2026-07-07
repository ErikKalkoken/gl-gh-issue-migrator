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


class ColoredFormatter(logging.Formatter):
    # Define ANSI escape codes for colors
    blue = "\x1b[34;20m"
    red_bold = "\x1b[31;1m"
    grey = "\x1b[38;20m"
    purple_bold = "\x1b[35;1m"
    red = "\x1b[31;20m"
    reset = "\x1b[0m"
    yellow = "\x1b[33;20m"

    # The format you want for your logs
    log_format = "%(asctime)s %(levelname)s %(message)s"

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

        if self.use_color:
            self.FORMATS = {
                logging.DEBUG: self.blue + self.log_format + self.reset,
                logging.INFO: self.grey + self.log_format + self.reset,
                logging.WARNING: self.yellow + self.log_format + self.reset,
                logging.ERROR: self.red + self.log_format + self.reset,
                logging.CRITICAL: self.purple_bold + self.log_format + self.reset,
            }
        else:
            self.FORMATS = {
                logging.DEBUG: self.log_format,
                logging.INFO: self.log_format,
                logging.WARNING: self.log_format,
                logging.ERROR: self.log_format,
                logging.CRITICAL: self.log_format,
            }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%Y/%m/%d %H:%M")
        return formatter.format(record)


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
        "-l",
        "--log-level",
        choices=logging.getLevelNamesMapping().keys(),
        default="INFO",
        help=("Set log level"),
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
        "--show-config",
        action="store_true",
        help="Show effective config and exit (requires valid config).",
    )
    parser.add_argument(
        "--skip-user-validation",
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
    options = parser.parse_args()

    level_mapping = logging.getLevelNamesMapping()
    target_level = level_mapping.get(options.log_level.upper(), logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(use_color=not options.no_color))
    logging.basicConfig(
        level=target_level,
        handlers=[console_handler],
    )

    if options.show_config:
        print(options)
        print("----------")
        print(parser.format_values())
        return

    m = Migrator(
        github_repo_name=options.github_repo_name,
        github_token=options.github_token,
        gitlab_host=options.gitlab_host,
        gitlab_repo_name=options.gitlab_repo_name,
        gitlab_token=options.gitlab_token,
        is_dry_run=options.dry_run,
        issue_ids=options.issue_id,
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
