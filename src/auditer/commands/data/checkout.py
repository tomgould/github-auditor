from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import quote

import click
from rich.console import Console
from rich.progress import Progress

from auditer.config import AppConfig, get_account, get_accounts_for_project, get_project
from auditer.filtering import (
    filter_by_project,
    filter_excluded_repos,
    filter_repos_by_activity,
    filter_repos_by_visibility,
)
from auditer.git_ops import GitError, GitOpResult, clone_repo, detect_orphaned_repos, fetch_and_pull, is_repo_dirty
from auditer.bitbucket_client import GIT_AUTH_USERNAME as BITBUCKET_GIT_AUTH_USERNAME
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError, GitHubAPIError
from auditer.models import AccountConfig, ProjectConfig

logger = logging.getLogger(__name__)
console = Console(soft_wrap=True, force_terminal=True)

MAX_COMMITS_SHOWN = 10      # Max commit lines per branch in change details
MAX_SUBJECT_LENGTH = 144    # Truncate commit messages longer than this
MAX_REFS_SHOWN = 10         # Max new branches/tags listed inline
SUMMARY_WIDTH = 50          # Width of the summary separator line


def _repo_web_url(clone_url: str) -> str:
    """Convert a clone URL to a GitHub web URL."""
    return clone_url.removesuffix(".git")


def _print_repo_details(name: str, web_url: str, result: GitOpResult) -> None:
    """Print change details for a single repo."""
    console.print(f"  [bold]{name}[/bold]  [dim]{web_url}[/dim]")
    for bu in result.branch_updates:
        author_str = ", ".join(bu.authors) if bu.authors else ""
        author_part = f" ({author_str})" if author_str else ""
        branch_url = f"{web_url}/tree/{quote(bu.name, safe='/')}"
        compare_url = (
            f"{web_url}/compare/{bu.old_hash}...{bu.new_hash}"
            if bu.old_hash and bu.new_hash
            else branch_url
        )

        if bu.forced:
            # Force push: show added/removed counts
            parts = []
            if bu.commit_count:
                s = "s" if bu.commit_count != 1 else ""
                parts.append(f"+{bu.commit_count} commit{s}")
            if bu.removed_count:
                s = "s" if bu.removed_count != 1 else ""
                parts.append(f"[red]-{bu.removed_count} commit{s}[/red]")
            change_str = ", ".join(parts) if parts else "ref reset"
            console.print(
                f"    [cyan]{bu.name}[/cyan]  [red]force-pushed[/red]"
                f" {change_str}{author_part}  [dim]{compare_url}[/dim]"
            )
        elif bu.commit_count:
            s = "s" if bu.commit_count != 1 else ""
            console.print(
                f"    [cyan]{bu.name}[/cyan]  +{bu.commit_count} commit{s}"
                f"{author_part}  [dim]{compare_url}[/dim]"
            )
        else:
            console.print(
                f"    [cyan]{bu.name}[/cyan]  [dim]ref updated[/dim]"
                f"{author_part}  [dim]{compare_url}[/dim]"
            )

        for short_hash, subject, author in bu.commits[:MAX_COMMITS_SHOWN]:
            trimmed = subject[:MAX_SUBJECT_LENGTH] + "..." if len(subject) > MAX_SUBJECT_LENGTH else subject
            console.print(
                f"      [dim]{short_hash}[/dim] {trimmed} [dim]({author})[/dim]"
            )
        if len(bu.commits) > MAX_COMMITS_SHOWN:
            remaining = len(bu.commits) - MAX_COMMITS_SHOWN
            console.print(f"      [dim]... and {remaining} more[/dim]")

    if result.new_branches:
        console.print(f"    [green]new branches:[/green]")
        for branch in result.new_branches[:MAX_REFS_SHOWN]:
            branch_url = f"{web_url}/tree/{quote(branch, safe='/')}"
            console.print(f"      {branch}  [dim]{branch_url}[/dim]")
        if len(result.new_branches) > MAX_REFS_SHOWN:
            remaining = len(result.new_branches) - MAX_REFS_SHOWN
            console.print(f"      [dim]... and {remaining} more[/dim]")

    if result.new_tags:
        console.print(f"    [yellow]new tags:[/yellow]")
        for tag in result.new_tags[:MAX_REFS_SHOWN]:
            tag_url = f"{web_url}/releases/tag/{quote(tag, safe='/')}"
            console.print(f"      {tag}  [dim]{tag_url}[/dim]")
        if len(result.new_tags) > MAX_REFS_SHOWN:
            remaining = len(result.new_tags) - MAX_REFS_SHOWN
            console.print(f"      [dim]... and {remaining} more[/dim]")


def run_checkout(
    config: AppConfig,
    account: AccountConfig,
    fetch_branches: bool = False,
    fetch_tags: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    skip_dirty: bool = True,
    project: ProjectConfig | None = None,
    months: int | None = None,
) -> None:
    base_dir = account.data_dir if account.data_dir else config.data_dir
    data_dir = Path(base_dir) / account.slug

    with get_provider(account) as client:
        # Validate token if present
        if account.token:
            try:
                username = client.validate_token()
                logger.info(f"Authenticated as: {username}")
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        # Fetch repo list (unfiltered for accurate orphan detection)
        console.print(f"Fetching repositories for [bold]{account.username}[/bold]...")
        try:
            all_repos = client.list_repos(apply_visibility_filter=False)
        except GitHubAPIError as e:
            console.print(f"[red]API error:[/red] {e}")
            sys.exit(3)
        # Save all remote names before any filtering (for orphan detection)
        all_remote_names = {r["name"] for r in all_repos}

        # Apply visibility filter (include_private, include_forks)
        raw_repos = filter_repos_by_visibility(
            all_repos,
            account,
            is_private=lambda r: r.get("private", False),
            is_fork=lambda r: r.get("fork", False),
            get_name=lambda r: r["name"],
        )
        # Filter excluded repos
        raw_repos, skipped = filter_excluded_repos(
            raw_repos, account, lambda r: r["name"]
        )
        if skipped:
            console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

        if project:
            raw_repos, proj_skipped = filter_by_project(
                raw_repos, project, account.slug, lambda r: r["name"]
            )
            if proj_skipped:
                console.print(
                    f"Filtered to [bold]{len(raw_repos)}[/bold] repo(s) in project '{project.name}'."
                )

        # CLI --months overrides the account's persistent active_within_months
        # default for this one run; when neither is set, no filtering happens
        # (existing behavior, unchanged for every account that doesn't opt in).
        effective_months = months if months is not None else account.active_within_months
        if effective_months is not None:
            raw_repos, months_skipped = filter_repos_by_activity(
                raw_repos, effective_months, lambda r: r.get("pushed_at", "")
            )
            if months_skipped:
                console.print(
                    f"Filtered out [bold]{months_skipped}[/bold] repo(s) with no activity "
                    f"in the last {effective_months} months."
                )

        total = len(raw_repos)
        console.print(f"Found [bold]{total}[/bold] repos. Processing...")

    cloned = 0
    updated = 0
    errors = 0
    total_new_branches = 0
    total_updated_branches = 0
    total_new_tags = 0
    total_commits = 0
    skipped_dirty = 0
    cloned_repos: list[tuple[str, str]] = []  # (name, web_url)
    skipped_dirty_repos: list[tuple[str, str]] = []  # (name, web_url)
    repos_with_changes: list[tuple[str, str, GitOpResult]] = []  # (name, web_url, result)
    error_repos: list[tuple[str, str]] = []

    with Progress(console=console) as progress:
        task = progress.add_task("Checking out repos", total=total)

        for i, repo in enumerate(raw_repos, 1):
            name = repo["name"]
            is_private = repo.get("private", False)
            default_branch = account.branch_overrides.get(name) or repo.get(
                "default_branch", "main"
            )
            clone_url = repo["clone_url"]
            web_url = _repo_web_url(clone_url)
            visibility = "private" if is_private else "public"

            target_dir = data_dir / visibility / name
            action = "update" if (target_dir / ".git").is_dir() else "clone"

            progress.update(task, description=f"[{i}/{total}] {action}: {name}")

            if dry_run:
                console.print(f"  [dim]DRY RUN:[/dim] Would {action} {name} ({visibility})")
                progress.advance(task)
                continue

            if action == "update":
                if skip_dirty and is_repo_dirty(target_dir):
                    skipped_dirty += 1
                    skipped_dirty_repos.append((name, web_url))
                    console.print(f"  [yellow]SKIP[/yellow] {name}: dirty working tree")
                    progress.advance(task)
                    continue

                # Repo exists — fetch and pull
                op_result = fetch_and_pull(
                    repo_dir=target_dir,
                    clone_url=clone_url,
                    default_branch=default_branch,
                    token=account.token,
                    fetch_branches=fetch_branches,
                    fetch_tags=fetch_tags,
                    **({"auth_username": BITBUCKET_GIT_AUTH_USERNAME} if account.provider == "bitbucket" else {}),
                )
                if op_result.success:
                    updated += 1
                    total_new_branches += len(op_result.new_branches)
                    total_updated_branches += len(op_result.branch_updates)
                    total_new_tags += len(op_result.new_tags)
                    total_commits += sum(
                        bu.commit_count for bu in op_result.branch_updates
                    )
                    has_changes = (
                        op_result.new_branches
                        or op_result.branch_updates
                        or op_result.new_tags
                    )
                    if has_changes:
                        repos_with_changes.append((name, web_url, op_result))
                    if verbose:
                        logger.debug(f"Updated {name}")
                else:
                    errors += 1
                    error_repos.append((name, op_result.message))
                    console.print(f"  [red]ERROR[/red] {name}: {op_result.message}")
            else:
                # Clone fresh
                try:
                    op_result = clone_repo(
                        clone_url=clone_url,
                        target_dir=target_dir,
                        token=account.token,
                        name=name,
                        **({"auth_username": BITBUCKET_GIT_AUTH_USERNAME} if account.provider == "bitbucket" else {}),
                    )
                except GitError as e:
                    errors += 1
                    error_repos.append((name, str(e)))
                    console.print(f"  [red]ERROR[/red] {name}: {e}")
                    progress.advance(task)
                    continue
                if op_result.success:
                    cloned += 1
                    cloned_repos.append((name, web_url))
                    if verbose:
                        logger.debug(f"Cloned {name}")
                else:
                    errors += 1
                    error_repos.append((name, op_result.message))
                    console.print(f"  [red]ERROR[/red] {name}: {op_result.message}")

            progress.advance(task)

    # Detect orphans (compare against unfiltered API list)
    if not dry_run:
        orphans = detect_orphaned_repos(data_dir, all_remote_names)
        if orphans:
            console.print("\n[yellow]Orphaned local repos (no longer on GitHub):[/yellow]")
            for subdir, name in orphans:
                console.print(f"  ORPHAN: {account.slug}/{subdir}/{name}")

    # Show skipped dirty repos
    if skipped_dirty_repos:
        console.print(f"\n[bold yellow]Skipped — dirty ({len(skipped_dirty_repos)}):[/bold yellow]")
        for name, web_url in skipped_dirty_repos:
            console.print(f"  {name}  [dim]{web_url}[/dim]")

    # Show newly cloned repos
    if cloned_repos:
        console.print(f"\n[bold green]Newly cloned ({len(cloned_repos)}):[/bold green]")
        for name, web_url in cloned_repos:
            console.print(f"  {name}  [dim]{web_url}[/dim]")

    # Show repos with changes
    if repos_with_changes:
        console.print(
            f"\n[bold blue]Changes detected in {len(repos_with_changes)} repos:[/bold blue]"
        )
        for name, web_url, result in repos_with_changes:
            _print_repo_details(name, web_url, result)

    # Recap errors at the end for easy review
    if error_repos:
        console.print(f"\n[bold red]Errors ({len(error_repos)}):[/bold red]")
        for name, msg in error_repos:
            console.print(f"  [red]{name}:[/red] {msg}")

    # Summary
    console.print()
    console.print("=" * SUMMARY_WIDTH)
    console.print("  CHECKOUT SUMMARY")
    console.print("=" * SUMMARY_WIDTH)
    console.print(f"  Total repos:        {total}")
    if dry_run:
        console.print("  [dim](dry run — no changes made)[/dim]")
    else:
        console.print(f"  Newly cloned:       {cloned}")
        console.print(f"  Updated:            {updated}")
        if skipped_dirty:
            console.print(f"  Skipped (dirty):    {skipped_dirty}")
        console.print(f"  Errors:             {errors}")
        if total_updated_branches or total_new_branches or total_new_tags or total_commits:
            console.print()
            console.print(f"  Branches updated:   {total_updated_branches}")
            console.print(f"  New branches:       {total_new_branches}")
            console.print(f"  New tags:           {total_new_tags}")
            console.print(f"  Total commits:      {total_commits}")
    console.print("=" * SUMMARY_WIDTH)


@click.command("checkout")
@click.option("--account", "account_slug", help="Account slug to checkout")
@click.option("--all", "checkout_all", is_flag=True, help="Checkout all configured accounts")
@click.option("--branches", is_flag=True, help="Fetch all remote branches")
@click.option("--tags", is_flag=True, help="Fetch all tags")
@click.option("--full", is_flag=True, help="Full sync: fetch all branches and tags")
@click.option("--dry-run", is_flag=True, help="Show what would be done without doing it")
@click.option("--skip-dirty/--no-skip-dirty", default=True, help="Skip repos with uncommitted local changes (default: on)")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.option("--months", "months", type=int, default=None, help="Only checkout repos active within the last N months")
@click.pass_context
def checkout_cmd(
    ctx: click.Context,
    account_slug: str | None,
    checkout_all: bool,
    branches: bool,
    tags: bool,
    full: bool,
    dry_run: bool,
    skip_dirty: bool,
    project_name: str | None,
    months: int | None,
):
    """Clone or update all repositories for an account."""
    config: AppConfig = ctx.obj["lazy_config"].config
    verbose: bool = ctx.obj.get("verbose", False)

    if full:
        branches = True
        tags = True

    project = None
    if project_name:
        project = get_project(config, project_name)
        if not checkout_all and not account_slug:
            # --project alone: operate on all accounts in the project
            accounts = get_accounts_for_project(config, project)
            if not accounts:
                console.print(f"[yellow]No accounts have repos in project '{project_name}'[/yellow]")
                sys.exit(0)

    if project and not checkout_all and not account_slug:
        pass  # accounts already set above
    elif checkout_all:
        accounts = config.accounts
    elif account_slug:
        accounts = [get_account(config, account_slug)]
    else:
        console.print("[red]Specify --account SLUG or --all[/red]")
        sys.exit(1)

    for account in accounts:
        console.print(f"\n[bold blue]Checking out: {account.slug}[/bold blue]")
        run_checkout(
            config,
            account,
            fetch_branches=branches,
            fetch_tags=tags,
            dry_run=dry_run,
            verbose=verbose,
            skip_dirty=skip_dirty,
            project=project,
            months=months,
        )
