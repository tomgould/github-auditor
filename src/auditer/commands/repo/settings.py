from __future__ import annotations

import sys

import click
from rich.console import Console

from auditer.config import AppConfig, get_account, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError
from auditer.models import ProjectConfig

console = Console(soft_wrap=True, force_terminal=True)


@click.command("settings")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo")
@click.option("--all", "update_all", is_flag=True, help="Update all repos")
@click.option("--description", default=None, help="Repository description")
@click.option("--homepage", default=None, help="Repository homepage URL")
@click.option("--has-issues", "has_issues", type=bool, default=None, help="Enable/disable issues")
@click.option("--has-wiki", "has_wiki", type=bool, default=None, help="Enable/disable wiki")
@click.option("--has-projects", "has_projects", type=bool, default=None, help="Enable/disable projects")
@click.option("--default-branch", "default_branch", default=None, help="Default branch name")
@click.option(
    "--visibility",
    type=click.Choice(["public", "private"]),
    default=None,
    help="Repository visibility",
)
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def settings_cmd(
    ctx: click.Context,
    account_slug: str,
    repo_name: str | None,
    update_all: bool,
    description: str | None,
    homepage: str | None,
    has_issues: bool | None,
    has_wiki: bool | None,
    has_projects: bool | None,
    default_branch: str | None,
    visibility: str | None,
    project_name: str | None,
):
    """Update repository settings."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)

    project: ProjectConfig | None = None
    if project_name:
        project = get_project(config, project_name)

    if not repo_name and not update_all:
        console.print("[red]Specify --repo NAME or --all[/red]")
        sys.exit(1)

    # Build settings dict from non-None options
    settings: dict = {}
    if description is not None:
        settings["description"] = description
    if homepage is not None:
        settings["homepage"] = homepage
    if has_issues is not None:
        settings["has_issues"] = has_issues
    if has_wiki is not None:
        settings["has_wiki"] = has_wiki
    if has_projects is not None:
        settings["has_projects"] = has_projects
        if account.provider == "bitbucket":
            console.print(
                "[yellow]Warning:[/yellow] --has-projects is ignored for Bitbucket "
                "accounts (Bitbucket has no Projects-visibility concept)."
            )
    if default_branch is not None:
        settings["default_branch"] = default_branch
    if visibility is not None:
        settings["visibility"] = visibility

    if not settings:
        console.print("[red]No settings specified. Use --description, --homepage, etc.[/red]")
        sys.exit(1)

    # Confirm for bulk or visibility changes
    if update_all:
        click.confirm(
            f"Update settings on ALL repos for '{account.username}'?",
            abort=True,
        )
    elif visibility is not None:
        click.confirm(
            f"Change visibility of {account.username}/{repo_name} to '{visibility}'?",
            abort=True,
        )

    with get_provider(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        if repo_name:
            client.update_repo(account.username, repo_name, **settings)
            console.print(
                f"[green]Updated settings for {account.username}/{repo_name}.[/green]"
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

            updated = 0
            for repo in repos:
                name = repo["name"]
                client.update_repo(account.username, name, **settings)
                updated += 1

            console.print(f"[green]Updated settings for {updated} repo(s).[/green]")
