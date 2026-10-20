# GitHub Auditor

A command-line tool for looking after a lot of GitHub repositories at once. It keeps a local clone of every repository in your accounts and writes Markdown reports from them: which repositories are missing a README or a licence, who has access to what, and what each developer did in a given week or month. It can also archive, license, tag and reconfigure repositories in bulk.

It's for anyone with more repositories than they can keep in their head: a personal account with years of projects, or an organisation with dozens of repositories and people. It handles any number of GitHub users and organisations, and Bitbucket Cloud workspaces for most commands.

## Install

It isn't on PyPI, so install it from a clone. You need Python 3.10 or later and git.

```bash
git clone https://github.com/tomgould/github-auditor.git
cd github-auditor
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
```

Then add your accounts and a GitHub personal access token to `config.yaml`. The token needs the `repo` scope for private repositories, or `public_repo` for public ones only. `config.yaml` is in `.gitignore`.

A note on the name: the repository is `github-auditor`, but the Python package is `github-auditer`, imported as `auditer`, and the command it installs is `auditer`.

## Quick start

```bash
auditer checkout --all --full    # clone every repository, with all branches and tags
auditer audit --all              # a health report for each account
auditer activity --account my-org --since 2026-10-01 --until 2026-10-14
```

Reports go into `reports/`, with a folder for each account. Clones go into `data/`.

## Commands

| Command | What it does |
| --- | --- |
| [`checkout`](docs/commands.md#checkout) | Clones every repository in an account, or pulls the latest changes. Reports new branches and tags, spots force pushes and skips clones with uncommitted changes. |
| [`audit`](docs/commands.md#audit) | Lists repositories missing a README, a licence or a description, finds stale ones, suggests which to pin and gives each a health score out of 100. |
| [`activity`](docs/commands.md#activity) | Reports what each developer did over a date range: commits, lines, pull requests, merges, reviews, comments, issues and tags. |
| [`access`](docs/commands.md#access) | Shows every collaborator against every repository, with the highest permission each one holds. |
| [`members`](docs/commands.md#members) | Lists or removes organisation members. |
| [`collaborators`](docs/commands.md#collaborators) | Lists, adds or removes repository collaborators. |
| [`archive`](docs/commands.md#archive) | Archives stale repositories. |
| [`add-license`](docs/commands.md#add-license) | Adds an MIT, Apache 2.0, GPL 3.0 or BSD licence file where one is missing. |
| [`topics`](docs/commands.md#topics) | Lists, sets or adds repository topics. |
| [`settings`](docs/commands.md#settings) | Changes the description, homepage, issues, wiki, projects, default branch or visibility. |
| [`profile`](docs/commands.md#profile) | Writes a profile README for an account. |
| [`readme`](docs/commands.md#readme) | Writes a README for a repository from its contents, and can push it. |
| [`config`](docs/commands.md#config) | Checks `config.yaml`, or shows it with the tokens masked. |

Risky changes, such as removing a member, archiving or changing visibility, ask for confirmation first. `auditer COMMAND --help` lists every option, and [docs/commands.md](docs/commands.md) has examples for each command.

Global options go before the command: `--config PATH` for another config file, `--verbose` for debug logging, and `--version`.

## Configuration

Each account is an entry in `config.yaml`:

```yaml
accounts:
  - slug: "my-org"          # the name used on the command line and in paths
    type: "org"             # "user" or "org"
    username: "my-org-name"
    token: "ghp_..."
    include_private: true
    include_forks: false
```

You can also leave repositories out, follow other people's public repositories, report on public repositories without a token, group repositories from several accounts into a project and use `--project NAME` with any command, and add Bitbucket Cloud workspaces. [docs/configuration.md](docs/configuration.md) covers all of it, and `config.example.yaml` lists every option.

## How the activity report counts

Commits, line counts and tags come from the local clones. That's fast, costs no API requests and still works after access to an account has ended. Only pull requests, issues and reviews come from the API. If the API isn't available, the report says so at the top and shows those columns as "n/a" rather than 0.

Date ranges are whole days in one timezone, for git and the API alike. Lock files, vendored code, test fixtures, data exports and binary files are left out of the line counts, and the report lists what was left out for each developer. Someone who commits under several names or email addresses gets one row, and bots get a table of their own. The [activity reference](docs/commands.md#activity) has the detail.

## Nightly sync

`scripts/daily-checkout.sh` runs `auditer checkout --all --full` from the repository root, ready for cron:

```bash
0 4 * * * /path/to/github-auditor/scripts/daily-checkout.sh
```

It logs to `~/.cache/github-auditor/checkout.log`. Set `AUDITER_LOG` to log somewhere else, or `AUDITER_BIN` if `auditer` isn't on cron's `PATH`, for example the `auditer` in a virtualenv.

## Security

- Tokens live in `config.yaml`, which git ignores.
- Git authenticates through a short-lived `GIT_ASKPASS` script, so a token never appears in a clone URL.
- Anything that looks like a GitHub token is masked in the logs.
- Commands run git without a shell.
- Most commands check the token before doing anything else.

## Development

```bash
pip install -e ".[dev]"
pytest                   # 910 tests
```

[docs/development.md](docs/development.md) covers the tests, checking by hand, using the GitHub client from Python and the code layout. [ROADMAP.md](ROADMAP.md) has the planned work.

## Licence

MIT, see [LICENSE](LICENSE).
