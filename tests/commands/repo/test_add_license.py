import httpx
import pytest
import respx
from click.testing import CliRunner

from auditer.cli import cli
from auditer.licenses import LICENSES
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


class TestAddLicenseSingleRepo:
    @respx.mock
    def test_add_mit_license(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser", "name": "Test User"}
            )
        )
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/contents/LICENSE"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        respx.put(
            "https://api.github.com/repos/testuser/my-repo/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "content": {"name": "LICENSE", "sha": "abc123"},
                    "commit": {"sha": "def456", "message": "Add MIT license"},
                },
            )
        )

        result = invoke([
            "add-license", "--account", "myuser", "--repo", "my-repo",
            "--license", "MIT",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added MIT license" in output

    @respx.mock
    def test_skip_existing_license(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser", "name": "Test User"}
            )
        )
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                200, json={"name": "LICENSE", "sha": "existing"}
            )
        )

        result = invoke([
            "add-license", "--account", "myuser", "--repo", "my-repo",
            "--license", "MIT",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "already has a LICENSE" in output

    @respx.mock
    def test_custom_owner_name(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/contents/LICENSE"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        route = respx.put(
            "https://api.github.com/repos/testuser/my-repo/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "content": {"name": "LICENSE", "sha": "abc123"},
                    "commit": {"sha": "def456", "message": "Add MIT license"},
                },
            )
        )

        result = invoke([
            "add-license", "--account", "myuser", "--repo", "my-repo",
            "--license", "MIT", "--owner-name", "John Doe",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added MIT license" in output

        # Verify the request body contains the owner name
        import base64
        import json

        body = json.loads(route.calls[0].request.content)
        license_content = base64.b64decode(body["content"]).decode()
        assert "John Doe" in license_content


class TestAddLicenseAllRepos:
    @respx.mock
    def test_add_to_missing_repos(self, invoke):
        repos = [
            {"name": "no-license", "fork": False, "private": False},
            {"name": "has-license", "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser", "name": "Test User"}
            )
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.get(
            "https://api.github.com/repos/testuser/no-license/contents/LICENSE"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        respx.get(
            "https://api.github.com/repos/testuser/has-license/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                200, json={"name": "LICENSE", "sha": "existing"}
            )
        )
        respx.put(
            "https://api.github.com/repos/testuser/no-license/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "content": {"name": "LICENSE", "sha": "abc"},
                    "commit": {"sha": "def", "message": "Add Apache-2.0 license"},
                },
            )
        )

        result = invoke([
            "add-license", "--account", "myuser", "--license", "Apache-2.0",
        ], input="y\n")
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added Apache-2.0 license to 1 repo(s)" in output

    @respx.mock
    def test_all_repos_have_license(self, invoke):
        repos = [{"name": "has-license", "fork": False, "private": False}]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser", "name": "Test User"}
            )
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.get(
            "https://api.github.com/repos/testuser/has-license/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                200, json={"name": "LICENSE", "sha": "existing"}
            )
        )

        result = invoke([
            "add-license", "--account", "myuser", "--license", "MIT",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "All repos already have a LICENSE" in output

    @respx.mock
    def test_excludes_repos(self, invoke):
        repos = [
            {"name": "excluded-repo", "fork": False, "private": False},
            {"name": "has-license", "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser", "name": "Test User"}
            )
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.get(
            "https://api.github.com/repos/testuser/has-license/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                200, json={"name": "LICENSE", "sha": "existing"}
            )
        )

        result = invoke([
            "add-license", "--account", "myuser", "--license", "MIT",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Excluded" in output
        assert "All repos already have a LICENSE" in output


class TestAddLicenseBitbucket:
    @respx.mock
    def test_bitbucket_add_license(self, runner, tmp_path):
        """Smoke test: add-license works end-to-end with a bitbucket-provider account."""
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

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "my-repo",
                    "owner": {"nickname": "myworkspace"},
                    "mainbranch": {"name": "main"},
                    "links": {"html": {"href": "https://bitbucket.org/myworkspace/my-repo"}},
                },
            )
        )
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/src/main/LICENSE"
        ).mock(return_value=httpx.Response(404, json={"error": {"message": "Not Found"}}))
        respx.post(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/src"
        ).mock(return_value=httpx.Response(201))

        result = runner.invoke(
            cli,
            [
                "--config", str(config_path),
                "add-license", "--account", "bb",
                "--repo", "my-repo", "--license", "MIT",
            ],
            input="y\n",
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Added MIT license" in output


class TestAddLicenseAuth:
    @respx.mock
    def test_auth_error(self, invoke):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        result = invoke([
            "add-license", "--account", "myuser", "--repo", "my-repo",
            "--license", "MIT",
        ])
        output = strip_ansi(result.output)
        assert result.exit_code != 0
        assert "Authentication failed" in output


class TestProjectFilter:
    @respx.mock
    def test_project_filters_repos(self, runner, tmp_path):
        """--project filters add-license to only repos in the project."""
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
                "my-proj": {"repos": ["myuser/no-license"]},
            },
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_dict))

        repos = [
            {"name": "no-license", "fork": False, "private": False},
            {"name": "other-repo", "fork": False, "private": False},
        ]
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser", "name": "Test User"}
            )
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        respx.get(
            "https://api.github.com/repos/testuser/no-license/contents/LICENSE"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        respx.put(
            "https://api.github.com/repos/testuser/no-license/contents/LICENSE"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "content": {"name": "LICENSE", "sha": "abc"},
                    "commit": {"sha": "def", "message": "Add MIT license"},
                },
            )
        )

        result = runner.invoke(
            cli,
            ["--config", str(config_path), "add-license", "--account", "myuser",
             "--license", "MIT", "--project", "my-proj"],
            input="y\n",
        )
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Filtered to" in output
        assert "Added MIT license to 1 repo(s)" in output


class TestLicenseTemplates:
    def test_all_templates_have_placeholders(self):
        for key, template in LICENSES.items():
            rendered = template.format(year=2026, owner="Test Owner")
            assert "2026" in rendered, f"{key} missing year placeholder"
            assert "Test Owner" in rendered, f"{key} missing owner placeholder"

    def test_all_license_keys_present(self):
        expected = {"MIT", "Apache-2.0", "GPL-3.0", "BSD-2-Clause", "BSD-3-Clause"}
        assert set(LICENSES.keys()) == expected
