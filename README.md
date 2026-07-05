# gl-gh-issue-migrator

A tool for migrating issues from a GitHub project to a GitLab repo.

> [!WARNING]
> This app is in development and not yet stable

## Features

- Migrates issues including all comments, labels and embedded files
- Adds a custom label to new issues so they can be identified easily
- Adds migration note to new issues in description with link to original issue
- Adds migration comment with link to original issue to old issue
- Embedded files are uploaded to an external object store (i.e. vercel)
- Mapping user mentions between GitLab and GitHub
- Unknown user mentions are disabled
- Rate limits from both GH and GL are respected

## How to use

1. Import GitLab project into GitHub via official import feature on web site
2. Update user mappings
3. Migrate issues with issue-migrator
4. Add migration note & archive GH project
