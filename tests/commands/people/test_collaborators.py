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


COLLABS = [
    {
        "login": "alice",
        "permissions": {"admin": True, "push": True, "pull": True, "maintain": False, "triage": False},
    },
    {
        "login": "bob",
        "permissions": {"admin": False, "push": True, "pull": True, "maintain": False, "triage": False},
    },
]


class TestCollaboratorsList:
    @respx.mock
    def test_list_single_repo(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/collaborators"
        ).mock(return_value=httpx.Response(200, json=COLLABS))

        result = invoke(["collaborators", "list", "--account", "myuser", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "alice" in output
        assert "bob" in output

    @respx.mock
    def test_list_all_repos(self, invoke):
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
        respx.get(
            "https://api.github.com/repos/testuser/repo1/collaborators"
        ).mock(return_value=httpx.Response(200, json=COLLABS))
        respx.get(
            "https://api.github.com/repos/testuser/repo2/collaborators"
        ).mock(return_value=httpx.Response(200, json=[]))

        result = invoke(["collaborators", "list", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "repo1" in output
        assert "repo2" in output

    @respx.mock
    def test_list_all_repos_excludes(self, invoke):
        repos = [
            {"name": "repo1", "fork": False, "private": False},
            {"name": "excluded-repo", "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.get(
            "https://api.github.com/repos/testuser/repo1/collaborators"
        ).mock(return_value=httpx.Response(200, json=COLLABS))

        result = invoke(["collaborators", "list", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Excluded" in output


class TestCollaboratorsListBitbucket:
    @respx.mock
    def test_list_single_repo_bitbucket(self, runner, tmp_path):
        """Smoke test: 'collaborators list' works with a bitbucket-provider account."""
        import yaml

        config_dict = {
            "defaults": {
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

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
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
                            "repository": {"name": "my-repo"},
                        }
                    ]
                },
            )
        )

        result = runner.invoke(
            cli,
            [
                "--config", str(config_path),
                "collaborators", "list",
                "--account", "bb", "--repo", "my-repo",
            ],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "alice" in output


class TestCollaboratorsAddRemoveBitbucketGuard:
    def _write_bb_config(self, tmp_path):
        import yaml

        config_dict = {
            "defaults": {
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

    def test_add_guard_bitbucket(self, runner, tmp_path):
        config_path = self._write_bb_config(tmp_path)
        result = runner.invoke(
            cli,
            [
                "--config", str(config_path),
                "collaborators", "add", "alice",
                "--account", "bb", "--repo", "my-repo",
            ],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 1
        assert "not implemented for Bitbucket" in output

    def test_remove_guard_bitbucket(self, runner, tmp_path):
        config_path = self._write_bb_config(tmp_path)
        result = runner.invoke(
            cli,
            [
                "--config", str(config_path),
                "collaborators", "remove", "alice",
                "--account", "bb", "--repo", "my-repo",
            ],
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 1
        assert "not implemented for Bitbucket" in output


class TestCollaboratorsListAuth:
    @respx.mock
    def test_list_auth_error(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        result = invoke(["collaborators", "list", "--account", "myuser", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Authentication failed" in output

    @respx.mock
    def test_list_empty_collabs(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/collaborators"
        ).mock(return_value=httpx.Response(200, json=[]))

        result = invoke(["collaborators", "list", "--account", "myuser", "--repo", "my-repo"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "my-repo" in output


class TestCollaboratorsAdd:
    @respx.mock
    def test_add_single_repo(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.put(
            "https://api.github.com/repos/testuser/my-repo/collaborators/alice"
        ).mock(return_value=httpx.Response(201, json={"id": 1}))

        result = invoke([
            "collaborators", "add", "alice",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added 'alice'" in output

    @respx.mock
    def test_add_with_permission(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.put(
            "https://api.github.com/repos/testuser/my-repo/collaborators/alice"
        ).mock(return_value=httpx.Response(201, json={"id": 1}))

        result = invoke([
            "collaborators", "add", "alice",
            "--account", "myuser", "--repo", "my-repo",
            "--permission", "admin",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "admin" in output

    @respx.mock
    def test_add_all_repos(self, invoke):
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
        respx.put(
            "https://api.github.com/repos/testuser/repo1/collaborators/alice"
        ).mock(return_value=httpx.Response(201, json={"id": 1}))
        respx.put(
            "https://api.github.com/repos/testuser/repo2/collaborators/alice"
        ).mock(return_value=httpx.Response(201, json={"id": 1}))

        result = invoke([
            "collaborators", "add", "alice",
            "--account", "myuser", "--all-repos",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added 'alice' to 2 repo(s)" in output

    def test_add_no_repo_or_all(self, invoke):
        result = invoke([
            "collaborators", "add", "alice", "--account", "myuser",
        ])
        assert result.exit_code != 0

    def test_add_all_repos_aborted(self, invoke):
        result = invoke([
            "collaborators", "add", "alice",
            "--account", "myuser", "--all-repos",
        ], input="n\n")
        assert result.exit_code != 0


class TestCollaboratorsRemove:
    @respx.mock
    def test_remove_single_repo(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.delete(
            "https://api.github.com/repos/testuser/my-repo/collaborators/alice"
        ).mock(return_value=httpx.Response(204))

        result = invoke([
            "collaborators", "remove", "alice",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Removed 'alice'" in output

    @respx.mock
    def test_remove_not_found(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.delete(
            "https://api.github.com/repos/testuser/my-repo/collaborators/ghost"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

        result = invoke([
            "collaborators", "remove", "ghost",
            "--account", "myuser", "--repo", "my-repo",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "not found" in output

    @respx.mock
    def test_remove_all_repos(self, invoke):
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
        respx.delete(
            "https://api.github.com/repos/testuser/repo1/collaborators/alice"
        ).mock(return_value=httpx.Response(204))
        respx.delete(
            "https://api.github.com/repos/testuser/repo2/collaborators/alice"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

        result = invoke([
            "collaborators", "remove", "alice",
            "--account", "myuser", "--all-repos",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Removed 'alice' from 1 repo(s)" in output
        assert "1 not found" in output

    def test_remove_no_repo_or_all(self, invoke):
        result = invoke([
            "collaborators", "remove", "alice", "--account", "myuser",
        ])
        assert result.exit_code != 0
