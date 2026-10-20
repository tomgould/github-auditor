from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from auditer.config import AppConfig, get_account
from auditer.filtering import filter_excluded_repos
from auditer.github_client import AuthenticationError, GitHubClient
from auditer.models import AccountConfig, ProfileData, RepoInfo
from auditer.reports.profile import generate_profile_readme

logger = logging.getLogger(__name__)
console = Console(soft_wrap=True, force_terminal=True)


def run_profile(config: AppConfig, account: AccountConfig) -> None:
    if account.provider == "bitbucket":
        console.print(
            "[red]GitHub profile READMEs are a GitHub-specific convention (a repo named "
            "exactly after your username, rendered on your profile page) — Bitbucket Cloud "
            "has no equivalent. This command is GitHub-only.[/red]"
        )
        sys.exit(1)

    reports_dir = Path(config.reports_dir) / account.slug
    reports_dir.mkdir(parents=True, exist_ok=True)

    with GitHubClient(account) as client:
        # Validate token if present
        if account.token:
            try:
                username = client.validate_token()
                logger.info(f"Authenticated as: {username}")
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        # Fetch user profile
        if account.token:
            user_data = client.get_authenticated_user()
        else:
            user_data = client.get_user(account.username)
            if user_data is None:
                console.print(f"[red]User '{account.username}' not found.[/red]")
                sys.exit(1)

        # Fetch repos
        console.print(f"Fetching repos for [bold]{account.slug}[/bold]...")
        repos = client.list_repos()
        repos, skipped = filter_excluded_repos(repos, account, lambda r: r["name"])
        if skipped:
            console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

        repo_infos = [RepoInfo.from_api_response(r) for r in repos]

        # Fetch events
        events = client.list_user_events(user_data["login"])

    # Build ProfileData
    profile = ProfileData(
        account_slug=account.slug,
        username=user_data["login"],
        name=user_data.get("name") or "",
        bio=user_data.get("bio") or "",
        location=user_data.get("location") or "",
        company=user_data.get("company") or "",
        blog=user_data.get("blog") or "",
        twitter_username=user_data.get("twitter_username") or "",
        avatar_url=user_data.get("avatar_url") or "",
        html_url=user_data.get("html_url") or f"https://github.com/{user_data['login']}",
        public_repos_count=user_data.get("public_repos", 0),
        public_gists=user_data.get("public_gists", 0),
        followers=user_data.get("followers", 0),
        following=user_data.get("following", 0),
        repos=repo_infos,
        events=events,
    )

    # Generate report
    report = generate_profile_readme(profile)
    report_path = reports_dir / "profile_readme.md"
    report_path.write_text(report)
    console.print(f"Report written to [bold]{report_path}[/bold]")

    # Terminal summary
    top_langs = profile.language_breakdown[:3]
    lang_str = ", ".join(f"{lang} ({pct}%)" for lang, _, pct in top_langs) if top_langs else "none"
    console.print()
    console.print("=" * 50)
    console.print(f"  PROFILE SUMMARY \u2014 {profile.name or profile.username}")
    console.print("=" * 50)
    console.print(f"  Repos:       {len(profile.repos)}")
    console.print(f"  Stars:       {profile.total_stars}")
    console.print(f"  Forks:       {profile.total_forks}")
    console.print(f"  Followers:   {profile.followers}")
    console.print(f"  Top langs:   {lang_str}")
    console.print("=" * 50)


@click.command("profile")
@click.option("--account", "account_slug", help="Account slug")
@click.option("--all", "run_all", is_flag=True, help="All configured accounts")
@click.pass_context
def profile_cmd(ctx: click.Context, account_slug: str | None, run_all: bool):
    """Generate a GitHub profile README."""
    config: AppConfig = ctx.obj["lazy_config"].config

    if run_all:
        accounts = config.accounts
    elif account_slug:
        accounts = [get_account(config, account_slug)]
    else:
        console.print("[red]Specify --account SLUG or --all[/red]")
        sys.exit(1)

    for account in accounts:
        console.print(f"\n[bold blue]Profile: {account.slug}[/bold blue]")
        run_profile(config, account)
