from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from click.testing import CliRunner

from auditer.cli import cli
from auditer.commands.data.activity import run_activity
from auditer.config import AppConfig, load_config_from_dict
from auditer.git_ops import LocalRepoInfo
from auditer.models import AccountConfig, ProjectConfig
from tests.conftest import strip_ansi


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_real_git_fetch():
    """Keep run_activity hermetic: the fixtures' repo paths don't exist, and
    no test should reach a real remote. Tests of the fetch step re-patch."""
    from auditer.git_ops import GitOpResult

    with patch(
        "auditer.commands.data.activity.fetch_repo",
        return_value=GitOpResult(success=True, message="Fetched"),
    ) as fetch, patch(
        "auditer.commands.data.activity.get_merge_commits", return_value=[],
    ):
        yield fetch


@pytest.fixture
def mock_config():
    from auditer.config import AppConfig

    account = AccountConfig(
        slug="test-user",
        type="user",
        username="testuser",
        token="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
    )
    return AppConfig(
        accounts=[account],
        stale_months=12,
        data_dir="data",
        reports_dir="/tmp/test-reports",
        log_level="INFO",
    )


@pytest.fixture
def mock_local_repo():
    return LocalRepoInfo(
        name="my-repo",
        path=Path("/data/test-user/public/my-repo"),
        private=False,
        owner="testuser",
        clone_url="https://github.com/testuser/my-repo.git",
        has_readme=True,
        has_license_file=True,
        default_branch="main",
        pushed_at="2025-06-01T12:00:00+00:00",
        size_kb=500,
    )


@pytest.fixture
def sample_commit_log():
    return [
        {
            "sha": "abc1234",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "date": "2025-06-01",
            "subject": "Add feature X",
            "additions": 50,
            "deletions": 10,
        },
        {
            "sha": "def5678",
            "author_name": "Bob",
            "author_email": "bob@example.com",
            "date": "2025-06-02",
            "subject": "Fix bug Y",
            "additions": 5,
            "deletions": 3,
        },
    ]


def _invoke(runner, args, mock_config):
    with patch("auditer.cli.LazyConfig") as MockLazy:
        MockLazy.return_value.config = mock_config
        return runner.invoke(cli, args)


class TestActivityRequiresAccount:
    def test_requires_account_or_all(self, runner, mock_config):
        result = _invoke(runner, ["activity"], mock_config)
        output = strip_ansi(result.output)
        assert "Specify --account SLUG or --all" in output


class TestActivitySingleAccount:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_single_account(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo, sample_commit_log
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output
        assert "Total commits:" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_all_accounts(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo, sample_commit_log
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--all", "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output


class TestActivityNoLocalRepos:
    @patch("auditer.commands.data.activity.scan_local_repos")
    def test_no_local_repos(self, mock_scan, runner, mock_config):
        mock_scan.return_value = []
        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "No local repos found" in output
        assert "auditer checkout" in output


class TestActivityFilters:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_user_filter(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo, sample_commit_log
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--user", "Alice",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_repo_filter(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo, sample_commit_log
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--repo", "my-repo",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_repo_filter_not_found(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo
    ):
        mock_scan.return_value = [mock_local_repo]

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--repo", "nonexistent",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "not found" in output


class TestActivityDateRange:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_since_until(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo, sample_commit_log
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-01-01", "--until", "2025-12-31"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "2025-01-01" in output
        assert "2025-12-31" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_default_date_range(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user"],
            mock_config,
        )
        output = strip_ansi(result.output)
        # Should show a date range without error
        assert "ACTIVITY SUMMARY" in output


class TestActivityNoToken:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    def test_no_token_skips_api(
        self, mock_tags, mock_git_log, mock_scan, runner, sample_commit_log, mock_local_repo
    ):
        from auditer.config import AppConfig

        account = AccountConfig(
            slug="no-token",
            type="user",
            username="publicuser",
            token=None,
        )
        config = AppConfig(
            accounts=[account],
            stale_months=12,
            data_dir="data",
            reports_dir="/tmp/test-reports",
            log_level="INFO",
        )
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log

        result = _invoke(
            runner,
            ["activity", "--account", "no-token",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output
        # Commits still counted from local git
        assert "Total commits:" in output


class TestActivityExcludedRepos:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_excluded_repos_filtered(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner
    ):
        from auditer.config import AppConfig

        account = AccountConfig(
            slug="test-user",
            type="user",
            username="testuser",
            token="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
            exclude_repos=["excluded-repo"],
        )
        config = AppConfig(
            accounts=[account],
            stale_months=12,
            data_dir="data",
            reports_dir="/tmp/test-reports",
            log_level="INFO",
        )

        repo1 = LocalRepoInfo(
            name="my-repo", path=Path("/data/public/my-repo"), private=False,
            owner="testuser", clone_url="https://github.com/testuser/my-repo.git",
            has_readme=True, has_license_file=True, default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00", size_kb=500,
        )
        repo2 = LocalRepoInfo(
            name="excluded-repo", path=Path("/data/public/excluded-repo"), private=False,
            owner="testuser", clone_url="https://github.com/testuser/excluded-repo.git",
            has_readme=True, has_license_file=True, default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00", size_kb=500,
        )
        mock_scan.return_value = [repo1, repo2]
        mock_git_log.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            config,
        )
        output = strip_ansi(result.output)
        assert "Excluded 1 repo(s)" in output


class TestUserFilterRestrictsData:
    """Verify --user actually restricts commits, not just the leaderboard."""

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_user_filter_removes_other_users_commits(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo
    ):
        """When --user Alice is set, Bob's commits must not appear anywhere."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [
            {
                "sha": "aaa1111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "date": "2025-06-01",
                "subject": "Alice's commit",
                "additions": 50,
                "deletions": 10,
            },
            {
                "sha": "bbb2222",
                "author_name": "Bob",
                "author_email": "bob@example.com",
                "date": "2025-06-02",
                "subject": "Bob's commit",
                "additions": 5,
                "deletions": 3,
            },
        ]

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--user", "Alice",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        # Only 1 commit (Alice's), not 2
        assert "Total commits:         1" in output
        assert "Active developers:     1" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_user_filter_shows_filter_in_summary(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--user", "Alice",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "Filtered to user:      Alice" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_user_and_repo_filter_combined(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [
            {
                "sha": "aaa1111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "date": "2025-06-01",
                "subject": "Alice's commit",
                "additions": 10,
                "deletions": 2,
            },
        ]

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--user", "Alice",
             "--repo", "my-repo",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "Filtered to user:      Alice" in output
        assert "Filtered to repo:      my-repo" in output
        assert "Total commits:         1" in output


class TestBuildReportFilename:
    def test_basic(self):
        from auditer.commands.data.activity import _build_report_filename

        assert _build_report_filename("2026-02-08", "2026-02-14") == (
            "activity_2026-02-08_2026-02-14.md"
        )

    def test_with_user(self):
        from auditer.commands.data.activity import _build_report_filename

        assert _build_report_filename("2026-02-08", "2026-02-14", user_filter="Alice") == (
            "activity_2026-02-08_2026-02-14_user-alice.md"
        )

    def test_with_repo(self):
        from auditer.commands.data.activity import _build_report_filename

        assert _build_report_filename("2026-02-08", "2026-02-14", repo_filter="my-project") == (
            "activity_2026-02-08_2026-02-14_repo-my-project.md"
        )

    def test_with_user_and_repo(self):
        from auditer.commands.data.activity import _build_report_filename

        assert _build_report_filename(
            "2026-02-08", "2026-02-14", user_filter="Alice", repo_filter="my-project"
        ) == "activity_2026-02-08_2026-02-14_user-alice_repo-my-project.md"


class TestCrossRepoBackfill:
    """Verify that email→login mappings from one repo are applied to
    developers in other repos before cross-repo aggregation."""

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_backfill_merges_across_repos(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config,
    ):
        """Developer in repo2 (email only) merges with repo1 (has login via API)."""
        repo1 = LocalRepoInfo(
            name="repo1", path=Path("/data/test-user/public/repo1"), private=False,
            owner="testuser", clone_url="https://github.com/testuser/repo1.git",
            has_readme=True, has_license_file=True, default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00", size_kb=500,
        )
        repo2 = LocalRepoInfo(
            name="repo2", path=Path("/data/test-user/public/repo2"), private=False,
            owner="testuser", clone_url="https://github.com/testuser/repo2.git",
            has_readme=True, has_license_file=True, default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00", size_kb=500,
        )
        mock_scan.return_value = [repo1, repo2]
        mock_tags.return_value = []

        # repo1: alice commits with alice@example.com
        # repo2: alice commits with alice@example.com
        # API commits for repo1 map alice@example.com → alicesmith
        # API commits for repo2 return nothing (no mapping)
        commit_log = [
            {
                "sha": "aaa1111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "date": "2025-06-01",
                "subject": "Work in this repo",
                "additions": 10,
                "deletions": 2,
            },
        ]
        mock_git_log.return_value = commit_log

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []

        # Only repo1 returns the mapping; repo2 returns empty
        def fake_list_repo_commits(owner, name, since=None, until=None):
            if name == "repo1":
                return [{
                    "author": {"login": "alicesmith"},
                    "commit": {"author": {"email": "alice@example.com"}},
                }]
            return []

        client_instance.list_repo_commits.side_effect = fake_list_repo_commits
        MockClient.return_value = client_instance

        from auditer.commands.data.activity import run_activity
        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30",
        )

        # Should produce 1 merged developer, not 2
        assert len(result.developers) == 1
        assert result.developers[0].github_login == "alicesmith"
        assert result.developers[0].commits == 2  # 1 from each repo


class TestActivityTerminalSummary:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_terminal_summary(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner, mock_config, mock_local_repo, sample_commit_log
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output
        assert "Total commits:" in output
        assert "Active developers:" in output
        assert "Total PRs opened:" in output
        assert "Total issues opened:" in output


class TestProjectFilter:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_project_filters_repos(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner
    ):
        """When project is active, only project repos are included in the activity result."""
        config = load_config_from_dict(
            {
                "defaults": {
                    "stale_months": 12,
                    "reports_dir": "/tmp/test-reports",
                    "data_dir": "data",
                },
                "accounts": [
                    {
                        "slug": "test-user",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                    }
                ],
                "projects": {
                    "my-proj": {"repos": ["test-user/wanted-repo"]},
                },
            }
        )
        account = config.accounts[0]
        project = config.projects[0]

        wanted_repo = LocalRepoInfo(
            name="wanted-repo",
            path=Path("/data/test-user/public/wanted-repo"),
            private=False,
            owner="testuser",
            clone_url="https://github.com/testuser/wanted-repo.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
        )
        other_repo = LocalRepoInfo(
            name="other-repo",
            path=Path("/data/test-user/public/other-repo"),
            private=False,
            owner="testuser",
            clone_url="https://github.com/testuser/other-repo.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
        )
        mock_scan.return_value = [wanted_repo, other_repo]
        mock_git_log.return_value = [
            {
                "sha": "aaa1111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "date": "2025-06-01",
                "subject": "Work",
                "additions": 10,
                "deletions": 2,
            },
        ]
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        from auditer.commands.data.activity import run_activity

        result = run_activity(
            config,
            account,
            since="2025-06-01",
            until="2025-06-30",
            project=project,
        )

        # Only wanted-repo should be in the result
        assert len(result.repos) == 1
        assert result.repos[0].name == "wanted-repo"

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_project_reports_dir(
        self, MockClient, mock_tags, mock_git_log, mock_scan, tmp_path
    ):
        """When project is active, reports go to reports/{project_name}/ not reports/{slug}/."""
        config = load_config_from_dict(
            {
                "defaults": {
                    "stale_months": 12,
                    "reports_dir": str(tmp_path / "reports"),
                    "data_dir": "data",
                },
                "accounts": [
                    {
                        "slug": "test-user",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                    }
                ],
                "projects": {
                    "my-proj": {"repos": ["test-user/my-repo"]},
                },
            }
        )
        account = config.accounts[0]
        project = config.projects[0]

        repo = LocalRepoInfo(
            name="my-repo",
            path=Path("/data/test-user/public/my-repo"),
            private=False,
            owner="testuser",
            clone_url="https://github.com/testuser/my-repo.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
        )
        mock_scan.return_value = [repo]
        mock_git_log.return_value = []
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        from auditer.commands.data.activity import run_activity

        run_activity(
            config,
            account,
            since="2025-06-01",
            until="2025-06-30",
            project=project,
        )

        # Report should be in project dir, not account slug dir
        project_report_dir = tmp_path / "reports" / "my-proj"
        slug_report_dir = tmp_path / "reports" / "test-user"
        assert project_report_dir.exists()
        assert not slug_report_dir.exists()

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_project_cli_option(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner
    ):
        """--project CLI option resolves the project and filters repos."""
        config = load_config_from_dict(
            {
                "defaults": {
                    "stale_months": 12,
                    "reports_dir": "/tmp/test-reports",
                    "data_dir": "data",
                },
                "accounts": [
                    {
                        "slug": "test-user",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                    }
                ],
                "projects": {
                    "my-proj": {"repos": ["test-user/my-repo"]},
                },
            }
        )

        repo = LocalRepoInfo(
            name="my-repo",
            path=Path("/data/test-user/public/my-repo"),
            private=False,
            owner="testuser",
            clone_url="https://github.com/testuser/my-repo.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
        )
        mock_scan.return_value = [repo]
        mock_git_log.return_value = []
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--project", "my-proj",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_project_with_account(
        self, MockClient, mock_tags, mock_git_log, mock_scan, runner
    ):
        """--project combined with --account works."""
        config = load_config_from_dict(
            {
                "defaults": {
                    "stale_months": 12,
                    "reports_dir": "/tmp/test-reports",
                    "data_dir": "data",
                },
                "accounts": [
                    {
                        "slug": "test-user",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                    }
                ],
                "projects": {
                    "my-proj": {"repos": ["test-user/my-repo"]},
                },
            }
        )

        repo = LocalRepoInfo(
            name="my-repo",
            path=Path("/data/test-user/public/my-repo"),
            private=False,
            owner="testuser",
            clone_url="https://github.com/testuser/my-repo.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
        )
        mock_scan.return_value = [repo]
        mock_git_log.return_value = []
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--project", "my-proj", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output


class TestBranchesFlag:
    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_branches_flag_calls_branch_functions(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches,
        runner, mock_config, mock_local_repo, sample_commit_log,
    ):
        """--branches flag triggers list_active_branches and get_branch_commits."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        mock_list_branches.return_value = [
            {"name": "feature/xyz", "commit_count": 2},
        ]
        mock_branch_commits.return_value = [
            {
                "sha": "bbb1111",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "date": "2025-06-01",
                "subject": "Branch work",
                "additions": 30,
                "deletions": 5,
            },
        ]

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--branches",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "ACTIVITY SUMMARY" in output
        assert "Branches with activity:" in output
        mock_list_branches.assert_called_once()
        mock_branch_commits.assert_called_once()
        # With --branches, get_commit_log should use default_branch="main"
        call_kwargs = mock_git_log.call_args
        assert call_kwargs[1].get("default_branch") == "main" or (
            len(call_kwargs[0]) >= 4 and call_kwargs[0][3] == "main"
        )

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_default_uses_all_branches(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log,
    ):
        """Without --branches, get_commit_log uses --all (no default_branch param)."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        # get_commit_log should be called WITHOUT default_branch (uses --all)
        mock_git_log.assert_called_once()
        call_kwargs = mock_git_log.call_args
        # default_branch should not be passed or should be None
        assert call_kwargs[1].get("default_branch") is None or (
            len(call_kwargs[0]) < 4
        )

    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_branches_not_called_without_flag(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches,
        runner, mock_config, mock_local_repo, sample_commit_log,
    ):
        """Without --branches, branch functions are not called."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        mock_list_branches.assert_not_called()
        mock_branch_commits.assert_not_called()


class TestBranchCommitDedup:
    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_shared_commit_across_branches_not_double_counted(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches,
        mock_config, mock_local_repo,
    ):
        """A commit reachable from two stacked/overlapping branches (e.g. a
        release branch built on top of several feature branches) must only
        be counted once in the account-wide developer rollup, even though
        each branch's own table legitimately lists it."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = []
        mock_tags.return_value = []
        mock_list_branches.return_value = [
            {"name": "feature-a", "commit_count": 1},
            {"name": "release", "commit_count": 1},
        ]
        shared_commit = {
            "sha": "shared1",
            "author_name": "Steven Jones",
            "author_email": "steven@example.com",
            "date": "2025-06-01",
            "subject": "shared work",
            "additions": 30,
            "deletions": 5,
        }
        # Both branches happen to contain the exact same commit (release
        # branch built on top of feature-a, neither merged to default yet).
        mock_branch_commits.return_value = [shared_commit]

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30",
            include_branches=True,
        )

        repo = result.repos[0]
        # Per-branch tables both list the commit (2 rows total across
        # branches) — expected, each shows its own branch's diff.
        assert sum(len(b.commits) for b in repo.branches) == 2
        # The deduped rollup must only count it once.
        assert len(repo.branch_commits) == 1
        assert result.total_branch_commits == 1
        assert len(result.developers) == 1
        assert result.developers[0].commits == 1
        assert result.developers[0].additions == 30

    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_branch_only_developer_appears_in_leaderboard(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches,
        mock_config, mock_local_repo,
    ):
        """A developer whose only in-range work is commits on a still-open
        PR branch (no default-branch commits, no PR opened/merged in the
        window) must still show up in the account-wide developer list, not
        just the per-repo Branch Activity table."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = []  # nothing on default branch
        mock_tags.return_value = []
        mock_list_branches.return_value = [
            {"name": "NAV-1234-fix", "commit_count": 1},
        ]
        mock_branch_commits.return_value = [{
            "sha": "abc1234",
            "author_name": "Steven Jones",
            "author_email": "steven@example.com",
            "date": "2025-06-01",
            "subject": "fix on open PR branch",
            "additions": 10,
            "deletions": 1,
        }]

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        # PR exists but was opened before the window and hasn't merged —
        # excluded from repo.developers by the created_at/merged_at filter.
        client_instance.list_pulls.return_value = [{
            "user": {"login": "steven-jones"},
            "created_at": "2025-05-01T00:00:00Z",
            "merged_at": None,
            "state": "open",
        }]
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30",
            include_branches=True,
        )

        names = [d.name for d in result.developers]
        assert "Steven Jones" in names
        steven = [d for d in result.developers if d.name == "Steven Jones"][0]
        assert steven.commits == 1
        assert steven.prs_opened == 0  # PR predates the window, correctly excluded


class TestRepoSlugUsedForApiCalls:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_api_calls_use_repo_slug_not_display_name(
        self, MockClient, mock_tags, mock_git_log, mock_scan, mock_config,
    ):
        """A local checkout's directory name can differ from the repo's
        real API identifier (e.g. Bitbucket's display name "Bundler JS"
        vs. its actual slug "bundler-js") — every PR/issue/review/commit
        API call must use repo_slug, not name, or it 404s (confirmed
        against the live Bitbucket API)."""
        local_repo = LocalRepoInfo(
            name="Bundler JS",
            path=Path("/data/test-user/public/Bundler JS"),
            private=False,
            owner="testuser",
            clone_url="https://bitbucket.org/testuser/bundler-js.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
            repo_slug="bundler-js",
        )
        mock_scan.return_value = [local_repo]
        mock_git_log.return_value = []
        mock_tags.return_value = []

        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = []
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        MockClient.return_value = client_instance

        run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30",
        )

        # API date filters are the UTC dates covering the London window
        # (00:00 BST on 1 June is 23:00 UTC on 31 May); the exact cut is
        # made locally.
        client_instance.list_pulls.assert_called_once_with(
            "testuser", "bundler-js", since="2025-05-31",
        )
        client_instance.list_repo_issues.assert_called_once_with(
            "testuser", "bundler-js", since="2025-05-31",
        )
        client_instance.list_review_comments.assert_called_once_with(
            "testuser", "bundler-js", since="2025-05-31",
        )
        client_instance.list_issue_comments.assert_called_once_with(
            "testuser", "bundler-js", since="2025-05-31",
        )
        client_instance.list_repo_commits.assert_called_once_with(
            "testuser", "bundler-js", since="2025-05-31", until="2025-06-30",
        )


class TestBitbucketProviderActivity:
    @respx.mock
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    def test_bitbucket_account_activity_flow(
        self, mock_tags, mock_git_log, mock_scan, tmp_path,
    ):
        """Smoke test: activity works end-to-end with a bitbucket-provider account."""
        config = load_config_from_dict(
            {
                "defaults": {
                    "reports_dir": str(tmp_path / "reports"),
                    "data_dir": str(tmp_path / "data"),
                },
                "accounts": [
                    {
                        "slug": "bb",
                        "type": "user",
                        "username": "myworkspace",
                        "token": "bb_test_api_token_1234567890",
                        "provider": "bitbucket",
                        "workspace": "myworkspace",
                        "auth_email": "tom@example.com",
                    }
                ],
            }
        )
        account = config.accounts[0]

        local_repo = LocalRepoInfo(
            name="repo1",
            path=Path("/data/bb/public/repo1"),
            private=False,
            owner="myworkspace",
            clone_url="https://bitbucket.org/myworkspace/repo1.git",
            has_readme=True,
            has_license_file=True,
            default_branch="main",
            pushed_at="2025-06-01T12:00:00+00:00",
            size_kb=500,
        )
        mock_scan.return_value = [local_repo]
        mock_git_log.return_value = [
            {
                "sha": "abc1234",
                "author_name": "Alice",
                "author_email": "alice@example.com",
                "date": "2025-06-01",
                "subject": "Add feature X",
                "additions": 50,
                "deletions": 10,
            }
        ]
        mock_tags.return_value = []

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/repo1/pullrequests"
        ).mock(return_value=httpx.Response(200, json={"values": []}))
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/repo1/issues"
        ).mock(return_value=httpx.Response(200, json={"values": []}))
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/repo1/commits"
        ).mock(return_value=httpx.Response(200, json={"values": []}))

        result = run_activity(config, account, since="2025-06-01", until="2025-06-30")

        assert result.total_commits == 1
        assert result.repos[0].name == "repo1"

        report_path = tmp_path / "reports" / "bb" / "activity_2025-06-01_2025-06-30.md"
        assert report_path.exists()


def _ok_client(rate_limit: dict | None = None) -> MagicMock:
    client_instance = MagicMock()
    client_instance.validate_token.return_value = "testuser"
    client_instance.list_pulls.return_value = []
    client_instance.list_repo_issues.return_value = []
    client_instance.list_review_comments.return_value = []
    client_instance.list_repo_commits.return_value = []
    # Pre-flight reads the headers captured by the client's last response
    client_instance.last_rate_limit = rate_limit or {
        "limit": 5000, "remaining": 4500, "used": 500, "reset": 1800000000,
    }
    return client_instance


class TestApiErrorsSurfaced:
    """A failed API call must never produce a silently all-zero report."""

    @patch("auditer.commands.data.activity.logger")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_failed_call_recorded_and_reported(
        self, MockClient, mock_tags, mock_git_log, mock_scan, mock_logger,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        client_instance = _ok_client()
        client_instance.list_pulls.side_effect = RuntimeError("Forbidden (403)")
        MockClient.return_value = client_instance
        mock_config.reports_dir = str(tmp_path)

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "API data incomplete for 1 repo(s): my-repo" in output
        # Logged at WARNING (visible without --verbose), not DEBUG
        warned = [str(c.args[0]) for c in mock_logger.warning.call_args_list]
        assert any("my-repo: list_pulls failed" in m for m in warned)

        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "**⚠ Data quality:**" in report
        assert "- **my-repo**: list_pulls: Forbidden (403)" in report
        # Commits from local git are still counted
        assert "| Total commits | 2 |" in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_api_errors_on_result_and_kept_through_user_filter(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        client_instance = _ok_client()
        client_instance.list_repo_issues.side_effect = RuntimeError("boom")
        MockClient.return_value = client_instance
        mock_config.reports_dir = str(tmp_path)

        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30", user_filter="alice",
        )
        assert result.repos[0].api_errors == ["list_repo_issues: boom"]
        assert result.api_errors == {"my-repo": ["list_repo_issues: boom"]}

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_user_filter_dropping_repo_still_reports_its_errors(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        """A repo with no activity for the filtered user is dropped from the
        report, but a failed PR fetch there is exactly what could have hidden
        that user's PRs — so the banner and terminal line must still name it."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        client_instance = _ok_client()
        client_instance.list_pulls.side_effect = RuntimeError("Forbidden (403)")
        MockClient.return_value = client_instance
        mock_config.reports_dir = str(tmp_path)

        result = _invoke(
            runner,
            ["activity", "--account", "test-user", "--user", "nobody-here",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        assert "API data incomplete for 1 repo(s): my-repo" in strip_ansi(result.output)
        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30_user-nobody-here.md").read_text()
        assert "- **my-repo**: list_pulls: Forbidden (403)" in report
        assert "| Total commits | 0 |" in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_no_errors_no_banner(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        MockClient.return_value = _ok_client()
        mock_config.reports_dir = str(tmp_path)

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        assert "API data incomplete" not in strip_ansi(result.output)
        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "Data quality" not in report


class TestRateLimitPreflight:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_prints_remaining_and_reset(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        MockClient.return_value = _ok_client()
        mock_config.reports_dir = str(tmp_path)

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "GitHub API rate limit: 4,500/5,000 requests remaining" in output
        assert "resets at 08:00:00 UTC" in output  # 1800000000 epoch
        assert "low" not in output.split("remaining")[1].split("\n")[0]

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_warns_when_low(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        MockClient.return_value = _ok_client(
            {"limit": 5000, "remaining": 42, "used": 4958, "reset": 1800000000}
        )
        mock_config.reports_dir = str(tmp_path)

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        output = strip_ansi(result.output)
        assert "42/5,000 requests remaining" in output
        assert "low; the scan will wait for the reset" in output

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_falls_back_to_rate_limit_endpoint(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        client_instance = _ok_client()
        client_instance.last_rate_limit = None
        client_instance.get_rate_limit.return_value = {
            "resources": {"core": {"limit": 5000, "remaining": 1234, "reset": 1800000000}}
        }
        MockClient.return_value = client_instance
        mock_config.reports_dir = str(tmp_path)

        result = _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30"],
            mock_config,
        )
        assert "1,234/5,000 requests remaining" in strip_ansi(result.output)


class TestPRCounts:
    def test_count_prs_open_is_not_opened_minus_merged(self):
        from auditer.commands.data.activity import _count_prs

        prs = [
            # Opened before the range, merged inside it → merged only
            {"created_at": "2025-05-20T00:00:00Z", "merged_at": "2025-06-03T00:00:00Z"},
            # Opened inside, merged inside → opened + merged
            {"created_at": "2025-06-02T00:00:00Z", "merged_at": "2025-06-04T00:00:00Z"},
            # Opened inside, still open → opened + still_open
            {"created_at": "2025-06-05T00:00:00Z", "merged_at": None, "state": "open"},
            # Opened inside, merged after the range → still open for the range
            {"created_at": "2025-06-06T00:00:00Z", "merged_at": "2025-07-02T00:00:00Z"},
            # Opened inside, closed without merging → closed, not open
            {"created_at": "2025-06-07T00:00:00Z", "merged_at": None, "state": "closed",
             "closed_at": "2025-06-08T00:00:00Z"},
            # Opened before the range, updated in it, still open → open at end
            {"created_at": "2025-05-01T00:00:00Z", "updated_at": "2025-06-10T00:00:00Z",
             "merged_at": None, "state": "open"},
            # Opened before the range and untouched since → stale, not counted
            {"created_at": "2025-05-01T00:00:00Z", "updated_at": "2025-05-02T00:00:00Z",
             "merged_at": None, "state": "open"},
        ]
        # (opened, merged, open at end, closed unmerged)
        assert _count_prs(prs, "2025-06-01", "2025-06-30") == (4, 2, 3, 1)

    def test_developer_prs_open_never_negative(self):
        from auditer.commands.data.activity import _aggregate_developers

        prs = [
            {"user": {"login": "bob"}, "created_at": "2025-05-20T00:00:00Z",
             "merged_at": "2025-06-03T00:00:00Z"},
            {"user": {"login": "bob"}, "created_at": "2025-05-21T00:00:00Z",
             "merged_at": "2025-06-04T00:00:00Z"},
            {"user": {"login": "bob"}, "created_at": "2025-06-05T00:00:00Z",
             "merged_at": None, "state": "open"},
        ]
        devs = _aggregate_developers([], prs, since="2025-06-01", until="2025-06-30")
        bob = devs[0]
        assert (bob.prs_opened, bob.prs_merged, bob.prs_open) == (1, 2, 1)

    def test_closed_pr_branches(self):
        from auditer.commands.data.activity import _closed_pr_branches

        prs = [
            {"state": "closed", "head": {"ref": "feat/landed"}},
            {"state": "closed", "head": {"ref": "feat/reopened"}},
            {"state": "open", "head": {"ref": "feat/reopened"}},
            {"state": "open", "head": {"ref": "feat/wip"}},
            {"state": "closed"},  # no head info (e.g. older Bitbucket shape)
        ]
        assert _closed_pr_branches(prs) == {"feat/landed"}


class TestBranchDedupAndSkip:
    def _client(self, prs):
        c = _ok_client()
        c.list_pulls.return_value = prs
        return c

    def _branch_commit(self, sha, subject="work"):
        return {
            "sha": sha, "author_name": "Alice", "author_email": "alice@example.com",
            "date": "2025-06-02", "subject": subject, "additions": 10, "deletions": 1,
        }

    @patch("auditer.commands.data.activity.get_patch_ids")
    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_closed_pr_branch_is_skipped(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches, mock_patch_ids,
        mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        mock_patch_ids.return_value = {}
        mock_list_branches.return_value = [
            {"name": "feat/landed", "commit_count": 1},
            {"name": "feat/wip", "commit_count": 1},
        ]
        mock_branch_commits.return_value = [self._branch_commit("bbb1111")]
        MockClient.return_value = self._client([
            {"number": 1, "state": "closed", "head": {"ref": "feat/landed"},
             "user": {"login": "alice"}, "created_at": "2025-06-01T00:00:00Z",
             "merged_at": "2025-06-03T00:00:00Z", "updated_at": "2025-06-03T00:00:00Z"},
        ])
        mock_config.reports_dir = str(tmp_path)

        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30", include_branches=True,
        )
        repo = result.repos[0]
        assert [b.name for b in repo.branches] == ["feat/wip"]
        mock_branch_commits.assert_called_once()
        assert mock_branch_commits.call_args[0][1] == "feat/wip"

    @patch("auditer.commands.data.activity.get_patch_ids")
    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_duplicate_patch_credited_to_earliest_copy(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches, mock_patch_ids,
        mock_config, mock_local_repo, tmp_path,
    ):
        """Seen live: a QA branch 'pulled in' Max's fix as Sasha's commit
        an hour later. One copy counts, and it's the original, whatever the
        branch names (QA_... sorts before the feature branch here)."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = []
        mock_tags.return_value = []
        mock_list_branches.return_value = [
            {"name": "QA_28_09", "commit_count": 1},
            {"name": "feat/audit-fix", "commit_count": 1},
        ]
        later = {"sha": "aaa", "author_name": "Sasha", "author_email": "s@x",
                 "date": "2025-06-02", "committed_at": "2025-06-02T18:26:01+05:30",
                 "subject": "Pulled in audit fix", "additions": 5, "deletions": 0}
        original = {"sha": "bbb", "author_name": "Max", "author_email": "m@x",
                    "date": "2025-06-02", "committed_at": "2025-06-02T13:49:36+01:00",
                    "subject": "Audit fix", "additions": 5, "deletions": 0}
        mock_branch_commits.side_effect = [[later], [original]]
        mock_patch_ids.side_effect = lambda path, revs, *a, **kw: (
            {} if revs == ["origin/main"] else {"aaa": "P", "bbb": "P"}
        )
        MockClient.return_value = self._client([])
        mock_config.reports_dir = str(tmp_path)

        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30", include_branches=True,
        )
        # 12:49 UTC beats 12:56 UTC: Max's commit is the one counted
        assert [c.sha for c in result.repos[0].branch_commits] == ["bbb"]
        assert {d.name: d.commits for d in result.developers} == {"Max": 1}

    @patch("auditer.commands.data.activity.get_patch_ids")
    @patch("auditer.commands.data.activity.list_active_branches")
    @patch("auditer.commands.data.activity.get_branch_commits")
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_rebased_copies_deduped_by_patch_id(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_branch_commits, mock_list_branches, mock_patch_ids,
        mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        """A branch commit whose patch is already on main (rebased copy) is
        dropped; the same patch on two branches counts once account-wide."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log  # 2 commits on main
        mock_tags.return_value = []
        mock_list_branches.return_value = [
            {"name": "feat/a", "commit_count": 2},
            {"name": "feat/b", "commit_count": 1},
        ]
        mock_branch_commits.side_effect = [
            [self._branch_commit("a1", "on main too"), self._branch_commit("a2", "new")],
            [self._branch_commit("b1", "same patch as a2")],
        ]

        def patch_ids(path, revs, since, until, **kwargs):
            if revs == ["origin/main"]:
                return {"abc1234": "P-main-1", "def5678": "P-main-2", "m3": "P-shared"}
            if revs[0] == "origin/feat/a":
                return {"a1": "P-shared", "a2": "P-new"}
            return {"b1": "P-new"}
        mock_patch_ids.side_effect = patch_ids
        MockClient.return_value = self._client([])
        mock_config.reports_dir = str(tmp_path)

        result = run_activity(
            mock_config, mock_config.accounts[0],
            since="2025-06-01", until="2025-06-30", include_branches=True,
        )
        repo = result.repos[0]
        # a1 dropped (already on main); a2 kept; b1 kept per-branch but is
        # the same patch as a2 so counts once in the account-wide list.
        assert {b.name: [c.sha for c in b.commits] for b in repo.branches} == {
            "feat/a": ["a2"], "feat/b": ["b1"],
        }
        assert [c.sha for c in repo.branch_commits] == ["a2"]
        assert result.total_commits == 3  # 2 on main + 1 unique branch commit


class TestDeveloperAliases:
    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_name_alias_folds_commits_into_login(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        """'Mia Lindberg' commits (no resolvable email) merge into the
        miaLind login row alongside her PRs, and Developer Detail lists
        the commits under that login."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [
            {"sha": "m1", "author_name": "Mia Lindberg", "author_email": "mia@laptop.example",
             "date": "2025-06-02", "subject": "Fix thing", "additions": 5, "deletions": 1},
        ]
        mock_tags.return_value = []
        client = _ok_client()
        client.list_pulls.return_value = [
            {"number": 9, "title": "Fix thing", "user": {"login": "miaLind"},
             "state": "closed", "merged_at": "2025-06-03T00:00:00Z",
             "created_at": "2025-06-02T00:00:00Z", "updated_at": "2025-06-03T00:00:00Z",
             "html_url": "https://github.com/testuser/my-repo/pull/9"},
        ]
        MockClient.return_value = client
        mock_config.reports_dir = str(tmp_path)
        mock_config.developer_aliases = {"miaLind": ["Mia Lindberg"]}

        result = run_activity(
            mock_config, mock_config.accounts[0], since="2025-06-01", until="2025-06-30",
        )
        assert len(result.developers) == 1
        mia = result.developers[0]
        assert mia.github_login == "miaLind"
        assert (mia.commits, mia.prs_merged) == (1, 1)
        assert result.email_to_login["mia@laptop.example"] == "miaLind"

        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "### miaLind" in report
        assert "| 2025-06-02 | my-repo | [m1]" in report
        assert "### Mia Lindberg" not in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_email_alias(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [
            {"sha": "m1", "author_name": "M", "author_email": "mia@Laptop.example",
             "date": "2025-06-02", "subject": "x", "additions": 5, "deletions": 1},
        ]
        mock_tags.return_value = []
        MockClient.return_value = _ok_client()
        mock_config.reports_dir = str(tmp_path)
        mock_config.developer_aliases = {"miaLind": ["mia@laptop.example"]}

        result = run_activity(
            mock_config, mock_config.accounts[0], since="2025-06-01", until="2025-06-30",
        )
        assert [d.github_login for d in result.developers] == ["miaLind"]


class TestProfileNameLinking:
    """Commits from a git email GitHub doesn't link to the account join the
    login whose profile name is the commit author's name (simon777's
    commits as "Simon Calder" <simoncalder@...>), without config."""

    def _run(self, MockClient, mock_tags, mock_git_log, mock_scan, mock_config,
             mock_local_repo, tmp_path, profile, author="Simon Calder"):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [
            {"sha": "s1", "author_name": author, "author_email": "simon@laptop.example",
             "date": "2025-06-02", "subject": "Add page", "additions": 7, "deletions": 2},
        ]
        mock_tags.return_value = []
        client = _ok_client()
        client.list_pulls.return_value = [
            {"number": 4, "title": "Add page", "user": {"login": "simon777"},
             "state": "closed", "merged_at": "2025-06-03T00:00:00Z",
             "created_at": "2025-06-02T00:00:00Z", "updated_at": "2025-06-03T00:00:00Z",
             "html_url": "https://github.com/testuser/my-repo/pull/4"},
        ]
        if isinstance(profile, Exception):
            client.get_user.side_effect = profile
        else:
            client.get_user.return_value = profile
        MockClient.return_value = client
        mock_config.reports_dir = str(tmp_path)
        result = run_activity(
            mock_config, mock_config.accounts[0], since="2025-06-01", until="2025-06-30",
        )
        return result, client

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_commits_join_the_login_with_that_profile_name(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        result, client = self._run(
            MockClient, mock_tags, mock_git_log, mock_scan, mock_config, mock_local_repo,
            tmp_path, {"login": "simon777", "name": "Simon Calder", "type": "User"},
        )
        client.get_user.assert_called_once_with("simon777")
        assert len(result.developers) == 1
        dev = result.developers[0]
        assert dev.github_login == "simon777"
        assert (dev.commits, dev.additions, dev.prs_merged) == (1, 7, 1)
        assert dev.email == "simon@laptop.example"

        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "### simon777" in report
        assert "| 2025-06-02 | my-repo | [s1]" in report
        assert "### Simon Calder" not in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_a_one_word_profile_name_is_too_generic(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        result, _ = self._run(
            MockClient, mock_tags, mock_git_log, mock_scan, mock_config, mock_local_repo,
            tmp_path, {"login": "simon777", "name": "simon", "type": "User"},
            author="simon",
        )
        assert sorted(d.github_login for d in result.developers) == ["", "simon777"]

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_a_different_name_is_not_linked(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        result, _ = self._run(
            MockClient, mock_tags, mock_git_log, mock_scan, mock_config, mock_local_repo,
            tmp_path, {"login": "simon777", "name": "Simon Calder", "type": "User"},
            author="Simeon Cald",
        )
        assert len(result.developers) == 2

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_a_failed_profile_lookup_is_not_fatal(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        result, _ = self._run(
            MockClient, mock_tags, mock_git_log, mock_scan, mock_config, mock_local_repo,
            tmp_path, httpx.ConnectError("down"),
        )
        assert len(result.developers) == 2
        assert (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").exists()

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_logins_with_linked_commits_are_not_looked_up(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_config, mock_local_repo, tmp_path,
    ):
        """No profile request for someone whose commits already resolve."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [
            {"sha": "a1", "author_name": "Alice",
             "author_email": "1+alice@users.noreply.github.com",
             "date": "2025-06-02", "subject": "x", "additions": 1, "deletions": 0},
        ]
        mock_tags.return_value = []
        client = _ok_client()
        client.list_pulls.return_value = [
            {"number": 1, "title": "x", "user": {"login": "alice"}, "state": "open",
             "merged_at": None, "created_at": "2025-06-02T00:00:00Z",
             "updated_at": "2025-06-02T00:00:00Z",
             "html_url": "https://github.com/testuser/my-repo/pull/1"},
        ]
        MockClient.return_value = client
        mock_config.reports_dir = str(tmp_path)
        run_activity(mock_config, mock_config.accounts[0], since="2025-06-01", until="2025-06-30")
        client.get_user.assert_not_called()

    def test_bots_and_shared_names_are_left_out(self):
        from auditer.commands.data.activity import (
            _AccountScan,
            _fetch_profile_names,
            _profile_name_lookup,
        )

        account = AccountConfig(slug="a", type="org", username="a")
        client = MagicMock()
        client.get_user.side_effect = lambda login: {
            "Copilot": {"login": "Copilot", "name": "Copilot Agent", "type": "Bot"},
            "jo1": {"login": "jo1", "name": "Jo Smith", "type": "User"},
            "jo2": {"login": "jo2", "name": "jo-smith", "type": "User"},
            "ann": {"login": "ann", "name": "Ann Lee", "type": "User"},
            "gone": None,
        }[login]
        names = _fetch_profile_names(client, account, ["Copilot", "jo1", "jo2", "ann", "gone"])
        assert names == {"jo1": "Jo Smith", "ann": "Ann Lee"}

        scan = _AccountScan(
            account=account, repos=[], email_to_login={},
            profile_names={**names, "jo2": "Jo  Smith"},
        )
        # "Jo Smith" and "Jo  Smith" normalise alike: two logins, so neither is used
        assert _profile_name_lookup([scan]) == {"annlee": "ann"}

    def test_branch_rows_are_linked_too(self):
        """The Branch Activity row names the login, not the git author."""
        from auditer.commands.data.activity import _AccountScan, _build_result
        from auditer.models import BranchActivity, DeveloperActivity, RepoActivity
        from auditer.timewindow import DateWindow

        def dev(name, email, login=""):
            return DeveloperActivity(
                name=name, email=email, commits=1, additions=1, deletions=0,
                prs_opened=0, prs_merged=0, issues_opened=0, issues_closed=0,
                review_comments=0, github_login=login,
            )

        branch_dev = dev("Simon Calder", "s@laptop.example")
        repo = RepoActivity(
            name="r", owner="o", commits=[], prs_opened=1, prs_merged=0,
            issues_opened=0, issues_closed=0, review_comments=0,
            developers=[dev("simon777", "simon777@github", "simon777")],
            branches=[BranchActivity(name="feat/x", repo_name="r", commits=[],
                                     developers=[branch_dev])],
            branch_developers=[dev("Simon Calder", "s@laptop.example")],
        )
        scan = _AccountScan(
            account=AccountConfig(slug="a", type="org", username="a"), repos=[repo],
            email_to_login={}, profile_names={"simon777": "Simon Calder"},
        )
        window = DateWindow.from_dates("2025-06-01", "2025-06-30", "Europe/London")

        result = _build_result([scan], window, None, {})

        assert branch_dev.github_login == "simon777"
        assert [d.github_login for d in result.developers] == ["simon777"]

    def test_bitbucket_accounts_are_not_looked_up(self):
        from auditer.commands.data.activity import _fetch_profile_names

        client = MagicMock()
        account = AccountConfig(slug="b", type="org", username="b", provider="bitbucket")
        assert _fetch_profile_names(client, account, ["someone"]) == {}
        client.get_user.assert_not_called()


class TestPRReviewHelpers:
    PR = {
        "number": 7,
        "title": "Fix parser",
        "user": {"login": "alice"},
        "html_url": "https://github.com/testuser/my-repo/pull/7",
        "created_at": "2025-05-20T09:00:00Z",
        "updated_at": "2025-06-03T09:00:00Z",
    }

    def test_pr_may_have_reviews_in_range(self):
        from auditer.commands.data.activity import _pr_may_have_reviews_in_range

        assert _pr_may_have_reviews_in_range(self.PR, "2025-06-01", "2025-06-30")
        # Last touched before the range: no review can be in range
        # (22:00Z is 23:00 BST on 31 May)
        assert not _pr_may_have_reviews_in_range(
            {**self.PR, "updated_at": "2025-05-31T22:00:00Z"}, "2025-06-01", "2025-06-30",
        )
        # 23:00Z on 31 May is 00:00 on 1 June in London: inside the window
        assert _pr_may_have_reviews_in_range(
            {**self.PR, "updated_at": "2025-05-31T23:00:00Z"}, "2025-06-01", "2025-06-30",
        )
        # Opened after the range
        assert not _pr_may_have_reviews_in_range(
            {**self.PR, "created_at": "2025-07-01T00:00:00Z", "updated_at": "2025-07-02T00:00:00Z"},
            "2025-06-01", "2025-06-30",
        )
        # No updated_at at all (malformed / unknown) — skip rather than guess
        assert not _pr_may_have_reviews_in_range(
            {k: v for k, v in self.PR.items() if k != "updated_at"}, "2025-06-01", "2025-06-30",
        )

    def test_build_review_records_filters(self):
        from auditer.commands.data.activity import _build_review_records

        def rv(login, state, submitted="2025-06-02T10:00:00Z", body="Some feedback"):
            return {"user": {"login": login}, "state": state, "submitted_at": submitted, "body": body}

        raw = [
            rv("bob", "APPROVED", body=""),
            rv("carol", "CHANGES_REQUESTED"),
            rv("bob", "COMMENTED"),
            rv("bob", "COMMENTED", body="  "),  # inline-thread reply: skipped
            {"user": {"login": "bob"}, "state": "PENDING", "body": "draft"},  # unsubmitted
            rv("alice", "COMMENTED"),  # PR author replying on own PR
            rv("bob", "APPROVED", submitted="2025-07-01T10:00:00Z"),  # out of range
            rv("dave", "DISMISSED"),
        ]
        records = _build_review_records(self.PR, raw, "my-repo", "2025-06-01", "2025-06-30")

        assert [(r.reviewer, r.state) for r in records] == [
            ("bob", "approved"),
            ("carol", "changes_requested"),
            ("bob", "commented"),
            ("dave", "dismissed"),
        ]
        first = records[0]
        assert first.pr_number == 7
        assert first.pr_author == "alice"
        assert first.submitted_at == "2025-06-02"
        assert first.url == "https://github.com/testuser/my-repo/pull/7"
        assert first.repo_name == "my-repo"


class TestPRReviewsActivity:
    def _config(self, tmp_path):
        account = AccountConfig(
            slug="test-user", type="user", username="testuser",
            token="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
        )
        return AppConfig(
            accounts=[account], stale_months=12, data_dir=str(tmp_path / "data"),
            reports_dir=str(tmp_path / "reports"), log_level="INFO",
        )

    def _client(self):
        client_instance = MagicMock()
        client_instance.validate_token.return_value = "testuser"
        client_instance.list_pulls.return_value = [
            {
                "number": 7, "title": "Fix parser", "user": {"login": "alice"},
                "state": "open", "merged_at": None,
                "created_at": "2025-05-20T09:00:00Z", "updated_at": "2025-06-03T09:00:00Z",
                "html_url": "https://github.com/testuser/my-repo/pull/7",
            },
            {
                "number": 3, "title": "Old work", "user": {"login": "alice"},
                "state": "closed", "merged_at": "2025-04-01T00:00:00Z",
                "created_at": "2025-03-01T09:00:00Z", "updated_at": "2025-04-01T00:00:00Z",
                "html_url": "https://github.com/testuser/my-repo/pull/3",
            },
        ]
        client_instance.list_pull_reviews.return_value = [
            {"user": {"login": "bob"}, "state": "CHANGES_REQUESTED",
             "submitted_at": "2025-06-02T10:00:00Z", "body": "Needs a test"},
            {"user": {"login": "bob"}, "state": "APPROVED",
             "submitted_at": "2025-06-03T09:00:00Z", "body": ""},
        ]
        client_instance.list_repo_issues.return_value = []
        client_instance.list_review_comments.return_value = []
        client_instance.list_repo_commits.return_value = []
        return client_instance

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_reviews_counted_and_only_touched_prs_fetched(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        client_instance = self._client()
        MockClient.return_value = client_instance
        config = self._config(tmp_path)

        result = run_activity(
            config, config.accounts[0], since="2025-06-01", until="2025-06-30",
        )

        # PR #3 was last updated before the range, so its reviews aren't fetched
        client_instance.list_pull_reviews.assert_called_once_with("testuser", "my-repo", 7)
        repo = result.repos[0]
        assert (repo.reviews_submitted, repo.reviews_approved) == (2, 1)
        bob = next(d for d in result.developers if d.github_login == "bob")
        assert (bob.reviews_submitted, bob.reviews_approved) == (2, 1)
        assert result.total_reviews == 2

        report = (tmp_path / "reports" / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "| PR reviews submitted | 2 |" in report
        assert "| PR approvals | 1 |" in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_user_filter_keeps_reviewer_with_no_commits(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        mock_local_repo, sample_commit_log, tmp_path,
    ):
        """A repo where the filtered user only reviewed (no commits/PRs of
        their own) must still appear, with just their reviews."""
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = [c for c in sample_commit_log if c["author_name"] == "Alice"]
        mock_tags.return_value = []
        MockClient.return_value = self._client()
        config = self._config(tmp_path)

        result = run_activity(
            config, config.accounts[0], since="2025-06-01", until="2025-06-30",
            user_filter="bob",
        )

        assert len(result.repos) == 1
        repo = result.repos[0]
        assert repo.commits == []
        assert repo.prs == []
        assert [r.reviewer for r in repo.review_records] == ["bob", "bob"]
        assert repo.reviews_submitted == 2
        assert [d.github_login for d in result.developers] == ["bob"]

        report = (
            tmp_path / "reports" / "test-user" / "activity_2025-06-01_2025-06-30_user-bob.md"
        ).read_text()
        assert "**Reviews** (oldest first):" in report
        assert "| alice | Changes requested |" in report


class TestLocalOnly:
    """Reports from local checkouts alone, never touching the API."""

    def _run(self, runner, mock_config, extra_args=()):
        return _invoke(
            runner,
            ["activity", "--account", "test-user",
             "--since", "2025-06-01", "--until", "2025-06-30", *extra_args],
            mock_config,
        )

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.commands.data.activity.get_provider")
    def test_flag_skips_api_entirely(
        self, mock_provider, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        mock_config.reports_dir = str(tmp_path)

        result = self._run(runner, mock_config, ["--local-only"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        mock_provider.assert_not_called()
        assert "Local git only (--local-only)" in output
        assert "Data source:           local git only (--local-only)" in output
        assert "Total commits:         2" in output

        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "**Data source:** Local git only (--local-only)." in report
        assert "| Total commits | 2 |" in report
        assert "Data quality" not in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.commands.data.activity.get_provider")
    def test_account_config_local_only(
        self, mock_provider, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        mock_config.reports_dir = str(tmp_path)
        mock_config.accounts[0].local_only = True

        result = self._run(runner, mock_config)
        assert result.exit_code == 0
        mock_provider.assert_not_called()
        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "**Data source:** Local git only (local_only: true in config)." in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_auth_failure_falls_back_to_local(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        """A revoked token (engagement over) no longer aborts with exit 2;
        the report is built from git and says why the API data is missing."""
        from auditer.github_client import AuthenticationError

        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        client_instance = _ok_client()
        client_instance.validate_token.side_effect = AuthenticationError("token revoked", status_code=401)
        MockClient.return_value = client_instance
        mock_config.reports_dir = str(tmp_path)

        result = self._run(runner, mock_config)
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Authentication failed: token revoked" in output
        assert "Local git only (API authentication failed: token revoked)" in output
        client_instance.close.assert_called_once()
        client_instance.list_pulls.assert_not_called()
        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "**Data source:** Local git only (API authentication failed: token revoked)." in report
        assert "| Total commits | 2 |" in report

    @patch("auditer.commands.data.activity.scan_local_repos")
    @patch("auditer.commands.data.activity.get_commit_log")
    @patch("auditer.commands.data.activity.get_tags_in_range")
    @patch("auditer.github_client.GitHubClient")
    def test_api_run_has_no_data_source_line(
        self, MockClient, mock_tags, mock_git_log, mock_scan,
        runner, mock_config, mock_local_repo, sample_commit_log, tmp_path,
    ):
        mock_scan.return_value = [mock_local_repo]
        mock_git_log.return_value = sample_commit_log
        mock_tags.return_value = []
        MockClient.return_value = _ok_client()
        mock_config.reports_dir = str(tmp_path)

        result = self._run(runner, mock_config)
        assert "Local git only" not in strip_ansi(result.output)
        report = (tmp_path / "test-user" / "activity_2025-06-01_2025-06-30.md").read_text()
        assert "**Data source:**" not in report


def _local_repo(name: str, owner: str = "testuser", slug: str = "test-user") -> LocalRepoInfo:
    return LocalRepoInfo(
        name=name, path=Path(f"/data/{slug}/public/{name}"), private=False,
        owner=owner, clone_url=f"https://github.com/{owner}/{name}.git",
        has_readme=True, has_license_file=True, default_branch="main",
        pushed_at="2026-10-12T12:00:00+00:00", size_kb=1,
    )


def _pr(number, author, created, merged=None, closed=None, state=None, **extra):
    return {
        "number": number, "title": f"PR {number}", "user": {"login": author},
        "created_at": created, "updated_at": merged or closed or created,
        "merged_at": merged, "closed_at": closed or merged,
        "state": state or ("closed" if (merged or closed) else "open"),
        "html_url": f"https://github.com/testuser/my-repo/pull/{number}",
        "head": {"ref": f"b{number}"}, **extra,
    }


class TestAccuracyFixes:
    """PR states, merges, PR comments and the London window, end to end."""

    def _run(self, mock_config, tmp_path, client, commits=None, merges=None, **kwargs):
        mock_config.reports_dir = str(tmp_path)
        with patch("auditer.commands.data.activity.scan_local_repos",
                   return_value=[_local_repo("my-repo")]), \
             patch("auditer.commands.data.activity.get_commit_log",
                   return_value=commits or []), \
             patch("auditer.commands.data.activity.get_tags_in_range", return_value=[]), \
             patch("auditer.commands.data.activity.get_merge_commits",
                   return_value=merges or []), \
             patch("auditer.github_client.GitHubClient", return_value=client):
            return run_activity(
                mock_config, mock_config.accounts[0],
                since="2026-10-10", until="2026-10-16", **kwargs,
            )

    def _dev(self, result, login):
        return next(d for d in result.developers if d.github_login == login)

    def test_pr_states_at_end_of_window(self, mock_config, tmp_path):
        client = _ok_client()
        client.list_pulls.return_value = [
            # closed without merging in the window (example-web #5785)
            _pr(5785, "tom", "2026-10-14T10:00:00Z", closed="2026-10-14T12:00:00Z"),
            # opened before the window, still open, touched in it (#5585, #5336)
            _pr(5585, "tom", "2026-10-09T10:00:00Z", updated_at="2026-10-15T10:00:00Z"),
            _pr(5336, "tom", "2026-09-10T10:00:00Z", updated_at="2026-10-12T10:00:00Z"),
            # open but untouched since before the window: not in-flight work
            _pr(4000, "gone", "2026-03-01T10:00:00Z", updated_at="2026-03-02T10:00:00Z"),
        ]
        result = self._run(mock_config, tmp_path, client)
        tom = self._dev(result, "tom")
        assert (tom.prs_open, tom.prs_closed, tom.prs_opened) == (2, 1, 1)
        states = {p.number: p.state for p in result.repos[0].prs}
        assert states == {5785: "closed", 5585: "open", 5336: "open"}
        assert result.repos[0].prs_open == 2
        assert "gone" not in {d.github_login for d in result.developers}
        report = (tmp_path / "test-user" / "activity_2026-10-10_2026-10-16.md").read_text()
        assert "| PRs closed without merging | 1 |" in report
        assert "| PRs still open at end of window | 2 |" in report

    def test_merged_by_fetched_once_per_merged_pr_and_credited(self, mock_config, tmp_path):
        client = _ok_client()
        client.list_pulls.side_effect = lambda owner, repo, **kw: [] if kw.get("state") else [
            _pr(355, "sam", "2026-10-13T10:00:00Z", merged="2026-10-15T14:38:17Z"),
            _pr(9, "sam", "2026-10-01T10:00:00Z", merged="2026-10-14T08:36:49Z",
                merged_by={"login": "tom"}),           # already known: no request
            _pr(1, "sam", "2026-09-01T10:00:00Z", merged="2026-10-01T10:00:00Z"),  # outside
        ]
        client.get_pull.return_value = {"merged_by": {"login": "tom"}}
        result = self._run(mock_config, tmp_path, client)
        client.get_pull.assert_called_once_with("testuser", "my-repo", 355)
        assert self._dev(result, "tom").prs_merged_by == 2
        assert self._dev(result, "sam").prs_merged == 2
        report = (tmp_path / "test-user" / "activity_2026-10-10_2026-10-16.md").read_text()
        assert "**PRs merged** (by them, any author):" in report

    def test_pr_conversation_comments_counted_separately(self, mock_config, tmp_path):
        client = _ok_client()
        client.list_issue_comments.return_value = [
            {"user": {"login": "tom"}, "created_at": "2026-10-12T11:35:47Z",
             "html_url": "https://github.com/o/r/pull/5#issuecomment-1"},
            # on an issue, not a PR
            {"user": {"login": "tom"}, "created_at": "2026-10-12T11:35:47Z",
             "html_url": "https://github.com/o/r/issues/6#issuecomment-2"},
            # 22:30Z on the 9th is 23:30 BST: before the window
            {"user": {"login": "tom"}, "created_at": "2026-10-09T22:30:00Z",
             "html_url": "https://github.com/o/r/pull/5#issuecomment-3"},
        ]
        client.list_review_comments.return_value = [
            {"user": {"login": "tom"}, "created_at": "2026-10-16T10:32:10Z"},
        ]
        result = self._run(mock_config, tmp_path, client)
        tom = self._dev(result, "tom")
        assert (tom.pr_comments, tom.review_comments) == (1, 1)
        assert result.repos[0].pr_comments == 1

    def test_merge_commits_credited_without_lines(self, mock_config, tmp_path):
        commit = {"sha": "c1", "author_name": "Tom", "author_email": "tom@x",
                  "date": "2026-10-12", "subject": "work", "additions": 10, "deletions": 1,
                  "excluded_additions": 500, "excluded_deletions": 0}
        merge = {"sha": "m1", "author_name": "Tom", "author_email": "tom@x",
                 "date": "2026-10-13", "subject": "Merge pull request #1",
                 "additions": 0, "deletions": 0}
        result = self._run(mock_config, tmp_path, _ok_client(), commits=[commit], merges=[merge])
        [tom] = result.developers
        assert (tom.commits, tom.merge_commits, tom.additions) == (1, 1, 10)
        assert tom.excluded_additions == 500
        report = (tmp_path / "test-user" / "activity_2026-10-10_2026-10-16.md").read_text()
        assert "## Data Quality: Lines Excluded From Counts" in report
        assert "| Tom | 500 | 0 |" in report
        assert "| Merge commits (not in commits or lines) | 1 |" in report

    def test_merged_after_window_counts_as_open_not_merged(self, mock_config, tmp_path):
        client = _ok_client()
        # 23:30Z on the 16th is 00:30 BST on the 17th: after the window
        client.list_pulls.side_effect = lambda owner, repo, **kw: [] if kw.get("state") else [
            _pr(7, "tom", "2026-10-12T10:00:00Z", merged="2026-10-16T23:30:00Z"),
        ]
        result = self._run(mock_config, tmp_path, client)
        tom = self._dev(result, "tom")
        assert (tom.prs_merged, tom.prs_open) == (0, 1)
        client.get_pull.assert_not_called()


class TestFetchBeforeScan:
    def _run(self, mock_config, tmp_path, fetch_mock, **kwargs):
        mock_config.reports_dir = str(tmp_path)
        with patch("auditer.commands.data.activity.scan_local_repos",
                   return_value=[_local_repo("my-repo")]), \
             patch("auditer.commands.data.activity.get_commit_log", return_value=[]), \
             patch("auditer.commands.data.activity.get_tags_in_range", return_value=[]), \
             patch("auditer.commands.data.activity.fetch_repo", fetch_mock), \
             patch("auditer.github_client.GitHubClient", return_value=_ok_client()):
            return run_activity(
                mock_config, mock_config.accounts[0],
                since="2026-10-10", until="2026-10-16", **kwargs,
            )

    def test_fetches_each_repo_with_the_account_token(self, mock_config, tmp_path):
        from auditer.git_ops import GitOpResult

        fetch = MagicMock(return_value=GitOpResult(success=True, message="Fetched"))
        result = self._run(mock_config, tmp_path, fetch)
        fetch.assert_called_once()
        assert fetch.call_args.kwargs["token"] == mock_config.accounts[0].token
        assert "git fetched at the start of this run" in result.data_as_of
        assert result.fetch_errors == {}

    def test_no_fetch_skips_it_and_says_so(self, mock_config, tmp_path):
        fetch = MagicMock()
        result = self._run(mock_config, tmp_path, fetch, fetch=False)
        fetch.assert_not_called()
        assert "git fetch skipped (--no-fetch)" in result.data_as_of

    def test_failed_fetch_is_flagged_in_report(self, mock_config, tmp_path):
        from auditer.git_ops import GitOpResult

        fetch = MagicMock(return_value=GitOpResult(success=False, message="Fetch failed: boom"))
        result = self._run(mock_config, tmp_path, fetch)
        assert result.fetch_errors == {"my-repo": "Fetch failed: boom"}
        report = (tmp_path / "test-user" / "activity_2026-10-10_2026-10-16.md").read_text()
        header = report.split("## ")[0]
        assert "**Data as of:**" in header
        assert "**⚠ Stale git data:** git fetch failed for 1 repo(s)" in header
        assert "- **my-repo**: Fetch failed: boom" in header

    def test_local_only_account_is_not_fetched(self, mock_config, tmp_path):
        fetch = MagicMock()
        mock_config.accounts[0].local_only = True
        result = self._run(mock_config, tmp_path, fetch)
        fetch.assert_not_called()
        assert "local_only: true in config" in result.data_as_of


class TestCombinedAccounts:
    @pytest.fixture
    def two_accounts(self, tmp_path):
        org = AccountConfig(slug="acme", type="org", username="acme", token="ghp_" + "a" * 36)
        me = AccountConfig(slug="tom", type="user", username="tomgould", token="ghp_" + "b" * 36)
        return AppConfig(
            accounts=[org, me], stale_months=12, data_dir="data",
            reports_dir=str(tmp_path), log_level="INFO",
        )

    def _commit(self, sha, adds):
        return {"sha": sha, "author_name": "Jane Doe", "author_email": "tom@x",
                "date": "2026-10-12", "subject": sha, "additions": adds, "deletions": 0}

    def _patches(self):
        def scan(data_dir):
            slug = Path(data_dir).name
            owner = "acme" if slug == "acme" else "tomgould"
            name = "shared-name"  # same repo name in both accounts
            return [_local_repo(name, owner=owner, slug=slug)]

        def log(path, since, until, **kw):
            return [self._commit("org1", 10)] if "/acme/" in str(path) else [
                self._commit("me1", 5), self._commit("me2", 1),
            ]

        client = _ok_client()
        client.list_repo_commits.return_value = [
            {"author": {"login": "tomgould"}, "commit": {"author": {"email": "tom@x"}}},
        ]
        return [
            patch("auditer.commands.data.activity.scan_local_repos", side_effect=scan),
            patch("auditer.commands.data.activity.get_commit_log", side_effect=log),
            patch("auditer.commands.data.activity.get_tags_in_range", return_value=[]),
            patch("auditer.github_client.GitHubClient", return_value=client),
        ]

    def test_one_leaderboard_row_across_accounts(self, two_accounts, tmp_path):
        from contextlib import ExitStack

        from auditer.commands.data.activity import run_combined_activity

        with ExitStack() as stack:
            for p in self._patches():
                stack.enter_context(p)
            result = run_combined_activity(
                two_accounts, two_accounts.accounts, since="2026-10-10", until="2026-10-16",
            )
        [tom] = result.developers
        assert (tom.github_login, tom.commits, tom.additions) == ("tomgould", 3, 16)
        assert result.accounts == ["acme", "tom"]
        report_path = (
            tmp_path / "combined" / "activity_2026-10-10_2026-10-16_accounts-acme+tom.md"
        )
        report = report_path.read_text()
        assert "# Developer Activity Report — acme + tom" in report
        assert "**Accounts:** acme, tom (combined)" in report
        assert "## Per-Account Summary" in report
        assert "| acme | 1 | 1 | 0 | 10 |" in report
        assert "| tom | 1 | 2 | 0 | 6 |" in report
        # Same repo name in both accounts: labelled owner/name
        assert "### acme/shared-name" in report and "### tomgould/shared-name" in report

    def test_cli_combines_repeated_account_flags(self, runner, two_accounts):
        with patch("auditer.commands.data.activity.run_combined_activity") as combined, \
             patch("auditer.commands.data.activity.run_activity") as single:
            _invoke(runner, ["activity", "--account", "acme", "--account", "tom",
                             "--since", "2026-10-10", "--until", "2026-10-16"], two_accounts)
        single.assert_not_called()
        assert [a.slug for a in combined.call_args.args[1]] == ["acme", "tom"]

    def test_cli_single_account_unchanged(self, runner, two_accounts):
        with patch("auditer.commands.data.activity.run_combined_activity") as combined, \
             patch("auditer.commands.data.activity.run_activity") as single:
            _invoke(runner, ["activity", "--account", "acme",
                             "--since", "2026-10-10", "--until", "2026-10-16"], two_accounts)
        combined.assert_not_called()
        single.assert_called_once()
        assert single.call_args.kwargs["fetch"] is True

    def test_cli_all_without_combine_is_per_account(self, runner, two_accounts):
        with patch("auditer.commands.data.activity.run_combined_activity") as combined, \
             patch("auditer.commands.data.activity.run_activity") as single:
            _invoke(runner, ["activity", "--all", "--no-fetch"], two_accounts)
        combined.assert_not_called()
        assert single.call_count == 2
        assert single.call_args.kwargs["fetch"] is False

    def test_cli_all_with_combine(self, runner, two_accounts):
        with patch("auditer.commands.data.activity.run_combined_activity") as combined:
            _invoke(runner, ["activity", "--all", "--combine"], two_accounts)
        combined.assert_called_once()

    def test_cli_rejects_bad_date(self, runner, two_accounts):
        result = _invoke(runner, ["activity", "--account", "acme", "--since", "10/10/2026"],
                         two_accounts)
        assert result.exit_code != 0
        assert "YYYY-MM-DD" in strip_ansi(result.output)


class TestDataAsOf:
    def _scan(self, states, skipped="", local_reason=""):
        from auditer.commands.data.activity import _AccountScan

        return _AccountScan(
            account=AccountConfig(slug="a", type="org", username="a"), repos=[],
            email_to_login={}, local_reason=local_reason, fetch_skipped=skipped,
            fetch_states=states,
        )

    def test_stale_clones_reported_separately(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from auditer.commands.data.activity import _describe_data_as_of

        london = ZoneInfo("Europe/London")
        good = datetime(2026, 10, 16, 17, 5, tzinfo=london)
        last_ok = datetime(2026, 10, 12, 9, 8, tzinfo=london)
        line = _describe_data_as_of(
            [self._scan([(good, False), (last_ok, True), (last_ok, True)],
                        skipped="local_only: true in config", local_reason="x")],
            london,
        )
        assert line == (
            "commits and tags as of 2026-10-16 17:05 BST "
            "(git fetch skipped (local_only: true in config)); the most recent git "
            "fetch failed for 2 of 3 repo(s), so their data is from their last "
            "successful fetch: 2026-10-12 09:08 BST"
        )

    def test_fresh_fetch_with_api(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from auditer.commands.data.activity import _describe_data_as_of

        london = ZoneInfo("Europe/London")
        t1 = datetime(2026, 10, 16, 19, 2, tzinfo=london)
        t2 = datetime(2026, 10, 16, 19, 4, tzinfo=london)
        line = _describe_data_as_of([self._scan([(t1, False), (t2, False)])], london)
        assert line == (
            "commits and tags as of 2026-10-16 19:02 BST to 2026-10-16 19:04 BST "
            "(git fetched at the start of this run); PRs, issues and reviews from "
            "the API at report time"
        )


class TestLocalOnlyShowsNotAvailable:
    def test_api_figures_are_na_not_zero(self, mock_config, tmp_path):
        """Without the API, PRs/reviews are unknown — 0 read as 'did none'."""
        commit = {"sha": "c1", "author_name": "Ann", "author_email": "ann@x",
                  "date": "2026-10-12", "subject": "work", "additions": 3, "deletions": 1}
        mock_config.reports_dir = str(tmp_path)
        with patch("auditer.commands.data.activity.scan_local_repos",
                   return_value=[_local_repo("my-repo")]), \
             patch("auditer.commands.data.activity.get_commit_log", return_value=[commit]), \
             patch("auditer.commands.data.activity.get_tags_in_range", return_value=[]), \
             patch("auditer.commands.data.activity.get_provider") as provider:
            result = run_activity(
                mock_config, mock_config.accounts[0],
                since="2026-10-10", until="2026-10-16", local_only=True,
            )
        provider.assert_not_called()
        assert result.api_available is False
        report = (tmp_path / "test-user" / "activity_2026-10-10_2026-10-16.md").read_text()
        assert "| PRs merged | n/a (local git only) |" in report
        assert "| PR reviews submitted | n/a (local git only) |" in report
        assert "| Total commits | 1 |" in report
        # Leaderboard: git columns are numbers, API columns n/a, tags a number
        na8 = " | ".join(["n/a"] * 8)
        assert f"| Ann | 1 | 0 | 3 | 1 | 2 | {na8} | 0 |" in report
        assert "| my-repo | 1 | 3 | 1 | 2 | n/a | n/a | n/a | n/a | 0 | Ann |" in report
        assert "n/a: not available" in report

    def test_api_report_keeps_numbers(self, mock_config, tmp_path):
        mock_config.reports_dir = str(tmp_path)
        with patch("auditer.commands.data.activity.scan_local_repos",
                   return_value=[_local_repo("my-repo")]), \
             patch("auditer.commands.data.activity.get_commit_log", return_value=[]), \
             patch("auditer.commands.data.activity.get_tags_in_range", return_value=[]), \
             patch("auditer.github_client.GitHubClient", return_value=_ok_client()):
            result = run_activity(
                mock_config, mock_config.accounts[0], since="2026-10-10", until="2026-10-16",
            )
        assert result.api_available is True
        report = (tmp_path / "test-user" / "activity_2026-10-10_2026-10-16.md").read_text()
        assert "| PRs merged | 0 |" in report
        assert "n/a" not in report
