import httpx
import pytest
import respx
from click.testing import CliRunner

from auditer.cli import cli
from tests.conftest import strip_ansi


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def config_dict(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": str(tmp_path / "data"),
            "reports_dir": str(tmp_path / "reports"),
        },
        "accounts": [
            {
                "slug": "myuser",
                "type": "user",
                "username": "testuser",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                "exclude_repos": ["excluded-repo"],
            }
        ],
    }


@pytest.fixture
def invoke(runner, config_dict, tmp_path):
    import yaml

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_dict))

    def _invoke(args, input=None):
        return runner.invoke(cli, ["--config", str(config_path)] + args, input=input)

    return _invoke


STALE_REPO = {
    "name": "old-project",
    "owner": {"login": "testuser"},
    "html_url": "https://github.com/testuser/old-project",
    "private": False,
    "description": "",
    "language": "Python",
    "stargazers_count": 0,
    "forks_count": 0,
    "open_issues_count": 0,
    "license": None,
    "pushed_at": "2020-01-01T00:00:00Z",
    "fork": False,
    "archived": False,
    "topics": [],
    "default_branch": "main",
    "size": 100,
}

ACTIVE_REPO = {
    "name": "active-project",
    "owner": {"login": "testuser"},
    "html_url": "https://github.com/testuser/active-project",
    "private": False,
    "description": "Active",
    "language": "Python",
    "stargazers_count": 10,
    "forks_count": 1,
    "open_issues_count": 0,
    "license": {"spdx_id": "MIT"},
    "pushed_at": "2026-02-01T00:00:00Z",
    "fork": False,
    "archived": False,
    "topics": [],
    "default_branch": "main",
    "size": 200,
}

ALREADY_ARCHIVED = {
    "name": "already-archived",
    "owner": {"login": "testuser"},
    "html_url": "https://github.com/testuser/already-archived",
    "private": False,
    "description": "",
    "language": None,
    "stargazers_count": 0,
    "forks_count": 0,
    "open_issues_count": 0,
    "license": None,
    "pushed_at": "2019-01-01T00:00:00Z",
    "fork": False,
    "archived": True,
    "topics": [],
    "default_branch": "main",
    "size": 50,
}


class TestArchiveSpecificRepo:
    @respx.mock
    def test_archive_specific_repo(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(
                200, json={"name": "my-repo", "archived": True}
            )
        )

        result = invoke([
            "archive", "--account", "myuser", "--repo", "my-repo",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Archived testuser/my-repo" in output

    def test_archive_specific_repo_aborted(self, invoke):
        result = invoke([
            "archive", "--account", "myuser", "--repo", "my-repo",
        ], input="n\n")
        assert result.exit_code != 0


class TestArchiveStaleRepos:
    @respx.mock
    def test_archive_stale_repos(self, invoke):
        repos = [STALE_REPO, ACTIVE_REPO, ALREADY_ARCHIVED]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.patch("https://api.github.com/repos/testuser/old-project").mock(
            return_value=httpx.Response(
                200, json={"name": "old-project", "archived": True}
            )
        )

        result = invoke(["archive", "--account", "myuser"], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "old-project" in output
        assert "Archived 1 repo(s)" in output

    @respx.mock
    def test_archive_custom_months(self, invoke):
        repos = [STALE_REPO, ACTIVE_REPO]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.patch("https://api.github.com/repos/testuser/old-project").mock(
            return_value=httpx.Response(200, json={"name": "old-project", "archived": True})
        )

        result = invoke([
            "archive", "--account", "myuser", "--months", "24",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "old-project" in output

    @respx.mock
    def test_no_stale_repos(self, invoke):
        repos = [ACTIVE_REPO]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )

        result = invoke(["archive", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "No stale repos found" in output

    @respx.mock
    def test_skip_already_archived(self, invoke):
        repos = [ALREADY_ARCHIVED]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )

        result = invoke(["archive", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "No stale repos found" in output

    @respx.mock
    def test_excludes_repos(self, invoke):
        excluded = dict(STALE_REPO)
        excluded["name"] = "excluded-repo"
        repos = [excluded, ACTIVE_REPO]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )

        result = invoke(["archive", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Excluded" in output
        assert "No stale repos found" in output


class TestBitbucketGuard:
    @pytest.fixture
    def bb_invoke(self, runner, tmp_path):
        import yaml

        config_dict = {
            "defaults": {
                "stale_months": 12,
                "data_dir": str(tmp_path / "data"),
                "reports_dir": str(tmp_path / "reports"),
            },
            "accounts": [
                {
                    "slug": "bb-user",
                    "type": "user",
                    "username": "testuser",
                    "provider": "bitbucket",
                    "workspace": "myworkspace",
                    "auth_email": "tom@example.com",
                    "token": "bb_test_api_token_1234567890",
                }
            ],
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict))

        def _invoke(args, input=None):
            return runner.invoke(cli, ["--config", str(config_path)] + args, input=input)

        return _invoke

    def test_archive_all_rejects_bitbucket(self, bb_invoke):
        result = bb_invoke(["archive", "--account", "bb-user"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "GitHub-only" in output

    def test_archive_repo_rejects_bitbucket(self, bb_invoke):
        result = bb_invoke(["archive", "--account", "bb-user", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "GitHub-only" in output


class TestProjectFilter:
    @respx.mock
    def test_project_filters_repos(self, runner, tmp_path):
        """--project filters archive to only repos in the project."""
        import yaml

        config_dict = {
            "defaults": {
                "stale_months": 12,
                "data_dir": str(tmp_path / "data"),
                "reports_dir": str(tmp_path / "reports"),
            },
            "accounts": [
                {
                    "slug": "myuser",
                    "type": "user",
                    "username": "testuser",
                    "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                }
            ],
            "projects": {
                "my-proj": {"repos": ["myuser/old-project"]},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict))

        # Both stale, but only old-project is in the project
        other_stale = dict(STALE_REPO)
        other_stale["name"] = "other-stale"

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[STALE_REPO, other_stale])
        )
        respx.patch("https://api.github.com/repos/testuser/old-project").mock(
            return_value=httpx.Response(
                200, json={"name": "old-project", "archived": True}
            )
        )

        result = runner.invoke(
            cli,
            ["--config", str(config_path), "archive", "--account", "myuser", "--project", "my-proj"],
            input="y\n",
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Filtered to" in output
        assert "Archived 1 repo(s)" in output
        # other-stale should not be archived
        assert "other-stale" not in output
