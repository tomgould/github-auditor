from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from click.testing import CliRunner

from auditer.cli import cli
from auditer.config import load_config_from_dict
from auditer.models import AccessResult, RepoAccess
from auditer.reports.access import generate_access_report

from tests.conftest import strip_ansi


# --- Fixtures ---


@pytest.fixture
def config_dict(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "reports_dir": str(tmp_path / "reports"),
            "data_dir": str(tmp_path / "data"),
        },
        "accounts": [
            {
                "slug": "test",
                "type": "user",
                "username": "testuser",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
            }
        ],
    }


@pytest.fixture
def config(config_dict):
    return load_config_from_dict(config_dict)


@pytest.fixture
def multi_account_config_dict(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "reports_dir": str(tmp_path / "reports"),
            "data_dir": str(tmp_path / "data"),
        },
        "accounts": [
            {
                "slug": "acct1",
                "type": "user",
                "username": "user1",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
            },
            {
                "slug": "acct2",
                "type": "user",
                "username": "user2",
                "token": "ghp_testtoken9876543210abcdefghijklmnopq",
            },
        ],
    }


def _mock_repo(name, private=False, owner="testuser"):
    return {
        "name": name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{owner}/{name}",
        "private": private,
        "fork": False,
        "description": "",
        "language": "Python",
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "license": None,
        "pushed_at": "2025-06-01T12:00:00Z",
        "archived": False,
        "topics": [],
        "default_branch": "main",
        "size": 100,
    }


def _mock_collaborator(login, admin=False, maintain=False, push=False, triage=False, pull=True, id=0):
    return {
        "login": login,
        "id": id,
        "permissions": {
            "admin": admin,
            "maintain": maintain,
            "push": push,
            "triage": triage,
            "pull": pull,
        },
    }


# --- CLI tests ---


class TestAccessCmd:
    def test_no_account_or_all(self, config_dict, tmp_path):
        """Neither --account nor --all should error."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: test\n"
                "    type: user\n"
                "    username: testuser\n"
                "    token: ghp_testtoken1234567890abcdefghijklmnopq\n"
            )
            result = runner.invoke(cli, ["access"])
        assert result.exit_code != 0
        assert "Specify --account SLUG or --all" in strip_ansi(result.output)

    @respx.mock
    def test_single_account(self, config_dict, tmp_path):
        """Single account with 2 repos, verify report written + terminal summary."""
        runner = CliRunner()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200, json=[_mock_repo("repo-a"), _mock_repo("repo-b", private=True)]
            )
        )
        respx.get("https://api.github.com/repos/testuser/repo-a/collaborators").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _mock_collaborator("alice", admin=True, maintain=True, push=True, triage=True),
                    _mock_collaborator("bob", push=True, triage=True),
                ],
            )
        )
        respx.get("https://api.github.com/repos/testuser/repo-b/collaborators").mock(
            return_value=httpx.Response(
                200,
                json=[_mock_collaborator("alice", push=True, triage=True)],
            )
        )

        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: test\n"
                "    type: user\n"
                "    username: testuser\n"
                "    token: ghp_testtoken1234567890abcdefghijklmnopq\n"
            )
            result = runner.invoke(cli, ["access", "--account", "test"])

        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "ACCESS SUMMARY" in output
        assert "Total repos:" in output

        # Console should include per-repo tables
        assert "repo-a" in output
        assert "repo-b" in output
        assert "alice" in output
        assert "bob" in output
        assert "admin" in output

        # Report file should exist
        report_path = tmp_path / "reports" / "test" / "access_report.md"
        assert report_path.exists()
        report = report_path.read_text()
        assert "repo-a" in report
        assert "repo-b" in report

    @respx.mock
    def test_sort_id_console_output(self, config_dict, tmp_path):
        """--sort id shows user IDs in console tables."""
        runner = CliRunner()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200, json=[_mock_repo("repo-a")]
            )
        )
        respx.get("https://api.github.com/repos/testuser/repo-a/collaborators").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _mock_collaborator("zara", push=True, id=10),
                    _mock_collaborator("adam", push=True, id=500),
                ],
            )
        )

        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: test\n"
                "    type: user\n"
                "    username: testuser\n"
                "    token: ghp_testtoken1234567890abcdefghijklmnopq\n"
            )
            result = runner.invoke(cli, ["access", "--account", "test", "--sort", "id"])

        output = strip_ansi(result.output)
        assert result.exit_code == 0
        # Both users and their IDs should appear
        assert "zara" in output
        assert "adam" in output
        assert "10" in output
        assert "500" in output

    @respx.mock
    def test_all_accounts(self, multi_account_config_dict, tmp_path):
        """--all processes all configured accounts."""
        runner = CliRunner()

        # Account 1
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "user1"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[_mock_repo("repo1", owner="user1")])
        )
        respx.get("https://api.github.com/repos/user1/repo1/collaborators").mock(
            return_value=httpx.Response(200, json=[_mock_collaborator("user1", admin=True)])
        )

        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: acct1\n"
                "    type: user\n"
                "    username: user1\n"
                "    token: ghp_testtoken1234567890abcdefghijklmnopq\n"
                "  - slug: acct2\n"
                "    type: user\n"
                "    username: user2\n"
                "    token: ghp_testtoken9876543210abcdefghijklmnopq\n"
            )
            result = runner.invoke(cli, ["access", "--all"])

        output = strip_ansi(result.output)
        assert "Access report: acct1" in output
        assert "Access report: acct2" in output

    @respx.mock
    def test_auth_error(self, config_dict, tmp_path):
        """Authentication failure exits with code 2."""
        runner = CliRunner()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )

        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: test\n"
                "    type: user\n"
                "    username: testuser\n"
                "    token: ghp_testtoken1234567890abcdefghijklmnopq\n"
            )
            result = runner.invoke(cli, ["access", "--account", "test"])

        assert result.exit_code == 2
        assert "Authentication failed" in strip_ansi(result.output)

    def test_no_token_errors(self, tmp_path):
        """Account with no token should error."""
        runner = CliRunner()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: notoken\n"
                "    type: user\n"
                "    username: notokenuser\n"
            )
            result = runner.invoke(cli, ["access", "--account", "notoken"])

        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "no token" in output.lower()

    @respx.mock
    def test_excludes_repos(self, config_dict, tmp_path):
        """Excluded repos should be filtered out."""
        runner = CliRunner()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200, json=[_mock_repo("keep-me"), _mock_repo("skip-me")]
            )
        )
        respx.get("https://api.github.com/repos/testuser/keep-me/collaborators").mock(
            return_value=httpx.Response(
                200, json=[_mock_collaborator("testuser", admin=True)]
            )
        )

        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path("config.yaml").write_text(
                f"defaults:\n  reports_dir: {tmp_path / 'reports'}\n"
                f"  data_dir: {tmp_path / 'data'}\n"
                "accounts:\n"
                "  - slug: test\n"
                "    type: user\n"
                "    username: testuser\n"
                "    token: ghp_testtoken1234567890abcdefghijklmnopq\n"
                "    exclude_repos:\n"
                "      - skip-me\n"
            )
            result = runner.invoke(cli, ["access", "--account", "test"])

        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Excluded 1 repo(s)" in output

        report_path = tmp_path / "reports" / "test" / "access_report.md"
        report = report_path.read_text()
        assert "keep-me" in report
        assert "skip-me" not in report


# --- Report generator tests ---


class TestGenerateAccessReport:
    def test_report_has_header(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[],
        )
        report = generate_access_report(result)
        assert "# Access Report" in report
        assert "`testuser`" in report
        assert "Generated on" in report

    def test_matrix_section(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[
                        _mock_collaborator("alice", admin=True, maintain=True, push=True, triage=True),
                        _mock_collaborator("bob", push=True, triage=True),
                    ],
                ),
                RepoAccess(
                    repo_name="repo-b",
                    repo_url="https://github.com/testuser/repo-b",
                    private=True,
                    collaborators=[
                        _mock_collaborator("alice", push=True, triage=True),
                    ],
                ),
            ],
        )
        report = generate_access_report(result)
        assert "## Access Matrix" in report
        # Check both users present
        assert "| alice |" in report
        assert "| bob |" in report
        # Both repos in header
        assert "repo-a" in report
        assert "repo-b" in report

    def test_per_repo_tables(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="my-repo",
                    repo_url="https://github.com/testuser/my-repo",
                    private=False,
                    collaborators=[
                        _mock_collaborator("alice", admin=True, maintain=True, push=True, triage=True),
                    ],
                ),
            ],
        )
        report = generate_access_report(result)
        assert "## Per-Repo Details" in report
        assert "### [my-repo]" in report
        assert "| Collaborator | ID | Admin | Maintain | Push | Triage | Pull |" in report
        assert "| alice | 0 | Yes |" in report

    def test_empty_repos(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[],
        )
        report = generate_access_report(result)
        assert "No repositories found." in report

    def test_private_repo_tagged(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="secret",
                    repo_url="https://github.com/testuser/secret",
                    private=True,
                    collaborators=[_mock_collaborator("alice")],
                ),
            ],
        )
        report = generate_access_report(result)
        assert "_(private)_" in report

    def test_public_repo_tagged(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="open",
                    repo_url="https://github.com/testuser/open",
                    private=False,
                    collaborators=[_mock_collaborator("alice")],
                ),
            ],
        )
        report = generate_access_report(result)
        assert "_(public)_" in report

    def test_highest_permission_in_matrix(self):
        """User with admin should show 'admin' in matrix, not 'push'."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[
                        _mock_collaborator("alice", admin=True, maintain=True, push=True, triage=True),
                    ],
                ),
            ],
        )
        report = generate_access_report(result)
        # In the matrix row, alice should have "admin"
        lines = report.split("\n")
        matrix_lines = [l for l in lines if l.startswith("| alice")]
        assert len(matrix_lines) >= 1
        # The matrix row (first occurrence) should contain admin
        assert "admin" in matrix_lines[0]

    def test_no_collaborators_message(self):
        """Repo with no collaborators shows message."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="empty",
                    repo_url="https://github.com/testuser/empty",
                    private=False,
                    collaborators=[],
                ),
            ],
        )
        report = generate_access_report(result)
        assert "No collaborators." in report

    def test_dash_for_no_access_in_matrix(self):
        """User without access to a repo should show dash in matrix."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[
                        _mock_collaborator("alice", push=True, triage=True),
                    ],
                ),
                RepoAccess(
                    repo_name="repo-b",
                    repo_url="https://github.com/testuser/repo-b",
                    private=False,
                    collaborators=[
                        _mock_collaborator("bob", pull=True),
                    ],
                ),
            ],
        )
        report = generate_access_report(result)
        lines = report.split("\n")
        # Alice row should have push for repo-a, dash for repo-b
        alice_row = [l for l in lines if l.startswith("| alice")][0]
        assert "push" in alice_row
        assert "\u2014" in alice_row
        # Bob row should have dash for repo-a, pull for repo-b
        bob_row = [l for l in lines if l.startswith("| bob")][0]
        assert "pull" in bob_row
        assert "\u2014" in bob_row

    def test_sort_by_id_matrix(self):
        """sort_by='id' orders users by GitHub ID ascending (lower = older)."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[
                        # charlie has lowest ID (oldest account), alice has highest
                        _mock_collaborator("alice", push=True, id=300),
                        _mock_collaborator("bob", push=True, id=100),
                        _mock_collaborator("charlie", push=True, id=50),
                    ],
                ),
            ],
        )
        report = generate_access_report(result, sort_by="id")
        lines = report.split("\n")
        # Find matrix data rows (start with "| <name>")
        user_rows = [l for l in lines if l.startswith("| ") and not l.startswith("| User") and not l.startswith("|---") and not l.startswith("| Collaborator")]
        # Filter to matrix rows only (before Per-Repo section)
        matrix_idx = next(i for i, l in enumerate(lines) if "Access Matrix" in l)
        detail_idx = next(i for i, l in enumerate(lines) if "Per-Repo Details" in l)
        matrix_rows = [l for i, l in enumerate(lines) if matrix_idx < i < detail_idx and l.startswith("| ") and not l.startswith("| User") and not l.startswith("|---")]
        assert len(matrix_rows) == 3
        # charlie (50) should come first, then bob (100), then alice (300)
        assert matrix_rows[0].startswith("| charlie")
        assert matrix_rows[1].startswith("| bob")
        assert matrix_rows[2].startswith("| alice")

    def test_sort_by_id_per_repo_table(self):
        """sort_by='id' also orders per-repo detail tables by ID."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[
                        _mock_collaborator("zara", push=True, id=10),
                        _mock_collaborator("adam", push=True, id=500),
                    ],
                ),
            ],
        )
        report = generate_access_report(result, sort_by="id")
        lines = report.split("\n")
        # Find per-repo detail rows
        detail_idx = next(i for i, l in enumerate(lines) if "Per-Repo Details" in l)
        detail_rows = [l for i, l in enumerate(lines) if i > detail_idx and l.startswith("| ") and not l.startswith("| Collaborator") and not l.startswith("|---")]
        assert len(detail_rows) == 2
        # zara (10) before adam (500) despite alphabetical order being reverse
        assert detail_rows[0].startswith("| zara")
        assert detail_rows[1].startswith("| adam")

    def test_sort_by_name_is_default(self):
        """Default sort is alphabetical by name."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[
                        _mock_collaborator("charlie", push=True, id=1),
                        _mock_collaborator("alice", push=True, id=999),
                    ],
                ),
            ],
        )
        report = generate_access_report(result)
        lines = report.split("\n")
        matrix_idx = next(i for i, l in enumerate(lines) if "Access Matrix" in l)
        detail_idx = next(i for i, l in enumerate(lines) if "Per-Repo Details" in l)
        matrix_rows = [l for i, l in enumerate(lines) if matrix_idx < i < detail_idx and l.startswith("| ") and not l.startswith("| User") and not l.startswith("|---")]
        # alice before charlie alphabetically, despite charlie having lower ID
        assert matrix_rows[0].startswith("| alice")
        assert matrix_rows[1].startswith("| charlie")

    def test_sort_by_id_shows_note(self):
        """sort_by='id' includes a note about sort order."""
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="repo-a",
                    repo_url="https://github.com/testuser/repo-a",
                    private=False,
                    collaborators=[_mock_collaborator("alice", push=True, id=1)],
                ),
            ],
        )
        report = generate_access_report(result, sort_by="id")
        assert "lower ID = older account" in report


# --- Model tests ---


class TestAccessResult:
    def test_unique_users(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(
                    repo_name="r1",
                    repo_url="",
                    private=False,
                    collaborators=[{"login": "alice"}, {"login": "bob"}],
                ),
                RepoAccess(
                    repo_name="r2",
                    repo_url="",
                    private=False,
                    collaborators=[{"login": "bob"}, {"login": "charlie"}],
                ),
            ],
        )
        assert result.unique_users == ["alice", "bob", "charlie"]

    def test_total_access_entries(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
            repos=[
                RepoAccess(repo_name="r1", repo_url="", private=False, collaborators=[{"login": "a"}, {"login": "b"}]),
                RepoAccess(repo_name="r2", repo_url="", private=False, collaborators=[{"login": "a"}]),
            ],
        )
        assert result.total_access_entries == 3

    def test_empty_result(self):
        result = AccessResult(
            account_slug="test",
            account_username="testuser",
        )
        assert result.unique_users == []
        assert result.total_access_entries == 0


class TestBitbucketProviderAccess:
    @respx.mock
    def test_bitbucket_account_access_report(self, tmp_path):
        """Smoke test: access report works end-to-end with a bitbucket-provider account."""
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

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "name": "repo1",
                            "owner": {"nickname": "myworkspace"},
                            "is_private": False,
                            "description": "",
                            "language": "python",
                            "size": 100,
                            "mainbranch": {"name": "main"},
                            "links": {
                                "html": {"href": "https://bitbucket.org/myworkspace/repo1"},
                                "clone": [],
                            },
                            "updated_on": "2025-06-01T12:00:00.000000+00:00",
                        }
                    ]
                },
            )
        )
        respx.get(
            "https://api.bitbucket.org/2.0/workspaces/myworkspace/permissions/repositories"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "permission": "admin",
                            "user": {"nickname": "alice", "account_id": "acc-1"},
                            "repository": {"name": "repo1"},
                        }
                    ]
                },
            )
        )

        from auditer.commands.people.access import run_access_report

        result = run_access_report(config, account)

        assert len(result.repos) == 1
        assert result.repos[0].repo_name == "repo1"
        assert result.repos[0].collaborators[0]["login"] == "alice"

        report_path = tmp_path / "reports" / "bb" / "access_report.md"
        assert report_path.exists()
        assert "alice" in report_path.read_text()


class TestProjectFilter:
    @respx.mock
    def test_project_filters_repos(self, tmp_path):
        """--project filters access report to only repos in the project."""
        config = load_config_from_dict({
            "defaults": {
                "reports_dir": str(tmp_path / "reports"),
                "data_dir": str(tmp_path / "data"),
            },
            "accounts": [
                {
                    "slug": "test",
                    "type": "user",
                    "username": "testuser",
                    "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                },
            ],
            "projects": {
                "my-proj": {"repos": ["test/wanted-repo"]},
            },
        })
        account = config.accounts[0]
        project = config.projects[0]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _mock_repo("wanted-repo"),
                    _mock_repo("other-repo"),
                ],
            )
        )
        respx.get("https://api.github.com/repos/testuser/wanted-repo/collaborators").mock(
            return_value=httpx.Response(
                200, json=[_mock_collaborator("testuser", admin=True)]
            )
        )

        from auditer.commands.people.access import run_access_report
        result = run_access_report(config, account, project=project)

        # Only wanted-repo should be in the result
        assert len(result.repos) == 1
        assert result.repos[0].repo_name == "wanted-repo"

        # Report should be in project directory
        report_path = tmp_path / "reports" / "my-proj" / "access_report.md"
        assert report_path.exists()
        report = report_path.read_text()
        assert "wanted-repo" in report
        assert "other-repo" not in report
