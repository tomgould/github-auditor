from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from auditer.config import AppConfig, get_account, get_project
from auditer.filtering import filter_by_project, filter_excluded_repos
from auditer.git_ops import scan_local_repos
from auditer.git_provider import get_provider
from auditer.github_client import AuthenticationError
from auditer.models import AccountConfig, ProjectConfig, ReadmeData
from auditer.reports.readme import generate_repo_readme, scan_repo

logger = logging.getLogger(__name__)
console = Console(soft_wrap=True, force_terminal=True)


def run_readme(
    config: AppConfig,
    account: AccountConfig,
    repo_name: str | None = None,
    run_all: bool = False,
    force: bool = False,
    push: bool = False,
    project: ProjectConfig | None = None,
) -> None:
    if project:
        reports_dir = Path(config.reports_dir) / project.name / "readmes"
    else:
        reports_dir = Path(config.reports_dir) / account.slug / "readmes"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Resolve data_dir
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
        return

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

    if repo_name:
        # Single repo mode
        match = [r for r in local_repos if r.name == repo_name]
        if not match:
            console.print(f"[red]Repo '{repo_name}' not found in local checkouts.[/red]")
            console.print(f"Available: {', '.join(r.name for r in local_repos[:20])}")
            sys.exit(1)
        targets = match
    else:
        # All mode — filter to repos missing README unless --force
        if not force:
            targets = [r for r in local_repos if not r.has_readme]
            skipped_readme = len(local_repos) - len(targets)
            if skipped_readme:
                console.print(
                    f"Skipping [bold]{skipped_readme}[/bold] repo(s) that already have a README. "
                    f"Use --force to generate anyway."
                )
        else:
            targets = local_repos

    if not targets:
        console.print("[green]All repos already have READMEs. Nothing to generate.[/green]")
        return

    # Confirm before pushing
    if push:
        if not account.token:
            console.print("[red]--push requires an authenticated account (token required).[/red]")
            sys.exit(1)
        target_names = ", ".join(t.name for t in targets[:10])
        if len(targets) > 10:
            target_names += f", ... ({len(targets)} total)"
        click.confirm(
            f"Push README to {len(targets)} repo(s) on GitHub? [{target_names}]",
            abort=True,
        )

    console.print(f"Generating READMEs for [bold]{len(targets)}[/bold] repo(s)...")

    generated = 0
    pushed = 0
    with get_provider(account) as client:
        if account.token:
            try:
                username = client.validate_token()
                logger.info(f"Authenticated as: {username}")
            except AuthenticationError as e:
                console.print(f"[red]Authentication failed:[/red] {e}")
                sys.exit(2)

        for lr in targets:
            # Scan local structure
            scan_result = scan_repo(lr.path)

            # Enrich via API
            api_data = client.get_repo(lr.owner, lr.repo_slug)
            if api_data:
                description = api_data.get("description") or ""
                language = api_data.get("language") or ""
                topics = api_data.get("topics", [])
                license_val = (
                    api_data.get("license", {}).get("spdx_id", "")
                    if api_data.get("license")
                    else ""
                )
                url = api_data.get("html_url", f"https://github.com/{lr.owner}/{lr.name}")
                is_fork = api_data.get("fork", False)
                default_branch = api_data.get("default_branch", lr.default_branch)
                stars = api_data.get("stargazers_count", 0)
                forks = api_data.get("forks_count", 0)
            else:
                description = ""
                language = ""
                topics = []
                license_val = ""
                url = f"https://github.com/{lr.owner}/{lr.name}"
                is_fork = False
                default_branch = lr.default_branch
                stars = 0
                forks = 0

            # Merge framework and key_dependencies into package_info
            pkg_info = scan_result["package_info"].copy() if scan_result["package_info"] else {}
            if scan_result.get("framework"):
                pkg_info["framework"] = scan_result["framework"]
            if scan_result.get("key_dependencies"):
                pkg_info["key_dependencies"] = scan_result["key_dependencies"]

            readme_data = ReadmeData(
                name=lr.name,
                owner=lr.owner,
                url=url,
                description=description,
                language=language,
                topics=topics,
                license=license_val,
                stars=stars,
                forks=forks,
                default_branch=default_branch,
                is_fork=is_fork,
                tree=scan_result["tree"],
                package_info=pkg_info,
                has_tests=scan_result["has_tests"],
                has_ci=scan_result["has_ci"],
                has_contributing=scan_result["has_contributing"],
                has_changelog=scan_result["has_changelog"],
                entry_points=scan_result["entry_points"],
                detected_languages=scan_result["detected_languages"],
                install_command=scan_result["install_command"],
                test_command=scan_result["test_command"],
            )

            report = generate_repo_readme(readme_data)

            # Always save locally
            report_path = reports_dir / f"{lr.name}.md"
            report_path.write_text(report)

            if push:
                # Check for existing README to get SHA for update
                existing = client.get_file(lr.owner, lr.repo_slug, "README.md")
                sha = existing["sha"] if existing else None
                action = "Update" if sha else "Add"
                client.create_or_update_file(
                    lr.owner,
                    lr.repo_slug,
                    "README.md",
                    report,
                    f"{action} README.md",
                    sha=sha,
                )
                console.print(f"  [green]✓[/green] {lr.name} → pushed to GitHub")
                pushed += 1
            else:
                console.print(f"  [green]✓[/green] {lr.name} → {report_path}")

            generated += 1

    # Terminal summary
    console.print()
    console.print("=" * 50)
    console.print(f"  README GENERATION — {account.slug}")
    console.print("=" * 50)
    console.print(f"  Generated:  {generated}")
    if push:
        console.print(f"  Pushed:     {pushed}")
    console.print(f"  Total repos: {len(local_repos)}")
    console.print("=" * 50)


@click.command("readme")
@click.argument("repo", required=False)
@click.option("--account", "account_slug", required=True, help="Account slug")
@click.option("--all", "run_all", is_flag=True, help="Generate for all repos missing a README")
@click.option("--force", is_flag=True, help="Generate even if README already exists")
@click.option("--push", is_flag=True, help="Push README directly to GitHub")
@click.option("--project", "project_name", default=None, help="Filter to repos in this project")
@click.pass_context
def readme_cmd(ctx: click.Context, repo: str | None, account_slug: str, run_all: bool, force: bool, push: bool, project_name: str | None):
    """Generate a README for a repository from its local checkout."""
    config: AppConfig = ctx.obj["lazy_config"].config

    if not repo and not run_all:
        console.print("[red]Specify a REPO argument or --all[/red]")
        sys.exit(1)

    project = None
    if project_name:
        project = get_project(config, project_name)

    account = get_account(config, account_slug)
    console.print(f"\n[bold blue]README: {account.slug}[/bold blue]")
    run_readme(config, account, repo_name=repo, run_all=run_all, force=force, push=push, project=project)
