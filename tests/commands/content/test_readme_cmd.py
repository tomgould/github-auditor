from pathlib import Path
from unittest.mock import patch

import httpx
import respx
from click.testing import CliRunner

from auditer.cli import cli
from tests.conftest import strip_ansi


def _sample_config_dict(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": str(tmp_path / "data"),
            "reports_dir": str(tmp_path / "reports"),
            "log_level": "INFO",
        },
        "accounts": [
            {
                "slug": "test-user",
                "type": "user",
                "username": "testuser",
                "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
            },
        ],
    }


def _mock_repo_api_response():
    return httpx.Response(200, json={
        "name": "my-repo",
        "owner": {"login": "testuser"},
        "html_url": "https://github.com/testuser/my-repo",
        "private": False,
        "description": "A test repo",
        "language": "Python",
        "stargazers_count": 10,
        "forks_count": 2,
        "open_issues_count": 1,
        "license": {"spdx_id": "MIT"},
        "pushed_at": "2025-01-15T10:00:00Z",
        "fork": False,
        "archived": False,
        "topics": ["python", "cli"],
        "default_branch": "main",
        "size": 1024,
    })


def _create_local_repo(data_dir, slug, repo_name, private=False, has_readme=True):
    """Create a minimal local repo structure for testing."""
    subdir = "private" if private else "public"
    repo_dir = data_dir / slug / subdir / repo_name
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    # Create git config for owner detection
    git_config = repo_dir / ".git" / "config"
    git_config.write_text(
        f"[remote \"origin\"]\n\turl = https://github.com/testuser/{repo_name}.git\n"
    )
    # Create HEAD file for branch detection
    (repo_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    if has_readme:
        (repo_dir / "README.md").write_text(f"# {repo_name}\n")
    # Create a pyproject.toml for scan detection
    (repo_dir / "pyproject.toml").write_text(
        '[project]\nname = "my-repo"\ndescription = "A test project"\n'
        'dependencies = ["click"]\n'
    )
    return repo_dir


class TestReadmeCmd:
    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_single_repo(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        # Create a local repo with a pyproject.toml
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "pyproject.toml").write_text(
            '[project]\nname = "my-repo"\ndescription = "A test"\n'
        )
        (repo_dir / "src").mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=repo_dir,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=500,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=_mock_repo_api_response()
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "my-repo", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "my-repo" in output
        assert "README GENERATION" in output
        assert "Generated:  1" in output

        # Check report was written
        report_path = tmp_path / "reports" / "test-user" / "readmes" / "my-repo.md"
        assert report_path.exists()
        content = report_path.read_text()
        assert "# my-repo" in content
        assert "A test repo" in content

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_repo_not_found(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        mock_scan.return_value = [
            LocalRepoInfo(
                name="other-repo",
                path=tmp_path / "other-repo",
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/other-repo.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "nonexistent", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "not found in local checkouts" in output
        assert result.exit_code == 1

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_all_missing_readmes(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        # Create repo dirs for scan_repo
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()
        repo_b = tmp_path / "repo-b"
        repo_b.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="has-readme",
                path=repo_a,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/has-readme.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
            LocalRepoInfo(
                name="no-readme",
                path=repo_b,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/no-readme.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/no-readme").mock(
            return_value=httpx.Response(200, json={
                "name": "no-readme",
                "owner": {"login": "testuser"},
                "html_url": "https://github.com/testuser/no-readme",
                "description": "Needs a README",
                "language": "Python",
                "license": None,
                "fork": False,
                "archived": False,
                "topics": [],
                "default_branch": "main",
            })
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--all", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Skipping 1 repo(s) that already have a README" in output
        assert "Generated:  1" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_all_with_force(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()
        repo_b = tmp_path / "repo-b"
        repo_b.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="repo-a",
                path=repo_a,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/repo-a.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
            LocalRepoInfo(
                name="repo-b",
                path=repo_b,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/repo-b.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/repo-a").mock(
            return_value=_mock_repo_api_response()
        )
        respx.get("https://api.github.com/repos/testuser/repo-b").mock(
            return_value=_mock_repo_api_response()
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--all", "--force", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Generated:  2" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_excludes_repos(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        config_dict["accounts"][0]["exclude_repos"] = ["excluded-repo"]
        mock_load.return_value = load_config_from_dict(config_dict)

        repo_dir = tmp_path / "excluded-repo"
        repo_dir.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="excluded-repo",
                path=repo_dir,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/excluded-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--all", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "Excluded 1 repo(s)" in output
        assert "All repos already have READMEs" in output or "Nothing to generate" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_no_local_repos(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        mock_scan.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--all", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "No local repos found" in output
        assert "auditer checkout" in output

    @patch("auditer.cli.load_config")
    def test_no_repo_or_all(self, mock_load, tmp_path):
        from auditer.config import load_config_from_dict

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "Specify a REPO argument or --all" in output
        assert result.exit_code == 1

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_auth_error(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=tmp_path / "my-repo",
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--all", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "Authentication failed" in output
        assert result.exit_code == 2

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_all_have_readmes(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        mock_scan.return_value = [
            LocalRepoInfo(
                name="repo-with-readme",
                path=tmp_path / "repo-with-readme",
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/repo-with-readme.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "--all", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "All repos already have READMEs" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_push_creates_readme(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=repo_dir,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=_mock_repo_api_response()
        )
        # No existing README
        respx.get("https://api.github.com/repos/testuser/my-repo/contents/README.md").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        # Create file
        respx.put("https://api.github.com/repos/testuser/my-repo/contents/README.md").mock(
            return_value=httpx.Response(201, json={"content": {"sha": "abc123"}})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["readme", "my-repo", "--account", "test-user", "--push"],
            input="y\n",
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "pushed to GitHub" in output
        assert "Pushed:     1" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_push_updates_existing_readme(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=repo_dir,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=_mock_repo_api_response()
        )
        # Existing README with SHA
        respx.get("https://api.github.com/repos/testuser/my-repo/contents/README.md").mock(
            return_value=httpx.Response(200, json={"sha": "existing-sha-123"})
        )
        # Update file
        respx.put("https://api.github.com/repos/testuser/my-repo/contents/README.md").mock(
            return_value=httpx.Response(200, json={"content": {"sha": "new-sha"}})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["readme", "my-repo", "--account", "test-user", "--push"],
            input="y\n",
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "pushed to GitHub" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_push_requires_token(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        config_dict["accounts"][0]["token"] = None
        mock_load.return_value = load_config_from_dict(config_dict)

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=tmp_path / "my-repo",
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["readme", "--all", "--account", "test-user", "--push"],
        )
        output = strip_ansi(result.output)
        assert "--push requires an authenticated account" in output
        assert result.exit_code == 1

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_push_confirm_abort(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=tmp_path / "my-repo",
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["readme", "--all", "--account", "test-user", "--push"],
            input="n\n",
        )
        assert result.exit_code != 0  # Aborted

    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_push_also_saves_locally(self, mock_scan, mock_load, tmp_path):
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=repo_dir,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=_mock_repo_api_response()
        )
        respx.get("https://api.github.com/repos/testuser/my-repo/contents/README.md").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        respx.put("https://api.github.com/repos/testuser/my-repo/contents/README.md").mock(
            return_value=httpx.Response(201, json={"content": {"sha": "abc"}})
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["readme", "my-repo", "--account", "test-user", "--push"],
            input="y\n",
        )
        assert result.exit_code == 0

        # Check that local copy was also saved
        report_path = tmp_path / "reports" / "test-user" / "readmes" / "my-repo.md"
        assert report_path.exists()
        assert "# my-repo" in report_path.read_text()


def _sample_config_dict_bitbucket(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": str(tmp_path / "data"),
            "reports_dir": str(tmp_path / "reports"),
            "log_level": "INFO",
        },
        "accounts": [
            {
                "slug": "bb-user",
                "type": "user",
                "username": "myworkspace",
                "provider": "bitbucket",
                "workspace": "myworkspace",
                "auth_email": "tom@example.com",
                "token": "bb_test_api_token_1234567890",
            },
        ],
    }


class TestReadmeCmdBitbucket:
    @respx.mock
    @patch("auditer.cli.load_config")
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_bitbucket_single_repo(self, mock_scan, mock_load, tmp_path):
        """Smoke test: readme generation works end-to-end with a bitbucket-provider account."""
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config_dict = _sample_config_dict_bitbucket(tmp_path)
        mock_load.return_value = load_config_from_dict(config_dict)

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "pyproject.toml").write_text(
            '[project]\nname = "my-repo"\ndescription = "A test"\n'
        )

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=repo_dir,
                private=False,
                owner="myworkspace",
                clone_url="https://bitbucket.org/myworkspace/my-repo.git",
                has_readme=True,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=500,
            ),
        ]

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "my-repo",
                    "owner": {"nickname": "myworkspace"},
                    "description": "A test repo",
                    "language": "python",
                    "mainbranch": {"name": "main"},
                    "links": {"html": {"href": "https://bitbucket.org/myworkspace/my-repo"}},
                },
            )
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["readme", "my-repo", "--account", "bb-user"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "my-repo" in output
        assert "Generated:  1" in output

        report_path = tmp_path / "reports" / "bb-user" / "readmes" / "my-repo.md"
        assert report_path.exists()
        assert "A test repo" in report_path.read_text()


class TestProjectFilter:
    @respx.mock
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_project_filters_repos(self, mock_scan, tmp_path):
        """--project filters readme generation to only repos in the project."""
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config = load_config_from_dict({
            "defaults": {
                "data_dir": str(tmp_path / "data"),
                "reports_dir": str(tmp_path / "reports"),
            },
            "accounts": [
                {
                    "slug": "test-user",
                    "type": "user",
                    "username": "testuser",
                    "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                },
            ],
            "projects": {
                "my-proj": {"repos": ["test-user/wanted-repo"]},
            },
        })
        account = config.accounts[0]
        project = config.projects[0]

        repo_wanted = tmp_path / "wanted-repo"
        repo_wanted.mkdir()
        repo_other = tmp_path / "other-repo"
        repo_other.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="wanted-repo",
                path=repo_wanted,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/wanted-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
            LocalRepoInfo(
                name="other-repo",
                path=repo_other,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/other-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/wanted-repo").mock(
            return_value=httpx.Response(200, json={
                "name": "wanted-repo",
                "owner": {"login": "testuser"},
                "html_url": "https://github.com/testuser/wanted-repo",
                "description": "A wanted repo",
                "language": "Python",
                "license": None,
                "fork": False,
                "archived": False,
                "topics": [],
                "default_branch": "main",
            })
        )

        from auditer.commands.content.readme import run_readme
        run_readme(config, account, run_all=True, force=True, project=project)

        # Only wanted-repo should have a report
        report_dir = tmp_path / "reports" / "my-proj" / "readmes"
        assert (report_dir / "wanted-repo.md").exists()
        assert not (report_dir / "other-repo.md").exists()

    @respx.mock
    @patch("auditer.commands.content.readme.scan_local_repos")
    def test_project_report_in_project_dir(self, mock_scan, tmp_path):
        """When project is active, reports go to reports/{project.name}/readmes/."""
        from auditer.config import load_config_from_dict
        from auditer.git_ops import LocalRepoInfo

        config = load_config_from_dict({
            "defaults": {
                "data_dir": str(tmp_path / "data"),
                "reports_dir": str(tmp_path / "reports"),
            },
            "accounts": [
                {
                    "slug": "test-user",
                    "type": "user",
                    "username": "testuser",
                    "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                },
            ],
            "projects": {
                "my-proj": {"repos": ["test-user/my-repo"]},
            },
        })
        account = config.accounts[0]
        project = config.projects[0]

        repo_dir = tmp_path / "my-repo"
        repo_dir.mkdir()

        mock_scan.return_value = [
            LocalRepoInfo(
                name="my-repo",
                path=repo_dir,
                private=False,
                owner="testuser",
                clone_url="https://github.com/testuser/my-repo.git",
                has_readme=False,
                has_license_file=True,
                default_branch="main",
                pushed_at="2025-01-15T10:00:00Z",
                size_kb=100,
            ),
        ]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=_mock_repo_api_response()
        )

        from auditer.commands.content.readme import run_readme
        run_readme(config, account, run_all=True, force=True, project=project)

        # Report should be in project directory, not account directory
        project_report = tmp_path / "reports" / "my-proj" / "readmes" / "my-repo.md"
        account_report = tmp_path / "reports" / "test-user" / "readmes" / "my-repo.md"
        assert project_report.exists()
        assert not account_report.exists()
