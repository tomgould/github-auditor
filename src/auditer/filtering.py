from __future__ import annotations

from typing import Callable, TypeVar

from auditer.models import AccountConfig, ProjectConfig

T = TypeVar("T")


def filter_excluded_repos(
    items: list[T],
    account: AccountConfig,
    get_name: Callable[[T], str],
) -> tuple[list[T], int]:
    """Filter items by account's include_repos and exclude_repos lists (case-insensitive).

    If include_repos is set, only repos in that list are kept.
    Then exclude_repos is applied to remove specific repos.
    Returns (filtered_items, skipped_count).
    """
    original_count = len(items)
    result = items
    if account.include_repos:
        included = {name.lower() for name in account.include_repos}
        result = [item for item in result if get_name(item).lower() in included]
    if account.exclude_repos:
        excluded = {name.lower() for name in account.exclude_repos}
        result = [item for item in result if get_name(item).lower() not in excluded]
    return result, original_count - len(result)


def filter_repos_by_visibility(
    items: list[T],
    account: AccountConfig,
    is_private: Callable[[T], bool],
    is_fork: Callable[[T], bool] | None = None,
    get_name: Callable[[T], str] | None = None,
) -> list[T]:
    """Apply include_private and include_forks filters from account config.

    Repos explicitly listed in include_repos bypass the fork/private filters.
    """
    explicitly_included = (
        {name.lower() for name in account.include_repos}
        if account.include_repos and get_name
        else set()
    )
    result = items
    if not account.include_private:
        result = [
            item for item in result
            if not is_private(item)
            or (get_name and get_name(item).lower() in explicitly_included)
        ]
    if not account.include_forks and is_fork is not None:
        result = [
            item for item in result
            if not is_fork(item)
            or (get_name and get_name(item).lower() in explicitly_included)
        ]
    return result


def filter_by_project(
    items: list[T],
    project: ProjectConfig,
    account_slug: str,
    get_name: Callable[[T], str],
) -> tuple[list[T], int]:
    """Filter items to only those in the project for the given account.

    If the account is in the project's accounts list, all items pass through.
    Otherwise, only items matching the project's repos list are kept.
    Returns (filtered_items, skipped_count).
    """
    # Account-level inclusion: all repos pass through
    if account_slug in project.accounts:
        return items, 0

    prefix = f"{account_slug}/"
    allowed = {
        entry[len(prefix):].lower()
        for entry in project.repos
        if entry.lower().startswith(prefix.lower())
    }
    original_count = len(items)
    result = [item for item in items if get_name(item).lower() in allowed]
    return result, original_count - len(result)


def filter_repos_by_activity(
    items: list[T],
    months: int,
    get_pushed_at: Callable[[T], str],
) -> tuple[list[T], int]:
    """Filter items to those pushed to within the last `months` months.

    Uses the same days/30.44 month-length approximation as
    RepoInfo.months_inactive, so this agrees with `archive --months` and
    the audit report's staleness figures. Items with a missing or
    unparseable pushed_at are excluded (treated as stale) rather than
    assumed recent. Returns (filtered_items, skipped_count).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    original_count = len(items)
    result: list[T] = []
    for item in items:
        pushed_at = get_pushed_at(item)
        if not pushed_at:
            continue
        try:
            then = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        months_inactive = (now - then).days / 30.44
        if months_inactive <= months:
            result.append(item)
    return result, original_count - len(result)
