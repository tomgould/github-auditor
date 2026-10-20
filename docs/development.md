# Development

## Running the tests

```bash
pip install -e ".[dev]"
pytest                                   # 910 tests
pytest --cov=auditer                     # with coverage
pytest tests/commands/data/test_activity_cmd.py -v   # one file
```

The tests use [respx](https://lundberg.github.io/respx/) in place of the GitHub and Bitbucket APIs, so they need no token or network. Command tests live in `tests/commands/`, in the same folders as the commands.

## Checking by hand

After a change, or on a first install, these runs cover the main paths. Use one of your own account slugs.

```bash
auditer config --validate                    # "Config is valid. N account(s) configured."
auditer config --show                        # tokens cut to their last four characters
auditer checkout --account <slug> --dry-run  # lists what would be cloned or pulled; changes nothing
auditer checkout --account <slug> --full     # clones, then fetches all branches and tags
auditer audit --account <slug>               # writes reports/<slug>/audit_report.md
auditer --verbose audit --account <slug>     # adds the API requests and git commands
```

Some behaviour worth checking:

- A clone with uncommitted changes is skipped ("Skipping some-repo (local changes detected)") unless you pass `--no-skip-dirty`.
- A fake clone of a repository that isn't on GitHub (`mkdir -p data/<slug>/public/fake-orphan/.git`) makes `checkout` warn about it, and `audit` still lists it with default values after a yellow "Could not enrich" warning. Delete the folder afterwards.
- Moving a clone's `README.md` away makes `audit` report it as missing; the same goes for `LICENSE`.
- With `exclude_repos` set, both `checkout` and `audit` print "Excluded N repo(s) per config."

## Using the GitHub client

`GitHubClient` can be used from Python for jobs the commands don't cover.

```python
from auditer.models import AccountConfig
from auditer.github_client import GitHubClient, AuthenticationError, RateLimitError

account = AccountConfig(slug="my-user", type="user", username="myuser", token="ghp_...")

with GitHubClient(account) as client:
    repos = client.list_repos()                          # every repository for the account
    repo = client.get_repo("owner", "repo-name")         # None on 404
    login = client.validate_token()                      # the token's login, or None

    client.add_collaborator("owner", "repo", "user", permission="admin")
    client.update_repo("owner", "repo", description="New description", has_wiki=False)
    client.set_topics("owner", "repo", ["python", "cli"])

    file = client.get_file("owner", "repo", "README.md") # None on 404
    client.create_or_update_file(
        "owner", "repo", "README.md",
        content="# Updated README",                      # base64-encoded for you
        message="Update README",
        sha=file["sha"],                                 # needed to update an existing file
    )

    teams = client.get_paginated("/orgs/my-org/teams")   # any endpoint, following Link headers
```

Other methods include `has_readme`, `get_user`, `get_authenticated_user`, `list_org_members`, `remove_org_member`, `list_collaborators`, `remove_collaborator`, `get_topics`, `list_user_events`, `list_repo_commits`, `list_pulls`, `list_pull_reviews`, `list_repo_issues` and `get_rate_limit`.

Errors are `GitHubAPIError` (with `status_code`), `AuthenticationError` for a bad or expired token, and `RateLimitError`. The client makes up to three attempts on server and network errors, with a 30-second timeout for each request, and waits for the reset when the hourly rate limit runs out.

## Layout

```
src/auditer/
  cli.py               Click entry point; config loads on first use, so --help works without one
  config.py            loads and validates config.yaml
  models.py            data classes, including the health score
  github_client.py     GitHub API client (httpx)
  bitbucket_client.py  Bitbucket Cloud API client
  git_provider.py      picks the client for an account
  git_ops.py           git commands, local scanning and the line-count exclusions
  timewindow.py        whole-day date ranges in one timezone
  filtering.py         shared repository filters
  licenses.py          the five licence templates
  commands/            one module per command: data/, repo/, people/, meta/, content/
  reports/             the Markdown report writers
  utils/               logging that masks tokens, and URL sanitising
tests/                 pytest, with command tests under tests/commands/
scripts/               daily-checkout.sh for cron
```

`ROADMAP.md` has the planned work. `CLAUDE.md` describes the architecture and conventions for AI coding assistants.
