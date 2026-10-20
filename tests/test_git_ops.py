import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from auditer.git_ops import (
    BranchUpdate,
    GitError,
    GitOpResult,
    LocalRepoInfo,
    _build_auth_url,
    _get_commit_details,
    _make_askpass_script,
    _parse_fetch_output,
    _run_git,
    _validate_name,
    clone_repo,
    fetch_and_pull,
    get_branch_commits,
    get_local_repo_metadata,
    is_repo_dirty,
    list_active_branches,
    scan_local_repos,
)


class TestValidateName:
    def test_valid_names(self):
        for name in ["my-repo", "repo_123", "CamelCase", "a.b.c", "test"]:
            _validate_name(name)  # Should not raise

    def test_path_traversal(self):
        with pytest.raises(GitError, match="Invalid repository name"):
            _validate_name("../etc/passwd")

    def test_invalid_chars(self):
        with pytest.raises(GitError, match="Invalid repository name"):
            _validate_name("repo/subdir")

    def test_spaces_allowed(self):
        # Some real Bitbucket repo names contain spaces (e.g. "CUE Twitter
        # Embed") — spaces aren't a path-traversal risk, only "/", "\", and
        # ".." are, so this is intentionally accepted.
        _validate_name("my repo")  # Should not raise

    def test_empty(self):
        with pytest.raises(GitError, match="Invalid repository name"):
            _validate_name("")


class TestIsRepoDirty:
    def _make_completed_process(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_clean_repo(self, mock_run_git, tmp_path):
        mock_run_git.return_value = self._make_completed_process(stdout="")
        assert is_repo_dirty(tmp_path) is False

    @patch("auditer.git_ops._run_git")
    def test_dirty_repo(self, mock_run_git, tmp_path):
        mock_run_git.return_value = self._make_completed_process(stdout=" M file.txt\n")
        assert is_repo_dirty(tmp_path) is True

    @patch("auditer.git_ops._run_git")
    def test_git_failure_returns_false(self, mock_run_git, tmp_path):
        mock_run_git.return_value = self._make_completed_process(returncode=128, stderr="fatal: error")
        assert is_repo_dirty(tmp_path) is False


class TestParseFetchOutput:
    def test_empty_output(self):
        new_branches, ref_updates, new_tags = _parse_fetch_output("")
        assert new_branches == []
        assert ref_updates == []
        assert new_tags == []

    def test_no_changes(self):
        output = "From https://github.com/user/repo\n"
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == []
        assert ref_updates == []
        assert new_tags == []

    def test_new_branch(self):
        output = (
            "From https://github.com/user/repo\n"
            " * [new branch]      main       -> origin/main\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == ["main"]
        assert ref_updates == []
        assert new_tags == []

    def test_multiple_new_branches(self):
        output = (
            "From https://github.com/user/repo\n"
            " * [new branch]      main       -> origin/main\n"
            " * [new branch]      develop    -> origin/develop\n"
            " * [new branch]      feat/login -> origin/feat/login\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == ["main", "develop", "feat/login"]

    def test_new_tag(self):
        output = (
            "From https://github.com/user/repo\n"
            " * [new tag]         v1.0.0     -> v1.0.0\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == []
        assert ref_updates == []
        assert new_tags == ["v1.0.0"]

    def test_multiple_new_tags(self):
        output = (
            "From https://github.com/user/repo\n"
            " * [new tag]         v1.0.0     -> v1.0.0\n"
            " * [new tag]         v1.1.0     -> v1.1.0\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_tags == ["v1.0.0", "v1.1.0"]

    def test_updated_branch(self):
        output = (
            "From https://github.com/user/repo\n"
            "   abc1234..def5678  main       -> origin/main\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == []
        assert len(ref_updates) == 1
        name, old_hash, new_hash, forced = ref_updates[0]
        assert name == "main"
        assert old_hash == "abc1234"
        assert new_hash == "def5678"
        assert forced is False

    def test_force_pushed_branch(self):
        output = (
            "From https://github.com/user/repo\n"
            " + abc1234...def5678 feat/work  -> origin/feat/work (forced update)\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert len(ref_updates) == 1
        name, old_hash, new_hash, forced = ref_updates[0]
        assert name == "feat/work"
        assert old_hash == "abc1234"
        assert new_hash == "def5678"
        assert forced is True

    def test_mixed_output(self):
        output = (
            "From https://github.com/user/repo\n"
            " * [new branch]      develop    -> origin/develop\n"
            "   abc1234..def5678  main       -> origin/main\n"
            " + 1111111...2222222 hotfix     -> origin/hotfix (forced update)\n"
            " * [new tag]         v2.0.0     -> v2.0.0\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == ["develop"]
        assert len(ref_updates) == 2
        assert ref_updates[0] == ("main", "abc1234", "def5678", False)
        assert ref_updates[1] == ("hotfix", "1111111", "2222222", True)
        assert new_tags == ["v2.0.0"]

    def test_fetching_line_ignored(self):
        output = (
            "Fetching origin\n"
            "From https://github.com/user/repo\n"
            " * [new branch]      main       -> origin/main\n"
        )
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == ["main"]

    def test_line_without_arrow_ignored(self):
        output = "some random line without arrow\n"
        new_branches, ref_updates, new_tags = _parse_fetch_output(output)
        assert new_branches == []
        assert ref_updates == []
        assert new_tags == []


class TestGetCommitDetails:
    def _make_completed_process(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_normal_range(self, mock_run_git):
        mock_run_git.return_value = self._make_completed_process(
            stdout="abc1234\x1eAlice\x1eFix bug\nabc5678\x1eBob\x1eAdd feature\n"
        )
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "old123", "new456"
        )
        assert count == 2
        assert authors == ["Alice", "Bob"]
        assert len(commits) == 2
        assert commits[0] == ("abc1234", "Fix bug", "Alice")
        assert commits[1] == ("abc5678", "Add feature", "Bob")
        assert removed == 0

    @patch("auditer.git_ops._run_git")
    def test_empty_range(self, mock_run_git):
        mock_run_git.return_value = self._make_completed_process(stdout="")
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "same123", "same123"
        )
        assert count == 0
        assert authors == []
        assert commits == []
        assert removed == 0

    @patch("auditer.git_ops._run_git")
    def test_git_log_failure(self, mock_run_git):
        mock_run_git.return_value = self._make_completed_process(
            returncode=128, stderr="fatal: bad revision"
        )
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "bad123", "bad456"
        )
        assert count == 0
        assert authors == []
        assert commits == []
        assert removed == 0

    @patch("auditer.git_ops._run_git")
    def test_force_push_with_removed_commits(self, mock_run_git):
        # First call: git log new..old (added commits) — 1 new commit
        # Second call: git rev-list --count old..new (removed commits) — 3 removed
        mock_run_git.side_effect = [
            self._make_completed_process(
                stdout="abc1234\x1eAlice\x1eRewrite history\n"
            ),
            self._make_completed_process(stdout="3\n"),
        ]
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "old123", "new456", forced=True
        )
        assert count == 1
        assert authors == ["Alice"]
        assert commits == [("abc1234", "Rewrite history", "Alice")]
        assert removed == 3

    @patch("auditer.git_ops._run_git")
    def test_force_push_no_added_commits_gets_tip_author(self, mock_run_git):
        # First call: git log (no added commits)
        # Second call: git rev-list --count (5 removed)
        # Third call: git log -1 for tip author
        mock_run_git.side_effect = [
            self._make_completed_process(stdout=""),
            self._make_completed_process(stdout="5\n"),
            self._make_completed_process(stdout="Charlie\n"),
        ]
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "old123", "new456", forced=True
        )
        assert count == 0
        assert authors == ["Charlie"]
        assert commits == []
        assert removed == 5

    @patch("auditer.git_ops._run_git")
    def test_single_commit(self, mock_run_git):
        mock_run_git.return_value = self._make_completed_process(
            stdout="abc1234\x1eAlice\x1eSingle commit\n"
        )
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "old123", "new456"
        )
        assert count == 1
        assert authors == ["Alice"]
        assert commits == [("abc1234", "Single commit", "Alice")]
        assert removed == 0

    @patch("auditer.git_ops._run_git")
    def test_duplicate_author_deduplicated(self, mock_run_git):
        mock_run_git.return_value = self._make_completed_process(
            stdout="abc1234\x1eAlice\x1eFirst\nabc5678\x1eAlice\x1eSecond\n"
        )
        count, authors, commits, removed = _get_commit_details(
            "/tmp/repo", "old123", "new456"
        )
        assert count == 2
        assert authors == ["Alice"]  # Deduplicated and sorted


class TestGitOpResult:
    def test_defaults(self):
        result = GitOpResult(success=True, message="OK")
        assert result.new_branches == []
        assert result.branch_updates == []
        assert result.new_tags == []

    def test_with_data(self):
        bu = BranchUpdate(name="main", commit_count=3)
        result = GitOpResult(
            success=True,
            message="Updated",
            new_branches=["develop"],
            branch_updates=[bu],
            new_tags=["v1.0"],
        )
        assert result.new_branches == ["develop"]
        assert len(result.branch_updates) == 1
        assert result.branch_updates[0].commit_count == 3
        assert result.new_tags == ["v1.0"]


class TestBranchUpdate:
    def test_defaults(self):
        bu = BranchUpdate(name="main")
        assert bu.old_hash == ""
        assert bu.new_hash == ""
        assert bu.commit_count == 0
        assert bu.authors == []
        assert bu.commits == []
        assert bu.forced is False
        assert bu.removed_count == 0

    def test_force_push(self):
        bu = BranchUpdate(
            name="feat/test",
            old_hash="aaa",
            new_hash="bbb",
            commit_count=1,
            authors=["Alice"],
            forced=True,
            removed_count=5,
        )
        assert bu.forced is True
        assert bu.removed_count == 5


class TestGetLocalRepoMetadata:
    def _make_completed_process(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_not_a_git_repo(self, tmp_path):
        result = get_local_repo_metadata(tmp_path, is_private=False)
        assert result is None

    @patch("auditer.git_ops._run_git")
    def test_full_metadata_extraction(self, mock_run_git, tmp_path):
        # Create .git dir and some files
        (tmp_path / ".git").mkdir()
        (tmp_path / "README.md").touch()
        (tmp_path / "LICENSE").touch()
        (tmp_path / "src").mkdir()

        mock_run_git.side_effect = [
            # remote.origin.url
            self._make_completed_process(stdout="https://github.com/alice/my-repo.git\n"),
            # rev-parse --abbrev-ref HEAD
            self._make_completed_process(stdout="main\n"),
            # log -1 --format=%aI
            self._make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            # count-objects -v
            self._make_completed_process(stdout="count: 10\nsize: 50\nsize-pack: 1234\n"),
        ]

        result = get_local_repo_metadata(tmp_path, is_private=False)
        assert result is not None
        assert result.name == tmp_path.name
        assert result.owner == "alice"
        assert result.clone_url == "https://github.com/alice/my-repo.git"
        assert result.default_branch == "main"
        assert result.pushed_at == "2025-06-01T12:00:00+00:00"
        assert result.size_kb == 1234
        assert result.has_readme is True
        assert result.has_license_file is True
        assert result.private is False

    @patch("auditer.git_ops._run_git")
    def test_no_readme(self, mock_run_git, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()

        mock_run_git.side_effect = [
            self._make_completed_process(stdout="https://github.com/bob/bare-repo.git\n"),
            self._make_completed_process(stdout="main\n"),
            self._make_completed_process(stdout="2025-01-01T00:00:00+00:00\n"),
            self._make_completed_process(stdout="size-pack: 0\n"),
        ]

        result = get_local_repo_metadata(tmp_path, is_private=True)
        assert result is not None
        assert result.has_readme is False
        assert result.has_license_file is False
        assert result.private is True

    @patch("auditer.git_ops._run_git")
    def test_case_insensitive_readme(self, mock_run_git, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "Readme.rst").touch()

        mock_run_git.side_effect = [
            self._make_completed_process(stdout="https://github.com/x/y.git\n"),
            self._make_completed_process(stdout="master\n"),
            self._make_completed_process(stdout=""),
            self._make_completed_process(stdout="size-pack: 0\n"),
        ]

        result = get_local_repo_metadata(tmp_path, is_private=False)
        assert result is not None
        assert result.has_readme is True

    @patch("auditer.git_ops._run_git")
    def test_repo_slug_parsed_independently_from_name(self, mock_run_git, tmp_path):
        """Bitbucket's display name (used for the checkout directory / the
        `name` field) can differ from its URL-safe slug (e.g. a repo named
        "Bundler JS" has slug "bundler-js") — API calls need the slug, or
        Bitbucket 404s. repo_slug must come from the clone URL, not the
        directory name."""
        (tmp_path / ".git").mkdir()

        mock_run_git.side_effect = [
            self._make_completed_process(
                stdout="https://x@bitbucket.org/myworkspace/bundler-js.git\n"
            ),
            self._make_completed_process(stdout="main\n"),
            self._make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            self._make_completed_process(stdout="size-pack: 0\n"),
        ]

        result = get_local_repo_metadata(tmp_path, is_private=False)
        assert result is not None
        assert result.owner == "myworkspace"
        assert result.repo_slug == "bundler-js"
        # name (the directory name) is independent of the parsed slug
        assert result.name == tmp_path.name

    @patch("auditer.git_ops._run_git")
    def test_repo_slug_falls_back_to_name_when_clone_url_missing(self, mock_run_git, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run_git.side_effect = [
            self._make_completed_process(stdout=""),  # no remote configured
            self._make_completed_process(stdout="main\n"),
            self._make_completed_process(stdout="2025-06-01T12:00:00+00:00\n"),
            self._make_completed_process(stdout="size-pack: 0\n"),
        ]
        result = get_local_repo_metadata(tmp_path, is_private=False)
        assert result is not None
        assert result.repo_slug == result.name


class TestScanLocalRepos:
    @patch("auditer.git_ops.get_local_repo_metadata")
    def test_scans_public_and_private(self, mock_get_meta, tmp_path):
        (tmp_path / "public" / "repo-a").mkdir(parents=True)
        (tmp_path / "private" / "repo-b").mkdir(parents=True)

        def fake_meta(repo_dir, is_private):
            return LocalRepoInfo(
                name=repo_dir.name,
                path=repo_dir,
                private=is_private,
                owner="user",
                clone_url=f"https://github.com/user/{repo_dir.name}.git",
                has_readme=True,
                has_license_file=False,
                default_branch="main",
                pushed_at="2025-01-01T00:00:00+00:00",
                size_kb=100,
            )

        mock_get_meta.side_effect = fake_meta

        repos = scan_local_repos(tmp_path)
        assert len(repos) == 2
        names = {r.name for r in repos}
        assert names == {"repo-a", "repo-b"}
        # repo-a is public, repo-b is private
        by_name = {r.name: r for r in repos}
        assert by_name["repo-a"].private is False
        assert by_name["repo-b"].private is True

    def test_empty_dir(self, tmp_path):
        repos = scan_local_repos(tmp_path)
        assert repos == []

    def test_nonexistent_dir(self, tmp_path):
        repos = scan_local_repos(tmp_path / "does-not-exist")
        assert repos == []

    @patch("auditer.git_ops.get_local_repo_metadata")
    def test_skips_non_git_dirs(self, mock_get_meta, tmp_path):
        (tmp_path / "public" / "not-a-repo").mkdir(parents=True)
        mock_get_meta.return_value = None

        repos = scan_local_repos(tmp_path)
        assert repos == []

    def test_skips_files_in_data_dir(self, tmp_path):
        """Files (not directories) in public/ should be skipped."""
        pub = tmp_path / "public"
        pub.mkdir()
        (pub / "stray-file.txt").write_text("not a repo")
        repos = scan_local_repos(tmp_path)
        assert repos == []


class TestBuildAuthUrl:
    def test_github_bare_url(self):
        # GitHub's clone_url never embeds a username — default "oauth2" case.
        result = _build_auth_url("https://github.com/owner/repo.git", "oauth2")
        assert result == "https://oauth2@github.com/owner/repo.git"

    def test_bitbucket_url_with_preexisting_userinfo(self):
        # Bitbucket's clone_url ALREADY embeds a username (the caller's own
        # nickname) — must be replaced, not concatenated onto.
        result = _build_auth_url(
            "https://tomgould@bitbucket.org/myworkspace/my-repo.git",
            "jane.doe@example.com",
        )
        assert result == (
            "https://jane.doe%40example.com@bitbucket.org/myworkspace/my-repo.git"
        )

    def test_email_at_symbol_is_percent_encoded(self):
        result = _build_auth_url("https://bitbucket.org/w/r.git", "user@example.com")
        assert "user%40example.com@bitbucket.org" in result
        assert "user@example.com@" not in result

    def test_path_and_query_preserved(self):
        result = _build_auth_url(
            "https://bitbucket.org/w/r.git?foo=bar", "oauth2",
        )
        assert result.endswith("/w/r.git?foo=bar")


class TestValidateNameDoubleDot:
    def test_double_dot_in_name(self):
        """Name like 'a..b' passes regex but is caught by '..' check."""
        with pytest.raises(GitError, match="Path traversal"):
            _validate_name("a..b")


class TestMakeAskpassScript:
    def test_creates_executable_script(self):
        script_path = _make_askpass_script("mytoken123")
        try:
            assert os.path.isfile(script_path)
            assert os.access(script_path, os.X_OK)
            content = open(script_path).read()
            assert "echo mytoken123" in content
            assert content.startswith("#!/bin/sh\n")
        finally:
            os.unlink(script_path)

    def test_created_with_restrictive_permissions(self):
        """File should be created with 0o700 from the start (no chmod race)."""
        script_path = _make_askpass_script("ghp_test")
        try:
            mode = os.stat(script_path).st_mode & 0o777
            assert mode == 0o700
        finally:
            os.unlink(script_path)

    def test_not_world_readable(self):
        """File must not be readable by group or others."""
        script_path = _make_askpass_script("ghp_secret")
        try:
            mode = os.stat(script_path).st_mode & 0o077
            assert mode == 0, f"Group/other permissions should be 0, got {oct(mode)}"
        finally:
            os.unlink(script_path)


class TestRunGit:
    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("subprocess.run")
    def test_basic_command(self, mock_run):
        mock_run.return_value = self._cp(stdout="ok\n")
        result = _run_git(["status"])
        assert result.stdout == "ok\n"
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["git", "status"]

    @patch("subprocess.run")
    def test_with_token_sets_askpass(self, mock_run):
        mock_run.return_value = self._cp()
        result = _run_git(["fetch"], token="ghp_test123")
        assert result.returncode == 0
        # Verify GIT_ASKPASS was set in the env
        call_kwargs = mock_run.call_args[1]
        assert "GIT_ASKPASS" in call_kwargs["env"]
        assert call_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

    @patch("subprocess.run")
    def test_timeout_returns_failure(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git"], timeout=30)
        result = _run_git(["fetch"], timeout=30)
        assert result.returncode == -1
        assert "timed out" in result.stderr

    @patch("subprocess.run")
    def test_askpass_cleanup_on_oserror_logs_warning(self, mock_run):
        """os.unlink failure during cleanup should log a warning, not crash."""
        mock_run.return_value = self._cp()
        import logging
        with patch("auditer.git_ops.os.unlink", side_effect=OSError("busy")):
            with patch("auditer.git_ops.logger") as mock_logger:
                result = _run_git(["fetch"], token="ghp_test")
                assert result.returncode == 0
                mock_logger.warning.assert_called_once()
                assert "askpass" in mock_logger.warning.call_args[0][0].lower()


class TestCloneRepo:
    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_clone_success(self, mock_run, tmp_path):
        target = tmp_path / "repos" / "my-repo"
        mock_run.side_effect = [
            self._cp(),  # clone
            self._cp(),  # config remote.origin.url
        ]
        result = clone_repo(
            "https://github.com/user/my-repo.git",
            target,
            token="ghp_test",
            name="my-repo",
        )
        assert result.success is True
        assert result.message == "Cloned"
        assert target.parent.exists()

    @patch("auditer.git_ops._run_git")
    def test_clone_failure(self, mock_run, tmp_path):
        target = tmp_path / "repos" / "bad-repo"
        mock_run.return_value = self._cp(returncode=128, stderr="fatal: not found")
        result = clone_repo("https://github.com/user/bad.git", target)
        assert result.success is False
        assert "Clone failed" in result.message

    @patch("auditer.git_ops._run_git")
    def test_clone_with_token_transforms_url(self, mock_run, tmp_path):
        target = tmp_path / "repos" / "repo"
        mock_run.side_effect = [self._cp(), self._cp()]
        clone_repo("https://github.com/user/repo.git", target, token="ghp_t")
        # First call should have oauth2@ in the URL
        clone_args = mock_run.call_args_list[0][0][0]
        assert "oauth2@" in clone_args[1]

    @patch("auditer.git_ops._run_git")
    def test_clone_without_token(self, mock_run, tmp_path):
        target = tmp_path / "repos" / "repo"
        mock_run.side_effect = [self._cp(), self._cp()]
        clone_repo("https://github.com/user/repo.git", target, token=None)
        clone_args = mock_run.call_args_list[0][0][0]
        assert "oauth2@" not in clone_args[1]

    def test_clone_validates_name(self, tmp_path):
        target = tmp_path / "repos" / "bad"
        with pytest.raises(GitError):
            clone_repo("https://github.com/u/r.git", target, name="bad/name")


class TestFetchAndPull:
    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_not_a_git_repo(self, tmp_path):
        result = fetch_and_pull(tmp_path, "https://github.com/u/r.git", "main")
        assert result.success is False
        assert "Not a git repository" in result.message

    @patch("auditer.git_ops._run_git")
    def test_fetch_success_no_changes(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            self._cp(),  # config remote.origin.url (token)
            self._cp(stderr=""),  # fetch
            self._cp(),  # rev-parse --verify origin/main
            self._cp(),  # reset --hard
            self._cp(),  # config remote.origin.url (cleanup)
        ]
        result = fetch_and_pull(tmp_path, "https://github.com/u/r.git", "main", token="t")
        assert result.success is True
        assert result.new_branches == []
        assert result.branch_updates == []
        assert result.new_tags == []

    @patch("auditer.git_ops._run_git")
    def test_fetch_failure(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            self._cp(),  # config (token)
            self._cp(returncode=1, stderr="error: could not connect"),  # fetch
            self._cp(),  # config (cleanup)
        ]
        result = fetch_and_pull(tmp_path, "https://github.com/u/r.git", "main", token="t")
        assert result.success is False
        assert "Fetch failed" in result.message

    @patch("auditer.git_ops._get_commit_details")
    @patch("auditer.git_ops._run_git")
    def test_fetch_with_ref_updates(self, mock_run, mock_details, tmp_path):
        (tmp_path / ".git").mkdir()
        fetch_stderr = (
            "From https://github.com/u/r\n"
            "   aaa1111..bbb2222  main -> origin/main\n"
        )
        mock_run.side_effect = [
            self._cp(stderr=fetch_stderr),  # fetch
            self._cp(),  # rev-parse --verify
            self._cp(),  # reset --hard
            self._cp(),  # config cleanup
        ]
        mock_details.return_value = (2, ["Alice"], [("c1", "Fix", "Alice"), ("c2", "Add", "Alice")], 0)
        result = fetch_and_pull(tmp_path, "https://github.com/u/r.git", "main")
        assert result.success is True
        assert len(result.branch_updates) == 1
        assert result.branch_updates[0].name == "main"
        assert result.branch_updates[0].commit_count == 2

    @patch("auditer.git_ops._run_git")
    def test_empty_repo_no_reset(self, mock_run, tmp_path):
        """When rev-parse --verify fails (empty repo), skip reset but still succeed."""
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            self._cp(stderr=""),  # fetch
            self._cp(returncode=1),  # rev-parse --verify fails
            self._cp(),  # config cleanup
        ]
        result = fetch_and_pull(tmp_path, "https://github.com/u/r.git", "main")
        assert result.success is True

    @patch("auditer.git_ops._run_git")
    def test_fetch_branches_creates_tracking(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            self._cp(stderr=""),  # fetch
            self._cp(),  # rev-parse --verify
            self._cp(),  # reset --hard
            self._cp(stdout="main\ndevelop\nHEAD\n"),  # branch -r
            self._cp(stdout="main\n"),  # rev-parse --abbrev-ref HEAD
            self._cp(),  # branch -f develop origin/develop
            self._cp(),  # config cleanup
        ]
        result = fetch_and_pull(
            tmp_path, "https://github.com/u/r.git", "main", fetch_branches=True
        )
        assert result.success is True
        # Verify branch -f was called for 'develop' (skipping HEAD and main)
        branch_f_calls = [
            c for c in mock_run.call_args_list
            if c[0][0][:2] == ["branch", "-f"]
        ]
        assert len(branch_f_calls) == 1
        assert "develop" in branch_f_calls[0][0][0]


class TestGetLocalRepoMetadataMalformedSize:
    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_malformed_size_pack(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "README.md").touch()
        mock_run.side_effect = [
            self._cp(stdout="https://github.com/u/r.git\n"),
            self._cp(stdout="main\n"),
            self._cp(stdout="2025-01-01T00:00:00+00:00\n"),
            self._cp(stdout="size-pack: abc\n"),  # non-numeric
        ]
        result = get_local_repo_metadata(tmp_path, is_private=False)
        assert result is not None
        assert result.size_kb == 0


class TestGetCommitLogDefaultBranch:
    """Verify get_commit_log uses default_branch instead of --all when provided."""

    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_with_default_branch(self, mock_run):
        from auditer.git_ops import get_commit_log

        mock_run.return_value = self._cp(stdout="")
        get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30", default_branch="main")
        args = mock_run.call_args[0][0]
        assert "origin/main" in args
        assert "--all" not in args

    @patch("auditer.git_ops._run_git")
    def test_without_default_branch(self, mock_run):
        from auditer.git_ops import get_commit_log

        mock_run.return_value = self._cp(stdout="")
        get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30", default_branch=None)
        args = mock_run.call_args[0][0]
        assert "--all" in args
        assert not any("origin/" in a for a in args if isinstance(a, str) and a.startswith("origin/"))

    @patch("auditer.git_ops._run_git")
    def test_backward_compat_no_param(self, mock_run):
        """Calling without default_branch uses --all (backward compat)."""
        from auditer.git_ops import get_commit_log

        mock_run.return_value = self._cp(stdout="")
        get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        args = mock_run.call_args[0][0]
        assert "--all" in args


class TestListActiveBranches:
    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_returns_active_branches(self, mock_run):
        mock_run.side_effect = [
            # for-each-ref: list branches
            self._cp(stdout="main\nfeature/xyz\nhotfix\nHEAD\n"),
            # rev-list --count for feature/xyz: 5 unmerged commits
            self._cp(stdout="5\n"),
            # rev-list --count for hotfix: 0 unmerged commits
            self._cp(stdout="0\n"),
        ]
        result = list_active_branches(
            Path("/repo"), "main", "2025-06-01", "2025-06-30"
        )
        assert len(result) == 1
        assert result[0]["name"] == "feature/xyz"
        assert result[0]["commit_count"] == 5

    @patch("auditer.git_ops._run_git")
    def test_skips_default_branch_and_head(self, mock_run):
        mock_run.side_effect = [
            self._cp(stdout="main\nHEAD\n"),
        ]
        result = list_active_branches(
            Path("/repo"), "main", "2025-06-01", "2025-06-30"
        )
        assert result == []

    @patch("auditer.git_ops._run_git")
    def test_empty_repo(self, mock_run):
        mock_run.return_value = self._cp(stdout="")
        result = list_active_branches(
            Path("/repo"), "main", "2025-06-01", "2025-06-30"
        )
        assert result == []

    @patch("auditer.git_ops._run_git")
    def test_git_failure(self, mock_run):
        mock_run.return_value = self._cp(returncode=128, stderr="fatal")
        result = list_active_branches(
            Path("/repo"), "main", "2025-06-01", "2025-06-30"
        )
        assert result == []


class TestGetBranchCommits:
    def _cp(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    @patch("auditer.git_ops._run_git")
    def test_returns_commits(self, mock_run):
        mock_run.return_value = self._cp(
            stdout=(
                "COMMIT\x1eabc1234\x1eAlice\x1ealice@example.com\x1e2025-06-01T10:00:00+00:00\x1eFeature work\n"
                "\n"
                " 1 file changed, 20 insertions(+), 5 deletions(-)\n"
            )
        )
        result = get_branch_commits(
            Path("/repo"), "feature/xyz", "main", "2025-06-01", "2025-06-30"
        )
        assert len(result) == 1
        assert result[0]["sha"] == "abc1234"
        assert result[0]["additions"] == 20
        assert result[0]["deletions"] == 5

    @patch("auditer.git_ops._run_git")
    def test_uses_not_default_branch(self, mock_run):
        mock_run.return_value = self._cp(stdout="")
        get_branch_commits(
            Path("/repo"), "feature/xyz", "main", "2025-06-01", "2025-06-30"
        )
        args = mock_run.call_args[0][0]
        assert "origin/feature/xyz" in args
        assert "--not" in args
        assert "origin/main" in args

    @patch("auditer.git_ops._run_git")
    def test_uses_committer_date(self, mock_run):
        """git selects by committer date for --since/--until, so the reported
        date must be %cI too — otherwise rebased commits land outside the range
        and the daily/weekly breakdowns no longer sum to the total."""
        mock_run.return_value = self._cp(stdout="")
        get_branch_commits(
            Path("/repo"), "feature/xyz", "main", "2025-06-01", "2025-06-30"
        )
        fmt = next(a for a in mock_run.call_args[0][0] if a.startswith("--format="))
        assert "%cI" in fmt and "%aI" not in fmt
        assert "%an" in fmt and "%ae" in fmt

    @patch("auditer.git_ops._run_git")
    def test_get_commit_log_uses_committer_date(self, mock_run):
        from auditer.git_ops import get_commit_log

        mock_run.return_value = self._cp(stdout="")
        get_commit_log(Path("/repo"), "2025-06-01", "2025-06-30")
        fmt = next(a for a in mock_run.call_args[0][0] if a.startswith("--format="))
        assert "%cI" in fmt and "%aI" not in fmt

    @patch("auditer.git_ops._run_git")
    def test_empty_branch(self, mock_run):
        mock_run.return_value = self._cp(stdout="")
        result = get_branch_commits(
            Path("/repo"), "feature/xyz", "main", "2025-06-01", "2025-06-30"
        )
        assert result == []

    @patch("auditer.git_ops._run_git")
    def test_excludes_pathspecs(self, mock_run):
        """Commits come from an unrestricted pass; line counts from a second
        pass with every exclusion (and --full-history, so simplification
        can't hide a commit from it)."""
        from auditer.git_ops import EXCLUDED_PATHSPECS
        mock_run.return_value = self._cp(
            stdout="COMMIT\x1eabc\x1eA\x1ea@x\x1e2025-06-01T10:00:00+00:00\x1es\n"
        )
        get_branch_commits(
            Path("/repo"), "feature/xyz", "main", "2025-06-01", "2025-06-30"
        )
        first, second = (c[0][0] for c in mock_run.call_args_list)
        assert not any(a.startswith(":(exclude)") for a in first)
        assert "--full-history" in second
        for pattern in EXCLUDED_PATHSPECS:
            assert f":(exclude){pattern}" in second


class TestExcludedPathspecs:
    def test_binary_extensions_excluded(self):
        from auditer.git_ops import EXCLUDED_PATHSPECS
        for ext in ("jpg", "jpeg", "png", "pdf", "woff2", "zip", "mp4", "exe", "bundle", "snap"):
            assert f"*.{ext}" in EXCLUDED_PATHSPECS
            assert f"*.{ext.upper()}" in EXCLUDED_PATHSPECS

    def test_library_dirs_excluded_at_any_depth(self):
        """Git's default pathspec anchors directory patterns at the repo root,
        so `vendor/**` misses `drupal/vendor/`; both forms must be present."""
        from auditer.git_ops import EXCLUDED_PATHSPECS
        for d in ("vendor", "node_modules", "bower_components", "third-party", "dist", "libraries",
                  "mocks", "__mocks__", "fixtures", "__snapshots__"):
            assert f"{d}/**" in EXCLUDED_PATHSPECS
            assert f"*/{d}/**" in EXCLUDED_PATHSPECS
        # WordPress core likewise
        assert "*/wp-content/plugins/**" in EXCLUDED_PATHSPECS

    def test_source_lib_dirs_not_excluded(self):
        """`lib/` and `src/lib/` are ordinary source in most repos here."""
        from auditer.git_ops import EXCLUDED_PATHSPECS
        assert not any(p.startswith("lib/") or p.startswith("*/lib/") for p in EXCLUDED_PATHSPECS)


class TestGetPatchIds:
    def _cp(self, returncode=0, stdout=b""):
        return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=b"")

    @patch("auditer.git_ops.subprocess.run")
    def test_maps_sha_to_patch_id(self, mock_sub_run):
        from auditer.git_ops import get_patch_ids

        # The log stream carries raw bytes (a NUL-free JPEG git diffed as
        # text); it must be piped through untouched, never decoded.
        log_bytes = b"commit aaa\ndiff --git a/x.jpg b/x.jpg\n+\xff\xd8\xff\xe0\n"
        mock_sub_run.side_effect = [
            self._cp(stdout=log_bytes),
            self._cp(stdout=b"p111 aaa\np222 bbb\n"),
        ]
        result = get_patch_ids(Path("/repo"), ["origin/feat", "--not", "origin/main"], "2025-06-01", "2025-06-30")
        assert result == {"aaa": "p111", "bbb": "p222"}
        log_cmd = mock_sub_run.call_args_list[0][0][0]
        assert log_cmd[:6] == ["git", "log", "-p", "--no-merges", "--no-color", "--format=commit %H"]
        # Whole days in Europe/London (BST in June), not git's bare-date
        # "current time of day" reading
        assert "--since=2025-06-01T00:00:00+01:00" in log_cmd
        assert "--until=2025-06-30T23:59:59+01:00" in log_cmd
        assert log_cmd[-3:] == ["origin/feat", "--not", "origin/main"]
        assert "text" not in mock_sub_run.call_args_list[0][1]
        pid_call = mock_sub_run.call_args_list[1]
        assert pid_call[0][0] == ["git", "patch-id", "--stable"]
        assert pid_call[1]["input"] == log_bytes

    @patch("auditer.git_ops.subprocess.run")
    def test_empty_on_git_failure(self, mock_sub_run):
        from auditer.git_ops import get_patch_ids

        mock_sub_run.return_value = self._cp(returncode=128)
        assert get_patch_ids(Path("/repo"), ["origin/main"], "2025-06-01", "2025-06-30") == {}

    @patch("auditer.git_ops.subprocess.run")
    def test_empty_on_unexpected_error(self, mock_sub_run):
        """A crash here must degrade to SHA matching, not abort the scan."""
        from auditer.git_ops import get_patch_ids

        mock_sub_run.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        assert get_patch_ids(Path("/repo"), ["origin/main"], "2025-06-01", "2025-06-30") == {}

    def test_empty_when_repo_missing(self):
        from auditer.git_ops import get_patch_ids

        assert get_patch_ids(Path("/nonexistent/repo"), ["origin/main"], "2025-06-01", "2025-06-30") == {}


class TestExcludedPathspecsAcceptedByGit:
    def test_every_pattern_is_valid_for_real_git(self, tmp_path):
        """git rejects some spellings outright (`:!__mocks__/**` is "Unimplemented
        pathspec magic '_'"), and a rejected pathspec makes every git log call
        fail — which once produced a report of zero commits for 398 repos.
        Run the real command with the full list against a throwaway repo."""
        from auditer.git_ops import exclusion_pathspecs, get_commit_log

        env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@x"}
        run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, env=env, check=True, capture_output=True)
        run("init", "-q", "-b", "main")
        (tmp_path / "app.py").write_text("print(1)\n")
        (tmp_path / "__mocks__").mkdir()
        (tmp_path / "__mocks__" / "api.json").write_text("{}\n" * 50)
        run("add", ".")
        run("commit", "-q", "-m", "init")
        # get_commit_log reads origin/<branch>; point origin at the repo itself
        run("remote", "add", "origin", str(tmp_path))
        run("fetch", "-q", "origin")

        from datetime import date, timedelta
        today = date.today()
        commits = get_commit_log(
            tmp_path, (today - timedelta(days=1)).isoformat(), today.isoformat(),
            default_branch="main",
        )
        assert len(commits) == 1
        # The mocked JSON (50 lines) is excluded; only app.py counts
        assert commits[0]["additions"] == 1

        # And the raw command itself exits 0 with every pattern attached
        r = subprocess.run(
            ["git", "log", "--format=%H", "--", ".", *exclusion_pathspecs()],
            cwd=tmp_path, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr


class _GitRepo:
    """A throwaway repo whose commits carry chosen committer dates."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.env = {
            **os.environ, "GIT_AUTHOR_NAME": "Tom", "GIT_AUTHOR_EMAIL": "tom@x",
            "GIT_COMMITTER_NAME": "Tom", "GIT_COMMITTER_EMAIL": "tom@x",
        }
        self.git("init", "-q", "-b", "main")

    def git(self, *args, date: str | None = None) -> str:
        env = dict(self.env)
        if date:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
        r = subprocess.run(
            ["git", *args], cwd=self.path, env=env, check=True, capture_output=True, text=True,
        )
        return r.stdout

    def commit(self, files: dict[str, str], message: str, date: str) -> str:
        for name, content in files.items():
            p = self.path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            self.git("add", name)
        self.git("commit", "-q", "--allow-empty-message", "-m", message, date=date)
        return self.git("rev-parse", "HEAD").strip()

    def publish(self) -> None:
        """Point origin at the repo itself so origin/* refs exist."""
        remotes = self.git("remote").split()
        if "origin" not in remotes:
            self.git("remote", "add", "origin", str(self.path))
        self.git("fetch", "-q", "origin")


class TestWholeDayWindowRealGit:
    """--since/--until are whole days in Europe/London, whatever the time of
    day the report runs. A bare `--since=2026-10-10` made git use the current
    time of day, dropping 26 commits from 10 Oct in one real report."""

    def test_window_bounds(self, tmp_path):
        from auditer.git_ops import get_commit_log

        repo = _GitRepo(tmp_path)
        repo.commit({"a.py": "1\n"}, "before", "2026-10-09T23:59:59+01:00")
        repo.commit({"b.py": "1\n"}, "first minute", "2026-10-10T00:00:30+01:00")
        repo.commit({"c.py": "1\n"}, "utc evening", "2026-10-09T23:30:00+00:00")  # 00:30 BST
        repo.commit({"d.py": "1\n"}, "last second", "2026-10-16T23:59:59+01:00")
        repo.commit({"e.py": "1\n"}, "after", "2026-10-17T00:00:00+01:00")
        repo.publish()

        commits = get_commit_log(tmp_path, "2026-10-10", "2026-10-16", default_branch="main")
        assert sorted(c["subject"] for c in commits) == ["first minute", "last second", "utc evening"]
        # Dates are London dates, not the committer's own offset
        assert {c["subject"]: c["date"] for c in commits}["utc evening"] == "2026-10-10"

    def test_empty_subject_is_kept(self, tmp_path):
        """Python's strip() treats \\x1e as whitespace: an empty subject used
        to lose its separator and the commit was dropped."""
        from auditer.git_ops import get_commit_log

        repo = _GitRepo(tmp_path)
        repo.commit({"a.py": "1\n2\n"}, "", "2026-10-12T10:00:00+01:00")
        repo.publish()
        commits = get_commit_log(tmp_path, "2026-10-10", "2026-10-16")
        assert len(commits) == 1
        assert commits[0]["subject"] == ""
        assert commits[0]["additions"] == 2


class TestTagsRealGit:
    def test_last_annotated_tag_is_kept(self, tmp_path):
        """An annotated tag has empty author fields; as the last line of the
        output, strip() ate its separators (example-api v.2.17.02)."""
        from auditer.git_ops import get_tags_in_range

        repo = _GitRepo(tmp_path)
        repo.commit({"a.py": "1\n"}, "one", "2026-10-12T10:00:00+01:00")
        repo.git("tag", "-a", "v1", "-m", "v1", date="2026-10-12T11:00:00+01:00")
        repo.git("tag", "-a", "v2", "-m", "v2", date="2026-10-16T12:27:13+01:00")
        tags = get_tags_in_range(tmp_path, "2026-10-10", "2026-10-16")
        assert [t["name"] for t in tags] == ["v1", "v2"]
        assert tags[-1]["tagger_name"] == "Tom"

    @patch("auditer.git_ops._run_git")
    def test_output_ending_in_empty_fields(self, mock_run):
        from auditer.git_ops import get_tags_in_range

        mock_run.return_value = subprocess.CompletedProcess(
            args=["git"], returncode=0, stderr="",
            stdout="v.2.17.02\x1e2026-10-16T12:27:13+01:00\x1etomgould\x1e<tom@x>\x1e\x1e\n",
        )
        tags = get_tags_in_range(Path("/repo"), "2026-10-10", "2026-10-16")
        assert [t["name"] for t in tags] == ["v.2.17.02"]


class TestLineExclusionsRealGit:
    def test_data_files_excluded_and_reported(self, tmp_path):
        from auditer.git_ops import get_commit_log

        repo = _GitRepo(tmp_path)
        repo.commit(
            {"app.py": "x\n", "import.csv": "a,b\n" * 100, "rows.tsv": "a\tb\n" * 10},
            "import", "2026-10-12T10:00:00+01:00",
        )
        repo.publish()
        [c] = get_commit_log(tmp_path, "2026-10-10", "2026-10-16")
        assert c["additions"] == 1
        assert c["excluded_additions"] == 110

    def test_commit_touching_only_excluded_files_still_counts(self, tmp_path):
        """A single pathspec-limited log dropped such commits entirely, so
        adding an exclusion changed commit counts, not just line counts."""
        from auditer.git_ops import get_commit_log

        repo = _GitRepo(tmp_path)
        repo.commit({"data.csv": "1\n2\n3\n"}, "data only", "2026-10-12T10:00:00+01:00")
        repo.publish()
        [c] = get_commit_log(tmp_path, "2026-10-10", "2026-10-16")
        assert (c["additions"], c["excluded_additions"]) == (0, 3)

    def test_json_excluded_only_in_data_dirs(self, tmp_path):
        from auditer.git_ops import get_commit_log

        repo = _GitRepo(tmp_path)
        repo.commit(
            {
                "package.json": "{\n}\n",                      # config: counted
                "tickets/x/attachments/a.json": "1\n" * 20,    # data: excluded
                "data/kent.json": "1\n" * 30,                  # data: excluded
                "apps/web/package-lock.json": "1\n" * 40,      # nested lock file
            },
            "mixed", "2026-10-12T10:00:00+01:00",
        )
        repo.publish()
        [c] = get_commit_log(tmp_path, "2026-10-10", "2026-10-16")
        assert c["additions"] == 2
        assert c["excluded_additions"] == 90


class TestMergeCommitsRealGit:
    def test_merges_listed_separately_and_not_in_commit_log(self, tmp_path):
        from auditer.git_ops import get_commit_log, get_merge_commits

        repo = _GitRepo(tmp_path)
        repo.commit({"a.py": "1\n"}, "base", "2026-10-11T10:00:00+01:00")
        repo.git("checkout", "-q", "-b", "feat")
        repo.commit({"b.py": "1\n2\n"}, "feature", "2026-10-12T10:00:00+01:00")
        repo.git("checkout", "-q", "main")
        repo.commit({"c.py": "1\n"}, "main work", "2026-10-12T11:00:00+01:00")
        repo.git("merge", "-q", "--no-ff", "feat", "-m", "Merge pull request #1",
                 date="2026-10-13T09:00:00+01:00")
        repo.publish()

        commits = get_commit_log(tmp_path, "2026-10-10", "2026-10-16", default_branch="main")
        assert sorted(c["subject"] for c in commits) == ["base", "feature", "main work"]
        merges = get_merge_commits(tmp_path, ["origin/main"], "2026-10-10", "2026-10-16")
        assert [m["subject"] for m in merges] == ["Merge pull request #1"]
        assert merges[0]["author_email"] == "tom@x"
        assert merges[0]["date"] == "2026-10-13"


class TestFetchRepo:
    def test_fetch_updates_remote_refs_and_leaves_worktree(self, tmp_path):
        from auditer.git_ops import fetch_repo, last_fetch_time

        upstream = _GitRepo(tmp_path / "upstream")
        upstream.commit({"a.py": "1\n"}, "one", "2026-10-12T10:00:00+01:00")
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(upstream.path), str(clone)], check=True)
        new_sha = upstream.commit({"b.py": "1\n"}, "two", "2026-10-16T17:30:00+01:00")
        upstream.git("tag", "v2")

        result = fetch_repo(clone, str(upstream.path))
        assert result.success, result.message
        remote_tip = subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=clone, capture_output=True, text=True,
        ).stdout.strip()
        assert remote_tip == new_sha
        assert "v2" in subprocess.run(
            ["git", "tag"], cwd=clone, capture_output=True, text=True,
        ).stdout
        # Working tree untouched: activity reads origin/* refs only
        assert not (clone / "b.py").exists()
        assert last_fetch_time(clone) is not None

    @patch("auditer.git_ops._run_git")
    def test_never_prompts_for_credentials(self, mock_run, tmp_path):
        from auditer.git_ops import fetch_repo

        (tmp_path / ".git").mkdir()
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout="", stderr="",
        )
        fetch_repo(tmp_path, "https://github.com/o/private.git", token=None)
        fetch_call = next(c for c in mock_run.call_args_list if c.args[0][0] == "fetch")
        assert fetch_call.kwargs["extra_env"] == {"GIT_TERMINAL_PROMPT": "0"}
        assert fetch_call.kwargs["token"] is None

    def test_not_a_repo(self, tmp_path):
        from auditer.git_ops import fetch_repo, last_fetch_time

        result = fetch_repo(tmp_path, "https://example.invalid/x.git")
        assert not result.success
        assert last_fetch_time(tmp_path) is None

    def test_failed_fetch_reports_reason(self, tmp_path):
        from auditer.git_ops import fetch_repo

        repo = _GitRepo(tmp_path / "r")
        repo.commit({"a.py": "1\n"}, "one", "2026-10-12T10:00:00+01:00")
        repo.git("remote", "add", "origin", str(tmp_path / "missing"))
        result = fetch_repo(repo.path, str(tmp_path / "missing"))
        assert not result.success
        # The reason, not git's closing "error: Could not fetch origin"
        assert result.message.startswith("Fetch failed: fatal:")


class TestRunGitMissingCwd:
    def test_missing_directory_is_a_failed_command(self, tmp_path):
        result = _run_git(["status"], cwd=str(tmp_path / "gone"))
        assert result.returncode == -1


class TestEditorSettingsExclusion:
    def test_vscode_config_excluded_but_code_below_it_counted(self, tmp_path):
        """`.vscode/**` excluded a whole VS Code extension kept under
        .vscode/extensions/; only the editor's own config files go now."""
        from auditer.git_ops import get_commit_log

        repo = _GitRepo(tmp_path)
        repo.commit(
            {
                ".vscode/settings.json": "{\n}\n",                 # excluded
                ".vscode/launch.json": "{\n}\n",                   # excluded
                ".vscode/my.code-snippets": "{\n}\n",              # excluded
                "apps/web/.vscode/tasks.json": "{\n}\n",           # excluded, any depth
                "team.code-workspace": "{\n}\n",                   # excluded
                ".vscode/extensions/stack/src/extension.ts": "1\n" * 30,  # counted
                ".vscode/extensions/stack/package.json": "1\n" * 5,       # counted
                ".vscode/README.md": "notes\n",                    # counted
            },
            "editor", "2026-10-12T10:00:00+01:00",
        )
        repo.publish()
        [c] = get_commit_log(tmp_path, "2026-10-10", "2026-10-16")
        assert c["additions"] == 36
        assert c["excluded_additions"] == 10

    def test_glob_patterns_passed_with_glob_magic(self):
        from auditer.git_ops import EXCLUDED_GLOB_PATHSPECS, exclusion_pathspecs

        args = exclusion_pathspecs()
        assert ":(exclude).vscode/**" not in args
        for p in EXCLUDED_GLOB_PATHSPECS:
            assert f":(exclude,glob){p}" in args


class TestLastFetchTime:
    def test_failed_fetch_does_not_count_as_fresh(self, tmp_path):
        """A failed fetch empties FETCH_HEAD but still stamps it; the time
        reported must be the last successful fetch, from the reflogs."""
        from auditer.git_ops import fetch_repo, last_fetch_failed, last_fetch_time

        upstream = _GitRepo(tmp_path / "upstream")
        upstream.commit({"a.py": "1\n"}, "one", "2026-10-12T10:00:00+01:00")
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(upstream.path), str(clone)], check=True)
        upstream.commit({"b.py": "1\n"}, "two", "2026-10-13T10:00:00+01:00")
        assert fetch_repo(clone, str(upstream.path)).success
        assert not last_fetch_failed(clone)
        good = last_fetch_time(clone)
        assert good is not None

        # Access ends: the next fetch fails
        missing = str(tmp_path / "gone")
        subprocess.run(["git", "remote", "set-url", "origin", missing], cwd=clone, check=True)
        assert not fetch_repo(clone, missing).success
        fetch_head = clone / ".git" / "FETCH_HEAD"
        assert fetch_head.stat().st_size == 0
        far_future = good.timestamp() + 10 * 86400
        os.utime(fetch_head, (far_future, far_future))

        assert last_fetch_failed(clone)
        fallback = last_fetch_time(clone)
        assert fallback is not None and fallback.timestamp() < far_future
        assert abs(fallback.timestamp() - good.timestamp()) < 60

    def test_never_fetched_clone_uses_reflog_or_none(self, tmp_path):
        from auditer.git_ops import last_fetch_failed, last_fetch_time

        (tmp_path / ".git").mkdir()
        assert last_fetch_time(tmp_path) is None
        assert not last_fetch_failed(tmp_path)

    def test_reset_entry_in_head_reflog_counts(self, tmp_path):
        """checkout only resets to origin/<branch> after a good fetch."""
        from auditer.git_ops import last_fetch_time

        logs = tmp_path / ".git" / "logs"
        logs.mkdir(parents=True)
        (tmp_path / ".git" / "FETCH_HEAD").write_text("")
        (logs / "HEAD").write_text(
            "a b T <t@x> 1791000000 +0100\tcommit: x\n"
            "b b T <t@x> 1791792528 +0100\treset: moving to origin/main\n"
            "b c T <t@x> 1791900000 +0100\tcommit: local work\n"
        )
        assert int(last_fetch_time(tmp_path).timestamp()) == 1791792528


class TestLastFetchFailedEmptyRepo:
    def test_empty_upstream_is_not_a_failed_fetch(self, tmp_path):
        """Fetching an empty repository succeeds with an empty FETCH_HEAD."""
        from auditer.git_ops import fetch_repo, last_fetch_failed

        upstream = _GitRepo(tmp_path / "empty")
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", str(upstream.path), str(clone)],
                       check=True, capture_output=True)
        assert fetch_repo(clone, str(upstream.path)).success
        assert (clone / ".git" / "FETCH_HEAD").stat().st_size == 0
        assert not last_fetch_failed(clone)
