# Configuration

`auditer` reads `config.yaml` from the current folder, or the file given with `--config`. Copy `config.example.yaml` to start; it lists every option with comments. `config.yaml` is in `.gitignore`, so your tokens stay out of git. Check it with `auditer config --validate`.

## The basics

```yaml
defaults:
  stale_months: 12          # repositories inactive for longer are "stale"
  data_dir: "data"          # where the clones go
  reports_dir: "reports"    # where the reports go
  log_level: "INFO"         # INFO, DEBUG, WARNING or ERROR
  timezone: "Europe/London" # IANA name; activity date ranges are whole days here

accounts:
  - slug: "my-user"         # short name used in paths and on the command line
    type: "user"            # "user" or "org"
    username: "my-github-username"
    token: "ghp_..."        # a personal access token, or null for public repositories only
    include_private: true   # default: true
    include_forks: true     # default: true
```

The token needs the `repo` scope to read private repositories, or `public_repo` for public ones only.

Optional settings for each account:

| Setting | What it does |
| --- | --- |
| `exclude_repos` | Repositories to skip, matched by name without regard to case |
| `include_repos` | Track only these repositories |
| `data_dir` | Keep this account's clones somewhere else |
| `branch_overrides` | Track another branch instead of the GitHub default, for example `some-repo: "develop"` |
| `active_within_months` | `checkout` only clones or pulls repositories pushed to within this many months |
| `local_only` | `activity` reads the clones only and never calls the API |

## Several accounts

List as many users and organisations as you like, then use `--all` or pick one with `--account`.

```yaml
accounts:
  - slug: "personal"
    type: "user"
    username: "my-username"
    token: "ghp_..."

  - slug: "work-org"
    type: "org"
    username: "my-company"
    token: "ghp_..."
```

```bash
auditer checkout --all --full
auditer audit --account work-org
```

## Choosing repositories

`exclude_repos` is a blocklist and `include_repos` an allowlist. When both are set, `include_repos` is applied first and `exclude_repos` then removes from the result. `checkout` and `audit` print how many were left out, for example "Excluded 2 repo(s) per config."

`include_private: false` and `include_forks: false` leave out private repositories and forks. A repository named in `include_repos` is always included, even if it's a fork or private.

## Where the clones go

Clones go into `{data_dir}/{slug}/public/` and `{data_dir}/{slug}/private/`. Set `data_dir` on an account to put its clones somewhere else; every command that reads the clones looks there too.

```yaml
accounts:
  - slug: "work"
    data_dir: "/mnt/data/work-repos"   # clones in /mnt/data/work-repos/work/public/ and .../private/
```

## Projects

A project groups repositories from several accounts under one name. `accounts` takes every repository in an account, and `repos` takes single repositories as `slug/name`. A project can use both.

```yaml
projects:
  my-project:
    description: "Web platform"
    accounts:
      - work-org
    repos:
      - personal/webapp
      - personal/shared-lib
```

```bash
auditer checkout --project my-project
auditer audit --project my-project
auditer activity --project my-project
auditer checkout --account personal --project my-project     # only that account's repositories in the project
auditer settings --account personal --all --project my-project --has-wiki false
```

For the commands that take `--all` (`checkout`, `audit`, `activity` and `access`), `--project` on its own covers every account with repositories in the project. With `--account`, it covers that account's repositories in the project. Reports go to `reports/{project}/` instead of `reports/{slug}/`.

## Other people's repositories

To follow public repositories you don't own, add the owner as an account and use your own token. That raises the API limit from 60 requests an hour to 5,000.

```yaml
accounts:
  - slug: "famous-dev"
    type: "user"
    username: "famous-dev"     # the user you want to follow
    token: "ghp_..."           # your token
    include_private: false
    include_forks: false
    include_repos:
      - "cool-project"
      - "awesome-lib"
```

The tool sees that the token belongs to someone else and lists repositories through the public `/users/{username}/repos` endpoint instead of `/user/repos`. The reports and `checkout` work as usual.

## Without a token

Set `token: null` to clone and audit public repositories without authenticating. The API then allows 60 requests an hour, and the activity report has commits and tags only.

```yaml
accounts:
  - slug: "public-user"
    type: "user"
    username: "someone"
    token: null
    include_private: false
```

## Developer aliases

The activity report merges most of a person's identities on its own (see [one person, several identities](commands.md#one-person-several-identities)). For the rest, map a GitHub login to the other names or emails that person commits under. Matching ignores case.

```yaml
developer_aliases:
  alice-login:
    - "Alice Smith"
    - "alice@laptop.local"
```

## Bitbucket Cloud

A Bitbucket Cloud workspace can sit alongside GitHub accounts. Set `provider`, `workspace` and `auth_email`:

```yaml
accounts:
  - slug: "my-bitbucket-workspace"
    type: "user"                    # required by validation, ignored for Bitbucket
    provider: "bitbucket"           # "github" (the default) or "bitbucket"
    workspace: "my-workspace-slug"
    username: "my-workspace-display-name"
    auth_email: "me@example.com"    # the Atlassian account's email
    token: "your-atlassian-api-token"
```

Create the token at [id.atlassian.com](https://id.atlassian.com) under Security, API tokens, with the `Repositories:Read`, `Pull requests:Read` and `Issues:Read` scopes. Bitbucket uses HTTP Basic authentication, with `auth_email` as the user name and the token as the password, for both the API and git.

These commands work with Bitbucket: `checkout`, `audit`, `activity`, `access`, `settings`, `readme`, `add-license`, `collaborators list` and `members list`.

These don't, and stop with an error on a Bitbucket account:

- `topics`, `archive` and `profile`, because Bitbucket has no topics, no archived flag for a repository and no profile README.
- `members remove`, because Bitbucket's API can't remove workspace members; that goes through Atlassian Admin.
- `collaborators add` and `collaborators remove`, because Bitbucket only grants repository access to existing workspace members and the API may not accept API tokens for it. It needs testing against a live workspace before it can be built.

Bitbucket has no stars, forks, topics, archived flag or licence field. So the audit report shows 0 stars and forks for Bitbucket repositories, and shows the licence as missing even when there is a licence file. The "not archived" part of the health score always gets full marks. `add-license` reads the file itself, so it does spot an existing licence.
