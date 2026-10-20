from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from auditer import __version__
from auditer.config import ConfigError, load_config
from auditer.utils.logging import setup_logging

console = Console(soft_wrap=True, force_terminal=True)


class LazyConfig:
    """Loads config on first access so --help works without a config file."""

    def __init__(self, config_path: Path | None):
        self._config_path = config_path
        self._config = None
        self._loaded = False

    @property
    def config(self):
        if not self._loaded:
            try:
                self._config = load_config(self._config_path)
            except ConfigError as e:
                console.print(f"[red]Config error:[/red] {e}")
                sys.exit(1)
            self._loaded = True
        return self._config


class AuditerGroup(click.Group):
    """Custom group that catches ConfigError for friendly error messages."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except ConfigError as e:
            console.print(f"[red]Config error:[/red] {e}")
            suggested = getattr(e, "suggested_slug", None)
            bad = getattr(e, "bad_slug", None)
            if suggested and bad:
                args = sys.argv[1:]
                corrected = [suggested if a == bad else a for a in args]
                console.print(
                    f"[dim]Did you mean: [bold]auditer {' '.join(corrected)}[/bold][/dim]"
                )
            sys.exit(1)


@click.group(cls=AuditerGroup)
@click.version_option(version=__version__, prog_name="auditer")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    help="Path to config.yaml (default: ./config.yaml)",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, verbose: bool):
    """GitHub repository auditer and management tool."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["lazy_config"] = LazyConfig(config_path)
    ctx.obj["verbose"] = verbose

    log_level = "DEBUG" if verbose else "INFO"
    setup_logging(level=log_level, verbose=verbose)


# Register commands
from auditer.commands.data.activity import activity_cmd  # noqa: E402
from auditer.commands.people.access import access_cmd  # noqa: E402
from auditer.commands.repo.add_license import add_license_cmd  # noqa: E402
from auditer.commands.repo.archive import archive_cmd  # noqa: E402
from auditer.commands.data.audit import audit_cmd  # noqa: E402
from auditer.commands.data.checkout import checkout_cmd  # noqa: E402
from auditer.commands.people.collaborators import collaborators_cmd  # noqa: E402
from auditer.commands.meta.config_cmd import config_cmd  # noqa: E402
from auditer.commands.people.members import members_cmd  # noqa: E402
from auditer.commands.content.profile import profile_cmd  # noqa: E402
from auditer.commands.content.readme import readme_cmd  # noqa: E402
from auditer.commands.repo.settings import settings_cmd  # noqa: E402
from auditer.commands.repo.topics import topics_cmd  # noqa: E402

cli.add_command(access_cmd)
cli.add_command(activity_cmd)
cli.add_command(add_license_cmd)
cli.add_command(archive_cmd)
cli.add_command(audit_cmd)
cli.add_command(checkout_cmd)
cli.add_command(collaborators_cmd)
cli.add_command(config_cmd)
cli.add_command(members_cmd)
cli.add_command(profile_cmd)
cli.add_command(readme_cmd)
cli.add_command(settings_cmd)
cli.add_command(topics_cmd)
