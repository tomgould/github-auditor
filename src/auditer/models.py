from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auditer.git_ops import LocalRepoInfo


@dataclass
class AccountConfig:
    slug: str
    type: str  # "user" or "org"
    username: str
    token: str | None = None
    include_private: bool = True
    include_forks: bool = True
    exclude_repos: list[str] = field(default_factory=list)
    include_repos: list[str] = field(default_factory=list)
    data_dir: str | None = None
    # Per-repo branch overrides: {repo_name: branch}. When set, checkout tracks
    # this branch instead of the repo's GitHub default branch.
    branch_overrides: dict[str, str] = field(default_factory=dict)
    # Git provider: "github" or "bitbucket". Defaults to "github" for backward compatibility.
    provider: str = "github"
    # Bitbucket workspace slug (the API path segment replacing GitHub's user/org
    # concept). Unused for github accounts.
    workspace: str | None = None
    # Bitbucket API-token auth requires HTTP Basic auth with the account's
    # Atlassian email as username and the API token (held in `token`) as
    # password. Unused for github accounts.
    auth_email: str | None = None
    # When set, `checkout` only clones/pulls repos pushed to within this
    # many months (per-account persistent default — `checkout --months N`
    # overrides it for a single run). None (default) means no filtering,
    # matching prior behavior.
    active_within_months: int | None = None
    # When True, `activity` never calls the hosting API for this account and
    # reports from the local checkouts alone (commits, tags, branches). For
    # accounts whose API access has ended, e.g. after a client engagement.
    local_only: bool = False


@dataclass
class ProjectConfig:
    name: str
    description: str = ""
    repos: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)


@dataclass
class RepoInfo:
    name: str
    owner: str
    url: str
    private: bool
    description: str
    language: str
    stars: int
    forks: int
    open_issues: int
    license: str
    has_readme: bool
    pushed_at: str
    is_fork: bool
    archived: bool
    topics: list[str]
    default_branch: str
    size_kb: int

    @classmethod
    def from_api_response(cls, data: dict, has_readme: bool = False) -> RepoInfo:
        return cls(
            name=data["name"],
            owner=data["owner"]["login"],
            url=data["html_url"],
            private=data.get("private", False),
            description=data.get("description") or "",
            language=data.get("language") or "",
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            open_issues=data.get("open_issues_count", 0),
            license=data.get("license", {}).get("spdx_id", "") if data.get("license") else "",
            has_readme=has_readme,
            pushed_at=data.get("pushed_at") or data.get("updated_at") or "",
            is_fork=data.get("fork", False),
            archived=data.get("archived", False),
            topics=data.get("topics", []),
            default_branch=data.get("default_branch", "main"),
            size_kb=data.get("size", 0),
        )

    @classmethod
    def from_local_and_api(cls, local: LocalRepoInfo, api_data: dict | None = None) -> RepoInfo:
        if api_data:
            license_val = (
                api_data.get("license", {}).get("spdx_id", "")
                if api_data.get("license")
                else ""
            )
            return cls(
                name=local.name,
                owner=local.owner,
                url=api_data.get("html_url", f"https://github.com/{local.owner}/{local.name}"),
                private=local.private,
                description=api_data.get("description") or "",
                language=api_data.get("language") or "",
                stars=api_data.get("stargazers_count", 0),
                forks=api_data.get("forks_count", 0),
                open_issues=api_data.get("open_issues_count", 0),
                license=license_val,
                has_readme=local.has_readme,
                pushed_at=local.pushed_at or api_data.get("pushed_at") or "",
                is_fork=api_data.get("fork", False),
                archived=api_data.get("archived", False),
                topics=api_data.get("topics", []),
                default_branch=local.default_branch,
                size_kb=api_data.get("size", local.size_kb),
            )
        # No API data — use local fields, defaults for API-only fields
        return cls(
            name=local.name,
            owner=local.owner,
            url=f"https://github.com/{local.owner}/{local.name}",
            private=local.private,
            description="",
            language="",
            stars=0,
            forks=0,
            open_issues=0,
            license="local" if local.has_license_file else "",
            has_readme=local.has_readme,
            pushed_at=local.pushed_at,
            is_fork=False,
            archived=False,
            topics=[],
            default_branch=local.default_branch,
            size_kb=local.size_kb,
        )

    @property
    def visibility(self) -> str:
        return "private" if self.private else "public"

    @property
    def months_inactive(self) -> float:
        if not self.pushed_at:
            return 999.0
        then = datetime.fromisoformat(self.pushed_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - then
        return round(delta.days / 30.44, 1)

    def is_stale(self, stale_months: int = 12) -> bool:
        return self.months_inactive > stale_months

    @property
    def has_license(self) -> bool:
        return bool(self.license)

    @property
    def has_description(self) -> bool:
        return bool(self.description)

    def format_size(self) -> str:
        if self.size_kb < 1024:
            return f"{self.size_kb} KB"
        mb = round(self.size_kb / 1024, 1)
        if mb < 1024:
            return f"{mb} MB"
        gb = round(mb / 1024, 1)
        return f"{gb} GB"

    def health_score(self) -> int:
        score = 0
        # README (20 points)
        if self.has_readme:
            score += 20
        # License (20 points)
        if self.has_license:
            score += 20
        # Description (15 points)
        if self.has_description:
            score += 15
        # Activity (25 points)
        months = self.months_inactive
        if months < 3:
            score += 25
        elif months < 6:
            score += 20
        elif months < 12:
            score += 15
        elif months < 24:
            score += 10
        elif months < 36:
            score += 5
        # Not archived (10 points)
        if not self.archived:
            score += 10
        # Low issues (10 points)
        if self.open_issues <= 5:
            score += 10
        elif self.open_issues <= 20:
            score += 7
        elif self.open_issues <= 50:
            score += 3
        return score

    def health_missing(self) -> list[str]:
        missing: list[str] = []
        if not self.has_readme:
            missing.append("no README")
        if not self.has_license:
            missing.append("no license")
        if not self.has_description:
            missing.append("no description")
        if self.archived:
            missing.append("archived")
        months = self.months_inactive
        if months >= 12:
            missing.append(f"stale ({months}mo)")
        if self.open_issues > 50:
            missing.append(f"{self.open_issues} open issues")
        elif self.open_issues > 20:
            missing.append(f"{self.open_issues} open issues")
        return missing

    def pin_score(self) -> float:
        recency = max(0, 10 - self.months_inactive / 6)
        fork_penalty = 0.5 if self.is_fork else 1.0
        return (self.stars * 3 + self.forks * 2 + recency) * fork_penalty


@dataclass
class AuditResult:
    account_slug: str
    account_username: str
    repos: list[RepoInfo] = field(default_factory=list)
    stale_months: int = 12

    @property
    def public_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if not r.private]

    @property
    def private_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if r.private]

    @property
    def missing_readme(self) -> list[RepoInfo]:
        return [r for r in self.repos if not r.has_readme]

    @property
    def missing_license(self) -> list[RepoInfo]:
        return [r for r in self.repos if not r.has_license]

    @property
    def missing_description(self) -> list[RepoInfo]:
        return [r for r in self.repos if not r.has_description]

    @property
    def stale_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if r.is_stale(self.stale_months)]

    @property
    def forked_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if r.is_fork]

    @property
    def archived_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if r.archived]

    @property
    def total_stars(self) -> int:
        return sum(r.stars for r in self.repos)

    @property
    def total_forks(self) -> int:
        return sum(r.forks for r in self.repos)

    @property
    def average_health(self) -> float:
        if not self.repos:
            return 0.0
        return round(sum(r.health_score() for r in self.repos) / len(self.repos), 1)

    @property
    def low_health_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if r.health_score() < 50]

    @property
    def pin_recommendations(self) -> list[RepoInfo]:
        candidates = [r for r in self.repos if not r.archived and not r.private]
        return sorted(candidates, key=lambda r: r.pin_score(), reverse=True)[:6]


@dataclass
class RepoAccess:
    repo_name: str
    repo_url: str
    private: bool
    collaborators: list[dict] = field(default_factory=list)


@dataclass
class AccessResult:
    account_slug: str
    account_username: str
    repos: list[RepoAccess] = field(default_factory=list)

    @property
    def unique_users(self) -> list[str]:
        users: set[str] = set()
        for repo in self.repos:
            for c in repo.collaborators:
                users.add(c["login"])
        return sorted(users)

    @property
    def total_access_entries(self) -> int:
        return sum(len(r.collaborators) for r in self.repos)


@dataclass
class ProfileData:
    account_slug: str
    username: str
    name: str
    bio: str
    location: str
    company: str
    blog: str
    twitter_username: str
    avatar_url: str
    html_url: str
    public_repos_count: int
    public_gists: int
    followers: int
    following: int
    repos: list[RepoInfo]
    events: list[dict] = field(default_factory=list)

    @property
    def total_stars(self) -> int:
        return sum(r.stars for r in self.repos)

    @property
    def total_forks(self) -> int:
        return sum(r.forks for r in self.repos)

    @property
    def original_repos(self) -> list[RepoInfo]:
        return [r for r in self.repos if not r.is_fork and not r.archived]

    @property
    def language_breakdown(self) -> list[tuple[str, int, float]]:
        originals = self.original_repos
        if not originals:
            return []
        counts: dict[str, int] = {}
        for r in originals:
            if r.language:
                counts[r.language] = counts.get(r.language, 0) + 1
        total = sum(counts.values())
        if total == 0:
            return []
        result = [
            (lang, count, round(count / total * 100, 1))
            for lang, count in counts.items()
        ]
        return sorted(result, key=lambda x: x[1], reverse=True)

    @property
    def pinned_repos(self) -> list[RepoInfo]:
        candidates = [r for r in self.repos if not r.private and not r.archived]
        return sorted(candidates, key=lambda r: r.pin_score(), reverse=True)[:6]


@dataclass
class CommitRecord:
    sha: str
    author_name: str
    author_email: str
    date: str  # ISO date "2025-01-15"
    subject: str
    additions: int
    deletions: int
    repo_name: str
    # Lines in files EXCLUDED_PATHSPECS leaves out of the counts (lock
    # files, vendored code, data files...): raw minus counted.
    excluded_additions: int = 0
    excluded_deletions: int = 0
    # Full committer timestamp (ISO, git %cI); `date` is its local day
    committed_at: str = ""


@dataclass
class PRRecord:
    number: int
    title: str
    author: str  # GitHub login
    state: str  # "open", "closed", "merged" — as at the end of the window
    created_at: str  # ISO date
    merged_at: str | None
    url: str  # GitHub URL
    repo_name: str
    # Login of whoever merged it (GitHub's merged_by), "" if unknown/unmerged
    merged_by: str = ""


@dataclass
class ReviewRecord:
    """One submitted PR review (approve / comment / request changes)."""

    pr_number: int
    pr_title: str
    pr_author: str  # GitHub login of the PR author
    reviewer: str  # GitHub login of the reviewer
    state: str  # "approved", "changes_requested", "commented", "dismissed"
    submitted_at: str  # ISO date
    url: str  # PR URL
    repo_name: str


@dataclass
class TagRecord:
    name: str
    tagger_name: str
    tagger_email: str
    date: str  # ISO date
    repo_name: str
    owner: str


@dataclass
class DeveloperActivity:
    name: str
    email: str
    commits: int
    additions: int
    deletions: int
    prs_opened: int
    prs_merged: int
    issues_opened: int
    issues_closed: int
    review_comments: int
    tags_created: int = 0
    github_login: str = ""
    # Submitted PR reviews (all states) and the approvals among them.
    # Distinct from review_comments, which counts inline line comments.
    reviews_submitted: int = 0
    reviews_approved: int = 0
    # PRs they authored still open at the end of the window and opened or
    # updated during it (a PR opened last week and worked on this week is
    # open work this week; one untouched for months is not). Closed-unmerged
    # PRs are not open. Kept as
    # its own count rather than derived from prs_opened - prs_merged:
    # prs_merged includes PRs opened before the range, so that subtraction
    # went negative for anyone who landed older PRs during the week.
    prs_open: int = 0
    # PRs they authored that were closed without merging in the window.
    prs_closed: int = 0
    # PRs they merged in the window (GitHub's merged_by), whoever wrote them.
    prs_merged_by: int = 0
    # Merge commits they authored (git). Not in commits or line counts.
    merge_commits: int = 0
    # Comments in PR conversations (issue comments on PRs), distinct from
    # review_comments, which are inline comments on the diff.
    pr_comments: int = 0
    # Lines in their commits that EXCLUDED_PATHSPECS left out of
    # additions/deletions (lock files, vendored code, data files...).
    excluded_additions: int = 0
    excluded_deletions: int = 0

    # Identity fields; every other field is a count summed by merged_with().
    _IDENTITY_FIELDS = ("name", "email", "github_login")

    @property
    def total_lines(self) -> int:
        return self.additions + self.deletions

    @property
    def net_lines(self) -> int:
        return self.additions - self.deletions

    def merged_with(self, other: DeveloperActivity) -> DeveloperActivity:
        """A new entry with every count summed.

        Keeps this entry's email (unless it's the ``login@github``
        placeholder and the other has a real one), the longer name (ties
        keep this one) and the first non-empty login. Generic over the dataclass fields so a
        new count can't be silently dropped during aggregation.
        """
        counts = {
            f.name: getattr(self, f.name) + getattr(other, f.name)
            for f in fields(self)
            if f.name not in self._IDENTITY_FIELDS
        }
        # API-only entries carry a "login@github" placeholder; a real git
        # email is more useful in the Contributors table.
        email = self.email
        if email.endswith("@github") and other.email and not other.email.endswith("@github"):
            email = other.email
        return DeveloperActivity(
            name=other.name if len(other.name) > len(self.name) else self.name,
            email=email,
            github_login=self.github_login or other.github_login,
            **counts,
        )


@dataclass
class BranchActivity:
    name: str
    repo_name: str
    commits: list[CommitRecord]
    developers: list[DeveloperActivity]


@dataclass
class RepoActivity:
    name: str
    owner: str
    commits: list[CommitRecord]
    prs_opened: int
    prs_merged: int
    issues_opened: int
    issues_closed: int
    review_comments: int
    developers: list[DeveloperActivity]
    prs: list[PRRecord] = field(default_factory=list)
    tags: list[TagRecord] = field(default_factory=list)
    branches: list[BranchActivity] = field(default_factory=list)
    # Feature-branch commits/developers, deduplicated by SHA across all of
    # `branches` (a commit reachable from two stacked/overlapping branches
    # must only count once). `branches[*].commits`/`developers` are kept
    # per-branch and may overlap — these two fields are the safe source for
    # anything that rolls branch activity into account-wide totals.
    branch_commits: list[CommitRecord] = field(default_factory=list)
    branch_developers: list[DeveloperActivity] = field(default_factory=list)
    reviews_submitted: int = 0
    reviews_approved: int = 0
    review_records: list[ReviewRecord] = field(default_factory=list)
    # API calls that failed for this repo, as "call: error" strings. Non-empty
    # means the PR/issue/review figures above are incomplete — surfaced in the
    # report header and terminal summary so a degraded report is never
    # mistaken for a complete one.
    api_errors: list[str] = field(default_factory=list)
    # PRs open at the end of the window, opened or updated during it
    # (see DeveloperActivity)
    prs_open: int = 0
    # PRs closed without merging in the window
    prs_closed: int = 0
    # Merge commits in the window (not in `commits`, never in line counts)
    merge_commits: list[CommitRecord] = field(default_factory=list)
    # Comments in PR conversations in the window (not inline review comments)
    pr_comments: int = 0
    # Why `git fetch` failed before the scan, "" if it worked or was skipped.
    # Non-empty means commits/tags are as of the clone's last good fetch.
    fetch_error: str = ""
    # Configured account the repo was scanned under (combined reports)
    account_slug: str = ""
    # False when the hosting API wasn't used for this repo (local-only): its
    # PR/issue/review figures are unknown, not zero.
    api_available: bool = True

    @property
    def all_commits(self) -> list[CommitRecord]:
        """Default-branch plus deduped feature-branch commits (disjoint sets)."""
        return list(self.commits) + list(self.branch_commits)


@dataclass
class ActivityResult:
    account_slug: str
    since: str  # ISO date
    until: str  # ISO date
    repos: list[RepoActivity]
    developers: list[DeveloperActivity]  # aggregated across repos
    email_to_login: dict[str, str] = field(default_factory=dict)
    # repo name → failed API calls, across every repo scanned — including
    # repos a --user filter later dropped from `repos`, since a failed PR
    # fetch is exactly what could have hidden that user's activity there.
    api_errors: dict[str, list[str]] = field(default_factory=dict)
    # Non-empty when the report was built from local git alone (no PRs,
    # issues or reviews): why — "--local-only", "local_only: true in
    # config", "no token configured", or an authentication failure.
    local_only_reason: str = ""
    # Timezone the whole-day window is measured in (e.g. "Europe/London")
    timezone: str = ""
    # How fresh the git data is, e.g. "git fetched 2026-10-16 18:55 BST
    # (this run)". "" when unknown (callers that build a result by hand).
    data_as_of: str = ""
    # repo label → git fetch error, for repos whose fetch failed
    fetch_errors: dict[str, str] = field(default_factory=dict)
    # Account slugs in the report; more than one means a combined report
    # (repos are then labelled owner/name).
    accounts: list[str] = field(default_factory=list)
    # False when no scanned account used the API: the report shows PR,
    # issue, review and comment figures as "n/a" rather than 0.
    api_available: bool = True

    @property
    def is_combined(self) -> bool:
        return len(self.accounts) > 1

    @property
    def total_commits(self) -> int:
        """Default-branch plus feature-branch commits."""
        return sum(len(r.all_commits) for r in self.repos)

    @property
    def total_prs(self) -> int:
        return sum(r.prs_opened for r in self.repos)

    @property
    def total_issues(self) -> int:
        return sum(r.issues_opened for r in self.repos)

    @property
    def total_tags(self) -> int:
        return sum(len(r.tags) for r in self.repos)

    @property
    def total_reviews(self) -> int:
        return sum(r.reviews_submitted for r in self.repos)

    @property
    def total_branch_commits(self) -> int:
        return sum(len(r.branch_commits) for r in self.repos)


@dataclass
class ReadmeData:
    name: str
    owner: str
    url: str
    description: str
    language: str
    topics: list[str]
    license: str
    stars: int
    forks: int
    default_branch: str
    is_fork: bool
    # Local scan results
    tree: list[str]
    package_info: dict
    has_tests: bool
    has_ci: bool
    has_contributing: bool
    has_changelog: bool
    entry_points: list[str]
    detected_languages: list[str]
    install_command: str
    test_command: str
