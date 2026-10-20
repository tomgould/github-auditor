import pytest

from auditer.config import (
    ConfigError,
    get_account,
    get_accounts_for_project,
    get_project,
    load_config_from_dict,
)


class TestLoadConfigFromDict:
    def test_valid_config(self, sample_config_dict):
        config = load_config_from_dict(sample_config_dict)
        assert config.stale_months == 12
        assert config.data_dir == "data"
        assert config.reports_dir == "reports"
        assert len(config.accounts) == 1
        assert config.accounts[0].slug == "test-user"
        assert config.accounts[0].type == "user"
        assert config.accounts[0].username == "testuser"

    def test_missing_accounts(self):
        with pytest.raises(ConfigError, match="at least one account"):
            load_config_from_dict({"defaults": {}})

    def test_empty_accounts(self):
        with pytest.raises(ConfigError, match="at least one account"):
            load_config_from_dict({"accounts": []})

    def test_missing_required_fields(self):
        data = {"accounts": [{"slug": "test"}]}
        with pytest.raises(ConfigError, match="missing required fields"):
            load_config_from_dict(data)

    def test_invalid_type(self):
        data = {
            "accounts": [
                {
                    "slug": "test",
                    "type": "invalid",
                    "username": "user",
                }
            ]
        }
        with pytest.raises(ConfigError, match="must be 'user' or 'org'"):
            load_config_from_dict(data)

    def test_token_none_allowed(self):
        data = {
            "accounts": [
                {
                    "slug": "public",
                    "type": "user",
                    "username": "user",
                    "token": None,
                    "include_private": False,
                }
            ]
        }
        config = load_config_from_dict(data)
        assert config.accounts[0].token is None

    def test_defaults_optional(self):
        data = {
            "accounts": [
                {"slug": "test", "type": "user", "username": "user"}
            ]
        }
        config = load_config_from_dict(data)
        assert config.stale_months == 12  # default
        assert config.data_dir == "data"  # default

    def test_multiple_accounts(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
                {"slug": "b", "type": "org", "username": "org-b"},
            ]
        }
        config = load_config_from_dict(data)
        assert len(config.accounts) == 2

    def test_exclude_repos_parsed(self):
        data = {
            "accounts": [
                {
                    "slug": "test",
                    "type": "user",
                    "username": "user",
                    "exclude_repos": ["old-repo", "broken-repo"],
                }
            ]
        }
        config = load_config_from_dict(data)
        assert config.accounts[0].exclude_repos == ["old-repo", "broken-repo"]

    def test_exclude_repos_default_empty(self):
        data = {
            "accounts": [
                {"slug": "test", "type": "user", "username": "user"}
            ]
        }
        config = load_config_from_dict(data)
        assert config.accounts[0].exclude_repos == []

    def test_data_dir_parsed(self):
        data = {
            "accounts": [
                {
                    "slug": "test",
                    "type": "user",
                    "username": "user",
                    "data_dir": "/mnt/work/repos",
                }
            ]
        }
        config = load_config_from_dict(data)
        assert config.accounts[0].data_dir == "/mnt/work/repos"

    def test_data_dir_default_none(self):
        data = {
            "accounts": [
                {"slug": "test", "type": "user", "username": "user"}
            ]
        }
        config = load_config_from_dict(data)
        assert config.accounts[0].data_dir is None

    def test_exclude_repos_null_becomes_empty(self):
        data = {
            "accounts": [
                {
                    "slug": "test",
                    "type": "user",
                    "username": "user",
                    "exclude_repos": None,
                }
            ]
        }
        config = load_config_from_dict(data)
        assert config.accounts[0].exclude_repos == []


class TestLoadConfig:
    def test_missing_file(self, tmp_path):
        from pathlib import Path

        from auditer.config import load_config

        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_empty_file(self, tmp_path):
        from auditer.config import load_config

        empty_config = tmp_path / "config.yaml"
        empty_config.write_text("")
        with pytest.raises(ConfigError, match="empty"):
            load_config(empty_config)


class TestLazyConfig:
    def test_lazy_config_error_exits(self, tmp_path):
        from unittest.mock import patch

        from click.testing import CliRunner

        from auditer.cli import cli

        # Point to a non-existent config file
        bad_path = tmp_path / "missing.yaml"
        runner = CliRunner()
        result = runner.invoke(cli, ["--config", str(bad_path), "audit", "--all"])
        assert result.exit_code != 0
        assert "Config error" in result.output or "not found" in result.output


class TestGetAccount:
    def test_found(self, sample_config_dict):
        config = load_config_from_dict(sample_config_dict)
        account = get_account(config, "test-user")
        assert account.slug == "test-user"

    def test_not_found(self, sample_config_dict):
        config = load_config_from_dict(sample_config_dict)
        with pytest.raises(ConfigError, match="not found"):
            get_account(config, "nonexistent")

    def test_not_found_suggests_close_match(self):
        data = {
            "accounts": [
                {"slug": "example-consulting", "type": "org", "username": "ec"},
                {"slug": "personal", "type": "user", "username": "me"},
            ]
        }
        config = load_config_from_dict(data)
        with pytest.raises(ConfigError) as exc_info:
            get_account(config, "example-accounting")
        assert exc_info.value.suggested_slug == "example-consulting"
        assert exc_info.value.bad_slug == "example-accounting"

    def test_not_found_no_close_match(self):
        data = {
            "accounts": [
                {"slug": "myorg", "type": "org", "username": "org"},
            ]
        }
        config = load_config_from_dict(data)
        with pytest.raises(ConfigError) as exc_info:
            get_account(config, "zzz-completely-different")
        assert exc_info.value.suggested_slug is None
        assert exc_info.value.bad_slug == "zzz-completely-different"


class TestProjectsParsing:
    def test_no_projects_key(self, sample_config_dict):
        """Omitting projects is fine — backward compatible."""
        config = load_config_from_dict(sample_config_dict)
        assert config.projects == []

    def test_projects_parsed(self):
        data = {
            "accounts": [
                {"slug": "my-user", "type": "user", "username": "me"},
                {"slug": "my-org", "type": "org", "username": "org"},
            ],
            "projects": {
                "client-work": {
                    "description": "Client X",
                    "repos": ["my-user/web-app", "my-org/api"],
                },
            },
        }
        config = load_config_from_dict(data)
        assert len(config.projects) == 1
        assert config.projects[0].name == "client-work"
        assert config.projects[0].description == "Client X"
        assert config.projects[0].repos == ["my-user/web-app", "my-org/api"]

    def test_projects_multiple(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "proj1": {"repos": ["a/repo1"]},
                "proj2": {"repos": ["a/repo2"]},
            },
        }
        config = load_config_from_dict(data)
        assert len(config.projects) == 2
        names = {p.name for p in config.projects}
        assert names == {"proj1", "proj2"}

    def test_projects_invalid_slug_reference(self):
        data = {
            "accounts": [
                {"slug": "my-user", "type": "user", "username": "me"},
            ],
            "projects": {
                "bad": {"repos": ["nonexistent/repo"]},
            },
        }
        with pytest.raises(ConfigError, match="unknown account slug"):
            load_config_from_dict(data)

    def test_projects_empty_repos(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "empty": {"repos": []},
            },
        }
        config = load_config_from_dict(data)
        assert config.projects[0].repos == []

    def test_projects_description_optional(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "minimal": {"repos": ["a/repo1"]},
            },
        }
        config = load_config_from_dict(data)
        assert config.projects[0].description == ""

    def test_projects_invalid_repo_format(self):
        """Repo entries must contain exactly one slash."""
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "bad": {"repos": ["no-slash"]},
            },
        }
        with pytest.raises(ConfigError, match="must be in 'slug/repo-name' format"):
            load_config_from_dict(data)

    def test_projects_accounts_parsed(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
                {"slug": "b", "type": "user", "username": "b"},
            ],
            "projects": {
                "proj": {"accounts": ["a", "b"]},
            },
        }
        config = load_config_from_dict(data)
        assert config.projects[0].accounts == ["a", "b"]

    def test_projects_invalid_account_reference(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "bad": {"accounts": ["nonexistent"]},
            },
        }
        with pytest.raises(ConfigError, match="account 'nonexistent' not found"):
            load_config_from_dict(data)

    def test_projects_accounts_default_empty(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "proj": {"repos": ["a/repo1"]},
            },
        }
        config = load_config_from_dict(data)
        assert config.projects[0].accounts == []


class TestGetAccountCLI:
    """ConfigError from get_account is caught at CLI level (no traceback)."""

    def test_bad_slug_shows_friendly_error(self, tmp_path):
        import sys
        from unittest.mock import patch

        import yaml
        from click.testing import CliRunner

        from auditer.cli import cli
        from tests.conftest import strip_ansi

        data = {
            "accounts": [
                {
                    "slug": "tomgould",
                    "type": "user",
                    "username": "tg",
                    "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                },
            ]
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(data))

        cli_args = ["members", "list", "--account", "tomgo"]
        runner = CliRunner()
        with patch.object(sys, "argv", ["auditer"] + cli_args):
            result = runner.invoke(
                cli, ["--config", str(config_path)] + cli_args
            )
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Config error" in output
        assert "Did you mean: auditer members list --account tomgould" in output
        assert "Traceback" not in result.output


class TestGetProject:
    def _config_with_projects(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "client-work": {"repos": ["a/web"]},
                "open-source": {"repos": ["a/lib"]},
            },
        }
        return load_config_from_dict(data)

    def test_found(self):
        config = self._config_with_projects()
        project = get_project(config, "client-work")
        assert project.name == "client-work"

    def test_not_found(self):
        config = self._config_with_projects()
        with pytest.raises(ConfigError, match="not found"):
            get_project(config, "nonexistent")

    def test_fuzzy_match(self):
        config = self._config_with_projects()
        with pytest.raises(ConfigError) as exc_info:
            get_project(config, "client-wrok")
        assert exc_info.value.suggested_project == "client-work"


class TestGetAccountsForProject:
    def test_returns_matching_accounts(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
                {"slug": "b", "type": "org", "username": "org-b"},
                {"slug": "c", "type": "user", "username": "user-c"},
            ],
            "projects": {
                "proj": {"repos": ["a/repo1", "b/repo2"]},
            },
        }
        config = load_config_from_dict(data)
        project = get_project(config, "proj")
        accounts = get_accounts_for_project(config, project)
        slugs = {a.slug for a in accounts}
        assert slugs == {"a", "b"}

    def test_includes_accounts_from_project_accounts_field(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
                {"slug": "b", "type": "org", "username": "org-b"},
                {"slug": "c", "type": "user", "username": "user-c"},
            ],
            "projects": {
                "proj": {"accounts": ["a", "c"]},
            },
        }
        config = load_config_from_dict(data)
        project = get_project(config, "proj")
        accounts = get_accounts_for_project(config, project)
        slugs = {a.slug for a in accounts}
        assert slugs == {"a", "c"}

    def test_combines_accounts_and_repos(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
                {"slug": "b", "type": "org", "username": "org-b"},
                {"slug": "c", "type": "user", "username": "user-c"},
            ],
            "projects": {
                "proj": {
                    "accounts": ["a"],
                    "repos": ["b/repo1"],
                },
            },
        }
        config = load_config_from_dict(data)
        project = get_project(config, "proj")
        accounts = get_accounts_for_project(config, project)
        slugs = {a.slug for a in accounts}
        assert slugs == {"a", "b"}

    def test_returns_empty_for_empty_project(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
            ],
            "projects": {
                "empty": {"repos": []},
            },
        }
        config = load_config_from_dict(data)
        project = get_project(config, "empty")
        accounts = get_accounts_for_project(config, project)
        assert accounts == []


class TestDeveloperAliases:
    def test_absent_defaults_to_empty(self, sample_config_dict):
        config = load_config_from_dict(sample_config_dict)
        assert config.developer_aliases == {}

    def test_parsed(self, sample_config_dict):
        sample_config_dict["developer_aliases"] = {
            "miaLind": ["Mia Lindberg", " mia@personal.example "],
            "madler": "Mohan Adler",  # a bare string is accepted as one alias
        }
        config = load_config_from_dict(sample_config_dict)
        assert config.developer_aliases == {
            "miaLind": ["Mia Lindberg", "mia@personal.example"],
            "madler": ["Mohan Adler"],
        }

    def test_rejects_non_mapping(self, sample_config_dict):
        sample_config_dict["developer_aliases"] = ["miaLind"]
        with pytest.raises(ConfigError, match="developer_aliases"):
            load_config_from_dict(sample_config_dict)

    def test_rejects_empty_alias(self, sample_config_dict):
        sample_config_dict["developer_aliases"] = {"miaLind": ["", "x"]}
        with pytest.raises(ConfigError, match="miaLind"):
            load_config_from_dict(sample_config_dict)


class TestLocalOnly:
    def test_defaults_false(self, sample_config_dict):
        config = load_config_from_dict(sample_config_dict)
        assert config.accounts[0].local_only is False

    def test_parsed(self, sample_config_dict):
        sample_config_dict["accounts"][0]["local_only"] = True
        config = load_config_from_dict(sample_config_dict)
        assert config.accounts[0].local_only is True


class TestTimezone:
    _ACCOUNTS = [{"slug": "a", "type": "user", "username": "a"}]

    def test_defaults_to_london(self):
        from auditer.config import load_config_from_dict

        assert load_config_from_dict({"accounts": self._ACCOUNTS}).timezone == "Europe/London"

    def test_configured_timezone(self):
        from auditer.config import load_config_from_dict

        cfg = load_config_from_dict(
            {"defaults": {"timezone": "America/New_York"}, "accounts": self._ACCOUNTS}
        )
        assert cfg.timezone == "America/New_York"

    def test_unknown_timezone_rejected(self):
        from auditer.config import ConfigError, load_config_from_dict

        with pytest.raises(ConfigError, match="timezone"):
            load_config_from_dict(
                {"defaults": {"timezone": "Mars/Olympus"}, "accounts": self._ACCOUNTS}
            )
