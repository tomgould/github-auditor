from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from auditer.config import AppConfig, ConfigError, load_config
from auditer.utils.security import mask_token

console = Console(soft_wrap=True, force_terminal=True)


@click.command("config")
@click.option("--validate", is_flag=True, help="Validate config.yaml structure")
@click.option("--show", is_flag=True, help="Display config with masked tokens")
@click.pass_context
def config_cmd(ctx: click.Context, validate: bool, show: bool):
    """Validate or display the configuration."""
    config_path = ctx.obj.get("config_path")

    if validate:
        try:
            config = load_config(config_path)
            console.print(f"[green]Config is valid.[/green] {len(config.accounts)} account(s) configured.")
            if config.projects:
                console.print(f"  {len(config.projects)} project(s) configured.")
        except ConfigError as e:
            console.print(f"[red]Config error:[/red] {e}")
            sys.exit(1)
        return

    if show:
        try:
            config: AppConfig = load_config(config_path)
        except ConfigError as e:
            console.print(f"[red]Config error:[/red] {e}")
            sys.exit(1)

        console.print(f"\n[bold]Defaults[/bold]")
        console.print(f"  stale_months: {config.stale_months}")
        console.print(f"  data_dir:     {config.data_dir}")
        console.print(f"  reports_dir:  {config.reports_dir}")
        console.print(f"  log_level:    {config.log_level}")
        console.print()

        table = Table(title="Accounts")
        table.add_column("Slug", style="bold")
        table.add_column("Type")
        table.add_column("Username")
        table.add_column("Token")
        table.add_column("Private")
        table.add_column("Forks")
        table.add_column("Data Dir")
        table.add_column("Included")
        table.add_column("Excluded")

        for acct in config.accounts:
            included = ", ".join(acct.include_repos) if acct.include_repos else "-"
            excluded = ", ".join(acct.exclude_repos) if acct.exclude_repos else "-"
            table.add_row(
                acct.slug,
                acct.type,
                acct.username,
                mask_token(acct.token),
                str(acct.include_private),
                str(acct.include_forks),
                acct.data_dir or "-",
                included,
                excluded,
            )

        console.print(table)

        if config.projects:
            console.print()
            proj_table = Table(title="Projects")
            proj_table.add_column("Name", style="bold")
            proj_table.add_column("Description")
            proj_table.add_column("Repos")
            for proj in config.projects:
                proj_table.add_row(
                    proj.name,
                    proj.description or "-",
                    str(len(proj.repos)),
                )
            console.print(proj_table)
        return

    # No flag specified
    console.print("Use --validate or --show. See --help for details.")
