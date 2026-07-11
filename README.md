# gl-gh-issue-migrator

A tool for migrating issues from a GitHub project to a GitLab repo.

> [!WARNING]
> This app is in development and not yet stable

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
- Can find mappings for unknown users

## How to use

1. Create a bot user account on GitHub (optional but recommended)
2. Create and store tokens for GitLab, GitHub and Vercel Blob in the config file
3. Import GitLab project into GitHub via official import feature on web site
4. Update user mappings
5. Migrate issues with issue-migrator
6. Add migration note & archive GH project
