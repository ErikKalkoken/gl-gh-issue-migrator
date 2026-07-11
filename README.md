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

## How to use

1. Create a bot user account on GitHub (optional but recommended)
1. Create and store tokens for GitLab, GitHub and Vercel Blob in the config file
1. Import the GitLab project into GitHub via the official import feature on it's site
1. Run issue-migrator on repos with `--dry-run` and `--find-mappings` to identify missing user mappings and potential issues
1. Update user mappings in config file
1. Run issue-migrator on repos to migrate issues
1. Add migration note & archive GH project
