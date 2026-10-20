import pytest

from auditer.bitbucket_client import BitbucketClient
from auditer.git_provider import get_provider
from auditer.github_client import GitHubClient
from auditer.models import AccountConfig


class TestGetProvider:
    def test_github_provider_returns_github_client(self):
        account = AccountConfig(
            slug="gh",
            type="user",
            username="testuser",
            token="ghp_testtoken1234567890abcdefghijklmnopq",
            provider="github",
        )
        client = get_provider(account)
        try:
            assert isinstance(client, GitHubClient)
        finally:
            client.close()

    def test_github_is_default_provider(self):
        """provider defaults to 'github' when not set."""
        account = AccountConfig(
            slug="gh",
            type="user",
            username="testuser",
            token="ghp_testtoken1234567890abcdefghijklmnopq",
        )
        client = get_provider(account)
        try:
            assert isinstance(client, GitHubClient)
        finally:
            client.close()

    def test_bitbucket_provider_returns_bitbucket_client(self):
        account = AccountConfig(
            slug="bb",
            type="user",
            username="testuser",
            token="bb_test_api_token_1234567890",
            provider="bitbucket",
            workspace="myworkspace",
            auth_email="tom@example.com",
        )
        client = get_provider(account)
        try:
            assert isinstance(client, BitbucketClient)
        finally:
            client.close()

    def test_unknown_provider_raises_value_error(self):
        account = AccountConfig(
            slug="mystery",
            type="user",
            username="testuser",
            token="tok",
            provider="gitlab",
        )
        with pytest.raises(ValueError, match="Unknown provider 'gitlab'"):
            get_provider(account)

    def test_unknown_provider_error_mentions_account_slug(self):
        account = AccountConfig(
            slug="mystery-account",
            type="user",
            username="testuser",
            token="tok",
            provider="gitlab",
        )
        with pytest.raises(ValueError, match="mystery-account"):
            get_provider(account)
