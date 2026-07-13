# gl-gh-issue-migrator

A tool for migrating issues from a GitHub project to a GitLab repo.

[![CI/CD Pipeline](https://github.com/ErikKalkoken/gl-gh-issue-migrator/actions/workflows/ci-cd.yaml/badge.svg)](https://github.com/ErikKalkoken/gl-gh-issue-migrator/actions/workflows/ci-cd.yaml)

## Features

- Migrates issues including assignees, comments, description, embedded files, labels, title
- Adds a custom label to new issues so they can be identified easily
- Adds migration note to new issues in description with link to original issue
- Adds migration comment with link to original issue to old issue
- Embedded files are uploaded to an external object store (i.e. vercel)
- Users are mapping between GitLab and GitHub for mentions and assignees
- Unknown user mentions are muted
- Rate limits from both GH and GL are respected
- Allows configuration by file, environment variables and/or command line options
- User mappings are validated and then cached
- Ability to find mappings for unknown users
- Color output can be disabled
- Console output can be disabled
- Aborted issue migrations can be continued.

## Use guide

This section describes how to use the tool.

### Prerequisites

You need the following:

- A personal access token for GitLab with the scopes: API.
- A GitHub app with the scopes: Issues read/write
- A pem file containing the private token of the GitHub app
- A token for a vercel blob (for storing embedded files) with the scopes: read/write

### Migration

1. Import the GitLab project into GitHub via the official import feature on it's site
1. Install your GitHub app into the imported repo
1. Add the tokens to the config file
1. Make a dry run to identify missing users and potential issues

    ```sh
    issue-migrator --dry-run --find-users GITLAB-REPO GITHUB-REPO
    ````

1. Add user mappings to config file
1. Re-do the same dry-run to validate new users
1. Run the actual issue migration

    ```sh
    issue-migrator GITLAB-REPO GITHUB-REPO
    ````

1. Add a migration note and archive the GitLab project

## Usage

Here is the complete usage information for the tool showing all available configuration options and features:

```plain
usage: issue-migrator [-h] [-v] [--clear-cache] [--dry-run] [--gitlab-host GITLAB_HOST] --gitlab-token GITLAB_TOKEN --github-app-id GITHUB_APP_ID
                      --github-installation-id GITHUB_INSTALLATION_ID --github-private-key GITHUB_PRIVATE_KEY [--issue-id ISSUE_ID] [--find-users] [--no-close-issues]
                      [--no-color] [--no-migration] [--no-labels] [--show-config] [--quiet] [--user-mapping USER_MAPPING] --vercel-blob-token VERCEL_BLOB_TOKEN
                      gitlab_repo_name github_repo_name

A tool for migrating issues from GitLab to GitHub.

positional arguments:
  gitlab_repo_name      Name of the GitLab repository, e.g. ErikKalkoken/aa-structures
  github_repo_name      Name of the GitHub repository, e.g. ErikKalkoken/aa-structures

options:
  -h, --help            show this help message and exit
  -v, --version         show program's version number and exit
  --clear-cache         Clears the cache (default: False)
  --dry-run             Run through the migration without making any changes. (default: False)
  --gitlab-host GITLAB_HOST
                        URL of the GitLab host. Can also be set via environment variable: GITLAB_HOST (default: https://gitlab.com)
  --gitlab-token GITLAB_TOKEN
                        Personal access token for GitLab. [env var: GITLAB_TOKEN]
  --github-app-id GITHUB_APP_ID
                        App ID of the GitHub app. [env var: GITHUB_APP_ID]
  --github-installation-id GITHUB_INSTALLATION_ID
                        Installation ID of the GitHub app. [env var: GITHUB_INSTALLATION_ID]
  --github-private-key GITHUB_PRIVATE_KEY
                        Path to pem file containing the private key
  --issue-id ISSUE_ID   Only include issue given by ID. This arg can be specified multiple times to add more IDs. (default: None)
  --find-users          When set will try to find user mappings for unknown users (default: False)
  --no-close-issues     Disables closing migrated issues. (default: False)
  --no-color            When set will disable colors in output (default: False)
  --no-migration        When set will not run the migration. Useful when one only wants to validate user mappings (default: False)
  --no-labels           When set will not run sync labels. (default: False)
  --show-config         Show effective config and exit (requires valid config). (default: False)
  --quiet               When set will suppress most console output. (default: False)
  --user-mapping USER_MAPPING
                        Mapping of GitLab to GitHub usernames in YAML. (default: {})
  --vercel-blob-token VERCEL_BLOB_TOKEN
                        Token for uploads to a vercel blob. [env var: BLOB_READ_WRITE_TOKEN]

Args that start with '--' can also be set in a config file (config.yaml). The config file uses YAML syntax and must represent a YAML 'mapping' (for details, see
http://learn.getgrav.org/advanced/yaml). In general, command-line values override environment variables which override config file values which override defaults.
```

## Configuration file example

See below for an example of the configuration file (`config.yaml`):

```yaml
gitlab-token: glpat-my-token

github-app-id: 987654321
github-installation-id: 12345678
github-private-key: path/to/private-key.pem

vercel-blob-token: vercel_blob_rw_my-token

user-mapping:
  erik_gitlab: erik_github
  john_1: john_2
```
