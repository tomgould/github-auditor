from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from auditer.timewindow import git_window_args, local_date
from auditer.utils.security import sanitise_git_output

logger = logging.getLogger(__name__)

# Restrict repo names to prevent path traversal. Spaces are allowed (some
# Bitbucket repo names legitimately contain them, e.g. "CUE Twitter Embed")
# but "/", "\", and other separators are not — the ".." check below is the
# actual traversal guard.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._ -]+$")

# Match hash ranges in git fetch output: abc1234..def5678 or abc1234...def5678
_HASH_RANGE_RE = re.compile(r"([0-9a-f]+)\.{2,3}([0-9a-f]+)")

# Pathspec exclusions for line-of-code counting (get_commit_log).
# These patterns are passed as `-- . :(exclude)<pattern>` to git log --shortstat
# (long form: the short `:!` form treats a leading `_` as pathspec magic,
# so `:!__mocks__/**` is a fatal error)
# to exclude lock files, vendored code, and other non-authored content.
#
# Matching semantics (git's default, non-glob pathspec): `*` also matches
# `/`, so `*.jpg` already covers any depth, but a directory pattern is
# anchored at the repo root — `vendor/**` misses `drupal/vendor/`. Each
# directory therefore appears twice, as `X/**` and `*/X/**`.
_LIBRARY_DIRS = [
    "vendor",
    "node_modules",
    "bower_components",
    "jspm_packages",
    "third-party",
    "third_party",
    "libraries",  # Drupal's committed JS libraries
    ".yarn",
    "dist",  # built output committed alongside its source
    # Recorded test data: API response mocks, fixtures and snapshots are
    # captured or generated, not written line by line (one repo had 2.2M
    # lines of JSON under tests/mocks in ten weeks).
    "mocks",
    "__mocks__",
    "fixtures",
    "__fixtures__",
    "__snapshots__",
]

# Binary formats git can mistake for text. Git's heuristic looks for a NUL
# byte in the first 8KB; a JPEG or PDF without one is diffed line by line
# and every byte-row counts as a "line" — a 20,000-line commit of hero
# images was the single biggest entry in one real report.
_BINARY_EXTENSIONS = [
    # Images
    "jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "ico", "tif", "tiff",
    "psd", "ai", "eps", "heic",
    # Fonts
    "woff", "woff2", "ttf", "otf", "eot",
    # Documents / archives
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "gz", "tgz",
    "bz2", "xz", "7z", "rar", "jar", "war",
    # Audio / video
    "mp3", "mp4", "m4a", "mov", "avi", "mkv", "webm", "wav", "ogg", "flac",
    # Compiled / binary blobs
    "exe", "dll", "so", "dylib", "bin", "dat", "sqlite", "db", "pyc", "class",
    "wasm",
    # Git bundles, HTTP archives and Jest snapshots: generated, text-ish
    "bundle", "har", "snap",
]

_LOCK_FILES = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "composer.lock",
    "Gemfile.lock",
    "Pipfile.lock",
    "poetry.lock",
    "Cargo.lock",
    "go.sum",
    "bun.lockb",
    "shrinkwrap.json",
    "npm-shrinkwrap.json",
    "packages.lock.json",
    "flake.lock",
]

# Data files: record formats that are exported, imported or generated, not
# written line by line. One week's ticket-workspace import was 132,797 lines
# of CSV and TSV, three quarters of one developer's "lines added". These
# extensions are almost never code, so they're excluded everywhere.
_DATA_EXTENSIONS = ["csv", "tsv", "jsonl", "ndjson"]
# JSON and XML are code and config too (package.json, tsconfig.json,
# composer.json, phpunit.xml), so they're excluded only inside directories
# whose name says they hold data: ticket attachments, data exports and dumps
# (seen live: data/kent-sections.json, 8,264 lines; attachments/*.json).
# A size threshold would be a better test but can't be written as a pathspec.
_DATA_DIRS = ["data", "attachments", "exports", "dumps"]
_DATA_DIR_EXTENSIONS = ["json", "xml"]

EXCLUDED_PATHSPECS = [
    # Lock files, at any depth: a bare `package-lock.json` only matches the
    # repo root, so a nested lock file (amplify/.../package-lock.json) was
    # counted as authored code.
    *_LOCK_FILES,
    *[f"*/{f}" for f in _LOCK_FILES],
    # Data files (see _DATA_EXTENSIONS / _DATA_DIRS)
    *[f"*.{ext}" for ext in _DATA_EXTENSIONS],
    *[f"*.{ext.upper()}" for ext in _DATA_EXTENSIONS],
    *[f"{d}/*.{ext}" for d in _DATA_DIRS for ext in _DATA_DIR_EXTENSIONS],
    *[f"*/{d}/*.{ext}" for d in _DATA_DIRS for ext in _DATA_DIR_EXTENSIONS],
    # Vendored / third-party / built directories, at any depth
    *[f"{d}/**" for d in _LIBRARY_DIRS],
    *[f"*/{d}/**" for d in _LIBRARY_DIRS],
    # WordPress core (not authored)
    "wp-admin/**",
    "wp-includes/**",
    "wp-content/plugins/**",
    "wp-content/themes/twenty*/**",
    "wp-content/uploads/**",
    "*/wp-admin/**",
    "*/wp-includes/**",
    "*/wp-content/plugins/**",
    "*/wp-content/themes/twenty*/**",
    "*/wp-content/uploads/**",
    # Minified / generated assets
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.bundle.js",
    "*.chunk.js",
    # Binary files (see _BINARY_EXTENSIONS)
    *[f"*.{ext}" for ext in _BINARY_EXTENSIONS],
    *[f"*.{ext.upper()}" for ext in _BINARY_EXTENSIONS],
    # IDE / editor settings (VS Code's are in EXCLUDED_GLOB_PATHSPECS)
    ".idea/**",
    "*.code-workspace",
]

# Patterns passed with glob magic (`:(exclude,glob)`), where `*` stops at
# `/` and `**/` means any depth: the editor config files at the top of any
# .vscode/ directory (settings.json, launch.json, tasks.json,
# extensions.json, snippets), but not source code that lives below one.
# `.vscode/**` threw away a whole VS Code extension kept in
# .vscode/extensions/ (8.4k lines in one week of example-web).
EXCLUDED_GLOB_PATHSPECS = [
    "**/.vscode/*.json",
    "**/.vscode/*.jsonc",
    "**/.vscode/*.code-snippets",
]


def exclusion_pathspecs() -> list[str]:
    """Every line-count exclusion as a git pathspec argument."""
    return [
        *[f":(exclude){p}" for p in EXCLUDED_PATHSPECS],
        *[f":(exclude,glob){p}" for p in EXCLUDED_GLOB_PATHSPECS],
    ]


@dataclass
class BranchUpdate:
    """Details about a branch that was updated during fetch."""

    name: str
    old_hash: str = ""
    new_hash: str = ""
    commit_count: int = 0
    authors: list[str] = field(default_factory=list)
    commits: list[tuple[str, str, str]] = field(default_factory=list)  # (hash, subject, author)
    forced: bool = False
    removed_count: int = 0


@dataclass
class GitOpResult:
    """Result of a git operation with details about what changed."""

    success: bool
    message: str
    new_branches: list[str] = field(default_factory=list)
    branch_updates: list[BranchUpdate] = field(default_factory=list)
    new_tags: list[str] = field(default_factory=list)


class GitError(Exception):
    pass


def _validate_name(name: str) -> None:
    if not _SAFE_NAME_RE.match(name):
        raise GitError(f"Invalid repository name: {name!r}")
    if ".." in name:
        raise GitError(f"Path traversal detected in name: {name!r}")


def _build_auth_url(clone_url: str, auth_username: str) -> str:
    """Inject auth_username as the URL's userinfo, replacing any existing one.

    A naive `clone_url.replace("https://", f"https://{auth_username}@")`
    breaks two ways: Bitbucket's clone_url already embeds a username
    (e.g. "https://tomgould@bitbucket.org/...", clobbering into a second,
    malformed userinfo section), and auth_username itself may contain "@"
    (Bitbucket's auth_email always does), which must be percent-encoded to
    be valid inside a URL's userinfo component rather than creating a
    second, unintended "@" delimiter.
    """
    parts = urlsplit(clone_url)
    netloc = f"{quote(auth_username, safe='')}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _make_askpass_script(token: str) -> str:
    path = tempfile.mktemp(suffix=".sh", prefix="git_askpass_")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    try:
        os.write(fd, f"#!/bin/sh\necho {token}\n".encode())
    finally:
        os.close(fd)
    return path


def _run_git(
    args: list[str],
    token: str | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    askpass_script = None
    if token:
        askpass_script = _make_askpass_script(token)
        env["GIT_ASKPASS"] = askpass_script
        env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(
            args=["git"] + args,
            returncode=-1,
            stdout="",
            stderr=f"Command timed out after {timeout} seconds",
        )
    except OSError as e:
        # Missing working directory (a clone deleted mid-scan) or no git
        # binary: a failed command, not a crash of the whole scan.
        result = subprocess.CompletedProcess(
            args=["git"] + args, returncode=-1, stdout="", stderr=str(e),
        )
    finally:
        if askpass_script:
            try:
                os.unlink(askpass_script)
            except OSError:
                logger.warning("Failed to remove askpass script: %s", askpass_script)

    return result


def is_repo_dirty(repo_dir: Path) -> bool:
    """Check if a repo has uncommitted changes (modified/untracked/staged files).

    Returns False on git failure (fail-open — don't block checkout on a status error).
    """
    result = _run_git(["status", "--porcelain"], cwd=str(repo_dir))
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def _parse_fetch_output(
    output: str,
) -> tuple[list[str], list[tuple[str, str, str, bool]], list[str]]:
    """Parse git fetch stderr to extract new/updated branches and tags.

    Returns:
        new_branches: list of new branch names
        ref_updates: list of (name, old_hash, new_hash, forced) tuples
        new_tags: list of new tag names
    """
    new_branches: list[str] = []
    ref_updates: list[tuple[str, str, str, bool]] = []
    new_tags: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("From ") or stripped.startswith("Fetching "):
            continue
        if "->" not in stripped:
            continue
        ref_part = stripped.split("->")[-1].strip()
        # Strip (forced update) suffix
        ref_part = ref_part.split("(")[0].strip()
        remote_ref = ref_part.removeprefix("origin/").strip()

        if "[new branch]" in stripped:
            new_branches.append(remote_ref)
        elif "[new tag]" in stripped:
            new_tags.append(remote_ref)
        else:
            match = _HASH_RANGE_RE.search(stripped)
            if match:
                old_hash = match.group(1)
                new_hash = match.group(2)
                forced = "..." in match.group(0)
                ref_updates.append((remote_ref, old_hash, new_hash, forced))
    return new_branches, ref_updates, new_tags


def _get_commit_details(
    cwd: str, old_hash: str, new_hash: str, forced: bool = False
) -> tuple[int, list[str], list[str], int]:
    """Get commit count, authors, and one-line summaries for a ref range.

    Returns: (commit_count, sorted_authors, commit_lines, removed_count)
    """
    # Commits added: reachable from new but not old
    result = _run_git(
        ["log", "--reverse", f"{old_hash}..{new_hash}", "--format=%h\x1e%an\x1e%s"],
        cwd=cwd,
    )
    commits: list[str] = []
    authors_set: set[str] = set()
    if result.returncode == 0 and result.stdout.strip():
        # Never strip() a record line: Python treats \x1e as whitespace, so
        # a commit with an empty subject would lose its last separator.
        for log_line in result.stdout.split("\n"):
            if not log_line:
                continue
            parts = log_line.split("\x1e", 2)
            if len(parts) == 3:
                short_hash, author, subject = parts
                authors_set.add(author)
                commits.append((short_hash, subject, author))
    elif result.returncode != 0:
        logger.debug(
            f"git log {old_hash}..{new_hash} failed: {result.stderr.strip()}"
        )

    # For force pushes, also count removed commits (reachable from old but not new)
    removed_count = 0
    if forced:
        rev_result = _run_git(
            ["rev-list", "--count", f"{new_hash}..{old_hash}"],
            cwd=cwd,
        )
        if rev_result.returncode == 0 and rev_result.stdout.strip():
            try:
                removed_count = int(rev_result.stdout.strip())
            except ValueError:
                pass
        # If no added commits, get authors from the new tip for context
        if not authors_set:
            tip = _run_git(
                ["log", new_hash, "-1", "--format=%an"],
                cwd=cwd,
            )
            if tip.returncode == 0 and tip.stdout.strip():
                authors_set.add(tip.stdout.strip())

    return len(commits), sorted(authors_set), commits, removed_count


def clone_repo(
    clone_url: str,
    target_dir: Path,
    token: str | None = None,
    name: str | None = None,
    auth_username: str = "oauth2",
) -> GitOpResult:
    if name:
        _validate_name(name)

    # Use https URL with auth_username for GIT_ASKPASS
    if token:
        auth_url = _build_auth_url(clone_url, auth_username)
    else:
        auth_url = clone_url

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    result = _run_git(
        ["clone", auth_url, str(target_dir)],
        token=token,
    )

    if result.returncode != 0:
        msg = sanitise_git_output(result.stderr)
        return GitOpResult(success=False, message=f"Clone failed: {msg}")

    # Ensure remote URL is clean (no auth info)
    _run_git(
        ["config", "remote.origin.url", clone_url],
        cwd=str(target_dir),
    )

    return GitOpResult(success=True, message="Cloned")


def fetch_and_pull(
    repo_dir: Path,
    clone_url: str,
    default_branch: str,
    token: str | None = None,
    fetch_branches: bool = False,
    fetch_tags: bool = False,
    auth_username: str = "oauth2",
) -> GitOpResult:
    if not (repo_dir / ".git").is_dir():
        return GitOpResult(success=False, message=f"Not a git repository: {repo_dir}")

    cwd = str(repo_dir)

    # Set authenticated remote temporarily via GIT_ASKPASS
    if token:
        auth_url = _build_auth_url(clone_url, auth_username)
        _run_git(["config", "remote.origin.url", auth_url], cwd=cwd)

    try:
        # Fetch all remotes, prune deleted branches
        fetch_args = ["fetch", "--all", "--prune"]
        if fetch_tags:
            # --force: these clones are read-only mirrors, so a tag moved on
            # the remote should clobber the local one rather than fail the
            # whole fetch ("would clobber existing tag").
            fetch_args.extend(["--tags", "--force"])
        result = _run_git(fetch_args, token=token, cwd=cwd)
        if result.returncode != 0:
            return GitOpResult(
                success=False,
                message=f"Fetch failed: {sanitise_git_output(result.stderr)}",
            )

        # Parse fetch output for new/updated refs
        new_branches, ref_updates, new_tags = _parse_fetch_output(result.stderr)

        # Get commit details for each updated branch
        branch_updates: list[BranchUpdate] = []
        for ref_name, old_hash, new_hash, forced in ref_updates:
            commit_count, authors, commits, removed_count = _get_commit_details(
                cwd, old_hash, new_hash, forced=forced
            )
            branch_updates.append(
                BranchUpdate(
                    name=ref_name,
                    old_hash=old_hash,
                    new_hash=new_hash,
                    commit_count=commit_count,
                    authors=authors,
                    commits=commits,
                    forced=forced,
                    removed_count=removed_count,
                )
            )

        # Check if the remote default branch exists (empty repos have no refs)
        check = _run_git(
            ["rev-parse", "--verify", f"origin/{default_branch}"],
            cwd=cwd,
        )
        if check.returncode != 0:
            logger.debug(
                f"Remote branch origin/{default_branch} not found — repo may be empty"
            )
        else:
            # Update current branch to match remote
            result = _run_git(
                ["reset", "--hard", f"origin/{default_branch}"],
                cwd=cwd,
            )
            if result.returncode != 0:
                return GitOpResult(
                    success=False,
                    message=f"Reset failed: {sanitise_git_output(result.stderr)}",
                )

        # Create/update local tracking branches for all remote branches
        if fetch_branches:
            result = _run_git(
                ["branch", "-r", "--format=%(refname:lstrip=3)"],
                cwd=cwd,
            )
            if result.returncode == 0:
                current = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
                current_branch = current.stdout.strip() if current.returncode == 0 else ""

                for branch in result.stdout.strip().splitlines():
                    branch = branch.strip()
                    if not branch or branch.startswith("HEAD") or branch == current_branch:
                        continue
                    # Create or force-update local branch to match remote
                    _run_git(
                        ["branch", "-f", branch, f"origin/{branch}"],
                        cwd=cwd,
                    )

    finally:
        # Always reset remote URL back to clean
        _run_git(["config", "remote.origin.url", clone_url], cwd=cwd)

    return GitOpResult(
        success=True,
        message="Updated",
        new_branches=new_branches,
        branch_updates=branch_updates,
        new_tags=new_tags,
    )


FETCH_TIMEOUT = 300  # seconds per repo


def fetch_repo(
    repo_dir: Path,
    clone_url: str,
    token: str | None = None,
    auth_username: str = "oauth2",
    timeout: int = FETCH_TIMEOUT,
) -> GitOpResult:
    """Fetch every branch and tag from the remote, pruning deleted branches.

    Used by ``activity`` before it reads a clone, so the report covers what
    is on the remote now rather than at the last ``checkout``. Unlike
    ``fetch_and_pull()`` it never touches the working tree or local
    branches: activity reads ``origin/*`` refs, and a clone someone is
    working in must not be reset. Auth is the same GIT_ASKPASS scheme.
    """
    if not (repo_dir / ".git").is_dir():
        return GitOpResult(success=False, message=f"Not a git repository: {repo_dir}")

    cwd = str(repo_dir)
    if token and clone_url:
        auth_url = _build_auth_url(clone_url, auth_username)
        _run_git(["config", "remote.origin.url", auth_url], cwd=cwd)
    try:
        # Never wait on a credential prompt: an account without a token
        # can't fetch a private repo, and the scan must not hang on it.
        result = _run_git(
            ["fetch", "--all", "--prune", "--tags", "--force"],
            token=token, cwd=cwd, timeout=timeout,
            extra_env={"GIT_TERMINAL_PROMPT": "0"},
        )
    finally:
        if token and clone_url:
            _run_git(["config", "remote.origin.url", clone_url], cwd=cwd)

    if result.returncode != 0:
        # git ends with "error: Could not fetch origin"; the reason (repo
        # deleted, auth refused) is on the first "fatal:" line.
        lines = [ln.strip() for ln in sanitise_git_output(result.stderr).splitlines() if ln.strip()]
        reason = next((ln for ln in lines if ln.startswith("fatal:")), lines[-1] if lines else "")
        return GitOpResult(
            success=False,
            message=f"Fetch failed: {reason or 'exit ' + str(result.returncode)}",
        )
    return GitOpResult(success=True, message="Fetched")


def last_fetch_failed(repo_dir: Path) -> bool:
    """Whether the clone's most recent fetch failed.

    git truncates FETCH_HEAD before it connects and only fills it in on
    success, so an empty FETCH_HEAD means the last attempt failed (seen
    live: 390 clones of an account whose access had ended, all with an
    empty FETCH_HEAD stamped with the time of the failed attempt) — unless
    the remote is an empty repository, where a good fetch has no refs to
    write either, so a clone with no remote-tracking refs never counts.
    """
    try:
        if (repo_dir / ".git" / "FETCH_HEAD").stat().st_size != 0:
            return False
    except OSError:
        return False
    refs = _run_git(["for-each-ref", "--count=1", "refs/remotes/"], cwd=str(repo_dir))
    return refs.returncode == 0 and bool(refs.stdout.strip())


def _reflog_time(line: str) -> datetime | None:
    """Timestamp of one reflog line: ``old new ident <ts> <tz>\tmessage``."""
    try:
        ts = int(line.split("\t", 1)[0].rsplit(" ", 2)[1])
    except (IndexError, ValueError):
        return None
    return datetime.fromtimestamp(ts).astimezone()


def _newest_fetch_reflog_time(git_dir: Path) -> datetime | None:
    """Newest reflog entry a successful fetch leaves behind.

    Either a remote-tracking ref moved by a fetch, or checkout's
    ``reset: moving to origin/...`` (``fetch_and_pull`` only resets after
    the fetch succeeded). A fetch that brought nothing new leaves neither,
    so this can be older than the last good fetch, never newer.
    """
    newest: datetime | None = None

    def _consider(t: datetime | None) -> None:
        nonlocal newest
        if t is not None and (newest is None or t > newest):
            newest = t

    remote_logs = git_dir / "logs" / "refs" / "remotes"
    if remote_logs.is_dir():
        for log in remote_logs.rglob("*"):
            if not log.is_file():
                continue
            try:
                lines = log.read_text(errors="replace").splitlines()
            except OSError:
                continue
            if lines:
                _consider(_reflog_time(lines[-1]))
    try:
        head_lines = (git_dir / "logs" / "HEAD").read_text(errors="replace").splitlines()
    except OSError:
        head_lines = []
    for line in reversed(head_lines):
        if "\treset: moving to origin/" in line:
            _consider(_reflog_time(line))
            break
    return newest


def last_fetch_time(repo_dir: Path) -> datetime | None:
    """When the clone last fetched successfully, as far as git can tell.

    FETCH_HEAD's mtime when it holds refs. A failed fetch also stamps
    FETCH_HEAD (empty), so then — or when there's no FETCH_HEAD — the
    newest fetch trace in the reflogs is used instead. None if neither.
    """
    git_dir = repo_dir / ".git"
    try:
        st = (git_dir / "FETCH_HEAD").stat()
        if st.st_size > 0:
            return datetime.fromtimestamp(st.st_mtime).astimezone()
    except OSError:
        pass
    return _newest_fetch_reflog_time(git_dir)


@dataclass
class LocalRepoInfo:
    """Metadata extracted from a locally checked-out repo."""

    name: str
    path: Path
    private: bool
    owner: str
    clone_url: str
    has_readme: bool
    has_license_file: bool
    default_branch: str
    pushed_at: str
    size_kb: int
    # URL-safe API identifier, parsed from clone_url — NOT always equal to
    # `name`. GitHub repo names are already URL-safe, but Bitbucket's
    # display "name" (used for `name`/the checkout directory, since it's
    # the readable identifier users expect) can contain spaces/caps that
    # differ from its actual "slug" (e.g. name="Bundler JS", slug=
    # "bundler-js"). API calls that take a repo path segment must use
    # `repo_slug`, not `name`, or Bitbucket 404s.
    repo_slug: str = ""

    def __post_init__(self) -> None:
        # Callers that construct LocalRepoInfo without knowing the real
        # slug (tests, or clone_url parsing failing) fall back to `name` —
        # correct for GitHub (name IS the slug) and no worse than the
        # pre-slug behavior for Bitbucket.
        if not self.repo_slug:
            self.repo_slug = self.name


def get_local_repo_metadata(repo_dir: Path, is_private: bool) -> LocalRepoInfo | None:
    """Extract metadata from a local git checkout. Returns None if not a git repo."""
    if not (repo_dir / ".git").is_dir():
        return None

    cwd = str(repo_dir)

    # Get remote origin URL → clone_url, owner, repo_slug
    result = _run_git(["config", "remote.origin.url"], cwd=cwd)
    clone_url = result.stdout.strip() if result.returncode == 0 else ""
    owner = ""
    repo_slug = ""
    if clone_url:
        # Parse owner/slug from URL like https://github.com/owner/repo.git
        parts = clone_url.rstrip("/").removesuffix(".git").split("/")
        if len(parts) >= 2:
            owner = parts[-2]
            repo_slug = parts[-1]

    # Default branch
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    default_branch = result.stdout.strip() if result.returncode == 0 else "main"

    # Latest push date (author date of most recent commit)
    result = _run_git(["log", "-1", "--format=%aI"], cwd=cwd)
    pushed_at = result.stdout.strip() if result.returncode == 0 else ""

    # Repo size (size-pack from count-objects)
    result = _run_git(["count-objects", "-v"], cwd=cwd)
    size_kb = 0
    if result.returncode == 0:
        for line in result.stdout.split("\n"):
            if line.startswith("size-pack:"):
                try:
                    size_kb = int(line.split(":")[1].strip())
                except ValueError:
                    pass
                break

    # Check for README and LICENSE files (case-insensitive)
    files_in_root = [f.name.lower() for f in repo_dir.iterdir() if f.is_file()]
    has_readme = any(f.startswith("readme") for f in files_in_root)
    has_license_file = any(f.startswith("license") or f.startswith("licence") for f in files_in_root)

    return LocalRepoInfo(
        name=repo_dir.name,
        path=repo_dir,
        private=is_private,
        owner=owner,
        clone_url=clone_url,
        has_readme=has_readme,
        has_license_file=has_license_file,
        default_branch=default_branch,
        pushed_at=pushed_at,
        size_kb=size_kb,
        repo_slug=repo_slug,
    )


def scan_local_repos(data_dir: Path) -> list[LocalRepoInfo]:
    """Walk data_dir/public/ and data_dir/private/ to find local checkouts."""
    repos: list[LocalRepoInfo] = []

    for subdir, is_private in [("public", False), ("private", True)]:
        dir_path = data_dir / subdir
        if not dir_path.is_dir():
            continue
        for entry in sorted(dir_path.iterdir()):
            if not entry.is_dir():
                continue
            info = get_local_repo_metadata(entry, is_private=is_private)
            if info is not None:
                repos.append(info)

    return repos


_COMMIT_FORMAT = "--format=COMMIT\x1e%H\x1e%an\x1e%ae\x1e%cI\x1e%s"
_STAT_RE = re.compile(r"\d+ files? changed")


def _parse_commit_log(stdout: str, tz: tzinfo | str | None = None) -> list[dict]:
    """Parse ``git log`` output in ``_COMMIT_FORMAT`` (+ optional --shortstat).

    Record lines are never ``strip()``-ed: Python treats ``\\x1e`` as
    whitespace, so a commit with an empty subject would lose its trailing
    separator and be dropped as malformed. ``date`` is the committer date
    in ``tz`` (the report's timezone), not the committer's own offset.
    """
    commits: list[dict] = []
    current: dict | None = None
    for line in stdout.split("\n"):
        if line.startswith("COMMIT\x1e"):
            parts = line.split("\x1e", 5)
            if len(parts) < 6:
                current = None
                continue
            _, sha, author_name, author_email, date_str, subject = parts
            current = {
                "sha": sha,
                "author_name": author_name,
                "author_email": author_email,
                "date": local_date(date_str, tz),
                "committed_at": date_str,
                "subject": subject.rstrip("\r"),
                "additions": 0,
                "deletions": 0,
            }
            commits.append(current)
        elif current is not None and _STAT_RE.search(line):
            add_match = re.search(r"(\d+) insertion", line)
            del_match = re.search(r"(\d+) deletion", line)
            current["additions"] = int(add_match.group(1)) if add_match else 0
            current["deletions"] = int(del_match.group(1)) if del_match else 0
    return commits


def _log_with_stats(
    repo_path: Path, revs: list[str], since: str, until: str,
    tz: tzinfo | str | None = None,
) -> list[dict]:
    """Non-merge commits in ``revs`` within the window, with line stats.

    Two passes, joined by SHA:

    1. without a pathspec — the list of commits and their raw line counts;
    2. with ``exclusion_pathspecs()`` — the counted lines.

    A single pathspec-limited log (the old approach) silently dropped
    every commit that only touched excluded files, so adding an exclusion
    changed commit counts, not just line counts. ``excluded_additions`` /
    ``excluded_deletions`` (raw minus counted) let the report show how much
    each developer had excluded. ``--full-history`` stops history
    simplification hiding side-branch commits from the pathspec pass.
    """
    base = [
        "log", *revs, "--no-merges", *git_window_args(since, until, tz),
        _COMMIT_FORMAT, "--shortstat",
    ]
    raw = _run_git(base, cwd=str(repo_path))
    if raw.returncode != 0 or not raw.stdout.strip():
        return []
    commits = _parse_commit_log(raw.stdout, tz)

    counted_res = _run_git(
        [*base, "--full-history", "--", ".", *exclusion_pathspecs()],
        cwd=str(repo_path),
    )
    if counted_res.returncode != 0:
        # Keep the commits rather than reporting none; their lines are raw.
        logger.warning(
            "%s: line-count exclusions failed (%s); using raw line counts",
            repo_path, sanitise_git_output(counted_res.stderr).strip()[:200],
        )
        counted = {c["sha"]: c for c in commits}
    else:
        counted = {c["sha"]: c for c in _parse_commit_log(counted_res.stdout, tz)}

    for c in commits:
        raw_add, raw_del = c["additions"], c["deletions"]
        k = counted.get(c["sha"])
        c["additions"] = k["additions"] if k else 0
        c["deletions"] = k["deletions"] if k else 0
        c["excluded_additions"] = max(0, raw_add - c["additions"])
        c["excluded_deletions"] = max(0, raw_del - c["deletions"])
    return commits


def get_commit_log(
    repo_path: Path, since: str, until: str, default_branch: str | None = None,
    tz: tzinfo | str | None = None,
) -> list[dict]:
    """Get non-merge commits with line stats for a whole-day date range.

    ``since``/``until`` are inclusive dates in ``tz`` (default
    Europe/London): the window is ``since`` 00:00 to ``until`` 23:59:59.

    Uses --no-merges: merge commits carry no ``--shortstat`` of their own
    (their changes are the merged commits' changes), so they're counted
    separately by ``get_merge_commits()`` and never add lines.

    When ``default_branch`` is provided, only commits reachable from
    ``origin/{default_branch}`` are counted (avoids double-counting
    squash-merged feature branch commits).  When ``None``, ``--all``
    is used for backward compatibility.

    ``date`` is the committer date (``%cI``) in ``tz``, not the author
    date: git's ``--since``/``--until`` select by committer date, so every
    returned commit's date falls inside the range and daily/weekly
    breakdowns sum to the total. Rebased or cherry-picked work therefore
    counts on the day it was rebased, not the day it was originally written.

    Returns list of dicts with keys: sha, author_name, author_email, date,
    subject, additions, deletions, excluded_additions, excluded_deletions.
    """
    revs = [f"origin/{default_branch}"] if default_branch is not None else ["--all"]
    return _log_with_stats(repo_path, revs, since, until, tz)


def get_merge_commits(
    repo_path: Path, revs: list[str], since: str, until: str,
    tz: tzinfo | str | None = None,
) -> list[dict]:
    """Merge commits in ``revs`` within the window (no line stats).

    A merge done on GitHub with "Create a merge commit" is authored by the
    person who pressed the button, so this credits merging work that
    ``--no-merges`` commit counts leave out. Their changes are the merged
    commits' changes, already counted, so no lines are attached.
    """
    result = _run_git(
        ["log", *revs, "--merges", *git_window_args(since, until, tz), _COMMIT_FORMAT],
        cwd=str(repo_path),
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return _parse_commit_log(result.stdout, tz)


def get_tags_in_range(
    repo_path: Path, since: str, until: str, tz: tzinfo | str | None = None,
) -> list[dict]:
    """Get tags created within a date range (whole days in ``tz``).

    For annotated tags, uses tagger info.  For lightweight tags, falls back
    to the commit author info.

    Returns list of dicts with keys: name, tagger_name, tagger_email, date.
    """
    result = _run_git(
        [
            "for-each-ref",
            "refs/tags/",
            "--sort=creatordate",
            "--format=%(refname:short)\x1e%(creatordate:iso-strict)"
            "\x1e%(taggername)\x1e%(taggeremail)"
            "\x1e%(authorname)\x1e%(authoremail)",
        ],
        cwd=str(repo_path),
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    tags: list[dict] = []
    # Never strip() the output: an annotated tag has empty author fields,
    # and Python's strip() treats \x1e as whitespace — the last tag in the
    # repo lost its trailing separators and was dropped as malformed.
    for line in result.stdout.split("\n"):
        if not line:
            continue
        parts = line.split("\x1e", 5)
        if len(parts) < 6:
            continue
        name, date_str, tagger_name, tagger_email, author_name, author_email = parts
        date = local_date(date_str, tz)
        if not (since <= date <= until):
            continue
        # Prefer tagger info (annotated tags), fall back to author (lightweight)
        tag_name = tagger_name.strip() if tagger_name.strip() else author_name.strip()
        tag_email = tagger_email.strip().strip("<>") if tagger_email.strip() else author_email.strip().strip("<>")
        if not tag_name:
            continue
        tags.append({
            "name": name,
            "tagger_name": tag_name,
            "tagger_email": tag_email,
            "date": date,
        })
    return tags


def list_active_branches(
    repo_path: Path, default_branch: str, since: str, until: str,
    tz: tzinfo | str | None = None,
) -> list[dict]:
    """List remote branches with commits in the date range NOT on the default branch.

    Returns list of dicts with keys: name, commit_count.
    Only branches with commit_count > 0 are returned.
    """
    window = git_window_args(since, until, tz)

    # List all remote branches
    result = _run_git(
        [
            "for-each-ref",
            "refs/remotes/origin/",
            "--format=%(refname:lstrip=3)",
        ],
        cwd=str(repo_path),
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    branches: list[dict] = []
    for line in result.stdout.strip().split("\n"):
        branch = line.strip()
        if not branch or branch == "HEAD" or branch == default_branch:
            continue

        # Count commits on this branch NOT reachable from default, in date range
        count_result = _run_git(
            [
                "rev-list",
                "--count",
                f"origin/{branch}",
                "--not",
                f"origin/{default_branch}",
                *window,
            ],
            cwd=str(repo_path),
        )
        if count_result.returncode != 0:
            continue
        try:
            count = int(count_result.stdout.strip())
        except ValueError:
            continue
        if count > 0:
            branches.append({"name": branch, "commit_count": count})

    return branches


def get_patch_ids(
    repo_path: Path, revs: list[str], since: str, until: str,
    tz: tzinfo | str | None = None,
) -> dict[str, str]:
    """Map commit SHA → stable patch-id for the non-merge commits in ``revs``.

    ``revs`` is passed straight to ``git log`` (e.g. ``["origin/main"]`` or
    ``["origin/feat/x", "--not", "origin/main"]``). The patch-id is a hash
    of the diff with line numbers and whitespace ignored, so a commit and
    its rebased or cherry-picked copy share one id even though their SHAs
    differ — which is how activity.py avoids counting work twice when a
    feature branch has been re-landed on the default branch, or stacked
    onto another branch. Returns ``{}`` on any git failure.
    """
    # Bytes throughout: the diff stream includes any binary file git failed
    # to detect as binary (NUL-free JPEGs etc.), which is not valid UTF-8.
    # Only patch-id's own output (hex + SHA) is decoded. Any failure here
    # must not abort the scan — callers fall back to SHA matching.
    try:
        log = subprocess.run(
            [
                "git", "log", "-p", "--no-merges", "--no-color",
                "--format=commit %H",
                *git_window_args(since, until, tz), *revs,
            ],
            capture_output=True, cwd=str(repo_path), timeout=300,
        )
        if log.returncode != 0 or not log.stdout:
            return {}
        pid = subprocess.run(
            ["git", "patch-id", "--stable"],
            input=log.stdout, capture_output=True,
            cwd=str(repo_path), timeout=300,
        )
        if pid.returncode != 0:
            return {}
        text = pid.stdout.decode("ascii", errors="replace")
    except Exception as e:  # noqa: BLE001 — degrade to SHA matching
        logger.debug("patch-id failed for %s: %s", repo_path, e)
        return {}

    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            patch_id, sha = parts
            result[sha] = patch_id
    return result


def get_branch_commits(
    repo_path: Path,
    branch: str,
    default_branch: str,
    since: str,
    until: str,
    tz: tzinfo | str | None = None,
) -> list[dict]:
    """Get commits on a branch that are NOT reachable from the default branch.

    Same return format as ``get_commit_log()``.
    """
    return _log_with_stats(
        repo_path, [f"origin/{branch}", "--not", f"origin/{default_branch}"],
        since, until, tz,
    )


def detect_orphaned_repos(
    data_dir: Path, remote_names: set[str], subdirs: list[str] | None = None
) -> list[tuple[str, str]]:
    orphans = []
    subdirs = subdirs or ["public", "private"]

    for subdir in subdirs:
        dir_path = data_dir / subdir
        if not dir_path.is_dir():
            continue
        for entry in dir_path.iterdir():
            if entry.is_dir() and entry.name not in remote_names:
                orphans.append((subdir, entry.name))

    return orphans
