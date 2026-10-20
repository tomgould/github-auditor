from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress

from auditer.bitbucket_client import GIT_AUTH_USERNAME as BITBUCKET_GIT_AUTH_USERNAME
from auditer.config import AppConfig, get_account, get_accounts_for_project, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.git_ops import (
    fetch_repo,
    get_branch_commits,
    get_commit_log,
    get_merge_commits,
    get_patch_ids,
    get_tags_in_range,
    last_fetch_failed,
    last_fetch_time,
    list_active_branches,
    scan_local_repos,
)
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError, GitHubClient
from auditer.models import (
    AccountConfig,
    ActivityResult,
    BranchActivity,
    CommitRecord,
    DeveloperActivity,
    ProjectConfig,
    PRRecord,
    RepoActivity,
    ReviewRecord,
    TagRecord,
)
from auditer.reports.activity import _is_bot, _normalise_name, generate_activity_report
from auditer.timewindow import (
    DEFAULT_TIMEZONE,
    DateWindow,
    get_timezone,
    local_date,
    parse_timestamp,
)

logger = logging.getLogger(__name__)
console = Console(soft_wrap=True, force_terminal=True)

# Every DeveloperActivity count, so _aggregate_developers can't miss one.
_DEV_COUNT_FIELDS = [
    f.name for f in fields(DeveloperActivity)
    if f.name not in DeveloperActivity._IDENTITY_FIELDS
]


def _build_report_filename(
    since: str,
    until: str,
    user_filter: str | None = None,
    repo_filter: str | None = None,
    accounts: list[str] | None = None,
) -> str:
    """Build a descriptive filename from the report options.

    Examples:
        activity_2026-02-08_2026-02-14.md
        activity_2026-02-08_2026-02-14_user-alice.md
        activity_2026-02-08_2026-02-14_repo-my-project.md
        activity_2026-02-08_2026-02-14_user-alice_repo-my-project.md
        activity_2026-02-08_2026-02-14_accounts-acme+alice.md  (combined)
    """
    parts = ["activity", since, until]
    if accounts and len(accounts) > 1:
        parts.append("accounts-" + "+".join(accounts))
    if user_filter:
        safe_user = user_filter.lower().replace(" ", "-")
        parts.append(f"user-{safe_user}")
    if repo_filter:
        safe_repo = repo_filter.lower().replace(" ", "-")
        parts.append(f"repo-{safe_repo}")
    return "_".join(parts) + ".md"


def _matches_user(name: str, email: str, user_filter: str, github_login: str = "") -> bool:
    """Check if a developer name, email, or GitHub login matches the user filter."""
    needle = user_filter.lower()
    return bool(
        needle in name.lower()
        or needle in email.lower()
        or (github_login and needle in github_login.lower())
    )


def _login_from_noreply(email: str) -> str:
    """Extract GitHub login from a noreply email.

    Format: 12345+login@users.noreply.github.com
    Returns empty string if not a noreply email.
    """
    if "noreply.github.com" in email and "+" in email:
        return email.split("+", 1)[1].split("@", 1)[0]
    return ""


def _in_range(ts: str | None, since: str, until: str, tz: tzinfo | None) -> bool:
    """Whether a timestamp falls on a day in [since, until] in ``tz``."""
    d = local_date(ts, tz)
    return bool(d) and since <= d <= until


def _merged_by_login(pr: dict) -> str:
    merged_by = pr.get("merged_by")
    if isinstance(merged_by, dict):
        login = merged_by.get("login")
        return login if isinstance(login, str) else ""
    return ""


def _pr_state_at(pr: dict, until: str, tz: tzinfo | None = None) -> str:
    """A PR's state at the end of ``until``: "merged", "closed" or "open".

    A PR merged or closed after the window was open during it. A closed PR
    with no timestamp (shouldn't happen) is taken as closed.
    """
    merged_at = pr.get("merged_at")
    if merged_at and local_date(merged_at, tz) <= until:
        return "merged"
    closed_at = pr.get("closed_at") or merged_at
    if closed_at:
        return "closed" if local_date(closed_at, tz) <= until else "open"
    return "closed" if pr.get("state") == "closed" else "open"


def _pr_open_at_end(
    pr: dict, since: str, until: str, tz: tzinfo | None = None,
) -> bool:
    """Whether a PR was in flight: open at the end of ``until``, and opened
    or updated during the window.

    PRs opened before the window count (a PR opened last week and worked on
    this week is open work this week), but a PR nobody has touched since
    the window started does not: counting those put fifteen people whose
    only "activity" was a months-old PR on the leaderboard. A PR merged
    after the window was open for it; a closed-unmerged PR is closed.
    ``updated_at`` is the PR's latest update, so for a past window a PR
    touched only after it also counts.
    """
    created = local_date(pr.get("created_at"), tz)
    if not created or created > until:
        return False
    updated = local_date(pr.get("updated_at"), tz) or created
    if created < since and updated < since:
        return False
    return _pr_state_at(pr, until, tz) == "open"


def _pr_closed_unmerged_in(pr: dict, since: str, until: str, tz: tzinfo | None = None) -> bool:
    """Whether a PR was closed without merging during the window."""
    if pr.get("merged_at"):
        return False
    return _in_range(pr.get("closed_at"), since, until, tz)


def _aggregate_developers(
    commits: list[CommitRecord],
    prs: list[dict] | None = None,
    issues: list[dict] | None = None,
    reviews: list[dict] | None = None,
    tags: list[TagRecord] | None = None,
    since: str = "",
    until: str = "",
    email_to_login: dict[str, str] | None = None,
    review_records: list[ReviewRecord] | None = None,
    name_to_login: dict[str, str] | None = None,
    merge_commits: list[CommitRecord] | None = None,
    pr_comments: list[dict] | None = None,
    tz: tzinfo | None = None,
) -> list[DeveloperActivity]:
    """Aggregate commit/PR/issue/review/tag data into per-developer summaries.

    ``reviews`` are raw inline review comments; ``review_records`` are
    submitted PR reviews, already date-filtered by ``_build_review_records``.
    ``pr_comments`` are PR conversation comments (issue comments on PRs).
    ``merge_commits`` count as merges only — never as commits or lines.

    Uses ``email_to_login`` (from the GitHub Commits API) to resolve git
    author emails to GitHub logins, enabling correct merging of commit data
    with PR/issue/review data keyed by login. ``name_to_login`` (from the
    ``developer_aliases`` config) does the same for git author names that
    can't be resolved by email.

    API timestamps are compared as dates in ``tz`` (the report timezone).
    Only creates entries for developers who have activity within the date
    range, or a PR still open at the end of it.
    """
    # `is None` rather than `or {}`: the caller's (possibly empty) dict is
    # mutated when a name alias resolves, and must stay the same object.
    if email_to_login is None:
        email_to_login = {}
    name_to_login = name_to_login or {}
    dev_map: dict[str, dict] = {}

    def _get_or_create(key: str, name: str, email: str) -> dict:
        if key not in dev_map:
            dev_map[key] = {
                "name": name, "email": email, "github_login": "",
                **{f: 0 for f in _DEV_COUNT_FIELDS},
            }
        return dev_map[key]

    def _for_login(login: str) -> dict:
        d = _get_or_create(login.lower(), login, f"{login}@github")
        d["github_login"] = login
        return d

    def _resolve_email_key(email: str, name: str = "") -> tuple[str, str]:
        """Resolve an email (and, failing that, a name) to (key, login).

        Returns (login.lower(), login) if a mapping exists, the email is a
        GitHub noreply address, or the name is a configured alias;
        otherwise (email.lower(), "").
        """
        lower = email.lower()
        # Check API-based mapping first
        login = email_to_login.get(lower, "")
        if login:
            return login.lower(), login
        # Fallback: extract from noreply email
        login = _login_from_noreply(lower)
        if login:
            return login.lower(), login
        # Fallback: configured alias on the author name. Remember the
        # email too so later lookups (other repos, Developer Detail)
        # resolve it directly.
        login = name_to_login.get(name.lower(), "") if name else ""
        if login:
            email_to_login.setdefault(lower, login)
            return login.lower(), login
        return lower, ""

    def _for_author(email: str, name: str) -> dict:
        key, login = _resolve_email_key(email, name)
        d = _get_or_create(key, name, email)
        # Keep the longest name (likely more complete)
        if len(name) > len(d["name"]):
            d["name"] = name
        if login and not d["github_login"]:
            d["github_login"] = login
        return d

    # Aggregate commits (already date-filtered by git)
    for c in commits:
        d = _for_author(c.author_email, c.author_name)
        d["commits"] += 1
        d["additions"] += c.additions
        d["deletions"] += c.deletions
        d["excluded_additions"] += c.excluded_additions
        d["excluded_deletions"] += c.excluded_deletions

    # Merge commits: credited as merges, no commits or lines
    for c in merge_commits or []:
        _for_author(c.author_email, c.author_name)["merge_commits"] += 1

    # Aggregate PRs — key by login (same namespace as resolved commits)
    for pr in prs or []:
        login = (pr.get("user") or {}).get("login", "")
        in_range_opened = _in_range(pr.get("created_at"), since, until, tz)
        in_range_merged = bool(pr.get("merged_at")) and _in_range(
            pr.get("merged_at"), since, until, tz,
        )
        in_range_closed = _pr_closed_unmerged_in(pr, since, until, tz)
        open_at_end = _pr_open_at_end(pr, since, until, tz)

        if login and (in_range_opened or in_range_merged or in_range_closed or open_at_end):
            d = _for_login(login)
            if in_range_opened:
                d["prs_opened"] += 1
            if open_at_end:
                d["prs_open"] += 1
            if in_range_merged:
                d["prs_merged"] += 1
            if in_range_closed:
                d["prs_closed"] += 1

        # Whoever pressed merge, whatever they wrote
        merger = _merged_by_login(pr) if in_range_merged else ""
        if merger:
            _for_login(merger)["prs_merged_by"] += 1

    # Aggregate issues — same pattern
    for issue in issues or []:
        login = (issue.get("user") or {}).get("login", "")
        if not login:
            continue
        in_range_opened = _in_range(issue.get("created_at"), since, until, tz)
        in_range_closed = (
            issue.get("state") == "closed"
            and _in_range(issue.get("closed_at"), since, until, tz)
        )
        if not in_range_opened and not in_range_closed:
            continue
        d = _for_login(login)
        if in_range_opened:
            d["issues_opened"] += 1
        if in_range_closed:
            d["issues_closed"] += 1

    # Aggregate inline review comments — same pattern
    for review in reviews or []:
        login = (review.get("user") or {}).get("login", "")
        if login and _in_range(review.get("created_at"), since, until, tz):
            _for_login(login)["review_comments"] += 1

    # PR conversation comments
    for comment in pr_comments or []:
        login = (comment.get("user") or {}).get("login", "")
        if login and _in_range(comment.get("created_at"), since, until, tz):
            _for_login(login)["pr_comments"] += 1

    # Aggregate submitted PR reviews (already date-filtered)
    for rv in review_records or []:
        d = _for_login(rv.reviewer)
        d["reviews_submitted"] += 1
        if rv.state == "approved":
            d["reviews_approved"] += 1

    # Aggregate tags (already date-filtered by get_tags_in_range)
    for tag in tags or []:
        _for_author(tag.tagger_email, tag.tagger_name)["tags_created"] += 1

    return [
        DeveloperActivity(**data)
        for data in dev_map.values()
        if data["name"]
    ]


def _build_pr_records(
    prs: list[dict], repo_name: str, since: str, until: str, tz: tzinfo | None = None,
) -> list[PRRecord]:
    """Build PRRecords for PRs opened, merged or closed in the window, or
    still open at its end. ``state`` is the state at the end of the window."""
    records: list[PRRecord] = []
    for pr in prs:
        merged_at = pr.get("merged_at")
        merged_date = local_date(merged_at, tz) if merged_at else None
        relevant = (
            _in_range(pr.get("created_at"), since, until, tz)
            or (merged_date is not None and since <= merged_date <= until)
            or _pr_closed_unmerged_in(pr, since, until, tz)
            or _pr_open_at_end(pr, since, until, tz)
        )
        if not relevant:
            continue
        records.append(PRRecord(
            number=pr.get("number", 0),
            title=pr.get("title", ""),
            author=(pr.get("user") or {}).get("login", ""),
            state=_pr_state_at(pr, until, tz),
            created_at=local_date(pr.get("created_at"), tz),
            merged_at=merged_date,
            url=pr.get("html_url", ""),
            repo_name=repo_name,
            merged_by=_merged_by_login(pr),
        ))
    return records


def _pr_may_have_reviews_in_range(
    pr: dict, since: str, until: str, tz: tzinfo | None = None,
) -> bool:
    """Whether a PR could carry a review submitted within [since, until].

    Submitting a review bumps the PR's ``updated_at`` (confirmed against the
    live GitHub API: a PR whose last event is a review has ``updated_at``
    equal to that review's ``submitted_at``; Bitbucket bumps ``updated_on``
    the same way), so a PR last updated before ``since`` can be skipped
    without fetching its reviews — one request per PR otherwise.
    """
    updated = local_date(pr.get("updated_at"), tz)
    created = local_date(pr.get("created_at"), tz)
    return bool(updated) and updated >= since and created <= until


def _build_review_records(
    pr: dict, raw_reviews: list[dict], repo_name: str, since: str, until: str,
    tz: tzinfo | None = None,
) -> list[ReviewRecord]:
    """Build ReviewRecords for one PR's submitted reviews within [since, until].

    Skipped:
    - pending (unsubmitted) reviews, which have no ``submitted_at``;
    - reviews on the reviewer's own PR (replies to feedback, not review work);
    - COMMENTED reviews with an empty body — GitHub records every reply in an
      inline thread as one of these, and their inline comments are already
      counted under review comments.
    """
    pr_author = (pr.get("user") or {}).get("login", "")
    records: list[ReviewRecord] = []
    for rv in raw_reviews:
        reviewer = (rv.get("user") or {}).get("login", "")
        submitted = local_date(rv.get("submitted_at"), tz)
        state = (rv.get("state") or "").lower()
        if not reviewer or not submitted or not (since <= submitted <= until):
            continue
        if state == "pending" or reviewer.lower() == pr_author.lower():
            continue
        if state == "commented" and not (rv.get("body") or "").strip():
            continue
        records.append(ReviewRecord(
            pr_number=pr.get("number", 0),
            pr_title=pr.get("title", ""),
            pr_author=pr_author,
            reviewer=reviewer,
            state=state,
            submitted_at=submitted,
            url=pr.get("html_url", ""),
            repo_name=repo_name,
        ))
    return records


def _count_prs(
    prs: list[dict], since: str, until: str, tz: tzinfo | None = None,
) -> tuple[int, int, int, int]:
    """Count PRs (opened, merged, open at end, closed unmerged) for the window.

    ``opened`` and ``merged`` are independent (a PR opened before the range
    and merged inside it counts only in ``merged``), so ``opened - merged``
    is meaningless. ``open_at_end`` counts PRs still open when the window
    ends that were opened or updated during it (see ``_pr_open_at_end``);
    ``closed`` counts PRs closed without merging during the window.
    """
    opened = merged = open_at_end = closed = 0
    for pr in prs:
        if _in_range(pr.get("created_at"), since, until, tz):
            opened += 1
        if _pr_open_at_end(pr, since, until, tz):
            open_at_end += 1
        if pr.get("merged_at") and _in_range(pr.get("merged_at"), since, until, tz):
            merged += 1
        if _pr_closed_unmerged_in(pr, since, until, tz):
            closed += 1
    return opened, merged, open_at_end, closed


def _closed_pr_branches(prs: list[dict]) -> set[str]:
    """Head branches whose every PR is closed (merged or declined).

    Work on such a branch has either landed on the default branch (and is
    counted there) or been abandoned; counting the branch again under
    Branch Activity double-counts the re-landed copies. A branch with any
    open PR is kept.
    """
    closed: set[str] = set()
    open_: set[str] = set()
    for pr in prs:
        ref = ((pr.get("head") or {}).get("ref") or "").strip()
        if not ref:
            continue
        if pr.get("state") == "open":
            open_.add(ref)
        else:
            closed.add(ref)
    return closed - open_


def _count_issues(
    issues: list[dict], since: str, until: str, tz: tzinfo | None = None,
) -> tuple[int, int]:
    """Count issues opened and closed within the date range."""
    opened = 0
    closed = 0
    for issue in issues:
        if _in_range(issue.get("created_at"), since, until, tz):
            opened += 1
        if issue.get("state") == "closed" and _in_range(issue.get("closed_at"), since, until, tz):
            closed += 1
    return opened, closed


def _count_reviews(
    reviews: list[dict], since: str, until: str, tz: tzinfo | None = None,
) -> int:
    """Count review (or PR conversation) comments within the date range."""
    return sum(1 for r in reviews if _in_range(r.get("created_at"), since, until, tz))


def _earliest_copies(copies: dict[str, list[CommitRecord]]) -> list[CommitRecord]:
    """One commit per patch-id: the earliest committed (ties: lowest SHA).

    The same patch on several branches is one piece of work. The earliest
    copy is the original; later ones are rebases or "pulled in" re-commits,
    often by someone else, so keeping the first one found (which depended
    on branch names) credited the re-applier about half the time.
    """
    def _key(c: CommitRecord):
        ts = parse_timestamp(c.committed_at)
        return (ts.timestamp() if ts else float("inf"), c.date, c.sha)

    return [min(group, key=_key) for group in copies.values()]


def _pr_conversation_comments(comments: list) -> list[dict]:
    """The issue comments that are on PRs (``/pull/`` in ``html_url``)."""
    return [
        c for c in comments
        if isinstance(c, dict) and "/pull/" in (c.get("html_url") or "")
    ]


def _filter_repo_by_user(
    ra: RepoActivity, user_filter: str
) -> RepoActivity | None:
    """Filter a RepoActivity to only include one user's data.

    Returns None if the user has no activity in this repo.
    """
    needle = user_filter.lower()

    # Filter developers first so we can use their emails as a fallback for
    # commit matching — git author names may not contain the GitHub login.
    # A developer whose only in-range activity is on a feature branch lives
    # in branch_developers, not developers, so both must be checked.
    filtered_devs = [
        d for d in ra.developers
        if _matches_user(d.name, d.email, user_filter, d.github_login)
    ]
    filtered_branch_devs = [
        d for d in ra.branch_developers
        if _matches_user(d.name, d.email, user_filter, d.github_login)
    ]
    # Emails belonging to the matched developer(s), used to catch commits
    # where the git author name doesn't contain the GitHub login string.
    matched_emails = {d.email.lower() for d in filtered_devs + filtered_branch_devs}

    def _mine(c: CommitRecord) -> bool:
        return (
            _matches_user(c.author_name, c.author_email, user_filter)
            or c.author_email.lower() in matched_emails
        )

    filtered_commits = [c for c in ra.commits if _mine(c)]
    filtered_branch_commits = [c for c in ra.branch_commits if _mine(c)]
    filtered_merges = [c for c in ra.merge_commits if _mine(c)]

    # PRs they wrote or merged
    filtered_prs = [
        p for p in ra.prs
        if needle in p.author.lower() or (p.merged_by and needle in p.merged_by.lower())
    ]

    # Filter tags to only this user
    filtered_tags = [
        t for t in ra.tags
        if _matches_user(t.tagger_name, t.tagger_email, user_filter)
        or t.tagger_email.lower() in matched_emails
    ]

    # Filter submitted reviews to only this user (as reviewer)
    filtered_reviews = [
        rv for rv in ra.review_records
        if needle in rv.reviewer.lower()
    ]

    # Recalculate repo-level counts from the filtered developer data
    def _total(attr: str) -> int:
        return sum(getattr(d, attr) for d in filtered_devs)

    # Filter branches to only this user
    filtered_branches: list[BranchActivity] = []
    for branch in ra.branches:
        b_commits = [c for c in branch.commits if _mine(c)]
        b_devs = [
            d for d in branch.developers
            if _matches_user(d.name, d.email, user_filter, d.github_login)
        ]
        if b_commits or b_devs:
            filtered_branches.append(BranchActivity(
                name=branch.name,
                repo_name=branch.repo_name,
                commits=b_commits,
                developers=b_devs,
            ))

    has_activity = (
        filtered_commits or filtered_merges or filtered_tags
        or filtered_branches or filtered_branch_commits
        or any(
            _total(a) for a in (
                "prs_opened", "prs_merged", "prs_open", "prs_closed", "prs_merged_by",
                "issues_opened", "review_comments", "reviews_submitted", "pr_comments",
            )
        )
    )
    if not has_activity:
        return None

    return RepoActivity(
        name=ra.name,
        owner=ra.owner,
        commits=filtered_commits,
        prs_opened=_total("prs_opened"),
        prs_merged=_total("prs_merged"),
        issues_opened=_total("issues_opened"),
        issues_closed=_total("issues_closed"),
        review_comments=_total("review_comments"),
        developers=filtered_devs,
        prs=filtered_prs,
        tags=filtered_tags,
        branches=filtered_branches,
        branch_commits=filtered_branch_commits,
        branch_developers=filtered_branch_devs,
        reviews_submitted=_total("reviews_submitted"),
        reviews_approved=_total("reviews_approved"),
        review_records=filtered_reviews,
        api_errors=list(ra.api_errors),
        prs_open=_total("prs_open"),
        prs_closed=_total("prs_closed"),
        merge_commits=filtered_merges,
        pr_comments=_total("pr_comments"),
        fetch_error=ra.fetch_error,
        account_slug=ra.account_slug,
        api_available=ra.api_available,
    )


def _build_alias_lookups(
    aliases: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split configured ``developer_aliases`` into (email→login, name→login).

    Both lookups are lowercased. An alias containing ``@`` is treated as an
    email, anything else as a git author name.
    """
    email_to_login: dict[str, str] = {}
    name_to_login: dict[str, str] = {}
    for login, names in (aliases or {}).items():
        for alias in names:
            key = alias.strip().lower()
            if not key:
                continue
            if "@" in key:
                email_to_login[key] = login
            else:
                name_to_login[key] = login
    return email_to_login, name_to_login


RATE_LIMIT_WARN_BELOW = 500


def _print_rate_limit_preflight(client, account: AccountConfig) -> None:
    """Show the GitHub token's remaining requests before a scan starts.

    The token shares one 5,000/hour bucket with everything else using it
    (e.g. the ``gh`` CLI), so a scan can start already near-exhausted.
    GitHub only; Bitbucket has no equivalent endpoint.

    Reads the ``x-ratelimit-*`` headers the client captured from its last
    response (the ``validate_token()`` call that precedes this), falling
    back to ``GET /rate_limit`` — whose body was seen live reporting the
    token as unused while real responses showed 1,800+ requests used.
    """
    if account.provider != "github":
        return
    try:
        core = getattr(client, "last_rate_limit", None)
        if not isinstance(core, dict):
            core = client.get_rate_limit().get("resources", {}).get("core", {})
        remaining = int(core.get("remaining", 0))
        limit = int(core.get("limit", 0))
        reset_at = int(core.get("reset", 0))
    except Exception as e:  # pre-flight only; never block the scan
        logger.warning(f"Could not read rate limit: {e}")
        return
    reset_str = datetime.fromtimestamp(reset_at, tz=timezone.utc).strftime(
        "%H:%M:%S UTC"
    )
    line = (
        f"GitHub API rate limit: [bold]{remaining:,}[/bold]/{limit:,} requests "
        f"remaining (resets at {reset_str})"
    )
    if remaining < RATE_LIMIT_WARN_BELOW:
        console.print(
            f"[yellow]{line} — low; the scan will wait for the reset if it "
            f"runs out.[/yellow]"
        )
    else:
        console.print(line)


@dataclass
class _AccountScan:
    """One account's scanned repos, before cross-repo backfill and --user."""

    account: AccountConfig
    repos: list[RepoActivity]
    email_to_login: dict[str, str]
    local_reason: str = ""
    # Why git fetch was skipped ("--no-fetch", ...); "" when it ran
    fetch_skipped: str = ""
    # Per clone, after the optional fetch: (last successful fetch as far as
    # git can tell, or None; whether its most recent fetch failed)
    fetch_states: list[tuple[datetime | None, bool]] = field(default_factory=list)
    # GitHub profile names of logins seen only through the API (login → name)
    profile_names: dict[str, str] = field(default_factory=dict)


def _api_only_logins(repos: list[RepoActivity]) -> list[str]:
    """Logins seen only through the API (PRs, reviews, comments), never on a
    commit: the people who may have commits under a git email GitHub
    doesn't link to their account. Bots are left out."""
    api_only: set[str] = set()
    with_commits: set[str] = set()
    for ra in repos:
        for dev in ra.developers + ra.branch_developers:
            if not dev.github_login:
                continue
            if dev.email.endswith("@github"):
                api_only.add(dev.github_login)
            else:
                with_commits.add(dev.github_login.lower())
    return sorted(
        login for login in api_only
        if login.lower() not in with_commits and not login.lower().endswith("[bot]")
    )


def _fetch_profile_names(client, account: AccountConfig, logins: list[str]) -> dict[str, str]:
    """GitHub profile names (login → name) for ``logins``, full names only.

    ``_build_result`` folds a git identity whose author name is exactly one
    of these names into the login, e.g. Simon Calder's commits from an
    email not linked to his account join his PRs and reviews as simon777.
    One-word names are too generic to match on and are skipped, as are bot
    accounts. GitHub only; a failed lookup is skipped, never fatal.
    """
    if account.provider != "github" or not logins:
        return {}
    names: dict[str, str] = {}
    for login in logins:
        try:
            profile = client.get_user(login)
        except Exception as e:  # enrichment only; never fail the scan
            logger.warning(f"Could not read the GitHub profile of {login}: {e}")
            continue
        if not isinstance(profile, dict) or profile.get("type", "User") != "User":
            continue
        name = profile.get("name")
        if isinstance(name, str) and len(name.split()) >= 2:
            names[login] = name.strip()
    return names


def _profile_name_lookup(scans: list[_AccountScan]) -> dict[str, str]:
    """Normalised profile name → login, leaving out names two logins share."""
    logins_by_name: dict[str, set[str]] = {}
    for s in scans:
        for login, name in s.profile_names.items():
            logins_by_name.setdefault(_normalise_name(name), set()).add(login)
    return {
        name: next(iter(logins))
        for name, logins in logins_by_name.items()
        if name and len(logins) == 1
    }


def _report_timezone(config: AppConfig) -> str:
    tz_name = getattr(config, "timezone", None)
    return tz_name if isinstance(tz_name, str) and tz_name else DEFAULT_TIMEZONE


def _scan_account(
    config: AppConfig,
    account: AccountConfig,
    window: DateWindow,
    repo_filter: str | None = None,
    project: ProjectConfig | None = None,
    include_branches: bool = False,
    local_only: bool = False,
    fetch: bool = True,
    alias_email_to_login: dict[str, str] | None = None,
    alias_name_to_login: dict[str, str] | None = None,
) -> _AccountScan | None:
    """Scan one account's local checkouts (and its API). None if nothing to scan."""
    since, until, tz = window.since, window.until, window.tz
    alias_email_to_login = alias_email_to_login or {}
    alias_name_to_login = alias_name_to_login or {}

    # Resolve data_dir
    base_dir = account.data_dir if account.data_dir else config.data_dir
    data_dir = Path(base_dir) / account.slug

    # Scan local checkouts
    console.print(f"Scanning local repos in [bold]{data_dir}[/bold]...")
    local_repos = scan_local_repos(data_dir)

    if not local_repos:
        console.print(
            f"[yellow]No local repos found in {data_dir}.[/yellow]\n"
            f"Run [bold]auditer checkout --account {account.slug}[/bold] first."
        )
        return None

    # Filter excluded repos
    local_repos, skipped = filter_excluded_repos(
        local_repos, account, lambda r: r.name
    )
    if skipped:
        console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

    if project:
        local_repos, proj_skipped = filter_by_project(
            local_repos, project, account.slug, lambda r: r.name
        )
        if proj_skipped:
            console.print(
                f"Filtered to [bold]{len(local_repos)}[/bold] repo(s) in project '{project.name}'."
            )

    # Filter to specific repo if requested
    if repo_filter:
        local_repos = [r for r in local_repos if r.name == repo_filter]
        if not local_repos:
            console.print(f"[yellow]Repo '{repo_filter}' not found in local checkouts.[/yellow]")
            return None

    total = len(local_repos)
    console.print(
        f"Found [bold]{total}[/bold] local repos. "
        f"Scanning activity from {since} to {until} (whole days, {tz})..."
    )

    # Decide whether the hosting API is used at all. Local git alone gives
    # commits, tags and branches; the API adds PRs, issues and reviews. An
    # account whose access has ended (a finished client engagement) still
    # has its checkouts, so a failed login degrades to local-only rather
    # than aborting the report.
    client: GitHubClient | None = None
    local_reason = ""
    if local_only:
        local_reason = "--local-only"
    elif account.local_only:
        local_reason = "local_only: true in config"
    elif not account.token:
        local_reason = "no token configured"

    if not local_reason:
        client = get_provider(account)
        try:
            username = client.validate_token()
            logger.info(f"Authenticated as: {username}")
        except AuthenticationError as e:
            client.close()
            client = None
            local_reason = f"API authentication failed: {e}"
            console.print(f"[yellow]Authentication failed:[/yellow] {e}")
        else:
            _print_rate_limit_preflight(client, account)

    if local_reason:
        console.print(
            f"[yellow]Local git only ({local_reason}). PRs, issues and reviews "
            f"will not be in the report; commits, tags and branches will.[/yellow]"
        )

    # Fetch every clone first, so commits pushed since the last `checkout`
    # are in the report. An account marked local_only has lost access, so
    # a fetch would only fail.
    fetch_skipped = ""
    if not fetch:
        fetch_skipped = "--no-fetch"
    elif account.local_only:
        fetch_skipped = "local_only: true in config"
    auth_kwargs = (
        {"auth_username": BITBUCKET_GIT_AUTH_USERNAME}
        if account.provider == "bitbucket" else {}
    )

    def process_repo(lr):
        fetch_error = ""
        if not fetch_skipped:
            fetched = fetch_repo(lr.path, lr.clone_url, token=account.token, **auth_kwargs)
            if not fetched.success:
                fetch_error = fetched.message
                logger.warning(f"{lr.name}: {fetched.message}")
        fetch_state = (last_fetch_time(lr.path), last_fetch_failed(lr.path))

        # Get commit log from local git.
        # Default: --all (comprehensive, includes unmerged branch work).
        # With --branches: default branch only + separate branch-only commits
        # (no double-counting, detailed breakdown).
        if include_branches:
            raw_commits = get_commit_log(
                lr.path, since, until, default_branch=lr.default_branch, tz=tz,
            )
        else:
            raw_commits = get_commit_log(lr.path, since, until, tz=tz)

        def _record(c: dict) -> CommitRecord:
            return CommitRecord(
                sha=c["sha"],
                author_name=c["author_name"],
                author_email=c["author_email"],
                date=c["date"],
                subject=c["subject"],
                additions=c.get("additions", 0),
                deletions=c.get("deletions", 0),
                repo_name=lr.name,
                excluded_additions=c.get("excluded_additions", 0),
                excluded_deletions=c.get("excluded_deletions", 0),
                committed_at=c.get("committed_at", ""),
            )

        commits = [_record(c) for c in raw_commits]

        # Get tags from local git
        raw_tags = get_tags_in_range(lr.path, since, until, tz=tz)
        tag_records = [
            TagRecord(
                name=t["name"],
                tagger_name=t["tagger_name"],
                tagger_email=t["tagger_email"],
                date=t["date"],
                repo_name=lr.name,
                owner=lr.owner,
            )
            for t in raw_tags
        ]

        # Fetch API data if token available
        prs: list[dict] = []
        issues: list[dict] = []
        reviews: list[dict] = []
        pr_comments: list[dict] = []
        review_records: list[ReviewRecord] = []
        # Seeded with configured email aliases; API commits add to it below.
        repo_email_to_login: dict[str, str] = dict(alias_email_to_login)
        # Every failed API call is recorded here and surfaced in the report
        # header and terminal summary. Silently swallowing these once produced
        # a report of all-zero PR/review figures after the token's rate limit
        # was exhausted, indistinguishable from a genuinely quiet week.
        api_errors: list[str] = []

        def _api_failed(call: str, e: Exception) -> None:
            api_errors.append(f"{call}: {e}")
            logger.warning(f"{lr.name}: {call} failed — {e}")

        if client:
            # API date filters are a superset (UTC dates covering the
            # window); the exact cut is made in the report's timezone.
            # Updated-since covers every PR opened, merged, closed, reviewed
            # or still in flight during the window (see _pr_open_at_end).
            try:
                prs = client.list_pulls(lr.owner, lr.repo_slug, since=window.api_since)
            except Exception as e:
                _api_failed("list_pulls", e)
            try:
                issues = client.list_repo_issues(lr.owner, lr.repo_slug, since=window.api_since)
            except Exception as e:
                _api_failed("list_repo_issues", e)
            try:
                reviews = client.list_review_comments(
                    lr.owner, lr.repo_slug, since=window.api_since,
                )
            except Exception as e:
                _api_failed("list_review_comments", e)
            try:
                pr_comments = _pr_conversation_comments(
                    client.list_issue_comments(lr.owner, lr.repo_slug, since=window.api_since)
                )
            except Exception as e:
                _api_failed("list_issue_comments", e)

            # Who merged each PR merged in the window: the list endpoint
            # leaves merged_by out, so one request per merged PR.
            for pr in prs:
                if "merged_by" in pr or not window.contains(pr.get("merged_at")):
                    continue
                try:
                    full = client.get_pull(lr.owner, lr.repo_slug, pr.get("number"))
                except Exception as e:
                    _api_failed(f"get_pull #{pr.get('number')}", e)
                    continue
                if isinstance(full, dict):
                    pr["merged_by"] = full.get("merged_by")

            # Submitted PR reviews — one request per PR touched in range
            for pr in prs:
                if not _pr_may_have_reviews_in_range(pr, since, until, tz):
                    continue
                try:
                    raw_reviews = client.list_pull_reviews(
                        lr.owner, lr.repo_slug, pr.get("number"),
                    )
                except Exception as e:
                    _api_failed(f"list_pull_reviews #{pr.get('number')}", e)
                    continue
                review_records.extend(
                    _build_review_records(pr, raw_reviews, lr.name, since, until, tz)
                )

            # Build email→login mapping from API commits
            try:
                api_commits = client.list_repo_commits(
                    lr.owner, lr.repo_slug, since=window.api_since, until=window.api_until,
                )
                for ac in api_commits:
                    if ac.get("author") and ac["author"].get("login"):
                        email = ac.get("commit", {}).get("author", {}).get("email", "")
                        if email:
                            repo_email_to_login[email.lower()] = ac["author"]["login"]
            except Exception as e:
                _api_failed("list_repo_commits", e)

        # Branch activity (unmerged work on feature branches)
        branch_activities: list[BranchActivity] = []
        # A commit can be reachable from more than one active branch (e.g. a
        # release branch built on top of several stacked feature branches),
        # or exist as a rebased copy of work already on the default branch
        # (same patch, different SHA). Both are deduplicated by patch-id,
        # falling back to SHA when git can't produce one:
        # - a branch commit whose patch is already on the default branch in
        #   range is dropped everywhere (it's counted via the default branch);
        # - across branches, the first occurrence wins for the account-wide
        #   `dedup_branch_commits` the earliest-committed copy wins (the
        #   original, not a later "pulled in" re-application on a QA branch,
        #   whatever the branch names); per-branch `branch_activities` keep their
        #   own overlapping commits since each still reflects that branch.
        # Branches whose every PR is closed are skipped outright: the work
        # either landed (and is on the default branch) or was abandoned.
        branch_copies: dict[str, list[CommitRecord]] = {}
        dedup_branch_commits: list[CommitRecord] = []
        merge_revs = ["--all"]
        if include_branches:
            merge_revs = [f"origin/{lr.default_branch}"]
            main_patches = set(
                get_patch_ids(
                    lr.path, [f"origin/{lr.default_branch}"], since, until, tz=tz,
                ).values()
            )
            skip_branches = _closed_pr_branches(prs)
            active_branches = list_active_branches(
                lr.path, lr.default_branch, since, until, tz=tz,
            )
            for ab in active_branches:
                if ab["name"] in skip_branches:
                    logger.debug(f"{lr.name}: skipping branch {ab['name']} (PR closed)")
                    continue
                merge_revs.append(f"origin/{ab['name']}")
                raw_bc = get_branch_commits(
                    lr.path, ab["name"], lr.default_branch, since, until, tz=tz,
                )
                patch_ids = get_patch_ids(
                    lr.path, [f"origin/{ab['name']}", "--not", f"origin/{lr.default_branch}"],
                    since, until, tz=tz,
                )
                bc = []
                for c in raw_bc:
                    pid = patch_ids.get(c["sha"], c["sha"])
                    if pid in main_patches:
                        continue  # rebased copy of work already on the default branch
                    bc.append(_record(c))
                if bc:
                    branch_devs = _aggregate_developers(
                        bc, since=since, until=until,
                        email_to_login=repo_email_to_login,
                        name_to_login=alias_name_to_login, tz=tz,
                    )
                    branch_activities.append(BranchActivity(
                        name=ab["name"],
                        repo_name=lr.name,
                        commits=bc,
                        developers=branch_devs,
                    ))
                    for c in bc:
                        branch_copies.setdefault(patch_ids.get(c.sha, c.sha), []).append(c)
            dedup_branch_commits = _earliest_copies(branch_copies)

        # Merge commits on the same refs the commits came from
        merge_records = [
            _record(c) for c in get_merge_commits(lr.path, merge_revs, since, until, tz=tz)
        ]

        branch_developers = _aggregate_developers(
            dedup_branch_commits, since=since, until=until,
            email_to_login=repo_email_to_login,
            name_to_login=alias_name_to_login, tz=tz,
        ) if dedup_branch_commits else []

        pr_records = _build_pr_records(prs, lr.name, since, until, tz)
        prs_opened, prs_merged, prs_open, prs_closed = _count_prs(prs, since, until, tz)
        issues_opened, issues_closed = _count_issues(issues, since, until, tz)
        review_count = _count_reviews(reviews, since, until, tz)
        pr_comment_count = _count_reviews(pr_comments, since, until, tz)

        developers = _aggregate_developers(
            commits, prs, issues, reviews, tags=tag_records,
            since=since, until=until, email_to_login=repo_email_to_login,
            review_records=review_records,
            name_to_login=alias_name_to_login,
            merge_commits=merge_records, pr_comments=pr_comments, tz=tz,
        )

        ra = RepoActivity(
            name=lr.name,
            owner=lr.owner,
            commits=commits,
            prs_opened=prs_opened,
            prs_merged=prs_merged,
            issues_opened=issues_opened,
            issues_closed=issues_closed,
            review_comments=review_count,
            developers=developers,
            prs=pr_records,
            tags=tag_records,
            branches=branch_activities,
            branch_commits=dedup_branch_commits,
            branch_developers=branch_developers,
            reviews_submitted=len(review_records),
            reviews_approved=sum(1 for rv in review_records if rv.state == "approved"),
            review_records=review_records,
            api_errors=api_errors,
            prs_open=prs_open,
            prs_closed=prs_closed,
            merge_commits=merge_records,
            pr_comments=pr_comment_count,
            fetch_error=fetch_error,
            account_slug=account.slug,
            api_available=client is not None,
        )
        return ra, repo_email_to_login, fetch_state

    repo_activities: list[RepoActivity] = []
    email_to_login: dict[str, str] = dict(alias_email_to_login)
    fetch_states: list[tuple[datetime | None, bool]] = []
    try:
        with Progress(console=console) as progress:
            label = "Fetching and scanning repos" if not fetch_skipped else "Scanning repos"
            task = progress.add_task(label, total=total)

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(process_repo, lr): lr for lr in local_repos}
                for future in as_completed(futures):
                    ra, repo_e2l, fetch_state = future.result()
                    repo_activities.append(ra)
                    email_to_login.update(repo_e2l)
                    fetch_states.append(fetch_state)
                    progress.advance(task)
        profile_names = (
            _fetch_profile_names(client, account, _api_only_logins(repo_activities))
            if client else {}
        )
    finally:
        if client:
            client.close()

    return _AccountScan(
        account=account,
        repos=repo_activities,
        email_to_login=email_to_login,
        local_reason=local_reason,
        fetch_skipped=fetch_skipped,
        fetch_states=fetch_states,
        profile_names=profile_names,
    )


def _describe_data_as_of(scans: list[_AccountScan], tz: tzinfo) -> str:
    """One line saying how fresh the git data is (and the API data).

    Clones whose most recent fetch failed are described separately: their
    data dates from their last successful fetch, which for an account whose
    access has ended can be days old (a failed fetch also stamps
    FETCH_HEAD, so its mtime alone said "fresh").
    """
    fmt = "%Y-%m-%d %H:%M %Z"

    def _span(times: list[datetime]) -> str:
        oldest = min(times).astimezone(tz).strftime(fmt)
        newest = max(times).astimezone(tz).strftime(fmt)
        return oldest if oldest == newest else f"{oldest} to {newest}"

    states = [st for s in scans for st in s.fetch_states]
    skipped = sorted({s.fetch_skipped for s in scans if s.fetch_skipped})
    if len(skipped) == len(scans):
        how = f"git fetch skipped ({', '.join(skipped)})"
    elif skipped:
        how = f"git fetched at the start of this run, except {', '.join(skipped)}"
    else:
        how = "git fetched at the start of this run"

    ok = [t for t, failed in states if t is not None and not failed]
    stale = [t for t, failed in states if failed]
    if ok:
        line = f"commits and tags as of {_span(ok)} ({how})"
    elif stale:
        line = f"commits and tags as of each clone's last successful fetch ({how})"
    else:
        line = f"commits and tags as of the clones' last fetch, time unknown ({how})"
    if stale:
        known = [t for t in stale if t is not None]
        when = f": {_span(known)}" if known else ""
        line += (
            f"; the most recent git fetch failed for {len(stale)} of {len(states)} "
            f"repo(s), so their data is from their last successful fetch{when}"
        )
    if any(not s.local_reason for s in scans):
        line += "; PRs, issues and reviews from the API at report time"
    return line


def _build_result(
    scans: list[_AccountScan],
    window: DateWindow,
    user_filter: str | None,
    alias_name_to_login: dict[str, str],
) -> ActivityResult:
    """Backfill logins across every scanned repo, apply --user, aggregate."""
    combined = len(scans) > 1
    combined_email_to_login: dict[str, str] = {}
    for s in scans:
        combined_email_to_login.update(s.email_to_login)
    repo_activities = [r for s in scans for r in s.repos]

    def _label(r: RepoActivity) -> str:
        return f"{r.owner}/{r.name}" if combined else r.name

    # Backfill github_login on per-repo developers using the combined
    # email→login mapping from all repos (and, combined, all accounts).
    # Each repo only builds a mapping from its own API commits, so a
    # developer whose email→login link was discovered in repo A might
    # still lack a github_login in repo B. Each branch's own developer list
    # (its Branch Activity row) is backfilled the same way.
    profile_name_to_login = _profile_name_lookup(scans)
    linked_by_profile: set[tuple[str, str]] = set()
    for ra in repo_activities:
        per_branch = [d for b in ra.branches for d in b.developers]
        for dev in ra.developers + ra.branch_developers + per_branch:
            if not dev.github_login:
                login = combined_email_to_login.get(dev.email.lower(), "")
                if not login:
                    login = _login_from_noreply(dev.email)
                if not login:
                    login = alias_name_to_login.get(dev.name.lower(), "")
                if not login:
                    # A commit author named exactly as an API-only login's
                    # GitHub profile (their git email isn't on the account)
                    login = profile_name_to_login.get(_normalise_name(dev.name), "")
                    if login:
                        linked_by_profile.add((dev.name, login))
                if login:
                    dev.github_login = login
                    # Let Developer Detail find this person's commits under
                    # the aliased email as well.
                    combined_email_to_login.setdefault(dev.email.lower(), login)
    for name, login in sorted(linked_by_profile):
        console.print(f"Linked git author '{name}' to {login} (GitHub profile name)")

    # Apply user filter: strip each repo down to only the filtered user's
    # commits, PRs, issues, and reviews. Drop repos with zero activity.
    scanned_repos = list(repo_activities)
    if user_filter:
        filtered = []
        for ra in repo_activities:
            filtered_ra = _filter_repo_by_user(ra, user_filter)
            if filtered_ra is not None:
                filtered.append(filtered_ra)
        repo_activities = filtered

    # Aggregate developers across all repos
    from auditer.reports.activity import _aggregate_developers_from_repos
    aggregated_devs = _aggregate_developers_from_repos(repo_activities)

    if combined:
        local_reason = "; ".join(
            f"{s.account.slug}: {s.local_reason}" for s in scans if s.local_reason
        )
    else:
        local_reason = scans[0].local_reason

    return ActivityResult(
        account_slug=" + ".join(s.account.slug for s in scans),
        since=window.since,
        until=window.until,
        repos=sorted(repo_activities, key=lambda r: len(r.all_commits), reverse=True),
        developers=aggregated_devs,
        email_to_login=combined_email_to_login,
        api_errors={_label(r): list(r.api_errors) for r in scanned_repos if r.api_errors},
        local_only_reason=local_reason,
        timezone=str(window.tz),
        data_as_of=_describe_data_as_of(scans, window.tz),
        fetch_errors={_label(r): r.fetch_error for r in scanned_repos if r.fetch_error},
        accounts=[s.account.slug for s in scans],
        api_available=any(not s.local_reason for s in scans),
    )


def _write_report_and_summary(
    activity_result: ActivityResult,
    report_path: Path,
    user_filter: str | None,
    repo_filter: str | None,
    include_branches: bool,
) -> None:
    report = generate_activity_report(
        activity_result, user_filter=user_filter, repo_filter=repo_filter,
        include_branches=include_branches,
    )
    report_path.write_text(report)
    console.print(f"\nReport written to [bold]{report_path}[/bold]")

    # Terminal summary
    console.print()
    console.print("=" * 50)
    console.print(f"  ACTIVITY SUMMARY — {activity_result.account_slug}")
    console.print("=" * 50)
    console.print(
        f"  Date range:            {activity_result.since} to {activity_result.until}"
        + (f" ({activity_result.timezone})" if activity_result.timezone else "")
    )
    if activity_result.data_as_of:
        console.print(f"  Data as of:            {activity_result.data_as_of}")
    if activity_result.local_only_reason:
        console.print(
            f"  Data source:           local git only ({activity_result.local_only_reason})"
        )
    if user_filter:
        console.print(f"  Filtered to user:      {user_filter}")
    if repo_filter:
        console.print(f"  Filtered to repo:      {repo_filter}")
    repos = activity_result.repos
    api = activity_result.api_available

    def _api(value: int) -> str:
        return f"{value:,}" if api else "n/a (local git only)"

    console.print(f"  Total commits:         {activity_result.total_commits:,}")
    console.print(f"  Merge commits:         {sum(len(r.merge_commits) for r in repos):,}")
    humans = [d for d in activity_result.developers if not _is_bot(d)]
    bots = len(activity_result.developers) - len(humans)
    console.print(
        f"  Active developers:     {len(humans)}"
        + (f" (plus {bots} bot{'s' if bots != 1 else ''})" if bots else "")
    )
    console.print(f"  Total PRs opened:      {_api(activity_result.total_prs)}")
    console.print(f"  PRs merged:            {_api(sum(r.prs_merged for r in repos))}")
    console.print(f"  PRs open at end:       {_api(sum(r.prs_open for r in repos))}")
    console.print(f"  Total issues opened:   {_api(activity_result.total_issues)}")
    console.print(f"  PR reviews submitted:  {_api(activity_result.total_reviews)}")
    console.print(f"  PR comments:           {_api(sum(r.pr_comments for r in repos))}")
    console.print(f"  Total tags created:    {activity_result.total_tags:,}")
    if include_branches:
        branch_count = sum(len(r.branches) for r in repos)
        console.print(f"  Branches with activity:{branch_count:>5,}")
        console.print(f"  Branch commits:        {activity_result.total_branch_commits:,}")
    console.print("=" * 50)

    # Never let a degraded report pass as a complete one. Checked against
    # the unfiltered scan results: a --user run should still flag a repo
    # whose PR fetch failed, even if that user has no activity there.
    failed_repos = sorted(activity_result.api_errors)
    if failed_repos:
        console.print(
            f"[red]API data incomplete for {len(failed_repos)} repo(s): "
            f"{', '.join(failed_repos)}[/red]"
        )
        console.print(
            "[red]PR, issue and review figures for these repos are missing or "
            "partial — see the Data quality note in the report.[/red]"
        )
    stale = sorted(activity_result.fetch_errors)
    if stale:
        console.print(
            f"[yellow]git fetch failed for {len(stale)} repo(s): {', '.join(stale)} — "
            f"their commits and tags are as of their last successful fetch.[/yellow]"
        )


def _empty_result(accounts: list[AccountConfig], window: DateWindow) -> ActivityResult:
    return ActivityResult(
        account_slug=" + ".join(a.slug for a in accounts),
        since=window.since,
        until=window.until,
        repos=[],
        developers=[],
        timezone=str(window.tz),
        accounts=[a.slug for a in accounts],
    )


def run_activity(
    config: AppConfig,
    account: AccountConfig,
    since: str,
    until: str,
    user_filter: str | None = None,
    repo_filter: str | None = None,
    project: ProjectConfig | None = None,
    include_branches: bool = False,
    local_only: bool = False,
    fetch: bool = True,
) -> ActivityResult:
    """Scan one account and write its report."""
    if project:
        reports_dir = Path(config.reports_dir) / project.name
    else:
        reports_dir = Path(config.reports_dir) / account.slug
    reports_dir.mkdir(parents=True, exist_ok=True)

    window = DateWindow.from_dates(since, until, _report_timezone(config))
    alias_email_to_login, alias_name_to_login = _build_alias_lookups(
        getattr(config, "developer_aliases", {}) or {}
    )
    scan = _scan_account(
        config, account, window, repo_filter=repo_filter, project=project,
        include_branches=include_branches, local_only=local_only, fetch=fetch,
        alias_email_to_login=alias_email_to_login,
        alias_name_to_login=alias_name_to_login,
    )
    if scan is None:
        return _empty_result([account], window)

    activity_result = _build_result([scan], window, user_filter, alias_name_to_login)
    report_path = reports_dir / _build_report_filename(
        since, until, user_filter, repo_filter,
    )
    _write_report_and_summary(
        activity_result, report_path, user_filter, repo_filter, include_branches,
    )
    return activity_result


def run_combined_activity(
    config: AppConfig,
    accounts: list[AccountConfig],
    since: str,
    until: str,
    user_filter: str | None = None,
    repo_filter: str | None = None,
    project: ProjectConfig | None = None,
    include_branches: bool = False,
    local_only: bool = False,
    fetch: bool = True,
) -> ActivityResult:
    """Scan several accounts and write one report across all of them.

    Developers are merged across accounts by GitHub login (and the usual
    email/name fallbacks), so someone who works in an organisation and in
    their own repos gets one leaderboard row. Repos are labelled
    ``owner/name``. Written to ``reports/combined/`` (or the project's
    directory) as ``activity_{since}_{until}_accounts-a+b.md``.
    """
    reports_dir = Path(config.reports_dir) / (project.name if project else "combined")
    reports_dir.mkdir(parents=True, exist_ok=True)

    window = DateWindow.from_dates(since, until, _report_timezone(config))
    alias_email_to_login, alias_name_to_login = _build_alias_lookups(
        getattr(config, "developer_aliases", {}) or {}
    )
    scans: list[_AccountScan] = []
    for account in accounts:
        console.print(f"\n[bold blue]Scanning account: {account.slug}[/bold blue]")
        scan = _scan_account(
            config, account, window, repo_filter=repo_filter, project=project,
            include_branches=include_branches, local_only=local_only, fetch=fetch,
            alias_email_to_login=alias_email_to_login,
            alias_name_to_login=alias_name_to_login,
        )
        if scan is not None:
            scans.append(scan)

    if not scans:
        return _empty_result(accounts, window)

    activity_result = _build_result(scans, window, user_filter, alias_name_to_login)
    report_path = reports_dir / _build_report_filename(
        since, until, user_filter, repo_filter,
        accounts=[s.account.slug for s in scans] if len(scans) > 1 else None,
    )
    _write_report_and_summary(
        activity_result, report_path, user_filter, repo_filter, include_branches,
    )
    return activity_result


def _validate_date(ctx, param, value):
    if value is None:
        return value
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise click.BadParameter("use YYYY-MM-DD") from None
    return value


@click.command("activity")
@click.option(
    "--account", "account_slugs", multiple=True,
    help="Account slug. Repeat it (--account a --account b) for one combined "
         "report across those accounts.",
)
@click.option("--all", "activity_all", is_flag=True, help="All configured accounts")
@click.option(
    "--combine", "combine", is_flag=True,
    help="With --all or --project: one combined report across the accounts "
         "instead of one report per account.",
)
@click.option("--user", "user_filter", default=None, help="Filter to a specific developer")
@click.option("--repo", "repo_filter", default=None, help="Filter to a specific repo")
@click.option(
    "--since",
    "since_date",
    default=None,
    callback=_validate_date,
    help="Start date, inclusive (YYYY-MM-DD, default: 30 days ago). Whole days "
         "in defaults.timezone (Europe/London unless configured).",
)
@click.option(
    "--until",
    "until_date",
    default=None,
    callback=_validate_date,
    help="End date, inclusive (YYYY-MM-DD, default: today)",
)
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.option(
    "--branches", "include_branches", is_flag=True,
    help="Show activity on feature branches (separate from default branch)",
)
@click.option(
    "--local-only", "local_only", is_flag=True,
    help="Report from local checkouts only; never call the hosting API "
         "(no PRs, issues or reviews). Also set per account with local_only: true.",
)
@click.option(
    "--fetch/--no-fetch", "fetch", default=True,
    help="git fetch every clone before scanning (default), so the report "
         "includes work pushed since the last checkout.",
)
@click.pass_context
def activity_cmd(
    ctx: click.Context,
    account_slugs: tuple[str, ...],
    activity_all: bool,
    combine: bool,
    user_filter: str | None,
    repo_filter: str | None,
    since_date: str | None,
    until_date: str | None,
    project_name: str | None,
    include_branches: bool,
    local_only: bool,
    fetch: bool,
):
    """Track developer activity across repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config

    project = None
    if project_name:
        project = get_project(config, project_name)
        if not activity_all and not account_slugs:
            # --project alone: operate on all accounts in the project
            accounts = get_accounts_for_project(config, project)
            if not accounts:
                console.print(
                    f"[yellow]No accounts have repos in project '{project_name}'[/yellow]"
                )
                sys.exit(0)

    if project and not activity_all and not account_slugs:
        pass  # accounts already set above
    elif activity_all:
        accounts = config.accounts
    elif account_slugs:
        accounts = [get_account(config, slug) for slug in dict.fromkeys(account_slugs)]
    else:
        console.print("[red]Specify --account SLUG or --all[/red]")
        sys.exit(1)

    # Default date range: the last 30 days, as dates in the report timezone
    today = datetime.now(get_timezone(_report_timezone(config))).date()
    if not until_date:
        until_date = today.isoformat()
    if not since_date:
        since_date = (today - timedelta(days=30)).isoformat()

    common = dict(
        since=since_date,
        until=until_date,
        user_filter=user_filter,
        repo_filter=repo_filter,
        project=project,
        include_branches=include_branches,
        local_only=local_only,
        fetch=fetch,
    )
    # Several --account flags always combine; --all/--project only on --combine
    if len(accounts) > 1 and (combine or len(account_slugs) > 1):
        console.print(
            f"\n[bold blue]Combined activity report: "
            f"{', '.join(a.slug for a in accounts)}[/bold blue]"
        )
        run_combined_activity(config, accounts, **common)
        return

    for account in accounts:
        console.print(f"\n[bold blue]Activity report: {account.slug}[/bold blue]")
        run_activity(config, account, **common)
