from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from auditer.config import AppConfig, get_account, get_accounts_for_project, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos, filter_repos_by_visibility
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError
from auditer.models import AccessResult, AccountConfig, ProjectConfig, RepoAccess
from auditer.reports.access import (
    PERMISSION_LEVELS,
    _build_user_id_map,
    _highest_permission,
    _sort_users,
    generate_access_report,
)

logger = logging.getLogger(__name__)
console = Console(soft_wrap=True, force_terminal=True)


def _print_access_tables(result: AccessResult, sort_by: str) -> None:
    """Print per-repo collaborator tables to the console using Rich."""
    if not result.repos:
        return

    user_id_map = _build_user_id_map(result)
    repos_sorted = sorted(result.repos, key=lambda r: r.repo_name.lower())

    for repo in repos_sorted:
        visibility = "private" if repo.private else "public"
        table = Table(title=f"{repo.repo_name} ({visibility})")
        table.add_column("Collaborator")
        table.add_column("Permission")
        table.add_column("ID", justify="right")

        collab_logins = [c["login"] for c in repo.collaborators]
        sorted_logins = _sort_users(collab_logins, sort_by, user_id_map)
        login_to_collab = {c["login"]: c for c in repo.collaborators}

        for login in sorted_logins:
            c = login_to_collab[login]
            perm = _highest_permission(c.get("permissions", {}))
            uid = str(c.get("id", ""))
            table.add_row(login, perm, uid)

        console.print(table)


def run_access_report(
    config: AppConfig,
    account: AccountConfig,
    sort_by: str = "name",
    project: ProjectConfig | None = None,
) -> AccessResult:
    if project:
        reports_dir = Path(config.reports_dir) / project.name
    else:
        reports_dir = Path(config.reports_dir) / account.slug
    reports_dir.mkdir(parents=True, exist_ok=True)

    if not account.token:
        console.print(
            f"[red]Account '{account.slug}' has no token.[/red] "
            "The access report requires authentication."
        )
        sys.exit(1)

    with get_provider(account) as client:
        try:
            username = client.validate_token()
            logger.info(f"Authenticated as: {username}")
        except AuthenticationError as e:
            console.print(f"[red]Authentication failed:[/red] {e}")
            sys.exit(2)

        # List repos
        console.print(f"Fetching repos for [bold]{account.slug}[/bold]...")
        repos = client.list_repos()

        # Apply filters
        repos, skipped = filter_excluded_repos(
            repos, account, lambda r: r["name"]
        )
        if skipped:
            console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

        repos = filter_repos_by_visibility(
            repos,
            account,
            is_private=lambda r: r.get("private", False),
            is_fork=lambda r: r.get("fork", False),
            get_name=lambda r: r["name"],
        )

        if project:
            repos, proj_skipped = filter_by_project(
                repos, project, account.slug, lambda r: r["name"]
            )
            if proj_skipped:
                console.print(
                    f"Filtered to [bold]{len(repos)}[/bold] repo(s) in project '{project.name}'."
                )

        total = len(repos)
        if total == 0:
            console.print("[yellow]No repositories found.[/yellow]")
            result = AccessResult(
                account_slug=account.slug,
                account_username=account.username,
            )
            return result

        console.print(f"Found [bold]{total}[/bold] repos. Fetching collaborators...")

        # Fetch collaborators per repo (threaded)
        access_repos: list[RepoAccess] = []
        with Progress(console=console) as progress:
            task = progress.add_task("Fetching collaborators", total=total)

            def fetch_one(repo: dict) -> RepoAccess:
                owner = repo["owner"]["login"]
                name = repo["name"]
                collabs = client.list_collaborators(owner, name)
                progress.advance(task)
                return RepoAccess(
                    repo_name=name,
                    repo_url=repo["html_url"],
                    private=repo.get("private", False),
                    collaborators=collabs,
                )

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(fetch_one, r) for r in repos]
                for future in as_completed(futures):
                    access_repos.append(future.result())

    result = AccessResult(
        account_slug=account.slug,
        account_username=account.username,
        repos=access_repos,
    )

    # Generate report
    report = generate_access_report(result, sort_by=sort_by)
    report_path = reports_dir / "access_report.md"
    report_path.write_text(report)
    console.print(f"\nReport written to [bold]{report_path}[/bold]")

    # Print per-repo tables to console
    _print_access_tables(result, sort_by)

    # Print terminal summary
    unique = result.unique_users
    repos_with_external = sum(
        1 for r in result.repos if len(r.collaborators) > 1
    )
    console.print()
    console.print("=" * 50)
    console.print(f"  ACCESS SUMMARY \u2014 {account.username}")
    console.print("=" * 50)
    console.print(f"  Total repos:               {len(result.repos)}")
    console.print(f"  Unique collaborators:      {len(unique)}")
    console.print(f"  Total access entries:      {result.total_access_entries}")
    console.print(f"  Repos with 2+ collabs:     {repos_with_external}")
    console.print("=" * 50)

    return result


@click.command("access")
@click.option("--account", "account_slug", help="Account slug")
@click.option("--all", "audit_all", is_flag=True, help="All configured accounts")
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["name", "id"], case_sensitive=False),
    default="name",
    help="Sort users by name (alphabetical) or id (ascending, lower = older account)",
)
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def access_cmd(ctx: click.Context, account_slug: str | None, audit_all: bool, sort_by: str, project_name: str | None):
    """Generate an access report showing who has access to which repos."""
    config: AppConfig = ctx.obj["lazy_config"].config

    project = None
    if project_name:
        project = get_project(config, project_name)
        if not audit_all and not account_slug:
            # --project alone: operate on all accounts in the project
            accounts = get_accounts_for_project(config, project)
            if not accounts:
                console.print(f"[yellow]No accounts have repos in project '{project_name}'[/yellow]")
                sys.exit(0)

    if project and not audit_all and not account_slug:
        pass  # accounts already set above
    elif audit_all:
        accounts = config.accounts
    elif account_slug:
        accounts = [get_account(config, account_slug)]
    else:
        console.print("[red]Specify --account SLUG or --all[/red]")
        sys.exit(1)

    for account in accounts:
        console.print(f"\n[bold blue]Access report: {account.slug}[/bold blue]")
        run_access_report(config, account, sort_by=sort_by, project=project)
