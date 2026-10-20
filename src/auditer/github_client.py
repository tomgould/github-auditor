from __future__ import annotations

import base64
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from auditer.filtering import filter_repos_by_visibility
from auditer.models import AccountConfig
from auditer.utils.security import mask_token

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(GitHubAPIError):
    pass


class RateLimitError(GitHubAPIError):
    pass


class GitHubClient:
    BASE_URL = "https://api.github.com"
    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.0  # seconds
    MAX_PAGES_ABSOLUTE = 1000  # Safety limit for pagination loops
    # On a primary rate limit (403 + x-ratelimit-remaining: 0) wait for the
    # reset instead of failing — a full activity scan is worth more than an
    # hour's pause, and the alternative is a report silently full of zeros.
    RATE_LIMIT_MAX_WAIT = 3700  # seconds; GitHub's window is one hour
    RATE_LIMIT_RESET_MARGIN = 5  # seconds past x-ratelimit-reset before retrying
    # Secondary rate limits (429, or 403 with retry-after) are short: GitHub
    # says honour retry-after, else wait at least a minute and back off
    # exponentially. Each wait uses one of MAX_RETRIES attempts.
    SECONDARY_RATE_LIMIT_BACKOFF = 60.0  # seconds, doubled per attempt
    SECONDARY_RATE_LIMIT_MAX_WAIT = 300.0  # seconds

    # Shared across every client and thread: concurrent workers hitting the
    # same exhausted bucket log the wait once, not once per worker.
    _rate_limit_lock = threading.Lock()
    _rate_limit_notified_reset: int = 0

    def __init__(self, account: AccountConfig):
        self.account = account
        self._authenticated_login: str | None = None
        # x-ratelimit-* headers from the most recent response: {limit,
        # remaining, used, reset}. More trustworthy than GET /rate_limit,
        # which was observed live reporting 0 used for a token whose
        # response headers showed 1,800+ used in the same window.
        self.last_rate_limit: dict[str, int] | None = None
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if account.token:
            headers["Authorization"] = f"Bearer {account.token}"

        # follow_redirects: a renamed or transferred repo answers 301 with a
        # Location on api.github.com; without following it the call failed
        # and the repo was reported as an API error. httpx drops the
        # Authorization header if a redirect leaves the host.
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _record_rate_limit(self, response: httpx.Response) -> None:
        if "x-ratelimit-remaining" not in response.headers:
            return
        try:
            self.last_rate_limit = {
                "limit": int(response.headers.get("x-ratelimit-limit", 0)),
                "remaining": int(response.headers["x-ratelimit-remaining"]),
                "used": int(response.headers.get("x-ratelimit-used", 0)),
                "reset": int(response.headers.get("x-ratelimit-reset", 0)),
            }
        except ValueError:
            pass

    def _wait_for_rate_limit_reset(self, reset_at: int) -> float:
        """Sleep until the primary rate limit resets. Returns seconds slept.

        Logged once per reset window across all clients/threads; every
        caller still sleeps, since each has its own request to retry.
        """
        wait = max(0.0, reset_at - time.time()) + self.RATE_LIMIT_RESET_MARGIN
        wait = min(wait, self.RATE_LIMIT_MAX_WAIT)
        reset_str = datetime.fromtimestamp(reset_at, tz=timezone.utc).strftime(
            "%H:%M:%S UTC"
        )
        with GitHubClient._rate_limit_lock:
            if GitHubClient._rate_limit_notified_reset != reset_at:
                GitHubClient._rate_limit_notified_reset = reset_at
                logger.warning(
                    f"GitHub rate limit exhausted for account '{self.account.slug}'. "
                    f"Waiting {int(wait)}s for the reset at {reset_str}, then continuing."
                )
        time.sleep(wait)
        return wait

    def _wait_secondary_rate_limit(self, response: httpx.Response, attempt: int) -> float:
        """Sleep before retrying a secondary-rate-limited request. Returns seconds slept."""
        try:
            wait = float(response.headers.get("retry-after", ""))
        except ValueError:
            wait = self.SECONDARY_RATE_LIMIT_BACKOFF * (2 ** (attempt - 1))
        wait = min(max(wait, 1.0), self.SECONDARY_RATE_LIMIT_MAX_WAIT)
        logger.warning(
            f"GitHub secondary rate limit ({response.status_code}) for account "
            f"'{self.account.slug}'. Waiting {int(wait)}s before retrying."
        )
        time.sleep(wait)
        return wait

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error = None
        rate_limit_waited = False
        attempt = 0
        while attempt < self.MAX_RETRIES:
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as e:
                attempt += 1
                last_error = GitHubAPIError(f"Network error: {e}")
                time.sleep(self.RETRY_BACKOFF * attempt)
                continue

            self._record_rate_limit(response)

            if response.status_code == 401:
                token_hint = mask_token(self.account.token)
                raise AuthenticationError(
                    f"Authentication failed for account '{self.account.slug}' "
                    f"(token: {token_hint}). Check your PAT has the required scopes.",
                    status_code=401,
                )

            # Primary limit (remaining == 0, on 403 or 429) waits for the
            # reset below; anything else on 429, or a 403 carrying
            # retry-after, is a secondary (burst/concurrency) limit.
            secondary_limited = (
                response.headers.get("x-ratelimit-remaining") != "0"
                and (
                    response.status_code == 429
                    or (response.status_code == 403 and "retry-after" in response.headers)
                )
            )
            if secondary_limited:
                attempt += 1
                last_error = RateLimitError(
                    f"Secondary rate limit ({response.status_code}) for {url} "
                    f"on account '{self.account.slug}'",
                    status_code=response.status_code,
                )
                if attempt < self.MAX_RETRIES:
                    self._wait_secondary_rate_limit(response, attempt)
                continue

            if response.status_code in (403, 429):
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining == "0":
                    reset_at = int(response.headers.get("x-ratelimit-reset", 0))
                    if not rate_limit_waited:
                        # Wait out the window and retry once. Doesn't consume
                        # a retry attempt — it's not a transient failure.
                        self._wait_for_rate_limit_reset(reset_at)
                        rate_limit_waited = True
                        continue
                    wait = max(0, reset_at - int(time.time())) + 1
                    raise RateLimitError(
                        f"Rate limit exceeded for account '{self.account.slug}' "
                        f"(still exhausted after waiting for the reset). "
                        f"Resets in {wait} seconds.",
                        status_code=response.status_code,
                    )
                raise GitHubAPIError(
                    f"Forbidden ({response.status_code}) for {url}",
                    status_code=response.status_code,
                )

            if response.status_code >= 500:
                attempt += 1
                last_error = GitHubAPIError(
                    f"Server error ({response.status_code}) for {url}",
                    status_code=response.status_code,
                )
                time.sleep(self.RETRY_BACKOFF * attempt)
                continue

            return response

        raise last_error or GitHubAPIError("Request failed after retries")

    def get_paginated(
        self,
        url: str,
        params: dict | None = None,
        max_pages: int | None = None,
        stop_when: Callable[[dict], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch every page of a list endpoint.

        ``stop_when`` is a per-item predicate: once any item on a page
        matches, that page is still appended but no further pages are
        fetched. Meant for ``sort=updated, direction=desc`` listings where
        everything after the first too-old item is older still; callers
        filter the final page's stragglers themselves.
        """
        all_items: list[dict[str, Any]] = []
        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1

        while page <= self.MAX_PAGES_ABSOLUTE:
            params["page"] = page
            response = self._request("GET", url, params=params)

            if response.status_code == 404:
                raise GitHubAPIError(
                    f"Not found: {url} — check that the account username "
                    f"and type (user/org) are correct in config.yaml.",
                    status_code=404,
                )
            response.raise_for_status()

            items = response.json()
            if not items:
                break

            all_items.extend(items)

            if stop_when is not None and any(stop_when(item) for item in items):
                break

            # Check Link header for next page
            link_header = response.headers.get("link", "")
            if 'rel="next"' not in link_header:
                break

            page += 1
            if max_pages is not None and page > max_pages:
                break

        return all_items

    def _is_own_account(self) -> bool:
        """Check if the token belongs to the configured account username."""
        if not self.account.token:
            return False
        if self._authenticated_login is None:
            # Lazy fallback: validate_token() wasn't called yet
            self._authenticated_login = self.validate_token()
        if self._authenticated_login is None:
            return False
        return self._authenticated_login.lower() == self.account.username.lower()

    def list_repos(
        self, apply_visibility_filter: bool = True,
    ) -> list[dict[str, Any]]:
        if self.account.type == "org":
            url = f"/orgs/{self.account.username}/repos"
            params = {"sort": "pushed", "type": "all"}
        elif self.account.token and self._is_own_account():
            # Own account: /user/repos returns private + public repos
            url = "/user/repos"
            params = {"sort": "pushed", "affiliation": "owner"}
        else:
            # No token, or third-party account: public repos only
            url = f"/users/{self.account.username}/repos"
            params = {"sort": "pushed", "type": "owner"}

        repos = self.get_paginated(url, params)

        if apply_visibility_filter:
            repos = filter_repos_by_visibility(
                repos,
                self.account,
                is_private=lambda r: r.get("private", False),
                is_fork=lambda r: r.get("fork", False),
                get_name=lambda r: r["name"],
            )

        return repos

    def has_readme(self, owner: str, repo: str) -> bool:
        try:
            response = self._request("GET", f"/repos/{owner}/{repo}/readme")
            return response.status_code == 200
        except GitHubAPIError:
            return False

    def get_repo(self, owner: str, repo: str) -> dict[str, Any] | None:
        """Fetch a single repo's metadata. Returns None on 404."""
        response = self._request("GET", f"/repos/{owner}/{repo}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def get_rate_limit(self) -> dict[str, Any]:
        response = self._request("GET", "/rate_limit")
        response.raise_for_status()
        return response.json()

    def get_authenticated_user(self) -> dict[str, Any]:
        response = self._request("GET", "/user")
        response.raise_for_status()
        return response.json()

    def validate_token(self) -> str | None:
        if not self.account.token:
            return None
        user = self.get_authenticated_user()
        login = user.get("login")
        self._authenticated_login = login
        return login

    # --- Org members ---

    def list_org_members(self, org: str) -> list[dict[str, Any]]:
        """List members of an organisation (paginated)."""
        return self.get_paginated(f"/orgs/{org}/members")

    def get_user(self, username: str) -> dict[str, Any] | None:
        """Fetch a user's public profile. Returns None on 404."""
        response = self._request("GET", f"/users/{username}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def remove_org_member(self, org: str, username: str) -> bool:
        """Remove a member from an organisation. Returns True on success, False on 404."""
        response = self._request("DELETE", f"/orgs/{org}/members/{username}")
        if response.status_code == 204:
            return True
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return False

    # --- Collaborators ---

    def list_collaborators(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List collaborators on a repository (paginated)."""
        return self.get_paginated(f"/repos/{owner}/{repo}/collaborators")

    def add_collaborator(
        self, owner: str, repo: str, username: str, permission: str = "push"
    ) -> bool:
        """Add or update a collaborator. Returns True on success (201 or 204)."""
        response = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/collaborators/{username}",
            json={"permission": permission},
        )
        if response.status_code in (201, 204):
            return True
        response.raise_for_status()
        return False

    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        """Remove a collaborator. Returns True on success, False on 404."""
        response = self._request(
            "DELETE", f"/repos/{owner}/{repo}/collaborators/{username}"
        )
        if response.status_code == 204:
            return True
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return False

    # --- Repo settings ---

    def update_repo(self, owner: str, repo: str, **settings: Any) -> dict[str, Any]:
        """Update repository settings (PATCH). Returns updated repo dict."""
        response = self._request(
            "PATCH", f"/repos/{owner}/{repo}", json=settings
        )
        response.raise_for_status()
        return response.json()

    # --- Topics ---

    def get_topics(self, owner: str, repo: str) -> list[str]:
        """Get repository topics."""
        response = self._request("GET", f"/repos/{owner}/{repo}/topics")
        response.raise_for_status()
        return response.json().get("names", [])

    def set_topics(self, owner: str, repo: str, topics: list[str]) -> list[str]:
        """Replace all repository topics. Returns the new topic list."""
        response = self._request(
            "PUT", f"/repos/{owner}/{repo}/topics", json={"names": topics}
        )
        response.raise_for_status()
        return response.json().get("names", [])

    # --- File content ---

    def get_file(
        self, owner: str, repo: str, path: str
    ) -> dict[str, Any] | None:
        """Get file contents from a repository. Returns None on 404."""
        response = self._request("GET", f"/repos/{owner}/{repo}/contents/{path}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    # --- Events ---

    def list_user_events(self, username: str, per_page: int = 30) -> list[dict[str, Any]]:
        """Fetch recent public events for a user."""
        response = self._request(
            "GET", f"/users/{username}/events/public",
            params={"per_page": per_page},
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json()

    # --- Commits ---

    def list_repo_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        until: str | None = None,
        max_pages: int = 3,
    ) -> list[dict[str, Any]]:
        """List commits for a repository (paginated, capped at max_pages).

        Used to build email→login mappings from commit author data.
        """
        params: dict[str, Any] = {"per_page": 100}
        if since:
            params["since"] = f"{since}T00:00:00Z"
        if until:
            params["until"] = f"{until}T23:59:59Z"
        try:
            return self.get_paginated(
                f"/repos/{owner}/{repo}/commits", params=params, max_pages=max_pages,
            )
        except httpx.HTTPStatusError as e:
            # 409 "Git Repository is empty": no commits, not an API failure
            # (it used to put every empty repo in the report's data-quality
            # warning).
            if e.response.status_code == 409:
                return []
            raise

    # --- Pull requests ---

    def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        per_page: int = 100,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """List pull requests for a repository (paginated, newest-updated first).

        With ``since`` (YYYY-MM-DD), pagination stops at the first page
        containing a PR last updated before that date — the listing is
        sorted by ``updated`` descending, so nothing on later pages can
        have been opened, merged, or reviewed in range. That page's
        too-old PRs are still returned; callers apply their own date
        filters. Without it, every PR in the repo's history is fetched.
        """
        stop_when = None
        if since:
            stop_when = lambda pr: (pr.get("updated_at") or "")[:10] < since  # noqa: E731
        return self.get_paginated(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "sort": "updated", "direction": "desc", "per_page": per_page},
            stop_when=stop_when,
        )

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any] | None:
        """Fetch one PR in full. Returns None on 404.

        The list endpoint omits ``merged_by``; this is the only way to know
        who merged a PR.
        """
        response = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def list_pull_reviews(
        self, owner: str, repo: str, number: int,
    ) -> list[dict[str, Any]]:
        """List submitted reviews (approve/comment/request changes) on one PR."""
        return self.get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/reviews")

    # --- Issues ---

    def list_repo_issues(
        self, owner: str, repo: str, state: str = "all", since: str | None = None, per_page: int = 100
    ) -> list[dict[str, Any]]:
        """List issues for a repository (paginated). Filters out pull requests."""
        params: dict[str, Any] = {
            "state": state,
            "sort": "updated",
            "direction": "desc",
            "per_page": per_page,
        }
        if since:
            params["since"] = since
        items = self.get_paginated(f"/repos/{owner}/{repo}/issues", params=params)
        # GitHub Issues API includes PRs — filter them out
        return [item for item in items if "pull_request" not in item]

    # --- Review comments ---

    def list_review_comments(
        self, owner: str, repo: str, since: str | None = None, per_page: int = 100
    ) -> list[dict[str, Any]]:
        """List review comments on pull requests for a repository (paginated)."""
        params: dict[str, Any] = {
            "sort": "updated",
            "direction": "desc",
            "per_page": per_page,
        }
        if since:
            params["since"] = since
        return self.get_paginated(f"/repos/{owner}/{repo}/pulls/comments", params=params)

    def list_issue_comments(
        self, owner: str, repo: str, since: str | None = None, per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List conversation comments on issues and PRs (updated since ``since``).

        GitHub stores a PR's conversation comments as issue comments; those
        on PRs have ``/pull/`` in their ``html_url``. Inline comments on the
        diff are separate (``list_review_comments``).
        """
        params: dict[str, Any] = {"sort": "updated", "direction": "desc", "per_page": per_page}
        if since:
            params["since"] = since
        return self.get_paginated(f"/repos/{owner}/{repo}/issues/comments", params=params)

    # --- File content ---

    def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str | None = None,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a file. Content is base64-encoded automatically.
        Pass sha (from get_file) to update an existing file."""
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if branch is not None:
            body["branch"] = branch
        if sha is not None:
            body["sha"] = sha
        response = self._request(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}", json=body
        )
        response.raise_for_status()
        return response.json()
