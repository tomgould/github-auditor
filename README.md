# GitHub Auditor

CLI tool for auditing and managing GitHub repositories across personal and organisation accounts. Supports multiple accounts, generates Markdown reports, and keeps local checkouts organised.

## Setup

The project is not on PyPI yet, so install it from a clone. The repository is called `github-auditor`; the Python package is `github-auditer` (imported as `auditer`) and it installs one command, `auditer`.

```bash
git clone https://github.com/tomgould/github-auditor.git
cd github-auditor

# Install (editable mode with dev dependencies), ideally inside a virtualenv
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml — add your GitHub PAT(s) and account details
```

Your PAT needs the `repo` scope (for private repos) or just `public_repo` for public-only auditing.

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| click | >= 8.1 | CLI framework |
| httpx | >= 0.27 | HTTP client (GitHub API) |
| rich | >= 13.0 | Terminal output (tables, colours) |
| pyyaml | >= 6.0 | YAML config parsing |

Dev dependencies: pytest, pytest-cov, respx (HTTP mocking), black, ruff.

Requires Python 3.10+.

## Commands

### Quick reference

| Command | What it does | Scope |
|---|---|---|
| `auditer config --validate` | Check config is valid | global |
| `auditer config --show` | Display config (tokens masked) | global |
| `auditer checkout --account SLUG` | Clone/pull repos for one account | single |
| `auditer checkout --all --full` | Clone/pull all accounts, all branches+tags | all |
| `auditer audit --account SLUG` | Generate audit report for one account | single |
| `auditer audit --all` | Generate audit reports for all accounts | all |
| `auditer activity --account SLUG` | Developer activity report for one account | single |
| `auditer activity --all` | Developer activity reports for all accounts | all |
| `auditer activity --account SLUG --user USER` | Activity filtered to one developer | single |
| `auditer activity --account SLUG --repo REPO` | Activity filtered to one repo | single repo |
| `auditer activity --account SLUG --user USER --repo REPO` | One developer's activity in one repo | single repo |
| `auditer activity --account SLUG --since 2025-01-01` | Activity for a custom date range | single |
| `auditer activity --account SLUG --user USER --repo REPO --since DATE` | Most targeted: one user, one repo, date range | single repo |
| `auditer activity --account SLUG --branches` | Activity with feature branch breakdown | single |
| `auditer activity --account SLUG --local-only` | Activity from local checkouts only, no API calls | single |
| `auditer activity --account SLUG --no-fetch` | Activity without fetching the clones first | single |
| `auditer activity --account A --account B` | One combined report across several accounts | combined |
| `auditer activity --all --combine` | One combined report across all accounts | combined |
| `auditer access --account SLUG` | Generate access report for one account | single |
| `auditer access --all` | Generate access reports for all accounts | all |
| `auditer access --account SLUG --sort id` | Access report sorted by user ID (oldest first) | single |
| `auditer access --all --sort id` | Access reports sorted by user ID for all accounts | all |
| `auditer profile --account SLUG` | Generate profile README for one account | single |
| `auditer profile --all` | Generate profile READMEs for all accounts | all |
| `auditer readme REPO --account SLUG` | Generate README for one repo | single repo |
| `auditer readme --all --account SLUG` | Generate READMEs for repos missing one | single |
| `auditer readme --all --force --account SLUG` | Generate READMEs for all repos | single |
| `auditer readme REPO --account SLUG --push` | Generate and push README to GitHub | single repo |
| `auditer members list --account SLUG` | List org members | single org |
| `auditer members list --all` | List members for all org accounts | all orgs |
| `auditer members remove USER --account SLUG` | Remove org member | single org |
| `auditer collaborators list --account SLUG` | List collaborators on all repos | single |
| `auditer collaborators list --account SLUG --repo REPO` | List collaborators on one repo | single repo |
| `auditer collaborators add USER --account SLUG --repo REPO` | Add collaborator to one repo | single repo |
| `auditer collaborators add USER --account SLUG --all-repos` | Add collaborator to all repos | all repos |
| `auditer collaborators remove USER --account SLUG --repo REPO` | Remove collaborator from one repo | single repo |
| `auditer collaborators remove USER --account SLUG --all-repos` | Remove collaborator from all repos | all repos |
| `auditer archive --account SLUG` | Archive stale repos (interactive) | single |
| `auditer archive --account SLUG --repo REPO` | Archive a specific repo | single repo |
| `auditer add-license --account SLUG --license MIT` | Add license to repos missing one | single |
| `auditer add-license --account SLUG --repo REPO --license MIT` | Add license to a specific repo | single repo |
| `auditer topics list --account SLUG` | List topics for all repos | single |
| `auditer topics set TOPICS --account SLUG --repo REPO` | Replace topics on a repo | single repo |
| `auditer topics add TOPIC --account SLUG` | Add topic to all repos | all repos |
| `auditer settings --account SLUG --repo REPO --description "..."` | Update settings on one repo | single repo |
| `auditer settings --account SLUG --all --has-wiki false` | Update settings on all repos | all repos |
| `auditer checkout --project my-proj` | Checkout repos in a project (all accounts) | project |
| `auditer audit --project my-proj` | Audit repos in a project (all accounts) | project |
| `auditer activity --project my-proj` | Activity report for repos in a project | project |
| `auditer access --project my-proj` | Access report for repos in a project | project |
| `auditer readme --all --account SLUG --project my-proj` | Generate READMEs for project repos | project |
| `auditer archive --account SLUG --project my-proj` | Archive stale repos in a project | project |
| `auditer add-license --account SLUG --license MIT --project my-proj` | Add license to project repos | project |
| `auditer topics list --account SLUG --project my-proj` | List topics for project repos | project |
| `auditer settings --account SLUG --all --project my-proj --has-wiki false` | Update settings on project repos | project |

---

### `auditer config` — Validate and display configuration

```bash
# Check config.yaml is valid and parseable
auditer config --validate
# Output: "Config is valid. 3 account(s) configured."

# Display config with masked tokens (safe to share)
auditer config --show
# Output: table with slug, type, username, token (last 4 chars only),
# include_private, include_forks, data_dir, include_repos, and exclude_repos for each account
```

### `auditer checkout` — Clone and sync repositories

Clone all repos for an account locally, or pull latest changes for existing checkouts.

```bash
# Clone/pull one account
auditer checkout --account my-user

# Clone/pull all configured accounts
auditer checkout --all

# Full sync: all branches + all tags
auditer checkout --all --full

# Or pick individually
auditer checkout --account my-user --branches
auditer checkout --account my-user --tags
auditer checkout --account my-user --branches --tags   # Same as --full

# Preview what would happen without making changes
auditer checkout --account my-user --dry-run

# Force-update repos even if they have uncommitted local changes
auditer checkout --account my-user --no-skip-dirty

# Only checkout repos with activity in the last 12 months (one-off override)
auditer checkout --account my-user --months 12
```

Repos are cloned into `data/{slug}/public/` and `data/{slug}/private/`. Running again pulls latest changes.

**Filtering by activity:** set `active_within_months: 12` on an account in `config.yaml` to make this the persistent default for that account only — every other account keeps checking out everything unless it also sets the field. `checkout --months N` overrides the account's config value for a single run (either direction — set a stricter or looser threshold ad hoc).

**Output includes:**
- Per-repo branch updates with commit count, authors, and individual commit messages
- Force push detection with added/removed commit counts
- New branches and tags discovered
- GitHub URLs: compare links for updated branches, commit links, tree links for new branches, release links for new tags
- Orphaned repo detection (local repos deleted from GitHub)
- Summary with totals for cloned, updated, skipped, errors, branches, tags, and commits

### `auditer audit` — Generate repository audit reports

The audit command uses a **local-first** approach: it scans your locally checked-out repos (from `data/`) rather than relying solely on the GitHub API.

```bash
# Audit one account
auditer audit --account my-user

# Audit all configured accounts
auditer audit --all

# Check the generated report
cat reports/my-user/audit_report.md
```

**How it works:**
1. Scans `data/{slug}/public/` and `data/{slug}/private/` for git checkouts
2. Extracts local metadata: README/LICENSE presence, default branch, last commit date, repo size
3. Enriches each repo via the GitHub API (`GET /repos/{owner}/{name}`) for stars, forks, language, description, topics, etc.
4. Gracefully handles deleted repos (404 from API) — they still appear in the report with default values
5. Generates a detailed Markdown report to `reports/{slug}/audit_report.md`

**The report checks for:**
- Missing README, License, Description
- Stale repos (configurable threshold, default 12 months)
- Health score (0-100) per repo — composite metric based on README, license, description, activity, archive status, and open issues
- Stars, forks, language, size
- Pin recommendations (ranked by stars, forks, recency)

You must run `auditer checkout` at least once before `auditer audit`, since audit scans local checkouts. If no local repos are found, audit prints a helpful message telling you to run checkout first.

### `auditer activity` — Developer activity tracking

Track developer activity (commits, PRs, issues, code reviews) across repositories. Uses local git for commit data and the GitHub API for PRs/issues/reviews. Resolves git author emails to GitHub logins via the Commits API for accurate developer merging — the same developer appearing with different emails or names is correctly identified as one person.

```bash
# Activity report for one account (last 30 days)
auditer activity --account my-user

# Activity for all accounts
auditer activity --all

# Custom date range
auditer activity --account my-user --since 2025-01-01 --until 2025-06-30

# Filter to a specific developer
auditer activity --account my-user --user alice

# Filter to a specific repo
auditer activity --account my-user --repo my-project

# Single user + single repo + date range (most targeted view)
auditer activity --account my-user --user alice --repo my-project --since 2025-01-01

# Single user across all their repos
auditer activity --account my-user --user alice --since 2025-01-01 --until 2025-06-30

# Include feature branch activity (separate section, no double-counting)
auditer activity --account my-user --branches

# Report from the local checkouts only, never calling the API (no PRs/issues/reviews)
auditer activity --account old-client --since 2026-08-01 --local-only

# One combined report across an organisation and a personal account:
# one leaderboard row per person, repos labelled owner/name
auditer activity --account my-org --account my-user --since 2026-10-10
# → reports/combined/activity_2026-10-10_<today>_accounts-my-org+my-user.md

# Skip the git fetch that runs before every scan (uses the clones as they are)
auditer activity --account my-user --no-fetch

# Check the generated report (filename includes options used)
ls reports/my-user/activity_*.md
cat reports/my-user/activity_2025-01-01_2025-06-30.md
```

**The report includes:**
- Contributors lookup table mapping each developer's display ID (@login or email) to their human name and email
- Header lines for the date range (whole days, with the timezone) and **Data as of**: when the clones were fetched, or that the fetch was skipped
- Summary table (active repos, developers, commits, merge commits, lines added/deleted, net lines, PRs opened/merged/closed without merging/still open, issues, PR reviews submitted, PR approvals, review comments, PR conversation comments, tags)
- Developer leaderboard (sorted by net lines, with commits, merge commits, lines added/deleted, net lines, merged PRs, merges, open PRs, issues, reviews, approvals, review comments, PR comments, tags created) — uses @login as primary identifier. "Merged PRs" is PRs they wrote that were merged in the range, whenever opened; "Merges" is PRs they merged, whoever wrote them (GitHub's `merged_by`); "Merge Commits" is git merge commits they authored, never part of Commits or line counts; "Open PRs" is PRs they wrote that were still open at the end of the range and opened or updated during it (a PR opened before the range and worked on in it counts; one untouched for months doesn't; a PR closed without merging is closed, not open). "PR Comments" are comments in the PR conversation; "Review Comments" are inline comments on the diff
- Data Quality: Lines Excluded From Counts: per developer, the lines left out of +Lines/-Lines by the exclusions below, so a large data import is visible rather than silently dropped
- Automation table: bot accounts (`renovate[bot]`, `dependabot[bot]`, any login or name ending `[bot]`) in the same columns, kept out of the leaderboard, contributors, top-contributor cells and developer detail but still counted in the Summary totals, which show an "of which by bots" commit line so Leaderboard + Automation reconciles to the total
- Daily breakdown (every day in the date range, including days with 0 commits, with net lines)
- Weekly breakdown (all weeks in range, shown as date ranges, with net lines; weeks cut by the date range are clipped and labelled `(partial)`, so every commit in range lands in a row)
- Per-repo activity table (sorted by net lines) with net lines, merged/open PRs, reviews, tags, and top contributor
- Per-repo detail (sorted by net lines) with contributor tables sorted by net lines
- Branch activity (with `--branches`): per-repo tables of unmerged feature branch commits with net lines and top contributor
- Per-developer detail (sorted by net lines): commits listed oldest-first with GitHub commit links, PRs with links and their state at the end of the range, PRs they merged (any author), PR reviews submitted (oldest-first, with PR author and outcome), tags with release links
- Combined reports only: a Per-Account Summary table

With `--branches`, the Summary, Daily/Weekly breakdowns and Per-Repo tables count feature-branch commits alongside default-branch commits, so every total matches the Developer Leaderboard; the Branch Activity section shows which of those commits are still unmerged. Feature-branch commits are deduplicated by git patch-id rather than SHA, so a rebased or cherry-picked copy of work already on the default branch is not counted twice, and the same patch on two branches counts once, credited to the earliest-committed copy (the original, not a later "pulled in" re-commit on a QA branch by someone else). Branches whose every PR is closed (merged or declined) are skipped, since that work has either landed on the default branch or been abandoned.

**Date range.** `--since`/`--until` are whole calendar days in `defaults.timezone` (Europe/London unless set in `config.yaml`): `--since 2026-10-10 --until 2026-10-16` is 10 Oct 00:00 to 16 Oct 23:59:59 London time, whenever the report is run. Git gets full timestamps (a bare date makes git use the current time of day, which once dropped every commit made before 18:13 on the first day), and API timestamps, which are UTC, are compared as London dates.

**Fresh data.** Before scanning, every clone is fetched (`git fetch --all --prune --tags --force`, using the account token; the working tree and local branches are left alone), so work pushed since the last `checkout` is in the report. The header's **Data as of** line says when; a repo whose fetch failed is listed in a **⚠ Stale git data** block. A failed fetch empties `.git/FETCH_HEAD` but still updates its timestamp, so for those clones the time shown is their last successful fetch, read from the reflogs; the line says how many clones that applies to (for an account whose access has ended, all of them). `--no-fetch` skips the fetch, and accounts with `local_only: true` are never fetched.

**Combined reports.** Repeat `--account` (or add `--combine` to `--all`/`--project`) to scan several accounts into one report: developers are merged across accounts by login, so someone working in an organisation and in their own repos gets one leaderboard row. Repos are labelled `owner/name` and the report goes to `reports/combined/` (or the project's directory). A single `--account` behaves exactly as before.

**Commit dates** are committer dates, not author dates. git selects commits for the date range by committer date, so this keeps the Daily and Weekly breakdowns summing to the total; it also means rebased or cherry-picked work counts on the day it was rebased, not the day it was first written.

**Duplicate developers** that automatic matching can't resolve (a git author name with a non-GitHub email, as with someone committing from a second account) are folded together with a `developer_aliases` map in `config.yaml`: keys are GitHub logins, values are the other names or emails that person commits under (see `config.example.yaml`). Beyond that, developers are merged when they share a normalised name (lowercase, letters and digits only), a login, or an email local part: `Anton Zorin` and `Anton-Zorin` become one row, as do a commit-only `Fiona Vale` and the `fionavale` GitHub login, and `sarah.campion@acmecorp.example` and `sarah.campion@acmepublishing.example`. Matches are transitive. Names and logins under four characters, local parts under eight, generic mailboxes (`admin`, `developer`, ...) and noreply addresses are never merged on; anything else goes in `developer_aliases`.

**Working without API access.** Everything except PRs, issues and reviews comes from the local checkouts, so an account whose API access has ended (a finished client engagement, a revoked token) can still be reported on. Pass `--local-only` for one run, or set `local_only: true` on the account in `config.yaml` to make it permanent; either skips the API entirely, and the report shows PR, issue, review and comment figures as **n/a** rather than 0, since they are unknown. If the API login fails for any other account, the command no longer aborts: it warns, continues from git alone, and the report header carries a **Data source: Local git only** line giving the reason, so the missing PR and review columns are never mistaken for zero activity.

**API data reliability.** Before scanning, the command prints the GitHub token's remaining requests and reset time, read from the rate-limit headers of a real request (yellow when under 500). If the token's 5,000/hour limit runs out mid-scan, the client logs a warning and waits for the reset (at most about an hour) instead of failing. PR listing stops paginating once it reaches PRs last updated before `--since`, so a repo with thousands of historical PRs costs one or two requests, not dozens. Any API call that still fails is logged at WARNING, listed in red in the terminal summary ("API data incomplete for N repo(s)"), and recorded in a **⚠ Data quality** block under the report's Mode line naming each repo and call, so a degraded report can never be mistaken for a quiet week.

**Reviews vs review comments.** "Reviews" counts submitted PR reviews (approve, comment, request changes) on other people's PRs, with "Approvals" as the approved subset. "Review comments" counts inline line comments. A review left only as inline comments shows up under review comments, not reviews. Reviews on your own PR are not counted. On Bitbucket, reviews are approvals and change requests, since Bitbucket has no "comment review". Fetching reviews costs one API request per PR updated within the date range.

All filters can be combined. `--user` filters commits, PRs, issues, and reviews to only that developer (matches name, email, or GitHub login). `--repo` restricts to a single repository. `--since`/`--until` set the date range (default: last 30 days). The most targeted query — `--account SLUG --user USER --repo REPO --since DATE` — shows exactly one developer's activity in one repo within a date range.

Report filenames encode the options used, so multiple reports coexist without overwriting: `activity_2026-02-08_2026-02-14.md`, `activity_2026-02-08_2026-02-14_user-alice.md`, `activity_2026-02-08_2026-02-14_repo-my-project.md`, `activity_2026-02-08_2026-02-14_user-alice_repo-my-project.md`.

Commit data comes from local git (`git log --no-merges`). By default, `--all` is used for comprehensive counting — this includes work on all branches including unmerged feature branches. Use `--branches` to split the view: default branch commits are counted separately via `origin/{default_branch}`, and unmerged feature branch work appears in a dedicated "Branch Activity" section with no double-counting. Merge commits are not counted as commits and add no lines; they are counted separately as Merge Commits (from the same refs). A commit that only touches excluded files still counts as a commit, with 0 lines. **Line counts exclude non-authored content**: lock files at any depth (`package-lock.json`, `yarn.lock`, `composer.lock`, etc.), data files (CSV, TSV, JSONL and NDJSON anywhere; JSON and XML only under `data/`, `attachments/`, `exports/` or `dumps/`, because elsewhere JSON and XML are usually config such as `package.json`), vendored directories (`vendor/`, `node_modules/`), WordPress core files (`wp-admin/`, `wp-includes/`, `wp-content/plugins/`), minified/generated assets (`*.min.js`, `*.min.css`, `*.map`), built output (`dist/`), committed libraries (`third-party/`, `libraries/`, `jspm_packages/`, `.yarn/`), recorded test data (`mocks/`, `__mocks__/`, `fixtures/`, `__fixtures__/`, `__snapshots__/`, `*.snap`, `*.har`, `*.bundle`) and binary files by extension (images, fonts, PDFs, archives, media, compiled blobs). Directory patterns apply at any depth, so `drupal/vendor/` is excluded as well as `vendor/`. Editor configuration is excluded (`.idea/`, `*.code-workspace`, and the JSON/snippet files directly inside any `.vscode/` directory), but source code below `.vscode/`, such as a VS Code extension kept in `.vscode/extensions/`, is counted. Binary extensions are listed explicitly because git only recognises a file as binary if there is a NUL byte in its first 8KB; a JPEG without one is diffed line by line and every byte-row counts as a line, which once put a 20,000-line image commit at the top of a report. The full exclusion list is in `git_ops.EXCLUDED_PATHSPECS`. Tags are extracted from local git refs (`git for-each-ref refs/tags/`), handling both annotated and lightweight tags. With a token, also fetches PRs, submitted PR reviews, issues, review comments, and API commits (for email→login mapping) via the GitHub API. Works without a token (commits and tags only, no login resolution). Respects `exclude_repos`.

### `auditer access` — Generate access reports

Generate a security-focused report showing who has access to which repos and at what permission level.

```bash
# Access report for one account
auditer access --account my-user

# Access report for all accounts
auditer access --all

# Sort users by GitHub user ID (lower = older account)
auditer access --account my-user --sort id

# Check the generated report
cat reports/my-user/access_report.md
```

**The report includes two views:**

1. **Access Matrix** — summary table with users as rows, repos as columns, and the highest permission level in each cell (admin > maintain > push > triage > pull)
2. **Per-Repo Detail Tables** — one table per repo showing each collaborator's full permission breakdown (Admin/Maintain/Push/Triage/Pull)

The terminal summary shows total repos, unique collaborators, total access entries, and repos with 2+ collaborators.

Options: `--sort name` (default, alphabetical) or `--sort id` (ascending GitHub user ID — lower ID means older account).

Requires a token (the collaborators API endpoint needs authentication). Respects `exclude_repos` and visibility filters.

### `auditer members` — Manage organisation members

```bash
# List members of an organisation
auditer members list --account my-org

# List members for all org-type accounts
auditer members list --all

# Remove a member (requires confirmation)
auditer members remove USERNAME --account my-org
```

Only works with `type: "org"` accounts. Shows a Rich table with login and ID.

### `auditer collaborators` — Manage repository collaborators

```bash
# List collaborators on a specific repo
auditer collaborators list --account my-user --repo my-repo

# List collaborators on all repos
auditer collaborators list --account my-user

# Add a collaborator to a repo (default: push permission)
auditer collaborators add USERNAME --account my-user --repo my-repo

# Add with specific permission level
auditer collaborators add USERNAME --account my-user --repo my-repo --permission admin

# Add to all repos (requires confirmation)
auditer collaborators add USERNAME --account my-user --all-repos

# Remove from a repo
auditer collaborators remove USERNAME --account my-user --repo my-repo

# Remove from all repos (requires confirmation)
auditer collaborators remove USERNAME --account my-user --all-repos
```

Permission levels: `pull`, `push` (default), `admin`, `maintain`, `triage`.

### `auditer archive` — Archive stale repositories

```bash
# Archive repos inactive for >12 months (default threshold)
auditer archive --account my-user

# Custom staleness threshold
auditer archive --account my-user --months 24

# Archive a specific repo (regardless of staleness)
auditer archive --account my-user --repo old-project
```

Shows a table of candidates before archiving. Skips already-archived repos. Requires confirmation.

### `auditer add-license` — Add LICENSE files

```bash
# Add MIT license to repos missing one
auditer add-license --account my-user --license MIT

# Add to a specific repo
auditer add-license --account my-user --repo my-repo --license Apache-2.0

# Custom copyright owner name (default: GitHub profile name)
auditer add-license --account my-user --license MIT --owner-name "John Doe"
```

Supported licenses: `MIT`, `Apache-2.0`, `GPL-3.0`, `BSD-2-Clause`, `BSD-3-Clause`. Skips repos that already have a LICENSE file.

### `auditer topics` — Manage repository topics

```bash
# List topics for all repos
auditer topics list --account my-user

# List topics for a specific repo
auditer topics list --account my-user --repo my-repo

# Replace all topics on a repo (comma-separated)
auditer topics set python,cli,audit --account my-user --repo my-repo

# Add a topic to all repos that don't have it
auditer topics add python --account my-user

# Add a topic to a specific repo
auditer topics add python --account my-user --repo my-repo
```

### `auditer settings` — Update repository settings

```bash
# Update description
auditer settings --account my-user --repo my-repo --description "New description"

# Update homepage
auditer settings --account my-user --repo my-repo --homepage "https://example.com"

# Disable issues and wiki on all repos (requires confirmation)
auditer settings --account my-user --all --has-issues false --has-wiki false

# Change visibility (requires confirmation)
auditer settings --account my-user --repo my-repo --visibility private
```

Options: `--description`, `--homepage`, `--has-issues`, `--has-wiki`, `--has-projects`, `--default-branch`, `--visibility`.

### `auditer profile` — Generate a GitHub profile README

Generate a Markdown profile README from your GitHub data, ready for local review or pasting into your profile repo.

```bash
# Generate for one account
auditer profile --account my-user

# Generate for all configured accounts
auditer profile --all

# Check the generated README
cat reports/my-user/profile_readme.md
```

**The generated README includes:**
- Header with display name and profile link
- About section (bio, location, company, blog, Twitter — omitted if all empty)
- Stats table (public repos, total stars, total forks, followers, following)
- Top Repositories (top 6 by pin score: stars, forks, recency)
- Languages breakdown (table + visual bar chart)
- Recent Activity (last 10 public events: pushes, PRs, new repos, issues, stars, forks)
- Footer with generation date

Works with or without a token. Without a token, uses public API endpoints only.

### `auditer readme` — Per-repo README generator

Generate a README for a repository by scanning its local checkout, detecting project structure, and enriching via the GitHub API.

```bash
# Generate README for a single repo (saved locally for review)
auditer readme my-repo --account my-user

# Generate READMEs for all repos missing one
auditer readme --all --account my-user

# Generate for all repos (even those that already have a README)
auditer readme --all --force --account my-user

# Push README directly to GitHub (requires confirmation)
auditer readme my-repo --account my-user --push

# Push READMEs for all repos missing one
auditer readme --all --account my-user --push

# Check the generated README
cat reports/my-user/readmes/my-repo.md
```

**How it works:**
1. Scans local checkouts for the account (must run `auditer checkout` first)
2. Detects project structure: package manager (npm/Composer/pip/Cargo/Go), test directories, CI configs, entry points
3. Enriches via GitHub API for description, topics, license, language
4. Generates a Markdown README to `reports/{slug}/readmes/{repo_name}.md`
5. With `--push`: commits the README directly to the repo on GitHub (creates or updates)

**The generated README includes:**
- Title and description (from API or package manager)
- Stats line (stars, forks, primary language — from API)
- Topic badges
- Framework detection (Symfony, Laravel, React, Vue, Django, Flask, etc.)
- Installation command (auto-detected from package manager)
- Key production dependencies (filtered, excludes build/test tools)
- Usage section (entry points and available scripts)
- Project structure (hierarchical box-drawing tree, 3 levels deep)
- Testing command (auto-detected)
- Languages table (detected languages with file counts and percentages)
- License
- Footer with generation date

Without `--force`, `--all` skips repos that already have a README. Respects `exclude_repos`.

With `--push`, the README is committed directly to the repo on GitHub via the Contents API. If a README already exists, it's updated (using the SHA for the existing file). A local copy is always saved to `reports/` regardless. Requires a token and confirmation before pushing.

### Global options

```bash
auditer --version                                # Show version
auditer --verbose checkout --account my-user     # Debug logging (shows API calls, git operations)
auditer --verbose audit --all                    # Debug logging for audit
auditer --config /path/to/config.yaml audit --all  # Use a custom config file path
```

## Configuration

See `config.example.yaml` for the full schema. Key options:

```yaml
defaults:
  stale_months: 12              # Repos inactive longer than this are "stale"
  data_dir: "data"              # Where local checkouts are stored
  reports_dir: "reports"        # Where audit reports are written
  log_level: "INFO"             # INFO, DEBUG, WARNING, ERROR

accounts:
  - slug: "my-user"            # Short name used in paths and CLI
    type: "user"               # "user" or "org"
    username: "my-github-username"
    token: "ghp_xxxx..."       # GitHub PAT (null for public-only)
    include_private: true       # Include private repos (default: true)
    include_forks: true         # Include forked repos (default: true)
    data_dir: "/custom/path"   # Override default data_dir for this account (optional)
    exclude_repos:             # Repos to skip (blocklist, optional)
      - "broken-legacy-project"
      - "archived-experiment"
    include_repos:             # Only track these repos (allowlist, optional)
      - "important-project"
```

### Multiple accounts

Configure as many personal users and organisations as needed:

```yaml
accounts:
  - slug: "personal"
    type: "user"
    username: "my-username"
    token: "ghp_xxxx..."

  - slug: "work-org"
    type: "org"
    username: "my-company"
    token: "ghp_yyyy..."

  - slug: "oss-user"
    type: "user"
    username: "famous-dev"
    token: "ghp_xxxx..."        # YOUR token (for rate limits)
    include_private: false
```

```bash
# Operate on all at once
auditer checkout --all --full
auditer audit --all

# Or target specific accounts
auditer checkout --account personal
auditer audit --account work-org
```

### Exclude repos

Skip specific repos by name (case-insensitive) during both checkout and audit:

```yaml
accounts:
  - slug: "my-user"
    exclude_repos:
      - "broken-legacy-project"
      - "archived-experiment"
```

```bash
auditer checkout --account my-user
# Output: "Excluded 2 repo(s) per config."

auditer audit --account my-user
# Output: "Excluded 2 repo(s) per config."
```

Exclusion is case-insensitive — `"My-Repo"` in the config matches a repo named `my-repo`.

### Per-account data directory

Store different accounts' repos in different locations:

```yaml
accounts:
  - slug: "work"
    data_dir: "/mnt/data/work-repos"
    # ...
  - slug: "personal"
    # Uses the default data_dir from defaults section
    # ...
```

```bash
auditer checkout --account work
ls /mnt/data/work-repos/work/public/

# Audit reads from the same custom directory
auditer audit --account work
```

### Fork and private repo filtering

Control which repo types are included per account. Repos explicitly listed in `include_repos` bypass these filters.

```yaml
accounts:
  - slug: "my-user"
    include_private: false    # Exclude private repos
    include_forks: false      # Exclude forked repos
```

```bash
auditer checkout --account my-user
# Only public, non-fork repos are cloned

ls data/my-user/public/     # Has repos
ls data/my-user/private/    # Empty or doesn't exist
```

### Projects

Group repos from different accounts into named projects. Use `--project` to filter any command to just the repos in a project. Projects support two inclusion modes: `accounts` (include all repos from entire accounts) and `repos` (include specific repos). Both can be combined.

```yaml
projects:
  my-project:
    description: "Client X web platform"
    accounts:                       # Include ALL repos from these accounts
      - work-org
    repos:                          # Include specific repos from other accounts
      - personal/webapp
      - personal/shared-lib
```

```bash
# Checkout only repos in the project (operates on all relevant accounts)
auditer checkout --project my-project

# Audit only project repos
auditer audit --project my-project

# Activity report for project repos
auditer activity --project my-project

# Combine with --account to scope to one account's project repos
auditer checkout --account personal --project my-project

# Works with all commands that list repos
auditer archive --account personal --project my-project
auditer topics list --account personal --project my-project
auditer settings --account personal --all --project my-project --has-wiki false
```

For commands that support `--all` (checkout, audit, activity, access), `--project` alone operates on all accounts that have repos in the project. `--project` combined with `--account` filters that single account's repos to the project. Reports are written to `reports/{project-name}/` instead of `reports/{slug}/`.

### Third-party accounts

Track public repos from users or orgs you don't own. Use your own token for higher rate limits (5000/hr authenticated vs 60/hr unauthenticated):

```yaml
accounts:
  - slug: "famous-dev"
    type: "user"
    username: "famous-dev"         # The user you want to track
    token: "ghp_xxxx..."          # YOUR token (for rate limits)
    include_private: false
    include_forks: false
    include_repos:                 # Only track these specific repos
      - "cool-project"
      - "awesome-lib"
```

```bash
auditer checkout --account famous-dev
# Clones only cool-project and awesome-lib from famous-dev

auditer audit --account famous-dev
# Generates audit report for their repos
```

The tool detects that the token belongs to a different user and automatically uses the public `/users/{username}/repos` endpoint instead of `/user/repos`. All commands work: checkout, audit, activity, readme, etc.

Use `include_repos` to allowlist specific repos (only those repos are tracked). Use `exclude_repos` to blocklist specific repos (all repos except those are tracked). Both can be combined: `include_repos` is applied first, then `exclude_repos` removes from the result. Repos listed in `include_repos` bypass `include_forks` and `include_private` filters — if you explicitly allowlist a fork or private repo, it is always included.

### Token-less accounts (public-only)

Audit public repos without a token (lower rate limits — 60 requests/hr):

```yaml
accounts:
  - slug: "public-user"
    type: "user"
    username: "someone"
    token: null
    include_private: false
```

```bash
auditer checkout --account public-user
# Clones only public repos (no auth needed)
```

### Bitbucket Cloud accounts

Track repos hosted on Bitbucket Cloud alongside your GitHub accounts, using the `provider`, `workspace`, and `auth_email` config fields.

**Bitbucket support currently covers `checkout`, `audit`, `activity`, `settings`, `readme`, `add-license`, `access`, `collaborators list`, and `members list`.** `topics`, `archive`, and `profile` remain GitHub-only — Bitbucket Cloud has no topics/labels concept, no repo-level archived flag, and no GitHub-profile-README convention respectively, so there's no equivalent to build toward at all; running any of these against a `provider: "bitbucket"` account prints a clear error and exits instead of failing partway through. `members remove` is explicitly not supported for Bitbucket accounts — Bitbucket Cloud's REST API has no endpoint for removing workspace members (membership changes go through Atlassian Admin or SCIM only). `collaborators add`/`collaborators remove` are likewise explicitly not supported for Bitbucket accounts — Bitbucket's per-repo permission-grant API requires the target user to already be a workspace member (no GitHub-style "invite an outside collaborator"), may return 402 Payment Required on Bitbucket's Free plan tier, and may only support legacy App Password auth rather than the API token auth this tool uses, so it needs verification against a live Bitbucket workspace before it can be built.

```yaml
accounts:
  - slug: "my-bitbucket-workspace"
    type: "user"                     # still required by config validation; value is
                                      # ignored for bitbucket accounts (no user/org
                                      # distinction — "workspace" identifies it instead)
    provider: "bitbucket"            # "github" (default) or "bitbucket"
    workspace: "my-workspace-slug"   # Bitbucket workspace slug — replaces GitHub's user/org
    username: "my-workspace-display-name"
    auth_email: "me@example.com"     # Atlassian account email, used for HTTP Basic auth
    token: "ATATTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Generate the token from the Atlassian account's API token settings at [id.atlassian.com](https://id.atlassian.com) (Security > API tokens), with the `Repositories:Read`, `Pull requests:Read`, and `Issues:Read` scopes.

Bitbucket auth uses HTTP Basic (`auth_email` as username, `token` as password) — for both the REST API and git-over-HTTPS clone/fetch — unlike GitHub's token-only convention.

```bash
auditer checkout --account my-bitbucket-workspace
auditer audit --account my-bitbucket-workspace
auditer activity --account my-bitbucket-workspace
auditer settings --account my-bitbucket-workspace --repo my-repo --description "Updated"
auditer readme my-repo --account my-bitbucket-workspace
auditer add-license --account my-bitbucket-workspace --license MIT
auditer access --account my-bitbucket-workspace
auditer collaborators list --account my-bitbucket-workspace
auditer members list --account my-bitbucket-workspace
```

**Known gaps versus GitHub accounts** (Bitbucket Cloud's API has no equivalent concept for these): no stars/forks count, no topics, no repo-level archived flag, and no license API field. The `audit` report will show 0 stars/forks and `**MISSING**` license for Bitbucket repos even when a LICENSE file exists locally, and the health score's "not archived" component always scores full marks (Bitbucket has no archived flag to check). The generated `readme` similarly always shows 0 stars/forks and no topics/license for Bitbucket repos. `add-license`'s "already has a LICENSE" check is unaffected by this gap — it reads the actual file from the repo instead of an API field, so it correctly detects an existing LICENSE on Bitbucket.

## Automated Daily Sync

Set up a cron job to keep all repos up to date:

```bash
# Edit crontab
crontab -e

# Add daily sync at 4 AM
0 4 * * * /path/to/github-auditor/scripts/daily-checkout.sh
```

The script runs `auditer checkout --all --full` (all accounts, all branches and tags) from the repository root, so it finds `config.yaml` and the `data/` folder. It logs to `~/.cache/github-auditor/checkout.log`; set `AUDITER_LOG` to use another file, or `AUDITER_BIN` if `auditer` is not on the PATH (for example, the full path to a virtualenv's `auditer`).

You can also add an alias for quick manual syncs:

```bash
# Add to ~/.bash_aliases or ~/.bashrc
alias ghsync='cd /path/to/github-auditor && auditer checkout --all --full'
```

## GitHub API Client (Library Use)

The `GitHubClient` class can be used programmatically for operations beyond the CLI commands.

**Exceptions** — all methods may raise:
- `GitHubAPIError(message, status_code)` — base class for API errors (5xx, network, etc.)
- `AuthenticationError` — invalid or expired token (401)
- `RateLimitError` — rate limit exceeded (403), includes reset time in message

The client retries on 5xx errors and network failures (3 attempts, 1s backoff). Timeout is 30 seconds per request.

```python
from auditer.models import AccountConfig
from auditer.github_client import GitHubClient, AuthenticationError, RateLimitError

account = AccountConfig(
    slug="my-user",
    type="user",            # "user" or "org"
    username="myuser",
    token="ghp_xxxx...",
    include_private=True,   # default: True
    include_forks=True,     # default: True
    exclude_repos=[],       # default: []
    include_repos=[],       # default: [] (empty = all repos)
    data_dir=None,          # default: None (uses global default)
)

with GitHubClient(account) as client:
    # --- Repo info ---
    repos = client.list_repos()                         # All repos for the account
    repo = client.get_repo("owner", "repo-name")        # Single repo metadata (None on 404)
    has_readme = client.has_readme("owner", "repo-name") # True/False

    # --- User info ---
    user = client.get_user("username")                   # Public profile (None on 404)
    me = client.get_authenticated_user()                 # Authenticated user's profile
    login = client.validate_token()                      # Verify PAT, returns login or None

    # --- Org members ---
    members = client.list_org_members("my-org")          # List all org members
    client.remove_org_member("my-org", "username")       # Remove member (True/False)

    # --- Collaborators ---
    collabs = client.list_collaborators("owner", "repo") # List repo collaborators
    client.add_collaborator("owner", "repo", "user")     # Add with default "push" permission
    client.add_collaborator("owner", "repo", "user", permission="admin")
    client.remove_collaborator("owner", "repo", "user")  # Remove (True/False)

    # --- Repo settings ---
    client.update_repo("owner", "repo", archived=True)   # Archive a repo
    client.update_repo("owner", "repo",                   # Update multiple settings
        description="New description",
        homepage="https://example.com",
        has_issues=False,
        has_wiki=False,
    )

    # --- Topics ---
    topics = client.get_topics("owner", "repo")          # ["python", "cli"]
    client.set_topics("owner", "repo", ["python", "cli", "audit"])

    # --- File content ---
    file = client.get_file("owner", "repo", "README.md") # File metadata + content (None on 404)
    # file["sha"] is needed for updates

    # Create a new file (content is base64-encoded automatically)
    client.create_or_update_file(
        "owner", "repo", "LICENSE",
        content="MIT License...",
        message="Add MIT license",
    )

    # Update an existing file (pass sha from get_file)
    client.create_or_update_file(
        "owner", "repo", "README.md",
        content="# Updated README",
        message="Update README",
        sha=file["sha"],
        branch="main",
    )

    # --- Events ---
    events = client.list_user_events("username")          # Recent public events (list of dicts)

    # --- Pagination (advanced) ---
    # get_paginated() handles Link-header pagination for any GitHub API endpoint
    custom = client.get_paginated("/orgs/my-org/teams")

    # --- Rate limit ---
    limits = client.get_rate_limit()                     # Current rate limit status
```

## Manual Testing Guide

After making changes or on first setup, use these steps to verify everything works.

### 1. Prerequisites

```bash
# Ensure the package is installed
pip install -e ".[dev]"

# Verify config is valid
auditer config --validate
# Expected: "Config is valid. N account(s) configured."

# Verify config looks right (tokens are masked)
auditer config --show
# Expected: table showing all accounts with tokens masked to last 4 chars
```

### 2. Test checkout (basic clone/pull)

```bash
# Dry run first — shows what would happen without making changes
auditer checkout --account <slug> --dry-run
# Expected: lists repos that would be cloned/pulled, no files created

# Clone/pull a single account
auditer checkout --account <slug>
# Expected: clones new repos, pulls updates for existing ones

# Verify repos were created
ls data/<slug>/public/
ls data/<slug>/private/   # Only if include_private: true
```

### 3. Test checkout with branches and tags

```bash
# Full sync with all branches and tags
auditer checkout --account <slug> --full
# Expected: fetches all remote branches and tags, shows new branches/tags discovered

# Or individually
auditer checkout --account <slug> --branches
auditer checkout --account <slug> --tags
```

### 4. Test skip dirty repos

```bash
# Make a local change in a checked-out repo
echo "test" >> data/<slug>/public/some-repo/test.txt

# Run checkout — this repo should be skipped
auditer checkout --account <slug>
# Expected: "Skipping some-repo (local changes detected)"

# Force update even dirty repos
auditer checkout --account <slug> --no-skip-dirty
# Expected: repo is updated despite local changes

# Clean up
rm data/<slug>/public/some-repo/test.txt
```

### 5. Test orphaned repo detection

When a repo exists locally but has been deleted from GitHub, checkout detects it:

```bash
# Create a fake local repo
mkdir -p data/<slug>/public/fake-orphan/.git

# Run checkout
auditer checkout --account <slug>
# Expected: warns about orphaned repo "fake-orphan"

# Clean up
rm -rf data/<slug>/public/fake-orphan
```

### 6. Test audit (local-first)

```bash
# Ensure you have local repos first
auditer checkout --account <slug>

# Run audit
auditer audit --account <slug>
# Expected: scans local repos, enriches via API, generates report

# Check the generated report
cat reports/<slug>/audit_report.md
# Expected: Markdown report with repo details, missing README/LICENSE warnings,
# stale repo detection, pin recommendations
```

### 7. Test audit local-first README/LICENSE detection

```bash
# Temporarily remove a README from a local checkout
mv data/<slug>/public/some-repo/README.md /tmp/README_backup.md

# Run audit — should report this repo as "missing README"
auditer audit --account <slug>
# Expected: "Missing README" appears in the report summary

# Restore the README
mv /tmp/README_backup.md data/<slug>/public/some-repo/README.md

# Run audit again — should no longer be missing
auditer audit --account <slug>
```

This also works for LICENSE files.

### 8. Test audit with deleted GitHub repos

If a repo exists locally but has been deleted from GitHub, audit handles it gracefully:

```bash
# Create a fake local repo that doesn't exist on GitHub
mkdir -p data/<slug>/public/fake-deleted-repo/.git

# Run audit
auditer audit --account <slug>
# Expected: yellow warning "Could not enrich 1 repo(s) (deleted from GitHub?): fake-deleted-repo"
# The repo still appears in the report with default values (0 stars, no description, etc.)

# Clean up
rm -rf data/<slug>/public/fake-deleted-repo
```

### 9. Test exclude repos

```yaml
# In config.yaml:
accounts:
  - slug: "my-user"
    exclude_repos:
      - "some-repo-name"
      - "another-repo"
```

```bash
# Both commands respect the exclude list
auditer checkout --account my-user
# Expected: "Excluded 2 repo(s) per config."

auditer audit --account my-user
# Expected: "Excluded 2 repo(s) per config."
```

### 10. Test per-account data directory

```yaml
# In config.yaml:
accounts:
  - slug: "work"
    data_dir: "/tmp/test-custom-dir"
    # ... other fields
```

```bash
# Checkout uses the custom directory
auditer checkout --account work
ls /tmp/test-custom-dir/work/public/
# Expected: repos cloned into /tmp/test-custom-dir/work/public/

# Audit also reads from the custom directory
auditer audit --account work
# Expected: scans /tmp/test-custom-dir/work/ for repos
```

### 11. Test fork and private repo filtering

```yaml
# In config.yaml:
accounts:
  - slug: "my-user"
    include_private: false
    include_forks: false
```

```bash
auditer checkout --account my-user
# Expected: only public, non-fork repos are cloned

ls data/my-user/public/     # Has repos
ls data/my-user/private/    # Should be empty or not exist
```

### 12. Test multi-account operations

```bash
# All accounts at once
auditer checkout --all --full
# Expected: processes each account sequentially, full sync

auditer audit --all
# Expected: generates separate reports for each account
```

### 13. Test verbose logging

```bash
# Debug logging shows API calls, git operations, and detailed output
auditer --verbose checkout --account <slug>
auditer --verbose audit --account <slug>
# Expected: additional DEBUG-level messages showing HTTP requests, git commands, etc.
```

### 14. Test custom config path

```bash
auditer --config /path/to/other-config.yaml checkout --account <slug>
auditer --config /path/to/other-config.yaml audit --all
# Expected: uses the specified config file instead of ./config.yaml
```

### 15. Running the automated test suite

```bash
# Run all tests
pytest
# Expected: 910 tests passing

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=auditer

# Run a specific test file
pytest tests/test_config.py -v
pytest tests/test_models.py -v
pytest tests/test_github_client.py -v
pytest tests/test_git_ops.py -v
pytest tests/test_filtering.py -v
pytest tests/test_activity_report.py -v
pytest tests/test_reports.py -v
pytest tests/test_security.py -v

# Command tests (in tests/commands/ subdirectories)
pytest tests/commands/data/test_checkout.py -v
pytest tests/commands/data/test_audit.py -v
pytest tests/commands/data/test_activity_cmd.py -v
pytest tests/commands/repo/test_archive.py -v
pytest tests/commands/repo/test_settings.py -v
pytest tests/commands/repo/test_topics.py -v
pytest tests/commands/repo/test_add_license.py -v
pytest tests/commands/people/test_members.py -v
pytest tests/commands/people/test_collaborators.py -v
pytest tests/commands/people/test_access.py -v
```

## Project Structure

```
github-auditor/
├── pyproject.toml               # Package metadata, dependencies, tool config
├── LICENSE                      # MIT license
├── README.md
├── CLAUDE.md                    # Architecture and conventions (for AI assistants)
├── ROADMAP.md                   # Feature list and planned work
├── config.example.yaml          # Template (committed)
├── config.yaml                  # Your credentials (gitignored)
├── scripts/
│   └── daily-checkout.sh        # Daily cron script (optional)
├── src/auditer/
│   ├── __init__.py              # Package version
│   ├── cli.py                   # Click CLI entry point, LazyConfig
│   ├── config.py                # YAML config loader, AppConfig
│   ├── filtering.py             # Shared repo filtering helpers
│   ├── github_client.py         # httpx GitHub API client (24 methods)
│   ├── git_provider.py          # GitProvider Protocol + get_provider() factory
│   ├── bitbucket_client.py      # Bitbucket Cloud REST API client (see "Bitbucket Cloud accounts")
│   ├── git_ops.py               # Git operations, local repo scanning
│   ├── models.py                # RepoInfo, AuditResult, AccessResult, ProfileData, ReadmeData, ActivityResult, PRRecord, TagRecord, AccountConfig
│   ├── licenses.py              # License template strings (5 licenses)
│   ├── commands/
│   │   ├── data/                # Data acquisition & analysis
│   │   │   ├── checkout.py      # Clone/pull command
│   │   │   ├── audit.py         # Local-first audit command
│   │   │   └── activity.py      # Developer activity tracking
│   │   ├── repo/                # Repository management
│   │   │   ├── archive.py       # Archive stale repos
│   │   │   ├── settings.py      # Update repo settings
│   │   │   ├── topics.py        # Manage repo topics
│   │   │   └── add_license.py   # Add LICENSE files to repos
│   │   ├── people/              # People & permissions
│   │   │   ├── members.py       # Manage org members
│   │   │   ├── collaborators.py # Manage repo collaborators
│   │   │   └── access.py        # Access report (collaborator permissions)
│   │   ├── meta/                # Configuration & utility
│   │   │   └── config_cmd.py    # Config validation/display
│   │   └── content/             # Content generation
│   │       ├── profile.py       # Profile README generator
│   │       └── readme.py        # Per-repo README generator
│   ├── reports/
│   │   ├── access.py            # Access report generator (matrix + per-repo)
│   │   ├── activity.py          # Activity report generator (leaderboard, breakdowns)
│   │   ├── markdown.py          # Audit report generator
│   │   ├── profile.py           # Profile README generator
│   │   └── readme.py            # Per-repo README generator
│   └── utils/
│       ├── logging.py           # Sanitised rich logging
│       └── security.py          # Token masking, URL sanitisation
├── tests/                       # pytest test suite (910 tests)
│   ├── conftest.py              # Shared fixtures and strip_ansi() helper
│   ├── test_config.py           # Core module tests
│   ├── test_models.py
│   ├── test_github_client.py
│   ├── test_git_ops.py
│   ├── test_filtering.py
│   ├── test_logging.py
│   ├── test_security.py
│   ├── test_activity_report.py
│   ├── test_reports.py
│   └── commands/                # Command tests (mirrors source structure)
│       ├── data/
│       │   ├── test_checkout.py
│       │   ├── test_audit.py
│       │   └── test_activity_cmd.py
│       ├── repo/
│       │   ├── test_archive.py
│       │   ├── test_settings.py
│       │   ├── test_topics.py
│       │   └── test_add_license.py
│       ├── people/
│       │   ├── test_members.py
│       │   ├── test_collaborators.py
│       │   └── test_access.py
│       ├── meta/
│       │   └── test_config_cmd.py
│       └── content/
│           ├── test_profile_cmd.py
│           └── test_readme_cmd.py
├── data/                        # Local checkouts (gitignored)
└── reports/                     # Generated reports (gitignored)
```

## Security

- Tokens are stored in `config.yaml` (gitignored, never committed)
- Git auth uses `GIT_ASKPASS` — tokens never embedded in URLs
- All subprocess calls use list form (no `shell=True`)
- All log output is sanitised to strip leaked tokens
- Token validation on startup before any operations

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full project roadmap.

## Licence

MIT, see [LICENSE](LICENSE).
