# Project Directories Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add config-level project groupings that let users scope CLI commands to a named subset of repos across accounts.

**Architecture:** Projects are logical views defined in `config.yaml` under a `projects:` key. Each project lists repos as `slug/repo-name`. A new `--project` CLI option filters repos at command time. Physical directory layout is unchanged.

**Tech Stack:** Python 3.10+, Click, dataclasses, pytest

**Design doc:** `docs/plans/2026-02-28-project-directories-design.md`

---

### Task 1: ProjectConfig dataclass

**Files:**
- Modify: `src/auditer/models.py:1-22` (add after AccountConfig)
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

In `tests/test_models.py`, add:

```python
from auditer.models import ProjectConfig

class TestProjectConfig:
    def test_defaults(self):
        p = ProjectConfig(name="my-project")
        assert p.name == "my-project"
        assert p.description == ""
        assert p.repos == []

    def test_with_repos(self):
        p = ProjectConfig(
            name="client-work",
            description="Client X platform",
            repos=["my-user/web-app", "my-org/api"],
        )
        assert p.description == "Client X platform"
        assert len(p.repos) == 2
        assert "my-user/web-app" in p.repos
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::TestProjectConfig -v`
Expected: FAIL — `ImportError: cannot import name 'ProjectConfig'`

**Step 3: Write minimal implementation**

In `src/auditer/models.py`, add after the `AccountConfig` dataclass (after line 21):

```python
@dataclass
class ProjectConfig:
    name: str
    description: str = ""
    repos: list[str] = field(default_factory=list)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py::TestProjectConfig -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/auditer/models.py tests/test_models.py
git commit -m "feat: add ProjectConfig dataclass"
```

---

### Task 2: AppConfig.projects field + config parsing

**Files:**
- Modify: `src/auditer/config.py:16-86` (AppConfig + load_config_from_dict)
- Modify: `src/auditer/models.py` (import in config.py)
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

In `tests/test_config.py`, add:

```python
class TestProjectsParsing:
    def test_no_projects_key(self, sample_config_dict):
        """Omitting projects is fine — backward compatible."""
        config = load_config_from_dict(sample_config_dict)
        assert config.projects == []

    def test_projects_parsed(self):
        data = {
            "accounts": [
                {"slug": "my-user", "type": "user", "username": "me"},
                {"slug": "my-org", "type": "org", "username": "org"},
            ],
            "projects": {
                "client-work": {
                    "description": "Client X",
                    "repos": ["my-user/web-app", "my-org/api"],
                },
            },
        }
        config = load_config_from_dict(data)
        assert len(config.projects) == 1
        assert config.projects[0].name == "client-work"
        assert config.projects[0].description == "Client X"
        assert config.projects[0].repos == ["my-user/web-app", "my-org/api"]

    def test_projects_multiple(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "proj1": {"repos": ["a/repo1"]},
                "proj2": {"repos": ["a/repo2"]},
            },
        }
        config = load_config_from_dict(data)
        assert len(config.projects) == 2
        names = {p.name for p in config.projects}
        assert names == {"proj1", "proj2"}

    def test_projects_invalid_slug_reference(self):
        data = {
            "accounts": [
                {"slug": "my-user", "type": "user", "username": "me"},
            ],
            "projects": {
                "bad": {"repos": ["nonexistent/repo"]},
            },
        }
        with pytest.raises(ConfigError, match="unknown account slug"):
            load_config_from_dict(data)

    def test_projects_empty_repos(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "empty": {"repos": []},
            },
        }
        config = load_config_from_dict(data)
        assert config.projects[0].repos == []

    def test_projects_description_optional(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "minimal": {"repos": ["a/repo1"]},
            },
        }
        config = load_config_from_dict(data)
        assert config.projects[0].description == ""

    def test_projects_invalid_repo_format(self):
        """Repo entries must contain exactly one slash."""
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "bad": {"repos": ["no-slash"]},
            },
        }
        with pytest.raises(ConfigError, match="must be in 'slug/repo-name' format"):
            load_config_from_dict(data)
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestProjectsParsing -v`
Expected: FAIL — `AppConfig` has no `projects` field

**Step 3: Write implementation**

In `src/auditer/config.py`:

1. Add import: `from auditer.models import AccountConfig, ProjectConfig`
2. Add `projects` field to `AppConfig`:
   ```python
   @dataclass
   class AppConfig:
       stale_months: int
       data_dir: str
       reports_dir: str
       log_level: str
       accounts: list[AccountConfig]
       projects: list[ProjectConfig] = field(default_factory=list)
   ```
   (Add `from dataclasses import dataclass, field` — already using `dataclass`, add `field`)

3. In `load_config_from_dict()`, after building `accounts`, add project parsing:
   ```python
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
       projects.append(ProjectConfig(
           name=proj_name,
           description=proj_data.get("description") or "",
           repos=repos,
       ))
   ```

4. Pass `projects=projects` to the `AppConfig(...)` constructor.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::TestProjectsParsing -v`
Expected: PASS

**Step 5: Run full config test suite**

Run: `pytest tests/test_config.py -v`
Expected: All pass (existing tests unaffected)

**Step 6: Commit**

```bash
git add src/auditer/config.py src/auditer/models.py tests/test_config.py
git commit -m "feat: parse projects from config.yaml"
```

---

### Task 3: get_project() helper

**Files:**
- Modify: `src/auditer/config.py` (add after `get_account`)
- Test: `tests/test_config.py`

**Step 1: Write the failing tests**

In `tests/test_config.py`, add:

```python
from auditer.config import get_project

class TestGetProject:
    def _config_with_projects(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "a"},
            ],
            "projects": {
                "client-work": {"repos": ["a/web"]},
                "open-source": {"repos": ["a/lib"]},
            },
        }
        return load_config_from_dict(data)

    def test_found(self):
        config = self._config_with_projects()
        project = get_project(config, "client-work")
        assert project.name == "client-work"

    def test_not_found(self):
        config = self._config_with_projects()
        with pytest.raises(ConfigError, match="not found"):
            get_project(config, "nonexistent")

    def test_fuzzy_match(self):
        config = self._config_with_projects()
        with pytest.raises(ConfigError) as exc_info:
            get_project(config, "client-wrok")
        assert exc_info.value.suggested_project == "client-work"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestGetProject -v`
Expected: FAIL — `cannot import name 'get_project'`

**Step 3: Write implementation**

In `src/auditer/config.py`, add after `get_account()`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::TestGetProject -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/auditer/config.py tests/test_config.py
git commit -m "feat: add get_project() config helper with fuzzy matching"
```

---

### Task 4: get_project_repos() filtering helper

**Files:**
- Modify: `src/auditer/filtering.py`
- Test: `tests/test_filtering.py`

**Step 1: Write the failing tests**

In `tests/test_filtering.py`, add:

```python
from auditer.models import ProjectConfig
from auditer.filtering import filter_by_project

class TestFilterByProject:
    def test_filters_to_project_repos(self):
        project = ProjectConfig(
            name="proj",
            repos=["acct-a/repo1", "acct-a/repo3", "acct-b/repo2"],
        )
        items = [{"name": "repo1"}, {"name": "repo2"}, {"name": "repo3"}]
        result, skipped = filter_by_project(
            items, project, "acct-a", lambda r: r["name"]
        )
        assert len(result) == 2
        assert skipped == 1
        names = {r["name"] for r in result}
        assert names == {"repo1", "repo3"}

    def test_different_account(self):
        project = ProjectConfig(
            name="proj",
            repos=["acct-a/repo1"],
        )
        items = [{"name": "repo1"}, {"name": "repo2"}]
        result, skipped = filter_by_project(
            items, project, "acct-b", lambda r: r["name"]
        )
        assert result == []
        assert skipped == 2

    def test_case_insensitive_repo_name(self):
        project = ProjectConfig(
            name="proj",
            repos=["acct/MyRepo"],
        )
        items = [{"name": "myrepo"}, {"name": "other"}]
        result, skipped = filter_by_project(
            items, project, "acct", lambda r: r["name"]
        )
        assert len(result) == 1
        assert result[0]["name"] == "myrepo"

    def test_empty_project_repos(self):
        project = ProjectConfig(name="empty", repos=[])
        items = [{"name": "repo1"}]
        result, skipped = filter_by_project(
            items, project, "acct", lambda r: r["name"]
        )
        assert result == []
        assert skipped == 1

    def test_works_with_objects(self):
        class Repo:
            def __init__(self, name):
                self.name = name

        project = ProjectConfig(name="proj", repos=["acct/keep"])
        items = [Repo("keep"), Repo("drop")]
        result, skipped = filter_by_project(
            items, project, "acct", lambda r: r.name
        )
        assert len(result) == 1
        assert skipped == 1
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_filtering.py::TestFilterByProject -v`
Expected: FAIL — `cannot import name 'filter_by_project'`

**Step 3: Write implementation**

In `src/auditer/filtering.py`, add:

```python
from auditer.models import AccountConfig, ProjectConfig

def filter_by_project(
    items: list[T],
    project: ProjectConfig,
    account_slug: str,
    get_name: Callable[[T], str],
) -> tuple[list[T], int]:
    """Filter items to only those in the project for the given account.

    Returns (filtered_items, skipped_count).
    """
    prefix = f"{account_slug}/"
    allowed = {
        entry[len(prefix):].lower()
        for entry in project.repos
        if entry.lower().startswith(prefix.lower())
    }
    original_count = len(items)
    result = [item for item in items if get_name(item).lower() in allowed]
    return result, original_count - len(result)
```

Update the existing import at the top: `from auditer.models import AccountConfig, ProjectConfig`

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_filtering.py::TestFilterByProject -v`
Expected: PASS

**Step 5: Run full filtering test suite**

Run: `pytest tests/test_filtering.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/auditer/filtering.py tests/test_filtering.py
git commit -m "feat: add filter_by_project() filtering helper"
```

---

### Task 5: get_accounts_for_project() helper

**Files:**
- Modify: `src/auditer/config.py`
- Test: `tests/test_config.py`

This helper is needed for the `--project` without `--account` case: find all accounts that have repos in a given project.

**Step 1: Write the failing tests**

In `tests/test_config.py`, add:

```python
from auditer.config import get_accounts_for_project

class TestGetAccountsForProject:
    def test_returns_matching_accounts(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
                {"slug": "b", "type": "org", "username": "org-b"},
                {"slug": "c", "type": "user", "username": "user-c"},
            ],
            "projects": {
                "proj": {"repos": ["a/repo1", "b/repo2"]},
            },
        }
        config = load_config_from_dict(data)
        project = get_project(config, "proj")
        accounts = get_accounts_for_project(config, project)
        slugs = {a.slug for a in accounts}
        assert slugs == {"a", "b"}

    def test_returns_empty_for_empty_project(self):
        data = {
            "accounts": [
                {"slug": "a", "type": "user", "username": "user-a"},
            ],
            "projects": {
                "empty": {"repos": []},
            },
        }
        config = load_config_from_dict(data)
        project = get_project(config, "empty")
        accounts = get_accounts_for_project(config, project)
        assert accounts == []
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestGetAccountsForProject -v`
Expected: FAIL — `cannot import name 'get_accounts_for_project'`

**Step 3: Write implementation**

In `src/auditer/config.py`, add:

```python
def get_accounts_for_project(
    config: AppConfig, project: ProjectConfig
) -> list[AccountConfig]:
    """Return accounts that have at least one repo in the project."""
    slugs_in_project = {
        entry.split("/")[0] for entry in project.repos if "/" in entry
    }
    return [a for a in config.accounts if a.slug in slugs_in_project]
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py::TestGetAccountsForProject -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/auditer/config.py tests/test_config.py
git commit -m "feat: add get_accounts_for_project() helper"
```

---

### Task 6: Add --project to checkout command

**Files:**
- Modify: `src/auditer/commands/data/checkout.py:99-106,128-131,288-333`
- Test: `tests/commands/data/test_checkout.py`

**Step 1: Write the failing tests**

In `tests/commands/data/test_checkout.py`, add tests for `--project` filtering. The key test is that when `--project` is passed, only repos listed in that project (for the account) are processed. Use the same test patterns already in the file — CliRunner with mocked git/API.

```python
def test_checkout_project_filters_repos(self, tmp_path):
    """--project filters repos to only those in the project."""
    # Config with two repos, project referencing only one
    config_data = {
        "accounts": [
            {"slug": "a", "type": "user", "username": "a", "token": "ghp_test1234567890abcdefghijklmnopqrstuv"},
        ],
        "projects": {
            "my-proj": {"repos": ["a/wanted-repo"]},
        },
    }
    # ... mock list_repos returning [wanted-repo, other-repo]
    # ... invoke with --project my-proj --account a
    # ... assert only wanted-repo was cloned
```

The exact test implementation should follow the existing patterns in the test file (use `respx`, mock `clone_repo`/`fetch_and_pull`, etc.).

**Step 2: Run test to verify it fails**

Run: `pytest tests/commands/data/test_checkout.py::TestCheckoutProject -v`
Expected: FAIL

**Step 3: Write implementation**

In `src/auditer/commands/data/checkout.py`:

1. Add imports:
   ```python
   from auditer.config import AppConfig, get_account, get_project, get_accounts_for_project
   from auditer.filtering import filter_by_project, filter_excluded_repos
   ```

2. Add `--project` option to `checkout_cmd`:
   ```python
   @click.option("--project", "project_name", default=None, help="Filter to repos in this project")
   ```

3. In the command function, after resolving accounts, resolve the project:
   ```python
   project = None
   if project_name:
       project = get_project(config, project_name)
       if not checkout_all and not account_slug:
           # --project alone: operate on all accounts in the project
           accounts = get_accounts_for_project(config, project)
           if not accounts:
               console.print(f"[yellow]No accounts have repos in project '{project_name}'[/yellow]")
               sys.exit(0)
   ```

4. Pass `project` to `run_checkout()` as a new optional parameter.

5. In `run_checkout()`, add `project: ProjectConfig | None = None` parameter. After `filter_excluded_repos`, add:
   ```python
   if project:
       raw_repos, proj_skipped = filter_by_project(
           raw_repos, project, account.slug, lambda r: r["name"]
       )
       if proj_skipped:
           console.print(f"Filtered to [bold]{len(raw_repos)}[/bold] repo(s) in project '{project.name}'.")
   ```

**Step 4: Run tests**

Run: `pytest tests/commands/data/test_checkout.py -v`
Expected: All pass (new + existing)

**Step 5: Commit**

```bash
git add src/auditer/commands/data/checkout.py tests/commands/data/test_checkout.py
git commit -m "feat: add --project option to checkout command"
```

---

### Task 7: Add --project to audit command

**Files:**
- Modify: `src/auditer/commands/data/audit.py:23-26,51-56,136-154`
- Test: `tests/commands/data/test_audit.py`

**Step 1: Write the failing test**

Same pattern as checkout: test that `--project` filters `local_repos` after scanning.

**Step 2: Run test to verify it fails**

Run: `pytest tests/commands/data/test_audit.py::TestAuditProject -v`

**Step 3: Write implementation**

In `src/auditer/commands/data/audit.py`:

1. Add imports for `get_project`, `get_accounts_for_project`, `filter_by_project`.
2. Add `--project` option to `audit_cmd`.
3. Add `project: ProjectConfig | None = None` parameter to `run_audit()`.
4. After `filter_excluded_repos(local_repos, ...)`, add project filtering:
   ```python
   if project:
       local_repos, proj_skipped = filter_by_project(
           local_repos, project, account.slug, lambda r: r.name
       )
       if proj_skipped:
           console.print(f"Filtered to [bold]{len(local_repos)}[/bold] repo(s) in project '{project.name}'.")
   ```
5. When `--project` is active, write reports to `reports/{project.name}/` instead of `reports/{slug}/`:
   ```python
   if project:
       reports_dir = Path(config.reports_dir) / project.name
   else:
       reports_dir = Path(config.reports_dir) / account.slug
   ```

**Step 4: Run tests**

Run: `pytest tests/commands/data/test_audit.py -v`

**Step 5: Commit**

```bash
git add src/auditer/commands/data/audit.py tests/commands/data/test_audit.py
git commit -m "feat: add --project option to audit command"
```

---

### Task 8: Add --project to activity command

**Files:**
- Modify: `src/auditer/commands/data/activity.py:364-371,396-401,609+`
- Test: `tests/commands/data/test_activity_cmd.py`

Same pattern as audit. The project filter goes after `filter_excluded_repos` and before the `repo_filter` check. Reports dir uses project name when active.

**Step 1-5:** Follow same TDD pattern as Tasks 6-7.

**Commit:**
```bash
git add src/auditer/commands/data/activity.py tests/commands/data/test_activity_cmd.py
git commit -m "feat: add --project option to activity command"
```

---

### Task 9: Add --project to remaining commands

**Files:**
- Modify: `src/auditer/commands/content/readme.py`
- Modify: `src/auditer/commands/people/access.py`
- Modify: `src/auditer/commands/repo/archive.py`
- Modify: `src/auditer/commands/repo/add_license.py`
- Modify: `src/auditer/commands/repo/topics.py` (list subcommand only)
- Modify: `src/auditer/commands/repo/settings.py`
- Tests: corresponding test files

Same pattern for each: add `--project` option, resolve project, add `filter_by_project` after existing filtering.

For commands that use API-fetched repo lists (checkout, archive, add-license, settings, access, topics): filter after `list_repos()` + `filter_excluded_repos()`.

For commands that scan local repos (audit, activity, readme): filter after `scan_local_repos()` + `filter_excluded_repos()`.

**Commit each command separately or batch them:**
```bash
git commit -m "feat: add --project option to readme, access, archive, add-license, topics, settings"
```

---

### Task 10: Update config command to show projects

**Files:**
- Modify: `src/auditer/commands/meta/config_cmd.py:32-73`
- Test: `tests/commands/meta/test_config_cmd.py`

**Step 1: Write the failing test**

```python
def test_show_displays_projects(self, tmp_path):
    data = {
        "accounts": [
            {"slug": "a", "type": "user", "username": "a", "token": "ghp_test1234567890abcdefghijklmnopqrstuv"},
        ],
        "projects": {
            "client-work": {
                "description": "Client X",
                "repos": ["a/web", "a/api"],
            },
        },
    }
    # Write config, invoke config --show, assert "Projects" section and "client-work" appear
```

**Step 2: Run test to verify it fails**

**Step 3: Write implementation**

In `config_cmd.py`, after the accounts table, add a projects section:

```python
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
```

For `--validate`, after the success message, add project count:
```python
if config.projects:
    console.print(f"  {len(config.projects)} project(s) configured.")
```

**Step 4: Run tests**

Run: `pytest tests/commands/meta/test_config_cmd.py -v`

**Step 5: Commit**

```bash
git add src/auditer/commands/meta/config_cmd.py tests/commands/meta/test_config_cmd.py
git commit -m "feat: show projects in config --show and --validate"
```

---

### Task 11: Update config.example.yaml

**Files:**
- Modify: `config.example.yaml`

Add a commented-out `projects` section:

```yaml
# Projects — group repos from any account into named collections.
# Use --project <name> with commands to scope operations.
# projects:
#   client-work:
#     description: "Client X web platform"
#     repos:
#       - my-user/web-app
#       - my-org/api-service
#   open-source:
#     description: "My open source work"
#     repos:
#       - my-user/cool-project
```

**Commit:**
```bash
git add config.example.yaml
git commit -m "docs: add projects section to config.example.yaml"
```

---

### Task 12: Update conftest.py fixtures

**Files:**
- Modify: `tests/conftest.py`

Add shared fixtures for project testing:

```python
@pytest.fixture
def sample_project() -> ProjectConfig:
    return ProjectConfig(
        name="test-project",
        description="A test project",
        repos=["test-user/my-repo", "test-user/secret-project"],
    )

@pytest.fixture
def sample_config_dict_with_projects() -> dict:
    return {
        "defaults": {"stale_months": 12, "data_dir": "data", "reports_dir": "reports"},
        "accounts": [
            {
                "slug": "test-user",
                "type": "user",
                "username": "testuser",
                "token": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx1234",
            }
        ],
        "projects": {
            "test-project": {
                "description": "A test project",
                "repos": ["test-user/my-repo"],
            },
        },
    }
```

**Commit:**
```bash
git add tests/conftest.py
git commit -m "test: add shared project fixtures to conftest.py"
```

---

### Task 13: Update documentation

**Files:**
- Modify: `README.md` — add Projects section explaining config and `--project` flag
- Modify: `CLAUDE.md` — update Architecture, Config Schema, Key files, and relevant command details
- Modify: `ROADMAP.md` — move project directories from planned to current features

**Commit:**
```bash
git add README.md CLAUDE.md ROADMAP.md
git commit -m "docs: document project directories feature"
```

---

### Task 14: Full test suite verification

Run the complete test suite to verify nothing is broken:

```bash
pytest -v
```

Expected: All tests pass, including the 600+ existing tests.

If any failures: fix and commit individually.

**Final commit (if any fixes needed):**
```bash
git commit -m "fix: resolve test failures from project directories integration"
```
