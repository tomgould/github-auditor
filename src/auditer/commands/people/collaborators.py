from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from auditer.config import AppConfig, get_account
from auditer.filtering import filter_excluded_repos
from auditer.git_provider import GitProvider, get_provider
from auditer.github_client import AuthenticationError, GitHubClient

console = Console(soft_wrap=True, force_terminal=True)


@click.group("collaborators")
def collaborators_cmd():
    """List, add, and remove repository collaborators."""


@collaborators_cmd.command("list")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo (default: all repos)")
@click.pass_context
def collaborators_list(ctx: click.Context, account_slug: str, repo_name: str | None):
    """List collaborators on repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)

    with get_provider(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        if repo_name:
            _show_collaborators(client, account.username, repo_name)
        else:
            repos = client.list_repos()
            repos, skipped = filter_excluded_repos(
                repos, account, lambda r: r["name"]
            )
            if skipped:
                console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

            for repo in repos:
                _show_collaborators(client, account.username, repo["name"])


def _show_collaborators(client: GitProvider, owner: str, repo_name: str):
    collabs = client.list_collaborators(owner, repo_name)
    table = Table(title=f"Collaborators — {owner}/{repo_name}")
    table.add_column("Login")
    table.add_column("Permissions")
    for c in collabs:
        perms = c.get("permissions", {})
        perm_str = ", ".join(k for k, v in perms.items() if v)
        table.add_row(c["login"], perm_str)
    console.print(table)


@collaborators_cmd.command("add")
@click.argument("username")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo")
@click.option("--all-repos", "all_repos", is_flag=True, help="Add to all repos")
@click.option(
    "--permission",
    type=click.Choice(["pull", "push", "admin", "maintain", "triage"]),
    default="push",
    help="Permission level (default: push)",
)
@click.pass_context
def collaborators_add(
    ctx: click.Context,
    username: str,
    account_slug: str,
    repo_name: str | None,
    all_repos: bool,
    permission: str,
):
    """Add a collaborator to repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)

    if account.provider == "bitbucket":
        console.print(
            "[red]'collaborators add' is not implemented for Bitbucket accounts.[/red]\n"
            "Bitbucket's per-repo permission-grant API requires the target user to "
            "already be a member of the workspace (there is no GitHub-style 'invite an "
            "outside collaborator' flow), may return 402 Payment Required on Bitbucket's "
            "Free plan tier, and may only support legacy App Password auth rather than "
            "the API token auth this tool uses — so it needs verification against a live "
            "Bitbucket workspace before it can be built."
        )
        sys.exit(1)

    if not repo_name and not all_repos:
        console.print("[red]Specify --repo NAME or --all-repos[/red]")
        sys.exit(1)

    with GitHubClient(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        if repo_name:
            success = client.add_collaborator(
                account.username, repo_name, username, permission
            )
            if success:
                console.print(
                    f"[green]Added '{username}' to {account.username}/{repo_name} "
                    f"with '{permission}' permission.[/green]"
                )
            else:
                console.print(f"[red]Failed to add '{username}'.[/red]")
        else:
            click.confirm(
                f"Add '{username}' to ALL repos for '{account.username}'?",
                abort=True,
            )
            repos = client.list_repos()
            repos, skipped = filter_excluded_repos(
                repos, account, lambda r: r["name"]
            )
            if skipped:
                console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

            added = 0
            for repo in repos:
                name = repo["name"]
                client.add_collaborator(account.username, name, username, permission)
                added += 1

            console.print(
                f"[green]Added '{username}' to {added} repo(s) "
                f"with '{permission}' permission.[/green]"
            )


@collaborators_cmd.command("remove")
@click.argument("username")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo")
@click.option("--all-repos", "all_repos", is_flag=True, help="Remove from all repos")
@click.pass_context
def collaborators_remove(
    ctx: click.Context,
    username: str,
    account_slug: str,
    repo_name: str | None,
    all_repos: bool,
):
    """Remove a collaborator from repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)

    if account.provider == "bitbucket":
        console.print(
            "[red]'collaborators remove' is not implemented for Bitbucket accounts.[/red]\n"
            "Bitbucket's per-repo permission-grant API requires the target user to "
            "already be a member of the workspace (there is no GitHub-style 'invite an "
            "outside collaborator' flow), may return 402 Payment Required on Bitbucket's "
            "Free plan tier, and may only support legacy App Password auth rather than "
            "the API token auth this tool uses — so it needs verification against a live "
            "Bitbucket workspace before it can be built."
        )
        sys.exit(1)

    if not repo_name and not all_repos:
        console.print("[red]Specify --repo NAME or --all-repos[/red]")
        sys.exit(1)

    with GitHubClient(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        if repo_name:
            success = client.remove_collaborator(account.username, repo_name, username)
            if success:
                console.print(
                    f"[green]Removed '{username}' from "
                    f"{account.username}/{repo_name}.[/green]"
                )
            else:
                console.print(
                    f"[yellow]'{username}' not found on "
                    f"{account.username}/{repo_name}.[/yellow]"
                )
        else:
            click.confirm(
                f"Remove '{username}' from ALL repos for '{account.username}'?",
                abort=True,
            )
            repos = client.list_repos()
            repos, skipped = filter_excluded_repos(
                repos, account, lambda r: r["name"]
            )
            if skipped:
                console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

            removed = 0
            not_found = 0
            for repo in repos:
                name = repo["name"]
                success = client.remove_collaborator(
                    account.username, name, username
                )
                if success:
                    removed += 1
                else:
                    not_found += 1

            console.print(
                f"[green]Removed '{username}' from {removed} repo(s).[/green] "
                f"{not_found} not found."
            )
