# gl-gh-issue-migrator

A tool for migrating issues from a GitHub project to a GitLab repo.

> [!WARNING]
> This app is in development and not yet stable

## Features

- Migrates issues including all comments, labels and embedded images
- Migrated issues are assigned a custom label so they can be identified later
- Adds migration note in description with link to original issue
- Adds migration note to comments with link to original issue
- Embedded images are uploaded an external images store (i.e. imgpile)
- Rate limits from both GH and GL are respected

## Planned

- Adds migration note to original issue on GitLab and closes it
- Rate limits and exception handling for image uploading and & downloading
- Ability to configure behavior

## How to use

1. Import GitLab project into GitHub via official import feature on web site
2. Run issue-migrator to migrate open issues
3. Add migration note & archive GH project
