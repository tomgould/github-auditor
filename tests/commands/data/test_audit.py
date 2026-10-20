import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from auditer.commands.data.audit import run_audit
from auditer.config import load_config_from_dict
from auditer.git_ops import LocalRepoInfo


def _make_completed_process(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def config(tmp_path):
    return load_config_from_dict(
        {
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
    )


@pytest.fixture
def account(config):
    return config.accounts[0]


def _setup_local_repo(data_dir: Path, slug: str, name: str, visibility: str = "public"):
    """Create a fake local repo directory with .git, README.md."""
    repo_dir = data_dir / slug / visibility / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir()
    (repo_dir / "README.md").touch()
    return repo_dir


class TestRunAuditAuth:
    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_audit_auth_error(self, mock_run_git, config, account, tmp_path):
        """Authentication failure during audit exits with code 2."""
        _setup_local_repo(Path(config.data_dir), "test", "repo1")

        mock_run_git.side_effect = [
            _make_completed_process(stdout="https://github.com/testuser/repo1.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )

        with pytest.raises(SystemExit) as exc_info:
            run_audit(config, account)
        assert exc_info.value.code == 2


class TestRunAudit:
    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_full_audit_flow(self, mock_run_git, config, account, tmp_path):
        _setup_local_repo(Path(config.data_dir), "test", "repo1")

        # Mock git calls for get_local_repo_metadata
        mock_run_git.side_effect = [
            _make_completed_process(stdout="https://github.com/testuser/repo1.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        # Mock API calls
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/repo1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "repo1",
                    "owner": {"login": "testuser"},
                    "html_url": "https://github.com/testuser/repo1",
                    "description": "Test repo",
                    "language": "Python",
                    "stargazers_count": 10,
                    "forks_count": 2,
                    "open_issues_count": 0,
                    "license": {"spdx_id": "MIT"},
                    "pushed_at": "2025-06-01T12:00:00Z",
                    "fork": False,
                    "archived": False,
                    "topics": [],
                    "default_branch": "main",
                    "size": 100,
                },
            )
        )

        result = run_audit(config, account)

        assert len(result.repos) == 1
        assert result.repos[0].name == "repo1"
        assert result.repos[0].has_readme is True
        assert result.repos[0].description == "Test repo"

        # Check report was written
        report_path = tmp_path / "reports" / "test" / "audit_report.md"
        assert report_path.exists()
        assert "repo1" in report_path.read_text()

    def test_no_local_repos(self, config, account, tmp_path):
        # data_dir exists but no repos inside
        result = run_audit(config, account)

        assert len(result.repos) == 0

    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_api_enrichment_failure_graceful(self, mock_run_git, config, account, tmp_path):
        _setup_local_repo(Path(config.data_dir), "test", "deleted-repo")

        mock_run_git.side_effect = [
            _make_completed_process(stdout="https://github.com/testuser/deleted-repo.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-01-01T00:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 50\n"),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/deleted-repo").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        result = run_audit(config, account)

        assert len(result.repos) == 1
        assert result.repos[0].name == "deleted-repo"
        # API-only fields should have defaults
        assert result.repos[0].stars == 0
        assert result.repos[0].description == ""

    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_exclude_repos_applied(self, mock_run_git, config, account, tmp_path):
        account.exclude_repos = ["skip-me"]
        _setup_local_repo(Path(config.data_dir), "test", "keep-me")
        _setup_local_repo(Path(config.data_dir), "test", "skip-me")

        # Only keep-me should be scanned — 4 git calls for each repo found by scan
        mock_run_git.side_effect = [
            # keep-me
            _make_completed_process(stdout="https://github.com/testuser/keep-me.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
            # skip-me
            _make_completed_process(stdout="https://github.com/testuser/skip-me.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/keep-me").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "keep-me",
                    "owner": {"login": "testuser"},
                    "html_url": "https://github.com/testuser/keep-me",
                    "description": "Kept",
                    "language": "Python",
                    "stargazers_count": 0,
                    "forks_count": 0,
                    "open_issues_count": 0,
                    "license": None,
                    "pushed_at": "2025-06-01T12:00:00Z",
                    "fork": False,
                    "archived": False,
                    "topics": [],
                    "default_branch": "main",
                    "size": 100,
                },
            )
        )

        result = run_audit(config, account)

        assert len(result.repos) == 1
        assert result.repos[0].name == "keep-me"

    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_per_account_data_dir(self, mock_run_git, config, account, tmp_path):
        custom_dir = tmp_path / "custom"
        account.data_dir = str(custom_dir)
        _setup_local_repo(custom_dir, "test", "custom-repo")

        mock_run_git.side_effect = [
            _make_completed_process(stdout="https://github.com/testuser/custom-repo.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/custom-repo").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "custom-repo",
                    "owner": {"login": "testuser"},
                    "html_url": "https://github.com/testuser/custom-repo",
                    "description": "Custom",
                    "language": "Go",
                    "stargazers_count": 5,
                    "forks_count": 0,
                    "open_issues_count": 0,
                    "license": None,
                    "pushed_at": "2025-06-01T12:00:00Z",
                    "fork": False,
                    "archived": False,
                    "topics": [],
                    "default_branch": "main",
                    "size": 200,
                },
            )
        )

        result = run_audit(config, account)

        assert len(result.repos) == 1
        assert result.repos[0].name == "custom-repo"
        assert result.repos[0].language == "Go"


class TestProjectFilter:
    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_project_filters_repos(self, mock_run_git, tmp_path):
        """--project filters audit to only repos in the project."""
        config = load_config_from_dict(
            {
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
                "projects": {
                    "my-proj": {"repos": ["test/wanted-repo"]},
                },
            }
        )
        account = config.accounts[0]
        project = config.projects[0]

        # Set up two local repos — only wanted-repo is in the project
        _setup_local_repo(Path(config.data_dir), "test", "wanted-repo")
        _setup_local_repo(Path(config.data_dir), "test", "other-repo")

        # scan_local_repos scans all dirs; 4 git calls per repo
        mock_run_git.side_effect = [
            # other-repo (alphabetical scan order)
            _make_completed_process(stdout="https://github.com/testuser/other-repo.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
            # wanted-repo
            _make_completed_process(stdout="https://github.com/testuser/wanted-repo.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/wanted-repo").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "wanted-repo",
                    "owner": {"login": "testuser"},
                    "html_url": "https://github.com/testuser/wanted-repo",
                    "description": "Wanted",
                    "language": "Python",
                    "stargazers_count": 0,
                    "forks_count": 0,
                    "open_issues_count": 0,
                    "license": None,
                    "pushed_at": "2025-06-01T12:00:00Z",
                    "fork": False,
                    "archived": False,
                    "topics": [],
                    "default_branch": "main",
                    "size": 100,
                },
            )
        )

        result = run_audit(config, account, project=project)

        # Only wanted-repo should be in the result
        assert len(result.repos) == 1
        assert result.repos[0].name == "wanted-repo"

    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_project_reports_dir(self, mock_run_git, tmp_path):
        """When project is active, report is written to reports/{project-name}/."""
        config = load_config_from_dict(
            {
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
                "projects": {
                    "my-proj": {"repos": ["test/repo1"]},
                },
            }
        )
        account = config.accounts[0]
        project = config.projects[0]

        _setup_local_repo(Path(config.data_dir), "test", "repo1")

        mock_run_git.side_effect = [
            _make_completed_process(stdout="https://github.com/testuser/repo1.git\n"),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/repo1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "repo1",
                    "owner": {"login": "testuser"},
                    "html_url": "https://github.com/testuser/repo1",
                    "description": "Test",
                    "language": "Python",
                    "stargazers_count": 0,
                    "forks_count": 0,
                    "open_issues_count": 0,
                    "license": None,
                    "pushed_at": "2025-06-01T12:00:00Z",
                    "fork": False,
                    "archived": False,
                    "topics": [],
                    "default_branch": "main",
                    "size": 100,
                },
            )
        )

        run_audit(config, account, project=project)

        # Report should be in project dir, not account slug dir
        project_report = tmp_path / "reports" / "my-proj" / "audit_report.md"
        slug_report = tmp_path / "reports" / "test" / "audit_report.md"
        assert project_report.exists()
        assert not slug_report.exists()
        assert "repo1" in project_report.read_text()


class TestBitbucketProviderAudit:
    @respx.mock
    @patch("auditer.git_ops._run_git")
    def test_bitbucket_account_full_audit_flow(self, mock_run_git, tmp_path):
        """Smoke test: audit works end-to-end with a bitbucket-provider account."""
        config = load_config_from_dict(
            {
                "defaults": {
                    "stale_months": 12,
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

        _setup_local_repo(Path(config.data_dir), "bb", "repo1")

        mock_run_git.side_effect = [
            _make_completed_process(
                stdout="https://bitbucket.org/myworkspace/repo1.git\n"
            ),
            _make_completed_process(stdout="main\n"),
            _make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            _make_completed_process(stdout="size-pack: 100\n"),
        ]

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/repo1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "repo1",
                    "is_private": False,
                    "description": "Bitbucket repo",
                    "language": "python",
                    "size": 2048,
                    "mainbranch": {"name": "main"},
                    "links": {
                        "html": {"href": "https://bitbucket.org/myworkspace/repo1"}
                    },
                    "owner": {"nickname": "myworkspace"},
                    "updated_on": "2025-06-01T12:00:00.000000+00:00",
                },
            )
        )

        result = run_audit(config, account)

        assert len(result.repos) == 1
        assert result.repos[0].name == "repo1"
        assert result.repos[0].has_readme is True
        assert result.repos[0].description == "Bitbucket repo"
        assert result.repos[0].language == "python"

        report_path = tmp_path / "reports" / "bb" / "audit_report.md"
        assert report_path.exists()
