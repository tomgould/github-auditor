from datetime import datetime, timedelta, timezone

from auditer.filtering import (
    filter_by_project,
    filter_excluded_repos,
    filter_repos_by_activity,
    filter_repos_by_visibility,
)
from auditer.models import AccountConfig, ProjectConfig


def _iso_months_ago(months: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=months * 30.44)).isoformat()


def _account(**kwargs) -> AccountConfig:
    defaults = dict(slug="test", type="user", username="testuser")
    defaults.update(kwargs)
    return AccountConfig(**defaults)


class TestFilterExcludedRepos:
    def test_no_exclusions(self):
        items = [{"name": "a"}, {"name": "b"}]
        result, skipped = filter_excluded_repos(
            items, _account(), lambda r: r["name"]
        )
        assert result == items
        assert skipped == 0

    def test_excludes_matching_repos(self):
        items = [{"name": "keep"}, {"name": "skip-me"}, {"name": "also-keep"}]
        account = _account(exclude_repos=["skip-me"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert len(result) == 2
        assert skipped == 1
        assert all(r["name"] != "skip-me" for r in result)

    def test_case_insensitive(self):
        items = [{"name": "MyRepo"}, {"name": "other"}]
        account = _account(exclude_repos=["myrepo"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert len(result) == 1
        assert skipped == 1
        assert result[0]["name"] == "other"

    def test_multiple_exclusions(self):
        items = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        account = _account(exclude_repos=["a", "c"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert len(result) == 1
        assert skipped == 2
        assert result[0]["name"] == "b"

    def test_works_with_objects(self):
        class Repo:
            def __init__(self, name):
                self.name = name

        items = [Repo("keep"), Repo("drop")]
        account = _account(exclude_repos=["drop"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r.name
        )
        assert len(result) == 1
        assert skipped == 1

    def test_empty_list(self):
        account = _account(exclude_repos=["something"])
        result, skipped = filter_excluded_repos([], account, lambda r: r["name"])
        assert result == []
        assert skipped == 0


class TestFilterIncludeRepos:
    def test_include_only_listed_repos(self):
        items = [{"name": "wanted"}, {"name": "other"}, {"name": "also-wanted"}]
        account = _account(include_repos=["wanted", "also-wanted"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert len(result) == 2
        assert skipped == 1
        names = {r["name"] for r in result}
        assert names == {"wanted", "also-wanted"}

    def test_include_case_insensitive(self):
        items = [{"name": "MyRepo"}, {"name": "other"}]
        account = _account(include_repos=["myrepo"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert len(result) == 1
        assert result[0]["name"] == "MyRepo"

    def test_include_and_exclude_combined(self):
        """include_repos is applied first, then exclude_repos removes from the result."""
        items = [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]
        account = _account(include_repos=["a", "b", "c"], exclude_repos=["b"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert len(result) == 2
        assert skipped == 2
        names = {r["name"] for r in result}
        assert names == {"a", "c"}

    def test_include_empty_list_no_filtering(self):
        items = [{"name": "a"}, {"name": "b"}]
        account = _account(include_repos=[])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert result == items
        assert skipped == 0

    def test_include_no_matches(self):
        items = [{"name": "a"}, {"name": "b"}]
        account = _account(include_repos=["nonexistent"])
        result, skipped = filter_excluded_repos(
            items, account, lambda r: r["name"]
        )
        assert result == []
        assert skipped == 2


class TestFilterReposByVisibility:
    def test_no_filtering(self):
        items = [
            {"name": "pub", "private": False, "fork": False},
            {"name": "priv", "private": True, "fork": False},
            {"name": "forked", "private": False, "fork": True},
        ]
        account = _account(include_private=True, include_forks=True)
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
        )
        assert len(result) == 3

    def test_exclude_private(self):
        items = [
            {"name": "pub", "private": False},
            {"name": "priv", "private": True},
        ]
        account = _account(include_private=False)
        result = filter_repos_by_visibility(
            items, account, is_private=lambda r: r["private"]
        )
        assert len(result) == 1
        assert result[0]["name"] == "pub"

    def test_exclude_forks(self):
        items = [
            {"name": "original", "private": False, "fork": False},
            {"name": "forked", "private": False, "fork": True},
        ]
        account = _account(include_forks=False)
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
        )
        assert len(result) == 1
        assert result[0]["name"] == "original"

    def test_exclude_both(self):
        items = [
            {"name": "pub", "private": False, "fork": False},
            {"name": "priv", "private": True, "fork": False},
            {"name": "forked", "private": False, "fork": True},
            {"name": "priv-fork", "private": True, "fork": True},
        ]
        account = _account(include_private=False, include_forks=False)
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
        )
        assert len(result) == 1
        assert result[0]["name"] == "pub"

    def test_include_repos_overrides_fork_filter(self):
        """Repos in include_repos are kept even when include_forks=False."""
        items = [
            {"name": "Scrapling", "private": False, "fork": False},
            {"name": "patchright", "private": False, "fork": True},
            {"name": "other-fork", "private": False, "fork": True},
        ]
        account = _account(
            include_forks=False,
            include_repos=["Scrapling", "patchright"],
        )
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
            get_name=lambda r: r["name"],
        )
        names = {r["name"] for r in result}
        assert names == {"Scrapling", "patchright"}

    def test_include_repos_overrides_private_filter(self):
        """Repos in include_repos are kept even when include_private=False."""
        items = [
            {"name": "public-repo", "private": False, "fork": False},
            {"name": "secret-repo", "private": True, "fork": False},
            {"name": "other-private", "private": True, "fork": False},
        ]
        account = _account(
            include_private=False,
            include_repos=["public-repo", "secret-repo"],
        )
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
            get_name=lambda r: r["name"],
        )
        names = {r["name"] for r in result}
        assert names == {"public-repo", "secret-repo"}

    def test_include_repos_override_case_insensitive(self):
        """The include_repos bypass is case-insensitive."""
        items = [
            {"name": "PatchRight", "private": False, "fork": True},
        ]
        account = _account(
            include_forks=False,
            include_repos=["patchright"],
        )
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
            get_name=lambda r: r["name"],
        )
        assert len(result) == 1
        assert result[0]["name"] == "PatchRight"

    def test_without_get_name_no_override(self):
        """Without get_name, include_repos cannot override visibility filters."""
        items = [
            {"name": "patchright", "private": False, "fork": True},
        ]
        account = _account(
            include_forks=False,
            include_repos=["patchright"],
        )
        result = filter_repos_by_visibility(
            items, account,
            is_private=lambda r: r["private"],
            is_fork=lambda r: r["fork"],
        )
        assert len(result) == 0

    def test_no_fork_callback_skips_fork_filter(self):
        items = [
            {"name": "a", "private": False, "fork": True},
        ]
        account = _account(include_forks=False)
        # No is_fork provided — fork filtering should be skipped
        result = filter_repos_by_visibility(
            items, account, is_private=lambda r: r["private"]
        )
        assert len(result) == 1


class TestFilterByProject:
    def test_filters_to_project_repos(self):
        project = ProjectConfig(
            name="proj",
            repos=["acct-a/repo1", "acct-a/repo3", "acct-b/repo2"],
        )
        items = [{"name": "repo1"}, {"name": "repo2"}, {"name": "repo3"}]
        result, skipped = filter_by_project(
            items, project, "acct-a", lambda r: r["name"]
        )
        assert len(result) == 2
        assert skipped == 1
        names = {r["name"] for r in result}
        assert names == {"repo1", "repo3"}

    def test_different_account(self):
        project = ProjectConfig(
            name="proj",
            repos=["acct-a/repo1"],
        )
        items = [{"name": "repo1"}, {"name": "repo2"}]
        result, skipped = filter_by_project(
            items, project, "acct-b", lambda r: r["name"]
        )
        assert result == []
        assert skipped == 2

    def test_case_insensitive_repo_name(self):
        project = ProjectConfig(
            name="proj",
            repos=["acct/MyRepo"],
        )
        items = [{"name": "myrepo"}, {"name": "other"}]
        result, skipped = filter_by_project(
            items, project, "acct", lambda r: r["name"]
        )
        assert len(result) == 1
        assert result[0]["name"] == "myrepo"

    def test_empty_project_repos(self):
        project = ProjectConfig(name="empty", repos=[])
        items = [{"name": "repo1"}]
        result, skipped = filter_by_project(
            items, project, "acct", lambda r: r["name"]
        )
        assert result == []
        assert skipped == 1

    def test_account_level_inclusion_passes_all(self):
        project = ProjectConfig(
            name="proj",
            accounts=["acct-a"],
            repos=["acct-b/repo2"],
        )
        items = [{"name": "repo1"}, {"name": "repo2"}, {"name": "repo3"}]
        result, skipped = filter_by_project(
            items, project, "acct-a", lambda r: r["name"]
        )
        assert len(result) == 3
        assert skipped == 0

    def test_account_level_does_not_affect_other_accounts(self):
        project = ProjectConfig(
            name="proj",
            accounts=["acct-a"],
            repos=["acct-b/repo2"],
        )
        items = [{"name": "repo1"}, {"name": "repo2"}, {"name": "repo3"}]
        result, skipped = filter_by_project(
            items, project, "acct-b", lambda r: r["name"]
        )
        assert len(result) == 1
        assert result[0]["name"] == "repo2"
        assert skipped == 2

    def test_mixed_accounts_and_repos(self):
        project = ProjectConfig(
            name="proj",
            accounts=["acct-a"],
            repos=["acct-b/specific-repo"],
        )
        # acct-a: all pass through
        items_a = [{"name": "x"}, {"name": "y"}]
        result_a, skipped_a = filter_by_project(
            items_a, project, "acct-a", lambda r: r["name"]
        )
        assert len(result_a) == 2
        assert skipped_a == 0
        # acct-b: only specific-repo
        items_b = [{"name": "specific-repo"}, {"name": "other"}]
        result_b, skipped_b = filter_by_project(
            items_b, project, "acct-b", lambda r: r["name"]
        )
        assert len(result_b) == 1
        assert skipped_b == 1

    def test_works_with_objects(self):
        class Repo:
            def __init__(self, name):
                self.name = name

        project = ProjectConfig(name="proj", repos=["acct/keep"])
        items = [Repo("keep"), Repo("drop")]
        result, skipped = filter_by_project(
            items, project, "acct", lambda r: r.name
        )
        assert len(result) == 1
        assert skipped == 1


class TestFilterReposByActivity:
    def test_recent_repo_kept(self):
        items = [{"name": "active", "pushed_at": _iso_months_ago(1)}]
        result, skipped = filter_repos_by_activity(
            items, 12, lambda r: r["pushed_at"]
        )
        assert len(result) == 1
        assert skipped == 0

    def test_old_repo_filtered_out(self):
        items = [{"name": "stale", "pushed_at": _iso_months_ago(24)}]
        result, skipped = filter_repos_by_activity(
            items, 12, lambda r: r["pushed_at"]
        )
        assert len(result) == 0
        assert skipped == 1

    def test_mixed_repos(self):
        items = [
            {"name": "active", "pushed_at": _iso_months_ago(1)},
            {"name": "stale", "pushed_at": _iso_months_ago(24)},
        ]
        result, skipped = filter_repos_by_activity(
            items, 12, lambda r: r["pushed_at"]
        )
        assert [r["name"] for r in result] == ["active"]
        assert skipped == 1

    def test_missing_pushed_at_excluded(self):
        items = [{"name": "unknown", "pushed_at": ""}]
        result, skipped = filter_repos_by_activity(
            items, 12, lambda r: r["pushed_at"]
        )
        assert len(result) == 0
        assert skipped == 1

    def test_unparseable_pushed_at_excluded_not_crashed(self):
        items = [{"name": "bad-date", "pushed_at": "not-a-date"}]
        result, skipped = filter_repos_by_activity(
            items, 12, lambda r: r["pushed_at"]
        )
        assert len(result) == 0
        assert skipped == 1

    def test_z_suffix_handled(self):
        # GitHub's API returns "Z" suffix ISO timestamps rather than "+00:00"
        z_timestamp = _iso_months_ago(1).split("+")[0] + "Z"
        items = [{"name": "active", "pushed_at": z_timestamp}]
        result, skipped = filter_repos_by_activity(
            items, 12, lambda r: r["pushed_at"]
        )
        assert len(result) == 1
        assert skipped == 0
