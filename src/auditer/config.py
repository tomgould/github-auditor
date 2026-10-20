from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from auditer.models import AccountConfig, ProjectConfig
from auditer.timewindow import DEFAULT_TIMEZONE

DEFAULT_CONFIG_PATH = Path("config.yaml")
EXAMPLE_CONFIG_PATH = Path("config.example.yaml")


@dataclass
class AppConfig:
    stale_months: int
    data_dir: str
    reports_dir: str
    log_level: str
    accounts: list[AccountConfig]
    projects: list[ProjectConfig] = field(default_factory=list)
    # GitHub login → other names/emails the same person commits under.
    # Used by `activity` to fold e.g. "Mia Lindberg" (git author name, no
    # noreply email) into the miaLind login row.
    developer_aliases: dict[str, list[str]] = field(default_factory=dict)
    # IANA timezone that `activity` measures whole-day windows in:
    # --since 2026-10-10 means 10 Oct 00:00 in this zone.
    timezone: str = DEFAULT_TIMEZONE


class ConfigError(Exception):
    pass


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise ConfigError(
            f"Config file not found: {config_path}\n"
            f"Copy {EXAMPLE_CONFIG_PATH} to {DEFAULT_CONFIG_PATH} and fill in your details."
        )

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not data:
        raise ConfigError(f"Config file is empty: {config_path}")

    return load_config_from_dict(data)


def load_config_from_dict(data: dict) -> AppConfig:
    defaults = data.get("defaults", {})
    accounts_data = data.get("accounts")

    if not accounts_data:
        raise ConfigError("Config must contain at least one account in 'accounts'.")

    accounts = []
    for i, acct in enumerate(accounts_data):
        missing = [k for k in ("slug", "type", "username") if not acct.get(k)]
        if missing:
            raise ConfigError(
                f"Account #{i + 1} is missing required fields: {', '.join(missing)}"
            )

        provider = acct.get("provider", "github")
        workspace = acct.get("workspace")
        auth_email = acct.get("auth_email")

        if provider not in ("github", "bitbucket"):
            raise ConfigError(
                f"Account '{acct['slug']}': provider must be 'github' or 'bitbucket', "
                f"got '{provider}'"
            )

        if provider == "bitbucket":
            if not workspace:
                raise ConfigError(
                    f"Account '{acct['slug']}': 'workspace' is required for bitbucket accounts"
                )
            if not auth_email:
                raise ConfigError(
                    f"Account '{acct['slug']}': 'auth_email' is required for bitbucket accounts"
                )
        else:
            if acct["type"] not in ("user", "org"):
                raise ConfigError(
                    f"Account '{acct['slug']}': type must be 'user' or 'org', got '{acct['type']}'"
                )

        accounts.append(
            AccountConfig(
                slug=acct["slug"],
                type=acct["type"],
                username=acct["username"],
                token=acct.get("token"),
                include_private=acct.get("include_private", True),
                include_forks=acct.get("include_forks", True),
                exclude_repos=acct.get("exclude_repos") or [],
                include_repos=acct.get("include_repos") or [],
                data_dir=acct.get("data_dir"),
                branch_overrides=acct.get("branch_overrides") or {},
                provider=provider,
                workspace=workspace,
                auth_email=auth_email,
                active_within_months=acct.get("active_within_months"),
                local_only=bool(acct.get("local_only", False)),
            )
        )

    # Parse projects
    account_slugs = {a.slug for a in accounts}
    projects: list[ProjectConfig] = []
    projects_data = data.get("projects") or {}
    for proj_name, proj_data in projects_data.items():
        proj_data = proj_data or {}
        repos = proj_data.get("repos") or []
        for repo_ref in repos:
            if "/" not in repo_ref or repo_ref.count("/") != 1:
                raise ConfigError(
                    f"Project '{proj_name}': repo '{repo_ref}' must be in 'slug/repo-name' format"
                )
            slug = repo_ref.split("/")[0]
            if slug not in account_slugs:
                raise ConfigError(
                    f"Project '{proj_name}': repo '{repo_ref}' references unknown account slug '{slug}'"
                )
        proj_accounts = proj_data.get("accounts") or []
        for acct_slug in proj_accounts:
            if acct_slug not in account_slugs:
                raise ConfigError(
                    f"Project '{proj_name}': account '{acct_slug}' not found in accounts"
                )
        projects.append(ProjectConfig(
            name=proj_name,
            description=proj_data.get("description") or "",
            repos=repos,
            accounts=proj_accounts,
        ))

    # Parse developer aliases: {login: [name or email, ...]}
    aliases: dict[str, list[str]] = {}
    aliases_data = data.get("developer_aliases") or {}
    if not isinstance(aliases_data, dict):
        raise ConfigError("'developer_aliases' must be a mapping of login -> list of names/emails")
    for login, names in aliases_data.items():
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
            raise ConfigError(
                f"developer_aliases['{login}'] must be a list of non-empty names or emails"
            )
        aliases[str(login)] = [n.strip() for n in names]

    tz_name = defaults.get("timezone") or DEFAULT_TIMEZONE
    try:
        ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ConfigError(
            f"defaults.timezone '{tz_name}' is not a known IANA timezone "
            f"(e.g. Europe/London)"
        ) from e

    return AppConfig(
        stale_months=defaults.get("stale_months", 12),
        data_dir=defaults.get("data_dir", "data"),
        reports_dir=defaults.get("reports_dir", "reports"),
        log_level=defaults.get("log_level", "INFO"),
        accounts=accounts,
        projects=projects,
        developer_aliases=aliases,
        timezone=str(tz_name),
    )


def get_account(config: AppConfig, slug: str) -> AccountConfig:
    for acct in config.accounts:
        if acct.slug == slug:
            return acct
    slugs = [a.slug for a in config.accounts]
    available = ", ".join(slugs)
    close = difflib.get_close_matches(slug, slugs, n=1, cutoff=0.6)
    err = ConfigError(f"Account '{slug}' not found. Available: {available}")
    err.bad_slug = slug
    err.suggested_slug = close[0] if close else None
    raise err


def get_accounts_for_project(
    config: AppConfig, project: ProjectConfig
) -> list[AccountConfig]:
    """Return accounts that have at least one repo in the project."""
    slugs_in_project = set(project.accounts)
    slugs_in_project.update(
        entry.split("/")[0] for entry in project.repos if "/" in entry
    )
    return [a for a in config.accounts if a.slug in slugs_in_project]


def get_project(config: AppConfig, name: str) -> ProjectConfig:
    for proj in config.projects:
        if proj.name == name:
            return proj
    names = [p.name for p in config.projects]
    available = ", ".join(names) if names else "(none)"
    close = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    err = ConfigError(f"Project '{name}' not found. Available: {available}")
    err.suggested_project = close[0] if close else None
    raise err
