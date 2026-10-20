# Commands

Every command reads `config.yaml` from the current folder. `auditer COMMAND --help` lists a command's options.

Most commands take `--account SLUG` for one account. The reports and `checkout` also take `--all` for every account. `--project NAME` limits a command to the repositories in a project (see [projects](configuration.md#projects)).

Commands that change things on GitHub ask before going ahead when the change is risky or wide: removing an organisation member, archiving, adding licences, changing visibility, pushing a README, or changing every repository at once. A change to one repository's collaborators, topics or other settings goes ahead without asking.

- [Global options](#global-options)
- Reports and clones: [`config`](#config), [`checkout`](#checkout), [`audit`](#audit), [`activity`](#activity), [`access`](#access)
- People: [`members`](#members), [`collaborators`](#collaborators)
- Repositories: [`archive`](#archive), [`add-license`](#add-license), [`topics`](#topics), [`settings`](#settings)
- Content: [`profile`](#profile), [`readme`](#readme)

## Global options

```bash
auditer --version
auditer --verbose checkout --account my-user        # debug logging: API requests and git commands
auditer --config /path/to/config.yaml audit --all   # use another config file
```

## config

```bash
auditer config --validate   # prints "Config is valid. 3 account(s) configured."
auditer config --show       # a table of the accounts, each token cut to its last four characters
```

## checkout

Clones every repository in an account, or pulls the latest changes into the clones you already have.

```bash
auditer checkout --account my-user
auditer checkout --all --full                       # every account, all branches and tags
auditer checkout --account my-user --branches       # fetch all remote branches
auditer checkout --account my-user --tags           # fetch all tags (both flags together = --full)
auditer checkout --account my-user --dry-run        # show what would happen, change nothing
auditer checkout --account my-user --no-skip-dirty  # update clones with uncommitted changes too
auditer checkout --account my-user --months 12      # only repositories active in the last 12 months
```

Clones go into `data/{slug}/public/` and `data/{slug}/private/`. A clone with uncommitted changes is skipped unless you pass `--no-skip-dirty`.

To make the activity filter permanent for one account, set `active_within_months: 12` on it in `config.yaml`. Other accounts still check out everything. `--months N` overrides the account's value for one run, stricter or looser.

The output shows:

- each updated branch, with its number of commits, the authors and the commit messages
- force pushes, with the number of commits added and removed
- new branches and tags
- links to GitHub: compare links for updated branches, commit links, tree links for new branches and release links for new tags
- local clones of repositories that have been deleted from GitHub
- totals for repositories cloned, updated, skipped and failed, and for branches, tags and commits

## audit

Scans the local clones, adds details from the API and writes `reports/{slug}/audit_report.md`. Run `checkout` first: `audit` reads the clones, and tells you to run `checkout` if it finds none.

```bash
auditer audit --account my-user
auditer audit --all
```

From each clone it reads whether there is a README and a licence, the default branch, the last commit date and the size. It then asks the API (`GET /repos/{owner}/{name}`) for stars, forks, language, description and topics. A repository that has been deleted from GitHub still appears in the report, with default values.

The report lists:

- repositories missing a README, a licence or a description
- stale repositories, with no activity for `stale_months` (12 by default)
- a health score out of 100 for each repository
- stars, forks, language and size
- which repositories to pin, ranked by stars, forks and recent activity

The health score is the sum of: a README (20 points), a licence (20), a description (15), recent activity (25 if active in the last three months, falling to 0 after three years), not archived (10) and few open issues (10 for five or fewer, falling to 0 above 50).

## activity

Reports what each developer did over a date range: commits, lines added and removed, merge commits, pull requests opened and merged, merges, reviews, approvals, comments, issues and tags. Commits, lines and tags come from the local clones. Pull requests, issues and reviews come from the API.

```bash
auditer activity --account my-user                                  # the last 30 days
auditer activity --all                                              # one report per account
auditer activity --account my-user --since 2026-01-01 --until 2026-06-30
auditer activity --account my-user --user alice                     # one developer
auditer activity --account my-user --repo my-project                # one repository
auditer activity --account my-user --user alice --repo my-project --since 2026-01-01
auditer activity --account my-user --branches                       # feature branches in their own section
auditer activity --account old-client --since 2026-08-01 --local-only   # never call the API
auditer activity --account my-org --account my-user --since 2026-10-10  # one combined report
auditer activity --all --combine                                    # one combined report for every account
auditer activity --account my-user --no-fetch                       # use the clones as they are
```

The report goes to `reports/{slug}/`. Its file name records the options, so reports don't overwrite each other: `activity_2026-02-08_2026-02-14.md`, `activity_2026-02-08_2026-02-14_user-alice.md`, `activity_2026-02-08_2026-02-14_user-alice_repo-my-project.md`. Combined reports go to `reports/combined/`, for example `activity_2026-10-10_2026-10-20_accounts-my-org+my-user.md`.

`--user` matches a name, an email address or a GitHub login, and filters commits, pull requests, issues and reviews. All the filters can be combined.

### What the report contains

- a contributors table that maps each developer's ID (`@login` or email) to their name and email
- the date range, with its timezone, and a "Data as of" line saying when the clones were fetched
- a summary: active repositories, developers, commits, merge commits, lines, pull requests by state, issues, reviews, approvals, comments and tags
- a developer leaderboard, sorted by net lines
- the lines left out of each developer's count (see [line counts](#line-counts)), so a large data import is visible rather than silently dropped
- a separate table for bots (`renovate[bot]`, `dependabot[bot]` and any login or name ending in `[bot]`), counted in the summary but kept out of the leaderboard
- daily and weekly breakdowns, including days with no commits; weeks cut by the date range are marked `(partial)`
- a table and a detail section for each repository, sorted by net lines
- with `--branches`, the commits on unmerged feature branches for each repository
- a section for each developer: their commits oldest first with links, their pull requests and the state each was in at the end of the range, the pull requests they merged, the reviews they submitted and the tags they created
- in a combined report, a summary for each account

The leaderboard columns mean:

- Merged PRs: pull requests they wrote that were merged in the range, whenever they were opened.
- Merges: pull requests they merged, whoever wrote them (GitHub's `merged_by`).
- Merge Commits: git merge commits they authored. These are never part of Commits or the line counts.
- Open PRs: pull requests they wrote that were still open at the end of the range and were opened or updated during it. A pull request closed without merging is closed, not open.
- Reviews: submitted reviews (approve, comment or request changes) on other people's pull requests. Approvals are the approved subset. Reviews of your own pull request don't count. On Bitbucket, reviews are approvals and change requests.
- Review Comments: inline comments on the diff. PR Comments: comments in the pull request conversation. A review left only as inline comments counts under Review Comments.

### Dates

`--since` and `--until` are whole calendar days in the timezone set by `defaults.timezone` in `config.yaml`. `auditer activity --help` shows the default. `--since 2026-10-10 --until 2026-10-16` runs from 10 October 00:00 to 16 October 23:59:59, whenever you run the report. Git is given full timestamps, and the API's UTC timestamps are compared as dates in the same timezone.

Commit dates are committer dates, not author dates, because git picks commits for a range by committer date. That keeps the daily and weekly rows adding up to the total. It also means rebased or cherry-picked work counts on the day it was rebased.

### Fresh data

Before scanning, every clone is fetched (`git fetch --all --prune --tags --force`, with the account's token). The working tree and local branches are left alone. The "Data as of" line says when the fetch happened. A repository whose fetch failed is listed under "Stale git data", with the time of its last successful fetch. `--no-fetch` skips the fetch, and accounts with `local_only: true` are never fetched.

### Working without the API

An account whose API access has ended, such as a revoked token, can still be reported on from its clones. Pass `--local-only` for one run, or set `local_only: true` on the account. The report then shows pull request, issue, review and comment figures as "n/a" rather than 0, because they are unknown. If the API login fails for any other account, the command warns, carries on from git alone, and says so at the top of the report.

The command prints the token's remaining API requests before it starts. If the hourly limit runs out mid-scan, it waits for the reset instead of failing. Listing pull requests stops once it reaches ones last updated before `--since`, so an old repository costs one or two requests. Any API call that still fails is listed in the terminal summary and in a "Data quality" block in the report. Fetching reviews costs one request for each pull request updated in the range.

### Combined reports

Repeat `--account`, or add `--combine` to `--all` or `--project`, to put several accounts in one report. Developers are merged across accounts by login, so someone who works in an organisation and in their own repositories gets one row. Repositories are labelled `owner/name`.

### Feature branches

By default, commits are read from every branch (`git log --all --no-merges`), so unmerged work counts. With `--branches`, default-branch commits come from `origin/{default_branch}` and unmerged feature-branch commits get their own section. The totals still include both, so they match the leaderboard.

Feature-branch commits are deduplicated by git patch-id rather than SHA. A rebased or cherry-picked copy of work already on the default branch isn't counted twice, and the same patch on two branches counts once, credited to the earliest copy. Branches whose pull requests are all closed (merged or declined) are skipped.

### Line counts

Lines that nobody wrote by hand are left out of the line counts. A commit that only touches excluded files still counts as a commit, with 0 lines. The exclusions are:

- lock files at any depth (`package-lock.json`, `yarn.lock`, `composer.lock` and others)
- data files: CSV, TSV, JSONL and NDJSON anywhere, and JSON and XML under `data/`, `attachments/`, `exports/` or `dumps/` (elsewhere JSON and XML are usually config, such as `package.json`)
- vendored code (`vendor/`, `node_modules/`), WordPress core (`wp-admin/`, `wp-includes/`, `wp-content/plugins/`) and committed libraries (`third-party/`, `libraries/`, `jspm_packages/`, `.yarn/`)
- minified and generated files (`*.min.js`, `*.min.css`, `*.map`) and built output (`dist/`)
- recorded test data (`mocks/`, `__mocks__/`, `fixtures/`, `__fixtures__/`, `__snapshots__/`, `*.snap`, `*.har`, `*.bundle`)
- editor settings (`.idea/`, `*.code-workspace`, and the JSON and snippet files directly inside a `.vscode/` folder; code further down, such as an extension in `.vscode/extensions/`, is counted)
- binary files by extension: images, fonts, PDFs, archives, media and compiled files

Folder patterns apply at any depth, so `app/vendor/` is left out as well as `vendor/`. Binary files are listed by extension because git only treats a file as binary if its first 8 KB contain a NUL byte, and a JPEG without one is counted line by line. The full list is `EXCLUDED_PATHSPECS` in `src/auditer/git_ops.py`.

### One person, several identities

Developers are merged when they share a GitHub login, a normalised name (lower case, letters and digits only) or an email local part. So `Anton Zorin` and `Anton-Zorin` become one row, as do a commit-only `Fiona Vale` and the `fionavale` login, and `sarah.campion@acmecorp.example` and `sarah.campion@acmepublishing.example`. Matches are transitive. Names and logins under four characters, local parts under eight, generic mailboxes (`admin`, `developer` and so on) and noreply addresses are never matched on.

When that isn't enough, for example a git author name with a non-GitHub email, list the other identities under `developer_aliases` in `config.yaml`. The keys are GitHub logins and the values are the names or emails that person commits under. See `config.example.yaml`.

Without a token, the report has commits and tags only, and emails aren't matched to logins.

## access

Writes `reports/{slug}/access_report.md`: who has access to which repositories, and at what level. It needs a token, because the collaborators API needs authentication.

```bash
auditer access --account my-user
auditer access --all
auditer access --account my-user --sort id   # by GitHub user ID; a lower ID is an older account
```

The report has a matrix of users against repositories, showing the highest permission in each cell (admin, maintain, push, triage, pull), and a table for each repository with each collaborator's full permissions. `--sort name` (the default) sorts users alphabetically.

## members

For accounts with `type: "org"`.

```bash
auditer members list --account my-org
auditer members list --all                    # every organisation account
auditer members remove USERNAME --account my-org   # asks first
```

## collaborators

```bash
auditer collaborators list --account my-user                  # every repository
auditer collaborators list --account my-user --repo my-repo
auditer collaborators add USERNAME --account my-user --repo my-repo                     # push permission
auditer collaborators add USERNAME --account my-user --repo my-repo --permission admin
auditer collaborators add USERNAME --account my-user --all-repos                        # asks first
auditer collaborators remove USERNAME --account my-user --repo my-repo
auditer collaborators remove USERNAME --account my-user --all-repos                     # asks first
```

Permission levels are `pull`, `push` (the default), `admin`, `maintain` and `triage`.

## archive

Shows the candidates, asks, then archives them. Repositories that are already archived are skipped.

```bash
auditer archive --account my-user              # inactive for longer than stale_months
auditer archive --account my-user --months 24  # another threshold
auditer archive --account my-user --repo old-project   # one repository, stale or not
```

## add-license

Adds a licence file to repositories that don't have one. It lists them and asks first.

```bash
auditer add-license --account my-user --license MIT
auditer add-license --account my-user --repo my-repo --license Apache-2.0
auditer add-license --account my-user --license MIT --owner-name "Jane Doe"
```

The licences are `MIT`, `Apache-2.0`, `GPL-3.0`, `BSD-2-Clause` and `BSD-3-Clause`. The copyright holder defaults to the account's GitHub profile name.

## topics

```bash
auditer topics list --account my-user
auditer topics list --account my-user --repo my-repo
auditer topics set python,cli,audit --account my-user --repo my-repo   # replaces all topics
auditer topics add python --account my-user                            # every repository without it
auditer topics add python --account my-user --repo my-repo
```

## settings

```bash
auditer settings --account my-user --repo my-repo --description "New description"
auditer settings --account my-user --repo my-repo --homepage "https://example.com"
auditer settings --account my-user --all --has-issues false --has-wiki false   # asks first
auditer settings --account my-user --repo my-repo --visibility private         # asks first
```

The settings are `--description`, `--homepage`, `--has-issues`, `--has-wiki`, `--has-projects`, `--default-branch` and `--visibility`.

## profile

Writes a profile README for an account to `reports/{slug}/profile_readme.md`, for you to review and paste into the profile repository. It works without a token, using the public API.

```bash
auditer profile --account my-user
auditer profile --all
```

It has the name and a profile link, an about section (bio, location, company, blog, Twitter, left out when all are empty), a stats table, the top six repositories, a languages table and chart, the last ten public events, and the date it was made.

## readme

Writes a README for a repository from its clone and the API, to `reports/{slug}/readmes/{repo}.md`. Run `checkout` first.

```bash
auditer readme my-repo --account my-user
auditer readme --all --account my-user            # every repository without a README
auditer readme --all --force --account my-user    # every repository
auditer readme my-repo --account my-user --push   # also commit it to GitHub; asks first
```

It detects the package manager (npm, Composer, pip, Cargo, Go), the framework, test folders, CI configuration and entry points. The README has the title and description, stars, forks and language, topics, install and test commands, the main dependencies, usage, a three-level folder tree, a languages table and the licence.

`--push` commits the README through the Contents API, creating or updating the file, and needs a token. A local copy is always saved in `reports/`.
