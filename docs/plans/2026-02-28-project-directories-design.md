# Project Directories Design

## Problem

Repos are currently organised by account slug (`data/{slug}/public|private/{repo}`). Users with multiple accounts need to group repos across accounts into logical projects (e.g. "client-work" containing repos from both a personal and org account).

## Approach

Projects are **config-level logical views** over existing repos. The physical directory structure stays unchanged. A project is a named list of `slug/repo-name` references resolved at command time via a `--project` filter.

This avoids all migration, preserves existing symlinks from other apps, and allows a repo to appear in multiple projects.

## Config Schema

Optional top-level `projects` key in `config.yaml`:

```yaml
projects:
  client-work:
    description: "Client X web platform"  # optional
    repos:
      - my-user/web-app
      - my-org/api-service
      - my-org/shared-lib
  open-source:
    description: "My open source work"
    repos:
      - my-user/cool-project
      - famous-dev/awesome-lib
```

Rules:
- `projects` is entirely optional. Omitting it preserves current behaviour exactly.
- Repos referenced as `{account-slug}/{repo-name}`.
- Every slug must match an existing account in config (validated at load time).
- A repo can appear in multiple projects.
- No implicit "default" project is created.

## Data Model

New dataclass:

```python
@dataclass
class ProjectConfig:
    name: str
    description: str = ""
    repos: list[str] = field(default_factory=list)  # "slug/repo-name"
```

`AppConfig` gains:

```python
projects: list[ProjectConfig] = field(default_factory=list)
```

New helper in `config.py`:

```python
def get_project(config: AppConfig, name: str) -> ProjectConfig
```

With fuzzy matching, same pattern as `get_account()`.

## Filtering

New helper in `filtering.py`:

```python
def get_project_repos(project: ProjectConfig, account_slug: str) -> set[str]:
    """Return repo names from a project that belong to a given account."""
    prefix = f"{account_slug}/"
    return {entry[len(prefix):] for entry in project.repos if entry.startswith(prefix)}
```

Applied before existing `filter_excluded_repos` and `filter_repos_by_visibility`.

## CLI Changes

New `--project` option on these commands:
- checkout, audit, activity, readme, access, archive, add-license, topics list, settings

Behaviour:
- `--project` alone (no `--account`): operates on all accounts that have repos in the project.
- `--project` + `--account`: filters that account's repos to the project set.
- `--project` + `--all`: filters every account to its project repos.
- No `--project`: current behaviour, unchanged.

## Reports

When `--project` is active, reports write to `reports/{project-name}/` instead of `reports/{slug}/`.

## Config Command

- `--show`: displays a Projects section (name, description, repo count).
- `--validate`: checks all project repo references resolve to valid account slugs.

## Backward Compatibility

100%. No file moves. No directory changes. No existing behaviour altered. The `projects` key is optional.

## Commands NOT affected

Single-repo commands that don't iterate collections: `topics set`, `topics add --repo`, `collaborators add/remove`, `members remove`.
