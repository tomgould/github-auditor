from unittest.mock import patch

import httpx
import respx
from click.testing import CliRunner

from auditer.cli import cli
from tests.conftest import strip_ansi


def _mock_user_response():
    return httpx.Response(200, json={
        "login": "testuser",
        "id": 12345,
        "name": "Test User",
        "bio": "I build things.",
        "location": "London, UK",
        "company": "Acme Corp",
        "blog": "https://testuser.dev",
        "twitter_username": "testuser",
        "avatar_url": "https://avatars.githubusercontent.com/u/12345",
        "html_url": "https://github.com/testuser",
        "public_repos": 2,
        "public_gists": 0,
        "followers": 10,
        "following": 5,
    })


def _mock_repos_response():
    return httpx.Response(200, json=[
        {
            "name": "my-repo",
            "owner": {"login": "testuser"},
            "html_url": "https://github.com/testuser/my-repo",
            "private": False,
            "description": "A test repo",
            "language": "Python",
            "stargazers_count": 42,
            "forks_count": 5,
            "open_issues_count": 3,
            "license": {"spdx_id": "MIT"},
            "pushed_at": "2025-01-15T10:00:00Z",
            "fork": False,
            "archived": False,
            "topics": ["python"],
            "default_branch": "main",
            "size": 1024,
        },
    ])


def _mock_events_response():
    return httpx.Response(200, json=[
        {"type": "PushEvent", "repo": {"name": "testuser/my-repo"}, "payload": {}},
    ])


def _sample_config_dict():
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": "data",
            "reports_dir": "/tmp/test-profile-reports",
            "log_level": "INFO",
        },
        "accounts": [
            {
                "slug": "test-user",
                "type": "user",
                "username": "testuser",
                "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
            },
            {
                "slug": "second-user",
                "type": "user",
                "username": "seconduser",
                "token": "ghp_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy5678",
            },
        ],
    }


def _sample_config_bitbucket():
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": "data",
            "reports_dir": "/tmp/test-profile-reports",
            "log_level": "INFO",
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
            },
        ],
    }


def _sample_config_no_token():
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": "data",
            "reports_dir": "/tmp/test-profile-reports",
            "log_level": "INFO",
        },
        "accounts": [
            {
                "slug": "public-user",
                "type": "user",
                "username": "publicuser",
                "token": None,
            },
        ],
    }


class TestProfileCmd:
    @respx.mock
    @patch("auditer.cli.load_config")
    def test_no_account_or_all(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_dict())
        runner = CliRunner()
        result = runner.invoke(cli, ["profile"])
        output = strip_ansi(result.output)
        assert "Specify --account SLUG or --all" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_single_account(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_dict())

        respx.get("https://api.github.com/user").mock(return_value=_mock_user_response())
        respx.get("https://api.github.com/user/repos").mock(return_value=_mock_repos_response())
        respx.get("https://api.github.com/users/testuser/events/public").mock(
            return_value=_mock_events_response()
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "PROFILE SUMMARY" in output
        assert "Test User" in output
        assert "profile_readme.md" in output
        assert result.exit_code == 0

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_all_accounts(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_dict())

        # Mock for both accounts
        respx.get("https://api.github.com/user").mock(return_value=_mock_user_response())
        respx.get("https://api.github.com/user/repos").mock(return_value=_mock_repos_response())
        respx.get("https://api.github.com/users/testuser/events/public").mock(
            return_value=_mock_events_response()
        )
        respx.get("https://api.github.com/users/seconduser/events/public").mock(
            return_value=_mock_events_response()
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--all"])
        output = strip_ansi(result.output)
        assert "Profile: test-user" in output
        assert "Profile: second-user" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_auth_error(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_dict())

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "Authentication failed" in output
        assert result.exit_code == 2

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_no_token_public(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_no_token())

        respx.get("https://api.github.com/users/publicuser").mock(
            return_value=_mock_user_response()
        )
        respx.get("https://api.github.com/users/publicuser/repos").mock(
            return_value=_mock_repos_response()
        )
        respx.get("https://api.github.com/users/testuser/events/public").mock(
            return_value=_mock_events_response()
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--account", "public-user"])
        output = strip_ansi(result.output)
        assert "PROFILE SUMMARY" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_user_not_found(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_no_token())

        respx.get("https://api.github.com/users/publicuser").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--account", "public-user"])
        output = strip_ansi(result.output)
        assert "not found" in output

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_rejects_bitbucket(self, mock_load):
        from auditer.config import load_config_from_dict
        mock_load.return_value = load_config_from_dict(_sample_config_bitbucket())

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--account", "bb-user"])
        output = strip_ansi(result.output)
        assert "GitHub-only" in output
        assert result.exit_code != 0

    @respx.mock
    @patch("auditer.cli.load_config")
    def test_excludes_repos(self, mock_load):
        from auditer.config import load_config_from_dict
        config_dict = _sample_config_dict()
        config_dict["accounts"][0]["exclude_repos"] = ["my-repo"]
        mock_load.return_value = load_config_from_dict(config_dict)

        respx.get("https://api.github.com/user").mock(return_value=_mock_user_response())
        respx.get("https://api.github.com/user/repos").mock(return_value=_mock_repos_response())
        respx.get("https://api.github.com/users/testuser/events/public").mock(
            return_value=_mock_events_response()
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "--account", "test-user"])
        output = strip_ansi(result.output)
        assert "Excluded 1 repo(s)" in output
