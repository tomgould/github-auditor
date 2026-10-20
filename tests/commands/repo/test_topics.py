import httpx
import pytest
import respx
from click.testing import CliRunner

from auditer.cli import cli
from tests.conftest import strip_ansi


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
def runner():
    return CliRunner()


@pytest.fixture
def invoke(runner, config_dict, tmp_path):
    import yaml

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_dict))

    def _invoke(args):
        return runner.invoke(cli, ["--config", str(config_path)] + args)

    return _invoke


class TestTopicsList:
    @respx.mock
    def test_list_single_repo(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(200, json={"names": ["python", "cli"]})
        )

        result = invoke(["topics", "list", "--account", "myuser", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "python" in output
        assert "cli" in output

    @respx.mock
    def test_list_single_repo_no_topics(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(200, json={"names": []})
        )

        result = invoke(["topics", "list", "--account", "myuser", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "(none)" in output

    @respx.mock
    def test_list_all_repos(self, invoke):
        repos = [
            {"name": "repo1", "topics": ["python"], "fork": False, "private": False},
            {"name": "repo2", "topics": [], "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )

        result = invoke(["topics", "list", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "repo1" in output
        assert "python" in output
        assert "repo2" in output

    @respx.mock
    def test_list_all_repos_excludes(self, invoke):
        repos = [
            {"name": "repo1", "topics": ["python"], "fork": False, "private": False},
            {"name": "excluded-repo", "topics": ["go"], "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )

        result = invoke(["topics", "list", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Excluded" in output
        assert "repo1" in output


class TestTopicsListAuth:
    @respx.mock
    def test_list_auth_error(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        result = invoke(["topics", "list", "--account", "myuser", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Authentication failed" in output


class TestTopicsSetAuth:
    @respx.mock
    def test_set_auth_error(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        result = invoke([
            "topics", "set", "python,cli",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Authentication failed" in output


class TestTopicsSet:
    @respx.mock
    def test_set_topics(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.put("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(
                200, json={"names": ["python", "cli", "audit"]}
            )
        )

        result = invoke([
            "topics", "set", "python,cli,audit",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Set topics" in output
        assert "python" in output


class TestTopicsAdd:
    @respx.mock
    def test_add_topic_single_repo(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(200, json={"names": ["python"]})
        )
        respx.put("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(
                200, json={"names": ["python", "cli"]}
            )
        )

        result = invoke([
            "topics", "add", "cli",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added 'cli'" in output

    @respx.mock
    def test_add_topic_already_present(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(200, json={"names": ["python", "cli"]})
        )

        result = invoke([
            "topics", "add", "cli",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "already has topic" in output

    @respx.mock
    def test_add_topic_all_repos(self, invoke):
        repos = [
            {"name": "repo1", "topics": ["python"], "fork": False, "private": False},
            {"name": "repo2", "topics": ["python", "cli"], "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.put("https://api.github.com/repos/testuser/repo1/topics").mock(
            return_value=httpx.Response(
                200, json={"names": ["python", "cli"]}
            )
        )

        result = invoke(["topics", "add", "cli", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added 'cli' to 1 repo(s)" in output
        assert "1 already had it" in output


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

        def _invoke(args):
            return runner.invoke(cli, ["--config", str(config_path)] + args)

        return _invoke

    def test_list_rejects_bitbucket(self, bb_invoke):
        result = bb_invoke(["topics", "list", "--account", "bb-user"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "GitHub-only" in output

    def test_set_rejects_bitbucket(self, bb_invoke):
        result = bb_invoke([
            "topics", "set", "python,cli",
            "--account", "bb-user", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "GitHub-only" in output

    def test_add_rejects_bitbucket(self, bb_invoke):
        result = bb_invoke(["topics", "add", "cli", "--account", "bb-user"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "GitHub-only" in output


class TestProjectFilter:
    @respx.mock
    def test_project_filters_topics_list(self, runner, tmp_path):
        """--project filters topics list to only repos in the project."""
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
                "my-proj": {"repos": ["myuser/repo1"]},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict))

        repos = [
            {"name": "repo1", "topics": ["python"], "fork": False, "private": False},
            {"name": "repo2", "topics": ["go"], "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )

        result = runner.invoke(
            cli,
            ["--config", str(config_path), "topics", "list", "--account", "myuser", "--project", "my-proj"],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Filtered to" in output
        assert "repo1" in output
        # repo2 should not appear in the table
        assert "repo2" not in output

    @respx.mock
    def test_project_filters_topics_add(self, runner, tmp_path):
        """--project filters topics add to only repos in the project."""
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
                "my-proj": {"repos": ["myuser/repo1"]},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict))

        repos = [
            {"name": "repo1", "topics": ["python"], "fork": False, "private": False},
            {"name": "repo2", "topics": ["python"], "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.put("https://api.github.com/repos/testuser/repo1/topics").mock(
            return_value=httpx.Response(
                200, json={"names": ["python", "cli"]}
            )
        )

        result = runner.invoke(
            cli,
            ["--config", str(config_path), "topics", "add", "cli", "--account", "myuser", "--project", "my-proj"],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Filtered to" in output
        assert "Added 'cli' to 1 repo(s)" in output
        # 0 already had it (only repo1 is in scope and it doesn't have 'cli')
        assert "0 already had it" in output
