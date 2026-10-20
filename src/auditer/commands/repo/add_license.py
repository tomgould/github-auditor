from __future__ import annotations

import sys
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from auditer.config import AppConfig, get_account, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError
from auditer.licenses import LICENSES
from auditer.models import ProjectConfig

console = Console(soft_wrap=True, force_terminal=True)

LICENSE_CHOICES = list(LICENSES.keys())


@click.command("add-license")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo (default: all missing)")
@click.option(
    "--license",
    "license_key",
    required=True,
    type=click.Choice(LICENSE_CHOICES),
    help="License template to use",
)
@click.option("--owner-name", default=None, help="Copyright owner name (default: GitHub profile name)")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def add_license_cmd(
    ctx: click.Context,
    account_slug: str,
    repo_name: str | None,
    license_key: str,
    owner_name: str | None,
    project_name: str | None,
):
    """Add a LICENSE file to repositories missing one."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)

    project: ProjectConfig | None = None
    if project_name:
        project = get_project(config, project_name)

    with get_provider(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        # Resolve owner name
        if not owner_name:
            user = client.get_authenticated_user()
            owner_name = user.get("name") or user.get("login", account.username)

        year = datetime.now(timezone.utc).year
        license_text = LICENSES[license_key].format(year=year, owner=owner_name)

        if repo_name:
            existing = client.get_file(account.username, repo_name, "LICENSE")
            if existing:
                console.print(
                    f"[yellow]{account.username}/{repo_name} already has a LICENSE "
                    f"file — skipping.[/yellow]"
                )
                return

            click.confirm(
                f"Add {license_key} license to {account.username}/{repo_name}?",
                abort=True,
            )
            client.create_or_update_file(
                account.username,
                repo_name,
                "LICENSE",
                license_text,
                f"Add {license_key} license",
            )
            console.print(
                f"[green]Added {license_key} license to "
                f"{account.username}/{repo_name}.[/green]"
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

            # Find repos missing a LICENSE
            missing = []
            for repo_data in repos:
                name = repo_data["name"]
                existing = client.get_file(account.username, name, "LICENSE")
                if not existing:
                    missing.append(name)

            if not missing:
                console.print("[green]All repos already have a LICENSE file.[/green]")
                return

            table = Table(title=f"Repos missing LICENSE — {account.username}")
            table.add_column("Repo")
            for name in missing:
                table.add_row(name)
            console.print(table)

            click.confirm(
                f"Add {license_key} license to {len(missing)} repo(s)?",
                abort=True,
            )

            added = 0
            for name in missing:
                client.create_or_update_file(
                    account.username,
                    name,
                    "LICENSE",
                    license_text,
                    f"Add {license_key} license",
                )
                added += 1

            console.print(f"[green]Added {license_key} license to {added} repo(s).[/green]")
