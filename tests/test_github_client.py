import base64
import logging
import time
from unittest.mock import patch

import httpx
import pytest
import respx

from auditer.github_client import (
    AuthenticationError,
    GitHubAPIError,
    GitHubClient,
    RateLimitError,
)
from auditer.models import AccountConfig


@pytest.fixture
def account():
    return AccountConfig(
        slug="test",
        type="user",
        username="testuser",
        token="ghp_testtoken1234567890abcdefghijklmnopq",
    )


@pytest.fixture
def account_org():
    return AccountConfig(
        slug="test-org",
        type="org",
        username="myorg",
        token="ghp_testtoken1234567890abcdefghijklmnopq",
    )


@pytest.fixture
def account_no_token():
    return AccountConfig(
        slug="public",
        type="user",
        username="someone",
        token=None,
        include_private=False,
    )


class TestAuthentication:
    @respx.mock
    def test_auth_error(self, account):
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            with pytest.raises(AuthenticationError, match="Authentication failed"):
                client.list_repos()

    @respx.mock
    def test_validate_token(self, account):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        with GitHubClient(account) as client:
            username = client.validate_token()
            assert username == "testuser"

    @respx.mock
    def test_validate_token_caches_login(self, account):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        with GitHubClient(account) as client:
            assert client._authenticated_login is None
            client.validate_token()
            assert client._authenticated_login == "testuser"

    def test_validate_token_no_token(self, account_no_token):
        with GitHubClient(account_no_token) as client:
            assert client.validate_token() is None


def _rate_limited(reset: str = "9999999999") -> httpx.Response:
    return httpx.Response(
        403,
        json={"message": "rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset},
    )


class TestRateLimit:
    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_rate_limit_waits_then_retries(self, mock_sleep, account):
        """A 403 with remaining=0 sleeps until the reset, then retries once."""
        reset_at = int(time.time()) + 120
        route = respx.get("https://api.github.com/user/repos")
        route.side_effect = [
            _rate_limited(str(reset_at)),
            httpx.Response(200, json=[{"name": "repo1"}]),
        ]
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            result = client.list_repos()
        assert [r["name"] for r in result] == ["repo1"]
        assert route.call_count == 2
        mock_sleep.assert_called_once()
        waited = mock_sleep.call_args[0][0]
        # ~120s to the reset plus the safety margin
        assert 120 <= waited <= 120 + GitHubClient.RATE_LIMIT_RESET_MARGIN + 1

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_rate_limit_wait_is_capped(self, mock_sleep, account):
        """A reset far in the future never sleeps longer than the cap."""
        route = respx.get("https://api.github.com/user/repos")
        route.side_effect = [
            _rate_limited("9999999999"),
            httpx.Response(200, json=[]),
        ]
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            client.list_repos()
        assert mock_sleep.call_args[0][0] == GitHubClient.RATE_LIMIT_MAX_WAIT

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_rate_limit_error_after_waiting(self, mock_sleep, account):
        """Still rate-limited after the wait → RateLimitError, no second wait."""
        respx.get("https://api.github.com/user/repos").mock(
            return_value=_rate_limited()
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            with pytest.raises(RateLimitError, match="Rate limit"):
                client.list_repos()
        mock_sleep.assert_called_once()

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_rate_limit_logged_once_per_reset(self, mock_sleep, account, caplog):
        """Concurrent workers hitting the same window log one warning."""
        reset = str(int(time.time()) + 60)
        respx.get("https://api.github.com/user").mock(
            side_effect=[
                _rate_limited(reset), httpx.Response(200, json={"login": "a"}),
                _rate_limited(reset), httpx.Response(200, json={"login": "a"}),
            ]
        )
        GitHubClient._rate_limit_notified_reset = 0
        with caplog.at_level(logging.WARNING, logger="auditer.github_client"):
            with GitHubClient(account) as c1, GitHubClient(account) as c2:
                c1.get_authenticated_user()
                c2.get_authenticated_user()
        warnings = [r for r in caplog.records if "rate limit exhausted" in r.message]
        assert len(warnings) == 1
        assert "Waiting" in warnings[0].message
        assert mock_sleep.call_count == 2  # both still wait

    @respx.mock
    def test_last_rate_limit_captured_from_headers(self, account):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, json={"login": "testuser"},
                headers={
                    "x-ratelimit-limit": "5000", "x-ratelimit-remaining": "3175",
                    "x-ratelimit-used": "1825", "x-ratelimit-reset": "1791569469",
                },
            )
        )
        with GitHubClient(account) as client:
            assert client.last_rate_limit is None
            client.validate_token()
            assert client.last_rate_limit == {
                "limit": 5000, "remaining": 3175, "used": 1825, "reset": 1791569469,
            }

    @respx.mock
    def test_get_rate_limit(self, account):
        respx.get("https://api.github.com/rate_limit").mock(
            return_value=httpx.Response(
                200,
                json={"resources": {"core": {"remaining": 4999}}},
            )
        )
        with GitHubClient(account) as client:
            data = client.get_rate_limit()
            assert data["resources"]["core"]["remaining"] == 4999


class TestPagination:
    @respx.mock
    def test_single_page(self, account):
        repos = [{"name": "repo1"}, {"name": "repo2"}]
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            result = client.get_paginated("/user/repos")
            assert len(result) == 2

    @respx.mock
    def test_multi_page(self, account):
        page1 = [{"name": "repo1"}]
        page2 = [{"name": "repo2"}]

        route = respx.get("https://api.github.com/user/repos")
        route.side_effect = [
            httpx.Response(
                200,
                json=page1,
                headers={"link": '<https://api.github.com/user/repos?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=page2),
        ]

        with GitHubClient(account) as client:
            result = client.get_paginated("/user/repos")
            assert len(result) == 2
            assert result[0]["name"] == "repo1"
            assert result[1]["name"] == "repo2"


class TestListRepos:
    @respx.mock
    def test_user_repos_authenticated(self, account):
        repos = [
            {"name": "r1", "fork": False, "private": False},
            {"name": "r2", "fork": True, "private": False},
        ]
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            result = client.list_repos()
            assert len(result) == 2

    @respx.mock
    def test_user_repos_exclude_forks(self, account):
        account.include_forks = False
        repos = [
            {"name": "r1", "fork": False, "private": False},
            {"name": "r2", "fork": True, "private": False},
        ]
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            result = client.list_repos()
            assert len(result) == 1
            assert result[0]["name"] == "r1"

    @respx.mock
    def test_org_repos(self, account_org):
        repos = [{"name": "org-repo", "fork": False, "private": False}]
        respx.get("https://api.github.com/orgs/myorg/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account_org) as client:
            result = client.list_repos()
            assert len(result) == 1

    @respx.mock
    def test_public_repos_no_token(self, account_no_token):
        repos = [{"name": "pub-repo", "fork": False, "private": False}]
        respx.get("https://api.github.com/users/someone/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account_no_token) as client:
            result = client.list_repos()
            assert len(result) == 1

    @respx.mock
    def test_third_party_with_token_uses_users_endpoint(self):
        """Token holder differs from account.username — use /users/{username}/repos."""
        account = AccountConfig(
            slug="third-party",
            type="user",
            username="famous-dev",
            token="ghp_testtoken1234567890abcdefghijklmnopq",
            include_private=False,
        )
        repos = [{"name": "cool-project", "fork": False, "private": False}]
        respx.get("https://api.github.com/users/famous-dev/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "my-own-user"
            result = client.list_repos()
            assert len(result) == 1
            assert result[0]["name"] == "cool-project"

    @respx.mock
    def test_own_account_with_token_uses_user_endpoint(self):
        """Token holder matches account.username — use /user/repos."""
        account = AccountConfig(
            slug="mine",
            type="user",
            username="myuser",
            token="ghp_testtoken1234567890abcdefghijklmnopq",
        )
        repos = [{"name": "my-repo", "fork": False, "private": True}]
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "myuser"
            result = client.list_repos()
            assert len(result) == 1
            assert result[0]["name"] == "my-repo"

    @respx.mock
    def test_own_account_case_insensitive(self):
        """Username comparison is case-insensitive."""
        account = AccountConfig(
            slug="mine",
            type="user",
            username="MyUser",
            token="ghp_testtoken1234567890abcdefghijklmnopq",
        )
        repos = [{"name": "my-repo", "fork": False, "private": False}]
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "myuser"
            result = client.list_repos()
            assert len(result) == 1

    @respx.mock
    def test_skip_visibility_filter(self, account):
        """apply_visibility_filter=False returns all repos including forks."""
        account.include_forks = False
        repos = [
            {"name": "r1", "fork": False, "private": False},
            {"name": "r2", "fork": True, "private": False},
        ]
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            client._authenticated_login = "testuser"
            result = client.list_repos(apply_visibility_filter=False)
            assert len(result) == 2
            names = {r["name"] for r in result}
            assert names == {"r1", "r2"}

    @respx.mock
    def test_lazy_fallback_when_validate_not_called(self):
        """If validate_token() wasn't called, list_repos() calls it lazily."""
        account = AccountConfig(
            slug="third-party",
            type="user",
            username="famous-dev",
            token="ghp_testtoken1234567890abcdefghijklmnopq",
            include_private=False,
        )
        repos = [{"name": "cool-project", "fork": False, "private": False}]
        # Lazy fallback calls /user to determine authenticated login
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "my-own-user"})
        )
        respx.get("https://api.github.com/users/famous-dev/repos").mock(
            return_value=httpx.Response(200, json=repos)
        )
        with GitHubClient(account) as client:
            # _authenticated_login is None — lazy fallback triggers
            assert client._authenticated_login is None
            result = client.list_repos()
            assert len(result) == 1
            assert client._authenticated_login == "my-own-user"


class TestHasReadme:
    @respx.mock
    def test_readme_exists(self, account):
        respx.get("https://api.github.com/repos/testuser/my-repo/readme").mock(
            return_value=httpx.Response(200, json={"name": "README.md"})
        )
        with GitHubClient(account) as client:
            assert client.has_readme("testuser", "my-repo") is True

    @respx.mock
    def test_readme_missing(self, account):
        respx.get("https://api.github.com/repos/testuser/my-repo/readme").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with GitHubClient(account) as client:
            assert client.has_readme("testuser", "my-repo") is False


class TestGetRepo:
    @respx.mock
    def test_returns_repo_data(self, account):
        repo_data = {
            "name": "my-repo",
            "owner": {"login": "testuser"},
            "html_url": "https://github.com/testuser/my-repo",
            "stargazers_count": 42,
        }
        respx.get("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(200, json=repo_data)
        )
        with GitHubClient(account) as client:
            result = client.get_repo("testuser", "my-repo")
            assert result is not None
            assert result["name"] == "my-repo"
            assert result["stargazers_count"] == 42

    @respx.mock
    def test_returns_none_on_404(self, account):
        respx.get("https://api.github.com/repos/testuser/deleted-repo").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with GitHubClient(account) as client:
            result = client.get_repo("testuser", "deleted-repo")
            assert result is None


class TestRetry:
    @respx.mock
    def test_retry_on_500(self, account):
        route = respx.get("https://api.github.com/user")
        route.side_effect = [
            httpx.Response(500, json={"message": "Internal Server Error"}),
            httpx.Response(200, json={"login": "testuser"}),
        ]
        with GitHubClient(account) as client:
            client.RETRY_BACKOFF = 0.01  # speed up test
            user = client.get_authenticated_user()
            assert user["login"] == "testuser"

    @respx.mock
    def test_retry_exhausted(self, account):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(500, json={"message": "error"})
        )
        with GitHubClient(account) as client:
            client.RETRY_BACKOFF = 0.01
            with pytest.raises(GitHubAPIError, match="Server error"):
                client.get_authenticated_user()


class TestListOrgMembers:
    @respx.mock
    def test_returns_member_list(self, account_org):
        members = [
            {"login": "alice", "id": 1},
            {"login": "bob", "id": 2},
        ]
        respx.get("https://api.github.com/orgs/myorg/members").mock(
            return_value=httpx.Response(200, json=members)
        )
        with GitHubClient(account_org) as client:
            result = client.list_org_members("myorg")
            assert len(result) == 2
            assert result[0]["login"] == "alice"

    @respx.mock
    def test_returns_empty_list(self, account_org):
        respx.get("https://api.github.com/orgs/myorg/members").mock(
            return_value=httpx.Response(200, json=[])
        )
        with GitHubClient(account_org) as client:
            result = client.list_org_members("myorg")
            assert result == []


class TestGetUser:
    @respx.mock
    def test_returns_user_data(self, account):
        user_data = {"login": "alice", "id": 1, "name": "Alice"}
        respx.get("https://api.github.com/users/alice").mock(
            return_value=httpx.Response(200, json=user_data)
        )
        with GitHubClient(account) as client:
            result = client.get_user("alice")
            assert result is not None
            assert result["login"] == "alice"

    @respx.mock
    def test_returns_none_on_404(self, account):
        respx.get("https://api.github.com/users/ghost").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with GitHubClient(account) as client:
            result = client.get_user("ghost")
            assert result is None


class TestRemoveOrgMember:
    @respx.mock
    def test_204_returns_true(self, account_org):
        respx.delete("https://api.github.com/orgs/myorg/members/alice").mock(
            return_value=httpx.Response(204)
        )
        with GitHubClient(account_org) as client:
            assert client.remove_org_member("myorg", "alice") is True

    @respx.mock
    def test_404_returns_false(self, account_org):
        respx.delete("https://api.github.com/orgs/myorg/members/ghost").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with GitHubClient(account_org) as client:
            assert client.remove_org_member("myorg", "ghost") is False


class TestListCollaborators:
    @respx.mock
    def test_returns_collaborator_list(self, account):
        collabs = [
            {"login": "alice", "permissions": {"push": True}},
            {"login": "bob", "permissions": {"push": False}},
        ]
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/collaborators"
        ).mock(return_value=httpx.Response(200, json=collabs))
        with GitHubClient(account) as client:
            result = client.list_collaborators("testuser", "my-repo")
            assert len(result) == 2
            assert result[0]["login"] == "alice"


class TestAddCollaborator:
    @respx.mock
    def test_201_returns_true(self, account):
        respx.put(
            "https://api.github.com/repos/testuser/my-repo/collaborators/alice"
        ).mock(return_value=httpx.Response(201, json={"id": 1}))
        with GitHubClient(account) as client:
            assert client.add_collaborator("testuser", "my-repo", "alice") is True

    @respx.mock
    def test_204_returns_true(self, account):
        respx.put(
            "https://api.github.com/repos/testuser/my-repo/collaborators/alice"
        ).mock(return_value=httpx.Response(204))
        with GitHubClient(account) as client:
            assert client.add_collaborator(
                "testuser", "my-repo", "alice", permission="admin"
            ) is True


class TestRemoveCollaborator:
    @respx.mock
    def test_204_returns_true(self, account):
        respx.delete(
            "https://api.github.com/repos/testuser/my-repo/collaborators/alice"
        ).mock(return_value=httpx.Response(204))
        with GitHubClient(account) as client:
            assert client.remove_collaborator("testuser", "my-repo", "alice") is True

    @respx.mock
    def test_404_returns_false(self, account):
        respx.delete(
            "https://api.github.com/repos/testuser/my-repo/collaborators/ghost"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        with GitHubClient(account) as client:
            assert client.remove_collaborator("testuser", "my-repo", "ghost") is False


class TestUpdateRepo:
    @respx.mock
    def test_returns_updated_repo(self, account):
        updated = {"name": "my-repo", "archived": True}
        respx.patch("https://api.github.com/repos/testuser/my-repo").mock(
            return_value=httpx.Response(200, json=updated)
        )
        with GitHubClient(account) as client:
            result = client.update_repo("testuser", "my-repo", archived=True)
            assert result["archived"] is True


class TestGetTopics:
    @respx.mock
    def test_returns_topic_names(self, account):
        respx.get("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(200, json={"names": ["python", "cli"]})
        )
        with GitHubClient(account) as client:
            result = client.get_topics("testuser", "my-repo")
            assert result == ["python", "cli"]


class TestSetTopics:
    @respx.mock
    def test_returns_updated_topics(self, account):
        respx.put("https://api.github.com/repos/testuser/my-repo/topics").mock(
            return_value=httpx.Response(200, json={"names": ["python", "cli", "audit"]})
        )
        with GitHubClient(account) as client:
            result = client.set_topics("testuser", "my-repo", ["python", "cli", "audit"])
            assert result == ["python", "cli", "audit"]


class TestGetFile:
    @respx.mock
    def test_returns_file_data(self, account):
        file_data = {
            "name": "README.md",
            "path": "README.md",
            "sha": "abc123",
            "content": base64.b64encode(b"# Hello").decode(),
            "encoding": "base64",
        }
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/contents/README.md"
        ).mock(return_value=httpx.Response(200, json=file_data))
        with GitHubClient(account) as client:
            result = client.get_file("testuser", "my-repo", "README.md")
            assert result is not None
            assert result["sha"] == "abc123"

    @respx.mock
    def test_returns_none_on_404(self, account):
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/contents/MISSING.md"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        with GitHubClient(account) as client:
            result = client.get_file("testuser", "my-repo", "MISSING.md")
            assert result is None


class TestTransportErrorRetry:
    @respx.mock
    def test_retries_on_connect_error(self, account):
        route = respx.get("https://api.github.com/user")
        route.side_effect = [
            httpx.ConnectError("connection refused"),
            httpx.Response(200, json={"login": "testuser"}),
        ]
        with GitHubClient(account) as client:
            client.RETRY_BACKOFF = 0.01
            user = client.get_authenticated_user()
            assert user["login"] == "testuser"

    @respx.mock
    def test_transport_error_exhausted(self, account):
        respx.get("https://api.github.com/user").mock(
            side_effect=httpx.ConnectError("fail")
        )
        with GitHubClient(account) as client:
            client.RETRY_BACKOFF = 0.01
            with pytest.raises(GitHubAPIError, match="Network error"):
                client.get_authenticated_user()


class TestForbiddenNonRateLimit:
    @respx.mock
    def test_403_non_ratelimit(self, account):
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                403,
                json={"message": "Repository access blocked"},
                headers={"x-ratelimit-remaining": "4999"},
            )
        )
        with GitHubClient(account) as client:
            with pytest.raises(GitHubAPIError, match="Forbidden") as exc_info:
                client.get_authenticated_user()
            assert not isinstance(exc_info.value, RateLimitError)


class TestPaginated404:
    @respx.mock
    def test_get_paginated_404(self, account):
        respx.get("https://api.github.com/orgs/ghost/repos").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with GitHubClient(account) as client:
            with pytest.raises(GitHubAPIError, match="Not found"):
                client.get_paginated("/orgs/ghost/repos")


class TestHasReadmeException:
    @respx.mock
    def test_network_error_returns_false(self, account):
        respx.get("https://api.github.com/repos/testuser/my-repo/readme").mock(
            side_effect=httpx.ConnectError("fail")
        )
        with GitHubClient(account) as client:
            client.RETRY_BACKOFF = 0.01
            assert client.has_readme("testuser", "my-repo") is False


class TestListUserEvents:
    @respx.mock
    def test_returns_events(self, account):
        events = [
            {"type": "PushEvent", "repo": {"name": "testuser/my-repo"}},
            {"type": "WatchEvent", "repo": {"name": "someone/cool"}},
        ]
        respx.get(
            "https://api.github.com/users/testuser/events/public"
        ).mock(return_value=httpx.Response(200, json=events))
        with GitHubClient(account) as client:
            result = client.list_user_events("testuser")
            assert len(result) == 2
            assert result[0]["type"] == "PushEvent"

    @respx.mock
    def test_returns_empty_on_404(self, account):
        respx.get(
            "https://api.github.com/users/ghost/events/public"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        with GitHubClient(account) as client:
            result = client.list_user_events("ghost")
            assert result == []


class TestCreateOrUpdateFile:
    @respx.mock
    def test_create_file(self, account):
        commit_data = {
            "content": {"name": "LICENSE", "sha": "def456"},
            "commit": {"sha": "abc789", "message": "Add LICENSE"},
        }
        route = respx.put(
            "https://api.github.com/repos/testuser/my-repo/contents/LICENSE"
        ).mock(return_value=httpx.Response(201, json=commit_data))

        with GitHubClient(account) as client:
            result = client.create_or_update_file(
                "testuser", "my-repo", "LICENSE", "MIT License text", "Add LICENSE"
            )
            assert result["commit"]["message"] == "Add LICENSE"

            # Verify base64 encoding in request body
            request = route.calls[0].request
            import json

            body = json.loads(request.content)
            assert body["content"] == base64.b64encode(b"MIT License text").decode()
            assert body["message"] == "Add LICENSE"
            assert "sha" not in body
            assert "branch" not in body

    @respx.mock
    def test_update_file_with_sha_and_branch(self, account):
        commit_data = {
            "content": {"name": "README.md", "sha": "new123"},
            "commit": {"sha": "xyz789", "message": "Update README"},
        }
        route = respx.put(
            "https://api.github.com/repos/testuser/my-repo/contents/README.md"
        ).mock(return_value=httpx.Response(200, json=commit_data))

        with GitHubClient(account) as client:
            result = client.create_or_update_file(
                "testuser",
                "my-repo",
                "README.md",
                "# Updated",
                "Update README",
                branch="main",
                sha="old456",
            )
            assert result["content"]["sha"] == "new123"

            import json

            body = json.loads(route.calls[0].request.content)
            assert body["sha"] == "old456"
            assert body["branch"] == "main"


class TestListRepoCommits:
    @respx.mock
    def test_returns_commits(self, account):
        commits = [
            {
                "sha": "abc123",
                "commit": {"author": {"name": "Alice", "email": "alice@example.com"}},
                "author": {"login": "alicegit"},
            },
        ]
        respx.get(
            "https://api.github.com/repos/testuser/my-repo/commits"
        ).mock(return_value=httpx.Response(200, json=commits))
        with GitHubClient(account) as client:
            result = client.list_repo_commits("testuser", "my-repo")
            assert len(result) == 1
            assert result[0]["author"]["login"] == "alicegit"

    @respx.mock
    def test_passes_since_until(self, account):
        route = respx.get(
            "https://api.github.com/repos/testuser/my-repo/commits"
        ).mock(return_value=httpx.Response(200, json=[]))
        with GitHubClient(account) as client:
            client.list_repo_commits(
                "testuser", "my-repo", since="2025-06-01", until="2025-06-30"
            )
            request = route.calls[0].request
            assert "since=2025-06-01" in str(request.url)
            assert "until=2025-06-30" in str(request.url)


class TestListPullReviews:
    @respx.mock
    def test_returns_reviews_for_one_pr(self, account):
        reviews = [
            {
                "user": {"login": "bob"},
                "state": "APPROVED",
                "submitted_at": "2025-06-02T10:00:00Z",
                "body": "Looks good",
            },
        ]
        route = respx.get(
            "https://api.github.com/repos/testuser/my-repo/pulls/42/reviews"
        ).mock(return_value=httpx.Response(200, json=reviews))
        with GitHubClient(account) as client:
            result = client.list_pull_reviews("testuser", "my-repo", 42)
        assert route.called
        assert result == reviews


class TestStopWhen:
    @respx.mock
    def test_stop_when_stops_after_matching_page(self, account):
        """The page containing the first match is kept; later pages aren't fetched."""
        route = respx.get("https://api.github.com/test")
        route.side_effect = [
            httpx.Response(
                200, json=[{"id": 1, "old": False}, {"id": 2, "old": True}],
                headers={"link": '<https://api.github.com/test?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"id": 3, "old": True}]),
        ]
        with GitHubClient(account) as client:
            result = client.get_paginated("/test", stop_when=lambda i: i["old"])
        assert [i["id"] for i in result] == [1, 2]
        assert route.call_count == 1

    @respx.mock
    def test_stop_when_no_match_fetches_all(self, account):
        route = respx.get("https://api.github.com/test")
        route.side_effect = [
            httpx.Response(
                200, json=[{"id": 1}],
                headers={"link": '<https://api.github.com/test?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"id": 2}]),
        ]
        with GitHubClient(account) as client:
            result = client.get_paginated("/test", stop_when=lambda i: False)
        assert len(result) == 2
        assert route.call_count == 2


class TestListPullsSince:
    @respx.mock
    def test_since_stops_at_first_too_old_page(self, account):
        """Listing is sort=updated desc, so pagination ends on the first page
        holding a PR last updated before `since`; that PR is still returned."""
        route = respx.get("https://api.github.com/repos/testuser/my-repo/pulls")
        route.side_effect = [
            httpx.Response(
                200,
                json=[
                    {"number": 3, "updated_at": "2025-06-10T00:00:00Z"},
                    {"number": 2, "updated_at": "2025-05-30T00:00:00Z"},
                ],
                headers={"link": '<https://api.github.com/repos/testuser/my-repo/pulls?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"number": 1, "updated_at": "2025-01-01T00:00:00Z"}]),
        ]
        with GitHubClient(account) as client:
            result = client.list_pulls("testuser", "my-repo", since="2025-06-01")
        assert [p["number"] for p in result] == [3, 2]
        assert route.call_count == 1
        params = route.calls.last.request.url.params
        assert params["sort"] == "updated" and params["direction"] == "desc"

    @respx.mock
    def test_without_since_fetches_every_page(self, account):
        route = respx.get("https://api.github.com/repos/testuser/my-repo/pulls")
        route.side_effect = [
            httpx.Response(
                200, json=[{"number": 2, "updated_at": "2025-05-30T00:00:00Z"}],
                headers={"link": '<https://api.github.com/repos/testuser/my-repo/pulls?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=[{"number": 1, "updated_at": "2025-01-01T00:00:00Z"}]),
        ]
        with GitHubClient(account) as client:
            result = client.list_pulls("testuser", "my-repo")
        assert len(result) == 2
        assert route.call_count == 2


class TestMaxPages:
    @respx.mock
    def test_max_pages_limits_pagination(self, account):
        page1 = [{"id": 1}]
        page2 = [{"id": 2}]
        page3 = [{"id": 3}]
        route = respx.get("https://api.github.com/test")
        route.side_effect = [
            httpx.Response(
                200, json=page1,
                headers={"link": '<https://api.github.com/test?page=2>; rel="next"'},
            ),
            httpx.Response(
                200, json=page2,
                headers={"link": '<https://api.github.com/test?page=3>; rel="next"'},
            ),
            httpx.Response(200, json=page3),
        ]
        with GitHubClient(account) as client:
            result = client.get_paginated("/test", max_pages=2)
            # Should stop after 2 pages even though there was a next link
            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["id"] == 2

    @respx.mock
    def test_max_pages_none_fetches_all(self, account):
        page1 = [{"id": 1}]
        page2 = [{"id": 2}]
        route = respx.get("https://api.github.com/test")
        route.side_effect = [
            httpx.Response(
                200, json=page1,
                headers={"link": '<https://api.github.com/test?page=2>; rel="next"'},
            ),
            httpx.Response(200, json=page2),
        ]
        with GitHubClient(account) as client:
            result = client.get_paginated("/test", max_pages=None)
            assert len(result) == 2


class TestRedirectsAndSecondaryLimits:
    @respx.mock
    def test_follows_repo_redirect(self, account):
        """A renamed/transferred repo answers 301 with a Location; the client
        follows it instead of reporting an API error (seen live:
        example-scraper)."""
        respx.get("https://api.github.com/repos/o/old-name").mock(
            return_value=httpx.Response(
                301, headers={"location": "https://api.github.com/repositories/42"},
            )
        )
        respx.get("https://api.github.com/repositories/42").mock(
            return_value=httpx.Response(200, json={"name": "new-name"})
        )
        with GitHubClient(account) as client:
            assert client.get_repo("o", "old-name") == {"name": "new-name"}

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_429_honours_retry_after_then_retries(self, mock_sleep, account):
        route = respx.get("https://api.github.com/repos/o/r")
        route.side_effect = [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json={"name": "r"}),
        ]
        with GitHubClient(account) as client:
            assert client.get_repo("o", "r") == {"name": "r"}
        assert route.call_count == 2
        mock_sleep.assert_called_once_with(7.0)

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_429_without_retry_after_backs_off_exponentially(self, mock_sleep, account):
        route = respx.get("https://api.github.com/repos/o/r")
        route.side_effect = [
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, json={"name": "r"}),
        ]
        with GitHubClient(account) as client:
            assert client.get_repo("o", "r") == {"name": "r"}
        base = GitHubClient.SECONDARY_RATE_LIMIT_BACKOFF
        assert [c.args[0] for c in mock_sleep.call_args_list] == [base, base * 2]

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_429_gives_up_after_max_retries(self, mock_sleep, account):
        route = respx.get("https://api.github.com/repos/o/r").mock(
            return_value=httpx.Response(429, headers={"retry-after": "1"})
        )
        with GitHubClient(account) as client:
            with pytest.raises(RateLimitError, match="Secondary rate limit"):
                client.get_repo("o", "r")
        assert route.call_count == GitHubClient.MAX_RETRIES

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_403_with_retry_after_is_a_secondary_limit(self, mock_sleep, account):
        route = respx.get("https://api.github.com/repos/o/r")
        route.side_effect = [
            httpx.Response(403, headers={"retry-after": "3"}),
            httpx.Response(200, json={"name": "r"}),
        ]
        with GitHubClient(account) as client:
            assert client.get_repo("o", "r") == {"name": "r"}
        mock_sleep.assert_called_once_with(3.0)

    @respx.mock
    @patch("auditer.github_client.time.sleep")
    def test_429_with_no_remaining_waits_for_primary_reset(self, mock_sleep, account):
        """GitHub may answer an exhausted primary limit with 429 too."""
        reset_at = int(time.time()) + 60
        route = respx.get("https://api.github.com/repos/o/r")
        route.side_effect = [
            httpx.Response(
                429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at)},
            ),
            httpx.Response(200, json={"name": "r"}),
        ]
        with GitHubClient(account) as client:
            assert client.get_repo("o", "r") == {"name": "r"}
        assert 60 <= mock_sleep.call_args[0][0] <= 60 + GitHubClient.RATE_LIMIT_RESET_MARGIN + 1


class TestPullAndIssueComments:
    @respx.mock
    def test_get_pull_returns_merged_by(self, account):
        respx.get("https://api.github.com/repos/o/r/pulls/5").mock(
            return_value=httpx.Response(
                200, json={"number": 5, "merged_by": {"login": "tomgould"}},
            )
        )
        with GitHubClient(account) as client:
            assert client.get_pull("o", "r", 5)["merged_by"] == {"login": "tomgould"}

    @respx.mock
    def test_get_pull_404_is_none(self, account):
        respx.get("https://api.github.com/repos/o/r/pulls/5").mock(
            return_value=httpx.Response(404)
        )
        with GitHubClient(account) as client:
            assert client.get_pull("o", "r", 5) is None

    @respx.mock
    def test_list_issue_comments_passes_since(self, account):
        route = respx.get("https://api.github.com/repos/o/r/issues/comments").mock(
            return_value=httpx.Response(200, json=[{"id": 1}])
        )
        with GitHubClient(account) as client:
            assert client.list_issue_comments("o", "r", since="2026-10-09") == [{"id": 1}]
        params = dict(route.calls[0].request.url.params)
        assert params["since"] == "2026-10-09"
        assert params["sort"] == "updated"


class TestEmptyRepoCommits:
    @respx.mock
    def test_409_empty_repository_is_no_commits(self, account):
        respx.get("https://api.github.com/repos/o/empty/commits").mock(
            return_value=httpx.Response(409, json={"message": "Git Repository is empty."})
        )
        with GitHubClient(account) as client:
            assert client.list_repo_commits("o", "empty", since="2026-10-09") == []

    @respx.mock
    def test_other_client_errors_still_raise(self, account):
        respx.get("https://api.github.com/repos/o/r/commits").mock(
            return_value=httpx.Response(422)
        )
        with GitHubClient(account) as client:
            with pytest.raises(httpx.HTTPStatusError):
                client.list_repo_commits("o", "r")
