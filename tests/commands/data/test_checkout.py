from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from click.testing import CliRunner
from rich.console import Console

from auditer.commands.data.checkout import (
    _print_repo_details,
    _repo_web_url,
    run_checkout,
)
from auditer.config import load_config_from_dict
from auditer.git_ops import BranchUpdate, GitOpResult, detect_orphaned_repos


@pytest.fixture
def config(tmp_path):
    return load_config_from_dict(
        {
            "defaults": {"stale_months": 12, "data_dir": str(tmp_path / "data")},
            "accounts": [
                {
                    "slug": "test",
                    "type": "user",
                    "username": "testuser",
                    "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                }
            ],
        }
    )


@pytest.fixture
def account(config):
    return config.accounts[0]


class TestRepoWebUrl:
    def test_strips_dot_git(self):
        assert _repo_web_url("https://github.com/user/repo.git") == "https://github.com/user/repo"

    def test_no_dot_git(self):
        assert _repo_web_url("https://github.com/user/repo") == "https://github.com/user/repo"

    def test_preserves_path(self):
        assert _repo_web_url("https://github.com/org/my-repo.git") == "https://github.com/org/my-repo"


class TestPrintRepoDetails:
    def _capture_output(self, name, web_url, result):
        """Capture rich console output as plain text."""
        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=200, soft_wrap=True)
        with patch("auditer.commands.data.checkout.console", test_console):
            _print_repo_details(name, web_url, result)
        return buf.getvalue()

    def test_branch_update_with_commits(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            branch_updates=[
                BranchUpdate(
                    name="main",
                    old_hash="aaa1111",
                    new_hash="bbb2222",
                    commit_count=2,
                    authors=["Alice", "Bob"],
                    commits=[
                        ("abc1234", "Fix login bug", "Alice"),
                        ("def5678", "Update deps", "Bob"),
                    ],
                )
            ],
        )
        output = self._capture_output("my-repo", "https://github.com/user/my-repo", result)
        assert "my-repo" in output
        assert "main" in output
        assert "+2 commits" in output
        assert "Alice" in output
        assert "Bob" in output
        assert "Fix login bug" in output
        assert "Update deps" in output
        assert "abc1234" in output
        assert "https://github.com/user/my-repo/compare/aaa1111...bbb2222" in output

    def test_single_commit_no_plural(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            branch_updates=[
                BranchUpdate(
                    name="main",
                    old_hash="aaa",
                    new_hash="bbb",
                    commit_count=1,
                    authors=["Alice"],
                    commits=[("abc1234", "Single fix", "Alice")],
                )
            ],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "+1 commit" in output
        assert "+1 commits" not in output

    def test_force_push_display(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            branch_updates=[
                BranchUpdate(
                    name="feat/work",
                    old_hash="old123",
                    new_hash="new456",
                    commit_count=2,
                    authors=["Alice"],
                    commits=[
                        ("aaa1111", "Rewritten commit 1", "Alice"),
                        ("bbb2222", "Rewritten commit 2", "Alice"),
                    ],
                    forced=True,
                    removed_count=3,
                )
            ],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "force-pushed" in output
        assert "+2 commits" in output
        assert "-3 commits" in output

    def test_force_push_ref_reset(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            branch_updates=[
                BranchUpdate(
                    name="main",
                    old_hash="old",
                    new_hash="new",
                    commit_count=0,
                    forced=True,
                    removed_count=0,
                )
            ],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "force-pushed" in output
        assert "ref reset" in output

    def test_new_branches_display(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            new_branches=["develop", "feat/login"],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "new branches:" in output
        assert "develop" in output
        assert "feat/login" in output
        assert "https://github.com/u/repo/tree/develop" in output
        assert "https://github.com/u/repo/tree/feat/login" in output

    def test_new_tags_display(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            new_tags=["v1.0.0", "v2.0.0"],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "new tags:" in output
        assert "v1.0.0" in output
        assert "v2.0.0" in output
        assert "https://github.com/u/repo/releases/tag/v1.0.0" in output

    def test_ref_updated_no_commits(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            branch_updates=[
                BranchUpdate(
                    name="main",
                    old_hash="aaa",
                    new_hash="bbb",
                    commit_count=0,
                )
            ],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "ref updated" in output

    def test_branch_url_with_special_chars(self):
        result = GitOpResult(
            success=True,
            message="Updated",
            new_branches=["feat/ES-6031"],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "https://github.com/u/repo/tree/feat/ES-6031" in output


class TestDetectOrphans:
    def test_no_orphans(self, tmp_path):
        pub = tmp_path / "public" / "repo1"
        pub.mkdir(parents=True)
        orphans = detect_orphaned_repos(tmp_path, {"repo1"})
        assert len(orphans) == 0

    def test_orphan_detected(self, tmp_path):
        pub = tmp_path / "public" / "old-repo"
        pub.mkdir(parents=True)
        orphans = detect_orphaned_repos(tmp_path, {"repo1"})
        assert len(orphans) == 1
        assert orphans[0] == ("public", "old-repo")

    def test_nonexistent_dir(self, tmp_path):
        orphans = detect_orphaned_repos(tmp_path, {"repo1"})
        assert len(orphans) == 0

    def test_multiple_orphans_across_subdirs(self, tmp_path):
        (tmp_path / "public" / "gone1").mkdir(parents=True)
        (tmp_path / "private" / "gone2").mkdir(parents=True)
        orphans = detect_orphaned_repos(tmp_path, {"active-repo"})
        assert len(orphans) == 2
        names = {o[1] for o in orphans}
        assert names == {"gone1", "gone2"}


class TestRunCheckout:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_clone_new_repo(self, mock_pull, mock_clone, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "new-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/new-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        mock_clone.assert_called_once()
        mock_pull.assert_not_called()

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_pull_existing_repo(self, mock_pull, mock_clone, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        # Create existing repo dir with .git
        repo_dir = tmp_path / "data" / "test" / "public" / "existing-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "existing-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/existing-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        run_checkout(config, account)

        mock_pull.assert_called_once()
        mock_clone.assert_not_called()

    @respx.mock
    def test_dry_run(self, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/repo1.git",
                        "fork": False,
                    }
                ],
            )
        )

        # Should not raise — no git operations performed
        run_checkout(config, account, dry_run=True)

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_pull_with_changes_tracked(self, mock_pull, mock_clone, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        repo_dir = tmp_path / "data" / "test" / "public" / "active-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "active-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/active-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_pull.return_value = GitOpResult(
            success=True,
            message="Updated",
            branch_updates=[
                BranchUpdate(
                    name="main",
                    old_hash="aaa",
                    new_hash="bbb",
                    commit_count=3,
                    authors=["Alice"],
                    commits=[
                        ("c1", "First", "Alice"),
                        ("c2", "Second", "Alice"),
                        ("c3", "Third", "Alice"),
                    ],
                )
            ],
            new_tags=["v1.0"],
        )

        run_checkout(config, account, fetch_branches=True, fetch_tags=True)

        mock_pull.assert_called_once()
        call_kwargs = mock_pull.call_args
        assert call_kwargs.kwargs.get("fetch_branches") or call_kwargs[1].get("fetch_branches")
        assert call_kwargs.kwargs.get("fetch_tags") or call_kwargs[1].get("fetch_tags")

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_error_tracked(self, mock_pull, mock_clone, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        repo_dir = tmp_path / "data" / "test" / "public" / "broken-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "broken-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/broken-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_pull.return_value = GitOpResult(success=False, message="Fetch failed: timeout")

        # Should not raise — errors are collected and displayed
        run_checkout(config, account)

        mock_pull.assert_called_once()

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_private_repo_goes_to_private_dir(
        self, mock_pull, mock_clone, config, account, tmp_path
    ):
        config.data_dir = str(tmp_path / "data")

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "secret-repo",
                        "owner": {"login": "testuser"},
                        "private": True,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/secret-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        mock_clone.assert_called_once()
        call_kwargs = mock_clone.call_args
        target_dir = call_kwargs.kwargs.get("target_dir") or call_kwargs[1].get("target_dir")
        if target_dir is None:
            # positional arg
            target_dir = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs.kwargs["target_dir"]
        assert "private" in str(target_dir)

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_months_filter_excludes_stale_repos(self, mock_pull, mock_clone, config, account, tmp_path):
        from datetime import datetime, timedelta, timezone

        config.data_dir = str(tmp_path / "data")
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        stale = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "active-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/active-repo.git",
                        "fork": False,
                        "pushed_at": recent,
                    },
                    {
                        "name": "stale-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/stale-repo.git",
                        "fork": False,
                        "pushed_at": stale,
                    },
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account, months=12)

        mock_clone.assert_called_once()
        cloned_name = mock_clone.call_args.kwargs.get("name")
        assert cloned_name == "active-repo"

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_account_active_within_months_applies_without_cli_flag(
        self, mock_pull, mock_clone, config, account, tmp_path
    ):
        from datetime import datetime, timedelta, timezone

        config.data_dir = str(tmp_path / "data")
        account.active_within_months = 12
        recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        stale = (datetime.now(timezone.utc) - timedelta(days=800)).isoformat()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "active-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/active-repo.git",
                        "fork": False,
                        "pushed_at": recent,
                    },
                    {
                        "name": "stale-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/stale-repo.git",
                        "fork": False,
                        "pushed_at": stale,
                    },
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        # No months= kwarg passed — the account's own config field should apply.
        run_checkout(config, account)

        mock_clone.assert_called_once()
        cloned_name = mock_clone.call_args.kwargs.get("name")
        assert cloned_name == "active-repo"

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_cli_months_overrides_account_default(self, mock_pull, mock_clone, config, account, tmp_path):
        from datetime import datetime, timedelta, timezone

        config.data_dir = str(tmp_path / "data")
        account.active_within_months = 1  # very strict account default
        pushed_6_months_ago = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/repo.git",
                        "fork": False,
                        "pushed_at": pushed_6_months_ago,
                    },
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        # CLI months=12 should override the account's stricter active_within_months=1
        run_checkout(config, account, months=12)

        mock_clone.assert_called_once()


class TestExcludeRepos:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_excluded_repos_skipped(self, mock_pull, mock_clone, tmp_path):
        config = load_config_from_dict(
            {
                "defaults": {"stale_months": 12, "data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                        "exclude_repos": ["skip-me"],
                    }
                ],
            }
        )
        account = config.accounts[0]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "keep-me",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/keep-me.git",
                        "fork": False,
                    },
                    {
                        "name": "skip-me",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/skip-me.git",
                        "fork": False,
                    },
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        # Only keep-me should be cloned, skip-me should be excluded
        mock_clone.assert_called_once()
        call_kwargs = mock_clone.call_args
        name = call_kwargs.kwargs.get("name") or call_kwargs[1].get("name")
        assert name == "keep-me"

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_exclude_is_case_insensitive(self, mock_pull, mock_clone, tmp_path):
        config = load_config_from_dict(
            {
                "defaults": {"stale_months": 12, "data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                        "exclude_repos": ["Skip-Me"],
                    }
                ],
            }
        )
        account = config.accounts[0]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "skip-me",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/skip-me.git",
                        "fork": False,
                    },
                ],
            )
        )

        run_checkout(config, account)

        mock_clone.assert_not_called()
        mock_pull.assert_not_called()


class TestSkipDirty:
    @respx.mock
    @patch("auditer.commands.data.checkout.is_repo_dirty", return_value=True)
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_dirty_repo_skipped(self, mock_pull, mock_clone, mock_dirty, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        # Create existing repo dir with .git
        repo_dir = tmp_path / "data" / "test" / "public" / "dirty-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "dirty-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/dirty-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        run_checkout(config, account, skip_dirty=True)

        mock_dirty.assert_called_once()
        mock_pull.assert_not_called()

    @respx.mock
    @patch("auditer.commands.data.checkout.is_repo_dirty", return_value=False)
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_clean_repo_not_skipped(self, mock_pull, mock_clone, mock_dirty, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        repo_dir = tmp_path / "data" / "test" / "public" / "clean-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "clean-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/clean-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        run_checkout(config, account, skip_dirty=True)

        mock_dirty.assert_called_once()
        mock_pull.assert_called_once()

    @respx.mock
    @patch("auditer.commands.data.checkout.is_repo_dirty", return_value=True)
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_skip_dirty_on_by_default(self, mock_pull, mock_clone, mock_dirty, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        repo_dir = tmp_path / "data" / "test" / "public" / "dirty-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "dirty-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/dirty-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        # skip_dirty defaults to True — dirty repo is skipped without explicit flag
        run_checkout(config, account)

        mock_dirty.assert_called_once()
        mock_pull.assert_not_called()

    @respx.mock
    @patch("auditer.commands.data.checkout.is_repo_dirty", return_value=True)
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_no_skip_dirty_forces_update(self, mock_pull, mock_clone, mock_dirty, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        repo_dir = tmp_path / "data" / "test" / "public" / "dirty-repo" / ".git"
        repo_dir.mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "dirty-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/dirty-repo.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        # --no-skip-dirty forces update even on dirty repos
        run_checkout(config, account, skip_dirty=False)

        mock_dirty.assert_not_called()
        mock_pull.assert_called_once()


class TestPerAccountDataDir:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_account_data_dir_override(self, mock_pull, mock_clone, tmp_path):
        custom_dir = tmp_path / "custom"
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                        "data_dir": str(custom_dir),
                    }
                ],
            }
        )
        account = config.accounts[0]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/repo1.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        mock_clone.assert_called_once()
        call_kwargs = mock_clone.call_args
        target_dir = call_kwargs.kwargs.get("target_dir") or call_kwargs[1].get("target_dir")
        # Should be under custom_dir/test/public, not the global data_dir
        assert str(custom_dir / "test") in str(target_dir)
        assert str(tmp_path / "data") not in str(target_dir)

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_account_data_dir_default(self, mock_pull, mock_clone, tmp_path):
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                    }
                ],
            }
        )
        account = config.accounts[0]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/repo1.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        mock_clone.assert_called_once()
        call_kwargs = mock_clone.call_args
        target_dir = call_kwargs.kwargs.get("target_dir") or call_kwargs[1].get("target_dir")
        # Should be under global data_dir/test/public
        assert str(tmp_path / "data" / "test") in str(target_dir)


class TestCheckoutCmd:
    def test_full_flag_sets_branches_and_tags(self):
        from auditer.commands.data.checkout import checkout_cmd

        runner = CliRunner()
        # The command needs a config context — we test that --full is accepted
        # and wires to branches=True, tags=True by checking the click params
        params = {p.name: p for p in checkout_cmd.params}
        assert "full" in params
        assert params["full"].is_flag
        assert "branches" in params
        assert "tags" in params

    def test_no_account_or_all_exits(self, tmp_path):
        import yaml

        from auditer.cli import cli

        runner = CliRunner()
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "defaults": {"data_dir": str(tmp_path / "data")},
            "accounts": [{
                "slug": "t", "type": "user", "username": "u",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
            }],
        }))
        result = runner.invoke(cli, ["--config", str(config_path), "checkout"])
        assert result.exit_code != 0

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_single_account_flag(self, mock_pull, mock_clone, tmp_path):
        import yaml

        from auditer.cli import cli

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump({
            "defaults": {"data_dir": str(tmp_path / "data")},
            "accounts": [{
                "slug": "myslug", "type": "user", "username": "testuser",
                "token": "ghp_testtoken1234567890abcdefghijklmnopq",
            }],
        }))

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[{
                "name": "r1", "owner": {"login": "testuser"},
                "private": False, "default_branch": "main",
                "clone_url": "https://github.com/testuser/r1.git", "fork": False,
            }])
        )
        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        runner = CliRunner()
        result = runner.invoke(cli, [
            "--config", str(config_path), "checkout", "--account", "myslug",
        ])
        assert result.exit_code == 0
        mock_clone.assert_called_once()


class TestThirdPartyCheckout:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_third_party_account_uses_users_endpoint(self, mock_pull, mock_clone, tmp_path):
        """Third-party account (token owner != username) uses /users/{username}/repos."""
        config = load_config_from_dict(
            {
                "defaults": {"stale_months": 12, "data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "famous",
                        "type": "user",
                        "username": "famous-dev",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                        "include_private": False,
                    }
                ],
            }
        )
        account = config.accounts[0]

        # validate_token returns the token holder's login (different from username)
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "my-own-user"})
        )
        # Should call /users/famous-dev/repos, NOT /user/repos
        respx.get("https://api.github.com/users/famous-dev/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "cool-project",
                        "owner": {"login": "famous-dev"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/famous-dev/cool-project.git",
                        "fork": False,
                    }
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        mock_clone.assert_called_once()


class TestPrintRepoDetailsTruncation:
    def _capture_output(self, name, web_url, result):
        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=200, soft_wrap=True)
        with patch("auditer.commands.data.checkout.console", test_console):
            _print_repo_details(name, web_url, result)
        return buf.getvalue()

    def test_truncates_long_commit_subject(self):
        long_subject = "x" * 200
        result = GitOpResult(
            success=True, message="Updated",
            branch_updates=[BranchUpdate(
                name="main", old_hash="a", new_hash="b",
                commit_count=1, authors=["Alice"],
                commits=[("abc1234", long_subject, "Alice")],
            )],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "..." in output
        assert long_subject not in output

    def test_truncates_commits_over_max(self):
        from auditer.commands.data.checkout import MAX_COMMITS_SHOWN

        commits = [(f"hash{i}", f"Commit {i}", "Alice") for i in range(MAX_COMMITS_SHOWN + 5)]
        result = GitOpResult(
            success=True, message="Updated",
            branch_updates=[BranchUpdate(
                name="main", old_hash="a", new_hash="b",
                commit_count=len(commits), authors=["Alice"],
                commits=commits,
            )],
        )
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "... and 5 more" in output

    def test_truncates_branches_over_max(self):
        from auditer.commands.data.checkout import MAX_REFS_SHOWN

        branches = [f"branch-{i}" for i in range(MAX_REFS_SHOWN + 3)]
        result = GitOpResult(success=True, message="Updated", new_branches=branches)
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "... and 3 more" in output

    def test_truncates_tags_over_max(self):
        from auditer.commands.data.checkout import MAX_REFS_SHOWN

        tags = [f"v{i}.0" for i in range(MAX_REFS_SHOWN + 2)]
        result = GitOpResult(success=True, message="Updated", new_tags=tags)
        output = self._capture_output("repo", "https://github.com/u/repo", result)
        assert "... and 2 more" in output


class TestCheckoutCloneError:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_clone_error_displayed(self, mock_pull, mock_clone, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[{
                "name": "fail-clone", "owner": {"login": "testuser"},
                "private": False, "default_branch": "main",
                "clone_url": "https://github.com/testuser/fail-clone.git", "fork": False,
            }])
        )

        mock_clone.return_value = GitOpResult(
            success=False, message="Clone failed: permission denied"
        )

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=200)
        with patch("auditer.commands.data.checkout.console", test_console):
            run_checkout(config, account)
        output = buf.getvalue()
        assert "ERROR" in output
        assert "fail-clone" in output


class TestCheckoutOrphans:
    @respx.mock
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    @patch("auditer.commands.data.checkout.clone_repo")
    def test_orphaned_repos_displayed(self, mock_clone, mock_pull, config, account, tmp_path):
        config.data_dir = str(tmp_path / "data")
        data_dir = tmp_path / "data" / "test"

        # Create an orphaned local repo
        (data_dir / "public" / "orphan-repo").mkdir(parents=True)
        # Existing repo with .git
        (data_dir / "public" / "active-repo" / ".git").mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[{
                "name": "active-repo", "owner": {"login": "testuser"},
                "private": False, "default_branch": "main",
                "clone_url": "https://github.com/testuser/active-repo.git", "fork": False,
            }])
        )
        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=200)
        with patch("auditer.commands.data.checkout.console", test_console):
            run_checkout(config, account)
        output = buf.getvalue()
        assert "ORPHAN" in output
        assert "orphan-repo" in output


    @respx.mock
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    @patch("auditer.commands.data.checkout.clone_repo")
    def test_forked_repo_not_flagged_as_orphan(self, mock_clone, mock_pull, tmp_path):
        """Repos filtered by include_forks=false should not appear as orphans."""
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                        "include_forks": False,
                    }
                ],
            }
        )
        account = config.accounts[0]
        data_dir = tmp_path / "data" / "test"

        # Local checkouts: one owned repo, one forked repo
        (data_dir / "public" / "owned-repo" / ".git").mkdir(parents=True)
        (data_dir / "public" / "forked-repo" / ".git").mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[
                {
                    "name": "owned-repo", "owner": {"login": "testuser"},
                    "private": False, "default_branch": "main",
                    "clone_url": "https://github.com/testuser/owned-repo.git",
                    "fork": False,
                },
                {
                    "name": "forked-repo", "owner": {"login": "testuser"},
                    "private": False, "default_branch": "main",
                    "clone_url": "https://github.com/testuser/forked-repo.git",
                    "fork": True,
                },
            ])
        )
        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=200)
        with patch("auditer.commands.data.checkout.console", test_console):
            run_checkout(config, account)
        output = buf.getvalue()
        # forked-repo should NOT be flagged as orphan (it exists on GitHub)
        assert "ORPHAN" not in output
        # Only owned-repo should be checked out (fork filtered from checkout)
        mock_pull.assert_called_once()

    @respx.mock
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    @patch("auditer.commands.data.checkout.clone_repo")
    def test_project_filtered_repo_not_flagged_as_orphan(self, mock_clone, mock_pull, tmp_path):
        """Repos filtered by --project should not appear as orphans."""
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                    }
                ],
                "projects": {
                    "my-proj": {"repos": ["test/wanted-repo"]},
                },
            }
        )
        account = config.accounts[0]
        project = config.projects[0]
        data_dir = tmp_path / "data" / "test"

        # Local checkouts: both repos exist on disk
        (data_dir / "public" / "wanted-repo" / ".git").mkdir(parents=True)
        (data_dir / "public" / "other-repo" / ".git").mkdir(parents=True)

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(200, json=[
                {
                    "name": "wanted-repo", "owner": {"login": "testuser"},
                    "private": False, "default_branch": "main",
                    "clone_url": "https://github.com/testuser/wanted-repo.git",
                    "fork": False,
                },
                {
                    "name": "other-repo", "owner": {"login": "testuser"},
                    "private": False, "default_branch": "main",
                    "clone_url": "https://github.com/testuser/other-repo.git",
                    "fork": False,
                },
            ])
        )
        mock_pull.return_value = GitOpResult(success=True, message="Updated")

        buf = StringIO()
        test_console = Console(file=buf, force_terminal=False, width=200)
        with patch("auditer.commands.data.checkout.console", test_console):
            run_checkout(config, account, project=project)
        output = buf.getvalue()
        # other-repo should NOT be flagged as orphan (it exists on GitHub)
        assert "ORPHAN" not in output


class TestProjectFilter:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_project_filters_repos(self, mock_pull, mock_clone, tmp_path):
        """--project filters repos to only those in the project."""
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                    }
                ],
                "projects": {
                    "my-proj": {"repos": ["test/wanted-repo"]},
                },
            }
        )
        account = config.accounts[0]
        project = config.projects[0]

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "wanted-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/wanted-repo.git",
                        "fork": False,
                    },
                    {
                        "name": "other-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/other-repo.git",
                        "fork": False,
                    },
                ],
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account, project=project)

        # Only wanted-repo should be cloned
        mock_clone.assert_called_once()
        call_kwargs = mock_clone.call_args
        name = call_kwargs.kwargs.get("name") or call_kwargs[1].get("name")
        assert name == "wanted-repo"

    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_project_no_repos_for_account(self, mock_pull, mock_clone, tmp_path):
        """Project with no repos for this account skips all."""
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
                "accounts": [
                    {
                        "slug": "test",
                        "type": "user",
                        "username": "testuser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                    },
                    {
                        "slug": "other",
                        "type": "user",
                        "username": "otheruser",
                        "token": "ghp_testtoken1234567890abcdefghijklmnopq",
                    },
                ],
                "projects": {
                    "other-proj": {"repos": ["other/some-repo"]},
                },
            }
        )
        account = config.accounts[0]  # "test" account
        project = config.projects[0]  # project with only "other" repos

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "testuser"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "my-repo",
                        "owner": {"login": "testuser"},
                        "private": False,
                        "default_branch": "main",
                        "clone_url": "https://github.com/testuser/my-repo.git",
                        "fork": False,
                    },
                ],
            )
        )

        run_checkout(config, account, project=project)

        # No repos should be cloned
        mock_clone.assert_not_called()
        mock_pull.assert_not_called()


class TestBitbucketProviderCheckout:
    @respx.mock
    @patch("auditer.commands.data.checkout.clone_repo")
    @patch("auditer.commands.data.checkout.fetch_and_pull")
    def test_bitbucket_account_clones_repo(self, mock_pull, mock_clone, tmp_path):
        """Smoke test: checkout works end-to-end with a bitbucket-provider account."""
        config = load_config_from_dict(
            {
                "defaults": {"data_dir": str(tmp_path / "data")},
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
        )
        account = config.accounts[0]

        respx.get("https://api.bitbucket.org/2.0/user").mock(
            return_value=httpx.Response(200, json={"username": "myworkspace"})
        )
        respx.get("https://api.bitbucket.org/2.0/repositories/myworkspace").mock(
            return_value=httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "name": "repo1",
                            "is_private": False,
                            "owner": {"nickname": "myworkspace"},
                            "mainbranch": {"name": "main"},
                            "links": {
                                "html": {"href": "https://bitbucket.org/myworkspace/repo1"},
                                "clone": [
                                    {
                                        "name": "https",
                                        "href": "https://myworkspace@bitbucket.org/myworkspace/repo1.git",
                                    }
                                ],
                            },
                        }
                    ]
                },
            )
        )

        mock_clone.return_value = GitOpResult(success=True, message="Cloned")

        run_checkout(config, account)

        mock_clone.assert_called_once()
