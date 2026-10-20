from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from auditer.config import AppConfig, get_account, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.github_client import AuthenticationError, GitHubClient
from auditer.models import ProjectConfig, RepoInfo

console = Console(soft_wrap=True, force_terminal=True)


@click.command("archive")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Archive a specific repo")
@click.option("--months", type=int, default=None, help="Staleness threshold (overrides config)")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def archive_cmd(
    ctx: click.Context,
    account_slug: str,
    repo_name: str | None,
    months: int | None,
    project_name: str | None,
):
    """Archive stale repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)
    if account.provider == "bitbucket":
        console.print(
            "[red]Bitbucket Cloud has no repository archived-state concept — "
            "this command is GitHub-only.[/red]"
        )
        sys.exit(1)
    stale_months = months if months is not None else config.stale_months

    project: ProjectConfig | None = None
    if project_name:
        project = get_project(config, project_name)

    with GitHubClient(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        if repo_name:
            # Archive a specific repo regardless of staleness
            click.confirm(
                f"Archive '{account.username}/{repo_name}'?",
                abort=True,
            )
            client.update_repo(account.username, repo_name, archived=True)
            console.print(
                f"[green]Archived {account.username}/{repo_name}.[/green]"
            )
        else:
            repos = client.list_repos()
            repos, skipped = filter_excluded_repos(
                repos, account, lambda r: r["name"]
            )
            if skipped:
                console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

            if project:
                repos, proj_skipped = filter_by_project(
                    repos, project, account.slug, lambda r: r["name"]
                )
                if proj_skipped:
                    console.print(
                        f"Filtered to [bold]{len(repos)}[/bold] repo(s) in project '{project.name}'."
                    )

            # Find stale, non-archived candidates
            candidates = []
            for repo_data in repos:
                if repo_data.get("archived", False):
                    continue
                info = RepoInfo.from_api_response(repo_data)
                if info.is_stale(stale_months):
                    candidates.append((repo_data["name"], info))

            if not candidates:
                console.print(
                    f"[green]No stale repos found (threshold: {stale_months} months).[/green]"
                )
                return

            # Show candidates
            table = Table(title=f"Stale repos (>{stale_months} months) — {account.username}")
            table.add_column("Repo")
            table.add_column("Inactive", justify="right")
            table.add_column("Last Push")
            for name, info in candidates:
                table.add_row(
                    name,
                    f"{info.months_inactive:.0f} mo",
                    info.pushed_at[:10] if info.pushed_at else "unknown",
                )
            console.print(table)

            click.confirm(
                f"Archive {len(candidates)} repo(s)?",
                abort=True,
            )

            archived = 0
            for name, _ in candidates:
                client.update_repo(account.username, name, archived=True)
                archived += 1

            console.print(f"[green]Archived {archived} repo(s).[/green]")
