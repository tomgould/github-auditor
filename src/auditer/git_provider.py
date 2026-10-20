from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from auditer.models import AccountConfig


@runtime_checkable
class GitProvider(Protocol):
    """Structural interface for git hosting API clients (GitHub, Bitbucket, ...).

    This Protocol is derived from the *actual* call sites in
    ``commands/data/checkout.py``, ``commands/data/audit.py``,
    ``commands/data/activity.py``, ``commands/repo/settings.py``,
    ``commands/content/readme.py``, ``commands/repo/add_license.py``,
    ``commands/people/access.py``, ``commands/people/collaborators.py``
    (``list`` subcommand), and ``commands/people/members.py`` (``list``
    subcommand) — not from the full method surface of ``GitHubClient``.
    ``GitHubClient`` already satisfies this Protocol structurally (Python
    Protocols are duck-typed; no inheritance needed).

    Note: ``get_authenticated_user()`` is intentionally NOT part of this
    Protocol — none of the covered command call sites call it directly
    (it's only used internally by ``GitHubClient.validate_token()``, and by
    ``add_license.py`` only as a fallback for ``--owner-name``, which is
    outside this Protocol's derived surface).
    """

    def __enter__(self) -> "GitProvider": ...

    def __exit__(self, *args: Any) -> None: ...

    def close(self) -> None: ...

    def validate_token(self) -> str | None: ...

    def list_repos(
        self, apply_visibility_filter: bool = True,
    ) -> list[dict[str, Any]]: ...

    def get_repo(self, owner: str, repo: str) -> dict[str, Any] | None: ...

    def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        per_page: int = 100,
        since: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any] | None: ...

    def list_pull_reviews(
        self, owner: str, repo: str, number: int,
    ) -> list[dict[str, Any]]: ...

    def list_issue_comments(
        self, owner: str, repo: str, since: str | None = None, per_page: int = 100,
    ) -> list[dict[str, Any]]: ...

    def list_repo_issues(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        since: str | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]: ...

    def list_review_comments(
        self, owner: str, repo: str, since: str | None = None, per_page: int = 100,
    ) -> list[dict[str, Any]]: ...

    def list_repo_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        until: str | None = None,
        max_pages: int = 3,
    ) -> list[dict[str, Any]]: ...

    def update_repo(self, owner: str, repo: str, **settings: Any) -> dict[str, Any]: ...

    def get_file(
        self, owner: str, repo: str, path: str,
    ) -> dict[str, Any] | None: ...

    def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
    ) -> dict[str, Any]: ...

    def list_collaborators(self, owner: str, repo: str) -> list[dict[str, Any]]: ...

    def list_org_members(self, org: str) -> list[dict[str, Any]]: ...


def get_provider(account: AccountConfig) -> GitProvider:
    """Factory: return the correct API client for an account's provider.

    ``BitbucketClient`` is imported locally (inside the function body)
    rather than at module top-level because it may not exist on disk yet
    when this module is first imported (created concurrently by a sibling
    agent).
    """
    if account.provider == "github":
        from auditer.github_client import GitHubClient

        return GitHubClient(account)

    if account.provider == "bitbucket":
        from auditer.bitbucket_client import BitbucketClient

        return BitbucketClient(account)

    raise ValueError(
        f"Unknown provider '{account.provider}' for account '{account.slug}'. "
        "Expected 'github' or 'bitbucket'."
    )
