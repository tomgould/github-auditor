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
def config_dict_org(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": str(tmp_path / "data"),
            "reports_dir": str(tmp_path / "reports"),
        },
        "accounts": [
            {
                "slug": "myorg",
                "type": "org",
                "username": "my-organisation",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
            },
            {
                "slug": "myuser",
                "type": "user",
                "username": "testuser",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
            },
        ],
    }


@pytest.fixture
def invoke(runner, config_dict_org, tmp_path):
    import yaml

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config_dict_org))

    def _invoke(args, input=None):
        return runner.invoke(cli, ["--config", str(config_path)] + args, input=input)

    return _invoke


@pytest.fixture
def config_dict_bitbucket(tmp_path):
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": str(tmp_path / "data"),
            "reports_dir": str(tmp_path / "reports"),
        },
        "accounts": [
            {
                "slug": "bb-team",
                "type": "user",
                "provider": "bitbucket",
                "workspace": "my-workspace",
                "username": "my-workspace-display-name",
                "auth_email": "tom@example.com",
                "token": "bb_test_api_token_1234567890",
            },
        ],
    }


@pytest.fixture
def invoke_bitbucket(runner, config_dict_bitbucket, tmp_path):
    import yaml

    config_path = tmp_path / "config_bb.yaml"
    config_path.write_text(yaml.dump(config_dict_bitbucket))

    def _invoke(args, input=None):
        return runner.invoke(cli, ["--config", str(config_path)] + args, input=input)

    return _invoke


class TestMembersList:
    @respx.mock
    def test_list_org_members(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"})
        )
        members = [
            {"login": "alice", "id": 1},
            {"login": "bob", "id": 2},
        ]
        respx.get("https://api.github.com/orgs/my-organisation/members").mock(
            return_value=httpx.Response(200, json=members)
        )

        result = invoke(["members", "list", "--account", "myorg"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "alice" in output
        assert "bob" in output
        assert "2 member(s)" in output

    @respx.mock
    def test_list_all_orgs(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"})
        )
        members = [{"login": "alice", "id": 1}]
        respx.get("https://api.github.com/orgs/my-organisation/members").mock(
            return_value=httpx.Response(200, json=members)
        )

        result = invoke(["members", "list", "--all"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "alice" in output

    def test_error_on_user_type(self, invoke):
        result = invoke(["members", "list", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert "not 'org'" in output
        assert "collaborators list --account myuser" in output

    def test_no_account_or_all(self, invoke):
        result = invoke(["members", "list"])
        assert result.exit_code != 0

    @respx.mock
    def test_list_empty_org(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"})
        )
        respx.get("https://api.github.com/orgs/my-organisation/members").mock(
            return_value=httpx.Response(200, json=[])
        )

        result = invoke(["members", "list", "--account", "myorg"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "0 member(s)" in output


class TestMembersListBitbucket:
    @respx.mock
    def test_list_workspace_members(self, invoke_bitbucket):
        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "bot"})
        )
        respx.get("https://api.bitbucket.org/2.0/workspaces/my-workspace/members").mock(
            return_value=httpx.Response(
                200,
                json={
                    "values": [
                        {"user": {"nickname": "alice", "account_id": "acc-1"}},
                        {"user": {"nickname": "bob", "account_id": "acc-2"}},
                    ]
                },
            )
        )

        result = invoke_bitbucket(["members", "list", "--account", "bb-team"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "alice" in output
        assert "bob" in output
        assert "2 member(s)" in output

    @respx.mock
    def test_list_all_includes_bitbucket_account(self, invoke_bitbucket):
        """--all must include Bitbucket accounts even though type='user'."""
        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "bot"})
        )
        respx.get("https://api.bitbucket.org/2.0/workspaces/my-workspace/members").mock(
            return_value=httpx.Response(
                200, json={"values": [{"user": {"nickname": "alice", "account_id": "acc-1"}}]}
            )
        )

        result = invoke_bitbucket(["members", "list", "--all"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "alice" in output


class TestMembersListAuth:
    @respx.mock
    def test_list_all_no_org_accounts(self, invoke, config_dict_org, tmp_path):
        """Config with only user-type accounts: --all shows warning."""
        import yaml

        runner = CliRunner()
        # Create config with only user accounts
        user_only = {
            "defaults": config_dict_org["defaults"],
            "accounts": [
                {
                    "slug": "u1", "type": "user", "username": "user1",
                    "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                },
            ],
        }
        config_path = tmp_path / "config_user.yaml"
        config_path.write_text(yaml.dump(user_only))
        result = runner.invoke(cli, ["--config", str(config_path), "members", "list", "--all"])
        output = strip_ansi(result.output)
        assert "No org-type or Bitbucket accounts configured" in output

    @respx.mock
    def test_list_auth_error(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        result = invoke(["members", "list", "--account", "myorg"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Authentication failed" in output


class TestMembersRemove:
    @respx.mock
    def test_remove_success(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"})
        )
        respx.delete("https://api.github.com/orgs/my-organisation/members/alice").mock(
            return_value=httpx.Response(204)
        )

        result = invoke(["members", "remove", "alice", "--account", "myorg"], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Removed 'alice'" in output

    @respx.mock
    def test_remove_not_found(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"})
        )
        respx.delete("https://api.github.com/orgs/my-organisation/members/ghost").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        result = invoke(["members", "remove", "ghost", "--account", "myorg"], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "not found" in output

    def test_remove_user_type_error(self, invoke):
        result = invoke(["members", "remove", "alice", "--account", "myuser"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "not 'org'" in output
        assert "collaborators remove alice --account myuser --all-repos" in output

    def test_remove_aborted(self, invoke):
        result = invoke(["members", "remove", "alice", "--account", "myorg"], input="n\n")
        assert result.exit_code != 0

    def test_remove_bitbucket_not_supported(self, invoke_bitbucket):
        result = invoke_bitbucket(["members", "remove", "alice", "--account", "bb-team"])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "not supported" in output
        assert "Atlassian Admin or SCIM" in output
