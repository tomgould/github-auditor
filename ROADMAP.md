# Roadmap

## Current Features

### Repo filtering (include/exclude)
Per-account `include_repos` (allowlist) and `exclude_repos` (blocklist) in `config.yaml`. When `include_repos` is set, only those repos are tracked. `exclude_repos` removes specific repos. Both can be combined: include is applied first, then exclude. Case-insensitive matching. Applied by all commands.

```yaml
accounts:
  - slug: "my-user"
    exclude_repos:
      - "broken-legacy-project"
    include_repos:          # Only track these repos (useful for third-party accounts)
      - "cool-project"
      - "awesome-lib"
```

### Skip dirty repos
`--skip-dirty` flag for checkout: skip repos that have local modifications instead of force-resetting.

### Per-account data directory
Per-account `data_dir` override in `config.yaml` (not a CLI flag). Allows different accounts to check out into different directories.

```yaml
accounts:
  - slug: "work-org"
    data_dir: "/mnt/data/work-repos"
```

### Local-first audit
`audit` scans locally checked-out repos (from `data_dir`) instead of relying solely on the GitHub API. Enables auditing local changes before committing. Respects per-account `data_dir` overrides.

### Shared repo filtering helper
Common repo filtering logic (include/exclude lists, fork/private filtering) in `src/auditer/filtering.py`, used by `checkout`, `audit`, and all management commands. Repos explicitly listed in `include_repos` bypass `include_forks` and `include_private` filters.

### `auditer config`
Validate or display configuration. `--validate` checks config.yaml structure and reports account count. `--show` displays all accounts in a Rich table with masked tokens.

### GitHub API methods
25 public methods in `github_client.py` covering repos (`list_repos`, `get_repo`, `has_readme`), users (`get_user`, `get_authenticated_user`, `validate_token`), events (`list_user_events`), org members (`list_org_members`, `remove_org_member`), collaborators (`list_collaborators`, `add_collaborator`, `remove_collaborator`), repo settings (`update_repo`), topics (`get_topics`, `set_topics`), file content (`get_file`, `create_or_update_file`), pull requests (`list_pulls`, `list_pull_reviews`), issues (`list_repo_issues`), review comments (`list_review_comments`), pagination (`get_paginated`), and rate limits (`get_rate_limit`).

### `auditer members`
List and remove organisation members. Click group with `list` and `remove` subcommands. Org-only guard, Rich table output, destructive remove requires confirmation.

### `auditer collaborators`
List, add, and remove repository collaborators. Click group with `list`, `add`, `remove` subcommands. Supports single repo or `--all-repos` bulk operations with confirmation. Permission levels: pull/push/admin/maintain/triage.

### `auditer archive`
Bulk archive stale repos. Uses `RepoInfo.is_stale()` with configurable `--months` threshold. Skips already-archived repos. Specific repo mode with `--repo`. Destructive — requires confirmation. Respects `exclude_repos`.

### `auditer add-license`
Mass-add license files to repos missing one. Supports MIT, Apache-2.0, GPL-3.0, BSD-2-Clause, BSD-3-Clause templates. `--owner-name` override (defaults to GitHub profile name). Checks for existing LICENSE before adding. Respects `exclude_repos`.

### `auditer topics`
Click group with `list`, `set`, `add` subcommands. List uses repo topics from `list_repos()` (no extra API calls). Set replaces all topics (comma-separated). Add appends a topic, skips if already present. Supports single repo or all repos.

### `auditer settings`
Bulk update repo settings. Options: `--description`, `--homepage`, `--has-issues`, `--has-wiki`, `--has-projects`, `--default-branch`, `--visibility`. Confirms on `--all` or visibility changes. Respects `exclude_repos`.

### `auditer access`
Security-focused access report showing who has access to which repos and at what permission level. Two views: **Access Matrix** (users as rows, repos as columns, highest permission in cells) and **Per-Repo Detail Tables** (full permission breakdown per collaborator). `--sort name` (default) or `--sort id` (ascending GitHub user ID — lower = older account). Fetches collaborators via API with threaded requests. Requires authentication. Respects `exclude_repos` and visibility filters. Report written to `reports/{slug}/access_report.md`.

### Health score
Composite 0-100 health score per repo, integrated into the audit report. Weighted components: README (20), license (20), description (15), activity recency (25), not-archived (10), low open issues (10). Shown as a Health column in repo tables, summary stats (average health, low-health count), and a dedicated Low Health Repos (<50) section listing what's missing per repo.

### `auditer profile`
Generate a GitHub profile README from API data. `--account SLUG` or `--all`. Fetches user profile, repos, and recent public events. Generates a 6-section Markdown README (Header, About, Stats, Top Repositories, Languages with visual bar chart, Recent Activity) to `reports/{slug}/profile_readme.md`. Works with or without a token. Terminal summary shows name, repos, stars, forks, followers, and top languages.

### `auditer activity`
Developer activity tracking. Hybrid approach: local git for commits and tags (fast, no API limits), GitHub API for PRs/issues/reviews. `--account SLUG` or `--all`. `--user USERNAME` to filter to a developer. `--repo REPO` to filter to a repo. `--since`/`--until` for date range (default: last 30 days). All filters combinable (e.g. `--account SLUG --user USER --repo REPO --since DATE`). Threaded repo processing with Rich progress bar. Merge commits excluded (`--no-merges`) for accurate line counts. Line counts exclude non-authored content via `EXCLUDED_PATHSPECS`: lock files (`package-lock.json`, `yarn.lock`, `composer.lock`, etc.), vendored directories (`vendor/`, `node_modules/`), WordPress core (`wp-admin/`, `wp-includes/`, `wp-content/plugins/`), and minified/generated assets (`*.min.js`, `*.min.css`, `*.map`). Report includes: Summary table (with merged/unmerged PRs and tags), Developer Leaderboard (sorted by commits, with merged/unmerged PRs, tags created), Daily Breakdown (every day in the date range including zeros), Weekly Breakdown (all weeks shown as date ranges), Per-Repo Activity (merged/unmerged PRs, tags, top contributor), Per-Repo Detail (contributor commit counts), Developer Detail (per-developer commits oldest-first with GitHub commit links, PRs with links and merge status, tags with release links). Aggregates developers by email across repos with deduplication (merges API-sourced login entries into commit-based email entries, merges same-name developers across repos). Developer names shown as "Name (@login)" when GitHub login is available. Works without a token (commits and tags from local git). 4 API methods: `list_pulls`, `list_pull_reviews`, `list_repo_issues`, `list_review_comments`. Counts submitted PR reviews (Reviews, with Approvals as a subset) separately from inline review comments, skipping reviews on the reviewer's own PR and body-less "commented" reviews (inline-thread replies); fetches reviews only for PRs updated within the date range; each developer's reviews are listed in Developer Detail. With `--branches`, the Summary, Daily/Weekly breakdowns and Per-Repo tables include feature-branch commits so they match the Developer Leaderboard. Bot accounts (`[bot]` suffix) sit in a separate Automation table, out of the leaderboard but inside the Summary totals. Developers are merged on a normalised name (so `Anton Zorin`/`Anton-Zorin` and `Fiona Vale`/`fionavale` collapse to one row). Commit dates are committer dates so Daily/Weekly sum to the total; weeks cut by the range are shown as `(partial)`. PR columns are Merged (merged in range) and Open (still open at the end of the range, opened or updated in it), so neither goes negative. Branch commits are deduplicated by patch-id (rebased copies of landed work don't count twice) and branches whose PRs are all closed are skipped. `developer_aliases` in config.yaml folds a person's other git names/emails into their login. Line counting excludes binary files by extension (git misdetects NUL-free JPEGs as text) and vendored/library/built directories at any depth. `--local-only` flag / `local_only: true` account setting report from local checkouts without touching the API, and an API login failure falls back to the same mode with a Data source line in the report instead of aborting. API reliability: pre-flight rate-limit print, the GitHub client waits for the rate-limit reset instead of failing, `list_pulls(since=)` stops paginating at the date range, and any failed API call is logged at WARNING, flagged red in the terminal and listed in a **⚠ Data quality** block in the report header. Report filename encodes options used (e.g. `activity_2026-02-08_2026-02-14_user-alice.md`) so multiple reports coexist in `reports/{slug}/`.

Accuracy fixes (Oct 2026): `--since`/`--until` are whole days in `defaults.timezone` (Europe/London) for git and API data alike (git used to take the current time of day); tag and commit parsing no longer drops a record whose last fields are empty (the last annotated tag in each repo was lost); every clone is fetched before scanning (`--no-fetch` to skip) with a **Data as of** header line; merges are credited (Merge Commits from git, Merges from GitHub's `merged_by`) without adding lines, and PR conversation comments are counted apart from inline review comments; data files (CSV/TSV/JSONL/NDJSON, and JSON/XML under data-like directories) and nested lock files are excluded from line counts, with per-developer excluded totals shown in a Data Quality section; closed-unmerged PRs count as closed, and PRs open at the end of the window are listed when opened or updated during it; `--account A --account B` (or `--all/--project --combine`) produces one combined report with one leaderboard row per person; the GitHub client follows repo redirects, retries secondary rate limits (429) with backoff, and treats an empty repo's 409 as no commits. Follow-up: editor config is excluded with glob pathspecs (`**/.vscode/*.json`) instead of `.vscode/**`, so code kept under `.vscode/` counts; local-only reports show PR/review figures as n/a; a failed fetch is recognised by its empty FETCH_HEAD and the Data as of line falls back to the reflogs; duplicate patches across branches are credited to the earliest copy; developers are also merged on email local part. Checked against an independent git count for a 398-repo Bitbucket account (11 weeks): every developer and repo matched.

### Third-party account tracking
Track public repositories from GitHub users or organisations you don't own. Configure with your own token (for higher API rate limits) and a different `username`. The client detects that the token holder differs from the configured username (case-insensitive comparison via `_is_own_account()`) and automatically uses `/users/{username}/repos` instead of `/user/repos`. All commands work with third-party accounts: checkout, audit, activity, readme, etc.

### `auditer readme`
Per-repo README generator. Scans local checkouts, detects project structure (package manager, tests, CI, entry points, languages, framework, key dependencies), enriches via GitHub API (description, topics, license, stars, forks). Generates Markdown README to `reports/{slug}/readmes/{repo_name}.md`. Supports single repo (`auditer readme REPO --account SLUG`) or batch (`--all` for repos missing a README, `--force` to regenerate all). `--push` commits the README directly to GitHub via the Contents API (creates or updates, requires confirmation and token). Package managers: npm, Composer, pip (tomllib/tomli), Cargo, Go. Framework detection: Symfony, Laravel, React, Vue, Django, Flask, FastAPI, and more. Hierarchical box-drawing project tree (3 levels deep). Languages table with file counts and percentages. README sections: Title, Stats, Topics, Framework, Installation, Dependencies, Usage, Project Structure, Testing, Languages, License, Footer.

### Projects
Cross-account repo grouping via named projects in `config.yaml`. `--project NAME` option on all repo-listing commands (checkout, audit, activity, access, readme, archive, add-license, topics list/add, settings) filters to only repos in the project. For commands with `--account`/`--all` (checkout, audit, activity, access), `--project` alone operates on all accounts that have repos in the project. Reports are written to `reports/{project-name}/` when a project is active. `ProjectConfig` dataclass, `get_project()` and `get_accounts_for_project()` helpers in config, `filter_by_project()` in filtering module.

```yaml
projects:
  my-project:
    repos:
      - "personal/webapp"
      - "work-org/api-service"
```

### Console and client hardening
All `Console()` instances use `soft_wrap=True, force_terminal=True` for correct ANSI output when redirected to logs. `get_paginated()` has an absolute safety limit of 1000 pages to prevent infinite loops. `get_paginated(stop_when=)` ends pagination early on sorted listings. On a primary rate limit (403 + `x-ratelimit-remaining: 0`) the client waits for `x-ratelimit-reset` (capped at 3,700 s, logged once across threads) and retries once before raising `RateLimitError`. `GitHubClient` in `activity.py` uses try/finally for reliable cleanup on exceptions.

### Bitbucket Cloud support (checkout, audit, activity, settings, readme, add-license, access, collaborators list, members list)
Track repos hosted on Bitbucket Cloud via `provider: "bitbucket"` on an account, alongside GitHub accounts. A `GitProvider` Protocol (`git_provider.py`) plus a `get_provider()` factory pick the right client (`GitHubClient` or `BitbucketClient`) per account — `checkout`, `audit`, `activity`, `settings`, `readme`, `add-license`, `access`, `collaborators list`, and `members list` call the Protocol's structural interface without knowing which provider they're talking to. `BitbucketClient` (`bitbucket_client.py`) is a Bitbucket Cloud REST API v2.0 client: workspace-based (`account.workspace` replaces GitHub's user/org concept), HTTP Basic auth via `account.auth_email` + an API token, and translates Bitbucket's repo/PR/issue/commit shapes into GitHub-shaped dicts so `RepoInfo` and the rest of the reporting layer stay provider-agnostic. `git_ops.clone_repo()`/`fetch_and_pull()` accept an `auth_username` param so git-over-HTTPS auth also matches Bitbucket's Basic-auth convention (`account.auth_email`, vs GitHub's default `oauth2`). `update_repo()` maps `description`/`has_issues`/`has_wiki`/`default_branch`/`visibility` onto Bitbucket's repository fields; `homepage` and `has_projects` have no Bitbucket equivalent and are silently dropped (`settings` prints a console warning for `--has-projects` on Bitbucket accounts). `add-license` and `readme` both go through `get_provider()` end to end, including writes (`create_or_update_file()` via Bitbucket's multipart `/src` POST): `add-license`'s "already has a LICENSE" check (`get_file()`) reads the actual file rather than an API field, so it correctly detects existing LICENSE files on Bitbucket even though the audit report's license field can't; `readme` always shows 0 stars/forks and no topics/license for Bitbucket repos (same gaps as `audit`). `members list` skips the org-only guard for Bitbucket accounts (workspace-scoped, no user/org distinction) and passes `account.workspace` to `list_org_members()`. `members remove` is a deliberate, permanent exception rather than a "not yet wired" gap: Bitbucket Cloud's REST API has no endpoint for removing workspace members at all (membership changes go through Atlassian Admin or SCIM only), so it prints an explicit error for Bitbucket accounts instead of attempting the call. `access` is fully wired to `get_provider()` — `BitbucketClient.list_collaborators()` maps Bitbucket's 3-level read/write/admin workspace-permission scale onto GitHub's 5-level admin/maintain/push/triage/pull shape (`maintain`/`triage` always `False`), so the Access Matrix and Per-Repo Detail views work unchanged for Bitbucket accounts. `collaborators list` is likewise wired to `get_provider()` and shares the same permission mapping; `collaborators add`/`collaborators remove` are NOT wired — they still hardcode `GitHubClient` and print an explicit guard error for Bitbucket accounts (`account.provider == "bitbucket"`) explaining that Bitbucket's per-repo permission-grant API requires the target user to already be a workspace member (no GitHub-style "invite an outside collaborator"), may return 402 Payment Required on Bitbucket's Free plan tier, and may only support legacy App Password auth rather than the API token auth this tool uses — so implementing them needs verification against a live Bitbucket workspace first. Known gaps versus GitHub accounts: no stars/forks, no topics, no repo-level archived flag, no license API field — `audit`'s health score and stats, and the generated `readme`, reflect these as 0/missing for Bitbucket repos. Wired into `checkout`, `audit`, `activity`, `settings`, `readme`, `add-license`, `access`, `collaborators list`, and `members list` — see "Full Bitbucket parity" below for the remaining gaps (`collaborators add`/`remove`, `topics`, `archive`, `profile`, `members remove`).

```yaml
accounts:
  - slug: "my-bitbucket-workspace"
    provider: "bitbucket"
    workspace: "my-workspace-slug"
    auth_email: "me@example.com"
    token: "ATATTxxxx..."
```

### Security and robustness hardening
`_make_askpass_script()` creates temp files with restrictive permissions (`0o700`) from the start via `os.open()` with `O_CREAT | O_EXCL` — no chmod race window. Cleanup failure logs a warning instead of silently passing. `_language_bar()` clamps percentage to 0-100 to prevent negative or oversized output. `_build_tree()` has a `max_lines` cap (default 500) to prevent unbounded output from large repos. Test coverage added for: malformed `pyproject.toml` regex fallback, `_merge_by_name` boundary conditions (2/3/4 char names, multi-word short names), single-week date ranges.

---

## Planned

### JSON output mode
Add `--json` flag to all reporting commands for CI/automation pipelines.

### Semantic code search
Index the data directory for semantic code search across all checked-out repos.

### Diff reports
Track changes between audit runs. Show new/removed repos, changed stats, new issues.

### Notifications
Optional alerts (email, webhook, desktop) when significant changes are detected during checkout.

### Search/filter command
`auditer search` — query repos by language, stars, staleness, topics, size, etc.

### Full Bitbucket parity (collaborators add/remove, topics, archive, profile)
Extend Bitbucket support beyond `checkout`/`audit`/`activity`/`settings`/`readme`/`add-license`/`access`/`collaborators list`/`members list` to the remaining commands and subcommands. `collaborators add`/`collaborators remove` need live-workspace verification, not just a mapping decision (the read-side mapping is already settled and shared with `access`/`collaborators list`): confirming whether the target user must already be a workspace member before a repo-level grant can be made, whether the Free plan tier's 402 Payment Required response needs special handling, and whether the grant endpoint accepts API token auth at all or only legacy App Passwords. `topics`, `archive`, and `profile` have no clean Bitbucket equivalent at all and need a product decision, not just engineering work: Bitbucket Cloud has no repo-level "archived" flag (`archive` relies on this), no topics/labels concept (`topics`), and no GitHub-profile-README convention (`profile` relies on a repo named exactly after the account's username being rendered on a profile page) — there is nothing to map these onto short of inventing a Bitbucket-specific substitute. `members remove` is not part of this parity work — it has no path to parity at all, since Bitbucket Cloud's REST API doesn't expose a workspace-member-removal endpoint under any circumstance.

`topics`, `archive`, and `profile` now fail fast on Bitbucket accounts instead of erroring confusingly partway through: each checks `account.provider == "bitbucket"` up front and exits with a clear "this command is GitHub-only" message (no engineering toward parity yet — this is just an explicit guard for a case with no Bitbucket equivalent at all).
