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
- Adds migration note to original issue on GitLab and closes it
