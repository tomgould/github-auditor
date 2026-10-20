from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from auditer.config import AppConfig, get_account
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError, GitHubClient

console = Console(soft_wrap=True, force_terminal=True)


@click.group("members")
def members_cmd():
    """List and manage organisation members."""


@members_cmd.command("list")
@click.option("--account", "account_slug", default=None, help="Account slug (org only)")
@click.option("--all", "list_all", is_flag=True, help="List members for all org accounts")
@click.pass_context
def members_list(ctx: click.Context, account_slug: str | None, list_all: bool):
    """List organisation members."""
    config: AppConfig = ctx.obj["lazy_config"].config

    if list_all:
        accounts = [
            a for a in config.accounts if a.type == "org" or a.provider == "bitbucket"
        ]
        if not accounts:
            console.print("[yellow]No org-type or Bitbucket accounts configured.[/yellow]")
            return
    elif account_slug:
        accounts = [get_account(config, account_slug)]
    else:
        console.print("[red]Specify --account SLUG or --all[/red]")
        sys.exit(1)

    for account in accounts:
        is_bitbucket = account.provider == "bitbucket"
        if not is_bitbucket and account.type != "org":
            console.print(
                f"[red]Account '{account.slug}' is type '{account.type}', "
                f"not 'org'. Members are only available for organisations.[/red]\n"
                f"[dim]Hint: To manage repo-level access, use "
                f"[bold]auditer collaborators list --account {account.slug}[/bold][/dim]"
            )
            continue

        # Bitbucket accounts are always workspace-scoped (no GitHub-style
        # user-vs-org distinction) — the workspace slug, not account.username,
        # is what identifies the org for the members API call.
        org_identifier = account.workspace if is_bitbucket else account.username

        with get_provider(account) as client:
            if account.token:
                try:
                    client.validate_token()
                except AuthenticationError as e:
                    console.print(f"[red]Authentication failed:[/red] {e}")
                    sys.exit(2)

            members = client.list_org_members(org_identifier)

            table = Table(title=f"Members — {org_identifier}")
            table.add_column("Login")
            table.add_column("ID", justify="right")
            for member in members:
                table.add_row(member["login"], str(member["id"]))
            console.print(table)
            console.print(f"Total: [bold]{len(members)}[/bold] member(s)")


@members_cmd.command("remove")
@click.argument("username")
@click.option("--account", "account_slug", required=True, help="Account slug (org only)")
@click.pass_context
def members_remove(ctx: click.Context, username: str, account_slug: str):
    """Remove a member from an organisation."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)

    if account.provider == "bitbucket":
        console.print(
            f"[red]Account '{account.slug}' is a Bitbucket account — removing "
            f"workspace members is not supported.[/red]\n"
            f"[dim]Bitbucket Cloud's REST API has no endpoint for removing "
            f"workspace members — membership changes go through Atlassian "
            f"Admin or SCIM only, not a documented API call — so this cannot "
            f"be implemented via API today.[/dim]"
        )
        sys.exit(1)

    if account.type != "org":
        console.print(
            f"[red]Account '{account.slug}' is type '{account.type}', "
            f"not 'org'. Members are only available for organisations.[/red]\n"
            f"[dim]Hint: To remove a user from repos, use "
            f"[bold]auditer collaborators remove {username} --account {account.slug} --all-repos[/bold][/dim]"
        )
        sys.exit(1)

    click.confirm(
        f"Remove '{username}' from organisation '{account.username}'?",
        abort=True,
    )

    with GitHubClient(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        success = client.remove_org_member(account.username, username)

    if success:
        console.print(f"[green]Removed '{username}' from {account.username}.[/green]")
    else:
        console.print(
            f"[yellow]User '{username}' not found in {account.username}.[/yellow]"
        )
