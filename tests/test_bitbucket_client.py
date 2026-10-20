from unittest.mock import patch

import httpx
import pytest
import respx

from auditer.bitbucket_client import BitbucketClient
from auditer.github_client import AuthenticationError, GitHubAPIError, RateLimitError
from auditer.models import AccountConfig, RepoInfo


@pytest.fixture
def account():
    return AccountConfig(
        slug="test-bb",
        type="user",
        username="testuser",
        token="bb_test_api_token_1234567890",
        provider="bitbucket",
        workspace="myworkspace",
        auth_email="tom@example.com",
    )


class TestAuthentication:
    @respx.mock
    def test_auth_error(self, account):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(401, json={"error": {"message": "Bad credentials"}})
        )
        with BitbucketClient(account) as client:
            with pytest.raises(AuthenticationError, match="Authentication failed"):
                client.list_repos()

    @respx.mock
    def test_validate_token(self, account):
        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "testuser", "nickname": "testuser"})
        )
        with BitbucketClient(account) as client:
            login = client.validate_token()
            assert login == "testuser"


class TestRateLimitRetry:
    """Bitbucket's burst limiter resets in seconds (unlike GitHub's
    hour-scale window) — a 429 must be waited out and retried within the
    existing MAX_RETRIES budget, not raised immediately. Confirmed live: a
    5-worker scan across ~400 repos tripped this on the large majority of
    requests, silently losing PR/issue/review data account-wide."""

    @respx.mock
    @patch("auditer.bitbucket_client.time.sleep")
    def test_retries_after_429_then_succeeds(self, mock_sleep, account):
        route = respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "5"}, json={"error": {"message": "rate limited"}}),
                httpx.Response(200, json={"values": []}),
            ]
        )
        with BitbucketClient(account) as client:
            result = client.list_repos(apply_visibility_filter=False)
        assert result == []
        assert route.call_count == 2
        mock_sleep.assert_called_once_with(5)

    @respx.mock
    @patch("auditer.bitbucket_client.time.sleep")
    def test_raises_after_exhausting_retries_on_persistent_429(self, mock_sleep, account):
        route = respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(429, headers={"retry-after": "5"}, json={"error": {"message": "rate limited"}})
        )
        with BitbucketClient(account) as client:
            with pytest.raises(RateLimitError, match="Rate limit exceeded"):
                client.list_repos(apply_visibility_filter=False)
        assert route.call_count == BitbucketClient.MAX_RETRIES

    @respx.mock
    @patch("auditer.bitbucket_client.time.sleep")
    def test_falls_back_to_backoff_estimate_without_retry_after_header(self, mock_sleep, account):
        route = respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            side_effect=[
                httpx.Response(429, json={"error": {"message": "rate limited"}}),
                httpx.Response(200, json={"values": []}),
            ]
        )
        with BitbucketClient(account) as client:
            client.list_repos(apply_visibility_filter=False)
        assert route.call_count == 2
        mock_sleep.assert_called_once_with(int(BitbucketClient.RETRY_BACKOFF * 5))


class TestListReposPagination:
    @respx.mock
    def test_two_pages(self, account):
        page1 = {
            "values": [
                {
                    "name": "repo1",
                    "owner": {"nickname": "myworkspace"},
                    "links": {"html": {"href": "https://bitbucket.org/myworkspace/repo1"}},
                }
            ],
            "next": "https://api.bitbucket.org/2.0/repositories/myworkspace?page=2",
        }
        page2 = {
            "values": [
                {
                    "name": "repo2",
                    "owner": {"nickname": "myworkspace"},
                    "links": {"html": {"href": "https://bitbucket.org/myworkspace/repo2"}},
                }
            ],
        }
        # More specific route (with ?page=2 query) must be registered before
        # the bare base-URL route — respx matches in registration order and
        # a query-less pattern also matches requests carrying a query string.
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace?page=2").mock(
            return_value=httpx.Response(200, json=page2)
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(200, json=page1)
        )
        with BitbucketClient(account) as client:
            result = client.list_repos()
            assert len(result) == 2
            names = {r["name"] for r in result}
            assert names == {"repo1", "repo2"}

    @respx.mock
    def test_single_page_no_next(self, account):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "name": "only-repo",
                            "owner": {"nickname": "myworkspace"},
                            "links": {"html": {"href": "https://bitbucket.org/myworkspace/only-repo"}},
                        }
                    ]
                },
            )
        )
        with BitbucketClient(account) as client:
            result = client.list_repos()
            assert len(result) == 1
            assert result[0]["name"] == "only-repo"

    @respx.mock
    def test_get_paginated_max_pages_limits(self, account):
        page1 = {
            "values": [{"name": "r1"}],
            "next": "https://api.bitbucket.org/2.0/repositories/myworkspace?page=2",
        }
        page2 = {
            "values": [{"name": "r2"}],
            "next": "https://api.bitbucket.org/2.0/repositories/myworkspace?page=3",
        }
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(200, json=page1)
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace?page=2").mock(
            return_value=httpx.Response(200, json=page2)
        )
        with BitbucketClient(account) as client:
            result = client.get_paginated(
                f"/repositories/{account.workspace}", max_pages=1
            )
            # Stops after 1 page even though "next" was present
            assert len(result) == 1
            assert result[0]["name"] == "r1"

    @respx.mock
    def test_get_paginated_stop_when(self, account):
        """The page with the first matching item is kept; `next` isn't followed."""
        page1 = {
            "values": [{"name": "r1", "old": False}, {"name": "r2", "old": True}],
            "next": "https://api.bitbucket.org/2.0/repositories/myworkspace?page=2",
        }
        route1 = respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(200, json=page1)
        )
        route2 = respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace?page=2").mock(
            return_value=httpx.Response(200, json={"values": [{"name": "r3", "old": True}]})
        )
        with BitbucketClient(account) as client:
            result = client.get_paginated(
                f"/repositories/{account.workspace}", stop_when=lambda i: i["old"],
            )
        assert [r["name"] for r in result] == ["r1", "r2"]
        assert route1.called and not route2.called

    @respx.mock
    def test_get_paginated_404(self, account):
        respx.get("https://api.bitbucket.org/2.0/repositories/ghost-workspace").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not Found"}})
        )
        with BitbucketClient(account) as client:
            with pytest.raises(GitHubAPIError, match="Not found"):
                client.get_paginated("/repositories/ghost-workspace")


class TestGetRepo:
    @respx.mock
    def test_returns_none_on_404(self, account):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/deleted-repo").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Repository not found"}})
        )
        with BitbucketClient(account) as client:
            result = client.get_repo("myworkspace", "deleted-repo")
            assert result is None

    @respx.mock
    def test_returns_translated_repo(self, account, sample_bitbucket_api_repo):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(200, json=sample_bitbucket_api_repo)
        )
        with BitbucketClient(account) as client:
            result = client.get_repo("myworkspace", "my-repo")
        assert result is not None
        assert result["name"] == "my-repo"


class TestTranslatedRepoShape:
    """Verify the translated repo dict has exactly what RepoInfo.from_api_response() expects."""

    @respx.mock
    def test_translated_repo_matches_repoinfo_fields(self, account, sample_bitbucket_api_repo):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(200, json=sample_bitbucket_api_repo)
        )
        with BitbucketClient(account) as client:
            result = client.get_repo("myworkspace", "my-repo")

        assert result is not None

        # Exact key set RepoInfo.from_api_response() reads from a repo dict.
        expected_keys = {
            "name",
            "owner",
            "html_url",
            "private",
            "description",
            "language",
            "stargazers_count",
            "forks_count",
            "open_issues_count",
            "license",
            "pushed_at",
            "fork",
            "archived",
            "topics",
            "default_branch",
            "size",
        }
        assert expected_keys.issubset(result.keys())
        assert result["owner"] == {"login": "myworkspace"}

        # RepoInfo.from_api_response() must be able to consume this dict directly.
        repo_info = RepoInfo.from_api_response(result, has_readme=True)
        assert repo_info.name == "my-repo"
        assert repo_info.owner == "myworkspace"
        assert repo_info.url == "https://bitbucket.org/myworkspace/my-repo"
        assert repo_info.private is False
        assert repo_info.description == "A test repository"
        assert repo_info.language == "python"
        assert repo_info.stars == 0
        assert repo_info.forks == 0
        assert repo_info.open_issues == 0
        assert repo_info.license == ""
        assert repo_info.has_readme is True
        assert repo_info.pushed_at == "2025-01-15T10:00:00.000000+00:00"
        assert repo_info.is_fork is False
        assert repo_info.archived is False
        assert repo_info.topics == []
        assert repo_info.default_branch == "main"
        assert repo_info.size_kb == 1024  # 1048576 bytes / 1024

    @respx.mock
    def test_fork_detected_via_parent_key(self, account, sample_bitbucket_api_repo):
        sample_bitbucket_api_repo["parent"] = {"full_name": "otherworkspace/my-repo"}
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(200, json=sample_bitbucket_api_repo)
        )
        with BitbucketClient(account) as client:
            result = client.get_repo("myworkspace", "my-repo")
        assert result["fork"] is True


class TestListPulls:
    @respx.mock
    def test_translates_merged_pr(self, account, sample_bitbucket_api_pr):
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests"
        ).mock(return_value=httpx.Response(200, json={"values": [sample_bitbucket_api_pr]}))
        with BitbucketClient(account) as client:
            result = client.list_pulls("myworkspace", "my-repo")
        assert len(result) == 1
        pr = result[0]
        assert pr["number"] == 5
        assert pr["title"] == "Add feature X"
        assert pr["user"]["login"] == "alice"
        assert pr["state"] == "closed"
        assert pr["merged_at"] == "2025-06-02T10:00:00.000000+00:00"
        assert pr["updated_at"] == "2025-06-02T10:00:00.000000+00:00"
        assert pr["html_url"] == "https://bitbucket.org/myworkspace/my-repo/pull-requests/5"
        assert pr["head"] == {"ref": ""}  # fixture has no source branch

    @respx.mock
    def test_head_ref_from_source_branch(self, account, sample_bitbucket_api_pr):
        """activity.py skips branches whose PR is closed, keyed on head.ref."""
        raw = dict(sample_bitbucket_api_pr, source={"branch": {"name": "feature/x"}})
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests"
        ).mock(return_value=httpx.Response(200, json={"values": [raw]}))
        with BitbucketClient(account) as client:
            result = client.list_pulls("myworkspace", "my-repo")
        assert result[0]["head"]["ref"] == "feature/x"

    @respx.mock
    def test_default_pagelen_is_50_not_100(self, account, sample_bitbucket_api_pr):
        """Bitbucket's /pullrequests endpoint caps pagelen at 50 and returns
        400 "Invalid pagelen" above that (confirmed against the live API) —
        list_pulls must not inherit the GitHub-appropriate Protocol default
        of 100, or every call silently fails and no PRs are ever fetched."""
        route = respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests"
        ).mock(return_value=httpx.Response(200, json={"values": [sample_bitbucket_api_pr]}))
        with BitbucketClient(account) as client:
            client.list_pulls("myworkspace", "my-repo")
        assert route.calls.last.request.url.params["pagelen"] == "50"

    @respx.mock
    def test_since_sorts_by_updated_desc_and_stops_early(self, account, sample_bitbucket_api_pr):
        """With `since`, results are sorted -updated_on and pagination stops on
        the first page holding a PR last updated before `since`."""
        recent = dict(sample_bitbucket_api_pr, id=9, updated_on="2025-06-05T10:00:00.000000+00:00")
        old = dict(sample_bitbucket_api_pr, id=8, updated_on="2025-05-01T10:00:00.000000+00:00")
        route = respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests"
        ).mock(return_value=httpx.Response(200, json={
            "values": [recent, old],
            "next": "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests?page=2",
        }))
        page2 = respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests?page=2"
        ).mock(return_value=httpx.Response(200, json={"values": [old]}))
        with BitbucketClient(account) as client:
            result = client.list_pulls("myworkspace", "my-repo", since="2025-06-01")
        assert [p["number"] for p in result] == [9, 8]
        assert route.calls.last.request.url.params["sort"] == "-updated_on"
        assert not page2.called


class TestListPullReviews:
    @respx.mock
    def test_maps_approvals_and_change_requests_from_activity(self, account):
        """Approvals and change requests come from the PR /activity feed;
        comment and update entries are ignored (comments are counted by
        list_review_comments)."""
        route = respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests/5/activity"
        ).mock(return_value=httpx.Response(200, json={"values": [
            {"update": {"date": "2025-06-01T09:00:00+00:00", "state": "OPEN"}},
            {"approval": {
                "date": "2025-06-02T10:00:00.000000+00:00",
                "user": {"nickname": "bob", "display_name": "Bob"},
            }},
            {"changes_requested": {
                "date": "2025-06-01T12:00:00.000000+00:00",
                "user": {"display_name": "Carol"},
            }},
            {"comment": {"created_on": "2025-06-01T11:00:00+00:00", "user": {"nickname": "dave"}}},
        ]}))
        with BitbucketClient(account) as client:
            result = client.list_pull_reviews("myworkspace", "my-repo", 5)

        assert route.calls.last.request.url.params["pagelen"] == "50"
        assert result == [
            {
                "user": {"login": "bob"},
                "state": "APPROVED",
                "submitted_at": "2025-06-02T10:00:00.000000+00:00",
                "body": "",
            },
            {
                "user": {"login": "Carol"},
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2025-06-01T12:00:00.000000+00:00",
                "body": "",
            },
        ]


class TestListRepoCommits:
    @respx.mock
    def test_parses_author_raw(self, account, sample_bitbucket_api_commit):
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/commits"
        ).mock(return_value=httpx.Response(200, json={"values": [sample_bitbucket_api_commit]}))
        with BitbucketClient(account) as client:
            result = client.list_repo_commits("myworkspace", "my-repo")
        assert len(result) == 1
        commit = result[0]
        assert commit["sha"] == "abc123def4567890abcdef1234567890abcdef12"
        assert commit["author"]["login"] == "alice"
        assert commit["commit"]["author"]["email"] == "alice@example.com"
        assert commit["commit"]["author"]["name"] == "Alice"


class TestListReviewComments:
    @respx.mock
    def test_aggregates_comments_across_prs(self, account, sample_bitbucket_api_pr):
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests"
        ).mock(return_value=httpx.Response(200, json={"values": [sample_bitbucket_api_pr]}))
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests/5/comments"
        ).mock(return_value=httpx.Response(200, json={"values": [
            {
                "user": {"nickname": "bob", "display_name": "Bob"},
                "created_on": "2025-06-02T12:00:00.000000+00:00",
            },
        ]}))
        with BitbucketClient(account) as client:
            result = client.list_review_comments("myworkspace", "my-repo")
        assert len(result) == 1
        assert result[0]["user"]["login"] == "bob"

    @respx.mock
    def test_skips_per_pr_fetch_for_prs_stale_before_since(self, account, sample_bitbucket_api_pr):
        """A PR whose updated_on predates `since` can't have comments after
        `since` (Bitbucket bumps updated_on on new comments) — its per-PR
        comments endpoint must not be requested at all. Without this, a repo
        with hundreds of historical PRs fires one request per PR every call,
        which can exhaust Bitbucket's rate limit during a multi-repo scan."""
        stale_pr = dict(sample_bitbucket_api_pr)
        stale_pr["id"] = 6
        stale_pr["updated_on"] = "2025-01-01T10:00:00.000000+00:00"
        fresh_pr = dict(sample_bitbucket_api_pr)
        fresh_pr["id"] = 7
        fresh_pr["updated_on"] = "2025-06-05T10:00:00.000000+00:00"

        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests"
        ).mock(return_value=httpx.Response(200, json={"values": [stale_pr, fresh_pr]}))
        # Only the fresh PR's comments endpoint is mocked. If the stale PR's
        # endpoint is requested too, respx raises for the unmatched route.
        fresh_route = respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/pullrequests/7/comments"
        ).mock(return_value=httpx.Response(200, json={"values": []}))

        with BitbucketClient(account) as client:
            client.list_review_comments("myworkspace", "my-repo", since="2025-06-01")

        assert fresh_route.called


class TestUpdateRepo:
    @respx.mock
    def test_translates_supported_kwargs(self, account, sample_bitbucket_api_repo):
        route = respx.put(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo"
        ).mock(return_value=httpx.Response(200, json=sample_bitbucket_api_repo))
        with BitbucketClient(account) as client:
            result = client.update_repo(
                "myworkspace",
                "my-repo",
                description="New desc",
                has_issues=True,
                has_wiki=False,
                default_branch="develop",
                visibility="private",
            )
        sent_body = route.calls.last.request.content
        import json as _json
        body = _json.loads(sent_body)
        assert body == {
            "description": "New desc",
            "has_issues": True,
            "has_wiki": False,
            "mainbranch": {"name": "develop"},
            "is_private": True,
        }
        assert result["name"] == "my-repo"

    @respx.mock
    def test_drops_unsupported_kwargs_without_raising(self, account, sample_bitbucket_api_repo):
        route = respx.put(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo"
        ).mock(return_value=httpx.Response(200, json=sample_bitbucket_api_repo))
        with BitbucketClient(account) as client:
            client.update_repo(
                "myworkspace",
                "my-repo",
                homepage="https://example.com",
                has_projects=True,
            )
        import json as _json
        body = _json.loads(route.calls.last.request.content)
        assert body == {}

    @respx.mock
    def test_visibility_public_maps_to_is_private_false(self, account, sample_bitbucket_api_repo):
        route = respx.put(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo"
        ).mock(return_value=httpx.Response(200, json=sample_bitbucket_api_repo))
        with BitbucketClient(account) as client:
            client.update_repo("myworkspace", "my-repo", visibility="public")
        import json as _json
        body = _json.loads(route.calls.last.request.content)
        assert body == {"is_private": False}


class TestGetFile:
    @respx.mock
    def test_returns_none_on_404(self, account, sample_bitbucket_api_repo):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(200, json=sample_bitbucket_api_repo)
        )
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/src/main/LICENSE"
        ).mock(return_value=httpx.Response(404, json={"error": {"message": "Not Found"}}))
        with BitbucketClient(account) as client:
            result = client.get_file("myworkspace", "my-repo", "LICENSE")
        assert result is None

    @respx.mock
    def test_returns_sha_from_commit_hash(self, account, sample_bitbucket_api_repo):
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo").mock(
            return_value=httpx.Response(200, json=sample_bitbucket_api_repo)
        )
        respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/src/main/README.md"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "type": "commit_file",
                    "path": "README.md",
                    "commit": {"hash": "deadbeef123"},
                },
            )
        )
        with BitbucketClient(account) as client:
            result = client.get_file("myworkspace", "my-repo", "README.md")
        assert result == {"sha": "deadbeef123", "path": "README.md", "type": "commit_file"}


class TestCreateOrUpdateFile:
    @respx.mock
    def test_posts_multipart_with_resolved_branch(self, account, sample_bitbucket_api_repo):
        repo_route = respx.get(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo"
        ).mock(return_value=httpx.Response(200, json=sample_bitbucket_api_repo))
        route = respx.post(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/src"
        ).mock(return_value=httpx.Response(201))
        with BitbucketClient(account) as client:
            result = client.create_or_update_file(
                "myworkspace", "my-repo", "README.md", "hello", "Add README",
            )
        # Missing branch must trigger a get_repo()-based fallback to resolve
        # the default branch before posting.
        assert repo_route.called
        sent = route.calls.last.request
        # Body is multipart/form-data, not a JSON payload.
        assert sent.headers["content-type"].startswith("multipart/form-data")
        assert b'name="branch"' in sent.content
        assert b"main" in sent.content
        assert b'name="README.md"' in sent.content
        assert b"hello" in sent.content
        assert result == {"path": "README.md", "branch": "main"}

    @respx.mock
    def test_explicit_branch_skips_get_repo(self, account):
        route = respx.post(
            "https://api.bitbucket.org/2.0/repositories/myworkspace/my-repo/src"
        ).mock(return_value=httpx.Response(201))
        with BitbucketClient(account) as client:
            result = client.create_or_update_file(
                "myworkspace", "my-repo", "LICENSE", "text", "Add license", branch="develop",
            )
        assert result == {"path": "LICENSE", "branch": "develop"}
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["content-type"].startswith("multipart/form-data")


class TestListCollaborators:
    @respx.mock
    def test_maps_permissions_and_skips_none(self, account):
        payload = {
            "values": [
                {
                    "permission": "admin",
                    "user": {"nickname": "alice", "account_id": "acc-1"},
                    "repository": {"name": "my-repo"},
                },
                {
                    "permission": "write",
                    "user": {"nickname": "bob", "account_id": "acc-2"},
                    "repository": {"name": "my-repo"},
                },
                {
                    "permission": "read",
                    "user": {"display_name": "Carol", "account_id": "acc-3"},
                    "repository": {"name": "my-repo"},
                },
                {
                    "permission": "none",
                    "user": {"nickname": "dave", "account_id": "acc-4"},
                    "repository": {"name": "my-repo"},
                },
            ]
        }
        respx.get(
            "https://api.bitbucket.org/2.0/workspaces/myworkspace/permissions/repositories"
        ).mock(return_value=httpx.Response(200, json=payload))
        with BitbucketClient(account) as client:
            result = client.list_collaborators("myworkspace", "my-repo")

        by_login = {c["login"]: c for c in result}
        assert set(by_login) == {"alice", "bob", "Carol"}

        assert by_login["alice"]["permissions"] == {
            "admin": True, "maintain": False, "push": True, "triage": False, "pull": True,
        }
        assert by_login["bob"]["permissions"] == {
            "admin": False, "maintain": False, "push": True, "triage": False, "pull": True,
        }
        assert by_login["Carol"]["permissions"] == {
            "admin": False, "maintain": False, "push": False, "triage": False, "pull": True,
        }
        assert by_login["alice"]["id"] == "acc-1"


class TestListOrgMembers:
    @respx.mock
    def test_translates_workspace_members(self, account):
        payload = {
            "values": [
                {"user": {"nickname": "alice", "account_id": "acc-1"}},
                {"user": {"display_name": "Bob Bobson", "account_id": "acc-2"}},
            ]
        }
        respx.get("https://api.bitbucket.org/2.0/workspaces/myworkspace/members").mock(
            return_value=httpx.Response(200, json=payload)
        )
        with BitbucketClient(account) as client:
            result = client.list_org_members("myworkspace")
        assert result == [
            {"login": "alice", "id": "acc-1"},
            {"login": "Bob Bobson", "id": "acc-2"},
        ]


class TestPullTranslationForActivity:
    @respx.mock
    def test_merged_pr_has_closed_at_and_merged_by(self, account):
        respx.get("https://api.bitbucket.org/2.0/repositories/ws/r/pullrequests").mock(
            return_value=httpx.Response(200, json={"values": [
                {"id": 1, "state": "MERGED", "author": {"nickname": "amy"},
                 "closed_by": {"nickname": "tom"},
                 "created_on": "2026-10-12T10:00:00+00:00",
                 "updated_on": "2026-10-13T10:00:00+00:00"},
                {"id": 2, "state": "DECLINED", "author": {"nickname": "amy"},
                 "closed_by": {"nickname": "tom"},
                 "created_on": "2026-10-12T10:00:00+00:00",
                 "updated_on": "2026-10-14T10:00:00+00:00"},
                {"id": 3, "state": "OPEN", "author": {"nickname": "amy"},
                 "created_on": "2026-10-12T10:00:00+00:00",
                 "updated_on": "2026-10-15T10:00:00+00:00"},
            ]})
        )
        with BitbucketClient(account) as client:
            merged, declined, open_ = client.list_pulls("ws", "r")
        assert merged["merged_by"] == {"login": "tom"}
        assert merged["closed_at"] == merged["merged_at"] == "2026-10-13T10:00:00+00:00"
        assert declined["merged_at"] is None and declined["merged_by"] is None
        assert declined["closed_at"] == "2026-10-14T10:00:00+00:00"
        assert open_["closed_at"] is None and open_["state"] == "open"
        # merged_by always present, so activity never asks for it per PR
        assert all("merged_by" in pr for pr in (merged, declined, open_))

    @respx.mock
    def test_get_pull(self, account):
        respx.get("https://api.bitbucket.org/2.0/repositories/ws/r/pullrequests/4").mock(
            return_value=httpx.Response(200, json={
                "id": 4, "state": "MERGED", "author": {"nickname": "amy"},
                "closed_by": {"display_name": "Jane Doe"},
                "updated_on": "2026-10-13T10:00:00+00:00",
            })
        )
        with BitbucketClient(account) as client:
            assert client.get_pull("ws", "r", 4)["merged_by"] == {"login": "Jane Doe"}

    def test_issue_comments_empty_to_avoid_double_counting(self, account):
        with BitbucketClient(account) as client:
            assert client.list_issue_comments("ws", "r", since="2026-10-09") == []
