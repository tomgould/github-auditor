from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from auditer.config import AppConfig, get_account, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.github_client import AuthenticationError, GitHubClient
from auditer.models import AccountConfig, ProjectConfig

console = Console(soft_wrap=True, force_terminal=True)


def _reject_bitbucket(account: AccountConfig) -> None:
    """Exit with an error if the account is a Bitbucket account (topics are GitHub-only)."""
    if account.provider == "bitbucket":
        console.print(
            "[red]Bitbucket Cloud has no topics/labels concept for repositories — "
            "this command is GitHub-only.[/red]"
        )
        sys.exit(1)


@click.group("topics")
def topics_cmd():
    """View and manage repository topics."""


@topics_cmd.command("list")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo (default: all repos)")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def topics_list(ctx: click.Context, account_slug: str, repo_name: str | None, project_name: str | None):
    """List topics for repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)
    _reject_bitbucket(account)

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
            topics = client.get_topics(account.username, repo_name)
            table = Table(title=f"Topics for {account.username}/{repo_name}")
            table.add_column("Topics")
            table.add_row(", ".join(topics) if topics else "(none)")
            console.print(table)
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

            table = Table(title=f"Topics — {account.username}")
            table.add_column("Repo")
            table.add_column("Topics")
            for repo in repos:
                name = repo["name"]
                topics = repo.get("topics", [])
                table.add_row(name, ", ".join(topics) if topics else "(none)")
            console.print(table)


@topics_cmd.command("set")
@click.argument("topic_list")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", required=True, help="Repository name")
@click.pass_context
def topics_set(ctx: click.Context, topic_list: str, account_slug: str, repo_name: str):
    """Replace all topics on a repository (comma-separated)."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)
    _reject_bitbucket(account)
    topics = [t.strip() for t in topic_list.split(",") if t.strip()]

    with GitHubClient(account) as client:
        if account.token:
            try:
                client.validate_token()
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        result = client.set_topics(account.username, repo_name, topics)
        console.print(
            f"[green]Set topics on {account.username}/{repo_name}:[/green] "
            f"{', '.join(result)}"
        )


@topics_cmd.command("add")
@click.argument("topic")
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--repo", "repo_name", default=None, help="Single repo (default: all repos)")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def topics_add(ctx: click.Context, topic: str, account_slug: str, repo_name: str | None, project_name: str | None):
    """Add a topic to repositories."""
    config: AppConfig = ctx.obj["lazy_config"].config
    account = get_account(config, account_slug)
    _reject_bitbucket(account)

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
            existing = client.get_topics(account.username, repo_name)
            if topic in existing:
                console.print(
                    f"[yellow]{account.username}/{repo_name} already has topic "
                    f"'{topic}' — skipping.[/yellow]"
                )
                return
            new_topics = existing + [topic]
            client.set_topics(account.username, repo_name, new_topics)
            console.print(
                f"[green]Added '{topic}' to {account.username}/{repo_name}[/green]"
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

            added = 0
            already = 0
            for repo in repos:
                name = repo["name"]
                existing = repo.get("topics", [])
                if topic in existing:
                    already += 1
                    continue
                new_topics = existing + [topic]
                client.set_topics(account.username, name, new_topics)
                added += 1

            console.print(
                f"[green]Added '{topic}' to {added} repo(s).[/green] "
                f"{already} already had it."
            )
