from __future__ import annotations

from unittest.mock import patch

import pytest

from auditer.models import (
    ActivityResult,
    BranchActivity,
    CommitRecord,
    DeveloperActivity,
    PRRecord,
    RepoActivity,
    ReviewRecord,
    TagRecord,
)
from auditer.reports.activity import (
    generate_activity_report,
    _aggregate_developers_from_repos,
    _collect_dev_prs,
    _date_range,
    _group_by_date,
    _group_by_week,
    _top_contributor,
)


@pytest.fixture
def sample_commits():
    return [
        CommitRecord(
            sha="abc1234", author_name="Alice", author_email="alice@example.com",
            date="2025-06-01", subject="Add feature X", additions=50, deletions=10,
            repo_name="my-repo",
        ),
        CommitRecord(
            sha="def5678", author_name="Alice", author_email="alice@example.com",
            date="2025-06-02", subject="Fix tests", additions=5, deletions=3,
            repo_name="my-repo",
        ),
        CommitRecord(
            sha="ghi9012", author_name="Bob", author_email="bob@example.com",
            date="2025-06-01", subject="Update docs", additions=20, deletions=0,
            repo_name="my-repo",
        ),
    ]


@pytest.fixture
def sample_developers():
    return [
        DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=10, additions=200, deletions=50,
            prs_opened=3, prs_merged=2,
            issues_opened=1, issues_closed=0,
            review_comments=5,
        ),
        DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=5, additions=100, deletions=20,
            prs_opened=2, prs_merged=1,
            issues_opened=3, issues_closed=2,
            review_comments=8,
        ),
    ]


@pytest.fixture
def sample_repo_activity(sample_commits, sample_developers):
    return RepoActivity(
        name="my-repo",
        owner="testuser",
        commits=sample_commits,
        prs_opened=5,
        prs_merged=3,
        issues_opened=4,
        issues_closed=2,
        review_comments=13,
        developers=sample_developers,
    )


@pytest.fixture
def sample_activity_result(sample_repo_activity, sample_developers):
    return ActivityResult(
        account_slug="test-user",
        since="2025-06-01",
        until="2025-06-30",
        repos=[sample_repo_activity],
        developers=sample_developers,
    )


class TestSummaryTable:
    def test_summary_table(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Summary" in report
        assert "| Active repos |" in report
        assert "| Active developers |" in report
        assert "| Total commits |" in report
        assert "| Lines added |" in report
        assert "| Lines deleted |" in report
        assert "| Net lines |" in report
        assert "| PRs merged |" in report
        assert "| PRs opened |" in report
        assert "| PRs still open at end of window |" in report
        assert "| PRs closed without merging |" in report
        assert "| Merge commits (not in commits or lines) |" in report
        assert "| PR conversation comments |" in report
        assert "| Issues opened/closed |" in report
        assert "| Review comments |" in report
        assert "| Tags created |" in report


class TestDeveloperLeaderboard:
    def test_developer_leaderboard(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Developer Leaderboard" in report
        assert "Alice" in report
        assert "Bob" in report
        # Alice has higher net lines (200-50=150 vs 100-20=80), so should appear first
        leaderboard = report.split("## Developer Leaderboard")[1].split("##")[0]
        alice_pos = leaderboard.index("Alice")
        bob_pos = leaderboard.index("Bob")
        assert alice_pos < bob_pos

    def test_leaderboard_sorted_by_net_lines(self):
        """Leaderboard is sorted by net lines (additions - deletions), not commits."""
        dev_few_commits_high_net = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=2, additions=500, deletions=50,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev_many_commits_low_net = DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=20, additions=100, deletions=90,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo = RepoActivity(
            name="r", owner="o", commits=[], prs_opened=0,
            prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0,
            developers=[dev_few_commits_high_net, dev_many_commits_low_net],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo],
            developers=[dev_few_commits_high_net, dev_many_commits_low_net],
        )
        report = generate_activity_report(result)
        leaderboard = report.split("## Developer Leaderboard")[1].split("##")[0]
        # Alice net=450, Bob net=10 → Alice first despite fewer commits
        alice_pos = leaderboard.index("Alice")
        bob_pos = leaderboard.index("Bob")
        assert alice_pos < bob_pos

    def test_leaderboard_has_net_lines_column(self, sample_activity_result):
        """Leaderboard includes Net Lines column."""
        report = generate_activity_report(sample_activity_result)
        assert "| Net Lines " in report


class TestDailyBreakdown:
    def test_daily_breakdown(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Daily Breakdown" in report
        assert "2025-06-01" in report
        assert "2025-06-02" in report

    def test_daily_breakdown_has_net_lines_column(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        daily = report.split("## Daily Breakdown")[1].split("##")[0]
        assert "| Net Lines |" in daily

    def test_daily_breakdown_includes_zero_days(self):
        """Days with no commits should still appear with 0."""
        commits = [
            CommitRecord(
                sha="a", author_name="Alice", author_email="a@x.com",
                date="2025-06-01", subject="x", additions=10, deletions=1,
                repo_name="r",
            ),
            CommitRecord(
                sha="b", author_name="Alice", author_email="a@x.com",
                date="2025-06-03", subject="y", additions=5, deletions=0,
                repo_name="r",
            ),
        ]
        dev = DeveloperActivity(
            name="Alice", email="a@x.com", commits=2, additions=15,
            deletions=1, prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        result = ActivityResult(
            account_slug="t", since="2025-06-01", until="2025-06-04",
            repos=[RepoActivity(
                name="r", owner="o", commits=commits, prs_opened=0,
                prs_merged=0, issues_opened=0, issues_closed=0,
                review_comments=0, developers=[dev],
            )],
            developers=[dev],
        )
        report = generate_activity_report(result)
        # All 4 days should appear, including the 2 without commits
        assert "2025-06-01" in report
        assert "2025-06-02" in report  # zero day
        assert "2025-06-03" in report
        assert "2025-06-04" in report  # zero day
        # The zero days should show 0 commits
        lines = report.split("\n")
        june_02_lines = [l for l in lines if "2025-06-02" in l]
        assert len(june_02_lines) == 1
        assert "| 0 |" in june_02_lines[0]


class TestWeeklyBreakdown:
    def test_weekly_breakdown(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Weekly Breakdown" in report
        # Weeks shown as date ranges, e.g. "Jun 02 – Jun 08"
        assert "–" in report

    def test_weekly_rows_are_chronological_across_months(self):
        """Labels must not be sorted as strings: "Oct 05" < "Sep 07"."""
        dev = _mk_dev("A", "a@x.com", commits=0)
        result = ActivityResult(
            account_slug="t", since="2025-08-01", until="2025-10-16",
            repos=[_mk_repo("r", [dev])], developers=[dev],
        )
        report = generate_activity_report(result)
        body = report.split("## Weekly Breakdown")[1].split("## ")[0]
        rows = [l.split("|")[1].strip() for l in body.splitlines() if l.startswith("| ") and not l.startswith("| Week")]
        # 1 Aug 2025 is a Friday: first row is the clipped Fri–Sun week
        assert rows[0] == "Aug 01 – Aug 03 (partial)"
        assert rows[-1] == "Oct 13 – Oct 16 (partial)"
        sep = next(i for i, r in enumerate(rows) if r.startswith("Sep 01"))
        oct_ = next(i for i, r in enumerate(rows) if r.startswith("Oct 06"))
        assert sep < oct_

    def test_weekly_breakdown_has_net_lines_column(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        weekly = report.split("## Weekly Breakdown")[1].split("##")[0]
        assert "| Net Lines |" in weekly


class TestPerRepoTable:
    def test_per_repo_table(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Per-Repo Activity" in report
        assert "my-repo" in report
        assert "Top Contributor" in report

    def test_per_repo_table_has_net_lines_column(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        per_repo = report.split("## Per-Repo Activity")[1].split("##")[0]
        assert "| Net Lines " in per_repo

    def test_per_repo_table_sorted_by_net_lines(self):
        """Per-Repo Activity table is sorted by net lines descending."""
        commits_high_net = [
            CommitRecord(
                sha="a", author_name="A", author_email="a@x.com",
                date="2025-06-01", subject="x", additions=500, deletions=10,
                repo_name="high-net",
            ),
        ]
        commits_low_net = [
            CommitRecord(
                sha="b", author_name="A", author_email="a@x.com",
                date="2025-06-01", subject="y", additions=100, deletions=90,
                repo_name="low-net",
            ),
        ]
        dev = DeveloperActivity(
            name="A", email="a@x.com", commits=1, additions=0, deletions=0,
            prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo_high = RepoActivity(
            name="high-net", owner="o", commits=commits_high_net,
            prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0, developers=[dev],
        )
        repo_low = RepoActivity(
            name="low-net", owner="o", commits=commits_low_net,
            prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0, developers=[dev],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo_low, repo_high], developers=[dev],
        )
        report = generate_activity_report(result)
        per_repo = report.split("## Per-Repo Activity")[1].split("##")[0]
        assert per_repo.index("high-net") < per_repo.index("low-net")


class TestPerRepoDetail:
    def test_per_repo_detail(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Per-Repo Detail" in report
        assert "### my-repo" in report
        # Per-repo detail is now a table with developer stats
        assert "| Developer | Commits |" in report
        assert "| Alice |" in report
        assert "| Bob |" in report

    def test_per_repo_detail_has_net_lines_column(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        detail = report.split("## Per-Repo Detail")[1].split("## Developer Detail")[0]
        assert "| Net Lines " in detail

    def test_per_repo_detail_devs_sorted_by_net_lines(self):
        """Within per-repo detail, developers are sorted by net lines."""
        dev_high_net = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=2, additions=300, deletions=10,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev_low_net = DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=20, additions=50, deletions=40,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        commits = [
            CommitRecord(
                sha="a", author_name="Alice", author_email="alice@example.com",
                date="2025-06-01", subject="x", additions=300, deletions=10,
                repo_name="r",
            ),
        ]
        repo = RepoActivity(
            name="r", owner="o", commits=commits, prs_opened=0,
            prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0,
            developers=[dev_low_net, dev_high_net],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev_high_net, dev_low_net],
        )
        report = generate_activity_report(result)
        detail = report.split("## Per-Repo Detail")[1].split("## Developer Detail")[0]
        # Alice net=290, Bob net=10 → Alice first despite fewer commits
        assert detail.index("Alice") < detail.index("Bob")


class TestUserFilterReport:
    def test_user_filter_report(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result, user_filter="Alice")
        assert "Alice" in report
        # Developer leaderboard should only have Alice
        assert "## Developer Leaderboard" in report


class TestRepoFilterReport:
    def test_repo_filter_report(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result, repo_filter="my-repo")
        assert "my-repo" in report


class TestEmptyActivity:
    def test_empty_activity(self):
        result = ActivityResult(
            account_slug="test",
            since="2025-06-01",
            until="2025-06-30",
            repos=[],
            developers=[],
        )
        report = generate_activity_report(result)
        assert "Developer Activity Report" in report
        assert "2025-06-01" in report
        assert "| Total commits | 0 |" in report
        # Daily breakdown should still appear with zero rows
        assert "## Daily Breakdown" in report
        assert "## Weekly Breakdown" in report


class TestDateRangeInHeader:
    def test_date_range_in_header(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "2025-06-01" in report
        assert "2025-06-30" in report


class TestGroupByDate:
    def test_group_by_date(self, sample_commits):
        groups = _group_by_date(sample_commits)
        assert "2025-06-01" in groups
        assert "2025-06-02" in groups
        assert len(groups["2025-06-01"]) == 2  # Alice + Bob
        assert len(groups["2025-06-02"]) == 1  # Alice


class TestGroupByWeek:
    def test_group_by_week(self, sample_commits):
        # sample_commits are on Jun 1 (Sunday) and Jun 2 (Monday).
        # since=Jun 1 is a Sunday → the week of May 26 is shown clipped to
        # Jun 1 as a partial week, so the Jun 1 commits are not dropped.
        groups = _group_by_week(sample_commits, "2025-06-01", "2025-06-07")
        labels = list(groups.keys())
        assert labels == ["Jun 01 – Jun 01 (partial)", "Jun 02 – Jun 07 (partial)"]
        assert len(groups[labels[0]]) == 2
        assert len(groups[labels[1]]) == 1
        # Every commit in range lands in exactly one row
        assert sum(len(v) for v in groups.values()) == len(sample_commits)

    def test_group_by_week_zero_weeks(self):
        """Weeks with no commits still appear."""
        commits = [
            CommitRecord(
                sha="a", author_name="A", author_email="a@x.com",
                date="2025-06-02", subject="x", additions=1, deletions=0,
                repo_name="r",
            ),
        ]
        # Range spans 3 weeks; Jun 1, 2025 is a Sunday
        # Since Jun 1 is not a Monday, first week starts Jun 2 (Monday)
        groups = _group_by_week(commits, "2025-06-01", "2025-06-15")
        # Should have weeks covering the range
        assert len(groups) >= 2
        # At least one week should have 0 commits
        empty_weeks = [k for k, v in groups.items() if len(v) == 0]
        assert len(empty_weeks) >= 1

    def test_group_by_week_partial_week_before_first_monday(self):
        """A range starting mid-week shows that week clipped to the range and
        labelled partial, so commits before the first Monday aren't dropped."""
        # Feb 8, 2026 is a Sunday. The Monday of that week is Feb 2.
        commits = [
            CommitRecord(
                sha="a", author_name="A", author_email="a@x.com",
                date="2026-02-08", subject="x", additions=1, deletions=0,
                repo_name="r",
            ),
        ]
        groups = _group_by_week(commits, "2026-02-08", "2026-02-14")
        labels = list(groups.keys())
        assert labels == ["Feb 08 – Feb 08 (partial)", "Feb 09 – Feb 14 (partial)"]
        assert len(groups[labels[0]]) == 1
        assert all("Feb 02" not in label for label in labels)

    def test_group_by_week_full_weeks_not_marked_partial(self):
        # Feb 2, 2026 is a Monday; Feb 15 a Sunday → two full weeks
        groups = _group_by_week([], "2026-02-02", "2026-02-15")
        assert list(groups.keys()) == ["Feb 02 – Feb 08", "Feb 09 – Feb 15"]

    def test_group_by_week_since_on_monday(self):
        """When since falls on a Monday, that week is included."""
        # Feb 9, 2026 is a Monday
        commits = [
            CommitRecord(
                sha="a", author_name="A", author_email="a@x.com",
                date="2026-02-09", subject="x", additions=1, deletions=0,
                repo_name="r",
            ),
        ]
        groups = _group_by_week(commits, "2026-02-09", "2026-02-15")
        labels = list(groups.keys())
        assert any("Feb 09" in label for label in labels)

    def test_group_by_week_single_week_monday_to_sunday(self):
        """A single full week (Monday to Sunday) produces exactly one week."""
        # Feb 9, 2026 is a Monday; Feb 15 is a Sunday
        commits = [
            CommitRecord(
                sha="a", author_name="A", author_email="a@x.com",
                date="2026-02-09", subject="start", additions=1, deletions=0,
                repo_name="r",
            ),
            CommitRecord(
                sha="b", author_name="A", author_email="a@x.com",
                date="2026-02-13", subject="mid", additions=2, deletions=0,
                repo_name="r",
            ),
        ]
        groups = _group_by_week(commits, "2026-02-09", "2026-02-15")
        assert len(groups) == 1
        label = list(groups.keys())[0]
        assert "Feb 09" in label
        assert "Feb 15" in label
        assert len(groups[label]) == 2


class TestDateRange:
    def test_date_range(self):
        dates = _date_range("2025-06-01", "2025-06-04")
        assert dates == ["2025-06-01", "2025-06-02", "2025-06-03", "2025-06-04"]

    def test_date_range_single_day(self):
        dates = _date_range("2025-06-01", "2025-06-01")
        assert dates == ["2025-06-01"]

    def test_date_range_invalid(self):
        dates = _date_range("bad", "dates")
        assert dates == []


class TestTopContributor:
    def test_top_contributor(self, sample_developers):
        top = _top_contributor(sample_developers)
        # No github_login set → display name is human name
        assert top == "Alice"

    def test_top_contributor_empty(self):
        assert _top_contributor([]) == "-"


class TestAggregateDevelopersFromRepos:
    def test_aggregate_across_repos(self):
        dev_alice = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=5, additions=100, deletions=10,
            prs_opened=1, prs_merged=1,
            issues_opened=0, issues_closed=0,
            review_comments=2,
        )
        dev_bob = DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=3, additions=50, deletions=5,
            prs_opened=0, prs_merged=0,
            issues_opened=1, issues_closed=0,
            review_comments=0,
        )
        dev_alice2 = DeveloperActivity(
            name="Alice Smith", email="alice@example.com",
            commits=2, additions=30, deletions=5,
            prs_opened=1, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=1,
        )
        repo1 = RepoActivity(
            name="repo1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev_alice, dev_bob],
        )
        repo2 = RepoActivity(
            name="repo2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev_alice2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        # Alice should be merged (same email)
        alice = [d for d in result if "alice" in d.email.lower()][0]
        assert alice.commits == 7  # 5 + 2
        assert alice.additions == 130  # 100 + 30
        assert alice.name == "Alice Smith"  # longer name preferred

        bob = [d for d in result if "bob" in d.email.lower()][0]
        assert bob.commits == 3

    def test_merge_by_name_different_emails(self):
        """Same name, different emails across repos should merge."""
        dev1 = DeveloperActivity(
            name="Brian Alder", email="brian@laptop.local",
            commits=5, additions=100, deletions=10,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev2 = DeveloperActivity(
            name="Brian Alder", email="brian.alder@work.com",
            commits=3, additions=50, deletions=5,
            prs_opened=2, prs_merged=1,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo1 = RepoActivity(
            name="repo1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev1],
        )
        repo2 = RepoActivity(
            name="repo2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        brians = [d for d in result if "brian" in d.name.lower()]
        assert len(brians) == 1
        assert brians[0].commits == 8
        assert brians[0].prs_opened == 2

    def test_aggregate_by_login_across_repos(self):
        """Same github_login merges across repos even with different emails."""
        dev1 = DeveloperActivity(
            name="Alice Smith", email="alice@work.com",
            commits=5, additions=100, deletions=10,
            prs_opened=1, prs_merged=1,
            issues_opened=0, issues_closed=0,
            review_comments=0,
            github_login="alicesmith",
        )
        dev2 = DeveloperActivity(
            name="Alice Smith", email="alice@personal.com",
            commits=3, additions=50, deletions=5,
            prs_opened=2, prs_merged=1,
            issues_opened=0, issues_closed=0,
            review_comments=0,
            github_login="alicesmith",
        )
        repo1 = RepoActivity(
            name="repo1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev1],
        )
        repo2 = RepoActivity(
            name="repo2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        alices = [d for d in result if d.github_login == "alicesmith"]
        assert len(alices) == 1
        assert alices[0].commits == 8
        assert alices[0].prs_opened == 3

    def test_merge_by_name_skips_short_names(self):
        """Names <=3 chars should not merge (too generic)."""
        dev1 = DeveloperActivity(
            name="bot", email="bot@ci.com",
            commits=10, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev2 = DeveloperActivity(
            name="bot", email="bot@other.com",
            commits=5, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo1 = RepoActivity(
            name="r1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev1],
        )
        repo2 = RepoActivity(
            name="r2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        bots = [d for d in result if "bot" in d.name.lower()]
        assert len(bots) == 2  # Not merged — name too short

    def test_merge_by_name_skips_two_char_names(self):
        """Names with 2 chars should not merge."""
        dev1 = DeveloperActivity(
            name="Al", email="al@one.com",
            commits=3, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev2 = DeveloperActivity(
            name="Al", email="al@two.com",
            commits=2, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo1 = RepoActivity(
            name="r1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev1],
        )
        repo2 = RepoActivity(
            name="r2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        als = [d for d in result if d.name.lower() == "al"]
        assert len(als) == 2  # Not merged — name too short

    def test_merge_by_name_merges_four_char_names(self):
        """Names with 4 chars should merge (above the <=3 threshold)."""
        dev1 = DeveloperActivity(
            name="John", email="john@one.com",
            commits=5, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev2 = DeveloperActivity(
            name="John", email="john@two.com",
            commits=3, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo1 = RepoActivity(
            name="r1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev1],
        )
        repo2 = RepoActivity(
            name="r2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        johns = [d for d in result if d.name.lower() == "john"]
        assert len(johns) == 1  # Merged — 4 chars, above threshold
        assert johns[0].commits == 8

    def test_includes_branch_only_developer(self):
        """A developer whose only activity is on an unmerged feature branch
        (branch_developers) must still appear in the account-wide rollup,
        not just the per-repo Branch Activity table."""
        branch_only_dev = DeveloperActivity(
            name="Steven Jones", email="steven@example.com",
            commits=3, additions=90, deletions=10,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo = RepoActivity(
            name="repo1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[],
            branch_developers=[branch_only_dev],
        )
        result = _aggregate_developers_from_repos([repo])
        assert len(result) == 1
        assert result[0].name == "Steven Jones"
        assert result[0].commits == 3
        assert result[0].additions == 90

    def test_merges_default_branch_and_branch_developer_by_login(self):
        """The same developer split across repo.developers (e.g. PR opened
        in-range) and repo.branch_developers (commits on the still-open PR
        branch) must merge into one entry, summing both."""
        default_dev = DeveloperActivity(
            name="Steven Jones", email="steven@example.com",
            commits=0, additions=0, deletions=0,
            prs_opened=1, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0, github_login="steven-jones",
        )
        branch_dev = DeveloperActivity(
            name="Steven Jones", email="steven@example.com",
            commits=3, additions=90, deletions=10,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0, github_login="steven-jones",
        )
        repo = RepoActivity(
            name="repo1", owner="o", commits=[], prs_opened=1, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[default_dev],
            branch_developers=[branch_dev],
        )
        result = _aggregate_developers_from_repos([repo])
        assert len(result) == 1
        assert result[0].commits == 3
        assert result[0].additions == 90
        assert result[0].prs_opened == 1

    def test_merge_by_name_skips_multi_word_short_names(self):
        """Names are normalised to alphanumerics before merging, so 'A B'
        ('ab', 2 chars) is too generic to merge on, space or no space."""
        dev1 = DeveloperActivity(
            name="A B", email="ab@one.com",
            commits=4, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        dev2 = DeveloperActivity(
            name="A B", email="ab@two.com",
            commits=2, additions=0, deletions=0,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0,
        )
        repo1 = RepoActivity(
            name="r1", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev1],
        )
        repo2 = RepoActivity(
            name="r2", owner="o", commits=[], prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev2],
        )
        result = _aggregate_developers_from_repos([repo1, repo2])
        abs_ = [d for d in result if d.name.lower() == "a b"]
        assert len(abs_) == 2
        assert sorted(d.commits for d in abs_) == [2, 4]


def _mk_dev(name, email, commits=1, login="", additions=10, deletions=0, **kw):
    fields = dict(
        prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
        review_comments=0,
    )
    fields.update(kw)
    return DeveloperActivity(
        name=name, email=email, commits=commits, additions=additions,
        deletions=deletions, github_login=login, **fields,
    )


def _mk_repo(name, devs, commits=(), **kw):
    return RepoActivity(
        name=name, owner="o", commits=list(commits), prs_opened=0, prs_merged=0,
        issues_opened=0, issues_closed=0, review_comments=0, developers=devs, **kw,
    )


class TestMergeByNormalisedName:
    def test_hyphen_and_space_variants_merge(self):
        """'Anton Zorin' and 'Anton-Zorin' are one person."""
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [_mk_dev("Anton Zorin", "az@work.com", commits=3)]),
            _mk_repo("b", [_mk_dev("Anton-Zorin", "az@home.com", commits=2)]),
        ])
        assert len(devs) == 1
        assert devs[0].commits == 5
        assert devs[0].name == "Anton Zorin"

    def test_name_matching_another_entry_login_merges(self):
        """A gmail-keyed 'Fiona Vale' merges into the 'fionavale' login."""
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [_mk_dev("Fiona Vale", "fv@personal.example", commits=4)]),
            _mk_repo("b", [_mk_dev("fionavale", "fionavale@github",
                             commits=1, login="fionavale", prs_opened=2)]),
        ])
        assert len(devs) == 1
        assert devs[0].commits == 5
        assert devs[0].prs_opened == 2
        assert devs[0].github_login == "fionavale"

    def test_short_normalised_names_not_merged(self):
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [_mk_dev("A.B.", "ab1@x.com")]),
            _mk_repo("b", [_mk_dev("A-B", "ab2@x.com")]),
        ])
        assert len(devs) == 2

    def test_different_people_stay_separate(self):
        """'madler' vs 'Mohan Adler' is not safely mergeable."""
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [_mk_dev("Mohan Adler", "m@x.com")]),
            _mk_repo("b", [_mk_dev("madler", "madler@github", login="madler")]),
        ])
        assert len(devs) == 2


class TestBotsInReport:
    def _result(self):
        alice = _mk_dev("Alice", "alice@x.com", commits=5, login="alice")
        renovate = _mk_dev("renovate[bot]", "r@github", commits=85, login="renovate[bot]")
        dependabot = _mk_dev("dependabot[bot]", "d@github", commits=3)  # no login, [bot] name
        commits = [
            CommitRecord(sha=f"s{i}", author_name="Alice", author_email="alice@x.com",
                         date="2025-06-02", subject="x", additions=1, deletions=0,
                         repo_name="r")
            for i in range(5)
        ] + [
            CommitRecord(sha=f"b{i}", author_name="renovate[bot]", author_email="r@github",
                         date="2025-06-02", subject="bump", additions=1, deletions=0,
                         repo_name="r")
            for i in range(85)
        ] + [
            CommitRecord(sha=f"d{i}", author_name="dependabot[bot]", author_email="d@github",
                         date="2025-06-03", subject="bump", additions=1, deletions=0,
                         repo_name="r")
            for i in range(3)
        ]
        repo = _mk_repo("r", [alice, renovate, dependabot], commits=commits)
        return ActivityResult(
            account_slug="t", since="2025-06-01", until="2025-06-07",
            repos=[repo], developers=[alice, renovate, dependabot],
        )

    def test_bots_in_automation_table_not_leaderboard(self):
        report = generate_activity_report(self._result())
        leaderboard = report.split("## Developer Leaderboard")[1].split("## Automation")[0]
        automation = report.split("## Automation")[1].split("## Daily Breakdown")[0]
        assert "| alice |" in leaderboard
        assert "renovate[bot]" not in leaderboard
        assert "| renovate[bot] | 85 |" in automation
        assert "| dependabot[bot] | 3 |" in automation
        assert "| alice |" not in automation

    def test_contributors_and_detail_exclude_bots(self):
        report = generate_activity_report(self._result())
        contributors = report.split("## Contributors")[1].split("## Summary")[0]
        assert "renovate[bot]" not in contributors
        assert "### renovate[bot]" not in report
        assert "### alice" in report

    def test_summary_totals_keep_bots_and_show_bot_share(self):
        report = generate_activity_report(self._result())
        assert "| Total commits | 93 |" in report
        assert "| &nbsp;&nbsp;of which by bots | 88 |" in report
        assert "| Active developers | 1 |" in report

    def test_top_contributor_prefers_humans(self):
        report = generate_activity_report(self._result())
        per_repo = report.split("## Per-Repo Activity")[1].split("## Per-Repo Detail")[0]
        assert "| alice |" in per_repo

    def test_top_contributor_falls_back_to_bot_when_alone(self):
        bot = _mk_dev("renovate[bot]", "r@github", commits=2, login="renovate[bot]")
        assert _top_contributor([bot]) == "renovate[bot]"

    def test_copilot_agent_is_a_bot(self):
        """GitHub's Copilot coding agent has no [bot] suffix on its login."""
        from auditer.reports.activity import _is_bot

        assert _is_bot(_mk_dev("Copilot", "Copilot@github", commits=0, login="Copilot"))
        assert not _is_bot(_mk_dev("Copilot Fan", "fan@x.com", commits=1, login="copilotfan"))

    def test_no_bots_no_automation_section(self, sample_activity_result):
        report = generate_activity_report(sample_activity_result)
        assert "## Automation" not in report
        assert "of which by bots" not in report


class TestDataQualityBanner:
    def test_banner_lists_failed_repos_and_calls(self, sample_developers, sample_commits):
        bad = _mk_repo("broken", sample_developers, commits=sample_commits,
                    api_errors=["list_pulls: Forbidden (403)", "list_repo_issues: Forbidden (403)"])
        good = _mk_repo("fine", sample_developers, commits=sample_commits)
        result = ActivityResult(
            account_slug="t", since="2025-06-01", until="2025-06-07",
            repos=[good, bad], developers=sample_developers,
        )
        report = generate_activity_report(result, include_branches=True)
        header = report.split("## ")[0]
        assert "**Mode:** Default branch + feature branches" in header
        assert "**⚠ Data quality:** API data incomplete for 1 repo(s)." in header
        assert "- **broken**: list_pulls: Forbidden (403); list_repo_issues: Forbidden (403)" in header
        assert "**fine**" not in header
        # Banner sits right under the Mode line
        assert header.index("**Mode:**") < header.index("Data quality")

    def test_no_banner_when_clean(self, sample_activity_result):
        assert "Data quality" not in generate_activity_report(sample_activity_result)


class TestBreakdownsSumToTotal:
    def test_daily_and_weekly_sum_to_total_commits(self):
        """Every commit in range appears in exactly one daily and one weekly row."""
        dates = ["2025-06-01", "2025-06-01", "2025-06-02", "2025-06-08", "2025-06-10"]
        commits = [
            CommitRecord(sha=f"s{i}", author_name="A", author_email="a@x.com",
                         date=d, subject="x", additions=1, deletions=0, repo_name="r")
            for i, d in enumerate(dates)
        ]
        dev = _mk_dev("A", "a@x.com", commits=len(commits))
        result = ActivityResult(
            account_slug="t", since="2025-06-01", until="2025-06-10",
            repos=[_mk_repo("r", [dev], commits=commits)], developers=[dev],
        )
        report = generate_activity_report(result)
        assert "| Total commits | 5 |" in report

        def _rows(section, next_section):
            body = report.split(section)[1].split(next_section)[0]
            return [l for l in body.splitlines() if l.startswith("| ") and not l.startswith("| Date") and not l.startswith("| Week")]

        daily = _rows("## Daily Breakdown", "## Weekly Breakdown")
        weekly = _rows("## Weekly Breakdown", "## Per-Repo Activity")
        assert sum(int(r.split("|")[2]) for r in daily) == 5
        assert sum(int(r.split("|")[2]) for r in weekly) == 5
        assert "| Jun 01 – Jun 01 (partial) | 2 |" in report
        assert "| Jun 02 – Jun 08 | 2 |" in report
        assert "| Jun 09 – Jun 10 (partial) | 1 |" in report


class TestDeveloperMerging:
    def test_merge_by_noreply_email(self):
        """Commits with noreply email merge with PRs by same login."""
        from auditer.commands.data.activity import _aggregate_developers

        commits = [
            CommitRecord(
                sha="a", author_name="JayStone",
                author_email="12345+JayStone@users.noreply.github.com",
                date="2025-06-01", subject="x", additions=10, deletions=1,
                repo_name="r",
            ),
        ]
        prs = [
            {
                "user": {"login": "JayStone"},
                "created_at": "2025-06-02T00:00:00Z",
                "merged_at": None,
                "state": "open",
            },
        ]
        result = _aggregate_developers(
            commits, prs=prs, since="2025-06-01", until="2025-06-30",
        )
        # Should be 1 developer, not 2
        assert len(result) == 1
        assert result[0].commits == 1
        assert result[0].prs_opened == 1

    def test_merge_by_email_to_login_mapping(self):
        """Commits merge with PRs when email_to_login provides the link."""
        from auditer.commands.data.activity import _aggregate_developers

        commits = [
            CommitRecord(
                sha="a", author_name="Gavin Rowe",
                author_email="gavin@example.org",
                date="2025-06-01", subject="x", additions=10, deletions=1,
                repo_name="r",
            ),
        ]
        prs = [
            {
                "user": {"login": "gavinrowe"},
                "created_at": "2025-06-02T00:00:00Z",
                "merged_at": "2025-06-03T00:00:00Z",
                "state": "closed",
            },
        ]
        email_to_login = {"gavin@example.org": "gavinrowe"}
        result = _aggregate_developers(
            commits, prs=prs, since="2025-06-01", until="2025-06-30",
            email_to_login=email_to_login,
        )
        assert len(result) == 1
        assert result[0].commits == 1
        assert result[0].prs_opened == 1
        assert result[0].prs_merged == 1
        assert result[0].github_login == "gavinrowe"

    def test_no_merge_without_mapping(self):
        """Without email_to_login, non-noreply emails stay separate from PRs."""
        from auditer.commands.data.activity import _aggregate_developers

        commits = [
            CommitRecord(
                sha="a", author_name="Alice",
                author_email="alice@example.com",
                date="2025-06-01", subject="x", additions=10, deletions=1,
                repo_name="r",
            ),
        ]
        prs = [
            {
                "user": {"login": "bobsmith"},
                "created_at": "2025-06-02T00:00:00Z",
                "merged_at": None,
                "state": "open",
            },
        ]
        result = _aggregate_developers(
            commits, prs=prs, since="2025-06-01", until="2025-06-30",
        )
        # No mapping — should remain 2 separate developers
        assert len(result) == 2

    def test_merge_with_mapping_same_person_different_email(self):
        """email_to_login resolves different emails to same login."""
        from auditer.commands.data.activity import _aggregate_developers

        commits = [
            CommitRecord(
                sha="a", author_name="Steven Jones",
                author_email="steven@example.com",
                date="2025-06-01", subject="x", additions=10, deletions=1,
                repo_name="r",
            ),
        ]
        prs = [
            {
                "user": {"login": "steven-js"},
                "created_at": "2025-06-02T00:00:00Z",
                "merged_at": None,
                "state": "open",
            },
        ]
        email_to_login = {"steven@example.com": "steven-js"}
        result = _aggregate_developers(
            commits, prs=prs, since="2025-06-01", until="2025-06-30",
            email_to_login=email_to_login,
        )
        assert len(result) == 1
        assert result[0].commits == 1
        assert result[0].prs_opened == 1
        assert result[0].github_login == "steven-js"
        assert result[0].name == "Steven Jones"


class TestMatchesUser:
    def test_matches_by_name(self):
        from auditer.commands.data.activity import _matches_user
        assert _matches_user("Alice Smith", "alice@example.com", "Alice") is True

    def test_matches_by_email(self):
        from auditer.commands.data.activity import _matches_user
        assert _matches_user("Alice", "alice@example.com", "alice@example") is True

    def test_matches_by_github_login(self):
        from auditer.commands.data.activity import _matches_user
        assert _matches_user("Alice", "alice@example.com", "alicegit", "alicegit") is True

    def test_no_match(self):
        from auditer.commands.data.activity import _matches_user
        assert not _matches_user("Alice", "alice@example.com", "bob")


class TestFilterRepoByUser:
    def test_filters_developers_by_github_login(self):
        from auditer.commands.data.activity import _filter_repo_by_user
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=5, additions=100, deletions=10,
            prs_opened=1, prs_merged=1,
            issues_opened=0, issues_closed=0,
            review_comments=0, github_login="alicegit",
        )
        commit = CommitRecord(
            sha="a", author_name="Alice", author_email="alice@example.com",
            date="2025-06-01", subject="x", additions=10, deletions=1,
            repo_name="r",
        )
        ra = RepoActivity(
            name="r", owner="o", commits=[commit],
            prs_opened=1, prs_merged=1, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
        )
        # Filter by github_login
        result = _filter_repo_by_user(ra, "alicegit")
        assert result is not None
        assert len(result.developers) == 1

    def test_commits_matched_via_developer_email_when_name_differs(self):
        """--user <github_login> must return commits even when git author name
        doesn't contain the login string (the common real-world case)."""
        from auditer.commands.data.activity import _filter_repo_by_user
        dev = DeveloperActivity(
            name="Dana Gold", email="dan@example.com",
            commits=3, additions=50, deletions=5,
            prs_opened=0, prs_merged=0,
            issues_opened=0, issues_closed=0,
            review_comments=0, github_login="dgold",
        )
        commit = CommitRecord(
            sha="b", author_name="Dana Gold", author_email="dan@example.com",
            date="2026-05-01", subject="fix bug", additions=50, deletions=5,
            repo_name="myrepo",
        )
        ra = RepoActivity(
            name="myrepo", owner="org", commits=[commit],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
        )
        result = _filter_repo_by_user(ra, "dgold")
        assert result is not None
        assert len(result.commits) == 1
        assert len(result.developers) == 1

class TestGetCommitLog:
    @patch("auditer.git_ops._run_git")
    def test_get_commit_log(self, mock_run):
        from auditer.git_ops import get_commit_log

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "COMMIT\x1eabc1234\x1eAlice\x1ealice@example.com\x1e2025-06-01T10:00:00+00:00\x1eAdd feature\n"
            "\n"
            " 3 files changed, 45 insertions(+), 12 deletions(-)\n"
            "COMMIT\x1edef5678\x1eBob\x1ebob@example.com\x1e2025-06-02T11:00:00+00:00\x1eFix bug\n"
            "\n"
            " 1 file changed, 5 insertions(+), 3 deletions(-)\n"
        )

        from pathlib import Path
        result = get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")

        assert len(result) == 2
        assert result[0]["sha"] == "abc1234"
        assert result[0]["author_name"] == "Alice"
        assert result[0]["author_email"] == "alice@example.com"
        assert result[0]["date"] == "2025-06-01"
        assert result[0]["subject"] == "Add feature"
        assert result[0]["additions"] == 45
        assert result[0]["deletions"] == 12

        assert result[1]["sha"] == "def5678"
        assert result[1]["additions"] == 5
        assert result[1]["deletions"] == 3

    @patch("auditer.git_ops._run_git")
    def test_get_commit_log_passes_no_merges(self, mock_run):
        """--no-merges must be passed to skip merge commits."""
        from auditer.git_ops import get_commit_log
        from pathlib import Path

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""

        get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        args = mock_run.call_args[0][0]
        assert "--no-merges" in args
        assert "--all" in args

    @patch("auditer.git_ops._run_git")
    def test_get_commit_log_excludes_lock_files_and_vendor(self, mock_run):
        """Pathspec exclusions for lock files, vendor dirs, etc. are passed."""
        from auditer.git_ops import EXCLUDED_PATHSPECS, exclusion_pathspecs, get_commit_log
        from pathlib import Path

        mock_run.return_value.returncode = 0
        # One commit, so the second (pathspec-limited) pass runs
        mock_run.return_value.stdout = (
            "COMMIT\x1eabc\x1eA\x1ea@x\x1e2025-06-02T10:00:00+00:00\x1es\n"
        )

        get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        args = mock_run.call_args[0][0]
        # Should contain -- . separator followed by exclusions
        assert "--" in args
        assert "." in args
        # Every exclusion should appear as a pathspec argument
        for pattern in EXCLUDED_PATHSPECS:
            assert f":(exclude){pattern}" in args
        for arg in exclusion_pathspecs():
            assert arg in args

    @patch("auditer.git_ops._run_git")
    def test_get_commit_log_empty(self, mock_run):
        from auditer.git_ops import get_commit_log
        from pathlib import Path

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""

        result = get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        assert result == []

    @patch("auditer.git_ops._run_git")
    def test_get_commit_log_no_shortstat_line(self, mock_run):
        """A commit without a shortstat line gets 0 additions/deletions."""
        from auditer.git_ops import get_commit_log
        from pathlib import Path

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "COMMIT\x1eabc1234\x1eAlice\x1ealice@example.com\x1e2025-06-01T10:00:00+00:00\x1eEmpty commit\n"
        )

        result = get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        assert len(result) == 1
        assert result[0]["additions"] == 0
        assert result[0]["deletions"] == 0

    @patch("auditer.git_ops._run_git")
    def test_get_commit_log_failure(self, mock_run):
        from auditer.git_ops import get_commit_log
        from pathlib import Path

        mock_run.return_value.returncode = 128
        mock_run.return_value.stdout = ""

        result = get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        assert result == []


class TestGetTagsInRange:
    @patch("auditer.git_ops._run_git")
    def test_get_tags_in_range(self, mock_run):
        from auditer.git_ops import get_tags_in_range
        from pathlib import Path

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "v1.0.0\x1e2025-06-10T12:00:00+00:00\x1eAlice\x1e<alice@example.com>\x1e\x1e\n"
            "v1.1.0\x1e2025-06-20T12:00:00+00:00\x1e\x1e\x1eBob\x1e<bob@example.com>\n"
        )

        result = get_tags_in_range(Path("/repo"), "2025-06-01", "2025-06-30")
        assert len(result) == 2
        assert result[0]["name"] == "v1.0.0"
        assert result[0]["tagger_name"] == "Alice"
        assert result[0]["tagger_email"] == "alice@example.com"
        assert result[0]["date"] == "2025-06-10"
        # Lightweight tag falls back to author info
        assert result[1]["name"] == "v1.1.0"
        assert result[1]["tagger_name"] == "Bob"

    @patch("auditer.git_ops._run_git")
    def test_get_tags_in_range_empty(self, mock_run):
        from auditer.git_ops import get_tags_in_range
        from pathlib import Path

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""

        result = get_tags_in_range(Path("/repo"), "2025-06-01", "2025-06-30")
        assert result == []

    @patch("auditer.git_ops._run_git")
    def test_get_tags_in_range_filters_by_date(self, mock_run):
        from auditer.git_ops import get_tags_in_range
        from pathlib import Path

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            "v0.9\x1e2025-05-15T12:00:00+00:00\x1eAlice\x1e<alice@example.com>\x1e\x1e\n"
            "v1.0\x1e2025-06-10T12:00:00+00:00\x1eAlice\x1e<alice@example.com>\x1e\x1e\n"
            "v2.0\x1e2025-07-01T12:00:00+00:00\x1eAlice\x1e<alice@example.com>\x1e\x1e\n"
        )

        result = get_tags_in_range(Path("/repo"), "2025-06-01", "2025-06-30")
        assert len(result) == 1
        assert result[0]["name"] == "v1.0"


class TestDeveloperDetail:
    def test_developer_detail_commits(self):
        """Developer detail section includes commits with GitHub links."""
        commits = [
            CommitRecord(
                sha="abc1234567890", author_name="Alice",
                author_email="alice@example.com", date="2025-06-01",
                subject="Add feature", additions=50, deletions=10,
                repo_name="my-repo",
            ),
        ]
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=1, additions=50, deletions=10,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        repo = RepoActivity(
            name="my-repo", owner="org", commits=commits,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )
        report = generate_activity_report(result)
        assert "## Developer Detail" in report
        # No github_login → display name is human name
        assert "### Alice" in report
        assert "**Commits**" in report
        # Commit hash linked to GitHub
        assert "[abc1234](https://github.com/org/my-repo/commit/abc1234567890)" in report
        assert "+50/-10" in report

    def test_developer_detail_prs(self):
        """Developer detail section includes PRs with links."""
        pr = PRRecord(
            number=42, title="Fix auth", author="alice",
            state="merged", created_at="2025-06-05",
            merged_at="2025-06-06",
            url="https://github.com/org/repo/pull/42",
            repo_name="repo",
        )
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=0, additions=0, deletions=0,
            prs_opened=1, prs_merged=1, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        repo = RepoActivity(
            name="repo", owner="org", commits=[],
            prs_opened=1, prs_merged=1, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
            prs=[pr],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )
        report = generate_activity_report(result)
        assert "**Pull Requests:**" in report
        assert "[#42](https://github.com/org/repo/pull/42)" in report
        assert "Merged" in report

    def test_developer_detail_tags(self):
        """Developer detail section includes tags with release links."""
        tag = TagRecord(
            name="v1.0.0", tagger_name="Alice",
            tagger_email="alice@example.com",
            date="2025-06-15", repo_name="repo", owner="org",
        )
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=0, additions=0, deletions=0,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            tags_created=1,
        )
        repo = RepoActivity(
            name="repo", owner="org", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
            tags=[tag],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )
        report = generate_activity_report(result)
        assert "**Tags:**" in report
        assert "[v1.0.0](https://github.com/org/repo/releases/tag/v1.0.0)" in report

    def test_leaderboard_columns(self):
        """Leaderboard has Net Lines, Merged PRs, Open PRs, and Tags columns."""
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=5, additions=100, deletions=20,
            prs_opened=4, prs_merged=3, issues_opened=1,
            issues_closed=0, review_comments=2,
            tags_created=2,
        )
        repo = RepoActivity(
            name="r", owner="o", commits=[], prs_opened=4,
            prs_merged=3, issues_opened=1, issues_closed=0,
            review_comments=2, developers=[dev],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )
        report = generate_activity_report(result)
        assert "| Net Lines " in report
        assert "| Merged PRs |" in report
        assert "| Open PRs |" in report
        assert "| Tags |" in report


class TestDevDisplayName:
    """Test that login is used as display ID in reports."""

    def _make_result(self, dev):
        repo = RepoActivity(
            name="r", owner="o",
            commits=[
                CommitRecord(
                    sha="abc123", author_name=dev.name,
                    author_email=dev.email, date="2025-06-01",
                    subject="x", additions=1, deletions=0, repo_name="r",
                ),
            ],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
        )
        return ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )

    def test_leaderboard_shows_login(self):
        dev = DeveloperActivity(
            name="Alice Smith", email="alice@example.com",
            commits=5, additions=100, deletions=20,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="alicesmith",
        )
        report = generate_activity_report(self._make_result(dev))
        assert "| alicesmith |" in report

    def test_leaderboard_no_login(self):
        dev = DeveloperActivity(
            name="Alice Smith", email="alice@example.com",
            commits=5, additions=100, deletions=20,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="",
        )
        report = generate_activity_report(self._make_result(dev))
        assert "| Alice Smith |" in report

    def test_developer_detail_header_shows_login(self):
        dev = DeveloperActivity(
            name="Bob Jones", email="bob@example.com",
            commits=1, additions=10, deletions=0,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="bobjones",
        )
        report = generate_activity_report(self._make_result(dev))
        assert "### bobjones" in report

    def test_top_contributor_shows_login(self):
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=10, additions=100, deletions=20,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="alicegit",
        )
        result = _top_contributor([dev])
        assert result == "alicegit"

    def test_top_contributor_no_login(self):
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=10, additions=100, deletions=20,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        result = _top_contributor([dev])
        assert result == "Alice"

    def test_per_repo_detail_shows_login(self):
        dev = DeveloperActivity(
            name="Carol", email="carol@example.com",
            commits=3, additions=30, deletions=5,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="carol42",
        )
        report = generate_activity_report(self._make_result(dev))
        # Per-Repo Detail table should show login
        detail_section = report.split("## Per-Repo Detail")[1].split("## Developer Detail")[0]
        assert "carol42" in detail_section

    def test_contributors_table(self):
        """Contributors table maps @login to name and email."""
        dev = DeveloperActivity(
            name="Alice Smith", email="alice@example.com",
            commits=5, additions=100, deletions=20,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="alicesmith",
        )
        report = generate_activity_report(self._make_result(dev))
        assert "## Contributors" in report
        assert "| alicesmith | Alice Smith | alice@example.com |" in report

    def test_contributors_table_no_login(self):
        """Contributors table uses email as ID when no login."""
        dev = DeveloperActivity(
            name="Bob", email="bob@example.com",
            commits=3, additions=30, deletions=5,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        report = generate_activity_report(self._make_result(dev))
        assert "## Contributors" in report
        assert "| Bob | Bob | bob@example.com |" in report

    def test_collect_dev_prs_matches_github_login(self):
        """_collect_dev_prs should match PRs by github_login."""
        from auditer.reports.activity import _collect_dev_prs

        dev = DeveloperActivity(
            name="Alice Smith", email="alice@example.com",
            commits=0, additions=0, deletions=0,
            prs_opened=1, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="alicesmith",
        )
        pr = PRRecord(
            number=1, title="Fix bug", author="alicesmith",
            state="open", created_at="2025-06-01", merged_at=None,
            url="https://github.com/org/repo/pull/1", repo_name="repo",
        )
        repo = RepoActivity(
            name="repo", owner="org", commits=[],
            prs_opened=1, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
            prs=[pr],
        )
        result = _collect_dev_prs([repo], dev)
        assert len(result) == 1
        assert result[0].number == 1

    def test_collect_dev_commits_matches_by_login(self):
        """_collect_dev_commits matches commits by resolved login when emails differ."""
        from auditer.reports.activity import _collect_dev_commits

        dev = DeveloperActivity(
            name="Steven Jones", email="steven@work.com",
            commits=0, additions=0, deletions=0,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            github_login="steven-js",
        )
        commit = CommitRecord(
            sha="abc123", author_name="Steven Jones",
            author_email="steven@home.com", date="2025-06-01",
            subject="x", additions=1, deletions=0, repo_name="r",
        )
        repo = RepoActivity(
            name="r", owner="o", commits=[commit],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[dev],
        )
        email_to_login = {"steven@home.com": "steven-js"}
        result = _collect_dev_commits([repo], dev, email_to_login)
        assert len(result) == 1

    def test_collect_dev_commits_includes_branch_commits(self):
        """_collect_dev_commits must also search r.branch_commits, or a
        developer's Developer Detail commit table would be empty even
        though they're listed in the Leaderboard via branch_developers."""
        from auditer.reports.activity import _collect_dev_commits

        dev = DeveloperActivity(
            name="Steven Jones", email="steven@example.com",
            commits=1, additions=1, deletions=0,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        branch_commit = CommitRecord(
            sha="branch1", author_name="Steven Jones",
            author_email="steven@example.com", date="2025-06-01",
            subject="fix on open PR branch", additions=1, deletions=0,
            repo_name="r",
        )
        repo = RepoActivity(
            name="r", owner="o", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0, developers=[],
            branch_commits=[branch_commit],
        )
        result = _collect_dev_commits([repo], dev)
        assert len(result) == 1
        assert result[0][0].sha == "branch1"


class TestBranchActivitySection:
    def test_branch_activity_section_rendered(self):
        """When repos have branches, the Branch Activity section appears."""
        branch_commit = CommitRecord(
            sha="bbb1111", author_name="Alice", author_email="alice@example.com",
            date="2025-06-01", subject="Branch work", additions=30, deletions=5,
            repo_name="my-repo",
        )
        branch_dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=1, additions=30, deletions=5,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        branch = BranchActivity(
            name="feature/xyz",
            repo_name="my-repo",
            commits=[branch_commit],
            developers=[branch_dev],
        )
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=5, additions=100, deletions=10,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        repo = RepoActivity(
            name="my-repo", owner="testuser",
            commits=[
                CommitRecord(
                    sha="aaa1111", author_name="Alice", author_email="alice@example.com",
                    date="2025-06-01", subject="Main work", additions=100, deletions=10,
                    repo_name="my-repo",
                ),
            ],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            developers=[dev],
            branches=[branch],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )
        report = generate_activity_report(result, include_branches=True)
        assert "## Branch Activity" in report
        assert "feature/xyz" in report
        assert "not yet merged" in report
        assert "**Mode:** Default branch + feature branches" in report

    def test_no_branch_section_when_empty(self):
        """When no repos have branches, Branch Activity section is omitted."""
        dev = DeveloperActivity(
            name="Alice", email="alice@example.com",
            commits=5, additions=100, deletions=10,
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
        )
        repo = RepoActivity(
            name="my-repo", owner="testuser",
            commits=[
                CommitRecord(
                    sha="aaa1111", author_name="Alice", author_email="alice@example.com",
                    date="2025-06-01", subject="Main work", additions=100, deletions=10,
                    repo_name="my-repo",
                ),
            ],
            prs_opened=0, prs_merged=0, issues_opened=0,
            issues_closed=0, review_comments=0,
            developers=[dev],
        )
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[dev],
        )
        report = generate_activity_report(result)
        assert "## Branch Activity" not in report

    def test_mode_all_branches(self):
        """Without include_branches, mode says 'All branches'."""
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[], developers=[],
        )
        report = generate_activity_report(result, include_branches=False)
        assert "**Mode:** All branches" in report

    def test_mode_with_branches(self):
        """With include_branches, mode says 'Default branch + feature branches'."""
        result = ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[], developers=[],
        )
        report = generate_activity_report(result, include_branches=True)
        assert "**Mode:** Default branch + feature branches (no double-counting)" in report


def _dev(name, email, commits=0, additions=0, deletions=0, **kw):
    return DeveloperActivity(
        name=name, email=email, commits=commits, additions=additions,
        deletions=deletions, prs_opened=kw.pop("prs_opened", 0),
        prs_merged=kw.pop("prs_merged", 0), issues_opened=0, issues_closed=0,
        review_comments=kw.pop("review_comments", 0), **kw,
    )


def _commit(sha, email, date, additions=10, deletions=1, repo="my-repo"):
    return CommitRecord(
        sha=sha, author_name=email.split("@")[0], author_email=email,
        date=date, subject=f"commit {sha}", additions=additions,
        deletions=deletions, repo_name=repo,
    )


class TestBranchCommitsInTotals:
    """Summary, Daily/Weekly Breakdown and Per-Repo tables must count
    feature-branch commits (r.branch_commits), so they agree with the
    Developer Leaderboard, which already did."""

    def _result(self):
        main = _commit("aaa1111", "alice@example.com", "2025-06-02", 100, 10)
        branch = _commit("bbb2222", "bob@example.com", "2025-06-03", 30, 5)
        alice = _dev("alice", "alice@example.com", 1, 100, 10)
        bob = _dev("bob", "bob@example.com", 1, 30, 5)
        repo = RepoActivity(
            name="my-repo", owner="o", commits=[main],
            prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0, developers=[alice],
            branch_commits=[branch], branch_developers=[bob],
        )
        branch_only = RepoActivity(
            name="branch-only-repo", owner="o", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=0, developers=[],
            branch_commits=[_commit("ccc3333", "bob@example.com", "2025-06-03", 7, 0,
                                    repo="branch-only-repo")],
            branch_developers=[_dev("bob", "bob@example.com", 1, 7, 0)],
        )
        return ActivityResult(
            account_slug="test", since="2025-06-02", until="2025-06-08",
            repos=[repo, branch_only],
            developers=_aggregate_developers_from_repos([repo, branch_only]),
        )

    def test_summary_counts_branch_commits(self):
        report = generate_activity_report(self._result(), include_branches=True)
        assert "| Total commits | 3 |" in report
        assert "| Active repos | 2 |" in report

    def test_daily_and_weekly_breakdown_count_branch_commits(self):
        report = generate_activity_report(self._result(), include_branches=True)
        assert "| 2025-06-03 | 2 | 37 | 5 | 32 | 1 |" in report
        assert "| Jun 02 – Jun 08 | 3 |" in report

    def test_per_repo_tables_count_branch_commits_and_developers(self):
        report = generate_activity_report(self._result(), include_branches=True)
        assert "| my-repo | 2 | 130 | 15 | 115 |" in report
        assert "| branch-only-repo | 1 | 7 | 0 | 7 |" in report
        detail = report.split("## Per-Repo Detail")[1].split("## Branch Activity")[0]
        branch_only_detail = detail.split("### branch-only-repo")[1]
        assert "| bob | 1 |" in branch_only_detail
        assert "No developer activity." not in branch_only_detail

    def test_activity_result_total_commits_includes_branch_commits(self):
        result = self._result()
        assert result.total_commits == 3
        assert result.total_branch_commits == 2


class TestPRReviewsInReport:
    def _result(self):
        records = [
            ReviewRecord(
                pr_number=7, pr_title="Fix parser", pr_author="alice",
                reviewer="bob", state=state, submitted_at=date,
                url="https://github.com/o/my-repo/pull/7", repo_name="my-repo",
            )
            for state, date in [
                ("changes_requested", "2025-06-02"),
                ("approved", "2025-06-03"),
                ("approved", "2025-06-04"),
            ]
        ]
        bob = _dev(
            "bob", "bob@github", review_comments=1, github_login="bob",
            reviews_submitted=3, reviews_approved=2,
        )
        repo = RepoActivity(
            name="my-repo", owner="o", commits=[],
            prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
            review_comments=1, developers=[bob],
            reviews_submitted=3, reviews_approved=2, review_records=records,
        )
        return ActivityResult(
            account_slug="test", since="2025-06-01", until="2025-06-30",
            repos=[repo], developers=[bob],
        )

    def test_summary_rows(self):
        report = generate_activity_report(self._result())
        assert "| PR reviews submitted | 3 |" in report
        assert "| PR approvals | 2 |" in report
        assert "| Review comments | 1 |" in report

    def test_leaderboard_review_columns(self):
        report = generate_activity_report(self._result())
        assert (
            "| Issues | Reviews | Approvals | Review Comments | PR Comments | Tags |"
            in report
        )
        # commits, merge commits, +, -, net, merged PRs, merges, open PRs,
        # issues, reviews, approvals, review comments, PR comments, tags
        assert "| bob | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 2 | 1 | 0 | 0 |" in report

    def test_review_only_repo_appears_in_per_repo_table(self):
        report = generate_activity_report(self._result())
        assert "| my-repo | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | bob |" in report

    def test_developer_detail_lists_reviews_oldest_first(self):
        report = generate_activity_report(self._result())
        detail = report.split("## Developer Detail")[1]
        assert "**Reviews** (oldest first):" in detail
        rows = [l for l in detail.splitlines() if "[#7](" in l]
        assert rows == [
            "| 2025-06-02 | my-repo | [#7](https://github.com/o/my-repo/pull/7) Fix parser | alice | Changes requested |",
            "| 2025-06-03 | my-repo | [#7](https://github.com/o/my-repo/pull/7) Fix parser | alice | Approved |",
            "| 2025-06-04 | my-repo | [#7](https://github.com/o/my-repo/pull/7) Fix parser | alice | Approved |",
        ]

    def test_activity_result_total_reviews(self):
        assert self._result().total_reviews == 3


class TestAggregateReviewCounts:
    def test_review_counts_summed_across_repos(self):
        def repo(name, dev):
            return RepoActivity(
                name=name, owner="o", commits=[], prs_opened=0, prs_merged=0,
                issues_opened=0, issues_closed=0, review_comments=0, developers=[dev],
            )

        a = _dev("bob", "bob@github", github_login="bob", reviews_submitted=2, reviews_approved=1)
        b = _dev("bob", "bob@github", github_login="bob", reviews_submitted=5, reviews_approved=3)
        devs = _aggregate_developers_from_repos([repo("r1", a), repo("r2", b)])
        assert len(devs) == 1
        assert (devs[0].reviews_submitted, devs[0].reviews_approved) == (7, 4)

    def test_merge_by_name_keeps_review_counts(self):
        def repo(name, dev):
            return RepoActivity(
                name=name, owner="o", commits=[], prs_opened=0, prs_merged=0,
                issues_opened=0, issues_closed=0, review_comments=0, developers=[dev],
            )

        a = _dev("Bob Smith", "bob@work.com", commits=2, reviews_submitted=1, reviews_approved=1)
        b = _dev("Bob Smith", "bob@home.com", commits=1, reviews_submitted=4, reviews_approved=2)
        devs = _aggregate_developers_from_repos([repo("r1", a), repo("r2", b)])
        assert len(devs) == 1
        assert (devs[0].reviews_submitted, devs[0].reviews_approved) == (5, 3)


class TestLocalOnlyHeader:
    def test_data_source_line_under_mode(self, sample_activity_result):
        sample_activity_result.local_only_reason = "--local-only"
        report = generate_activity_report(sample_activity_result, include_branches=True)
        header = report.split("## ")[0]
        assert "**Data source:** Local git only (--local-only). PRs, issues and reviews are not included" in header
        assert header.index("**Mode:**") < header.index("**Data source:**")

    def test_absent_when_api_used(self, sample_activity_result):
        assert "**Data source:**" not in generate_activity_report(sample_activity_result)


class TestMergeByEmailLocalPart:
    def test_same_local_part_different_domains_merge(self):
        """Seen live (Bitbucket, no logins): the same person under the old
        and new company domain, and under a domain typo."""
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [
                _mk_dev("sarahcampion-reach", "sarah.campion@acmecorp.example", commits=5),
                _mk_dev("Harriet Rowan", "harriet.rowan@acmecorp.example", commits=3),
            ]),
            _mk_repo("b", [
                _mk_dev("Sarah Campion", "sarah.campion@acmepublishing.example", commits=2),
                _mk_dev("Harriet", "harriet.rowan@acmecrop.example", commits=1),
            ]),
        ])
        assert sorted((d.email, d.commits) for d in devs) == [
            ("harriet.rowan@acmecorp.example", 4), ("sarah.campion@acmecorp.example", 7),
        ]

    def test_local_part_matches_a_name(self):
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [_mk_dev("sidney", "sidney.dalton@acmecorp.example", commits=2)]),
            _mk_repo("b", [_mk_dev("Sidney Dalton", "x@ad1.example.com", commits=1)]),
        ])
        assert len(devs) == 1 and devs[0].commits == 3

    def test_links_are_transitive(self):
        """name ↔ local part ↔ name: one person, not two half-merged rows."""
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [_mk_dev("Jo Bloggsworth", "jo@a.com", commits=1)]),
            _mk_repo("b", [_mk_dev("jbw", "jobloggsworth@b.com", commits=1)]),
            _mk_repo("c", [_mk_dev("JB", "jobloggsworth@c.com", commits=1)]),
        ])
        assert len(devs) == 1 and devs[0].commits == 3

    def test_generic_short_and_placeholder_local_parts_do_not_merge(self):
        devs = _aggregate_developers_from_repos([
            _mk_repo("a", [
                _mk_dev("Ann One", "developer@a.com"),
                _mk_dev("Bob Two", "developer@b.com"),
                _mk_dev("Cat Three", "j.smith@a.com"),     # "jsmith": too short
                _mk_dev("Dan Four", "j.smith@b.com"),
                _mk_dev("Eve Five", "1+someone@users.noreply.github.com"),
                _mk_dev("Fay Six", "2+someone@users.noreply.github.com"),
            ]),
        ])
        assert len(devs) == 6
