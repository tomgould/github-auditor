from __future__ import annotations

import re

import pytest

from auditer.models import AccountConfig, AuditResult, ProfileData, ProjectConfig, RepoInfo

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from Rich output for test assertions."""
    return _ANSI_RE.sub("", text)


@pytest.fixture
def sample_account() -> AccountConfig:
    return AccountConfig(
        slug="test-user",
        type="user",
        username="testuser",
        token="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
        include_private=True,
        include_forks=True,
    )


@pytest.fixture
def sample_account_no_token() -> AccountConfig:
    return AccountConfig(
        slug="public-user",
        type="user",
        username="publicuser",
        token=None,
        include_private=False,
        include_forks=True,
    )


@pytest.fixture
def sample_account_bitbucket() -> AccountConfig:
    return AccountConfig(
        slug="test-bb",
        type="user",
        username="testuser",
        token="bb_test_api_token_1234567890",
        include_private=True,
        include_forks=True,
        provider="bitbucket",
        workspace="myworkspace",
        auth_email="tom@example.com",
    )


@pytest.fixture
def sample_bitbucket_api_repo() -> dict:
    """Raw Bitbucket Cloud repository object (api-group-repositories)."""
    return {
        "name": "my-repo",
        "is_private": False,
        "description": "A test repository",
        "language": "python",
        "size": 1048576,
        "mainbranch": {"name": "main"},
        "links": {
            "html": {"href": "https://bitbucket.org/myworkspace/my-repo"},
            "clone": [
                {
                    "name": "https",
                    "href": "https://myworkspace@bitbucket.org/myworkspace/my-repo.git",
                },
                {
                    "name": "ssh",
                    "href": "git@bitbucket.org:myworkspace/my-repo.git",
                },
            ],
        },
        "owner": {"nickname": "myworkspace", "display_name": "My Workspace"},
        "updated_on": "2025-01-15T10:00:00.000000+00:00",
        # Bitbucket always includes this key — null for non-forks, populated
        # for forks. Omitting it here (as earlier versions of this fixture
        # did) masks bugs that check key presence instead of the value.
        "parent": None,
    }


@pytest.fixture
def sample_bitbucket_api_pr() -> dict:
    """Raw Bitbucket Cloud pull request object (api-group-pullrequests)."""
    return {
        "id": 5,
        "title": "Add feature X",
        "author": {"nickname": "alice", "display_name": "Alice"},
        "state": "MERGED",
        "created_on": "2025-06-01T10:00:00.000000+00:00",
        "updated_on": "2025-06-02T10:00:00.000000+00:00",
        "links": {
            "html": {
                "href": "https://bitbucket.org/myworkspace/my-repo/pull-requests/5"
            }
        },
    }


@pytest.fixture
def sample_bitbucket_api_commit() -> dict:
    """Raw Bitbucket Cloud commit object (api-group-commits)."""
    return {
        "hash": "abc123def4567890abcdef1234567890abcdef12",
        "date": "2025-06-01T10:00:00.000000+00:00",
        "author": {
            "raw": "Alice <alice@example.com>",
            "user": {"nickname": "alice", "display_name": "Alice"},
        },
    }


@pytest.fixture
def sample_api_repo() -> dict:
    return {
        "name": "my-repo",
        "owner": {"login": "testuser"},
        "html_url": "https://github.com/testuser/my-repo",
        "private": False,
        "description": "A test repository",
        "language": "Python",
        "stargazers_count": 42,
        "forks_count": 5,
        "open_issues_count": 3,
        "license": {"spdx_id": "MIT"},
        "pushed_at": "2025-01-15T10:00:00Z",
        "fork": False,
        "archived": False,
        "topics": ["python", "testing"],
        "default_branch": "main",
        "size": 1024,
        "clone_url": "https://github.com/testuser/my-repo.git",
    }


@pytest.fixture
def sample_api_repo_private() -> dict:
    return {
        "name": "secret-project",
        "owner": {"login": "testuser"},
        "html_url": "https://github.com/testuser/secret-project",
        "private": True,
        "description": "",
        "language": "Go",
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "license": None,
        "pushed_at": "2023-06-01T10:00:00Z",
        "fork": False,
        "archived": False,
        "topics": [],
        "default_branch": "main",
        "size": 256,
        "clone_url": "https://github.com/testuser/secret-project.git",
    }


@pytest.fixture
def sample_api_repo_fork() -> dict:
    return {
        "name": "forked-lib",
        "owner": {"login": "testuser"},
        "html_url": "https://github.com/testuser/forked-lib",
        "private": False,
        "description": "A fork of some library",
        "language": "JavaScript",
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "license": {"spdx_id": "Apache-2.0"},
        "pushed_at": "2024-06-01T10:00:00Z",
        "fork": True,
        "archived": False,
        "topics": [],
        "default_branch": "main",
        "size": 512,
        "clone_url": "https://github.com/testuser/forked-lib.git",
    }


@pytest.fixture
def sample_repo_info(sample_api_repo) -> RepoInfo:
    return RepoInfo.from_api_response(sample_api_repo, has_readme=True)


@pytest.fixture
def sample_config_dict() -> dict:
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": "data",
            "reports_dir": "reports",
            "log_level": "INFO",
        },
        "accounts": [
            {
                "slug": "test-user",
                "type": "user",
                "username": "testuser",
                "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                "include_private": True,
                "include_forks": True,
            }
        ],
    }


@pytest.fixture
def sample_project() -> ProjectConfig:
    return ProjectConfig(
        name="test-project",
        description="A test project",
        repos=["test-user/my-repo", "test-user/secret-project"],
    )


@pytest.fixture
def sample_config_dict_with_projects() -> dict:
    return {
        "defaults": {
            "stale_months": 12,
            "data_dir": "data",
            "reports_dir": "reports",
            "log_level": "INFO",
        },
        "accounts": [
            {
                "slug": "test-user",
                "type": "user",
                "username": "testuser",
                "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
                "include_private": True,
                "include_forks": True,
            }
        ],
        "projects": {
            "test-project": {
                "description": "A test project",
                "repos": ["test-user/my-repo"],
            },
        },
    }


@pytest.fixture
def sample_audit_result(sample_api_repo, sample_api_repo_private, sample_api_repo_fork):
    repos = [
        RepoInfo.from_api_response(sample_api_repo, has_readme=True),
        RepoInfo.from_api_response(sample_api_repo_private, has_readme=False),
        RepoInfo.from_api_response(sample_api_repo_fork, has_readme=True),
    ]
    return AuditResult(
        account_slug="test-user",
        account_username="testuser",
        repos=repos,
        stale_months=12,
    )


@pytest.fixture
def sample_user_profile() -> dict:
    return {
        "login": "testuser",
        "id": 12345,
        "name": "Test User",
        "bio": "I build things.",
        "location": "London, UK",
        "company": "Acme Corp",
        "blog": "https://testuser.dev",
        "twitter_username": "testuser",
        "avatar_url": "https://avatars.githubusercontent.com/u/12345",
        "html_url": "https://github.com/testuser",
        "public_repos": 25,
        "public_gists": 3,
        "followers": 100,
        "following": 50,
    }


@pytest.fixture
def sample_events() -> list[dict]:
    return [
        {
            "type": "PushEvent",
            "repo": {"name": "testuser/my-repo"},
            "payload": {},
        },
        {
            "type": "PullRequestEvent",
            "repo": {"name": "testuser/other-repo"},
            "payload": {
                "action": "opened",
                "pull_request": {"number": 42},
            },
        },
        {
            "type": "CreateEvent",
            "repo": {"name": "testuser/new-repo"},
            "payload": {"ref_type": "repository"},
        },
        {
            "type": "IssuesEvent",
            "repo": {"name": "testuser/my-repo"},
            "payload": {
                "action": "opened",
                "issue": {"number": 7},
            },
        },
        {
            "type": "WatchEvent",
            "repo": {"name": "someone/cool-project"},
            "payload": {},
        },
        {
            "type": "ForkEvent",
            "repo": {"name": "someone/lib"},
            "payload": {},
        },
    ]


@pytest.fixture
def sample_profile_data(
    sample_api_repo, sample_api_repo_fork, sample_events,
) -> ProfileData:
    repos = [
        RepoInfo.from_api_response(sample_api_repo, has_readme=True),
        RepoInfo.from_api_response(sample_api_repo_fork, has_readme=True),
    ]
    return ProfileData(
        account_slug="test-user",
        username="testuser",
        name="Test User",
        bio="I build things.",
        location="London, UK",
        company="Acme Corp",
        blog="https://testuser.dev",
        twitter_username="testuser",
        avatar_url="https://avatars.githubusercontent.com/u/12345",
        html_url="https://github.com/testuser",
        public_repos_count=25,
        public_gists=3,
        followers=100,
        following=50,
        repos=repos,
        events=sample_events,
    )
