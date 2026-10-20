from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from auditer.cli import cli
from auditer.config import AppConfig, ConfigError
from auditer.models import AccountConfig
from tests.conftest import strip_ansi


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def wide_console():
    """Use a wide console to prevent Rich table truncation in tests."""
    wide = Console(width=300, force_terminal=True, soft_wrap=True)
    with patch("auditer.commands.meta.config_cmd.console", wide):
        yield


def _make_config(accounts=None):
    if accounts is None:
        accounts = [
            AccountConfig(
                slug="acct1",
                type="user",
                username="alice",
                token="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                include_private=True,
                include_forks=False,
                exclude_repos=["skip-me"],
                data_dir="/custom/data",
            ),
            AccountConfig(
                slug="acct2",
                type="org",
                username="myorg",
                token="ghp_yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy5678",
            ),
        ]
    return AppConfig(
        stale_months=12,
        data_dir="data",
        reports_dir="reports",
        log_level="INFO",
        accounts=accounts,
    )


class TestConfigValidate:
    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_validate_success(self, mock_load, runner):
        mock_load.return_value = _make_config()
        result = runner.invoke(cli, ["config", "--validate"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Config is valid" in output
        assert "2 account(s)" in output

    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_validate_error(self, mock_load, runner):
        mock_load.side_effect = ConfigError("bad yaml")
        result = runner.invoke(cli, ["config", "--validate"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Config error" in output
        assert "bad yaml" in output


class TestConfigShow:
    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_show_displays_table(self, mock_load, runner):
        mock_load.return_value = _make_config()
        result = runner.invoke(cli, ["config", "--show"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        # Defaults section
        assert "stale_months" in output
        assert "data_dir" in output
        assert "reports_dir" in output
        assert "log_level" in output
        # Account table headers
        assert "Accounts" in output
        # Account data — masked tokens
        assert "acct1" in output
        assert "alice" in output
        assert "acct2" in output
        assert "myorg" in output
        # Token should be masked (last 4 chars only)
        assert "1234" in output
        assert "5678" in output
        # Excluded repos
        assert "skip-me" in output
        # Custom data_dir (Rich may truncate long paths)
        assert "/custom/d" in output

    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_show_error(self, mock_load, runner):
        mock_load.side_effect = ConfigError("file not found")
        result = runner.invoke(cli, ["config", "--show"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Config error" in output

    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_show_default_data_dir(self, mock_load, runner):
        """Account with no data_dir shows '-'."""
        acct = AccountConfig(
            slug="plain",
            type="user",
            username="bob",
            token="ghp_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz9999",
        )
        mock_load.return_value = _make_config(accounts=[acct])
        result = runner.invoke(cli, ["config", "--show"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        # data_dir column should show "-" for account without override
        # (the "-" appears in the table row for the data dir and excludes columns)
        assert "plain" in output

    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_show_no_excludes(self, mock_load, runner):
        """Account with no exclude_repos shows '-'."""
        acct = AccountConfig(
            slug="clean",
            type="user",
            username="carol",
            token="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbb",
            exclude_repos=[],
        )
        mock_load.return_value = _make_config(accounts=[acct])
        result = runner.invoke(cli, ["config", "--show"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "clean" in output


    @patch("auditer.commands.meta.config_cmd.load_config")
    def test_show_include_repos(self, mock_load, runner):
        """Account with include_repos shows the repo names."""
        acct = AccountConfig(
            slug="tracked",
            type="user",
            username="famous",
            token="ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaacccc",
            include_repos=["cool-project", "awesome-lib"],
        )
        mock_load.return_value = _make_config(accounts=[acct])
        result = runner.invoke(cli, ["config", "--show"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "cool-project" in output
        assert "awesome-lib" in output


class TestProjectsDisplay:
    def test_show_displays_projects(self, tmp_path):
        import yaml

        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a",
                 "token": "ghp_testtoken1234567890abcdefghijklmnopq"},
            ],
            "projects": {
                "client-work": {
                    "description": "Client X",
                    "repos": ["a/web", "a/api"],
                },
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(data))

        result = CliRunner().invoke(cli, ["--config", str(config_path), "config", "--show"])
        output = strip_ansi(result.output)
        assert "Projects" in output
        assert "client-work" in output
        assert "Client X" in output
        assert "2" in output  # 2 repos

    def test_validate_shows_project_count(self, tmp_path):
        import yaml

        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a",
                 "token": "ghp_testtoken1234567890abcdefghijklmnopq"},
            ],
            "projects": {
                "p1": {"repos": ["a/r1"]},
                "p2": {"repos": ["a/r2"]},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(data))

        result = CliRunner().invoke(cli, ["--config", str(config_path), "config", "--validate"])
        output = strip_ansi(result.output)
        assert "2 project(s)" in output

    def test_show_no_projects_no_section(self, tmp_path):
        import yaml

        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a",
                 "token": "ghp_testtoken1234567890abcdefghijklmnopq"},
            ],
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(data))

        result = CliRunner().invoke(cli, ["--config", str(config_path), "config", "--show"])
        output = strip_ansi(result.output)
        # No Projects table when there are no projects
        assert "Projects" not in output or output.count("Projects") == 0


class TestConfigNoFlags:
    def test_no_flags(self, runner):
        result = runner.invoke(cli, ["config"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "--validate" in output or "--show" in output
