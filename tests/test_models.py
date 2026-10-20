from pathlib import Path

import pytest

from auditer.git_ops import LocalRepoInfo
from auditer.models import (
    AccountConfig,
    ActivityResult,
    AuditResult,
    BranchActivity,
    CommitRecord,
    DeveloperActivity,
    PRRecord,
    ProfileData,
    ProjectConfig,
    ReadmeData,
    RepoActivity,
    RepoInfo,
    TagRecord,
)


class TestAccountConfig:
    def test_exclude_repos_default(self):
        acct = AccountConfig(slug="test", type="user", username="user")
        assert acct.exclude_repos == []

    def test_exclude_repos_set(self):
        acct = AccountConfig(
            slug="test", type="user", username="user",
            exclude_repos=["repo-a", "repo-b"],
        )
        assert acct.exclude_repos == ["repo-a", "repo-b"]


class TestProjectConfig:
    def test_defaults(self):
        p = ProjectConfig(name="my-project")
        assert p.name == "my-project"
        assert p.description == ""
        assert p.repos == []

    def test_with_repos(self):
        p = ProjectConfig(
            name="client-work",
            description="Client X platform",
            repos=["my-user/web-app", "my-org/api"],
        )
        assert p.description == "Client X platform"
        assert len(p.repos) == 2
        assert "my-user/web-app" in p.repos


class TestRepoInfoFromApi:
    def test_basic_fields(self, sample_api_repo):
        repo = RepoInfo.from_api_response(sample_api_repo, has_readme=True)
        assert repo.name == "my-repo"
        assert repo.owner == "testuser"
        assert repo.url == "https://github.com/testuser/my-repo"
        assert repo.private is False
        assert repo.description == "A test repository"
        assert repo.language == "Python"
        assert repo.stars == 42
        assert repo.forks == 5
        assert repo.license == "MIT"
        assert repo.has_readme is True
        assert repo.is_fork is False
        assert repo.archived is False

    def test_private_repo(self, sample_api_repo_private):
        repo = RepoInfo.from_api_response(sample_api_repo_private)
        assert repo.private is True
        assert repo.visibility == "private"
        assert repo.description == ""
        assert repo.license == ""

    def test_fork_repo(self, sample_api_repo_fork):
        repo = RepoInfo.from_api_response(sample_api_repo_fork)
        assert repo.is_fork is True

    def test_missing_license_none(self):
        data = {
            "name": "test",
            "owner": {"login": "user"},
            "html_url": "https://github.com/user/test",
            "license": None,
        }
        repo = RepoInfo.from_api_response(data)
        assert repo.license == ""
        assert repo.has_license is False


class TestRepoInfoProperties:
    def test_visibility_public(self, sample_repo_info):
        assert sample_repo_info.visibility == "public"

    def test_months_inactive(self, sample_repo_info):
        # pushed_at is 2025-01-15, so should be > 0 months
        assert sample_repo_info.months_inactive > 0

    def test_months_inactive_empty(self):
        repo = RepoInfo(
            name="t", owner="o", url="u", private=False, description="",
            language="", stars=0, forks=0, open_issues=0, license="",
            has_readme=False, pushed_at="", is_fork=False, archived=False,
            topics=[], default_branch="main", size_kb=0,
        )
        assert repo.months_inactive == 999.0

    def test_has_license(self, sample_repo_info):
        assert sample_repo_info.has_license is True

    def test_has_description(self, sample_repo_info):
        assert sample_repo_info.has_description is True

    def test_format_size_kb(self):
        repo = RepoInfo(
            name="t", owner="o", url="u", private=False, description="",
            language="", stars=0, forks=0, open_issues=0, license="",
            has_readme=False, pushed_at="", is_fork=False, archived=False,
            topics=[], default_branch="main", size_kb=500,
        )
        assert repo.format_size() == "500 KB"

    def test_format_size_mb(self):
        repo = RepoInfo(
            name="t", owner="o", url="u", private=False, description="",
            language="", stars=0, forks=0, open_issues=0, license="",
            has_readme=False, pushed_at="", is_fork=False, archived=False,
            topics=[], default_branch="main", size_kb=2048,
        )
        assert repo.format_size() == "2.0 MB"

    def test_format_size_gb(self):
        repo = RepoInfo(
            name="t", owner="o", url="u", private=False, description="",
            language="", stars=0, forks=0, open_issues=0, license="",
            has_readme=False, pushed_at="", is_fork=False, archived=False,
            topics=[], default_branch="main", size_kb=2_000_000,
        )
        assert "GB" in repo.format_size()

    def test_pin_score_no_fork(self, sample_repo_info):
        score = sample_repo_info.pin_score()
        assert score > 0

    def test_pin_score_fork_penalty(self, sample_api_repo_fork):
        repo = RepoInfo.from_api_response(sample_api_repo_fork)
        # Fork should have lower score due to 0.5 penalty
        assert repo.pin_score() < 10  # low stars + fork penalty

    def test_health_score_perfect(self):
        """Repo with everything: README, license, description, recent push, not archived, 0 issues."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repo = RepoInfo(
            name="perfect", owner="o", url="u", private=False,
            description="A great repo", language="Python", stars=10, forks=2,
            open_issues=0, license="MIT", has_readme=True, pushed_at=now,
            is_fork=False, archived=False, topics=[], default_branch="main",
            size_kb=100,
        )
        assert repo.health_score() == 100

    def test_health_score_missing_readme(self):
        """Missing README drops score by 20."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repo = RepoInfo(
            name="no-readme", owner="o", url="u", private=False,
            description="A repo", language="Python", stars=0, forks=0,
            open_issues=0, license="MIT", has_readme=False, pushed_at=now,
            is_fork=False, archived=False, topics=[], default_branch="main",
            size_kb=100,
        )
        assert repo.health_score() == 80

    def test_health_score_missing_license(self):
        """Missing license drops score by 20."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repo = RepoInfo(
            name="no-license", owner="o", url="u", private=False,
            description="A repo", language="Python", stars=0, forks=0,
            open_issues=0, license="", has_readme=True, pushed_at=now,
            is_fork=False, archived=False, topics=[], default_branch="main",
            size_kb=100,
        )
        assert repo.health_score() == 80

    def test_health_score_archived(self):
        """Archived repo loses 10 (not-archived) points."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repo = RepoInfo(
            name="archived", owner="o", url="u", private=False,
            description="A repo", language="Python", stars=0, forks=0,
            open_issues=0, license="MIT", has_readme=True, pushed_at=now,
            is_fork=False, archived=True, topics=[], default_branch="main",
            size_kb=100,
        )
        assert repo.health_score() == 90

    def test_health_score_stale_repo(self):
        """Activity score degrades with months_inactive."""
        repo = RepoInfo(
            name="stale", owner="o", url="u", private=False,
            description="A repo", language="Python", stars=0, forks=0,
            open_issues=0, license="MIT", has_readme=True,
            pushed_at="2020-01-01T00:00:00Z",  # very old
            is_fork=False, archived=False, topics=[], default_branch="main",
            size_kb=100,
        )
        # Very stale (>36 months) — gets 0 for activity
        # README(20) + License(20) + Desc(15) + Activity(0) + NotArchived(10) + Issues(10) = 75
        assert repo.health_score() == 75

    def test_health_score_many_issues(self):
        """High open_issues reduces the issues component."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repo = RepoInfo(
            name="issues", owner="o", url="u", private=False,
            description="A repo", language="Python", stars=0, forks=0,
            open_issues=100, license="MIT", has_readme=True, pushed_at=now,
            is_fork=False, archived=False, topics=[], default_branch="main",
            size_kb=100,
        )
        # >50 issues = 0 points for issues component
        assert repo.health_score() == 90  # 20+20+15+25+10+0


class TestAuditResult:
    def test_public_repos(self, sample_audit_result):
        assert len(sample_audit_result.public_repos) == 2

    def test_private_repos(self, sample_audit_result):
        assert len(sample_audit_result.private_repos) == 1

    def test_missing_readme(self, sample_audit_result):
        assert len(sample_audit_result.missing_readme) == 1
        assert sample_audit_result.missing_readme[0].name == "secret-project"

    def test_missing_license(self, sample_audit_result):
        missing = sample_audit_result.missing_license
        assert any(r.name == "secret-project" for r in missing)

    def test_missing_description(self, sample_audit_result):
        assert len(sample_audit_result.missing_description) == 1

    def test_total_stars(self, sample_audit_result):
        assert sample_audit_result.total_stars == 42

    def test_total_forks(self, sample_audit_result):
        assert sample_audit_result.total_forks == 5

    def test_forked_repos(self, sample_audit_result):
        assert len(sample_audit_result.forked_repos) == 1

    def test_pin_recommendations(self, sample_audit_result):
        pins = sample_audit_result.pin_recommendations
        assert len(pins) <= 6
        # All should be public and not archived
        for r in pins:
            assert not r.private
            assert not r.archived

    def test_average_health(self, sample_audit_result):
        avg = sample_audit_result.average_health
        assert isinstance(avg, float)
        assert 0 <= avg <= 100
        # Manually verify: sum of all repo scores / 3
        total = sum(r.health_score() for r in sample_audit_result.repos)
        assert avg == round(total / 3, 1)

    def test_average_health_empty(self):
        result = AuditResult(
            account_slug="test", account_username="test", repos=[]
        )
        assert result.average_health == 0.0

    def test_low_health_repos(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        healthy = RepoInfo(
            name="healthy", owner="o", url="u", private=False,
            description="Good", language="Python", stars=0, forks=0,
            open_issues=0, license="MIT", has_readme=True, pushed_at=now,
            is_fork=False, archived=False, topics=[], default_branch="main",
            size_kb=100,
        )
        unhealthy = RepoInfo(
            name="unhealthy", owner="o", url="u", private=False,
            description="", language="", stars=0, forks=0,
            open_issues=100, license="", has_readme=False,
            pushed_at="2018-01-01T00:00:00Z",
            is_fork=False, archived=True, topics=[], default_branch="main",
            size_kb=0,
        )
        result = AuditResult(
            account_slug="test", account_username="test",
            repos=[healthy, unhealthy],
        )
        low = result.low_health_repos
        assert len(low) == 1
        assert low[0].name == "unhealthy"


def _make_local(
    name="my-repo",
    private=False,
    owner="alice",
    has_readme=True,
    has_license_file=True,
    default_branch="main",
    pushed_at="2025-06-01T12:00:00+00:00",
    size_kb=500,
) -> LocalRepoInfo:
    return LocalRepoInfo(
        name=name,
        path=Path(f"/data/{name}"),
        private=private,
        owner=owner,
        clone_url=f"https://github.com/{owner}/{name}.git",
        has_readme=has_readme,
        has_license_file=has_license_file,
        default_branch=default_branch,
        pushed_at=pushed_at,
        size_kb=size_kb,
    )


def _make_api_data(
    name="my-repo",
    owner="alice",
    description="A test repo",
    language="Python",
    stars=10,
    forks=2,
    open_issues=1,
    license_spdx="MIT",
    is_fork=False,
    archived=False,
    topics=None,
) -> dict:
    return {
        "name": name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{owner}/{name}",
        "description": description,
        "language": language,
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": open_issues,
        "license": {"spdx_id": license_spdx} if license_spdx else None,
        "pushed_at": "2025-06-01T12:00:00Z",
        "fork": is_fork,
        "archived": archived,
        "topics": topics or ["python", "cli"],
        "default_branch": "main",
        "size": 1024,
    }


class TestFromLocalAndApi:
    def test_with_api_data(self):
        local = _make_local()
        api = _make_api_data()
        repo = RepoInfo.from_local_and_api(local, api)

        assert repo.name == "my-repo"
        assert repo.owner == "alice"
        assert repo.description == "A test repo"
        assert repo.language == "Python"
        assert repo.stars == 10
        assert repo.forks == 2
        assert repo.open_issues == 1
        assert repo.license == "MIT"
        assert repo.has_readme is True
        assert repo.is_fork is False
        assert repo.archived is False
        assert repo.topics == ["python", "cli"]
        assert repo.default_branch == "main"
        assert repo.size_kb == 1024  # from API

    def test_without_api_data(self):
        local = _make_local(has_license_file=True)
        repo = RepoInfo.from_local_and_api(local, None)

        assert repo.name == "my-repo"
        assert repo.owner == "alice"
        assert repo.description == ""
        assert repo.language == ""
        assert repo.stars == 0
        assert repo.forks == 0
        assert repo.open_issues == 0
        assert repo.license == "local"
        assert repo.has_readme is True
        assert repo.is_fork is False
        assert repo.archived is False
        assert repo.topics == []
        assert repo.default_branch == "main"
        assert repo.size_kb == 500  # from local

    def test_license_from_api_preferred(self):
        local = _make_local(has_license_file=True)
        api = _make_api_data(license_spdx="Apache-2.0")
        repo = RepoInfo.from_local_and_api(local, api)
        assert repo.license == "Apache-2.0"

    def test_license_local_fallback(self):
        local = _make_local(has_license_file=True)
        repo = RepoInfo.from_local_and_api(local, None)
        assert repo.license == "local"

    def test_no_license_anywhere(self):
        local = _make_local(has_license_file=False)
        repo = RepoInfo.from_local_and_api(local, None)
        assert repo.license == ""

    def test_local_readme_overrides(self):
        local = _make_local(has_readme=False)
        api = _make_api_data()
        repo = RepoInfo.from_local_and_api(local, api)
        assert repo.has_readme is False

    def test_private_from_local(self):
        local = _make_local(private=True)
        api = _make_api_data()
        repo = RepoInfo.from_local_and_api(local, api)
        assert repo.private is True


def _make_profile(repos=None, events=None) -> ProfileData:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    if repos is None:
        repos = [
            RepoInfo(
                name="alpha", owner="u", url="https://github.com/u/alpha",
                private=False, description="Alpha repo", language="Python",
                stars=10, forks=3, open_issues=0, license="MIT",
                has_readme=True, pushed_at=now, is_fork=False, archived=False,
                topics=[], default_branch="main", size_kb=100,
            ),
            RepoInfo(
                name="beta", owner="u", url="https://github.com/u/beta",
                private=False, description="Beta repo", language="Python",
                stars=5, forks=1, open_issues=0, license="MIT",
                has_readme=True, pushed_at=now, is_fork=False, archived=False,
                topics=[], default_branch="main", size_kb=100,
            ),
            RepoInfo(
                name="gamma", owner="u", url="https://github.com/u/gamma",
                private=False, description="Gamma repo", language="JavaScript",
                stars=2, forks=0, open_issues=0, license="MIT",
                has_readme=True, pushed_at=now, is_fork=False, archived=False,
                topics=[], default_branch="main", size_kb=100,
            ),
        ]
    return ProfileData(
        account_slug="test",
        username="testuser",
        name="Test User",
        bio="I build things.",
        location="London",
        company="",
        blog="",
        twitter_username="",
        avatar_url="",
        html_url="https://github.com/testuser",
        public_repos_count=3,
        public_gists=0,
        followers=10,
        following=5,
        repos=repos,
        events=events or [],
    )


class TestProfileData:
    def test_total_stars(self):
        p = _make_profile()
        assert p.total_stars == 17  # 10 + 5 + 2

    def test_total_forks(self):
        p = _make_profile()
        assert p.total_forks == 4  # 3 + 1 + 0

    def test_original_repos_excludes_forks_and_archived(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repos = [
            RepoInfo(
                name="original", owner="u", url="u", private=False,
                description="", language="Python", stars=0, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
            RepoInfo(
                name="forked", owner="u", url="u", private=False,
                description="", language="Python", stars=0, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=True, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
            RepoInfo(
                name="archived", owner="u", url="u", private=False,
                description="", language="Python", stars=0, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=True, topics=[], default_branch="main",
                size_kb=0,
            ),
        ]
        p = _make_profile(repos=repos)
        originals = p.original_repos
        assert len(originals) == 1
        assert originals[0].name == "original"

    def test_language_breakdown(self):
        p = _make_profile()
        breakdown = p.language_breakdown
        assert len(breakdown) == 2
        # Python has 2 repos, JavaScript has 1
        assert breakdown[0][0] == "Python"
        assert breakdown[0][1] == 2
        assert breakdown[1][0] == "JavaScript"
        assert breakdown[1][1] == 1
        # Percentages should add to 100
        total_pct = sum(pct for _, _, pct in breakdown)
        assert abs(total_pct - 100.0) < 0.2

    def test_language_breakdown_empty(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repos = [
            RepoInfo(
                name="no-lang", owner="u", url="u", private=False,
                description="", language="", stars=0, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
        ]
        p = _make_profile(repos=repos)
        assert p.language_breakdown == []

    def test_pinned_repos_top_6(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repos = [
            RepoInfo(
                name=f"repo-{i}", owner="u", url=f"https://github.com/u/repo-{i}",
                private=False, description="", language="Python", stars=i,
                forks=0, open_issues=0, license="", has_readme=True,
                pushed_at=now, is_fork=False, archived=False, topics=[],
                default_branch="main", size_kb=0,
            )
            for i in range(10)
        ]
        p = _make_profile(repos=repos)
        pinned = p.pinned_repos
        assert len(pinned) == 6
        # Should be sorted by pin_score desc (higher stars = higher score)
        assert pinned[0].name == "repo-9"

    def test_pinned_repos_excludes_private(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        repos = [
            RepoInfo(
                name="public", owner="u", url="u", private=False,
                description="", language="", stars=10, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
            RepoInfo(
                name="private", owner="u", url="u", private=True,
                description="", language="", stars=100, forks=0,
                open_issues=0, license="", has_readme=True, pushed_at=now,
                is_fork=False, archived=False, topics=[], default_branch="main",
                size_kb=0,
            ),
        ]
        p = _make_profile(repos=repos)
        pinned = p.pinned_repos
        assert len(pinned) == 1
        assert pinned[0].name == "public"


class TestDeveloperActivity:
    def test_total_lines(self):
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=10, additions=200, deletions=50,
            prs_opened=3, prs_merged=2,
            issues_opened=1, issues_closed=0,
            review_comments=5,
        )
        assert dev.total_lines == 250

    def test_total_lines_zero(self):
        dev = DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=0, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        assert dev.total_lines == 0

    def test_net_lines_positive(self):
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=10, additions=200, deletions=50,
            prs_opened=3, prs_merged=2,
            issues_opened=1, issues_closed=0,
            review_comments=5,
        )
        assert dev.net_lines == 150

    def test_net_lines_negative(self):
        dev = DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=5, additions=10, deletions=100,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        assert dev.net_lines == -90

    def test_net_lines_zero(self):
        dev = DeveloperActivity(
            name="Carol", email="carol@example.com",
            commits=3, additions=50, deletions=50,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        assert dev.net_lines == 0

    def test_prs_open_is_independent_of_merged(self):
        """prs_merged may exceed prs_opened (older PRs landed this week), so
        "open" is its own count rather than opened - merged."""
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=5, additions=100, deletions=20,
            prs_opened=2, prs_merged=5,
            issues_opened=0, issues_closed=0,
            review_comments=0, prs_open=1,
        )
        assert dev.prs_open == 1
        assert not hasattr(dev, "prs_unmerged")

    def test_tags_created_default(self):
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=1, additions=10, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        assert dev.tags_created == 0

    def test_tags_created_set(self):
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=1, additions=10, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
            tags_created=3,
        )
        assert dev.tags_created == 3


class TestPRRecord:
    def test_basic_fields(self):
        pr = PRRecord(
            number=42, title="Fix auth bug", author="alice",
            state="merged", created_at="2025-06-01",
            merged_at="2025-06-02",
            url="https://github.com/org/repo/pull/42",
            repo_name="repo",
        )
        assert pr.number == 42
        assert pr.state == "merged"
        assert pr.merged_at == "2025-06-02"

    def test_open_pr(self):
        pr = PRRecord(
            number=10, title="WIP", author="bob",
            state="open", created_at="2025-06-05",
            merged_at=None,
            url="https://github.com/org/repo/pull/10",
            repo_name="repo",
        )
        assert pr.merged_at is None
        assert pr.state == "open"


class TestTagRecord:
    def test_basic_fields(self):
        tag = TagRecord(
            name="v1.0.0", tagger_name="Alice",
            tagger_email="alice@example.com",
            date="2025-06-15", repo_name="repo", owner="org",
        )
        assert tag.name == "v1.0.0"
        assert tag.owner == "org"


class TestActivityResult:
    def test_total_commits(self):
        commits = [
            CommitRecord(sha="a", author_name="A", author_email="a@a.com",
                         date="2025-01-01", subject="s", additions=1, deletions=0,
                         repo_name="r1"),
            CommitRecord(sha="b", author_name="B", author_email="b@b.com",
                         date="2025-01-02", subject="s", additions=2, deletions=1,
                         repo_name="r1"),
        ]
        repo = RepoActivity(
            name="r1", owner="o", commits=commits,
            prs_opened=2, prs_merged=1,
            issues_opened=3, issues_closed=1,
            review_comments=5, developers=[],
        )
        result = ActivityResult(
            account_slug="test", since="2025-01-01", until="2025-01-31",
            repos=[repo], developers=[],
        )
        assert result.total_commits == 2
        assert result.total_prs == 2
        assert result.total_issues == 3

    def test_total_commits_multiple_repos(self):
        c1 = CommitRecord(sha="a", author_name="A", author_email="a@a.com",
                          date="2025-01-01", subject="s", additions=1, deletions=0,
                          repo_name="r1")
        c2 = CommitRecord(sha="b", author_name="B", author_email="b@b.com",
                          date="2025-01-02", subject="s", additions=2, deletions=1,
                          repo_name="r2")
        repo1 = RepoActivity(
            name="r1", owner="o", commits=[c1],
            prs_opened=1, prs_merged=0,
            issues_opened=2, issues_closed=0,
            review_comments=0, developers=[],
        )
        repo2 = RepoActivity(
            name="r2", owner="o", commits=[c2],
            prs_opened=3, prs_merged=2,
            issues_opened=1, issues_closed=1,
            review_comments=3, developers=[],
        )
        result = ActivityResult(
            account_slug="test", since="2025-01-01", until="2025-01-31",
            repos=[repo1, repo2], developers=[],
        )
        assert result.total_commits == 2
        assert result.total_prs == 4
        assert result.total_issues == 3

    def test_total_tags(self):
        tag1 = TagRecord(
            name="v1.0", tagger_name="A", tagger_email="a@a.com",
            date="2025-01-10", repo_name="r1", owner="o",
        )
        tag2 = TagRecord(
            name="v2.0", tagger_name="A", tagger_email="a@a.com",
            date="2025-01-15", repo_name="r2", owner="o",
        )
        repo1 = RepoActivity(
            name="r1", owner="o", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[],
            tags=[tag1],
        )
        repo2 = RepoActivity(
            name="r2", owner="o", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[],
            tags=[tag2],
        )
        result = ActivityResult(
            account_slug="test", since="2025-01-01", until="2025-01-31",
            repos=[repo1, repo2], developers=[],
        )
        assert result.total_tags == 2

    def test_empty_result(self):
        result = ActivityResult(
            account_slug="test", since="2025-01-01", until="2025-01-31",
            repos=[], developers=[],
        )
        assert result.total_commits == 0
        assert result.total_prs == 0
        assert result.total_issues == 0
        assert result.total_tags == 0

    def test_total_branch_commits_uses_deduped_field(self):
        """total_branch_commits reads the deduped branch_commits field, not
        a raw sum over branches[*].commits (which may overlap when two
        branches share ancestry, e.g. a release branch built on top of
        several feature branches)."""
        shared = CommitRecord(
            sha="shared1", author_name="A", author_email="a@a.com",
            date="2025-01-01", subject="s", additions=1, deletions=0,
            repo_name="r1",
        )
        branch_a = BranchActivity(
            name="feature-a", repo_name="r1", commits=[shared], developers=[],
        )
        branch_b = BranchActivity(
            name="release", repo_name="r1", commits=[shared], developers=[],
        )
        repo = RepoActivity(
            name="r1", owner="o", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[],
            branches=[branch_a, branch_b],
            branch_commits=[shared],  # caller is responsible for dedup
        )
        result = ActivityResult(
            account_slug="test", since="2025-01-01", until="2025-01-31",
            repos=[repo], developers=[],
        )
        # branches[*].commits sums to 2 (overlap), but branch_commits is
        # already deduped to 1 — total_branch_commits must use the latter.
        assert sum(len(b.commits) for b in repo.branches) == 2
        assert result.total_branch_commits == 1


class TestReadmeData:
    def test_basic_fields(self):
        data = ReadmeData(
            name="my-repo",
            owner="testuser",
            url="https://github.com/testuser/my-repo",
            description="A test repo",
            language="Python",
            topics=["python", "cli"],
            license="MIT",
            stars=10,
            forks=2,
            default_branch="main",
            is_fork=False,
            tree=["src/", "tests/", "pyproject.toml"],
            package_info={"manager": "pip", "name": "my-repo"},
            has_tests=True,
            has_ci=True,
            has_contributing=False,
            has_changelog=False,
            entry_points=["cli.py"],
            detected_languages=["Python"],
            install_command="pip install .",
            test_command="pytest",
        )
        assert data.name == "my-repo"
        assert data.owner == "testuser"
        assert data.language == "Python"
        assert data.stars == 10
        assert data.forks == 2
        assert data.has_tests is True
        assert data.has_ci is True
        assert data.install_command == "pip install ."
        assert data.test_command == "pytest"
        assert data.topics == ["python", "cli"]
        assert data.tree == ["src/", "tests/", "pyproject.toml"]

    def test_empty_fields(self):
        data = ReadmeData(
            name="empty",
            owner="user",
            url="https://github.com/user/empty",
            description="",
            language="",
            topics=[],
            license="",
            stars=0,
            forks=0,
            default_branch="main",
            is_fork=False,
            tree=[],
            package_info={},
            has_tests=False,
            has_ci=False,
            has_contributing=False,
            has_changelog=False,
            entry_points=[],
            detected_languages=[],
            install_command="",
            test_command="",
        )
        assert data.description == ""
        assert data.topics == []
        assert data.stars == 0
        assert data.forks == 0
        assert data.install_command == ""


class TestDeveloperActivityMergedWith:
    def _dev(self, **kw):
        from auditer.models import DeveloperActivity

        base = dict(name="Tom", email="tom@x", commits=0, additions=0, deletions=0,
                    prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
                    review_comments=0)
        return DeveloperActivity(**{**base, **kw})

    def test_sums_every_count_field(self):
        """Generic over the dataclass, so a new count can't be dropped."""
        from dataclasses import fields

        from auditer.models import DeveloperActivity

        counts = [f.name for f in fields(DeveloperActivity)
                  if f.name not in DeveloperActivity._IDENTITY_FIELDS]
        a = self._dev(**{c: 1 for c in counts})
        b = self._dev(**{c: 2 for c in counts})
        merged = a.merged_with(b)
        assert all(getattr(merged, c) == 3 for c in counts)
        assert {"merge_commits", "prs_merged_by", "pr_comments", "prs_closed",
                "excluded_additions"} <= set(counts)

    def test_prefers_real_email_over_login_placeholder(self):
        api_only = self._dev(email="tomgould@github", github_login="tomgould")
        from_git = self._dev(email="jane@example.com", name="Jane Doe")
        merged = api_only.merged_with(from_git)
        assert merged.email == "jane@example.com"
        assert merged.name == "Jane Doe"
        assert merged.github_login == "tomgould"
        # A real email is never replaced
        assert from_git.merged_with(api_only).email == "jane@example.com"
