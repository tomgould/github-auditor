from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta

from auditer.models import ActivityResult


def _dev_display_name(dev) -> str:
    """Format developer display ID: login when available, else name."""
    if dev.github_login:
        return dev.github_login
    return dev.name


# Bot accounts whose login has no "[bot]" suffix (GitHub's Copilot coding
# agent opens and comments on PRs as "Copilot", account type Bot)
_BOT_LOGINS = {"copilot"}


def _is_bot(dev) -> bool:
    """GitHub App accounts (renovate[bot], dependabot[bot], ...) end in ``[bot]``."""
    return (
        dev.github_login.lower().endswith("[bot]")
        or dev.name.lower().endswith("[bot]")
        or dev.github_login.lower() in _BOT_LOGINS
    )


def _normalise_name(name: str) -> str:
    """Lowercase alphanumerics only, so 'Anton Zorin' == 'Anton-Zorin'
    and 'Fiona Vale' == 'fionavale'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Shared by the Developer Leaderboard and Per-Repo Detail tables.
# "Reviews" counts submitted PR reviews (approvals included); "Review
# Comments" counts inline line comments; "PR Comments" counts comments in
# the PR conversation. "Merged PRs" are PRs they wrote that were merged;
# "Merges" are PRs they merged, whoever wrote them; "Merge Commits" are
# git merge commits they authored (never in Commits or line counts).
_DEV_TABLE_HEADER = [
    "| Developer | Commits | Merge Commits | +Lines | -Lines | Net Lines "
    "| Merged PRs | Merges | Open PRs | Issues | Reviews | Approvals "
    "| Review Comments | PR Comments | Tags |",
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
]

_DEV_TABLE_LEGEND = (
    "Commits exclude merge commits. Merged PRs: PRs they wrote that were "
    "merged in the window. Merges: PRs they merged, whoever wrote them. "
    "Open PRs: PRs they wrote still open at the end of the window and "
    "opened or updated during it (including PRs opened earlier). Review "
    "Comments are inline comments on the diff; PR Comments are comments in "
    "the PR conversation."
)


# Shown instead of 0 where the figure needs the hosting API and the report
# was built from local git alone: 0 would read as "did no reviews".
NOT_AVAILABLE = "n/a"


def _dev_table_row(d, api: bool = True) -> str:
    def api_cell(value) -> str:
        return str(value) if api else NOT_AVAILABLE

    return (
        f"| {_dev_display_name(d)} | {d.commits:,} | {d.merge_commits:,} "
        f"| {d.additions:,} | {d.deletions:,} | {d.net_lines:,} "
        f"| {api_cell(d.prs_merged)} | {api_cell(d.prs_merged_by)} | {api_cell(d.prs_open)} "
        f"| {api_cell(d.issues_opened)} | {api_cell(d.reviews_submitted)} "
        f"| {api_cell(d.reviews_approved)} "
        f"| {api_cell(d.review_comments)} | {api_cell(d.pr_comments)} | {d.tags_created} |"
    )


def _repo_label(r, combined: bool) -> str:
    """``owner/name`` in a combined (multi-account) report, else ``name``."""
    return f"{r.owner}/{r.name}" if combined else r.name


def _repo_developers(repo) -> list:
    """A repo's default-branch and feature-branch developers, merged."""
    return _aggregate_developers_from_repos([repo])


def generate_activity_report(
    result: ActivityResult,
    user_filter: str | None = None,
    repo_filter: str | None = None,
    include_branches: bool = False,
) -> str:
    """Generate a Markdown activity report.

    Data in `result` should already be filtered by user/repo before calling
    this function. The filter parameters are only used for display in the header.
    """
    lines: list[str] = []

    # Header
    combined = result.is_combined
    # No API data at all (local-only): PR, issue, review and comment figures
    # are "n/a", not 0. A combined report with some API accounts keeps
    # numbers; its Data source line names the accounts without them.
    api = result.api_available and not (result.local_only_reason and not combined)
    lines.append(f"# Developer Activity Report — {result.account_slug}")
    lines.append("")
    if result.timezone:
        lines.append(
            f"**Date range:** {result.since} to {result.until} "
            f"(whole days, {result.timezone})"
        )
    else:
        lines.append(f"**Date range:** {result.since} to {result.until}")
    if combined:
        lines.append(f"**Accounts:** {', '.join(result.accounts)} (combined)")
    if include_branches:
        lines.append("**Mode:** Default branch + feature branches (no double-counting)")
    else:
        lines.append("**Mode:** All branches")
    if result.local_only_reason:
        lines.append(
            f"**Data source:** Local git only ({result.local_only_reason}). "
            f"PRs, issues and reviews are not included; commits, tags and "
            f"branches are complete."
        )
    if result.data_as_of:
        lines.append(f"**Data as of:** {result.data_as_of}")
    if user_filter:
        lines.append(f"**Filtered to user:** {user_filter}")
    if repo_filter:
        lines.append(f"**Filtered to repo:** {repo_filter}")

    repos = result.repos
    developers = result.developers

    # Data quality — a repo whose API calls failed has zero or partial PR,
    # issue and review figures. Say so up front so the report can't be read
    # as "a quiet week" when it was actually a rate-limited scan.
    # result.api_errors covers every scanned repo, including ones a --user
    # filter dropped; fall back to the repos' own lists for callers that
    # build an ActivityResult by hand.
    failed: dict[str, list[str]] = dict(result.api_errors) or {
        _repo_label(r, combined): list(r.api_errors) for r in repos if r.api_errors
    }
    if failed:
        lines.append("")
        lines.append(
            f"**⚠ Data quality:** API data incomplete for "
            f"{len(failed)} repo(s). PR, issue and review figures for "
            f"these repos are missing or partial; commits and tags (from local "
            f"git) are unaffected."
        )
        lines.append("")
        for name in sorted(failed):
            lines.append(f"- **{name}**: {'; '.join(failed[name])}")
    if result.fetch_errors:
        lines.append("")
        lines.append(
            f"**⚠ Stale git data:** git fetch failed for "
            f"{len(result.fetch_errors)} repo(s); their commits and tags are as "
            f"of their last successful fetch."
        )
        lines.append("")
        for name in sorted(result.fetch_errors):
            lines.append(f"- **{name}**: {result.fetch_errors[name]}")
    lines.append("")

    humans = [d for d in developers if not _is_bot(d)]
    bots = [d for d in developers if _is_bot(d)]

    # Contributors lookup table
    if humans:
        lines.append("## Contributors")
        lines.append("")
        lines.append("| ID | Name | Email |")
        lines.append("|---|---|---|")
        sorted_contribs = sorted(humans, key=lambda d: d.commits, reverse=True)
        for d in sorted_contribs:
            display_id = _dev_display_name(d)
            lines.append(f"| {display_id} | {d.name} | {d.email} |")
        lines.append("")

    # Summary table — commits include feature-branch commits (r.all_commits)
    # so the totals match the Developer Leaderboard.
    total_commits = sum(len(r.all_commits) for r in repos)
    bot_commits = sum(d.commits for d in bots)
    total_additions = sum(d.additions for d in developers)
    total_deletions = sum(d.deletions for d in developers)
    total_prs_opened = sum(r.prs_opened for r in repos)
    total_prs_merged = sum(r.prs_merged for r in repos)
    total_prs_open = sum(r.prs_open for r in repos)
    total_prs_closed = sum(r.prs_closed for r in repos)
    total_merge_commits = sum(len(r.merge_commits) for r in repos)
    total_pr_comments = sum(r.pr_comments for r in repos)
    total_issues_opened = sum(r.issues_opened for r in repos)
    total_issues_closed = sum(r.issues_closed for r in repos)
    total_review_comments = sum(r.review_comments for r in repos)
    total_reviews_submitted = sum(r.reviews_submitted for r in repos)
    total_reviews_approved = sum(r.reviews_approved for r in repos)
    total_tags = sum(len(r.tags) for r in repos)

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    lines.append(f"| Active repos | {len([r for r in repos if r.all_commits])} |")
    lines.append(f"| Active developers | {len(humans)} |")
    lines.append(f"| Total commits | {total_commits:,} |")
    if bots:
        lines.append(f"| &nbsp;&nbsp;of which by bots | {bot_commits:,} |")
    lines.append(f"| Merge commits (not in commits or lines) | {total_merge_commits:,} |")
    lines.append(f"| Lines added | {total_additions:,} |")
    lines.append(f"| Lines deleted | {total_deletions:,} |")
    lines.append(f"| Net lines | {total_additions - total_deletions:,} |")
    na = f"{NOT_AVAILABLE} (local git only)"
    api_rows = [
        ("PRs opened", f"{total_prs_opened}"),
        ("PRs merged", f"{total_prs_merged}"),
        ("PRs closed without merging", f"{total_prs_closed}"),
        ("PRs still open at end of window", f"{total_prs_open}"),
        ("Issues opened/closed", f"{total_issues_opened}/{total_issues_closed}"),
        ("PR reviews submitted", f"{total_reviews_submitted:,}"),
        ("PR approvals", f"{total_reviews_approved:,}"),
        ("Review comments", f"{total_review_comments:,}"),
        ("PR conversation comments", f"{total_pr_comments:,}"),
    ]
    for label, value in api_rows:
        lines.append(f"| {label} | {value if api else na} |")
    lines.append(f"| Tags created | {total_tags:,} |")
    lines.append("")

    # Combined report: one row per account, so the per-developer totals
    # below can be traced back to where the work happened.
    if combined:
        lines.append("## Per-Account Summary")
        lines.append("")
        lines.append(
            "| Account | Active repos | Commits | Merge Commits | +Lines | -Lines "
            "| Net Lines | PRs merged | Tags |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for slug in result.accounts:
            acct_repos = [r for r in repos if r.account_slug == slug]
            acct_commits = [c for r in acct_repos for c in r.all_commits]
            adds = sum(c.additions for c in acct_commits)
            dels = sum(c.deletions for c in acct_commits)
            acct_api = any(r.api_available for r in acct_repos)
            prs_merged = sum(r.prs_merged for r in acct_repos) if acct_api else NOT_AVAILABLE
            lines.append(
                f"| {slug} | {len([r for r in acct_repos if r.all_commits])} "
                f"| {len(acct_commits):,} "
                f"| {sum(len(r.merge_commits) for r in acct_repos):,} "
                f"| {adds:,} | {dels:,} | {adds - dels:,} "
                f"| {prs_merged} "
                f"| {sum(len(r.tags) for r in acct_repos)} |"
            )
        lines.append("")

    # Developer leaderboard — sorted by net lines (additions - deletions)
    if humans:
        sorted_devs = sorted(humans, key=lambda d: d.net_lines, reverse=True)
        lines.append("## Developer Leaderboard")
        lines.append("")
        lines.append(_DEV_TABLE_LEGEND)
        if not api:
            lines.append("")
            lines.append(
                f"{NOT_AVAILABLE}: not available — this report was built from local "
                f"git only, so PRs, issues, reviews and comments are unknown, not zero."
            )
        lines.append("")
        lines.extend(_DEV_TABLE_HEADER)
        for d in sorted_devs:
            lines.append(_dev_table_row(d, api))
        lines.append("")

    # Automation — bot accounts, kept out of the leaderboard but still in
    # the Summary totals, so Leaderboard + Automation reconciles to them.
    if bots:
        sorted_bots = sorted(bots, key=lambda d: d.net_lines, reverse=True)
        lines.append("## Automation")
        lines.append("")
        lines.append(
            "Bot accounts (dependency updaters, CI). Included in the Summary "
            "totals, excluded from the Developer Leaderboard."
        )
        lines.append("")
        lines.extend(_DEV_TABLE_HEADER)
        for d in sorted_bots:
            lines.append(_dev_table_row(d, api))
        lines.append("")

    # Lines left out of the counts, per developer, so the exclusions are
    # visible rather than silent (e.g. a CSV import that would otherwise
    # have been most of someone's "lines added").
    excluded = sorted(
        (d for d in developers if d.excluded_additions or d.excluded_deletions),
        key=lambda d: d.excluded_additions + d.excluded_deletions, reverse=True,
    )
    if excluded:
        lines.append("## Data Quality: Lines Excluded From Counts")
        lines.append("")
        lines.append(
            "Lines in lock files, vendored, built or minified code, recorded "
            "test data (mocks, fixtures, snapshots), binaries, editor settings, "
            "and data files "
            "(CSV, TSV, JSONL, NDJSON anywhere; JSON and XML under data/, "
            "attachments/, exports/ or dumps/) are not in +Lines/-Lines above. "
            "This is what was left out."
        )
        lines.append("")
        lines.append("| Developer | +Lines excluded | -Lines excluded |")
        lines.append("|---|---|---|")
        for d in excluded:
            lines.append(
                f"| {_dev_display_name(d)} | {d.excluded_additions:,} "
                f"| {d.excluded_deletions:,} |"
            )
        lines.append("")

    # Collect all commits (default + feature branch) for date breakdowns
    all_commits = []
    for r in repos:
        all_commits.extend(r.all_commits)

    # Daily breakdown — every day in the since→until range
    lines.append("## Daily Breakdown")
    lines.append("")
    lines.append("| Date | Commits | +Lines | -Lines | Net Lines | Active Devs |")
    lines.append("|---|---|---|---|---|---|")

    daily = _group_by_date(all_commits)
    for date in _date_range(result.since, result.until):
        day_commits = daily.get(date, [])
        adds = sum(c.additions for c in day_commits)
        dels = sum(c.deletions for c in day_commits)
        net = adds - dels
        devs = len({c.author_email for c in day_commits})
        lines.append(
            f"| {date} | {len(day_commits):,} | {adds:,} | {dels:,} | {net:,} | {devs} |"
        )
    lines.append("")

    # Weekly breakdown — weeks shown as date ranges
    lines.append("## Weekly Breakdown")
    lines.append("")
    lines.append("| Week | Commits | +Lines | -Lines | Net Lines | Active Devs |")
    lines.append("|---|---|---|---|---|---|")

    weekly = _group_by_week(all_commits, result.since, result.until)
    # Insertion order is chronological; sorting the labels alphabetically
    # put "Oct 05" before "Sep 07" on any range spanning a month boundary.
    for week_label in weekly.keys():
        week_commits = weekly[week_label]
        adds = sum(c.additions for c in week_commits)
        dels = sum(c.deletions for c in week_commits)
        net = adds - dels
        devs = len({c.author_email for c in week_commits})
        lines.append(
            f"| {week_label} | {len(week_commits):,} | {adds:,} | {dels:,} | {net:,} | {devs} |"
        )
    lines.append("")

    # Per-repo activity table — commits and developers include feature-branch
    # work, matching the Summary and Leaderboard.
    active_repos = [
        r for r in repos
        if r.all_commits or r.prs_opened or r.prs_merged or r.issues_opened
        or r.tags or r.reviews_submitted or r.review_comments
        or r.merge_commits or r.pr_comments or r.prs_open or r.prs_closed
    ]
    if active_repos:
        def _repo_net_lines(r):
            return sum(c.additions - c.deletions for c in r.all_commits)

        sorted_repos = sorted(active_repos, key=_repo_net_lines, reverse=True)
        lines.append("## Per-Repo Activity")
        lines.append("")
        lines.append(
            "| Repo | Commits | +Lines | -Lines | Net Lines "
            "| Merged PRs | Open PRs | Issues | Reviews | Tags | Top Contributor |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted_repos:
            repo_commits = r.all_commits
            adds = sum(c.additions for c in repo_commits)
            dels = sum(c.deletions for c in repo_commits)
            net = adds - dels
            top = _top_contributor(_repo_developers(r))
            if r.api_available:
                api_cells = (
                    f"| {r.prs_merged} | {r.prs_open} | {r.issues_opened} "
                    f"| {r.reviews_submitted} "
                )
            else:
                api_cells = "| " + " | ".join([NOT_AVAILABLE] * 4) + " "
            lines.append(
                f"| {_repo_label(r, combined)} | {len(repo_commits):,} "
                f"| {adds:,} | {dels:,} | {net:,} "
                f"{api_cells}| {len(r.tags)} | {top} |"
            )
        lines.append("")

    # Per-repo detail — sorted by net lines
    if active_repos:
        lines.append("## Per-Repo Detail")
        lines.append("")
        for r in sorted(active_repos, key=_repo_net_lines, reverse=True):
            lines.append(f"### {_repo_label(r, combined)}")
            lines.append("")
            repo_devs = _repo_developers(r)
            if repo_devs:
                contribs = sorted(repo_devs, key=lambda d: d.net_lines, reverse=True)
                lines.extend(_DEV_TABLE_HEADER)
                for d in contribs:
                    lines.append(_dev_table_row(d, r.api_available))
            else:
                lines.append("No developer activity.")
            lines.append("")

    # Branch activity — unmerged work on feature branches
    has_branches = any(r.branches for r in repos)
    if has_branches:
        lines.append("## Branch Activity")
        lines.append("")
        lines.append("Commits on feature branches not yet merged to the default branch.")
        lines.append("")
        for r in repos:
            if not r.branches:
                continue
            lines.append(f"### {_repo_label(r, combined)}")
            lines.append("")
            lines.append(
                "| Branch | Commits | +Lines | -Lines | Net Lines | Top Contributor |"
            )
            lines.append("|---|---|---|---|---|---|")
            for b in sorted(r.branches, key=lambda b: sum(c.additions - c.deletions for c in b.commits), reverse=True):
                adds = sum(c.additions for c in b.commits)
                dels = sum(c.deletions for c in b.commits)
                net = adds - dels
                top = _top_contributor(b.developers)
                lines.append(
                    f"| {b.name} | {len(b.commits):,} | {adds:,} | {dels:,} | {net:,} | {top} |"
                )
            lines.append("")

    # Per-developer detail — commits, PRs, tags with GitHub links (humans only)
    if humans:
        lines.append("## Developer Detail")
        lines.append("")
        sorted_devs = sorted(humans, key=lambda d: d.net_lines, reverse=True)
        for dev in sorted_devs:
            lines.append(f"### {_dev_display_name(dev)}")
            lines.append("")

            # Collect this developer's commits across all repos (oldest first)
            dev_commits = _collect_dev_commits(repos, dev, result.email_to_login)
            if dev_commits:
                lines.append("**Commits** (oldest first):")
                lines.append("")
                lines.append("| Date | Repo | Message | +/- |")
                lines.append("|---|---|---|---|")
                for c, owner in dev_commits:
                    url = f"https://github.com/{owner}/{c.repo_name}/commit/{c.sha}"
                    short_sha = c.sha[:7]
                    subject = c.subject[:80]
                    lines.append(
                        f"| {c.date} | {c.repo_name} "
                        f"| [{short_sha}]({url}) {subject} "
                        f"| +{c.additions}/-{c.deletions} |"
                    )
                lines.append("")

            # Collect this developer's PRs across all repos
            dev_prs = _collect_dev_prs(repos, dev)
            if dev_prs:
                lines.append("**Pull Requests:**")
                lines.append("")
                lines.append("| Date | Repo | PR | Status |")
                lines.append("|---|---|---|---|")
                for pr in dev_prs:
                    status = pr.state.capitalize()
                    lines.append(
                        f"| {pr.created_at} | {pr.repo_name} "
                        f"| [#{pr.number}]({pr.url}) {pr.title} "
                        f"| {status} |"
                    )
                lines.append("")

            # PRs this developer merged (any author)
            dev_merged = _collect_dev_merged_prs(repos, dev)
            if dev_merged:
                lines.append("**PRs merged** (by them, any author):")
                lines.append("")
                lines.append("| Merged | Repo | PR | Author |")
                lines.append("|---|---|---|---|")
                for pr in dev_merged:
                    lines.append(
                        f"| {pr.merged_at} | {pr.repo_name} "
                        f"| [#{pr.number}]({pr.url}) {pr.title} | {pr.author} |"
                    )
                lines.append("")

            # Collect the PR reviews this developer submitted
            dev_reviews = _collect_dev_reviews(repos, dev)
            if dev_reviews:
                lines.append("**Reviews** (oldest first):")
                lines.append("")
                lines.append("| Date | Repo | PR | Author | Review |")
                lines.append("|---|---|---|---|---|")
                for rv in dev_reviews:
                    lines.append(
                        f"| {rv.submitted_at} | {rv.repo_name} "
                        f"| [#{rv.pr_number}]({rv.url}) {rv.pr_title} "
                        f"| {rv.pr_author} "
                        f"| {rv.state.replace('_', ' ').capitalize()} |"
                    )
                lines.append("")

            # Collect this developer's tags across all repos
            dev_tags = _collect_dev_tags(repos, dev, result.email_to_login)
            if dev_tags:
                lines.append("**Tags:**")
                lines.append("")
                lines.append("| Date | Repo | Tag |")
                lines.append("|---|---|---|")
                for tag in dev_tags:
                    url = f"https://github.com/{tag.owner}/{tag.repo_name}/releases/tag/{tag.name}"
                    lines.append(
                        f"| {tag.date} | {tag.repo_name} "
                        f"| [{tag.name}]({url}) |"
                    )
                lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated by GitHub Auditer on {datetime.now().strftime('%Y-%m-%d')}*")
    lines.append("")

    return "\n".join(lines)


def _collect_dev_commits(repos: list, dev, email_to_login: dict[str, str] | None = None) -> list[tuple]:
    """Collect a developer's commits across all repos, sorted oldest first.

    Matches by email equality or by resolving commit email to login
    via ``email_to_login`` and comparing to ``dev.github_login``.

    Searches both default-branch commits (r.commits) and feature-branch
    commits (r.branch_commits — already deduplicated by SHA across
    branches, so no risk of listing the same commit twice), so a developer
    whose only in-range work is on an unmerged branch still shows up here.

    Returns list of (CommitRecord, owner) tuples.
    """
    email_to_login = email_to_login or {}
    dev_email = dev.email.lower()
    dev_login = dev.github_login.lower() if dev.github_login else ""
    commits = []
    for r in repos:
        for c in list(r.commits) + list(r.branch_commits):
            commit_email = c.author_email.lower()
            if commit_email == dev_email:
                commits.append((c, r.owner))
            elif dev_login:
                resolved = email_to_login.get(commit_email, "").lower()
                if resolved == dev_login:
                    commits.append((c, r.owner))
    return sorted(commits, key=lambda x: (x[0].date, x[0].repo_name))


def _collect_dev_prs(repos: list, dev) -> list:
    """Collect a developer's PRs across all repos, sorted by date.

    Matches by github_login (PR author is always a login).
    Falls back to name/email for devs without login.
    """
    prs = []
    dev_login = dev.github_login.lower() if dev.github_login else ""
    for r in repos:
        for pr in r.prs:
            author = pr.author.lower()
            if dev_login and author == dev_login:
                prs.append(pr)
            elif not dev_login and (author == dev.name.lower() or author == dev.email.lower()):
                prs.append(pr)
    return sorted(prs, key=lambda p: p.created_at)


def _collect_dev_merged_prs(repos: list, dev) -> list:
    """PRs a developer merged within the window (merged_by), oldest first."""
    if not dev.github_login:
        return []
    login = dev.github_login.lower()
    merged = [
        pr for r in repos for pr in r.prs
        if pr.merged_by and pr.merged_by.lower() == login and pr.merged_at
        and pr.state == "merged"
    ]
    return sorted(merged, key=lambda p: (p.merged_at, p.repo_name, p.number))


def _collect_dev_reviews(repos: list, dev) -> list:
    """Collect the PR reviews a developer submitted, oldest first.

    Reviewers are always logins, so devs without one have no reviews.
    """
    if not dev.github_login:
        return []
    dev_login = dev.github_login.lower()
    reviews = [
        rv for r in repos for rv in r.review_records
        if rv.reviewer.lower() == dev_login
    ]
    return sorted(reviews, key=lambda rv: (rv.submitted_at, rv.repo_name, rv.pr_number))


def _collect_dev_tags(repos: list, dev, email_to_login: dict[str, str] | None = None) -> list:
    """Collect a developer's tags across all repos, sorted by date.

    Same matching strategy as _collect_dev_commits: email equality
    or login resolution via email_to_login.
    """
    email_to_login = email_to_login or {}
    dev_email = dev.email.lower()
    dev_login = dev.github_login.lower() if dev.github_login else ""
    tags = []
    for r in repos:
        for tag in r.tags:
            tag_email = tag.tagger_email.lower()
            if tag_email == dev_email:
                tags.append(tag)
            elif dev_login:
                resolved = email_to_login.get(tag_email, "").lower()
                if resolved == dev_login:
                    tags.append(tag)
    return sorted(tags, key=lambda t: (t.date, t.repo_name))


def _date_range(since: str, until: str) -> list[str]:
    """Return a list of all date strings from since to until (inclusive)."""
    dates: list[str] = []
    try:
        current = datetime.strptime(since, "%Y-%m-%d")
        end = datetime.strptime(until, "%Y-%m-%d")
    except ValueError:
        return dates
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def _group_by_date(commits: list) -> dict[str, list]:
    """Group commits by date string."""
    groups: dict[str, list] = defaultdict(list)
    for c in commits:
        groups[c.date].append(c)
    return groups


def _week_start(dt: datetime) -> datetime:
    """Return the Monday of the ISO week containing dt."""
    return dt - timedelta(days=dt.weekday())


def _group_by_week(
    commits: list, since: str, until: str
) -> dict[str, list]:
    """Group commits by week, keyed by date range (e.g. 'Feb 10 – Feb 16').

    Generates all weeks spanning the since→until range so weeks with zero
    commits still appear in the output.
    """
    try:
        since_dt = datetime.strptime(since, "%Y-%m-%d")
        until_dt = datetime.strptime(until, "%Y-%m-%d")
    except ValueError:
        return {}

    # Build ordered list of week (monday, sunday) pairs covering the range,
    # starting from the Monday on or before since_dt so every in-range
    # commit lands in a row. Weeks cut by the range are labelled "(partial)"
    # and clipped to the dates actually covered.
    weeks: list[tuple[datetime, datetime]] = []
    current_monday = _week_start(since_dt)
    while current_monday <= until_dt:
        current_sunday = current_monday + timedelta(days=6)
        weeks.append((current_monday, current_sunday))
        current_monday += timedelta(days=7)

    # Initialise groups with week labels in order
    groups: dict[str, list] = {}
    label_by_monday: dict[datetime, str] = {}
    for monday, sunday in weeks:
        start = max(monday, since_dt)
        end = min(sunday, until_dt)
        label = f"{start.strftime('%b %d')} – {end.strftime('%b %d')}"
        if start != monday or end != sunday:
            label += " (partial)"
        groups[label] = []
        label_by_monday[monday] = label

    # Assign commits to weeks
    for c in commits:
        try:
            dt = datetime.strptime(c.date, "%Y-%m-%d")
        except ValueError:
            continue
        label = label_by_monday.get(_week_start(dt))
        if label is not None:
            groups[label].append(c)

    return groups


def _top_contributor(developers: list) -> str:
    """Return the display name of the developer with the most commits.

    Bots are passed over when any human contributed; a repo touched only by
    automation still names the bot rather than showing nothing.
    """
    if not developers:
        return "-"
    humans = [d for d in developers if not _is_bot(d)]
    top = max(humans or developers, key=lambda d: d.commits)
    return _dev_display_name(top)


def _aggregate_developers_from_repos(repos: list) -> list:
    """Aggregate developers across filtered repos.

    Includes both default-branch developers (repo.developers) and
    feature-branch developers (repo.branch_developers — already
    deduplicated by commit SHA across overlapping/stacked branches within
    each repo). Without this, a contributor whose only in-range activity is
    on an unmerged branch (e.g. an open PR) would be silently absent from
    the leaderboard, developer detail, and active-developer count, even
    though they show up correctly in the per-repo Branch Activity table.

    Primary key is github_login (lowercase) when available, falling back
    to email (lowercase). This ensures the same developer merges correctly
    even when using different emails across repos.

    A secondary pass merges entries that share the same human-readable
    name (case-insensitive) for edge cases without login info.
    """
    from auditer.models import DeveloperActivity

    dev_map: dict[str, DeveloperActivity] = {}

    def _add(d: DeveloperActivity) -> None:
        key = d.github_login.lower() if d.github_login else d.email.lower()
        if key in dev_map:
            dev_map[key] = dev_map[key].merged_with(d)
        else:
            dev_map[key] = replace(d)

    for repo in repos:
        for d in repo.developers:
            _add(d)
        for d in repo.branch_developers:
            _add(d)

    # Second pass: merge entries with the same name but different emails.
    # This handles developers who use different email addresses across repos
    # (e.g. personal laptop email vs work email).
    _merge_by_name(dev_map)

    return sorted(dev_map.values(), key=lambda d: d.commits, reverse=True)


# Email local parts too generic to identify a person
_GENERIC_LOCAL_PARTS = {
    "admin", "administrator", "root", "git", "dev", "developer", "developers",
    "info", "user", "ubuntu", "jenkins", "deploy", "deployer", "build", "ci",
    "bot", "support", "team", "test", "mail", "contact", "noreply", "no-reply",
}
_MIN_EMAIL_IDENTITY = 8


def _email_identity(email: str) -> str:
    """A git email's local part, normalised, if it can identify a person.

    ``sarah.campion@acmecorp.example`` and ``sarah.campion@acmepublishing.example``
    (or a domain typo, ``acmecrop.example``) are one person. Skipped: the
    ``login@github`` placeholders and noreply addresses (logins cover
    those), generic mailboxes, and anything under 8 characters.
    """
    email = email.lower()
    if "@" not in email or email.endswith("@github") or "noreply" in email:
        return ""
    local = email.split("@", 1)[0]
    if local in _GENERIC_LOCAL_PARTS:
        return ""
    ident = _normalise_name(local)
    return ident if len(ident) >= _MIN_EMAIL_IDENTITY else ""


def _merge_by_name(dev_map: dict) -> None:
    """Merge developer entries that are the same person under different keys.

    Entries are linked when they share an identity: a normalised name
    (lowercase alphanumerics, so 'Anton Zorin' == 'Anton-Zorin'), a
    normalised GitHub login (so a gmail-keyed 'Fiona Vale' joins the
    'fionavale' login), or an email local part (``_email_identity``).
    Links are transitive. Each group folds into the entry with the most
    commits. Normalised names and logins under 4 characters are too
    generic to merge on and are skipped.
    """
    parent = {key: key for key in dev_map}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    owner_of: dict[str, str] = {}

    def link(ident: str, key: str) -> None:
        if not ident:
            return
        if ident in owner_of:
            parent[find(key)] = find(owner_of[ident])
        else:
            owner_of[ident] = key

    for key, dev in dev_map.items():
        for raw in (dev.name, dev.github_login):
            ident = _normalise_name(raw or "")
            link(ident if len(ident) >= 4 else "", key)
        link(_email_identity(dev.email), key)

    groups: dict[str, list[str]] = defaultdict(list)
    for key in dev_map:
        groups[find(key)].append(key)

    for keys in groups.values():
        if len(keys) < 2:
            continue
        # Most commits first — that entry is kept as canonical
        keys.sort(key=lambda k: dev_map[k].commits, reverse=True)
        canonical_key = keys[0]
        for dup_key in keys[1:]:
            dev_map[canonical_key] = dev_map[canonical_key].merged_with(dev_map[dup_key])
            del dev_map[dup_key]
