from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

import httpx

from auditer.filtering import filter_repos_by_visibility
from auditer.github_client import AuthenticationError, GitHubAPIError, RateLimitError
from auditer.models import AccountConfig
from auditer.utils.security import mask_token

# Matches the email address inside a git-style "Name <email@example.com>"
# author string. Bitbucket's commit "author" field only exposes this raw
# string (plus an optional linked "user" account) — there is no separate
# email field like GitHub's commit.author.email.
_RAW_AUTHOR_EMAIL_RE = re.compile(r"<([^>]+)>")

logger = logging.getLogger(__name__)

# Bitbucket Cloud's repository_permission.permission enum (workspace
# permissions endpoint) is a 3-level scale, unlike GitHub's 5-level
# admin/maintain/push/triage/pull. Mapped per confirmed swagger enum
# ["read", "write", "admin", "none"]: read->pull, write->push, admin->admin.
# "none" (explicit no-access grant) is skipped entirely — GitHub's
# collaborators endpoint never lists no-access entries either.
_BITBUCKET_PERMISSION_NONE = "none"

# GitHub-shaped update_repo() kwargs with a direct Bitbucket repository field
# equivalent (confirmed against the /2.0 swagger "repository" definitions
# object: links, uuid, full_name, is_private, parent, scm, owner, name,
# description, created_on, updated_on, size, language, has_issues, has_wiki,
# fork_policy, project, mainbranch — notably NO "website"/homepage field
# exists on the repository object in the current Cloud API, unlike GitHub).
_UPDATE_REPO_SUPPORTED_KEYS = {"description", "has_issues", "has_wiki", "default_branch", "visibility"}


def _parse_commit_author_raw(raw: str) -> tuple[str, str]:
    """Split a Bitbucket commit's ``author.raw`` string into (name, email)."""
    if not raw:
        return "", ""
    match = _RAW_AUTHOR_EMAIL_RE.search(raw)
    email = match.group(1) if match else ""
    name = raw.split("<", 1)[0].strip()
    return name, email


# Fixed sentinel username Bitbucket Cloud requires for git-over-HTTPS auth
# with an API token. This is NOT the account email — Atlassian's own docs
# explicitly warn against using the email or token name as the username
# here, even though the REST API (BitbucketClient's own HTTP calls above)
# correctly uses (auth_email, token) as Basic auth. Two different
# conventions for the same credential, confirmed against
# support.atlassian.com's git-over-HTTPS troubleshooting docs.
GIT_AUTH_USERNAME = "x-bitbucket-api-token-auth"


class BitbucketClient:
    """Bitbucket Cloud REST API (2.0) client mirroring GitHubClient's public surface.

    Docs consulted (developer.atlassian.com/cloud/bitbucket/rest/ and
    support.atlassian.com/bitbucket-cloud/):
    - api-group-repositories (repository object, list/get repo)
    - api-group-pullrequests (pull request object, states, comments)
    - api-group-issue-tracker (issue object, 404 when tracker disabled)
    - api-group-users (current authenticated user, GDPR username exception)
    - REST API pagination ("next" URL field in the response body)
    - API token Basic auth (Atlassian account email + token)
    """

    BASE_URL = "https://api.bitbucket.org/2.0"
    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.0  # seconds
    MAX_PAGES_ABSOLUTE = 1000  # Safety limit for pagination loops

    def __init__(self, account: AccountConfig):
        self.account = account
        self._authenticated_login: str | None = None
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            # Bitbucket Cloud REST API token auth: Basic auth with the
            # Atlassian account email as username and the API token as
            # password (NOT the Bitbucket username — that's a documented
            # gotcha specific to the REST API, as opposed to git-over-HTTPS).
            auth=(account.auth_email or "", account.token or ""),
            timeout=30.0,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.TransportError as e:
                last_error = GitHubAPIError(f"Network error: {e}")
                time.sleep(self.RETRY_BACKOFF * (attempt + 1))
                continue

            if response.status_code == 401:
                token_hint = mask_token(self.account.token)
                raise AuthenticationError(
                    f"Authentication failed for account '{self.account.slug}' "
                    f"(token: {token_hint}). Check that account.auth_email is your "
                    f"Atlassian account email and account.token is a valid API token.",
                    status_code=401,
                )

            if response.status_code == 403:
                # Unlike a 401 (bad credentials), a 403 here means the token
                # authenticated fine but lacks the scope for this specific
                # request — e.g. calling /user without the read:account /
                # read:user:bitbucket scope selected when the token was
                # created. Surfaced as AuthenticationError (not raised raw)
                # so it's a clean message instead of an unhandled traceback.
                raise AuthenticationError(
                    f"Forbidden (403) for account '{self.account.slug}' at {url}. "
                    f"The API token authenticated but likely lacks the scope required "
                    f"for this request — check the token's scopes in Bitbucket "
                    f"(Personal settings > API tokens).",
                    status_code=403,
                )

            if response.status_code == 429:
                # Bitbucket Cloud rate-limits with 429 (unlike GitHub's 403 +
                # x-ratelimit-remaining pattern). A Retry-After header isn't
                # consistently documented as guaranteed on Bitbucket 429s, so
                # fall back to a backoff-based estimate when it's absent.
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    wait = int(retry_after)
                else:
                    wait = int(self.RETRY_BACKOFF * 5)
                # Bitbucket's burst limiter resets quickly (observed
                # Retry-After values of just a few seconds), unlike GitHub's
                # hour-scale rate limit windows — so, unlike a bare raise,
                # it's worth actually waiting and retrying within the
                # existing MAX_RETRIES budget. Confirmed live: a 5-worker
                # scan across ~400 repos trips this on the large majority of
                # requests: previously this raised immediately on the first
                # 429, silently losing PR/issue/review data for almost every
                # repo in the account (swallowed by callers' generic
                # exception handling) rather than just slowing down.
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(wait)
                    continue
                raise RateLimitError(
                    f"Rate limit exceeded for account '{self.account.slug}'. "
                    f"Retry after {wait} seconds.",
                    status_code=429,
                )

            if response.status_code >= 500:
                last_error = GitHubAPIError(
                    f"Server error ({response.status_code}) for {url}",
                    status_code=response.status_code,
                )
                time.sleep(self.RETRY_BACKOFF * (attempt + 1))
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
        """Fetch all pages of a Bitbucket list endpoint.

        Bitbucket paginates via a "next" URL field in the JSON response body
        (not a Link header like GitHub) — each page's ``next`` value is a
        complete absolute URL that already includes the query string for the
        following page, so it's requested as-is with no extra params.

        ``stop_when`` is a per-item predicate: once any item on a page
        matches, that page is still appended but no further pages are
        fetched (same contract as ``GitHubClient.get_paginated``).
        """
        all_items: list[dict[str, Any]] = []
        page_count = 0
        next_url: str | None = url
        next_params: dict | None = dict(params or {})
        next_params.setdefault("pagelen", 100)

        while next_url and page_count < self.MAX_PAGES_ABSOLUTE:
            response = self._request("GET", next_url, params=next_params)

            if response.status_code == 404:
                raise GitHubAPIError(
                    f"Not found: {url} — check that account.workspace is "
                    f"correct in config.yaml.",
                    status_code=404,
                )
            response.raise_for_status()

            data = response.json()
            items = data.get("values", [])
            if not items:
                break

            all_items.extend(items)
            page_count += 1

            if stop_when is not None and any(stop_when(item) for item in items):
                break

            next_url = data.get("next")
            next_params = None  # "next" already carries its own query string

            if max_pages is not None and page_count >= max_pages:
                break

        return all_items

    # --- Repo translation ---

    def _translate_repo(self, data: dict) -> dict[str, Any]:
        """Translate a Bitbucket repository object into a GitHub-shaped dict.

        Field names match exactly what RepoInfo.from_api_response() reads.
        Genuine Bitbucket gaps are mapped to safe defaults rather than
        fabricated data (documented per-field below).
        """
        owner = data.get("owner") or {}
        owner_login = (
            owner.get("nickname")
            or owner.get("display_name")
            or self.account.workspace
            or ""
        )
        size_bytes = data.get("size") or 0

        clone_links = (data.get("links") or {}).get("clone") or []
        clone_url = next(
            (c.get("href", "") for c in clone_links if c.get("name") == "https"),
            clone_links[0].get("href", "") if clone_links else "",
        )

        return {
            "name": data.get("name", ""),
            "owner": {"login": owner_login},
            "html_url": (data.get("links") or {}).get("html", {}).get("href", ""),
            "clone_url": clone_url,
            "private": data.get("is_private", False),
            "description": data.get("description") or "",
            "language": data.get("language") or "",
            # No stars concept in Bitbucket Cloud.
            "stargazers_count": 0,
            # Bitbucket doesn't expose a fork *count* on the repo object —
            # only whether this repo is itself a fork (via "parent").
            "forks_count": 0,
            # Issue tracker is a separate, often-disabled Bitbucket feature —
            # don't fetch it here just to populate a count.
            "open_issues_count": 0,
            # License is not exposed by the Bitbucket Cloud API.
            "license": None,
            # No topics/labels concept for Bitbucket repos.
            "topics": [],
            # No archive concept in Bitbucket Cloud.
            "archived": False,
            # Bitbucket always includes a "parent" key on the repository
            # object — null for non-forks, populated for forks — so
            # presence alone ("parent" in data) incorrectly marks every
            # repo as a fork. Must check the value, not just the key.
            "fork": data.get("parent") is not None,
            "default_branch": (data.get("mainbranch") or {}).get("name", "main"),
            # Bitbucket's "size" is in bytes; RepoInfo.size_kb (and GitHub's
            # API) expect KB, so convert.
            "size": int(round(size_bytes / 1024)),
            # Bitbucket has no distinct "pushed_at" field — updated_on is the
            # closest equivalent (last write activity on the repo).
            "pushed_at": data.get("updated_on", ""),
            "updated_at": data.get("updated_on", ""),
        }

    # --- Repos ---

    def list_repos(self, apply_visibility_filter: bool = True) -> list[dict[str, Any]]:
        items = self.get_paginated(
            f"/repositories/{self.account.workspace}",
            params={"pagelen": 100, "sort": "-updated_on"},
        )
        repos = [self._translate_repo(r) for r in items]

        if apply_visibility_filter:
            repos = filter_repos_by_visibility(
                repos,
                self.account,
                is_private=lambda r: r.get("private", False),
                is_fork=lambda r: r.get("fork", False),
                get_name=lambda r: r["name"],
            )

        return repos

    def get_repo(self, owner: str, repo: str) -> dict[str, Any] | None:
        """Fetch a single repo's metadata. Returns None on 404."""
        response = self._request("GET", f"/repositories/{owner}/{repo}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return self._translate_repo(response.json())

    # --- Auth / current user ---

    def get_authenticated_user(self) -> dict[str, Any]:
        response = self._request("GET", "/user")
        response.raise_for_status()
        return response.json()

    def validate_token(self) -> str | None:
        if not self.account.token:
            return None
        user = self.get_authenticated_user()
        # /user is the one endpoint that still returns "username" for the
        # *currently authenticated* account (an explicit exception to the
        # 2019 GDPR removal of usernames elsewhere in the API); fall back to
        # nickname if it's ever absent.
        login = user.get("username") or user.get("nickname")
        self._authenticated_login = login
        return login

    # --- Commits ---

    def list_repo_commits(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        until: str | None = None,
        max_pages: int = 3,
    ) -> list[dict[str, Any]]:
        """List commits for a repository, translated to GitHub-shaped dicts.

        Used to build email→login mappings from commit author data (see
        activity.py). Bitbucket's commits endpoint has no since/until query
        parameters (unlike GitHub), so date filtering is applied client-side
        after fetching, comparing each commit's date against since/until
        (plain YYYY-MM-DD strings, matching the caller's convention).
        """
        items = self.get_paginated(
            f"/repositories/{owner}/{repo}/commits", max_pages=max_pages,
        )
        results: list[dict[str, Any]] = []
        for c in items:
            date = c.get("date", "")
            if since and date[:10] < since:
                continue
            if until and date[:10] > until:
                continue

            author = c.get("author") or {}
            name, email = _parse_commit_author_raw(author.get("raw", ""))
            user = author.get("user") or {}
            # Bitbucket exposes no GitHub-style public "login" for accounts
            # other than the currently authenticated user (GDPR, 2019) —
            # "nickname" is the closest available display identifier.
            login = user.get("nickname") or user.get("display_name") or ""

            results.append({
                "sha": c.get("hash", ""),
                "author": {"login": login} if login else None,
                "commit": {"author": {"email": email, "name": name, "date": date}},
            })
        return results

    # --- Pull requests ---

    def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        per_page: int = 50,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """List pull requests for a repository, translated to GitHub-shaped dicts.

        Bitbucket PR states are OPEN/MERGED/DECLINED/SUPERSEDED rather than
        GitHub's open/closed+merged flag — translated below so downstream
        code (activity.py) can keep reading GitHub-shaped state/merged_at.

        ``per_page`` defaults to 50, not the Protocol's 100 (correct for
        GitHub) — Bitbucket's ``/pullrequests`` endpoint specifically caps
        ``pagelen`` at 50 and returns 400 "Invalid pagelen" above that,
        confirmed against the live API. Every other Bitbucket list endpoint
        this client calls accepts up to 100.

        With ``since`` (YYYY-MM-DD), results are sorted by ``-updated_on``
        and pagination stops at the first page containing a PR last updated
        before that date (see ``GitHubClient.list_pulls``). That page's
        too-old PRs are still returned for the caller to filter.
        """
        params: dict[str, Any] = {"pagelen": per_page, "sort": "-updated_on"}
        if state != "all":
            params["state"] = state.upper()
        # else: omit the state filter — Bitbucket returns PRs across all
        # states when unfiltered.

        stop_when = None
        if since:
            stop_when = lambda pr: (pr.get("updated_on") or "")[:10] < since  # noqa: E731

        items = self.get_paginated(
            f"/repositories/{owner}/{repo}/pullrequests", params=params,
            stop_when=stop_when,
        )
        return [self._translate_pr(pr) for pr in items]

    @staticmethod
    def _translate_pr(pr: dict) -> dict[str, Any]:
        """One Bitbucket PR as a GitHub-shaped dict (see ``list_pulls``)."""
        author = pr.get("author") or {}
        login = author.get("nickname") or author.get("display_name") or ""
        bb_state = pr.get("state", "OPEN")

        merged_at = None
        closed_at = None
        merged_by = None
        if bb_state != "OPEN":
            # Bitbucket's pull request object has no explicit "merged_on"
            # or "closed_on" field — updated_on is the closest available
            # approximation for when it was merged or declined.
            closed_at = pr.get("updated_on")
        if bb_state == "MERGED":
            merged_at = closed_at
            closer = pr.get("closed_by") or {}
            closer_login = closer.get("nickname") or closer.get("display_name") or ""
            merged_by = {"login": closer_login} if closer_login else None

        return {
            "number": pr.get("id"),
            "title": pr.get("title", ""),
            "user": {"login": login},
            "state": "open" if bb_state == "OPEN" else "closed",
            "created_at": pr.get("created_on", ""),
            "updated_at": pr.get("updated_on", ""),
            "closed_at": closed_at,
            "merged_at": merged_at,
            # Always present (possibly None) so activity.py never makes a
            # per-PR request to find it, as it must on GitHub.
            "merged_by": merged_by,
            "html_url": (pr.get("links") or {}).get("html", {}).get("href", ""),
            # GitHub's head.ref equivalent — activity.py uses it to skip
            # feature branches whose PR is already closed.
            "head": {
                "ref": ((pr.get("source") or {}).get("branch") or {}).get("name", ""),
            },
        }

    def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any] | None:
        """Fetch one PR, GitHub-shaped. Returns None on 404."""
        response = self._request("GET", f"/repositories/{owner}/{repo}/pullrequests/{number}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return self._translate_pr(response.json())

    def list_pull_reviews(
        self, owner: str, repo: str, number: int,
    ) -> list[dict[str, Any]]:
        """List approvals and change requests on one PR, as GitHub-shaped reviews.

        Read from the PR's ``/activity`` feed, whose ``approval`` and
        ``changes_requested`` entries are each ``{date, user, pullrequest}``
        (confirmed against the live API). Bitbucket has no "comment review"
        — PR comments are already counted by ``list_review_comments()`` —
        so only these two kinds are returned.
        """
        items = self.get_paginated(
            f"/repositories/{owner}/{repo}/pullrequests/{number}/activity",
            params={"pagelen": 50},
        )
        kinds = {"approval": "APPROVED", "changes_requested": "CHANGES_REQUESTED"}
        results: list[dict[str, Any]] = []
        for entry in items:
            for key, state in kinds.items():
                event = entry.get(key)
                if not event:
                    continue
                user = event.get("user") or {}
                login = user.get("nickname") or user.get("display_name") or ""
                results.append({
                    "user": {"login": login},
                    "state": state,
                    "submitted_at": event.get("date", ""),
                    "body": "",
                })
        return results

    # --- Issues ---

    def list_repo_issues(
        self, owner: str, repo: str, state: str = "all", since: str | None = None,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List issues for a repository, translated to GitHub-shaped dicts.

        Returns [] (rather than raising) if the repo's issue tracker is
        disabled — Bitbucket returns 404 in that case, and the tracker is a
        separate, often-disabled feature, so this mirrors GitHubClient's
        forgiving pattern where it makes sense.
        """
        try:
            items = self.get_paginated(
                f"/repositories/{owner}/{repo}/issues", params={"pagelen": per_page},
            )
        except GitHubAPIError as e:
            if e.status_code == 404:
                return []
            raise

        # Bitbucket issue states: new/open/resolved/on hold/invalid/
        # duplicate/wontfix/closed — collapsed to GitHub's open/closed.
        closed_states = {"resolved", "closed", "invalid", "duplicate", "wontfix"}

        results: list[dict[str, Any]] = []
        for issue in items:
            bb_state = (issue.get("state") or "").lower()
            gh_state = "closed" if bb_state in closed_states else "open"
            created = issue.get("created_on", "")
            if since and created[:10] < since:
                continue

            reporter = issue.get("reporter") or {}
            login = reporter.get("nickname") or reporter.get("display_name") or ""

            results.append({
                "number": issue.get("id"),
                "title": issue.get("title", ""),
                "user": {"login": login},
                "state": gh_state,
                "created_at": created,
                # Bitbucket issues have no distinct "closed_at" field —
                # updated_on is used as the closest approximation once closed.
                "closed_at": issue.get("updated_on") if gh_state == "closed" else None,
            })
        return results

    # --- Review comments ---

    def list_review_comments(
        self, owner: str, repo: str, since: str | None = None, per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List pull request comments across the repository.

        Bitbucket has no repo-wide equivalent to GitHub's
        ``/repos/{owner}/{repo}/pulls/comments`` — comments are only
        available per-pull-request. This fans out: list all PRs, then fetch
        each PR's comments and aggregate. Bounded by MAX_PAGES_ABSOLUTE per
        PR (via get_paginated) and by the number of PRs in the repo.

        When ``since`` is given, PRs whose ``updated_on`` predates it are
        skipped before the per-PR fetch — a PR untouched since ``since``
        cannot have comments created after it, because Bitbucket bumps
        ``updated_on`` on new comment activity. Without this, a repo with
        hundreds of historical PRs (normal for any long-lived active repo)
        fires one HTTP request per PR ever created every time this is
        called, which can exhaust Bitbucket's API rate limit during a
        multi-repo scan and cause unrelated calls (e.g. list_pulls on other
        repos processed afterward) to start failing with 429s.
        """
        try:
            prs = self.get_paginated(
                f"/repositories/{owner}/{repo}/pullrequests", params={"pagelen": 50},
            )
        except GitHubAPIError:
            return []

        if since:
            prs = [
                pr for pr in prs
                if (pr.get("updated_on") or pr.get("created_on") or "")[:10] >= since
            ]

        results: list[dict[str, Any]] = []
        for pr in prs:
            pr_id = pr.get("id")
            if pr_id is None:
                continue
            try:
                comments = self.get_paginated(
                    f"/repositories/{owner}/{repo}/pullrequests/{pr_id}/comments",
                    params={"pagelen": per_page},
                )
            except GitHubAPIError:
                continue

            for c in comments:
                created = c.get("created_on", "")
                if since and created[:10] < since:
                    continue
                user = c.get("user") or {}
                login = user.get("nickname") or user.get("display_name") or ""
                results.append({
                    "user": {"login": login},
                    "created_at": created,
                })
        return results

    def list_issue_comments(
        self, owner: str, repo: str, since: str | None = None, per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """Always []: Bitbucket has no separate PR conversation comments.

        Every comment on a Bitbucket PR, general or inline, comes from the
        same ``/comments`` feed that ``list_review_comments()`` already
        counts, so returning them here too would count them twice.
        """
        return []

    # --- Repo settings ---

    def update_repo(self, owner: str, repo: str, **settings: Any) -> dict[str, Any]:
        """Update repository settings (PUT). Returns the updated repo dict.

        Confirmed against the Bitbucket Cloud swagger spec
        (api.bitbucket.org/swagger.json): PUT /repositories/{workspace}/
        {repo_slug} accepts a partial "repository" object; only "owner" and
        "full_name" are ignored (per the endpoint description). Kwargs with
        no Bitbucket field equivalent (e.g. "homepage" — confirmed absent
        from the repository schema — and "has_projects", a GitHub-only
        concept) are silently dropped from the request body, logged at
        DEBUG, and never raised as an error.
        """
        body: dict[str, Any] = {}
        if "description" in settings:
            body["description"] = settings["description"]
        if "has_issues" in settings:
            body["has_issues"] = settings["has_issues"]
        if "has_wiki" in settings:
            body["has_wiki"] = settings["has_wiki"]
        if "default_branch" in settings:
            # mainbranch is a nested {"name": ...} object per the "branch"
            # definition it $refs. Note: there's a known Bitbucket Cloud
            # issue where this field doesn't always take effect server-side
            # (community-reported) — sent best-effort regardless.
            body["mainbranch"] = {"name": settings["default_branch"]}
        if "visibility" in settings:
            # GitHub's --visibility public/private choice maps to
            # Bitbucket's boolean is_private.
            body["is_private"] = settings["visibility"] == "private"

        dropped = [k for k in settings if k not in _UPDATE_REPO_SUPPORTED_KEYS]
        for key in dropped:
            logger.debug(
                "update_repo: '%s' has no Bitbucket equivalent — dropped from PUT body",
                key,
            )

        response = self._request("PUT", f"/repositories/{owner}/{repo}", json=body)
        response.raise_for_status()
        return self._translate_repo(response.json())

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
        """Create or update a file via a multipart POST to .../src.

        ``sha`` is accepted only for signature compatibility with
        GitHubClient — Bitbucket's src endpoint has no CAS/If-Match
        concept; it always creates a new commit on top of the target
        branch's current tip regardless of whether the file previously
        existed, so ``sha`` is unused here.

        Per the confirmed swagger spec, the "branch" form field is
        optional and Bitbucket defaults to the main branch when it's
        omitted — but to be explicit (and to avoid relying on
        possibly-stale main-branch state), an unspecified ``branch`` is
        resolved up front via get_repo()'s translated "default_branch".
        """
        if branch is None:
            repo_data = self.get_repo(owner, repo)
            branch = (repo_data or {}).get("default_branch") or "main"

        # multipart/form-data: file path is the field *name* (not a query
        # param); httpx sets a Content-Disposition filename equal to the
        # dict key by default for raw bytes values, which satisfies
        # Bitbucket's "form fields that have a filename parameter are
        # treated as file uploads" rule.
        files = {path: content.encode("utf-8")}
        data = {"message": message, "branch": branch}

        response = self._request(
            "POST", f"/repositories/{owner}/{repo}/src", data=data, files=files,
        )
        response.raise_for_status()
        # 201 on success with an empty body (confirmed via swagger) — there
        # is no JSON to parse, so return a synthetic dict. No consumer in
        # this codebase currently reads this return value.
        return {"path": path, "branch": branch}

    def get_file(self, owner: str, repo: str, path: str) -> dict[str, Any] | None:
        """Get file metadata from a repository. Returns None on 404.

        Uses ``?format=meta`` so the response is the JSON "commit_file"
        object (path/commit/attributes) rather than the file's raw byte
        contents, which is what a plain GET on a file path returns.
        Always includes a "sha" key (the file's commit hash from the
        commit_file object, or "" if unavailable) so callers that
        pattern-match on file["sha"] (e.g. readme.py) don't crash.
        """
        repo_data = self.get_repo(owner, repo)
        branch = (repo_data or {}).get("default_branch") or "main"

        response = self._request(
            "GET", f"/repositories/{owner}/{repo}/src/{branch}/{path}",
            params={"format": "meta"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        commit_hash = (data.get("commit") or {}).get("hash", "")
        return {
            "sha": commit_hash,
            "path": data.get("path", path),
            "type": data.get("type", ""),
        }

    # --- Collaborators ---

    def list_collaborators(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List effective collaborators on a repository via the workspace
        permissions endpoint, translated to GitHub-shaped dicts.

        GET /workspaces/{workspace}/permissions/repositories returns
        per-user effective permission across the whole workspace in one
        call (paginated); filtered here to this repo via the documented
        ``q=repository.name="{repo}"`` query. ``owner`` is treated as the
        workspace slug, matching how repo dicts from list_repos() populate
        "owner.login" from account.workspace.

        Bitbucket's permission scale (read/write/admin, confirmed via the
        repository_permission.permission swagger enum) is mapped onto
        GitHub's 5-level scale: admin->admin, write->push, read->pull;
        "maintain" and "triage" have no Bitbucket equivalent and are
        always False. Entries with permission "none" (explicit no-access)
        are skipped — GitHub's collaborators endpoint never lists
        no-access entries either.
        """
        items = self.get_paginated(
            f"/workspaces/{owner}/permissions/repositories",
            params={"q": f'repository.name="{repo}"'},
        )
        results: list[dict[str, Any]] = []
        for item in items:
            permission = item.get("permission", _BITBUCKET_PERMISSION_NONE)
            if permission == _BITBUCKET_PERMISSION_NONE:
                continue
            user = item.get("user") or {}
            login = user.get("nickname") or user.get("display_name") or ""
            uid = user.get("account_id") or user.get("uuid") or ""
            results.append({
                "login": login,
                "id": uid,
                "permissions": {
                    "admin": permission == "admin",
                    "maintain": False,
                    "push": permission in ("admin", "write"),
                    "triage": False,
                    "pull": permission in ("admin", "write", "read"),
                },
            })
        return results

    # --- Workspace members ---

    def list_org_members(self, workspace: str) -> list[dict[str, Any]]:
        """List members of a workspace, translated to GitHub-shaped dicts.

        The ``workspace`` param name mirrors GitHubClient.list_org_members
        (org) for interface compatibility — for Bitbucket it's the
        workspace slug. GET /workspaces/{workspace}/members returns
        workspace_membership objects wrapping an "account" (aliased to
        "user" in the response per the confirmed swagger). Bitbucket has
        no GitHub-style integer user ID (account_id is a string, per the
        confirmed "user" schema) — used as-is for the "id" field that
        members.py reads via str(member["id"]).
        """
        items = self.get_paginated(f"/workspaces/{workspace}/members")
        results: list[dict[str, Any]] = []
        for item in items:
            user = item.get("user") or {}
            login = user.get("nickname") or user.get("display_name") or ""
            uid = user.get("account_id") or user.get("uuid") or ""
            results.append({"login": login, "id": uid})
        return results
