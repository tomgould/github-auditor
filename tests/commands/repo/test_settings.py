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


class TestSettingsSingleRepo:
    @respx.mock
    def test_update_description(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(
                200, json={"name": "my-repo", "description": "New desc"}
            )
        )

        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--description", "New desc",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings" in output

    @respx.mock
    def test_update_homepage(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(200, json={"name": "my-repo"})
        )

        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--homepage", "https://example.com",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings" in output

    @respx.mock
    def test_update_visibility_confirms(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(200, json={"name": "my-repo"})
        )

        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--visibility", "private",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings" in output

    def test_visibility_aborted(self, invoke):
        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--visibility", "private",
        ], input="n\n")
        assert result.exit_code != 0

    @respx.mock
    def test_update_boolean_options(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(200, json={"name": "my-repo"})
        )

        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--has-issues", "false", "--has-wiki", "false",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings" in output


class TestSettingsAllRepos:
    @respx.mock
    def test_update_all_repos(self, invoke):
        repos = [
            {"name": "repo1", "fork": False, "private": False},
            {"name": "repo2", "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.patch("https://api.github.com/repos/testuser/repo1").mock(
            return_value=httpx.Response(200, json={"name": "repo1"})
        )
        respx.patch("https://api.github.com/repos/testuser/repo2").mock(
            return_value=httpx.Response(200, json={"name": "repo2"})
        )

        result = invoke([
            "settings", "--account", "myuser", "--all",
            "--has-wiki", "false",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings for 2 repo(s)" in output

    def test_update_all_aborted(self, invoke):
        result = invoke([
            "settings", "--account", "myuser", "--all",
            "--has-wiki", "false",
        ], input="n\n")
        assert result.exit_code != 0


class TestSettingsAuth:
    @respx.mock
    def test_auth_error(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--description", "test",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Authentication failed" in output

    @respx.mock
    def test_visibility_confirm_abort(self, invoke):
        """--visibility change prompts and 'n' aborts."""
        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--visibility", "private",
        ], input="n\n")
        assert result.exit_code != 0

    @respx.mock
    def test_multiple_boolean_options(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(200, json={"name": "my-repo"})
        )
        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
            "--has-issues", "false", "--has-wiki", "true", "--has-projects", "false",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings" in output
        # GitHub accounts support --has-projects — no Bitbucket warning expected.
        assert "ignored for Bitbucket" not in output


class TestProjectFilter:
    @respx.mock
    def test_project_filters_repos(self, runner, tmp_path):
        """--project filters settings to only repos in the project."""
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
            {"name": "repo1", "fork": False, "private": False},
            {"name": "repo2", "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        route = respx.patch("https://api.github.com/repos/testuser/repo1").mock(
            return_value=httpx.Response(200, json={"name": "repo1"})
        )

        result = runner.invoke(
            cli,
            ["--config", str(config_path), "settings", "--account", "myuser", "--all",
             "--has-wiki", "false", "--project", "my-proj"],
            input="y\n",
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Filtered to" in output
        assert "Updated settings for 1 repo(s)" in output
        # Only repo1 should have been updated
        assert route.call_count == 1


class TestSettingsBitbucket:
    @pytest.fixture
    def bb_config_path(self, tmp_path):
        import yaml

        config_dict = {
            "defaults": {
                "stale_months": 12,
                "data_dir": str(tmp_path / "data"),
                "reports_dir": str(tmp_path / "reports"),
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
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict))
        return config_path

    @respx.mock
    def test_bitbucket_update_repo(self, runner, bb_config_path):
        """Smoke test: settings works end-to-end with a bitbucket-provider account."""
        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.put("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "my-repo",
                    "owner": {"nickname": "myworkspace"},
                    "links": {"html": {"href": "https://bitbucket.org/myworkspace/my-repo"}},
                },
            )
        )

        result = runner.invoke(
            cli,
            [
                "--config", str(bb_config_path), "settings", "--account", "bb",
                "--repo", "my-repo", "--description", "New desc",
            ],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Updated settings" in output

    @respx.mock
    def test_has_projects_warns_on_bitbucket(self, runner, bb_config_path):
        """--has-projects prints a warning for Bitbucket accounts (no Projects concept)."""
        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.put("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "my-repo",
                    "owner": {"nickname": "myworkspace"},
                    "links": {"html": {"href": "https://bitbucket.org/myworkspace/my-repo"}},
                },
            )
        )

        result = runner.invoke(
            cli,
            [
                "--config", str(bb_config_path), "settings", "--account", "bb",
                "--repo", "my-repo", "--has-projects", "true",
            ],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "ignored for Bitbucket" in output


class TestSettingsErrors:
    def test_no_repo_or_all(self, invoke):
        result = invoke([
            "settings", "--account", "myuser", "--description", "test",
        ])
        assert result.exit_code != 0

    def test_no_settings_specified(self, invoke):
        result = invoke([
            "settings", "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "No settings specified" in output
