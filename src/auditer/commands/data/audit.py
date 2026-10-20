from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress

from auditer.config import AppConfig, get_account, get_accounts_for_project, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.git_ops import scan_local_repos
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError
from auditer.models import AccountConfig, AuditResult, ProjectConfig, RepoInfo
from auditer.reports.markdown import generate_audit_report

logger = logging.getLogger(__name__)
console = Console(soft_wrap=True, force_terminal=True)


def run_audit(
    config: AppConfig,
    account: AccountConfig,
    project: ProjectConfig | None = None,
) -> AuditResult:
    if project:
        reports_dir = Path(config.reports_dir) / project.name
    else:
        reports_dir = Path(config.reports_dir) / account.slug
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Resolve data_dir same as checkout command
    base_dir = account.data_dir if account.data_dir else config.data_dir
    data_dir = Path(base_dir) / account.slug

    # Scan local checkouts
    console.print(f"Scanning local repos in [bold]{data_dir}[/bold]...")
    local_repos = scan_local_repos(data_dir)

    if not local_repos:
        console.print(
            f"[yellow]No local repos found in {data_dir}.[/yellow]\n"
            f"Run [bold]auditer checkout --account {account.slug}[/bold] first."
        )
        result = AuditResult(
            account_slug=account.slug,
            account_username=account.username,
            repos=[],
            stale_months=config.stale_months,
        )
        return result

    # Filter excluded repos
    local_repos, skipped = filter_excluded_repos(
        local_repos, account, lambda r: r.name
    )
    if skipped:
        console.print(f"Excluded [bold]{skipped}[/bold] repo(s) per config.")

    if project:
        local_repos, proj_skipped = filter_by_project(
            local_repos, project, account.slug, lambda r: r.name
        )
        if proj_skipped:
            console.print(
                f"Filtered to [bold]{len(local_repos)}[/bold] repo(s) in project '{project.name}'."
            )

    total = len(local_repos)
    console.print(f"Found [bold]{total}[/bold] local repos. Enriching via API...")

    with get_provider(account) as client:
        # Validate token if present
        if account.token:
            try:
                username = client.validate_token()
                logger.info(f"Authenticated as: {username}")
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        # Enrich each repo with API data (threaded)
        api_results: dict[str, dict | None] = {}
        with Progress(console=console) as progress:
            task = progress.add_task("Enriching repos", total=total)

            def enrich_one(owner: str, name: str, slug: str) -> tuple[str, dict | None]:
                # `slug` (not `name`) for the API call — Bitbucket's display
                # name can contain spaces/caps that differ from its actual
                # URL-safe slug, which 404s if used as the path segment.
                # `name` stays the dict key so downstream lookups by
                # lr.name (used for local directory/report display) work.
                data = client.get_repo(owner, slug)
                progress.advance(task)
                return name, data

            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [
                    pool.submit(enrich_one, lr.owner, lr.name, lr.repo_slug)
                    for lr in local_repos
                ]
                for future in as_completed(futures):
                    name, data = future.result()
                    api_results[name] = data

    # Build RepoInfo list
    repos: list[RepoInfo] = []
    unenriched: list[str] = []
    for lr in local_repos:
        api_data = api_results.get(lr.name)
        if api_data is None:
            unenriched.append(lr.name)
        repos.append(RepoInfo.from_local_and_api(lr, api_data))

    if unenriched:
        console.print(
            f"[yellow]Could not enrich {len(unenriched)} repo(s) "
            f"(deleted from GitHub?):[/yellow] {', '.join(unenriched)}"
        )

    result = AuditResult(
        account_slug=account.slug,
        account_username=account.username,
        repos=repos,
        stale_months=config.stale_months,
    )

    # Generate report
    report = generate_audit_report(result)
    report_path = reports_dir / "audit_report.md"
    report_path.write_text(report)
    console.print(f"\nReport written to [bold]{report_path}[/bold]")

    # Print summary
    console.print()
    console.print("=" * 50)
    console.print(f"  AUDIT SUMMARY \u2014 {account.username}")
    console.print("=" * 50)
    console.print(f"  Total repos:           {len(result.repos)}")
    console.print(f"    Public:              {len(result.public_repos)}")
    console.print(f"    Private:             {len(result.private_repos)}")
    console.print(f"  Missing README:        {len(result.missing_readme)}")
    console.print(f"  Missing License:       {len(result.missing_license)}")
    console.print(f"  Missing Description:   {len(result.missing_description)}")
    console.print(f"  Stale (>{config.stale_months}mo):         {len(result.stale_repos)}")
    console.print(f"  Average health:        {result.average_health}")
    console.print(f"  Low health (<50):      {len(result.low_health_repos)}")
    console.print("=" * 50)

    return result


@click.command("audit")
@click.option("--account", "account_slug", help="Account slug to audit")
@click.option("--all", "audit_all", is_flag=True, help="Audit all configured accounts")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def audit_cmd(ctx: click.Context, account_slug: str | None, audit_all: bool, project_name: str | None):
    """Audit repositories and generate a Markdown report."""
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
        console.print(f"\n[bold blue]Auditing account: {account.slug}[/bold blue]")
        run_audit(config, account, project=project)
